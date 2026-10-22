from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    Container,
    get_container,
    get_principal,
    rate_limited_user,
    require_admin,
    tenant_repo,
)
from app.api.schemas import ApprovalDecision, ApprovalOut, ChatRequest, IncidentOut, RunOut, ToolInvocationOut
from app.auth.principal import Principal
from app.db.repositories import TenantRepository
from app.errors import NotFound

router = APIRouter(tags=["agent"])


@router.post("/agent/chat", response_model=RunOut, summary="Send a message to the agent")
def chat(
    body: ChatRequest,
    principal: Principal = Depends(rate_limited_user),
    container: Container = Depends(get_container),
) -> object:
    return container.agent.run(principal, body.message)


@router.get(
    "/agent/runs",
    response_model=list[RunOut],
    summary="Agent runs: your own, or the whole organisation's for administrators",
)
def list_runs(
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    repo: TenantRepository = Depends(tenant_repo),
) -> list:
    return repo.list_runs(user_id=None if principal.is_admin else principal.user_id, limit=limit)


@router.get("/agent/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str, principal: Principal = Depends(get_principal), repo: TenantRepository = Depends(tenant_repo)
) -> object:
    run = repo.get_run(run_id)
    if run is None or (not principal.is_admin and run.user_id != principal.user_id):
        raise NotFound("Run not found")
    return run


@router.get("/agent/tools", summary="Tool catalogue with schemas and permissions")
def list_tools(_: Principal = Depends(get_principal), container: Container = Depends(get_container)) -> list:
    return [t.schema() for t in container.registry.all()]


@router.get("/agent/tool-invocations", response_model=list[ToolInvocationOut])
def list_invocations(
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    repo: TenantRepository = Depends(tenant_repo),
) -> list:
    return repo.list_invocations(user_id=None if principal.is_admin else principal.user_id, limit=limit)


@router.get(
    "/approvals",
    response_model=list[ApprovalOut],
    summary="Approval requests: your own, or the whole organisation's for administrators",
)
def list_approvals(
    status: Literal["pending", "approved", "rejected", "expired", "failed", "executing"] | None = None,
    principal: Principal = Depends(get_principal),
    repo: TenantRepository = Depends(tenant_repo),
) -> list:
    return repo.list_approvals(status=status, requested_by=None if principal.is_admin else principal.user_id)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
def approve(
    approval_id: str,
    body: ApprovalDecision | None = None,
    admin: Principal = Depends(require_admin),
    container: Container = Depends(get_container),
) -> object:
    return container.agent.decide(admin, approval_id, approve=True, note=body.note if body else None)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    approval_id: str,
    body: ApprovalDecision | None = None,
    admin: Principal = Depends(require_admin),
    container: Container = Depends(get_container),
) -> object:
    return container.agent.decide(admin, approval_id, approve=False, note=body.note if body else None)


@router.get(
    "/incidents", response_model=list[IncidentOut], summary="Simulated incidents in your organisation"
)
def list_incidents(repo: TenantRepository = Depends(tenant_repo)) -> list:
    return repo.list_incidents()
