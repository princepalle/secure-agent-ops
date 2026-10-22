"""Request and response models for the HTTP API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.auth.principal import Role

_TENANT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _check_password(value: str) -> str:
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("password must contain letters and digits")
    return value


# --- auth -----------------------------------------------------------------------
class RegisterRequest(_In):
    tenant_name: str = Field(min_length=3, max_length=40, description="Lowercase letters, digits and hyphens")
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("tenant_name")
    @classmethod
    def _tenant(cls, value: str) -> str:
        value = value.lower()
        if not _TENANT_NAME.match(value):
            raise ValueError("use 3-40 lowercase letters, digits or hyphens")
        return value

    _password = field_validator("password")(_check_password)


class LoginRequest(_In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class CreateUserRequest(_In):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: Role = Role.USER

    _password = field_validator("password")(_check_password)


class PrincipalOut(BaseModel):
    user_id: str
    email: str
    role: Role
    tenant_id: str
    tenant_name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
    principal: PrincipalOut


class UserOut(_Out):
    id: str
    email: str
    role: str
    is_active: bool
    created_at: datetime


# --- agent ----------------------------------------------------------------------
class ChatRequest(_In):
    message: str = Field(min_length=1, max_length=4000)


class RunOut(_Out):
    id: str
    user_id: str
    message: str
    intent: str | None
    classifier_source: str | None
    tool_name: str | None
    status: str
    response: str | None
    citations: list[dict[str, Any]]
    latency_ms: float | None
    llm_provider: str | None
    llm_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    token_source: str = Field(description="measured | estimated | none")
    estimated_cost_usd: float | None
    error: str | None
    trace_id: str | None
    created_at: datetime


class ToolInvocationOut(_Out):
    id: str
    run_id: str
    user_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    error: str | None
    duration_ms: float | None
    created_at: datetime


class ApprovalDecision(_In):
    note: str | None = Field(default=None, max_length=500)


class ApprovalOut(_Out):
    id: str
    run_id: str
    requested_by: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    result: dict[str, Any] | None
    expires_at: datetime
    created_at: datetime


class IncidentOut(_Out):
    id: str
    title: str
    severity: str
    description: str
    affected_component: str
    status: str
    created_by: str
    approved_by: str
    created_at: datetime


# --- documents --------------------------------------------------------------------
class DocumentOut(_Out):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    chunk_count: int
    uploaded_by: str
    created_at: datetime


class SearchHit(BaseModel):
    index: int
    document_id: str
    filename: str
    chunk_index: int
    score: float
    snippet: str


class SearchResponse(BaseModel):
    query: str
    latency_ms: float
    results: list[SearchHit]


# --- ops ----------------------------------------------------------------------------
class AuditOut(_Out):
    id: str
    actor_id: str | None
    action: str
    target: str | None
    detail: dict[str, Any]
    created_at: datetime


class OpsSummary(BaseModel):
    scope: Literal["tenant", "user"]
    runs_considered: int
    runs_by_status: dict[str, int]
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    latency_note: str
    tokens_measured: int
    tokens_estimated: int
    token_note: str
    estimated_cost_usd: float | None
    cost_note: str
    tool_calls_by_status: dict[str, int]
    pending_approvals: int
    recent_errors: list[dict[str, Any]]
    llm_provider: str
    embedding_provider: str
