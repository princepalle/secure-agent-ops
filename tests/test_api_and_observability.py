from __future__ import annotations

from tests.conftest import PASSWORD, add_user, chat, register


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "vector_store": "ok"}
    assert ready.json()["llm_provider"] == "mock"


def test_metrics_endpoint_exposes_platform_metrics(client, two_tenants):
    acme, _ = two_tenants
    chat(client, acme.user, "What is the canary policy?")
    chat(client, acme.user, "Show me the usage report")
    body = client.get("/metrics").text
    for name in (
        "sao_http_requests_total",
        "sao_agent_run_duration_seconds",
        "sao_tool_invocations_total",
        "sao_retrieval_duration_seconds",
        "sao_llm_tokens_total",
        "sao_agent_node_duration_seconds",
    ):
        assert name in body, name
    assert 'sao_llm_tokens_total{direction="input",provider="mock",source="estimated"}' in body
    assert 'outcome="denied",tool="usage_report"' in body
    assert acme.user.tenant_id not in body  # no tenant ids in metric labels


def test_route_templates_used_as_metric_labels(client):
    admin = register(client, "acme")
    client.get("/agent/runs/0123456789abcdef0123456789abcdef", headers=admin.headers)
    body = client.get("/metrics").text
    assert 'route="/agent/runs/{run_id}"' in body
    assert "0123456789abcdef0123456789abcdef" not in body


def test_security_headers_and_request_id(client):
    response = client.get("/health", headers={"X-Request-ID": "demo-request-0001"})
    assert response.headers["X-Request-ID"] == "demo-request-0001"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    bogus = client.get("/health", headers={"X-Request-ID": "<script>"})
    assert bogus.headers["X-Request-ID"] != "<script>"


def test_error_format_is_consistent_and_safe(client):
    missing = client.get("/does-not-exist")
    assert missing.status_code == 404
    assert set(missing.json()["error"]) >= {"code", "message", "request_id"}
    admin = register(client, "acme")
    invalid = client.post("/agent/chat", json={"message": ""}, headers=admin.headers)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"][0]["field"] == "message"
    extra = client.post("/agent/chat", json={"message": "hi", "role": "admin"}, headers=admin.headers)
    assert extra.status_code == 422  # unknown fields are rejected, not ignored
    too_long = client.post("/agent/chat", json={"message": "x" * 5000}, headers=admin.headers)
    assert too_long.status_code == 422


def test_unhandled_errors_do_not_leak_details(client, monkeypatch):
    admin = register(client, "acme")

    def explode(*args, **kwargs):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(client.app.state.container.rag, "search", explode)
    from fastapi.testclient import TestClient

    # Behave like a real server: return the 500 response instead of re-raising into the test.
    raw = TestClient(client.app, raise_server_exceptions=False)
    response = raw.get("/documents/search", params={"q": "anything"}, headers=admin.headers)
    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert response.json()["error"]["code"] == "internal_error"


def test_agent_survives_internal_failure(client, monkeypatch):
    admin = register(client, "acme")

    def explode(*args, **kwargs):
        raise RuntimeError("internal")

    monkeypatch.setattr(client.app.state.container.agent.deps.classifier, "classify", explode)
    run = chat(client, admin, "What is our retention policy?")
    assert run["status"] == "error"
    assert run["response"] == "Something went wrong while handling that request."


def test_login_rate_limit(app_factory):
    client = app_factory(login_rate_limit_per_minute=3)
    for _ in range(3):
        assert (
            client.post("/auth/login", json={"email": "a@b.example", "password": "wrong-pass-1"}).status_code
            == 401
        )
    limited = client.post("/auth/login", json={"email": "a@b.example", "password": "wrong-pass-1"})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_per_user_rate_limit_on_agent(app_factory):
    client = app_factory(rate_limit_per_minute=2)
    admin = register(client, "acme")
    assert client.post("/agent/chat", json={"message": "hello"}, headers=admin.headers).status_code == 200
    assert client.post("/agent/chat", json={"message": "hello"}, headers=admin.headers).status_code == 200
    assert client.post("/agent/chat", json={"message": "hello"}, headers=admin.headers).status_code == 429


def test_ops_summary_labels_estimates_honestly(client, two_tenants):
    acme, _ = two_tenants
    chat(client, acme.user, "What is the canary policy for new model versions?")
    summary = client.get("/ops/summary", headers=acme.admin.headers).json()
    assert summary["scope"] == "tenant"
    assert summary["tokens_measured"] == 0
    assert summary["tokens_estimated"] > 0
    assert "estimates" in summary["token_note"]
    assert summary["estimated_cost_usd"] is None
    assert summary["latency_ms_p50"] is not None
    user_summary = client.get("/ops/summary", headers=acme.user.headers).json()
    assert user_summary["scope"] == "user"


def test_audit_trail_is_admin_only_and_records_security_events(client):
    admin = register(client, "acme")
    user = add_user(client, admin, "analyst@acme.example")
    client.post("/auth/login", json={"email": "analyst@acme.example", "password": "Wrong-password-1"})
    assert client.get("/ops/audit", headers=user.headers).status_code == 403
    actions = {e["action"] for e in client.get("/ops/audit", headers=admin.headers).json()}
    assert {"tenant.registered", "user.created", "auth.login", "auth.login_failed"} <= actions


def test_self_registration_can_be_disabled(app_factory):
    client = app_factory(allow_self_registration=False)
    response = client.post(
        "/auth/register", json={"tenant_name": "acme", "email": "a@acme.example", "password": PASSWORD}
    )
    assert response.status_code == 403


def test_tool_catalogue_lists_schemas(client):
    admin = register(client, "acme")
    tools = {t["name"]: t for t in client.get("/agent/tools", headers=admin.headers).json()}
    assert tools["create_incident"]["requires_approval"] is True
    assert tools["usage_report"]["allowed_roles"] == ["admin"]
    assert "properties" in tools["document_search"]["input_schema"]
