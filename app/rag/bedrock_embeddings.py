"""Amazon Titan Text Embeddings v2 via Bedrock (optional; requires ``boto3`` and AWS credentials)."""

from __future__ import annotations

import json

from app.config import Settings

_TITAN_V2_DIMENSIONS = {256, 512, 1024}


class BedrockEmbedder:
    def __init__(self, settings: Settings) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("Install the 'bedrock' extra to use SAO_EMBEDDING_PROVIDER=bedrock") from exc
        if settings.embedding_dim not in _TITAN_V2_DIMENSIONS:
            raise ValueError("Titan v2 supports SAO_EMBEDDING_DIM of 256, 512 or 1024")
        self.model_id = settings.bedrock_embedding_model_id
        self.dimension = settings.embedding_dim
        self.name = f"titan{self.dimension}"
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            config=Config(
                read_timeout=settings.llm_timeout_seconds, retries={"max_attempts": 3, "mode": "adaptive"}
            ),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            body = json.dumps({"inputText": text[:8000], "dimensions": self.dimension, "normalize": True})
            response = self._client.invoke_model(
                modelId=self.model_id, body=body, contentType="application/json", accept="application/json"
            )
            vectors.append(json.loads(response["body"].read())["embedding"])
        return vectors
