"""Ingestion and retrieval: the glue between uploads, SQLite metadata and the vector store."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from app.auth.principal import Principal
from app.config import Settings
from app.db.models import Document
from app.db.repositories import TenantRepository
from app.errors import Conflict, NotFound
from app.observability import metrics
from app.observability.tracing import tracer
from app.rag.chunking import chunk_text
from app.rag.loaders import ParsedDocument
from app.rag.store import StoredChunk, VectorStore


@dataclass(frozen=True, slots=True)
class Citation:
    index: int
    document_id: str
    filename: str
    chunk_index: int
    score: float
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[StoredChunk]
    latency_ms: float

    def citations(self, snippet_chars: int = 240) -> list[Citation]:
        return [
            Citation(
                index=i + 1,
                document_id=c.document_id,
                filename=c.filename,
                chunk_index=c.chunk_index,
                score=c.score,
                snippet=c.text[:snippet_chars] + ("…" if len(c.text) > snippet_chars else ""),
            )
            for i, c in enumerate(self.chunks)
        ]


class RagService:
    def __init__(self, settings: Settings, store: VectorStore) -> None:
        self.settings = settings
        self.store = store

    def ingest(self, repo: TenantRepository, principal: Principal, parsed: ParsedDocument) -> Document:
        if repo.tenant_id != principal.tenant_id:
            raise PermissionError("repository tenant does not match principal")
        if repo.find_document_by_sha(parsed.sha256):
            raise Conflict(f"'{parsed.filename}' has already been indexed")
        chunks = chunk_text(
            parsed.text, chunk_size=self.settings.chunk_size, overlap=self.settings.chunk_overlap
        )
        document = repo.add_document(
            Document(
                filename=parsed.filename,
                content_type=parsed.content_type,
                sha256=parsed.sha256,
                size_bytes=parsed.size_bytes,
                chunk_count=len(chunks),
                uploaded_by=principal.user_id,
            )
        )
        with tracer.start_as_current_span("rag.index") as span:
            span.set_attribute("rag.chunks", len(chunks))
            self.store.add_chunks(
                principal.tenant_id, document.id, document.filename, [c.text for c in chunks]
            )
        repo.add_audit(
            "document.indexed",
            actor_id=principal.user_id,
            target=document.id,
            detail={"filename": document.filename, "chunks": len(chunks)},
        )
        return document

    def delete(self, repo: TenantRepository, principal: Principal, document_id: str) -> None:
        document = repo.get_document(document_id)
        if document is None:
            raise NotFound("Document not found")
        self.store.delete_document(principal.tenant_id, document_id)
        repo.delete_document(document_id)
        repo.add_audit(
            "document.deleted",
            actor_id=principal.user_id,
            target=document_id,
            detail={"filename": document.filename},
        )

    def search(self, tenant_id: str, query: str, top_k: int | None = None) -> RetrievalResult:
        started = time.perf_counter()
        with tracer.start_as_current_span("rag.retrieve") as span:
            raw = self.store.query(tenant_id, query, top_k or self.settings.retrieval_top_k)
            chunks = [c for c in raw if c.score >= self.settings.retrieval_min_score]
            span.set_attribute("rag.candidates", len(raw))
            span.set_attribute("rag.results", len(chunks))
        latency = (time.perf_counter() - started) * 1000
        metrics.RETRIEVAL_LATENCY.observe(latency / 1000)
        return RetrievalResult(chunks=chunks, latency_ms=round(latency, 2))
