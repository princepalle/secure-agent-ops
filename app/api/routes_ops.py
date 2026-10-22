from __future__ import annotations

import statistics
from collections import Counter

from fastapi import APIRouter, Depends, Query, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import Container, get_container, get_principal, require_admin, tenant_repo
from app.api.schemas import AuditOut, OpsSummary
from app.auth.principal import Principal
from app.db.repositories import TenantRepository

router = APIRouter(tags=["operations"])


@router.get("/health", summary="Liveness: the process is up")
def health(container: Container = Depends(get_container)) -> dict:
    return {"status": "ok", "version": container.settings.app_version}


@router.get("/ready", summary="Readiness: database and vector store reachable")
def ready(response: Response, container: Container = Depends(get_container)) -> dict:
    checks = {}
    try:
        with container.db.session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    checks["vector_store"] = "ok" if container.rag.store.heartbeat() else "error"
    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "degraded",
        "checks": checks,
        "llm_provider": container.settings.llm_provider,
        "embedding_provider": container.settings.embedding_provider,
    }


@router.get("/metrics", summary="Prometheus metrics", response_class=Response)
def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100, method="inclusive")[pct - 1], 2)


@router.get(
    "/ops/summary",
    response_model=OpsSummary,
    summary="Dashboard summary: your activity, or the organisation's for administrators",
)
def summary(
    limit: int = Query(200, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
    repo: TenantRepository = Depends(tenant_repo),
    container: Container = Depends(get_container),
) -> OpsSummary:
    user_filter = None if principal.is_admin else principal.user_id
    runs = repo.list_runs(user_id=user_filter, limit=limit)
    invocations = repo.list_invocations(user_id=user_filter, limit=limit)
    latencies = [r.latency_ms for r in runs if r.latency_ms is not None]
    cost_values = [r.estimated_cost_usd for r in runs if r.estimated_cost_usd is not None]

    errors = [
        {
            "kind": "agent_run",
            "id": r.id,
            "status": r.status,
            "error": r.error,
            "at": r.created_at.isoformat(),
        }
        for r in runs
        if r.status == "error"
    ]
    errors += [
        {
            "kind": "tool",
            "id": i.id,
            "tool": i.tool_name,
            "status": i.status,
            "error": i.error,
            "at": i.created_at.isoformat(),
        }
        for i in invocations
        if i.status in {"failed", "timeout"}
    ]
    errors.sort(key=lambda e: e["at"], reverse=True)

    def tokens(source: str) -> int:
        return sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in runs if r.token_source == source)

    llm = container.settings.llm_provider
    return OpsSummary(
        scope="user" if user_filter else "tenant",
        runs_considered=len(runs),
        runs_by_status=dict(Counter(r.status for r in runs)),
        latency_ms_p50=_percentile(latencies, 50),
        latency_ms_p95=_percentile(latencies, 95),
        latency_note="Measured server-side wall-clock time per agent run.",
        tokens_measured=tokens("measured"),
        tokens_estimated=tokens("estimated"),
        token_note=(
            "Mock mode: token counts are estimates (~4 characters per token); no model was called."
            if llm == "mock"
            else "Measured counts come from the provider's usage report."
        ),
        estimated_cost_usd=round(sum(cost_values), 6) if cost_values else None,
        cost_note=(
            "Estimated from configured SAO_PRICE_* values."
            if container.settings.pricing_configured
            else "No pricing configured - cost not estimated."
        ),
        tool_calls_by_status=dict(Counter(f"{i.tool_name}:{i.status}" for i in invocations)),
        pending_approvals=len(repo.list_approvals(status="pending", requested_by=user_filter, limit=500)),
        recent_errors=errors[:10],
        llm_provider=llm,
        embedding_provider=container.settings.embedding_provider,
    )


@router.get(
    "/ops/audit", response_model=list[AuditOut], summary="Security audit trail for your organisation (admin)"
)
def audit(
    limit: int = Query(100, ge=1, le=500),
    _: Principal = Depends(require_admin),
    repo: TenantRepository = Depends(tenant_repo),
) -> list:
    return repo.list_audit(limit=limit)
