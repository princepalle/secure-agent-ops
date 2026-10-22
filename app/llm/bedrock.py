"""Amazon Bedrock adapter using the model-agnostic Converse API (optional extra: ``bedrock``).

Credentials come from the standard AWS chain (env vars, profile, or an IAM
role) - never from this application's config. Token counts come from the
``usage`` block Bedrock returns, so they are reported as *measured*.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.llm.base import LLMError, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class BedrockLLM:
    provider = "bedrock"
    supports_planning = True

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError("Install the 'bedrock' extra to use SAO_LLM_PROVIDER=bedrock") from exc
            client = boto3.client(
                "bedrock-runtime",
                region_name=settings.aws_region,
                config=Config(
                    connect_timeout=5,
                    read_timeout=settings.llm_timeout_seconds,
                    retries={"max_attempts": 3, "mode": "adaptive"},
                ),
            )
        self._client = client
        self.model = settings.bedrock_model_id or ""

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = self._client.converse(  # type: ignore[attr-defined]
                modelId=self.model,
                system=[{"text": request.system}],
                messages=[{"role": "user", "content": [{"text": request.prompt}]}],
                inferenceConfig={"maxTokens": request.max_tokens, "temperature": 0.0},
            )
        except Exception as exc:
            # botocore errors can include account ids / ARNs; log them, return a generic error.
            logger.warning("bedrock converse failed: %s", type(exc).__name__)
            raise LLMError("The language model request failed") from exc

        blocks = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(block.get("text", "") for block in blocks).strip()
        usage = response.get("usage", {})
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
        )
