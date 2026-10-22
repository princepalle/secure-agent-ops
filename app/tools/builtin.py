"""The built-in tools.

| tool              | kind        | roles        | approval |
|-------------------|-------------|--------------|----------|
| document_search   | read-only   | user, admin  | no       |
| system_status     | read-only   | user, admin  | no       |
| usage_report      | read-only   | admin        | no       |
| create_incident   | state change| user, admin  | yes      |
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.auth.principal import Role
from app.db.models import Incident, utcnow
from app.db.repositories import TenantRepository
from app.tools.base import ToolContext, ToolSpec

EVERYONE = frozenset({Role.USER, Role.ADMIN})
ADMINS = frozenset({Role.ADMIN})


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- document_search ----------------------------------------------------------
class DocumentSearchArgs(_Args):
    query: str = Field(min_length=2, max_length=500, description="What to look for")
    top_k: int = Field(default=4, ge=1, le=8, description="Maximum passages to return")


def document_search(ctx: ToolContext, args: DocumentSearchArgs) -> dict:
    result = ctx.rag.search(ctx.principal.tenant_id, args.query, args.top_k)
    return {
        "query": args.query,
        "retrieval_latency_ms": result.latency_ms,
        "results": [
            {
                "document_id": c.document_id,
                "filename": c.filename,
                "chunk_index": c.chunk_index,
                "score": c.score,
                "text": c.text,
            }
            for c in result.chunks
        ],
    }


# --- system_status -------------------------------------------------------------
class SystemStatusArgs(_Args):
    component: Literal["all", "api", "database", "vector_store", "llm"] = "all"


def system_status(ctx: ToolContext, args: SystemStatusArgs) -> dict:
    """Live, read-only checks. Never calls a paid model."""
    checks: dict[str, dict] = {}
    wanted = {"api", "database", "vector_store", "llm"} if args.component == "all" else {args.component}

    if "api" in wanted:
        checks["api"] = {
            "status": "ok",
            "version": ctx.settings.app_version,
            "uptime_seconds": round(time.time() - ctx.started_at, 1),
        }
    if "database" in wanted:
        started = time.perf_counter()
        try:
            with ctx.db.session() as session:
                session.execute(text("SELECT 1"))
                docs = TenantRepository(session, ctx.principal.tenant_id).count_documents()
            checks["database"] = {
                "status": "ok",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "tenant_documents": docs,
            }
        except Exception:
            checks["database"] = {"status": "error"}
    if "vector_store" in wanted:
        ok = ctx.rag.store.heartbeat()
        checks["vector_store"] = {
            "status": "ok" if ok else "error",
            "tenant_chunks": ctx.rag.store.count(ctx.principal.tenant_id) if ok else None,
            "embedder": ctx.rag.store.embedder.name,
        }
    if "llm" in wanted:
        checks["llm"] = {
            "status": "ok",
            "provider": ctx.llm.provider,
            "model": ctx.llm.model,
            "mode": "mock (no external calls)" if ctx.llm.provider == "mock" else "live",
            "note": "configuration check only; no model call made",
        }
    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"overall": overall, "components": checks}


# --- usage_report (admin only) -------------------------------------------------
class UsageReportArgs(_Args):
    days: int = Field(default=7, ge=1, le=90)


def usage_report(ctx: ToolContext, args: UsageReportArgs) -> dict:
    since = utcnow() - timedelta(days=args.days)
    with ctx.db.session() as session:
        repo = TenantRepository(session, ctx.principal.tenant_id)
        runs = repo.list_runs(limit=5000, since=since)
        invocations = repo.list_invocations(limit=5000, since=since)
    measured = [r for r in runs if r.token_source == "measured"]  # noqa: S105 - label, not a secret
    estimated = [r for r in runs if r.token_source == "estimated"]  # noqa: S105
    return {
        "window_days": args.days,
        "runs": len(runs),
        "runs_by_status": dict(Counter(r.status for r in runs)),
        "tool_calls_by_tool": dict(Counter(i.tool_name for i in invocations)),
        "tokens_measured": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in measured),
        "tokens_estimated": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in estimated),
        "estimated_cost_usd": round(sum(r.estimated_cost_usd or 0 for r in runs), 6) or None,
    }


# --- create_incident (state-changing, approval required) -----------------------
class CreateIncidentArgs(_Args):
    title: str = Field(min_length=5, max_length=120)
    severity: Literal["sev1", "sev2", "sev3", "sev4"] = "sev3"
    description: str = Field(default="", max_length=2000)
    affected_component: str = Field(default="unspecified", min_length=2, max_length=80)


def create_incident(ctx: ToolContext, args: CreateIncidentArgs) -> dict:
    """SIMULATED: writes a record to the local database. Pages nobody, calls no external system."""
    with ctx.db.session() as session:
        repo = TenantRepository(session, ctx.principal.tenant_id)
        incident = repo.add_incident(
            Incident(
                title=args.title,
                severity=args.severity,
                description=args.description,
                affected_component=args.affected_component,
                created_by=ctx.principal.user_id,
                approved_by=ctx.actor_id,
            )
        )
        repo.add_audit(
            "incident.created",
            actor_id=ctx.actor_id,
            target=incident.id,
            detail={"severity": args.severity, "simulated": True},
        )
        return {
            "incident_id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "affected_component": incident.affected_component,
            "status": incident.status,
            "simulated": True,
        }


BUILTIN_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "document_search",
        "Search the caller's organisation knowledge base and return cited passages.",
        DocumentSearchArgs,
        document_search,
        EVERYONE,
        read_only=True,
        requires_approval=False,
    ),
    ToolSpec(
        "system_status",
        "Read-only health of the API, database, vector store and LLM configuration.",
        SystemStatusArgs,
        system_status,
        EVERYONE,
        read_only=True,
        requires_approval=False,
    ),
    ToolSpec(
        "usage_report",
        "Administrator-only usage summary for the organisation.",
        UsageReportArgs,
        usage_report,
        ADMINS,
        read_only=True,
        requires_approval=False,
    ),
    ToolSpec(
        "create_incident",
        "Open a (simulated) incident record. Requires administrator approval.",
        CreateIncidentArgs,
        create_incident,
        EVERYONE,
        read_only=False,
        requires_approval=True,
    ),
)


class ToolRegistry:
    def __init__(self, tools: tuple[ToolSpec, ...] = BUILTIN_TOOLS) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str | None) -> ToolSpec | None:
        return self._tools.get(name or "")

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())
