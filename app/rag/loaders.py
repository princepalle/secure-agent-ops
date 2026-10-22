"""Upload validation and text extraction for PDF, TXT and Markdown."""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.errors import PayloadTooLarge, Unprocessable, UnsupportedMediaType

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".txt": "text", ".md": "markdown", ".markdown": "markdown"}
MAX_PDF_PAGES = 200
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    filename: str
    content_type: str
    text: str
    sha256: str
    size_bytes: int


def safe_filename(name: str) -> str:
    """Strip directories and unusual characters; never trust a client-supplied path."""
    base = PurePath(name.replace("\\", "/")).name
    cleaned = _UNSAFE_CHARS.sub("_", base).strip(" .")[:150]
    return cleaned or "upload"


def _extract_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise UnsupportedMediaType("File has a .pdf extension but is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise Unprocessable("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise Unprocessable(f"PDF has more than {MAX_PDF_PAGES} pages")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except PdfReadError as exc:
        logger.info("rejected unreadable PDF: %s", exc)
        raise Unprocessable("The PDF could not be read") from exc


def _extract_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise UnsupportedMediaType("File looks binary, not text")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Unprocessable("Text files must be UTF-8 encoded") from exc


def parse_upload(filename: str, data: bytes, *, max_bytes: int) -> ParsedDocument:
    name = safe_filename(filename)
    extension = PurePath(name).suffix.lower()
    content_type = ALLOWED_EXTENSIONS.get(extension)
    if content_type is None:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UnsupportedMediaType(f"Unsupported file type '{extension or 'none'}'. Allowed: {allowed}")
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    if not data:
        raise Unprocessable("File is empty")

    text = _extract_pdf(data) if content_type == "pdf" else _extract_text(data)
    if not text.strip():
        raise Unprocessable("No extractable text found in the file")
    return ParsedDocument(
        filename=name,
        content_type=content_type,
        text=text,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
