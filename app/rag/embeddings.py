"""Embedding providers.

``HashingEmbedder`` is a deterministic, dependency-free fallback: signed
feature hashing of word unigrams and bigrams with sublinear term weighting,
L2-normalised. It captures lexical overlap (not semantics), needs no network
and gives identical vectors in every process, which makes retrieval tests
reproducible. Swap in ``BedrockEmbedder`` for semantic retrieval.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter
from typing import Protocol

from app.config import Settings

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    (
        "a an and are as at be been but by can could do does did for from had has have how i if in "
        "into is it its may me my no not of on or our should so than that the their them then "
        "there these they this to was we were what when where which who why will with would you "
        "your"
    ).split()
)
# Longest first. A light suffix stripper (not a full Porter stemmer) so that
# approve / approval / approved and tool / tools land on the same feature.
_SUFFIXES = (
    "ations",
    "ation",
    "ings",
    "ing",
    "ions",
    "ion",
    "ies",
    "ied",
    "ers",
    "er",
    "ed",
    "es",
    "al",
    "ly",
    "s",
    "e",
)


def stem(token: str) -> str:
    if token.isdigit():
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed and light stemming. Hyphenated words are split."""
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.name = f"hash{dimension}"

    def _features(self, text: str) -> Counter[str]:
        tokens = tokenize(text)
        features: Counter[str] = Counter(tokens)
        for left, right in itertools.pairwise(tokens):
            features[f"{left} {right}"] += 1
        return features

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for feature, count in self._features(text).items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimension
            sign = 1.0 if (value >> 63) & 1 else -1.0
            weight = (1.0 + math.log(count)) * (0.6 if " " in feature else 1.0)
            vector[index] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Chroma rejects zero vectors under cosine distance; use a tiny constant vector.
            return [1.0 / math.sqrt(self.dimension)] * self.dimension
        return [v / norm for v in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "bedrock":
        from app.rag.bedrock_embeddings import BedrockEmbedder

        return BedrockEmbedder(settings)
    return HashingEmbedder(settings.embedding_dim)
