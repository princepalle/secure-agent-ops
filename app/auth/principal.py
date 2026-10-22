from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. Built from the database on every request, never from the LLM."""

    user_id: str
    tenant_id: str
    tenant_name: str
    email: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
