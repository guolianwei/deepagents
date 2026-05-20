"""Container reaper daemon for the User-Scoped Docker Sandbox API Service.

Stops and removes idle Docker containers matching the deepagents sandbox labels.
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

import docker


def reap_idle_containers(
    db: sqlite3.Connection,
    max_idle_seconds: int = 3600,
    docker_client: Any = None
) -> list[str]:
    """Stop and remove user sandbox Docker containers that have exceeded the idle TTL.

    Args:
        db: An active sqlite3 database connection.
        max_idle_seconds: The idle threshold in seconds.
        docker_client: An optional pre-initialized Docker client.

    Returns:
        A list of container IDs that were successfully reaped/removed.
    """
    # 1. Establish connection to Docker if not provided
    client = docker_client
    if client is None:
        try:
            client = docker.from_env()
        except Exception as e:
            print(f"[Warning] Failed to connect to Docker daemon during reaping: {e}")
            return []

    reaped_ids: list[str] = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # 2. Fetch all active sandboxes logged in the database
    rows = db.execute("SELECT cache_key, container_id, last_active_at FROM sandboxes WHERE status = 'running'").fetchall()
    active_records = {row["container_id"]: row for row in rows}

    try:
        # 3. Query all containers matching the deepagents.sandbox label
        containers = client.containers.list(
            all=True,
            filters={"label": "deepagents.sandbox=true"}
        )

        for container in containers:
            container_id = container.id
            record = active_records.get(container_id)
            if not record:
                # If there's no DB record of it but it has our label, let's treat it as orphaned and reap it
                print(f"[Reaper] Found orphaned container {container.name} ({container_id[:12]}). Cleaning up...")
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                    reaped_ids.append(container_id)
                except Exception as e:
                    print(f"[Error] Failed to remove orphaned container {container.name}: {e}")
                continue

            # Parse last active timestamp
            try:
                last_active = datetime.datetime.fromisoformat(record["last_active_at"])
                # Ensure tzinfo is present (if DB timestamp was timezone naive, assign UTC)
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                last_active = now

            idle_duration = (now - last_active).total_seconds()

            if idle_duration > max_idle_seconds:
                print(f"[Reaper] Container {container.name} has been idle for {idle_duration:.1f}s. Reaping...")
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                    
                    # Update database status
                    db.execute(
                        "UPDATE sandboxes SET status = 'stopped' WHERE container_id = ?",
                        (container_id,)
                    )
                    db.commit()
                    reaped_ids.append(container_id)
                except Exception as e:
                    print(f"[Error] Failed to reap container {container.name}: {e}")

    except Exception as e:
        print(f"[Error] Reaper execution failed: {e}")

    return reaped_ids
