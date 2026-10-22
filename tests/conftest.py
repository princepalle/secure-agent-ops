from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import LLMClient
from app.main import create_app

PASSWORD = "Correct-horse-42-battery"  # gitleaks:allow - test fixture, not a real credential
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


@dataclass
class Account:
    email: str
    token: str
    tenant_id: str
    user_id: str
    role: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "data_dir": tmp_path / "data",
        "jwt_secret": "test-secret-" + "x" * 40,
        "login_rate_limit_per_minute": 1000,
        "rate_limit_per_minute": 1000,
        "seed_demo_data": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable[..., TestClient]:
    clients: list[TestClient] = []

    def factory(llm: LLMClient | None = None, **overrides: object) -> TestClient:
        app = create_app(make_settings(tmp_path, **overrides), llm=llm)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(app_factory: Callable[..., TestClient]) -> Iterator[TestClient]:
    return app_factory()


def register(client: TestClient, tenant: str, email: str | None = None) -> Account:
    email = email or f"admin@{tenant}.example"
    response = client.post(
        "/auth/register", json={"tenant_name": tenant, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    p = body["principal"]
    return Account(email, body["access_token"], p["tenant_id"], p["user_id"], p["role"])


def add_user(client: TestClient, admin: Account, email: str, role: str = "user") -> Account:
    response = client.post(
        "/auth/users", json={"email": email, "password": PASSWORD, "role": role}, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    return login(client, email)


def login(client: TestClient, email: str, password: str = PASSWORD) -> Account:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    p = body["principal"]
    return Account(email, body["access_token"], p["tenant_id"], p["user_id"], p["role"])


def upload(client: TestClient, account: Account, filename: str, data: bytes) -> dict:
    response = client.post("/documents", files={"file": (filename, data)}, headers=account.headers)
    assert response.status_code == 201, response.text
    return response.json()


def upload_sample(client: TestClient, account: Account, tenant_dir: str) -> None:
    for path in sorted((SAMPLE_DIR / tenant_dir).glob("*.md")):
        upload(client, account, path.name, path.read_bytes())


def chat(client: TestClient, account: Account, message: str) -> dict:
    response = client.post("/agent/chat", json={"message": message}, headers=account.headers)
    assert response.status_code == 200, response.text
    return response.json()


@dataclass
class Org:
    admin: Account
    user: Account


@pytest.fixture
def two_tenants(client: TestClient) -> tuple[Org, Org]:
    """acme and globex, each with an admin and a standard user, each with their own sample documents."""
    acme_admin = register(client, "acme")
    globex_admin = register(client, "globex")
    acme = Org(acme_admin, add_user(client, acme_admin, "analyst@acme.example"))
    globex = Org(globex_admin, add_user(client, globex_admin, "analyst@globex.example"))
    upload_sample(client, acme_admin, "acme")
    upload_sample(client, globex_admin, "globex")
    return acme, globex


def make_pdf(text: str) -> bytes:
    """A minimal single-page PDF with real extractable text (no external libraries)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
