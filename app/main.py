"""Application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.agent.service import AgentService
from app.api import routes_agent, routes_auth, routes_documents, routes_ops
from app.api.deps import Container
from app.config import Settings, get_settings
from app.db.session import Database
from app.demo import seed_demo_data
from app.errors import install_error_handlers
from app.llm import build_llm
from app.llm.base import LLMClient
from app.observability import metrics
from app.observability.middleware import RequestContextMiddleware
from app.observability.tracing import configure_tracing
from app.rag.embeddings import build_embedder
from app.rag.service import RagService
from app.rag.store import VectorStore
from app.security.ratelimit import SlidingWindowLimiter
from app.tools.builtin import ToolRegistry

logger = logging.getLogger("app")


def build_container(settings: Settings, llm: LLMClient | None = None) -> Container:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings)
    rag = RagService(settings, VectorStore(str(settings.chroma_path), build_embedder(settings)))
    llm = llm or build_llm(settings)
    registry = ToolRegistry()
    return Container(
        settings=settings,
        db=db,
        rag=rag,
        llm=llm,
        registry=registry,
        agent=AgentService(settings, db, rag, llm, registry),
        user_limiter=SlidingWindowLimiter(settings.rate_limit_per_minute),
        login_limiter=SlidingWindowLimiter(settings.login_rate_limit_per_minute),
    )


def create_app(settings: Settings | None = None, *, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    configure_tracing(settings)
    container = build_container(settings, llm)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.seed_demo_data and seed_demo_data(settings, container.db, container.rag):
            logger.info("seeded demo tenants")
        logger.info(
            "SecureAgentOps %s started (env=%s, llm=%s, embeddings=%s)",
            settings.app_version,
            settings.environment,
            container.llm.provider,
            container.rag.store.embedder.name,
        )
        yield
        container.db.engine.dispose()

    show_docs = settings.environment != "production"
    app = FastAPI(
        title="SecureAgentOps API",
        version=settings.app_version,
        description=(
            "Secure, multi-tenant AI agent platform with approval-gated tools and tenant-isolated RAG."
        ),
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.state.container = container
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    for router in (routes_auth.router, routes_agent.router, routes_documents.router, routes_ops.router):
        app.include_router(router)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
    metrics.BUILD_INFO.labels(settings.app_version, settings.llm_provider, settings.embedding_provider).set(1)
    return app


def app_factory() -> FastAPI:  # used by uvicorn --factory
    return create_app()
