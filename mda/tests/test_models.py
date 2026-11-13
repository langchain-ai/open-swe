from collections.abc import Iterator
from typing import Any

import anthropic
import pytest
import rt.gateway as gateway_module
import runtime_api.models as shared
from aiohttp import web
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import JsonValue
from rt.backend import Run, RunContext
from rt.gateway import GATEWAY_BEARER
from rt.models import _SDK_RETRY_DISABLE, _build, build_model
from runtime_api.catalog import ModelSpec
from runtime_api.gateway_auth import GatewayChatAnthropic
from runtime_api.loop import GATEWAY_CREDENTIAL, GatewayCredential
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
    """What reaches ``init_chat_model`` is what the backend's ``make_model`` sends it, but
    for the SDK retries of a provider whose retries MDA's model middleware owns."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    built: list[tuple[str, dict[str, object]]] = []

    def init_chat_model(model: str, **kwargs: object) -> object:
        built.append((model, kwargs))
        return object()

    monkeypatch.setattr(shared, "init_chat_model", init_chat_model)
    spec = _spec(option["id"], option["default_effort"])

    model = build_model(spec, use_gateway=use_gateway)

    # The spec's kwargs crossed the wire as JSON, as the backend's never do.
    kwargs = provider_model_kwargs(option["id"], option["default_effort"], max_tokens=64_000)
    provider = option["id"].split(":", 1)[0]
    if provider in _SDK_RETRY_DISABLE:
        kwargs = {**kwargs, "max_retries": _SDK_RETRY_DISABLE[provider]}
    if use_gateway and provider == "anthropic":
        # Its every request asks the run's gateway credential, which init_chat_model can't.
        assert built == []
        assert isinstance(model, GatewayChatAnthropic)
        return
    expected_id, expected = model_init_kwargs(
        option["id"], kwargs, use_gateway=use_gateway, gateway=GATEWAY_BEARER
    )
    # Clients that carry the run's credential are built anew for each model.
    fresh = {"async_client", "client_args"}
    assert [
        (model_id, {k: v for k, v in sent.items() if k not in fresh}) for model_id, sent in built
    ] == [(expected_id, {k: v for k, v in expected.items() if k not in fresh})]


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
    # Its retries are the model middleware's (``rt.model_retries``), not the SDK's.
    assert model.max_retries == 0


def test_a_baseten_model_goes_to_baseten_with_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASETEN_API_KEY", "baseten-key")

    model = build_model(ModelSpec(model_id="baseten:moonshot/kimi", kwargs={}), use_gateway=False)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "moonshot/kimi"
    assert model.openai_api_base == "https://inference.baseten.co/v1"


async def test_a_shared_model_calls_the_gateway_as_each_run_acts_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One model serves every run; each request carries its own run's credential, which
    the backend is asked for once and the run keeps."""
    seen: list[str | None] = []

    async def gateway(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"error": {"message": "stop here"}}, status=401)

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "http://backend.invalid")
    asked: list[str] = []

    def run_for(credential: GatewayCredential) -> Run:
        run = Run(RunContext.model_validate({"__run_token": "run-token"}))

        async def post(path: str, body: dict[str, JsonValue], **_: object) -> JsonValue:
            asked.append(path.rsplit("/", 1)[-1])
            return credential.model_dump(mode="json")

        monkeypatch.setattr(run.backend, "post", post)
        return run

    person = run_for(GatewayCredential(token="person-token"))
    system = run_for(GatewayCredential())
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", gateway)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "lsv2-deployment-key")
        model = build_model(_spec("anthropic:claude-haiku-5-5", "medium"), use_gateway=True)
        for run in (person, person, system):
            monkeypatch.setattr(gateway_module, "current_run", lambda run=run: run)
            with pytest.raises(anthropic.AuthenticationError):
                await model.ainvoke([HumanMessage("hi")])
    finally:
        await runner.cleanup()

    assert seen == ["Bearer person-token", "Bearer person-token", "Bearer lsv2-deployment-key"]
    assert asked == [GATEWAY_CREDENTIAL.name, GATEWAY_CREDENTIAL.name]
