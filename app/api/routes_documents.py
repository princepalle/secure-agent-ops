from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status

from app.api.deps import Container, get_container, rate_limited_user, require_admin, tenant_repo
from app.api.schemas import DocumentOut, SearchHit, SearchResponse
from app.auth.principal import Principal
from app.db.repositories import TenantRepository
from app.errors import PayloadTooLarge
from app.observability import metrics
from app.rag.loaders import parse_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index a PDF, TXT or Markdown file into your organisation's knowledge base",
)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(rate_limited_user),
    container: Container = Depends(get_container),
    repo: TenantRepository = Depends(tenant_repo),
) -> object:
    limit = container.settings.max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 64 * 1024:  # allow multipart overhead
        raise PayloadTooLarge(f"File exceeds the {container.settings.max_upload_mb:g} MB limit")
    data = await file.read(limit + 1)  # read at most limit+1 bytes, never the whole stream
    parsed = parse_upload(file.filename or "upload", data, max_bytes=limit)
    document = container.rag.ingest(repo, principal, parsed)
    repo.session.commit()  # commit before responding so the client never sees an uncommitted success
    metrics.DOCUMENTS_INDEXED.inc()
    return document


@router.get("", response_model=list[DocumentOut])
def list_documents(repo: TenantRepository = Depends(tenant_repo)) -> list:
    return repo.list_documents()


@router.get("/search", response_model=SearchResponse, summary="Direct retrieval (no LLM)")
def search(
    q: str = Query(min_length=2, max_length=500),
    top_k: int = Query(4, ge=1, le=8),
    principal: Principal = Depends(rate_limited_user),
    container: Container = Depends(get_container),
) -> SearchResponse:
    result = container.rag.search(principal.tenant_id, q, top_k)
    return SearchResponse(
        query=q, latency_ms=result.latency_ms, results=[SearchHit(**c.to_dict()) for c in result.citations()]
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document and its vectors (admin)",
)
def delete(
    document_id: str,
    admin: Principal = Depends(require_admin),
    container: Container = Depends(get_container),
    repo: TenantRepository = Depends(tenant_repo),
) -> None:
    container.rag.delete(repo, admin, document_id)
    repo.session.commit()
