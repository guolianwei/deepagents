"""Unit tests for Phase 1 (Authentication and User Identity).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add parent directory to sys.path to allow importing from a folder containing hyphens
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth import (
    create_access_token,
    get_current_user_id,
    get_user_by_username,
    hash_password,
    register_user,
    verify_password,
)


def test_password_hashing() -> None:
    """Test that password hashing and verification works correctly."""
    password = "secret_password_123"
    hashed = hash_password(password)
    
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_user_registration_and_lookup(db_conn: sqlite3.Connection) -> None:
    """Test user registration and lookup query functionality."""
    username = "test_user"
    password = "securepassword"
    
    # Verify user does not exist yet
    assert get_user_by_username(db_conn, username) is None
    
    # Register user
    user = register_user(db_conn, username, password)
    assert user["username"] == username
    assert user["id"].startswith("usr_")
    
    # Verify user can be looked up
    db_user = get_user_by_username(db_conn, username)
    assert db_user is not None
    assert db_user["username"] == username
    assert verify_password(password, db_user["password_hash"]) is True


def test_jwt_token_flow() -> None:
    """Test that JWT access tokens are successfully created and decoded."""
    user_id = "usr_12345678"
    username = "alice"
    
    token = create_access_token({"sub": user_id, "username": username})
    assert isinstance(token, str)
    assert len(token) > 0


@pytest.mark.asyncio
async def test_get_current_user_id_valid() -> None:
    """Test get_current_user_id extracts identity correctly from a valid token."""
    user_id = "usr_9999"
    token = create_access_token({"sub": user_id})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    
    resolved_id = await get_current_user_id(credentials)
    assert resolved_id == user_id


@pytest.mark.asyncio
async def test_get_current_user_id_invalid() -> None:
    """Test get_current_user_id raises HTTPException with invalid credentials."""
    invalid_credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid_jwt_token_string")
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(invalid_credentials)
    
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Could not validate credentials"
