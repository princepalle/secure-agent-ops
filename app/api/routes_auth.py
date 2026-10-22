from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import Container, client_ip, get_container, get_principal, get_session, require_admin
from app.api.schemas import (
    CreateUserRequest,
    LoginRequest,
    PrincipalOut,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.auth.principal import Principal
from app.auth.service import authenticate, create_tenant_user, register_tenant
from app.auth.tokens import create_access_token
from app.db.repositories import AccountRepository, TenantRepository
from app.errors import Forbidden, Unauthorized

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(principal: Principal, container: Container) -> TokenResponse:
    token, expires_in = create_access_token(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role.value,
        settings=container.settings,
    )
    return TokenResponse(access_token=token, expires_in=expires_in, principal=_out(principal))


def _out(principal: Principal) -> PrincipalOut:
    return PrincipalOut(
        user_id=principal.user_id,
        email=principal.email,
        role=principal.role,
        tenant_id=principal.tenant_id,
        tenant_name=principal.tenant_name,
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    summary="Create a new organisation (tenant) and its first administrator",
)
def register(
    body: RegisterRequest,
    request: Request,
    container: Container = Depends(get_container),
    session: Session = Depends(get_session),
) -> TokenResponse:
    if not container.settings.allow_self_registration:
        raise Forbidden("Self-registration is disabled")
    container.login_limiter.check(f"register:{client_ip(request)}", "register")
    principal = register_tenant(
        AccountRepository(session),
        tenant_name=body.tenant_name,
        email=str(body.email).lower(),
        password=body.password,
    )
    session.commit()
    return _token(principal, container)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest, request: Request, container: Container = Depends(get_container)
) -> TokenResponse:
    email = str(body.email).lower()
    container.login_limiter.check(f"login:{client_ip(request)}", "login")
    container.login_limiter.check(f"login-email:{email}", "login")
    with container.db.session() as session:  # commit the audit row even when login fails
        principal = authenticate(AccountRepository(session), email=email, password=body.password)
    if principal is None:
        raise Unauthorized("Invalid email or password")
    return _token(principal, container)


@router.get("/me", response_model=PrincipalOut)
def me(principal: Principal = Depends(get_principal)) -> PrincipalOut:
    return _out(principal)


@router.get("/users", response_model=list[UserOut], summary="List users in your organisation (admin)")
def list_users(admin: Principal = Depends(require_admin), session: Session = Depends(get_session)) -> list:
    return TenantRepository(session, admin.tenant_id).list_users()


@router.post(
    "/users",
    response_model=PrincipalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a user to your organisation (admin)",
)
def create_user(
    body: CreateUserRequest,
    admin: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> PrincipalOut:
    created = create_tenant_user(
        AccountRepository(session),
        admin,
        email=str(body.email).lower(),
        password=body.password,
        role=body.role,
    )
    session.commit()
    return _out(created)
