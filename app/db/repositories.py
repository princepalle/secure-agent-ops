"""Data access.

``TenantRepository`` is the only way application code reads or writes
tenant-owned rows. It is constructed with a tenant id taken from the
authenticated principal and adds ``WHERE tenant_id = :tenant`` to every
query, so a forgotten filter in a route cannot leak another tenant's data.

``AccountRepository`` covers the few lookups that must happen *before* a
tenant is known (login, registration, token validation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    AgentRun,
    Approval,
    AuditEvent,
    Document,
    Incident,
    Tenant,
    ToolInvocation,
    User,
)

T = TypeVar("T")


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)

    def get_tenant_by_name(self, name: str) -> Tenant | None:
        return self.session.scalar(select(Tenant).where(Tenant.name == name))

    def create_tenant(self, name: str) -> Tenant:
        tenant = Tenant(name=name)
        self.session.add(tenant)
        self.session.flush()
        return tenant

    def get_user_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def get_user(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def add_audit(
        self,
        action: str,
        *,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                tenant_id=tenant_id, actor_id=actor_id, action=action, target=target, detail=detail or {}
            )
        )


class TenantRepository:
    def __init__(self, session: Session, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required for tenant-scoped data access")
        self.session = session
        self.tenant_id = tenant_id

    # -- helpers ---------------------------------------------------------
    def _scoped(self, model: type[T]) -> Select[tuple[T]]:
        return select(model).where(model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]

    def _get(self, model: type[T], obj_id: str) -> T | None:
        return self.session.scalar(self._scoped(model).where(model.id == obj_id))  # type: ignore[attr-defined]

    def _add(self, obj: T) -> T:
        obj.tenant_id = self.tenant_id  # type: ignore[attr-defined]
        self.session.add(obj)
        self.session.flush()
        return obj

    # -- users -----------------------------------------------------------
    def list_users(self) -> list[User]:
        return list(self.session.scalars(self._scoped(User).order_by(User.created_at)))

    def get_user(self, user_id: str) -> User | None:
        return self._get(User, user_id)

    def add_user(self, user: User) -> User:
        return self._add(user)

    # -- documents -------------------------------------------------------
    def add_document(self, document: Document) -> Document:
        return self._add(document)

    def get_document(self, document_id: str) -> Document | None:
        return self._get(Document, document_id)

    def find_document_by_sha(self, sha256: str) -> Document | None:
        return self.session.scalar(self._scoped(Document).where(Document.sha256 == sha256))

    def list_documents(self) -> list[Document]:
        return list(self.session.scalars(self._scoped(Document).order_by(Document.created_at.desc())))

    def count_documents(self) -> int:
        return (
            self.session.scalar(
                select(func.count()).select_from(Document).where(Document.tenant_id == self.tenant_id)
            )
            or 0
        )

    def delete_document(self, document_id: str) -> bool:
        result = self.session.execute(
            delete(Document).where(Document.tenant_id == self.tenant_id, Document.id == document_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # -- agent runs ------------------------------------------------------
    def add_run(self, run: AgentRun) -> AgentRun:
        return self._add(run)

    def get_run(self, run_id: str) -> AgentRun | None:
        return self._get(AgentRun, run_id)

    def list_runs(
        self, *, user_id: str | None = None, limit: int = 50, since: datetime | None = None
    ) -> list[AgentRun]:
        stmt = self._scoped(AgentRun)
        if user_id:
            stmt = stmt.where(AgentRun.user_id == user_id)
        if since:
            stmt = stmt.where(AgentRun.created_at >= since)
        return list(self.session.scalars(stmt.order_by(AgentRun.created_at.desc()).limit(limit)))

    # -- tool invocations ------------------------------------------------
    def add_invocation(self, invocation: ToolInvocation) -> ToolInvocation:
        return self._add(invocation)

    def get_invocation(self, invocation_id: str) -> ToolInvocation | None:
        return self._get(ToolInvocation, invocation_id)

    def list_invocations(
        self, *, user_id: str | None = None, limit: int = 50, since: datetime | None = None
    ) -> list[ToolInvocation]:
        stmt = self._scoped(ToolInvocation)
        if user_id:
            stmt = stmt.where(ToolInvocation.user_id == user_id)
        if since:
            stmt = stmt.where(ToolInvocation.created_at >= since)
        return list(self.session.scalars(stmt.order_by(ToolInvocation.created_at.desc()).limit(limit)))

    # -- approvals -------------------------------------------------------
    def add_approval(self, approval: Approval) -> Approval:
        return self._add(approval)

    def get_approval(self, approval_id: str) -> Approval | None:
        return self._get(Approval, approval_id)

    def transition_approval(self, approval_id: str, from_status: str, to_status: str) -> bool:
        """Compare-and-set so two administrators can't both execute the same approval."""
        result = self.session.execute(
            update(Approval)
            .where(
                Approval.tenant_id == self.tenant_id,
                Approval.id == approval_id,
                Approval.status == from_status,
            )
            .values(status=to_status)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    def list_approvals(
        self, *, status: str | None = None, requested_by: str | None = None, limit: int = 50
    ) -> list[Approval]:
        stmt = self._scoped(Approval)
        if status:
            stmt = stmt.where(Approval.status == status)
        if requested_by:
            stmt = stmt.where(Approval.requested_by == requested_by)
        return list(self.session.scalars(stmt.order_by(Approval.created_at.desc()).limit(limit)))

    # -- incidents -------------------------------------------------------
    def add_incident(self, incident: Incident) -> Incident:
        return self._add(incident)

    def list_incidents(self, limit: int = 50) -> list[Incident]:
        return list(
            self.session.scalars(self._scoped(Incident).order_by(Incident.created_at.desc()).limit(limit))
        )

    # -- audit -----------------------------------------------------------
    def add_audit(
        self,
        action: str,
        *,
        actor_id: str | None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                tenant_id=self.tenant_id, actor_id=actor_id, action=action, target=target, detail=detail or {}
            )
        )

    def list_audit(self, limit: int = 50) -> list[AuditEvent]:
        return list(
            self.session.scalars(self._scoped(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit))
        )
