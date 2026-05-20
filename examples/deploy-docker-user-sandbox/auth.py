"""Authentication and user identity utilities for the User-Scoped Docker Sandbox API Service.

Provides password hashing, JWT generation, decoding, database persistence, and FastAPI auth dependencies.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import uuid
from typing import Annotated, Any

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

# Configuration & Env Setup
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-deepagents-sandbox")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

security_bearer = HTTPBearer()


def hash_password(password: str) -> str:
    """Hash a plain-text password using bcrypt.

    Args:
        password: The plain-text password to hash.

    Returns:
        The bcrypt-hashed password string.
    """
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a bcrypt-hashed password.

    Args:
        plain_password: The user-supplied plain-text password.
        hashed_password: The stored hashed password.

    Returns:
        True if the password matches, False otherwise.
    """
    plain_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(data: dict[str, Any]) -> str:
    """Create a signed JWT access token.

    Args:
        data: Key-value pairs to encode in the token payload.

    Returns:
        A signed JWT token string.
    """
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_bearer)]
) -> str:
    """FastAPI dependency to extract and validate the user identity from a JWT bearer token.

    Args:
        credentials: The Bearer authorization credentials.

    Returns:
        The authenticated user_id (the sub claim).

    Raises:
        HTTPException: If the token is missing, expired, or invalid.
    """
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


# --- DATABASE USER OPERATIONS ---

def get_user_by_username(db: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """Retrieve a user record by their username.

    Args:
        db: An active sqlite3 database connection.
        username: The username to search for.

    Returns:
        A sqlite3.Row containing user details, or None if not found.
    """
    return db.execute("SELECT id, username, password_hash, created_at FROM users WHERE username = ?", (username,)).fetchone()


def register_user(db: sqlite3.Connection, username: str, plain_password: str) -> dict[str, Any]:
    """Register a new user inside the database with hashed password.

    Args:
        db: An active sqlite3 database connection.
        username: The requested username.
        plain_password: The user-supplied plain password.

    Returns:
        A dictionary containing the registered user details.
    """
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    password_hash = hash_password(plain_password)
    
    db.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, password_hash, now)
    )
    db.commit()
    return {"id": user_id, "username": username, "created_at": now}
