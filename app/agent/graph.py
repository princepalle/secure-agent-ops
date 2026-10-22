"""The agent workflow as a LangGraph state machine.

    receive -> authorize_request -> classify -> select_tool -> authorize_tool
        authorize_tool --allow-->            execute_tool -> retrieve -> generate
        authorize_tool --require_approval--> request_approval -> retrieve -> generate
        authorize_tool --deny / no tool-->   generate
    generate -> record -> END

Authorisation happens in code (``authorize_request``, ``authorize_tool``)
using the database-backed principal. The LLM only appears in ``classify``
(optional, as a proposer) and ``generate`` (wording a grounded answer).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import responses
from app.agent.classifier import Classification, Classifier
from app.agent.prompts import ANSWER_SYSTEM, answer_prompt
from app.auth.principal import Principal
from app.config import Settings
from app.db.models import AgentRun, Approval, ToolInvocation, utcnow
from app.db.repositories import AccountRepository, TenantRepository
from app.db.session import Database
from app.llm.base import GroundingSource, LLMClient, LLMError, LLMRequest
from app.llm.usage import Usage, account, combine
from app.observability import metrics
from app.observability.tracing import tracer
from app.rag.service import RagService
from app.rag.store import StoredChunk
from app.tools.base import ToolContext, ToolSpec
from app.tools.builtin import ToolRegistry
from app.tools.executor import ToolArgumentError, ToolResult, execute, validate_arguments
from app.tools.policy import Decision, Verdict, authorize

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    run_id: str
    message: str
    principal: Principal
    started: float
    classification: Classification
    spec: ToolSpec | None
    tool_args: dict[str, Any]
    arg_error: str | None
    decision: Decision | None
    tool_result: ToolResult | None
    approval_id: str | None
    chunks: list[StoredChunk]
    response: str
    citations: list[dict[str, Any]]
    status: str
    error: str | None
    usage: Usage
    llm_provider: str | None
    llm_model: str | None


@dataclass
class AgentDeps:
    settings: Settings
    db: Database
    rag: RagService
    llm: LLMClient
    registry: ToolRegistry
    classifier: Classifier
    started_at: float

    def tool_context(self, principal: Principal, actor_id: str) -> ToolContext:
        return ToolContext(
            principal=principal,
            actor_id=actor_id,
            settings=self.settings,
            db=self.db,
            rag=self.rag,
            llm=self.llm,
            started_at=self.started_at,
        )


def _timed(name: str, fn: Callable[[AgentState], dict[str, Any]]) -> Callable[[AgentState], dict[str, Any]]:
    def node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        with tracer.start_as_current_span(f"agent.{name}") as span:
            span.set_attribute("agent.run_id", state.get("run_id", ""))
            update = fn(state)
        metrics.AGENT_NODE_DURATION.labels(name).observe(time.perf_counter() - started)
        return update

    node.__name__ = name
    return node


def build_graph(deps: AgentDeps):
    def receive(state: AgentState) -> dict[str, Any]:
        message = " ".join(state["message"].split())[: deps.settings.max_message_chars]
        return {"message": message, "usage": Usage.none(), "chunks": [], "citations": [], "error": None}

    def authorize_request(state: AgentState) -> dict[str, Any]:
        principal = state["principal"]
        with deps.db.session() as session:
            user = AccountRepository(session).get_user(principal.user_id)
        if user is None or not user.is_active or user.tenant_id != principal.tenant_id:
            return {
                "status": "denied",
                "response": "Your account is not permitted to use the agent.",
                "decision": Decision(Verdict.DENY, "inactive or unknown user"),
            }
        return {}

    def classify(state: AgentState) -> dict[str, Any]:
        classification = deps.classifier.classify(state["message"])
        update: dict[str, Any] = {"classification": classification}
        if classification.llm_response is not None:
            usage = account(classification.llm_response, state["message"], deps.settings)
            update |= {
                "usage": combine(state["usage"], usage),
                "llm_provider": classification.llm_response.provider,
                "llm_model": classification.llm_response.model,
            }
        return update

    def select_tool(state: AgentState) -> dict[str, Any]:
        classification = state["classification"]
        spec = deps.registry.get(classification.tool_name)
        if spec is None:
            return {"spec": None, "tool_args": {}}
        try:
            return {"spec": spec, "tool_args": validate_arguments(spec, classification.arguments)}
        except ToolArgumentError as exc:
            return {"spec": spec, "tool_args": {}, "arg_error": str(exc)}

    def authorize_tool(state: AgentState) -> dict[str, Any]:
        if state.get("spec") is None:
            return {"decision": None}
        decision = authorize(state["principal"], state["spec"])
        with tracer.start_as_current_span("policy.authorize") as span:
            span.set_attribute("policy.verdict", decision.verdict.value)
        if decision.verdict is Verdict.DENY:
            _record_invocation(deps, state, status="denied", error=decision.reason)
        return {"decision": decision}

    def route_after_authorize(state: AgentState) -> str:
        if state.get("status") == "denied":
            return "generate"
        if state.get("arg_error") or state.get("spec") is None:
            return "generate"
        verdict = state["decision"].verdict  # type: ignore[union-attr]
        return {Verdict.ALLOW: "execute_tool", Verdict.REQUIRE_APPROVAL: "request_approval"}.get(
            verdict, "generate"
        )

    def execute_tool(state: AgentState) -> dict[str, Any]:
        principal = state["principal"]
        result = execute(
            state["spec"],
            deps.tool_context(principal, principal.user_id),  # type: ignore[arg-type]
            state["tool_args"],
        )
        _record_invocation(deps, state, status=result.status, result=result)
        return {"tool_result": result}

    def request_approval(state: AgentState) -> dict[str, Any]:
        principal, spec = state["principal"], state["spec"]
        assert spec is not None  # noqa: S101 - guaranteed by route_after_authorize
        invocation_id = _record_invocation(deps, state, status="pending_approval")
        with deps.db.session() as session:
            repo = TenantRepository(session, principal.tenant_id)
            approval = repo.add_approval(
                Approval(
                    run_id=state["run_id"],
                    tool_invocation_id=invocation_id,
                    requested_by=principal.user_id,
                    tool_name=spec.name,
                    arguments=state["tool_args"],
                    status="pending",
                    expires_at=utcnow() + timedelta(hours=deps.settings.approval_ttl_hours),
                )
            )
            repo.add_audit(
                "approval.requested",
                actor_id=principal.user_id,
                target=approval.id,
                detail={"tool": spec.name},
            )
        return {"approval_id": approval.id, "status": "pending_approval"}

    def retrieve(state: AgentState) -> dict[str, Any]:
        intent = state["classification"].intent
        result = state.get("tool_result")
        if intent == "knowledge_question" and result and result.status == "succeeded" and result.output:
            chunks = [
                StoredChunk(
                    document_id=r["document_id"],
                    filename=r["filename"],
                    chunk_index=r["chunk_index"],
                    text=r["text"],
                    score=r["score"],
                )
                for r in result.output["results"]
            ]
            return {"chunks": chunks}
        if intent == "create_incident" and state.get("approval_id"):
            # Ground the pending-approval reply in the tenant's own runbooks.
            return {
                "chunks": deps.rag.search(
                    state["principal"].tenant_id, _guidance_query(state), top_k=2
                ).chunks
            }
        return {}

    def generate(state: AgentState) -> dict[str, Any]:
        classification = state.get("classification")
        sources = [
            GroundingSource(index=i + 1, filename=c.filename, text=c.text)
            for i, c in enumerate(state.get("chunks", []))
        ]
        citations = [
            {
                "index": s.index,
                "document_id": c.document_id,
                "filename": c.filename,
                "chunk_index": c.chunk_index,
                "score": c.score,
                "snippet": c.text[:240] + ("…" if len(c.text) > 240 else ""),
            }
            for s, c in zip(sources, state.get("chunks", []), strict=True)
        ]

        if state.get("status") == "denied" and classification is None:
            return {"citations": []}
        if state.get("arg_error"):
            return {
                "status": "error",
                "error": "invalid tool arguments",
                "response": responses.invalid_arguments(state["arg_error"]),
            }  # type: ignore[arg-type]
        decision = state.get("decision")
        if decision is not None and decision.verdict is Verdict.DENY:
            return {"status": "denied", "response": responses.denied(state["spec"].name, decision.reason)}  # type: ignore[union-attr]
        if state.get("status") == "pending_approval":
            return {
                "citations": citations,
                "response": responses.pending_approval(
                    state["principal"],
                    state["spec"].name,  # type: ignore[union-attr]
                    state["tool_args"],
                    state["approval_id"],
                    sources,
                    _guidance_query(state),
                ),
            }
        result = state.get("tool_result")
        if result is not None and result.status != "succeeded":
            return {
                "status": "error",
                "error": result.error,
                "response": responses.tool_failed(state["spec"].name, result),
            }  # type: ignore[union-attr]
        assert classification is not None  # noqa: S101
        if classification.intent == "system_status":
            return {"status": "completed", "response": responses.system_status(result.output)}  # type: ignore[union-attr]
        if classification.intent == "usage_report":
            return {"status": "completed", "response": responses.usage_report(result.output)}  # type: ignore[union-attr]
        if classification.intent == "general":
            return {"status": "completed", "response": responses.help_text()}
        if not sources:
            return {"status": "completed", "response": responses.no_results()}

        request = LLMRequest(
            system=ANSWER_SYSTEM,
            prompt=answer_prompt(state["message"], sources),
            max_tokens=deps.settings.llm_max_output_tokens,
            question=state["message"],
            sources=sources,
        )
        try:
            with tracer.start_as_current_span("llm.generate") as span:
                span.set_attribute("llm.provider", deps.llm.provider)
                llm_response = deps.llm.complete(request)
            metrics.LLM_CALLS.labels(deps.llm.provider, "ok").inc()
        except LLMError:
            metrics.LLM_CALLS.labels(deps.llm.provider, "error").inc()
            return {
                "status": "error",
                "error": "llm request failed",
                "citations": citations,
                "response": "The language model is unavailable right now. The sources below may still help.",
            }
        usage = account(llm_response, ANSWER_SYSTEM + request.prompt, deps.settings)
        return {
            "status": "completed",
            "response": llm_response.text,
            "citations": citations,
            "usage": combine(state["usage"], usage),
            "llm_provider": llm_response.provider,
            "llm_model": llm_response.model,
        }

    def record(state: AgentState) -> dict[str, Any]:
        persist_run(deps, state)
        return {}

    graph = StateGraph(AgentState)
    nodes = {
        "receive": receive,
        "authorize_request": authorize_request,
        "classify": classify,
        "select_tool": select_tool,
        "authorize_tool": authorize_tool,
        "execute_tool": execute_tool,
        "request_approval": request_approval,
        "retrieve": retrieve,
        "generate": generate,
        "record": record,
    }
    for name, fn in nodes.items():
        graph.add_node(name, _timed(name, fn))
    graph.add_edge(START, "receive")
    graph.add_edge("receive", "authorize_request")
    graph.add_conditional_edges(
        "authorize_request",
        lambda s: "generate" if s.get("status") == "denied" else "classify",
        {"generate": "generate", "classify": "classify"},
    )
    graph.add_edge("classify", "select_tool")
    graph.add_edge("select_tool", "authorize_tool")
    graph.add_conditional_edges(
        "authorize_tool",
        route_after_authorize,
        {"execute_tool": "execute_tool", "request_approval": "request_approval", "generate": "generate"},
    )
    graph.add_edge("execute_tool", "retrieve")
    graph.add_edge("request_approval", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "record")
    graph.add_edge("record", END)
    return graph.compile()


def _guidance_query(state: AgentState) -> str:
    args = state.get("tool_args", {})
    return f"{args.get('severity', '')} incident {args.get('affected_component', '')} page on-call escalate"


def _record_invocation(
    deps: AgentDeps,
    state: AgentState,
    *,
    status: str,
    result: ToolResult | None = None,
    error: str | None = None,
) -> str:
    principal, spec = state["principal"], state["spec"]
    assert spec is not None  # noqa: S101
    with deps.db.session() as session:
        invocation = TenantRepository(session, principal.tenant_id).add_invocation(
            ToolInvocation(
                run_id=state["run_id"],
                user_id=principal.user_id,
                tool_name=spec.name,
                arguments=state.get("tool_args", {}),
                status=status,
                result=result.output if result else None,
                error=(result.error if result else error),
                duration_ms=result.duration_ms if result else None,
            )
        )
        if status == "denied":
            metrics.TOOL_INVOCATIONS.labels(spec.name, "denied").inc()
        return invocation.id


def persist_run(deps: AgentDeps, state: AgentState) -> AgentRun:
    principal = state["principal"]
    classification = state.get("classification")
    usage = state.get("usage") or Usage.none()
    status = state.get("status") or "error"
    latency_ms = round((time.perf_counter() - state["started"]) * 1000, 2)
    intent = classification.intent if classification else "none"
    with deps.db.session() as session:
        run = TenantRepository(session, principal.tenant_id).get_run(state["run_id"])
        if run is None:  # pragma: no cover - created before the graph runs
            raise RuntimeError("agent run row missing")
        run.intent = intent
        run.classifier_source = classification.source if classification else None
        run.tool_name = classification.tool_name if classification else None
        run.status = status
        run.response = state.get("response") or ""
        run.citations = state.get("citations") or []
        run.latency_ms = latency_ms
        run.llm_provider = state.get("llm_provider")
        run.llm_model = state.get("llm_model")
        run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
        run.token_source = usage.source
        run.estimated_cost_usd = usage.estimated_cost_usd
        run.error = (state.get("error") or None) and str(state.get("error"))[:500]
    metrics.AGENT_RUNS.labels(intent, status).inc()
    metrics.AGENT_DURATION.labels(intent).observe(latency_ms / 1000)
    return run
