"""Configuration safety and the Bedrock adapter (exercised offline with botocore's Stubber)."""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber

from app.llm.base import LLMError, LLMRequest
from app.llm.bedrock import BedrockLLM
from app.llm.usage import account, estimate_tokens
from tests.conftest import make_settings

MODEL_ID = "amazon.nova-lite-v1:0"


def _stubbed(tmp_path):
    settings = make_settings(
        tmp_path,
        llm_provider="bedrock",
        bedrock_model_id=MODEL_ID,
        price_input_per_1k_tokens=0.00006,
        price_output_per_1k_tokens=0.00024,
    )
    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )  # dummy values for the offline stub only
    return settings, client, Stubber(client)


def test_bedrock_converse_reports_measured_usage(tmp_path):
    settings, client, stubber = _stubbed(tmp_path)
    stubber.add_response(
        "converse",
        {
            "output": {
                "message": {"role": "assistant", "content": [{"text": "Escalate after 30 minutes [1]."}]}
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 812, "outputTokens": 41, "totalTokens": 853},
            "metrics": {"latencyMs": 640},
        },
        {
            "modelId": MODEL_ID,
            "system": [{"text": "sys"}],
            "messages": [{"role": "user", "content": [{"text": "prompt"}]}],
            "inferenceConfig": {"maxTokens": 100, "temperature": 0.0},
        },
    )
    with stubber:
        response = BedrockLLM(settings, client=client).complete(
            LLMRequest(system="sys", prompt="prompt", max_tokens=100)
        )
    assert response.text == "Escalate after 30 minutes [1]."
    usage = account(response, "prompt", settings)
    assert usage.source == "measured"
    assert (usage.input_tokens, usage.output_tokens) == (812, 41)
    assert usage.estimated_cost_usd == pytest.approx(812 / 1000 * 0.00006 + 41 / 1000 * 0.00024)


def test_bedrock_errors_become_generic_llm_errors(tmp_path):
    settings, client, stubber = _stubbed(tmp_path)
    stubber.add_client_error(
        "converse",
        service_error_code="AccessDeniedException",
        service_message="User arn:aws:iam::123456789012:user/x is not authorized",
    )
    with stubber, pytest.raises(LLMError) as raised:
        BedrockLLM(settings, client=client).complete(LLMRequest(system="s", prompt="p"))
    assert "123456789012" not in str(raised.value)


def test_mock_usage_is_estimated_and_never_costed(tmp_path):
    from app.llm.mock import MockLLM

    settings = make_settings(tmp_path, price_input_per_1k_tokens=1.0, price_output_per_1k_tokens=1.0)
    response = MockLLM().complete(LLMRequest(system="s", prompt="p", question="q"))
    usage = account(response, "a" * 40, settings)
    assert usage.source == "estimated"
    assert usage.input_tokens == estimate_tokens("a" * 40) == 10
    assert usage.estimated_cost_usd is None


def test_bedrock_requires_model_id(tmp_path):
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        make_settings(tmp_path, llm_provider="bedrock")


def test_production_requires_strong_jwt_secret(tmp_path):
    with pytest.raises(ValueError, match="JWT_SECRET"):
        make_settings(tmp_path, environment="production", jwt_secret="too-short")


def test_production_refuses_demo_seeding(tmp_path):
    with pytest.raises(ValueError, match="SEED_DEMO_DATA"):
        make_settings(tmp_path, environment="production", seed_demo_data=True)


def test_development_generates_ephemeral_secret(tmp_path):
    first = make_settings(tmp_path, environment="development", jwt_secret=None)
    second = make_settings(tmp_path, environment="development", jwt_secret=None)
    assert len(first.jwt_secret_value) >= 32
    assert first.jwt_secret_value != second.jwt_secret_value


def test_demo_seed_is_idempotent_and_isolated(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import login

    settings = make_settings(tmp_path, seed_demo_data=True, demo_password="Demo-password-2024")
    with TestClient(create_app(settings)) as client:
        acme = login(client, "analyst@acme.example", "Demo-password-2024")
        assert len(client.get("/documents", headers=acme.headers).json()) == 3
    with TestClient(create_app(settings)) as client:  # second start: no duplicates
        globex = login(client, "admin@globex.example", "Demo-password-2024")
        assert [d["filename"] for d in client.get("/documents", headers=globex.headers).json()] == [
            "globex-oncall-handbook.md"
        ]
