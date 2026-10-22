from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.auth.principal import Principal, Role
from app.config import Settings

if TYPE_CHECKING:
    from app.db.session import Database
    from app.llm.base import LLMClient
    from app.rag.service import RagService


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a tool may touch. The tenant always comes from ``principal``, never from tool arguments."""

    principal: Principal  # whose request this is (defines the tenant scope)
    actor_id: str  # who caused execution (the requester, or the approving admin)
    settings: Settings
    db: Database
    rag: RagService
    llm: LLMClient
    started_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]
    allowed_roles: frozenset[Role]
    read_only: bool
    requires_approval: bool
    timeout_seconds: float = 5.0

    def schema(self) -> dict[str, Any]:
        """JSON schema shown to the LLM and in the API. Purely descriptive - not trusted for enforcement."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
            "allowed_roles": sorted(r.value for r in self.allowed_roles),
        }
