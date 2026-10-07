"""A skill's integration tools reach the model once the skill is read."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from deepagents import create_deep_agent
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from mcp.types import CallToolResult, TextContent, Tool

from agent.mcp import MCPConnection, MCPConnectionUpdate, runtime
from agent.mcp.user import save_user_mcp
from agent.mcp.workspace import save_workspace_mcp
from agent.skill_store.store import ORGANIZATION_SKILLS_NAMESPACE
from agent.utils.model import make_model
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG
from tests.agent.test_agent_assembly_context import _base_config, _capture_create_deep_agent_kwargs
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope
from tests.agent.test_agent_integration_tools import _NOTION_SEARCH, _openai_offer

_OWNER = "octocat"
_LIST_ISSUES = "mcp_linear_list_issues_bc298ed4fd"


async def _notion_tools_for(login: str | None) -> list[BaseTool]:
    """Notion tools belong to the thread owner's account, so only a private thread has them."""
    return [_NOTION_SEARCH] if login else []


@dataclass
class _Servers:
    """MCP servers by URL: the tools each lists, and every call each receives."""

    tools: dict[str, list[str]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def connect(
        self,
        name: str,
        tools: Sequence[str],
        *,
        allowed: Sequence[str] | None = None,
        tier: Literal["workspace", "user"] = "workspace",
    ) -> str:
        url = f"https://mcp.example/{tier}/{name}"
        self.tools[url] = list(tools)
        update = MCPConnectionUpdate(
            name=name, url=url, allowed_tools=list(tools if allowed is None else allowed)
        )
        if tier == "user":
            await save_user_mcp(_OWNER, name, update)
        else:
            await save_workspace_mcp(DEFAULT_WORKSPACE_SLUG, name, update)
        return url

    async def discover(self, record: MCPConnection, namespace: tuple[str, ...]) -> list[Tool]:
        del namespace
        return [
            Tool(name=name, description=name, inputSchema={"type": "object"})
            for name in self.tools[record.url]
        ]


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("LLM_FALLBACK_MODEL_ID", raising=False)


@pytest.fixture
def servers(fake_store: object, monkeypatch: pytest.MonkeyPatch) -> _Servers:
    del fake_store
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    servers = _Servers()

    class Session:
        def __init__(self, url: str) -> None:
            self.url = url

        async def initialize(self) -> None:
            pass

        async def call_tool(self, name: str, arguments: object, **kwargs: object) -> CallToolResult:
            del arguments, kwargs
            servers.calls.append((self.url, name))
            return CallToolResult(content=[TextContent(type="text", text=f"{name} ran")])

    @asynccontextmanager
    async def session(connection: Mapping[str, object], **kwargs: object) -> AsyncIterator[Session]:
        del kwargs
        url = connection["url"]
        assert isinstance(url, str)
        yield Session(url)

    monkeypatch.setattr(runtime, "_discover_tools", servers.discover)
    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    return servers


def _read(skill: str) -> ToolCall:
    return ToolCall(
        name="read_file", args={"file_path": f"/organization-skills/{skill}/SKILL.md"}, id=None
    )


def _call(name: str) -> ToolCall:
    return ToolCall(name=name, args={}, id=None)


@dataclass
class _Script:
    """Answers each provider call with the next turn and records the request it would have sent.

    A turn is either the final text, or the tool calls of one reply.
    """

    turns: list[str | list[ToolCall]]
    sent: list[Mapping[str, object]] = field(default_factory=list)

    def answer(
        self,
        model: ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None,
        **kwargs: object,
    ) -> ChatResult:
        self.sent.append(model._get_request_payload(messages, stop=stop, **kwargs))
        index = len(self.sent) - 1
        turn = self.turns[index]
        reply = (
            AIMessage(turn)
            if isinstance(turn, str)
            else AIMessage(
                "",
                tool_calls=[
                    {**call, "id": f"call-{index}-{position}"} for position, call in enumerate(turn)
                ],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _skill(name: str, include_tools: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Use {include_tools}.\n"
        "metadata:\n"
        f"  include_tools: {include_tools}\n"
        "---\n\n"
        "Follow the instructions.\n"
    )


async def _run(script: _Script, *, skills: Mapping[str, str]) -> list[AnyMessage]:
    """Run the agent the factory builds; ``skills`` maps each organization skill to its include_tools."""
    config = _base_config()
    with patch("agent.server._notion_tools_for", _notion_tools_for):
        kwargs = await _capture_create_deep_agent_kwargs(
            config, thread_settings={"owner_login": _OWNER}, make_model=make_model
        )
    kwargs.pop("make_model_calls")
    store = InMemoryStore()
    for name, include_tools in skills.items():
        store.put(
            (ORGANIZATION_SKILLS_NAMESPACE,),
            f"/{name}/SKILL.md",
            {"content": _skill(name, include_tools), "encoding": "utf-8"},
        )
    kwargs["checkpointer"] = InMemorySaver()
    kwargs["store"] = store
    agent = create_deep_agent(**kwargs)
    prepared = {"work_dir": "/workspace", "rendered_system_prompt": "You are Open SWE."}

    async def generate(
        model: ChatOpenAI,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del run_manager
        return script.answer(model, messages, stop, **kwargs)

    with (
        patch("agent.server.PrepareAgentRunMiddleware._prepare", AsyncMock(return_value=prepared)),
        patch.object(ChatOpenAI, "_agenerate", generate),
    ):
        result = await agent.ainvoke({"messages": [HumanMessage("File the ticket.")]}, config)
    return result["messages"]


def _integration_offer(payload: Mapping[str, object]) -> set[str]:
    """The integration tools a request offers, in ``tools`` or added mid-conversation."""
    in_tools, added = _openai_offer(payload)
    return {name for name in [*in_tools, *added] if name.startswith(("mcp_", "notion-"))}


def _tool_results(messages: Sequence[AnyMessage], tool_name: str) -> list[str]:
    """The text of every result answering a call to ``tool_name``, refusals included."""
    call_ids = {
        call["id"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == tool_name
    }
    return [
        message.text
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id in call_ids
    ]


async def test_reading_a_skill_that_names_a_connection_offers_its_allowed_tools(
    servers: _Servers,
) -> None:
    await servers.connect(
        "linear",
        ["list_issues", "create_issue", "delete_issue"],
        allowed=["list_issues", "create_issue"],
    )
    await servers.connect("github", ["search"])
    script = _Script([[_read("file-tickets")], "Done."])

    await _run(script, skills={"file-tickets": "mcp_linear"})

    assert [_integration_offer(payload) for payload in script.sent] == [
        set(),
        {_LIST_ISSUES, "mcp_linear_create_issue_fd6622409c"},
    ]


@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_a_disclosed_tool_runs_without_being_loaded(
    servers: _Servers, saved_thread_scope: dict[str, str], visibility: str
) -> None:
    saved_thread_scope["visibility"] = visibility
    url = await servers.connect("linear", ["list_issues"])
    script = _Script([[_read("file-tickets")], [_call(_LIST_ISSUES)], "Done."])

    messages = await _run(script, skills={"file-tickets": "mcp_linear:list_issues"})

    assert _integration_offer(script.sent[1]) == {_LIST_ISSUES}
    assert _tool_results(messages, _LIST_ISSUES) == ["list_issues ran"]
    assert servers.calls == [(url, "list_issues")]


@pytest.mark.parametrize(
    ("include_tools", "offered"),
    [
        ("mcp_server_a:create_foo", {"mcp_server_a_create_foo_5c0291cede"}),
        ("mcp_server_b:create_foo", {"mcp_server_b_create_foo_bf8aec4031"}),
        ("mcp_server:a_create_foo", {"mcp_server_a_create_foo_fd899b21e8"}),
        ("mcp_my-crm:get.customer", {"mcp_my-crm_get_customer_ac88672888"}),
        (
            "mcp_langsmith-prod:list_prompts_with_extended_metadata_filters",
            {"mcp_langsmith-prod_list_prompts_with_extended_metadat_012f8d599f"},
        ),
        ("server_a", set()),
        ("create_foo", set()),
        ("unknown", set()),
    ],
)
async def test_a_skill_offers_exactly_the_mcp_tools_it_names(
    servers: _Servers, include_tools: str, offered: set[str]
) -> None:
    await servers.connect("server_a", ["create_foo"])
    await servers.connect("server_b", ["create_foo"])
    await servers.connect("server", ["a_create_foo"])
    await servers.connect("my-crm", ["get.customer"])
    await servers.connect("langsmith-prod", ["list_prompts_with_extended_metadata_filters"])
    script = _Script([[_read("skill")], "Done."])

    await _run(script, skills={"skill": include_tools})

    assert _integration_offer(script.sent[1]) == offered


@pytest.mark.parametrize(
    ("visibility", "offered"), [("private", {"notion-search"}), ("public", set())]
)
async def test_a_skill_names_a_notion_tool_by_its_exact_name(
    servers: _Servers, saved_thread_scope: dict[str, str], visibility: str, offered: set[str]
) -> None:
    saved_thread_scope["visibility"] = visibility
    # Gives the public run integration tools, and so skill tool resolution, too.
    await servers.connect("linear", ["list_issues"])
    script = _Script([[_read("search-notion")], "Done."])

    messages = await _run(script, skills={"search-notion": "notion-search"})

    assert _integration_offer(script.sent[1]) == offered
    assert messages[-1].text == "Done."


async def test_a_users_connection_replaces_the_workspaces_of_the_same_name(
    servers: _Servers,
) -> None:
    await servers.connect("linear", ["list_issues"])
    await servers.connect("linear", ["create_issue"], tier="user")
    script = _Script([[_read("file-tickets")], "Done."])

    await _run(script, skills={"file-tickets": "mcp_linear"})

    assert _integration_offer(script.sent[1]) == {"mcp_linear_create_issue_fd6622409c"}


@pytest.mark.parametrize(
    "first_turn",
    [[_call(_LIST_ISSUES)], [_read("file-tickets"), _call(_LIST_ISSUES)]],
    ids=["without-a-read", "in-the-same-turn-as-the-read"],
)
async def test_a_call_to_an_integration_tool_the_model_was_not_shown_is_refused(
    servers: _Servers, first_turn: list[ToolCall]
) -> None:
    await servers.connect("linear", ["list_issues"])
    script = _Script([first_turn, "Done."])

    messages = await _run(script, skills={"file-tickets": "mcp_linear"})

    assert _tool_results(messages, _LIST_ISSUES) == [
        f"Load {_LIST_ISSUES} with load_integration_tools before calling it."
    ]
    assert servers.calls == []


async def test_a_loaded_tool_that_a_read_skill_names_still_runs(servers: _Servers) -> None:
    url = await servers.connect("linear", ["list_issues"])
    load = ToolCall(name="load_integration_tools", args={"tool_names": [_LIST_ISSUES]}, id=None)
    script = _Script([[load], [_read("file-tickets")], [_call(_LIST_ISSUES)], "Done."])

    messages = await _run(script, skills={"file-tickets": "mcp_linear"})

    assert [_integration_offer(payload) for payload in script.sent] == [
        set(),
        {_LIST_ISSUES},
        {_LIST_ISSUES},
        {_LIST_ISSUES},
    ]
    assert _tool_results(messages, _LIST_ISSUES) == ["list_issues ran"]
    assert servers.calls == [(url, "list_issues")]


@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_the_general_purpose_subagent_runs_a_tool_its_read_skill_names(
    servers: _Servers, saved_thread_scope: dict[str, str], visibility: str
) -> None:
    saved_thread_scope["visibility"] = visibility
    url = await servers.connect("linear", ["list_issues"])
    task = ToolCall(
        name="task",
        args={"description": "File the ticket.", "subagent_type": "general-purpose"},
        id=None,
    )
    script = _Script(
        [
            [task],
            # The subagent's turns.
            [_read("file-tickets")],
            [_call(_LIST_ISSUES)],
            "Filed.",
            # The parent's.
            "Done.",
        ]
    )

    await _run(script, skills={"file-tickets": "mcp_linear"})

    assert [_integration_offer(payload) for payload in script.sent] == [
        set(),
        set(),
        {_LIST_ISSUES},
        {_LIST_ISSUES},
        set(),
    ]
    assert servers.calls == [(url, "list_issues")]
