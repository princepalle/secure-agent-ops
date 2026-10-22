"""SQLAlchemy models. Every tenant-owned table carries a non-null ``tenant_id``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on read; values are always written in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_document_tenant_sha"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    filename: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_runs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32))
    classifier_source: Mapped[str | None] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    # running | completed | pending_approval | denied | rejected | error
    status: Mapped[str] = mapped_column(String(24), default="running")
    response: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[Any]] = mapped_column(default=list)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # measured (reported by provider) | estimated (heuristic) | none (no LLM call)
    token_source: Mapped[str] = mapped_column(String(16), default="none")
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(500))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (Index("ix_tools_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # succeeded | failed | timeout | denied | pending_approval | rejected | expired
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_tenant_status", "tenant_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    tool_invocation_id: Mapped[str] = mapped_column(ForeignKey("tool_invocations.id"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # pending | executing | approved | rejected | expired | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    """A *simulated* incident record. Creating one pages nobody."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    severity: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text)
    affected_component: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"))
    actor_id: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
