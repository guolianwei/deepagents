"""Unified Pydantic request/response schemas for the User-Scoped Docker Sandbox API Service.

Includes models for user registration, authentication, assistant configuration, and chat interaction.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserRegister(BaseModel):
    """Schema for registering a new user."""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


class UserResponse(BaseModel):
    """Schema for a successful user registration response."""

    id: str
    username: str
    created_at: str


class TokenResponse(BaseModel):
    """Schema for a successful login token response."""

    access_token: str
    token_type: str = "bearer"


class AssistantCreate(BaseModel):
    """Schema for registering a new assistant definition."""

    id: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    model: str = "anthropic:claude-sonnet-4-6"
    image: str = "python:3.12-slim"
    base_dir: str = "/workspace"
    config: dict[str, Any] = Field(default_factory=dict)


class ThreadCreate(BaseModel):
    """Schema for starting a new dialogue thread session."""

    assistant_id: str
    name: str | None = None


class ChatRequest(BaseModel):
    """Schema for sending a dialogue message to an assistant."""

    message: str


class ChatResponse(BaseModel):
    """Schema for a standard assistant reply with sandbox execution logs."""

    response: str
    thread_id: str
    assistant_id: str
    container_id: str | None = None
