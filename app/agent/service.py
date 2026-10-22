"""Entry points for running the agent and deciding approvals."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.agent.classifier import Classifier
from app.agent.graph import AgentDeps, AgentState, build_graph, persist_run
from app.auth.principal import Principal, Role
from app.config import Settings
from app.db.models import AgentRun, Approval, as_utc, utcnow
from app.db.repositories import AccountRepository, TenantRepository
from app.db.session import Database
from app.errors import Conflict, Forbidden, NotFound
from app.llm.base import LLMClient
from app.observability import metrics
from app.observability.tracing import current_trace_id, tracer
from app.rag.service import RagService
from app.tools.builtin import ToolRegistry
from app.tools.executor import ToolArgumentError, execute, validate_arguments
from app.tools.policy import Verdict, can_approve

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        rag: RagService,
        llm: LLMClient,
        registry: ToolRegistry | None = None,
    ) -> None:
        registry = registry or ToolRegistry()
        self.deps = AgentDeps(
            settings=settings,
            db=db,
            rag=rag,
            llm=llm,
            registry=registry,
            classifier=Classifier(llm, registry),
            started_at=time.time(),
        )
        self.graph = build_graph(self.deps)

    # -- chat ------------------------------------------------------------
    def run(self, principal: Principal, message: str) -> AgentRun:
        started = time.perf_counter()
        with tracer.start_as_current_span("agent.run") as span:
            with self.deps.db.session() as session:
                run = TenantRepository(session, principal.tenant_id).add_run(
                    AgentRun(
                        user_id=principal.user_id,
                        message=message[: self.deps.settings.max_message_chars],
                        status="running",
                        trace_id=current_trace_id(),
                    )
                )
            state: AgentState = {
                "run_id": run.id,
                "message": message,
                "principal": principal,
                "started": started,
            }
            try:
                final = self.graph.invoke(state)
                span.set_attribute("agent.status", final.get("status", "unknown"))
            except Exception:
                logger.exception("agent run %s failed", run.id)
                persist_run(
                    self.deps,
                    {
                        **state,
                        "status": "error",
                        "error": "internal error",
                        "response": "Something went wrong while handling that request.",
                    },
                )
        with self.deps.db.session() as session:
            return TenantRepository(session, principal.tenant_id).get_run(run.id)  # type: ignore[return-value]

    # -- approvals -------------------------------------------------------
    def decide(self, approver: Principal, approval_id: str, *, approve: bool, note: str | None) -> Approval:
        settings = self.deps.settings
        with self.deps.db.session() as session:
            repo = TenantRepository(session, approver.tenant_id)
            approval = repo.get_approval(approval_id)  # tenant-scoped: other tenants' ids are "not found"
            if approval is None:
                raise NotFound("Approval not found")
            decision = can_approve(
                approver,
                tenant_id=approval.tenant_id,
                requested_by=approval.requested_by,
                require_distinct=settings.require_distinct_approver,
            )
            if decision.verdict is not Verdict.ALLOW:
                raise Forbidden(decision.reason[:1].upper() + decision.reason[1:])
            if approval.status != "pending":
                raise Conflict(f"Approval is already {approval.status}")

            if as_utc(approval.expires_at) < datetime.now(UTC):
                self._close(
                    repo,
                    approval,
                    approver,
                    status="expired",
                    run_status="rejected",
                    note="Approval expired before a decision was made.",
                )
                return approval
            if not approve:
                self._close(
                    repo,
                    approval,
                    approver,
                    status="rejected",
                    run_status="rejected",
                    note=note or "Rejected by an administrator.",
                )
                return approval
            if not repo.transition_approval(approval.id, "pending", "executing"):
                raise Conflict("Approval is being processed by someone else")
            requester = AccountRepository(session).get_user(approval.requested_by)

        if requester is None or not requester.is_active:
            return self._finish(
                approval,
                approver,
                status="failed",
                output=None,
                error="Requester is no longer active",
                duration_ms=None,
                note=note,
            )
        spec = self.deps.registry.get(approval.tool_name)
        requester_principal = Principal(
            user_id=requester.id,
            tenant_id=requester.tenant_id,
            tenant_name=approver.tenant_name,
            email=requester.email,
            role=Role(requester.role),
        )
        try:
            arguments = validate_arguments(spec, approval.arguments)  # type: ignore[arg-type]
        except (ToolArgumentError, AttributeError):
            return self._finish(
                approval,
                approver,
                status="failed",
                output=None,
                error="Stored arguments are no longer valid",
                duration_ms=None,
                note=note,
            )
        # Executes in the *requester's* tenant scope; the approver is recorded as the actor.
        result = execute(spec, self.deps.tool_context(requester_principal, approver.user_id), arguments)  # type: ignore[arg-type]
        status = "approved" if result.status == "succeeded" else "failed"
        return self._finish(
            approval,
            approver,
            status=status,
            output=result.output,
            error=result.error,
            duration_ms=result.duration_ms,
            note=note,
            tool_status=result.status,
        )

    def _close(
        self,
        repo: TenantRepository,
        approval: Approval,
        approver: Principal,
        *,
        status: str,
        run_status: str,
        note: str,
    ) -> None:
        approval.status, approval.decided_by, approval.decided_at = status, approver.user_id, utcnow()
        approval.decision_note = note[:500]
        invocation = repo.get_invocation(approval.tool_invocation_id)
        if invocation:
            invocation.status = status
        run = repo.get_run(approval.run_id)
        if run:
            run.status = run_status
            run.response = (run.response or "") + f"\n\n**Update:** {note}"
        repo.add_audit(f"approval.{status}", actor_id=approver.user_id, target=approval.id)
        metrics.APPROVALS.labels(status).inc()

    def _finish(
        self,
        approval: Approval,
        approver: Principal,
        *,
        status: str,
        output: dict | None,
        error: str | None,
        duration_ms: float | None,
        note: str | None,
        tool_status: str | None = None,
    ) -> Approval:
        with self.deps.db.session() as session:
            repo = TenantRepository(session, approver.tenant_id)
            stored = repo.get_approval(approval.id)
            assert stored is not None  # noqa: S101
            stored.status, stored.decided_by, stored.decided_at = status, approver.user_id, utcnow()
            stored.decision_note = (note or "")[:500] or None
            stored.result = output if output is not None else {"error": error}
            invocation = repo.get_invocation(stored.tool_invocation_id)
            if invocation:
                invocation.status = tool_status or "failed"
                invocation.result, invocation.error, invocation.duration_ms = output, error, duration_ms
            run = repo.get_run(stored.run_id)
            if run:
                if status == "approved" and output:
                    summary = (
                        (
                            f"Approved by an administrator. Incident `{output.get('incident_id')}` "
                            f"was created (simulated)."
                        )
                        if stored.tool_name == "create_incident"
                        else "Approved by an administrator and executed."
                    )
                    run.status = "completed"
                else:
                    summary = f"Approved, but execution failed: {error}"
                    run.status = "error"
                run.response = (run.response or "") + f"\n\n**Update:** {summary}"
            repo.add_audit(
                f"approval.{status}",
                actor_id=approver.user_id,
                target=stored.id,
                detail={"tool": stored.tool_name},
            )
        metrics.APPROVALS.labels(status).inc()
        return stored
