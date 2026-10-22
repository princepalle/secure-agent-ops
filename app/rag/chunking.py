"""Character-window chunking that prefers to break on paragraph, line or sentence boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass

_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str
    start: int
    end: int


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(_WS.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def _best_break(text: str, start: int, hard_end: int, min_end: int) -> int:
    """Latest natural break in [min_end, hard_end], else hard_end."""
    window = text[min_end:hard_end]
    for separator in ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "):
        pos = window.rfind(separator)
        if pos != -1:
            return min_end + pos + len(separator)
    return hard_end


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[Chunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    text = normalise(text)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        hard_end = min(start + chunk_size, len(text))
        end = (
            hard_end if hard_end == len(text) else _best_break(text, start, hard_end, start + chunk_size // 2)
        )
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(index=len(chunks), text=piece, start=start, end=end))
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        # Begin the overlap at a sentence boundary if one is inside it, else at a word boundary.
        if overlap and next_start < end:
            sentence = min(
                (p for p in (text.find(s, next_start, end) for s in (". ", "\n")) if p != -1), default=-1
            )
            space = text.find(" ", next_start, end)
            if sentence != -1 and sentence + 2 < end:
                next_start = sentence + 1
                while next_start < end and text[next_start] in " \n":
                    next_start += 1
            elif space != -1:
                next_start = space + 1
        start = next_start
    return chunks
