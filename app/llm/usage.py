"""Token and cost accounting that never passes an estimate off as a measurement."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config import Settings
from app.llm.base import LLMResponse
from app.observability import metrics

CHARS_PER_TOKEN_ESTIMATE = 4  # rough English-text heuristic; labelled "estimated" everywhere


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    source: str  # measured | estimated | none
    estimated_cost_usd: float | None

    @staticmethod
    def none() -> Usage:
        return Usage(None, None, "none", None)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE) if text else 0


def account(response: LLMResponse, prompt_text: str, settings: Settings) -> Usage:
    if response.input_tokens is not None and response.output_tokens is not None:
        source = "measured"
        input_tokens, output_tokens = response.input_tokens, response.output_tokens
    else:
        source = "estimated"
        input_tokens, output_tokens = estimate_tokens(prompt_text), estimate_tokens(response.text)

    cost = None
    # No cost for the mock: there is no real model, so any number would be invented.
    if settings.pricing_configured and response.provider != "mock":
        cost = round(
            input_tokens / 1000 * (settings.price_input_per_1k_tokens or 0)
            + output_tokens / 1000 * (settings.price_output_per_1k_tokens or 0),
            10,
        )
        metrics.LLM_COST.labels(response.provider).inc(cost)

    metrics.LLM_TOKENS.labels(response.provider, "input", source).inc(input_tokens)
    metrics.LLM_TOKENS.labels(response.provider, "output", source).inc(output_tokens)
    return Usage(input_tokens, output_tokens, source, cost)


def combine(first: Usage, second: Usage) -> Usage:
    if first.source == "none":
        return second
    if second.source == "none":
        return first
    source = "measured" if first.source == second.source == "measured" else "estimated"
    costs = [c for c in (first.estimated_cost_usd, second.estimated_cost_usd) if c is not None]
    return Usage(
        (first.input_tokens or 0) + (second.input_tokens or 0),
        (first.output_tokens or 0) + (second.output_tokens or 0),
        source,
        round(sum(costs), 10) if costs else None,
    )
