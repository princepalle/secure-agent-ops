from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMClient


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "bedrock":
        from app.llm.bedrock import BedrockLLM

        return BedrockLLM(settings)
    from app.llm.mock import MockLLM

    return MockLLM()
