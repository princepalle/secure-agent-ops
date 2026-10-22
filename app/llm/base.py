from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GroundingSource:
    index: int
    filename: str
    text: str


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    prompt: str
    max_tokens: int = 600
    # Structured copy of the grounding material. Real providers read it from the
    # prompt; the mock provider uses it to build a deterministic, cited answer.
    question: str = ""
    sources: list[GroundingSource] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    # Token counts reported by the provider. ``None`` means the provider did not
    # report them (the mock never does) - callers must not present them as measured.
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str
    model: str
    # True when the model may *propose* an intent/tool (still subject to policy).
    supports_planning: bool

    def complete(self, request: LLMRequest) -> LLMResponse: ...
