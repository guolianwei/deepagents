"""Thread session management and ownership validation for the User-Scoped Docker Sandbox API Service.
"""

from __future__ import annotations

import datetime
import sqlite3
import uuid
from typing import Any

from fastapi import HTTPException, status


def create_thread(
    db: sqlite3.Connection,
    user_id: str,
    assistant_id: str,
    name: str | None = None
) -> dict[str, Any]:
    """Start a new dialogue thread session.

    Args:
        db: An active sqlite3 database connection.
        user_id: The identifier of the authenticated thread owner.
        assistant_id: The assistant identifier bound to this thread.
        name: An optional custom name for this conversation.

    Returns:
        A dictionary containing thread metadata details.

    Raises:
        HTTPException: If the assistant does not exist in the database.
    """
    # Verify assistant exists before binding thread
    assistant = db.execute("SELECT id FROM assistants WHERE id = ?", (assistant_id,)).fetchone()
    if not assistant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assistant with ID '{assistant_id}' not found."
        )

    thread_id = f"thd_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thread_name = name or f"Conversation with {assistant_id}"

    db.execute(
        "INSERT INTO threads (id, user_id, assistant_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (thread_id, user_id, assistant_id, thread_name, now)
    )
    db.commit()

    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "assistant_id": assistant_id,
        "name": thread_name
    }


def list_threads(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    """List all dialogue thread sessions owned by a specific user.

    Args:
        db: An active sqlite3 database connection.
        user_id: The authenticated user identifier.

    Returns:
        A list of sqlite3.Row objects containing thread records.
    """
    return db.execute(
        "SELECT id, assistant_id, name, created_at FROM threads WHERE user_id = ?",
        (user_id,)
    ).fetchall()


def load_thread_for_user(db: sqlite3.Connection, thread_id: str, user_id: str) -> sqlite3.Row:
    """Load a thread record after enforcing ownership validation.

    Args:
        db: An active sqlite3 database connection.
        thread_id: The requested thread identifier.
        user_id: The authenticated user identifier attempting access.

    Returns:
        The sqlite3.Row object of the loaded thread.

    Raises:
        HTTPException: 404 if the thread does not exist, or 403 if owned by another user.
    """
    thread = db.execute(
        "SELECT id, user_id, assistant_id, name, created_at FROM threads WHERE id = ?",
        (thread_id,)
    ).fetchone()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread not found"
        )

    if thread["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: You do not own this conversation thread."
        )

    return thread
