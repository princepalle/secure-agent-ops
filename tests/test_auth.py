from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.db.models import User
from tests.conftest import PASSWORD, add_user, login, register


def test_register_creates_tenant_and_admin(client):
    account = register(client, "acme")
    assert account.role == "admin"
    me = client.get("/auth/me", headers=account.headers).json()
    assert me["tenant_name"] == "acme"
    assert me["email"] == "admin@acme.example"


def test_password_is_hashed_with_argon2(client):
    register(client, "acme")
    db = client.app.state.container.db
    with db.session() as session:
        user = session.scalar(select(User))
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_cannot_register_into_existing_tenant(client):
    register(client, "acme")
    response = client.post(
        "/auth/register", json={"tenant_name": "acme", "email": "intruder@evil.example", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_duplicate_email_rejected(client):
    register(client, "acme")
    response = client.post(
        "/auth/register", json={"tenant_name": "other", "email": "admin@acme.example", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_weak_password_rejected_without_echoing_it(client):
    response = client.post(
        "/auth/register", json={"tenant_name": "acme", "email": "a@acme.example", "password": "short"}
    )
    assert response.status_code == 422
    assert "short" not in response.text
    assert response.json()["error"]["code"] == "validation_error"


def test_login_failure_is_generic(client):
    register(client, "acme")
    wrong_password = client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": "nope-nope-1"}
    )
    unknown_user = client.post("/auth/login", json={"email": "ghost@acme.example", "password": "nope-nope-1"})
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]


def test_protected_routes_require_token(client):
    for method, path in [
        ("get", "/auth/me"),
        ("post", "/agent/chat"),
        ("get", "/documents"),
        ("get", "/approvals"),
        ("get", "/ops/summary"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_tampered_and_forged_tokens_rejected(client, settings):
    account = register(client, "acme")
    header, payload, signature = account.token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}AA"
    claims = {
        "sub": account.user_id,
        "tid": account.tenant_id,
        "role": "admin",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    wrong_key = jwt.encode(claims, "attacker-key-" + "y" * 40, algorithm="HS256")
    alg_none = jwt.encode(claims, key=None, algorithm="none")
    for token in (tampered, wrong_key, alg_none, "not-a-jwt"):
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_expired_token_rejected(client):
    account = register(client, "acme")
    container = client.app.state.container
    claims = jwt.decode(account.token, options={"verify_signature": False})
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=5)
    expired = jwt.encode(claims, container.settings.jwt_secret_value, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_token_with_mismatched_tenant_rejected(client):
    acme = register(client, "acme")
    globex = register(client, "globex")
    container = client.app.state.container
    claims = jwt.decode(acme.token, options={"verify_signature": False})
    claims["tid"] = globex.tenant_id  # validly signed, but the tenant doesn't match the user
    forged = jwt.encode(claims, container.settings.jwt_secret_value, algorithm="HS256")
    assert client.get("/documents", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_admin_adds_user_and_standard_user_cannot(client):
    admin = register(client, "acme")
    user = add_user(client, admin, "analyst@acme.example")
    assert user.role == "user"
    assert user.tenant_id == admin.tenant_id
    response = client.post(
        "/auth/users",
        json={"email": "x@acme.example", "password": PASSWORD, "role": "admin"},
        headers=user.headers,
    )
    assert response.status_code == 403
    assert client.get("/auth/users", headers=user.headers).status_code == 403


def test_role_and_deactivation_take_effect_immediately(client):
    admin = register(client, "acme")
    user = add_user(client, admin, "analyst@acme.example")
    db = client.app.state.container.db
    with db.session() as session:
        session.get(User, user.user_id).role = "admin"
    # Same token, but the role now comes from the database.
    assert client.get("/auth/users", headers=user.headers).status_code == 200
    with db.session() as session:
        session.get(User, user.user_id).is_active = False
    assert client.get("/auth/me", headers=user.headers).status_code == 401
    assert client.post("/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 401


def test_login_is_case_insensitive_on_email(client):
    register(client, "acme")
    assert login(client, "ADMIN@acme.example").tenant_id
