"""User-Scoped Docker Sandbox API Service.

This FastAPI application provides:
1. User registration & authentication (JWT tokens).
2. Dynamic assistant starting/registration.
3. Chat endpoints using thread IDs.
4. Automatic routing to a dedicated, persistent Docker container sandbox per user.

Run:
    uv run uvicorn server:app --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import sqlite3
import types
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
import docker

# --- DEEPAGENTS IMPORTS ---
try:
    from deepagents_cli.deploy.bundler import _render_deploy_graph
    from deepagents_cli.deploy.config import AgentConfig, DeployConfig, SandboxConfig
    DEEPAGENTS_AVAILABLE = True
except ImportError:
    DEEPAGENTS_AVAILABLE = False

# --- CONFIGURATION & ENV ---
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-deepagents-sandbox")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day
DB_PATH = Path(__file__).parent / "sandbox_api.db"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_bearer = HTTPBearer()

# --- DATABASE SETUP ---
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assistants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            model TEXT NOT NULL,
            image TEXT NOT NULL,
            base_dir TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            assistant_id TEXT NOT NULL REFERENCES assistants(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sandboxes (
            cache_key TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            last_active_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

# --- DOCKER SETUP ---
docker_client = None
sandbox_factories: dict[str, Any] = {}

def _ctx(identity: str) -> types.SimpleNamespace:
    """Creates the execution context with the user identity expected by DeepAgents sandbox factory."""
    return types.SimpleNamespace(
        server_info=types.SimpleNamespace(
            user=types.SimpleNamespace(identity=identity),
        ),
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events to handle DB initialization and Docker client connection."""
    global docker_client
    init_db()
    
    # Establish connection to Docker (takes care of SSH transport if DOCKER_HOST is set)
    try:
        docker_client = docker.from_env()
        docker_client.ping()
        print("[Info] Successfully connected to Docker daemon.")
    except Exception as e:
        print(f"[Warning] Failed to connect to Docker daemon: {e}")
        print("[Info] Operating in simulation / dry-run mode for sandbox executions.")
    
    yield
    
    if docker_client:
        try:
            docker_client.close()
        except Exception:
            pass

app = FastAPI(
    title="DeepAgents User-Scoped Sandbox API",
    description="Exposes REST API endpoints to manage assistants and users, mapping each to a dedicated Docker sandbox.",
    version="1.0.0",
    lifespan=lifespan
)

# --- PYDANTIC SCHEMAS ---
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)

class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AssistantCreate(BaseModel):
    id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    model: str = "anthropic:claude-sonnet-4-6"
    image: str = "python:3.12-slim"
    base_dir: str = "/workspace"
    config: dict[str, Any] = {}

class ThreadCreate(BaseModel):
    assistant_id: str
    name: str | None = None

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    thread_id: str
    assistant_id: str
    container_id: str | None = None

# --- AUTH UTILITIES ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict[str, Any]) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire.isoformat()})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user_id(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_bearer)]) -> str:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return user_id
    except JWTError:
        raise credentials_exception

# --- SANDBOX ENGINE INTEGRATION ---
def _get_or_compile_sandbox_factory(assistant: dict[str, Any]) -> Any:
    """Dynamically compiles the user-scope Docker sandbox factory for an assistant."""
    assistant_id = assistant["id"]
    if assistant_id in sandbox_factories:
        return sandbox_factories[assistant_id]
    
    if not DEEPAGENTS_AVAILABLE:
        print("[Warning] DeepAgents packages not importable. Falling back to simulator.")
        return None
    
    # 1. Prepare deploy config mirroring deepagents.toml
    config = DeployConfig(
        agent=AgentConfig(name=assistant_id),
        sandbox=SandboxConfig(
            provider="docker",
            scope="user",
            image=assistant["image"],
            base_dir=assistant["base_dir"]
        )
    )
    
    # 2. Render deploy graph source code
    source = _render_deploy_graph(config, mcp_present=False)
    
    # 3. Compile and execute graph source to register globals
    module_globals: dict[str, Any] = {"__file__": __file__}
    exec(compile(source, f"<deploy_graph_{assistant_id}>", "exec"), module_globals)
    
    # 4. Extract build factory and generate the sandbox backend factory
    build_factory = module_globals.get("_build_backend_factory")
    if not build_factory:
        raise ValueError("Failed to retrieve _build_backend_factory from compiled deploy graph.")
    
    factory = build_factory(assistant_id)
    sandbox_factories[assistant_id] = factory
    return factory

# --- ROUTES ---

@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (payload.username,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already registered")
    
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    password_hash = hash_password(payload.password)
    
    conn.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (user_id, payload.username, password_hash, now)
    )
    conn.commit()
    conn.close()
    
    return {"id": user_id, "username": payload.username, "created_at": now}

@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(payload: UserRegister):
    conn = get_db()
    user = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (payload.username,)).fetchone()
    conn.close()
    
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    token = create_access_token(data={"sub": user["id"], "username": payload.username})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/v1/assistants", status_code=status.HTTP_201_CREATED)
async def create_assistant(payload: AssistantCreate, user_id: Annotated[str, Depends(get_current_user_id)]):
    conn = get_db()
    existing = conn.execute("SELECT id FROM assistants WHERE id = ?", (payload.id,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Assistant with this ID already exists")
    
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO assistants (id, name, model, image, base_dir, config, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (payload.id, payload.name, payload.model, payload.image, payload.base_dir, json.dumps(payload.config), now)
    )
    conn.commit()
    conn.close()
    return {"id": payload.id, "status": "active"}

@app.get("/api/v1/assistants")
async def list_assistants(user_id: Annotated[str, Depends(get_current_user_id)]):
    conn = get_db()
    rows = conn.execute("SELECT id, name, model, image, base_dir, created_at FROM assistants").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/v1/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(payload: ThreadCreate, user_id: Annotated[str, Depends(get_current_user_id)]):
    conn = get_db()
    assistant = conn.execute("SELECT id FROM assistants WHERE id = ?", (payload.assistant_id,)).fetchone()
    if not assistant:
        conn.close()
        raise HTTPException(status_code=404, detail="Assistant not found")
    
    thread_id = f"thd_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    name = payload.name or f"Conversation with {payload.assistant_id}"
    
    conn.execute(
        "INSERT INTO threads (id, user_id, assistant_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (thread_id, user_id, payload.assistant_id, name, now)
    )
    conn.commit()
    conn.close()
    return {"thread_id": thread_id, "user_id": user_id, "assistant_id": payload.assistant_id, "name": name}

@app.get("/api/v1/threads")
async def list_threads(user_id: Annotated[str, Depends(get_current_user_id)]):
    conn = get_db()
    rows = conn.execute("SELECT id, assistant_id, name, created_at FROM threads WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/v1/threads/{thread_id}/chat", response_model=ChatResponse)
async def chat(
    thread_id: str,
    payload: ChatRequest,
    user_id: Annotated[str, Depends(get_current_user_id)]
):
    conn = get_db()
    # 1. Thread validation & owner check
    thread = conn.execute("SELECT id, user_id, assistant_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
    if not thread:
        conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if thread["user_id"] != user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this conversation thread.")
    
    assistant_id = thread["assistant_id"]
    assistant = conn.execute(
        "SELECT id, name, model, image, base_dir, config FROM assistants WHERE id = ?", 
        (assistant_id,)
    ).fetchone()
    conn.close()
    
    if not assistant:
        raise HTTPException(status_code=500, detail="Assistant configuration has drifted or is missing from database.")
    
    assistant_dict = dict(assistant)
    
    # 2. Get/Compile Sandbox Factory
    factory = None
    try:
        factory = _get_or_compile_sandbox_factory(assistant_dict)
    except Exception as e:
        print(f"[Error] Failed to compile sandbox factory: {e}")
        # Fallback to simulation
        
    container_id = None
    execution_result = ""
    
    # 3. Provision / Reuse the user-scoped container
    if factory and docker_client:
        try:
            # Invoking factory with user context creates or starts the user-scoped container transparently
            sandbox_holder = factory(_ctx(user_id))
            sandbox = sandbox_holder.default
            container_id = sandbox.id
            
            # Simple execution within the user's private sandbox
            # If the user asks the agent to run commands, we execute it in the sandbox!
            cmd = payload.message.strip()
            if cmd.startswith("run:"):
                # Explicit command execution inside sandbox
                exec_cmd = cmd[4:].strip()
                res = sandbox.execute(exec_cmd)
                execution_result = f"\n[Sandbox Execution Output (Exit Code {res.exit_code})]:\n{res.output}"
            else:
                # Default mock agent execution inside container
                res = sandbox.execute("echo 'Interactive conversation initialized.'")
                execution_result = f"\n[Sandbox Active] Container {container_id[:12]} verified."
                
            # Log active sandbox state
            conn = get_db()
            cache_key = f"user:{assistant_id}:{user_id}"
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO sandboxes (cache_key, container_id, status, last_active_at) "
                "VALUES (?, ?, 'running', ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET last_active_at = excluded.last_active_at",
                (cache_key, container_id, now)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Error] Sandbox execution failed: {e}")
            execution_result = f"\n[Sandbox Warning] Failed to run in sandbox: {e}"
    else:
        # Simulation Mode
        cache_key = f"user:{assistant_id}:{user_id}"
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
        container_id = f"sim-{digest}"
        execution_result = (
            f"\n[Sandbox Simulation] Running in simulated container '{container_id}'. "
            f"User workspace mapped to '{assistant_dict['base_dir']}'."
        )

    # 4. Formulate response
    # Real assistant run-loop would execute the LangGraph step here. 
    # For this standalone API endpoint, we return a structured dialog response.
    assistant_reply = (
        f"Hi! I am the assistant '{assistant_dict['name']}' (running model {assistant_dict['model']}). "
        f"I received your message: '{payload.message}'."
        f"{execution_result}"
    )
    
    return ChatResponse(
        response=assistant_reply,
        thread_id=thread_id,
        assistant_id=assistant_id,
        container_id=container_id
    )

@app.get("/api/v1/sandboxes")
async def list_sandboxes(user_id: Annotated[str, Depends(get_current_user_id)]):
    """Admin/User utility endpoint to see active sandboxes and their status."""
    conn = get_db()
    rows = conn.execute("SELECT cache_key, container_id, status, last_active_at FROM sandboxes").fetchall()
    conn.close()
    return [dict(r) for r in rows]
