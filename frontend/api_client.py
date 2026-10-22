"""Thin HTTP client for the SecureAgentOps API. The UI holds no secrets beyond the user's own token."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []

    def __str__(self) -> str:
        extra = "; ".join(f"{d.get('field')}: {d.get('issue')}" for d in self.details if isinstance(d, dict))
        return f"{self.message}" + (f" ({extra})" if extra else "")


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = httpx.request(
                method, f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, "connection_error", f"Cannot reach the API at {self.base_url}") from exc
        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_error:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise ApiError(
                response.status_code,
                error.get("code", "http_error"),
                error.get("message", f"HTTP {response.status_code}"),
                error.get("details"),
            )
        return body

    # auth
    def login(self, email: str, password: str) -> dict:
        return self._request("POST", "/auth/login", json={"email": email, "password": password})

    def register(self, tenant_name: str, email: str, password: str) -> dict:
        return self._request(
            "POST", "/auth/register", json={"tenant_name": tenant_name, "email": email, "password": password}
        )

    def users(self) -> list[dict]:
        return self._request("GET", "/auth/users")

    def create_user(self, email: str, password: str, role: str) -> dict:
        return self._request("POST", "/auth/users", json={"email": email, "password": password, "role": role})

    # agent
    def chat(self, message: str) -> dict:
        return self._request("POST", "/agent/chat", json={"message": message})

    def runs(self, limit: int = 50) -> list[dict]:
        return self._request("GET", "/agent/runs", params={"limit": limit})

    def tool_invocations(self, limit: int = 50) -> list[dict]:
        return self._request("GET", "/agent/tool-invocations", params={"limit": limit})

    def tools(self) -> list[dict]:
        return self._request("GET", "/agent/tools")

    def approvals(self, status: str | None = None) -> list[dict]:
        return self._request("GET", "/approvals", params={"status": status} if status else None)

    def decide(self, approval_id: str, approve: bool, note: str | None) -> dict:
        action = "approve" if approve else "reject"
        return self._request("POST", f"/approvals/{approval_id}/{action}", json={"note": note or None})

    def incidents(self) -> list[dict]:
        return self._request("GET", "/incidents")

    # documents
    def documents(self) -> list[dict]:
        return self._request("GET", "/documents")

    def upload(self, filename: str, data: bytes) -> dict:
        return self._request("POST", "/documents", files={"file": (filename, data)})

    def delete_document(self, document_id: str) -> None:
        self._request("DELETE", f"/documents/{document_id}")

    def search(self, query: str, top_k: int = 4) -> dict:
        return self._request("GET", "/documents/search", params={"q": query, "top_k": top_k})

    # ops
    def health(self) -> dict:
        return self._request("GET", "/health")

    def ready(self) -> dict:
        try:
            return self._request("GET", "/ready")
        except ApiError as exc:
            if exc.status == 503:
                return {"status": "degraded", "checks": {}}
            raise

    def summary(self) -> dict:
        return self._request("GET", "/ops/summary")

    def audit(self, limit: int = 100) -> list[dict]:
        return self._request("GET", "/ops/audit", params={"limit": limit})
