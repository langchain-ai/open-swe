"""Tests for the OrcaRouter named provider in agent.utils.model."""

from typing import Any
from unittest.mock import patch

import pytest

from agent.utils import model

ORCAROUTER = "orcarouter:auto"


def _capture() -> tuple[dict[str, Any], Any]:
    captured: dict[str, Any] = {}

    def _fake(model: str, **kwargs: Any) -> str:
        captured["model"] = model
        captured.update(kwargs)
        return "MODEL"

    return captured, _fake


def _make_model(model_id: str, **kwargs: Any) -> dict[str, Any]:
    model._MODEL_CACHE.clear()
    captured, fake = _capture()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(model_id, use_gateway=False, **kwargs)
    return captured


def test_orcarouter_routes_through_openai_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    captured = _make_model(ORCAROUTER)
    assert captured["model"] == "orcarouter/auto"
    assert captured["model_provider"] == "openai"
    assert captured["base_url"] == model.ORCAROUTER_BASE_URL
    assert captured["api_key"] == "sk-orca-test"


def test_orcarouter_bare_model_gets_namespace_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    captured = _make_model("orcarouter:auto")
    assert captured["model"] == "orcarouter/auto"


def test_orcarouter_explicit_provider_model_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    # A provider/model id with a slash is routed directly by OrcaRouter, so the
    # ``orcarouter/`` namespace prefix is not added.
    captured = _make_model("orcarouter:deepseek/deepseek-v4-pro")
    assert captured["model"] == "deepseek/deepseek-v4-pro"


def test_orcarouter_requires_api_key() -> None:
    with pytest.raises(ValueError, match="ORCAROUTER_API_KEY is required"):
        _make_model(ORCAROUTER)


def test_orcarouter_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    monkeypatch.setenv("ORCAROUTER_BASE_URL", "https://orcarouter.example/v1")
    captured = _make_model(ORCAROUTER)
    assert captured["base_url"] == "https://orcarouter.example/v1"


def test_orcarouter_gets_default_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    captured = _make_model(ORCAROUTER)
    assert captured["timeout"] == model.DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert captured["max_retries"] == model.DEFAULT_MAX_RETRIES


def test_orcarouter_provider_kwargs_maps_effort() -> None:
    kwargs = model.provider_model_kwargs(ORCAROUTER, "high", max_tokens=1000)
    assert kwargs["reasoning_effort"] == "high"


def test_orcarouter_provider_kwargs_none_disables_reasoning() -> None:
    kwargs = model.provider_model_kwargs(ORCAROUTER, "none", max_tokens=1000)
    assert kwargs["reasoning_effort"] == "none"


def test_orcarouter_provider_kwargs_no_effort_is_empty() -> None:
    kwargs = model.provider_model_kwargs(ORCAROUTER, None, max_tokens=1000)
    assert kwargs == {"max_tokens": 1000}


def test_validate_local_dev_llm_config_requires_orca_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("LLM_MODEL_ID", ORCAROUTER)
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ORCAROUTER_API_KEY is required"):
        model.validate_local_dev_llm_config()
