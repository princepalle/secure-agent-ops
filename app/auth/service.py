"""Registration, login and user management."""

from __future__ import annotations

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.principal import Principal, Role
from app.auth.tokens import TokenError, decode_access_token
from app.config import Settings
from app.db.models import User
from app.db.repositories import AccountRepository, TenantRepository
from app.errors import Conflict, Forbidden, Unauthorized


def _principal(user: User, tenant_name: str) -> Principal:
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        email=user.email,
        role=Role(user.role),
    )


def register_tenant(accounts: AccountRepository, *, tenant_name: str, email: str, password: str) -> Principal:
    """Create a new tenant and its first administrator.

    Joining an *existing* tenant is deliberately impossible here: otherwise
    anyone who guessed a tenant name could register into it. Additional users
    are created by that tenant's administrators.
    """
    if accounts.get_tenant_by_name(tenant_name):
        raise Conflict("That organisation name is taken. Ask its administrator to add you.")
    if accounts.get_user_by_email(email):
        raise Conflict("An account with that email already exists")
    tenant = accounts.create_tenant(tenant_name)
    user = User(
        tenant_id=tenant.id, email=email, password_hash=hash_password(password), role=Role.ADMIN.value
    )
    TenantRepository(accounts.session, tenant.id).add_user(user)
    accounts.add_audit("tenant.registered", tenant_id=tenant.id, actor_id=user.id, target=tenant.id)
    return _principal(user, tenant.name)


def create_tenant_user(
    accounts: AccountRepository, admin: Principal, *, email: str, password: str, role: Role
) -> Principal:
    if not admin.is_admin:
        raise Forbidden("Only administrators can add users")
    if accounts.get_user_by_email(email):
        raise Conflict("An account with that email already exists")
    user = User(
        tenant_id=admin.tenant_id, email=email, password_hash=hash_password(password), role=role.value
    )
    repo = TenantRepository(accounts.session, admin.tenant_id)
    repo.add_user(user)
    repo.add_audit("user.created", actor_id=admin.user_id, target=user.id, detail={"role": role.value})
    return _principal(user, admin.tenant_name)


def authenticate(accounts: AccountRepository, *, email: str, password: str) -> Principal | None:
    """Return the principal, or ``None`` (after auditing the failure) so the caller can commit the audit."""
    user = accounts.get_user_by_email(email)
    if (
        not verify_password(password, user.password_hash if user else None)
        or user is None
        or not user.is_active
    ):
        accounts.add_audit(
            "auth.login_failed", tenant_id=user.tenant_id if user else None, target=user.id if user else None
        )
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    tenant = accounts.get_tenant(user.tenant_id)
    accounts.add_audit("auth.login", tenant_id=user.tenant_id, actor_id=user.id)
    return _principal(user, tenant.name if tenant else "")


def principal_from_token(accounts: AccountRepository, token: str, settings: Settings) -> Principal:
    try:
        claims = decode_access_token(token, settings)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc
    user = accounts.get_user(claims["sub"])
    # The tenant in the token must still match the user's tenant in the database.
    if user is None or not user.is_active or user.tenant_id != claims["tid"]:
        raise Unauthorized("Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    tenant = accounts.get_tenant(user.tenant_id)
    return _principal(user, tenant.name if tenant else "")
