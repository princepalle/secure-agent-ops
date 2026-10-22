"""Environment-driven configuration.

Every setting can be supplied as an environment variable prefixed with ``SAO_``
(for example ``SAO_LLM_PROVIDER=bedrock``) or through a local ``.env`` file.
Nothing secret has a default value in source code.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAO_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    environment: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    data_dir: Path = Path("./data")
    database_url: str | None = None

    # --- Authentication -------------------------------------------------
    jwt_secret: SecretStr | None = None
    jwt_issuer: str = "secureagentops"
    jwt_audience: str = "secureagentops-api"
    access_token_minutes: int = Field(default=60, ge=5, le=24 * 60)
    allow_self_registration: bool = True

    # --- LLM ------------------------------------------------------------
    llm_provider: Literal["mock", "bedrock"] = "mock"
    aws_region: str = "us-east-1"
    bedrock_model_id: str | None = None
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=600, ge=50, le=4000)
    # Optional pricing (USD per 1K tokens). Cost is only estimated when both are set.
    price_input_per_1k_tokens: float | None = Field(default=None, ge=0)
    price_output_per_1k_tokens: float | None = Field(default=None, ge=0)

    # --- RAG ------------------------------------------------------------
    embedding_provider: Literal["hash", "bedrock"] = "hash"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dim: int = Field(default=384, ge=64, le=4096)
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=120, ge=0, le=2000)
    retrieval_top_k: int = Field(default=4, ge=1, le=8)
    retrieval_min_score: float = Field(default=0.08, ge=0, le=1)
    max_upload_mb: float = Field(default=5, gt=0, le=50)

    # --- Agent & tools --------------------------------------------------
    max_message_chars: int = Field(default=4000, ge=100, le=20000)
    tool_timeout_seconds: float = Field(default=5, gt=0, le=60)
    approval_ttl_hours: int = Field(default=24, ge=1, le=24 * 14)
    require_distinct_approver: bool = True

    # --- Abuse protection -----------------------------------------------
    rate_limit_per_minute: int = Field(default=30, ge=1)
    login_rate_limit_per_minute: int = Field(default=10, ge=1)

    # --- Observability --------------------------------------------------
    otel_service_name: str = "secureagentops-api"
    otel_exporter_otlp_endpoint: str | None = None
    otel_console_export: bool = False
    log_level: str = "INFO"

    # --- HTTP -----------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=list)

    # --- Demo data ------------------------------------------------------
    seed_demo_data: bool = False
    demo_password: SecretStr | None = None

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("SAO_CHUNK_OVERLAP must be smaller than SAO_CHUNK_SIZE")
        if self.llm_provider == "bedrock" and not self.bedrock_model_id:
            raise ValueError("SAO_BEDROCK_MODEL_ID is required when SAO_LLM_PROVIDER=bedrock")

        secret = self.jwt_secret.get_secret_value() if self.jwt_secret else ""
        if len(secret) < MIN_SECRET_LENGTH:
            if self.environment == "production":
                raise ValueError(
                    f"SAO_JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters in production"
                )
            # Development/test: use an ephemeral secret. Tokens stop working on restart.
            self.jwt_secret = SecretStr(secrets.token_urlsafe(48))
            if self.environment == "development":
                logger.warning("SAO_JWT_SECRET not set; using an ephemeral secret for this process")
        if self.environment == "production" and self.seed_demo_data:
            raise ValueError("SAO_SEED_DEMO_DATA must be false in production")
        return self

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'secureagentops.db').as_posix()}"

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def pricing_configured(self) -> bool:
        return self.price_input_per_1k_tokens is not None and self.price_output_per_1k_tokens is not None

    @property
    def jwt_secret_value(self) -> str:
        assert self.jwt_secret is not None  # set by the validator  # noqa: S101
        return self.jwt_secret.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
