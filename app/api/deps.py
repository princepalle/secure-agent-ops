"""FastAPI dependencies: the service container, authentication and role checks."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.agent.service import AgentService
from app.auth.principal import Principal
from app.auth.service import principal_from_token
from app.config import Settings
from app.db.repositories import AccountRepository, TenantRepository
from app.db.session import Database
from app.errors import Forbidden, Unauthorized
from app.llm.base import LLMClient
from app.rag.service import RagService
from app.security.ratelimit import SlidingWindowLimiter
from app.tools.builtin import ToolRegistry

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Container:
    settings: Settings
    db: Database
    rag: RagService
    llm: LLMClient
    registry: ToolRegistry
    agent: AgentService
    user_limiter: SlidingWindowLimiter
    login_limiter: SlidingWindowLimiter


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(container: Container = Depends(get_container)) -> Iterator[Session]:
    with container.db.session() as session:
        yield session


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    with container.db.session() as session:
        return principal_from_token(AccountRepository(session), credentials.credentials, container.settings)


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise Forbidden("Administrator role required")
    return principal


def tenant_repo(
    session: Session = Depends(get_session), principal: Principal = Depends(get_principal)
) -> TenantRepository:
    """The only repository routes get for tenant data - already scoped to the caller's tenant."""
    return TenantRepository(session, principal.tenant_id)


def rate_limited_user(
    principal: Principal = Depends(get_principal), container: Container = Depends(get_container)
) -> Principal:
    container.user_limiter.check(f"user:{principal.user_id}", "user")
    return principal


def client_ip(request: Request) -> str:
    # Behind a trusted proxy, configure uvicorn --proxy-headers / --forwarded-allow-ips instead of
    # reading X-Forwarded-For here (it is client-controlled).
    return request.client.host if request.client else "unknown"
