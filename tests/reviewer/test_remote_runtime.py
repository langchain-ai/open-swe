import time

import jwt
import pytest
from starlette.types import Message, Receive, Scope, Send

from openswe.remote_runtime.server import RunTokenMiddleware
from openswe.remote_runtime.tokens import (
    RemoteRun,
    RuntimeTokenError,
    sign_runtime_token,
    verify_runtime_token,
)

RUN = RemoteRun(
    thread_id="thread-1",
    assistant_id="reviewer",
    configurable={"repo": {"owner": "acme", "name": "widgets"}, "pr_number": 7},
)


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )


@pytest.mark.parametrize("requested", ["anthropic:claude-opus-5-5", "openai:gpt-6.1-sol"])
def test_remote_model_switch_overrides_cached_route_and_effort(requested: str) -> None:
    from openswe.remote_runtime.graph import RunSession
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface
    from openswe.utils.model import provider_model_kwargs

    main = ModelChoice("openai:gpt-6.1-sol", {})
    session = RunSession(
        ToolSurface(models=SurfaceModels(main=main, subagent=main, use_gateway=False)),
        {},
        None,
        started_here=True,
    )
    choice = session.model_for(
        {
            "model_route": "default",
            "requested_model": requested,
            "requested_effort": "medium",
        }
    )

    assert choice.model_id == requested
    assert choice.kwargs == provider_model_kwargs(requested, "medium", max_tokens=64_000)


async def test_app_lifespan_exits_without_managed_deep_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.api.app import app, lifespan
    from openswe.remote_runtime.client import RemoteRuntimeConfigurationError

    monkeypatch.delenv("MDA_RUNTIME_URL", raising=False)

    with pytest.raises(RemoteRuntimeConfigurationError, match="MDA_RUNTIME_URL is required"):
        async with lifespan(app):
            pass


def test_run_token_round_trips_the_signed_run() -> None:
    assert verify_runtime_token(sign_runtime_token(RUN)) == RUN


def test_run_token_signed_with_a_rotated_out_key_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "previous-signing-key-0123456789abcdef")
    token = sign_runtime_token(RUN)
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )

    assert verify_runtime_token(token).thread_id == "thread-1"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            jwt.encode(
                {"sub": "thread-1", "cfg": {}},
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="missing-claims",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": 0,
                    "exp": 1,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="expired",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 60,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "someone-elses-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="foreign-key",
        ),
    ],
)
def test_run_token_refuses_tokens_this_backend_did_not_issue(token: str) -> None:
    with pytest.raises(RuntimeTokenError):
        verify_runtime_token(token)


async def _status(authorization: str | None) -> tuple[int, RemoteRun | None]:
    seen: list[RemoteRun] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["state"]["remote_run"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = [(b"authorization", authorization.encode())] if authorization else []
    scope: Scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await RunTokenMiddleware(inner)(scope, receive, send)
    return sent[0]["status"], (seen[0] if seen else None)


async def test_tool_server_refuses_calls_without_a_valid_run_token() -> None:
    assert await _status(None) == (401, None)
    assert await _status("Bearer not-a-token") == (401, None)


async def test_tool_server_hands_the_verified_run_to_its_handlers() -> None:
    assert await _status(f"Bearer {sign_runtime_token(RUN)}") == (200, RUN)


async def test_mda_catalog_leaves_integrations_and_code_mode_to_mda() -> None:
    """Seldom-needed tools go to MDA's code mode, not the model's schemas; the graph's own stays here."""
    from langchain.agents import create_agent
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.tools import StructuredTool
    from langchain_quickjs import CodeInterpreterMiddleware

    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.remote_runtime.graph import RemoteGraph
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface

    def integration(name: str) -> StructuredTool:
        async def run(query: str) -> str:
            return query

        return StructuredTool.from_function(coroutine=run, name=name, description=name)

    async def build(config: object, surface: ToolSurface) -> None:
        surface.dynamic = DynamicToolMiddleware(
            {"MCP": [integration("mcp_search"), integration("mcp.dotted")]}
        )
        core = [integration(name) for name in ("read_file", "manage_thread", "slack_reply")]
        middleware: list[AgentMiddleware] = [
            surface.dynamic,
            CodeInterpreterMiddleware(ptc=[integration("mcp_search")]),
        ]
        surface.graph = create_agent(
            FakeListChatModel(responses=[]), tools=core, middleware=middleware
        )
        surface.code_mode = True
        surface.middleware = middleware
        choice = ModelChoice("openai:gpt-6.1-sol", {})
        surface.models = SurfaceModels(main=choice, subagent=choice, use_gateway=False)

    async def start_models(cfg: object) -> object:
        raise AssertionError("not asked for")

    graph = RemoteGraph("agent", build, start_models)
    catalog = await graph.hook(RUN, "catalog", {"run_id": "run-1"})

    assert isinstance(catalog, dict)
    tools = catalog["tools"]
    assert isinstance(tools, list)
    names = {tool["name"] for tool in tools if isinstance(tool, dict)}
    assert {"mcp_search", "mcp.dotted", "read_file", "manage_thread", "slack_reply"} <= names
    assert "eval" not in names
    assert catalog["code_only"] == ["manage_thread", "mcp_search"]
    assert catalog["also_from_code"] == ["read_file"]
