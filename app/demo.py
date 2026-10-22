"""Idempotent demo data: two tenants, an admin and a standard user in each, and a small knowledge base.

Run with ``python -m app.demo`` or set ``SAO_SEED_DEMO_DATA=true`` (refused in production).
The password comes from ``SAO_DEMO_PASSWORD``; if unset, a random one is generated and written
to ``<data_dir>/demo-credentials.txt`` with owner-only permissions - never printed or committed.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from app.auth.passwords import hash_password
from app.auth.principal import Principal, Role
from app.config import Settings, get_settings
from app.db.models import User
from app.db.repositories import AccountRepository, TenantRepository
from app.db.session import Database
from app.errors import Conflict
from app.rag.loaders import parse_upload
from app.rag.service import RagService

logger = logging.getLogger(__name__)

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
DEMO_TENANTS = {
    "acme": ["admin@acme.example", "analyst@acme.example"],
    "globex": ["admin@globex.example", "analyst@globex.example"],
}


def _demo_password(settings: Settings) -> str:
    if settings.demo_password and len(settings.demo_password.get_secret_value()) >= 12:
        return settings.demo_password.get_secret_value()
    password = f"Demo-{secrets.token_urlsafe(12)}-1"
    path = settings.data_dir / "demo-credentials.txt"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("Demo accounts (generated - development only)\n")
        for emails in DEMO_TENANTS.values():
            for email in emails:
                handle.write(f"{email}  {password}\n")
    logger.warning("demo password generated; see %s", path)
    return password


def seed_demo_data(settings: Settings, db: Database, rag: RagService) -> bool:
    """Returns False when demo data already exists."""
    with db.session() as session:
        if AccountRepository(session).get_tenant_by_name("acme"):
            return False
    password_hash = hash_password(_demo_password(settings))

    for tenant_name, (admin_email, user_email) in DEMO_TENANTS.items():
        with db.session() as session:
            accounts = AccountRepository(session)
            tenant = accounts.create_tenant(tenant_name)
            repo = TenantRepository(session, tenant.id)
            admin = repo.add_user(User(email=admin_email, password_hash=password_hash, role=Role.ADMIN.value))
            repo.add_user(User(email=user_email, password_hash=password_hash, role=Role.USER.value))
            repo.add_audit("demo.seeded", actor_id=None, target=tenant.id)
            principal = Principal(
                user_id=admin.id,
                tenant_id=tenant.id,
                tenant_name=tenant_name,
                email=admin_email,
                role=Role.ADMIN,
            )
            for path in sorted((SAMPLE_DIR / tenant_name).glob("*")):
                if path.suffix.lower() not in {".md", ".txt", ".pdf"}:
                    continue
                try:
                    rag.ingest(
                        repo,
                        principal,
                        parse_upload(path.name, path.read_bytes(), max_bytes=settings.max_upload_bytes),
                    )
                except Conflict:
                    continue
    logger.info("demo data seeded")
    return True


def main() -> None:  # pragma: no cover - CLI wrapper
    from app.llm import build_llm  # noqa: F401 - validates provider config early
    from app.rag.embeddings import build_embedder
    from app.rag.store import VectorStore

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("Refusing to seed demo data in production")
    db = Database(settings)
    rag = RagService(settings, VectorStore(str(settings.chroma_path), build_embedder(settings)))
    created = seed_demo_data(settings, db, rag)
    print("Demo data seeded." if created else "Demo data already present; nothing to do.")


if __name__ == "__main__":  # pragma: no cover
    main()
