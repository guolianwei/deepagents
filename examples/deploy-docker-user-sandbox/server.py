"""User-Scoped Docker Sandbox API Service.

Orchestration layer that mounts routes and manages database and Docker lifetimes.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import docker
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBearer

# Import modular components
import auth
import assistants
import threads
import graph_runtime
import sandboxes
from models import (
    AssistantCreate,
    ChatRequest,
    ChatResponse,
    ThreadCreate,
    UserRegister,
    UserResponse,
    TokenResponse,
)

# Configuration & DB Path
DB_PATH = Path(
    os.environ.get(
        "SANDBOX_API_DB_PATH",
        str(Path(__file__).parent / "sandbox_api.db"),
    )
)
security_bearer = HTTPBearer()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Dependency provider for active sqlite3 DB connections.

    Yields:
        An active sqlite3.Connection with Row representation mapping.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize DB schema if tables do not exist."""
    conn = sqlite3.connect(DB_PATH)
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


# Shared Docker Client State
docker_client: docker.DockerClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events to handle DB initialization and Docker daemon connection."""
    global docker_client
    init_db()

    # Establish connection to Docker (takes care of SSH transport if DOCKER_HOST is set)
    # Default to user's remote server if no environment variable is already declared
    if "DOCKER_HOST" not in os.environ:
        os.environ["DOCKER_HOST"] = "ssh://glw@192.168.153.130"
        
    try:
        docker_client = docker.from_env()
        docker_client.ping()
        print(f"[Info] Successfully connected to Docker daemon on: {os.environ.get('DOCKER_HOST')}")
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
    lifespan=lifespan,
)


# --- ROUTE HANDLERS ---

@app.post("/api/v1/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: Annotated[sqlite3.Connection, Depends(get_db)]):
    """Register a new user."""
    existing = auth.get_user_by_username(db, payload.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")

    user_info = auth.register_user(db, payload.username, payload.password)
    return user_info


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(payload: UserRegister, db: Annotated[sqlite3.Connection, Depends(get_db)]):
    """Log in and obtain a JWT access token."""
    user = auth.get_user_by_username(db, payload.username)
    if not user or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    token = auth.create_access_token(data={"sub": user["id"], "username": payload.username})
    return {"access_token": token, "token_type": "bearer"}


@app.post("/api/v1/assistants", status_code=status.HTTP_201_CREATED)
async def create_assistant(
    payload: AssistantCreate,
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """Register a new assistant definition."""
    existing = assistants.get_assistant(db, payload.id)
    if existing:
        raise HTTPException(status_code=400, detail="Assistant with this ID already exists")

    res = assistants.create_assistant(
        db,
        assistant_id=payload.id,
        name=payload.name,
        model=payload.model,
        image=payload.image,
        base_dir=payload.base_dir,
        config=payload.config,
    )
    return res


@app.get("/api/v1/assistants")
async def list_assistants(
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """List all registered assistant definitions."""
    rows = assistants.list_assistants(db)
    return [dict(r) for r in rows]


@app.post("/api/v1/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: ThreadCreate,
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """Start a new dialogue thread session."""
    res = threads.create_thread(db, user_id=user_id, assistant_id=payload.assistant_id, name=payload.name)
    return res


@app.get("/api/v1/threads")
async def list_threads(
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """List all dialogue thread sessions owned by the authenticated user."""
    rows = threads.list_threads(db, user_id)
    return [dict(r) for r in rows]


@app.post("/api/v1/threads/{thread_id}/chat", response_model=ChatResponse)
async def chat(
    thread_id: str,
    payload: ChatRequest,
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """Route a dialogue message to the assistant and execute within the user's sandbox."""
    # 1. Ownership & Access Validation
    thread = threads.load_thread_for_user(db, thread_id, user_id)
    assistant_id = thread["assistant_id"]

    assistant = assistants.get_assistant(db, assistant_id)
    if not assistant:
        raise HTTPException(
            status_code=500,
            detail="Assistant configuration has drifted or is missing from database.",
        )

    # 2. Invoke Graph execution
    reply, container_id = graph_runtime.invoke_deepagents_graph(
        assistant_id=assistant_id,
        thread_id=thread_id,
        user_id=user_id,
        message=payload.message,
        assistant_dict=dict(assistant),
        docker_client=docker_client,
    )

    # 3. Log active sandbox container state in database if created
    if container_id:
        cache_key = f"user:{assistant_id}:{user_id}"
        sandboxes.register_sandbox(db, cache_key, container_id)

    return ChatResponse(
        response=reply,
        thread_id=thread_id,
        assistant_id=assistant_id,
        container_id=container_id,
    )


@app.get("/api/v1/sandboxes")
async def list_sandboxes(
    user_id: Annotated[str, Depends(auth.get_current_user_id)],
    db: Annotated[sqlite3.Connection, Depends(get_db)],
):
    """Admin utility endpoint to see logged sandbox sessions."""
    rows = sandboxes.list_sandboxes(db)
    return [dict(r) for r in rows]
