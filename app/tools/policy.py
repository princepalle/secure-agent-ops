"""Tool authorisation policy.

This is the single decision point for "may this principal run this tool?".
Its inputs are the authenticated principal (from the database) and the
static tool spec (from code). Nothing produced by the LLM - tool choice,
arguments, or text claiming approval - can change the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.auth.principal import Principal
from app.tools.base import ToolSpec


class Verdict(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason: str


def authorize(principal: Principal, spec: ToolSpec | None) -> Decision:
    if spec is None:
        return Decision(Verdict.DENY, "unknown tool")
    if principal.role not in spec.allowed_roles:
        return Decision(Verdict.DENY, f"role '{principal.role.value}' may not use '{spec.name}'")
    # Least privilege: anything that changes state needs a human in the loop.
    if spec.requires_approval or not spec.read_only:
        return Decision(
            Verdict.REQUIRE_APPROVAL, f"'{spec.name}' changes state and needs administrator approval"
        )
    return Decision(Verdict.ALLOW, "read-only tool permitted for role")


def can_approve(
    approver: Principal, *, tenant_id: str, requested_by: str, require_distinct: bool
) -> Decision:
    if approver.tenant_id != tenant_id:
        return Decision(Verdict.DENY, "approval belongs to another tenant")
    if not approver.is_admin:
        return Decision(Verdict.DENY, "only administrators can decide approvals")
    if require_distinct and approver.user_id == requested_by:
        return Decision(Verdict.DENY, "a different administrator must approve your own request")
    return Decision(Verdict.ALLOW, "administrator of the same tenant")
