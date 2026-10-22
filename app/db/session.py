from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Base


def create_db_engine(settings: Settings) -> Engine:
    url = settings.resolved_database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    Base.metadata.create_all(engine)
    return engine


class Database:
    """Owns the engine and hands out short-lived sessions."""

    def __init__(self, settings: Settings) -> None:
        self.engine = create_db_engine(settings)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
