import importlib
from unittest.mock import AsyncMock

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from fastapi import HTTPException
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from langgraph.graph.state import RunnableConfig

from openswe import chat
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.review import chat as review_chat_api

# `openswe.tools.__init__` rebinds these names to the tool *functions*, shadowing
# the submodules. Import the real modules so we can monkeypatch their globals.
list_review_findings = importlib.import_module("openswe.tools.list_review_findings")
read_repo_file = importlib.import_module("openswe.github.tools.read_repo_file")
search_repo_code = importlib.import_module("openswe.github.tools.search_repo_code")
web_search = importlib.import_module("openswe.tools.web_search")


# --- tools -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_tools_require_context_and_token(monkeypatch) -> None:
    monkeypatch.setattr("openswe.run_config.get_config", lambda: {"configurable": {}})
    result = await read_repo_file.read_repo_file("src/app.py")
    assert result["success"] is False

    config = {"configurable": {"chat_repo_owner": "acme", "chat_repo_name": "repo"}}
    monkeypatch.setattr("openswe.run_config.get_config", lambda: config)
    for tool, args in (
        (read_repo_file.read_repo_file, ("src/app.py",)),
        (search_repo_code.search_repo_code, ("foo",)),
    ):
        result = await tool(*args)
        assert result["error"] == "GitHub credentials unavailable; repository source was not read"


# --- graph factory guard -----------------------------------------------------


def test_chat_excludes_mutating_filesystem_tools() -> None:
    from openswe.chat import _EXCLUDED_TOOLS

    assert {"write_file", "edit_file", "delete", "execute"} <= _EXCLUDED_TOOLS


def test_chat_general_purpose_subagent_is_read_only() -> None:
    from openswe.chat import _chat_general_purpose_subagent

    spec = _chat_general_purpose_subagent()

    assert spec["name"] == "general-purpose"
    fs_middleware = [m for m in spec.get("middleware", []) if isinstance(m, FilesystemMiddleware)]
    assert len(fs_middleware) == 1
    enabled = fs_middleware[0]._enabled_tools
    assert enabled is not None
    assert {"write_file", "edit_file", "delete", "execute"}.isdisjoint(enabled)
    assert {"read_file", "ls", "glob", "grep"} <= enabled


@pytest.mark.asyncio
async def test_review_chat_rejects_thread_not_postable_for_this_pr(monkeypatch) -> None:
    async def no_accessible_threads(*args):
        return []

    monkeypatch.setattr(review_chat_api.pr_fixes, "find_pr_threads", no_accessible_threads)
    with pytest.raises(HTTPException) as error:
        await review_chat_api.proxy_review_chat_commands(
            "acme", "repo", 7, "octocat", "foreign-thread", b"{}"
        )
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    "model_id",
    [
        "anthropic:claude-opus-5-5",
        "openai:gpt-6.1-sol",
        "fireworks:accounts/fireworks/models/kimi-k3",
    ],
)
async def test_cache_ttl_on_main_and_subagent_provider_requests(
    model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setattr(
        chat,
        "cached_workspace_settings",
        AsyncMock(return_value=WorkspaceSettings({"gateway_enabled": False})),
    )
    monkeypatch.setattr(chat, "_resolve_chat_model", AsyncMock(return_value=(model_id, "medium")))
    monkeypatch.setattr(
        chat.PrepareChatRunMiddleware,
        "_prepare",
        AsyncMock(return_value={"rendered_system_prompt": "You are Open SWE."}),
    )
    payloads: list[dict[str, object]] = []

    async def generate(
        model: ChatAnthropic | ChatOpenAI | ChatFireworks,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        if isinstance(model, ChatFireworks):
            message_dicts, params = model._create_message_dicts(messages, stop)
            payloads.append({"messages": message_dicts, **params, **kwargs})
        else:
            payloads.append(model._get_request_payload(messages, stop=stop, **kwargs))
        reply = (
            AIMessage("Done.")
            if len(payloads) > 1
            else AIMessage(
                "",
                tool_calls=[
                    {
                        "id": "delegate-call",
                        "name": "task",
                        "args": {
                            "description": "Find the plan.",
                            "subagent_type": "general-purpose",
                        },
                    }
                ],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])

    for model_type in (ChatAnthropic, ChatOpenAI, ChatFireworks):
        monkeypatch.setattr(model_type, "_agenerate", generate)
    config: RunnableConfig = {
        "configurable": {"thread_id": "cache-ttl", "__is_for_execution__": True}
    }
    graph = await chat.get_chat_agent(config)
    await graph.ainvoke({"messages": [HumanMessage("Find the plan.")]}, config)

    assert len(payloads) == 3
    for payload in payloads:
        if model_id.startswith("anthropic:"):
            cache_control = {"type": "ephemeral", "ttl": "1h"}
            assert payload["cache_control"] == cache_control
            system = payload["system"]
            tools = payload["tools"]
            assert isinstance(system, list)
            assert isinstance(tools, list)
            assert system[-1]["cache_control"] == cache_control
            assert tools[-1]["cache_control"] == cache_control
        else:
            assert "cache_control" not in payload
