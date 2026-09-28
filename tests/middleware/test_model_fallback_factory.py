"""Tests for graph fallback middleware construction."""

from unittest.mock import MagicMock

from agent.middleware.model_fallback import ModelFallbackMiddleware, make_fallback_middleware


def test_make_fallback_middleware_returns_empty_for_same_model(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODEL_ID", "openai:gpt-6-sol")

    middleware = make_fallback_middleware(
        "openai:gpt-6-sol",
        use_gateway=False,
        model_factory=MagicMock(),
    )

    assert middleware == []


def test_make_fallback_middleware_builds_configured_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODEL_ID", "anthropic:claude-opus-5-5")
    fallback_model = MagicMock(model_name="claude-opus-5-5")
    model_factory = MagicMock(return_value=fallback_model)

    middleware = make_fallback_middleware(
        "openai:gpt-6-sol",
        use_gateway=True,
        model_factory=model_factory,
    )

    assert len(middleware) == 1
    assert isinstance(middleware[0], ModelFallbackMiddleware)
    model_factory.assert_called_once_with(
        "anthropic:claude-opus-5-5",
        use_gateway=True,
        max_tokens=64_000,
    )
