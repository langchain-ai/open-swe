from collections.abc import Iterator
from typing import Any

import pytest
import runtime_api.models as shared
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from rt.models import _build, build_model
from runtime_api.catalog import ModelSpec
from runtime_api.models import SUPPORTED_MODELS, model_init_kwargs, provider_model_kwargs

_ENV = (
    "LANGSMITH_API_KEY",
    "LANGSMITH_GATEWAY_API_KEY",
    "LANGSMITH_GATEWAY_ENABLED",
    "LANGSMITH_GATEWAY_BASE_URL",
    "LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "BASETEN_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _build.cache_clear()
    yield
    _build.cache_clear()


def _spec(model_id: str, effort: str) -> ModelSpec:
    # As the backend sends it: the provider settings as JSON.
    kwargs = provider_model_kwargs(model_id, effort, max_tokens=64_000)
    return ModelSpec.model_validate({"model_id": model_id, "kwargs": kwargs})


@pytest.mark.parametrize("use_gateway", [False, True])
@pytest.mark.parametrize(
    "option", SUPPORTED_MODELS, ids=[option["id"] for option in SUPPORTED_MODELS]
)
def test_the_runtime_builds_each_model_with_the_backends_settings(
    monkeypatch: pytest.MonkeyPatch, option: Any, use_gateway: bool
) -> None:
    """What reaches ``init_chat_model`` is what the backend's ``make_model`` sends it."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    built: list[tuple[str, dict[str, object]]] = []

    def init_chat_model(model: str, **kwargs: object) -> object:
        built.append((model, kwargs))
        return object()

    monkeypatch.setattr(shared, "init_chat_model", init_chat_model)
    spec = _spec(option["id"], option["default_effort"])

    build_model(spec, use_gateway=use_gateway)

    # The spec's kwargs crossed the wire as JSON, as the backend's never do.
    expected = model_init_kwargs(
        option["id"],
        provider_model_kwargs(option["id"], option["default_effort"], max_tokens=64_000),
        use_gateway=use_gateway,
    )
    assert built == [expected]


def test_an_openai_model_has_the_window_codex_serves_it_at() -> None:
    model = build_model(_spec("openai:gpt-6.1-sol", "xhigh"), use_gateway=False)

    assert isinstance(model, ChatOpenAI)
    assert model.profile is not None and model.profile["max_input_tokens"] == 272_000
    assert model.openai_api_base == "wss://api.openai.com/v1"
    assert model.use_responses_api is True


def test_a_gateway_routed_openai_model_can_use_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "false")

    model = build_model(_spec("openai:gpt-6.1-sol", "high"), use_gateway=True)

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://gateway.smith.langchain.com/openai/v1"
    assert model.use_responses_api is False
    assert model.reasoning_effort == "high"


def test_an_anthropic_model_streams() -> None:
    model = build_model(_spec("anthropic:claude-opus-5-5", "high"), use_gateway=False)

    assert isinstance(model, ChatAnthropic)
    # Runs aren't streamed: without it LangSmith has no time to first token, and a long
    # answer is cut off by the non-streaming request timeout.
    assert model.streaming is True
    assert model.max_retries == 6


def test_a_baseten_model_goes_to_baseten_with_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASETEN_API_KEY", "baseten-key")

    model = build_model(ModelSpec(model_id="baseten:moonshot/kimi", kwargs={}), use_gateway=False)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "moonshot/kimi"
    assert model.openai_api_base == "https://inference.baseten.co/v1"
