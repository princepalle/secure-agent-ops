"""Tenant-isolated vector store on ChromaDB.

Isolation is enforced twice:
1. Each tenant gets its own Chroma collection, so a query physically cannot
   reach another tenant's vectors.
2. Every chunk also carries ``tenant_id`` metadata; queries filter on it and
   results are re-checked before they are returned. If either layer were
   misconfigured, the other still holds.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.rag.embeddings import Embedder

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class StoredChunk:
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: float


class TenantIsolationError(RuntimeError):
    pass


class VectorStore:
    def __init__(self, path: str, embedder: Embedder) -> None:
        self.embedder = embedder
        self._client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False)
        )

    def _collection(self, tenant_id: str):
        if not _SAFE_ID.match(tenant_id):
            raise TenantIsolationError("tenant id has an unexpected format")
        return self._client.get_or_create_collection(
            name=f"t_{tenant_id}_{self.embedder.name}",
            metadata={"tenant_id": tenant_id, "embedder": self.embedder.name},
            configuration={"hnsw": {"space": "cosine"}},
            embedding_function=None,
        )

    def add_chunks(self, tenant_id: str, document_id: str, filename: str, texts: list[str]) -> int:
        if not texts:
            return 0
        collection = self._collection(tenant_id)
        collection.add(
            ids=[f"{document_id}:{i}" for i in range(len(texts))],
            documents=texts,
            embeddings=self.embedder.embed(texts),
            metadatas=[
                {"tenant_id": tenant_id, "document_id": document_id, "filename": filename, "chunk_index": i}
                for i in range(len(texts))
            ],
        )
        return len(texts)

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        self._collection(tenant_id).delete(
            where={"$and": [{"tenant_id": tenant_id}, {"document_id": document_id}]}
        )

    def count(self, tenant_id: str) -> int:
        return self._collection(tenant_id).count()

    def heartbeat(self) -> bool:
        try:
            self._client.heartbeat()
            return True
        except Exception:  # pragma: no cover - only on a broken store
            logger.exception("vector store heartbeat failed")
            return False

    def query(self, tenant_id: str, text: str, top_k: int) -> list[StoredChunk]:
        collection = self._collection(tenant_id)
        if collection.count() == 0:
            return []
        result: Any = collection.query(
            query_embeddings=self.embedder.embed([text]),
            n_results=min(top_k, collection.count()),
            where={"tenant_id": tenant_id},
            include=["documents", "metadatas", "distances"],
        )
        chunks: list[StoredChunk] = []
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            if meta.get("tenant_id") != tenant_id:  # defence in depth; should be unreachable
                logger.error("dropped cross-tenant chunk from query result")
                continue
            chunks.append(
                StoredChunk(
                    document_id=str(meta["document_id"]),
                    filename=str(meta["filename"]),
                    chunk_index=int(meta["chunk_index"]),
                    text=doc,
                    score=round(1.0 - float(distance), 4),
                )
            )
        return chunks
