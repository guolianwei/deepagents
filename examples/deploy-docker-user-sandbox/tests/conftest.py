"""Pytest configuration and shared fixtures for the User-Scoped Docker Sandbox API Service.
"""

from __future__ import annotations

import sqlite3
from typing import Generator

import pytest


@pytest.fixture
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Provides a clean, fully initialized in-memory SQLite database connection.

    Yields:
        An active sqlite3.Connection with tables created.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Initialize all schema tables
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
    
    yield conn
    
    conn.close()
