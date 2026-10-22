"""One tenant must never see, search, change or approve another tenant's data."""

from __future__ import annotations

import pytest

from app.db.repositories import TenantRepository
from app.rag.store import TenantIsolationError
from tests.conftest import chat

GLOBEX_SECRET_QUESTION = "What is the escalation codeword?"


def test_document_lists_are_isolated(client, two_tenants):
    acme, globex = two_tenants
    acme_docs = {d["filename"] for d in client.get("/documents", headers=acme.user.headers).json()}
    globex_docs = {d["filename"] for d in client.get("/documents", headers=globex.user.headers).json()}
    assert "globex-oncall-handbook.md" in globex_docs
    assert "globex-oncall-handbook.md" not in acme_docs
    assert acme_docs.isdisjoint(globex_docs)


def test_direct_search_never_returns_other_tenants_chunks(client, two_tenants):
    acme, globex = two_tenants
    acme_hits = client.get(
        "/documents/search", params={"q": "BLUE-HERON escalation codeword"}, headers=acme.user.headers
    ).json()["results"]
    assert all("globex" not in h["filename"] for h in acme_hits)
    assert all("BLUE-HERON" not in h["snippet"] for h in acme_hits)
    globex_hits = client.get(
        "/documents/search", params={"q": "BLUE-HERON escalation codeword"}, headers=globex.user.headers
    ).json()["results"]
    assert globex_hits[0]["filename"] == "globex-oncall-handbook.md"


def test_agent_answers_are_isolated(client, two_tenants):
    acme, globex = two_tenants
    acme_run = chat(client, acme.user, GLOBEX_SECRET_QUESTION)
    assert "BLUE-HERON" not in acme_run["response"]
    assert all(c["filename"] != "globex-oncall-handbook.md" for c in acme_run["citations"])
    globex_run = chat(client, globex.user, GLOBEX_SECRET_QUESTION)
    assert "BLUE-HERON" in globex_run["response"]


def test_cannot_read_other_tenants_run_by_id(client, two_tenants):
    acme, globex = two_tenants
    run = chat(client, globex.user, "What is the paging policy?")
    assert client.get(f"/agent/runs/{run['id']}", headers=globex.user.headers).status_code == 200
    assert client.get(f"/agent/runs/{run['id']}", headers=acme.admin.headers).status_code == 404
    acme_runs = client.get("/agent/runs", headers=acme.admin.headers).json()
    assert run["id"] not in {r["id"] for r in acme_runs}


def test_standard_user_cannot_read_colleagues_runs(client, two_tenants):
    acme, _ = two_tenants
    admin_run = chat(client, acme.admin, "What is the canary policy?")
    assert client.get(f"/agent/runs/{admin_run['id']}", headers=acme.user.headers).status_code == 404
    assert admin_run["id"] not in {
        r["id"] for r in client.get("/agent/runs", headers=acme.user.headers).json()
    }


def test_cannot_approve_or_see_other_tenants_approvals(client, two_tenants):
    acme, globex = two_tenants
    chat(client, globex.user, "Open a sev1 incident for the Globex help centre outage")
    globex_pending = client.get(
        "/approvals", params={"status": "pending"}, headers=globex.admin.headers
    ).json()
    assert len(globex_pending) == 1
    approval_id = globex_pending[0]["id"]
    assert client.get("/approvals", headers=acme.admin.headers).json() == []
    response = client.post(f"/approvals/{approval_id}/approve", headers=acme.admin.headers)
    assert response.status_code == 404
    assert client.get("/incidents", headers=globex.admin.headers).json() == []


def test_cannot_delete_other_tenants_document(client, two_tenants):
    acme, globex = two_tenants
    globex_doc = client.get("/documents", headers=globex.admin.headers).json()[0]
    assert client.delete(f"/documents/{globex_doc['id']}", headers=acme.admin.headers).status_code == 404
    assert len(client.get("/documents", headers=globex.admin.headers).json()) == 1


def test_tool_invocations_and_incidents_isolated(client, two_tenants):
    acme, globex = two_tenants
    chat(client, globex.user, "Is the database healthy?")
    acme_tools = client.get("/agent/tool-invocations", headers=acme.admin.headers).json()
    assert acme_tools == []


def test_same_document_can_exist_in_two_tenants(client, two_tenants):
    acme, globex = two_tenants
    data = b"Shared public text about deployment freezes during holidays."
    for org in (acme, globex):
        assert (
            client.post(
                "/documents", files={"file": ("shared.txt", data)}, headers=org.admin.headers
            ).status_code
            == 201
        )


def test_repository_refuses_missing_tenant(client):
    with client.app.state.container.db.session() as session, pytest.raises(ValueError, match="tenant_id"):
        TenantRepository(session, "")


def test_vector_store_rejects_malformed_tenant_id(client):
    store = client.app.state.container.rag.store
    with pytest.raises(TenantIsolationError):
        store.query("../../other", "anything", 3)
