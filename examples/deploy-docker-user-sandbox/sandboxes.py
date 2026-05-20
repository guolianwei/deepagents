"""Sandbox metadata tracking for the User-Scoped Docker Sandbox API Service.
"""

from __future__ import annotations

import datetime
import sqlite3


def register_sandbox(db: sqlite3.Connection, cache_key: str, container_id: str) -> None:
    """Log or update an active sandbox container session record.

    Args:
        db: An active sqlite3 database connection.
        cache_key: The cache key, structured as user:{assistant_id}:{user_id}.
        container_id: The Docker container identifier.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.execute(
        "INSERT INTO sandboxes (cache_key, container_id, status, last_active_at) "
        "VALUES (?, ?, 'running', ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET "
        "container_id = excluded.container_id, "
        "last_active_at = excluded.last_active_at",
        (cache_key, container_id, now)
    )
    db.commit()


def list_sandboxes(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Retrieve all logged sandbox container session records.

    Args:
        db: An active sqlite3 database connection.

    Returns:
        A list of sqlite3.Row objects containing sandbox details.
    """
    return db.execute("SELECT cache_key, container_id, status, last_active_at FROM sandboxes").fetchall()
