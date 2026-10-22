"""Deterministic reply templates.

Tool results, denials and approval notices are rendered from structured data
rather than by the LLM, so the assistant can't misreport what happened.
"""

from __future__ import annotations

from typing import Any

from app.auth.principal import Principal
from app.llm.base import GroundingSource
from app.llm.mock import rank_sentences
from app.tools.executor import ToolResult


def help_text() -> str:
    return (
        "Hi! I can answer questions from your organisation's knowledge base (with citations), "
        "check system status, and open incidents - incidents need an administrator's approval first."
    )


def no_results() -> str:
    return (
        "I couldn't find anything relevant in your organisation's knowledge base. "
        "Try different wording, or upload a document that covers it."
    )


def denied(tool: str, reason: str) -> str:
    return f"I can't run `{tool}` for you: {reason}. This is enforced by the platform's access policy."


def invalid_arguments(detail: str) -> str:
    return f"I couldn't build a valid request from that. {detail}"


def tool_failed(tool: str, result: ToolResult) -> str:
    return f"The `{tool}` tool did not complete ({result.status}): {result.error}. Nothing was changed."


def pending_approval(
    principal: Principal,
    tool: str,
    args: dict[str, Any],
    approval_id: str | None,
    sources: list[GroundingSource],
    guidance_query: str = "",
) -> str:
    if tool == "create_incident":
        action = (
            f'open a **{args["severity"].upper()}** incident "{args["title"]}" '
            f"(component: {args['affected_component']})"
        )
    else:
        action = f"run `{tool}`"
    text = (
        f"I've prepared a request to {action}. Because it changes state, it needs approval from an "
        f"administrator of **{principal.tenant_name}** before anything happens.\n\n"
        f"Approval ID: `{approval_id}` - nothing has been created yet."
    )
    guidance: list[str] = []
    for score, sentence, index in rank_sentences(guidance_query, sources):
        if len(guidance) >= 2 or score < 1:
            break
        guidance.append(f"- {sentence} [{index}]")
    if guidance:
        text += "\n\nWhile you wait, your runbooks say:\n" + "\n".join(guidance)
    return text


def system_status(output: dict[str, Any] | None) -> str:
    if not output:
        return "System status is unavailable."
    lines = [f"System status: **{output['overall'].upper()}**"]
    for name, check in output["components"].items():
        details = ", ".join(
            f"{k.replace('_', ' ')}: {v}" for k, v in check.items() if k != "status" and v is not None
        )
        lines.append(f"- **{name}**: {check['status']}" + (f" ({details})" if details else ""))
    return "\n".join(lines)


def usage_report(output: dict[str, Any] | None) -> str:
    if not output:
        return "Usage data is unavailable."
    lines = [
        f"Usage for the last {output['window_days']} day(s):",
        f"- Agent runs: {output['runs']} {output['runs_by_status'] or ''}".rstrip(),
        f"- Tool calls: {output['tool_calls_by_tool'] or 'none'}",
        f"- Tokens (measured by provider): {output['tokens_measured']}",
        f"- Tokens (estimated, ~4 chars/token): {output['tokens_estimated']}",
    ]
    cost = output.get("estimated_cost_usd")
    lines.append(
        f"- Estimated cost: ${cost}"
        if cost is not None
        else "- Estimated cost: not available (no pricing configured, or mock mode)"
    )
    return "\n".join(lines)
