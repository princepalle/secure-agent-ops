"""Deterministic mock LLM for demos and tests.

It never calls a network service and never reports token counts. Answers
are extractive: the sentences from the retrieved sources that best overlap
the question, each tagged with its citation number, so grounding and
citations can be demonstrated end to end without a paid model.
"""

from __future__ import annotations

import re

from app.llm.base import GroundingSource, LLMRequest, LLMResponse
from app.rag.embeddings import tokenize

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKDOWN = re.compile(r"^[#>*\-\s|`]+|[*`|]+")


def sentences_with_headings(text: str) -> list[tuple[str, str]]:
    """``(nearest markdown heading, sentence)`` pairs. Headings and short fragments are not sentences."""
    out: list[tuple[str, str]] = []
    heading = ""
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            heading = _MARKDOWN.sub("", line).strip()
            continue
        for raw in _SENTENCE_SPLIT.split(line):
            sentence = _MARKDOWN.sub("", raw).strip()
            if len(sentence) >= 25 and (sentence[-1] in ".!?:" or len(sentence.split()) >= 8):
                out.append((heading, sentence))
    return out


def split_sentences(text: str) -> list[str]:
    return [sentence for _, sentence in sentences_with_headings(text)]


def rank_sentences(query: str, sources: list[GroundingSource]) -> list[tuple[float, str, int]]:
    """Sentences ordered by term overlap with ``query`` (section headings count half), then source rank,
    then position."""
    terms = set(tokenize(query))
    ranked = []
    for position, source in enumerate(sources):
        for order, (heading, sentence) in enumerate(sentences_with_headings(source.text)):
            sentence_terms = set(tokenize(sentence))
            overlap = len(terms & sentence_terms) + 0.5 * len(
                terms & (set(tokenize(heading)) - sentence_terms)
            )
            ranked.append((overlap - position * 0.01 - order * 0.001, sentence, source.index))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


class MockLLM:
    provider = "mock"
    model = "mock-extractive-v1"
    supports_planning = False

    def __init__(self, max_points: int = 3) -> None:
        self.max_points = max_points

    def _rank(self, question: str, sources: list[GroundingSource]) -> list[tuple[float, str, int]]:
        return rank_sentences(question, sources)

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not request.sources:
            text = (
                "I couldn't find anything in your organisation's knowledge base about that. "
                "Try rephrasing, or upload a document that covers it."
            )
            return LLMResponse(text=text, provider=self.provider, model=self.model)

        picked: list[tuple[str, int]] = []
        for score, sentence, index in self._rank(request.question, request.sources):
            if len(picked) >= self.max_points or (picked and score < 1):
                break
            if sentence not in (s for s, _ in picked):
                picked.append((sentence, index))
        lines = [f"- {sentence} [{index}]" for sentence, index in picked]
        text = "Here's what your knowledge base says:\n\n" + "\n".join(lines)
        return LLMResponse(text=text, provider=self.provider, model=self.model)
