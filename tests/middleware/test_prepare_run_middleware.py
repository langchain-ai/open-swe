import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock
from xml.etree import ElementTree

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.middleware import AgentState, wrap_model_call
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.input_messages import human_input, person_introduction
from agent.middleware.conversation_offloading import ConversationOffloadingMiddleware
from agent.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
from agent.middleware.require_user_reply import RequireUserReplyMiddleware
from agent.run_config import RunConfig
from agent.server import PrepareAgentRunMiddleware
from agent.slack.payloads import SlackChannelContext
from agent.source_context import SlackThreadRef
from agent.utils import ttl_cache
from agent.utils.authorship import CollaboratorIdentity, ThreadParticipant


class DummyPrepareMiddleware(BasePrepareRunMiddleware):
    def __init__(self) -> None:
        self.calls = 0

    async def _prepare(self, state, runtime):
        self.calls += 1
        return {"work_dir": "/tmp/work", "rendered_system_prompt": "prepared prompt"}


@pytest.mark.asyncio
async def test_prepare_latch_skips_second_call():
    middleware = DummyPrepareMiddleware()

    update = await middleware.abefore_agent(
        cast(AgentState, {"messages": []}), cast(Runtime[None], MagicMock())
    )
    assert update is not None
    fingerprint = update.pop("run_prepared_for")
    assert isinstance(fingerprint, str)
    assert update == {
        "run_prepared": True,
        "work_dir": "/tmp/work",
        "rendered_system_prompt": "prepared prompt",
    }
    assert (
        await middleware.abefore_agent(
            cast(
                AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": fingerprint}
            ),
            cast(Runtime[None], MagicMock()),
        )
        is None
    )
    assert middleware.calls == 1


@pytest.mark.asyncio
async def test_prepare_latch_reruns_when_fingerprint_changes():
    middleware = DummyPrepareMiddleware()

    assert await middleware.abefore_agent(
        cast(AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": "stale"}),
        cast(Runtime[None], MagicMock()),
    )
    assert middleware.calls == 1


@pytest.mark.asyncio
async def test_prepare_prompt_injection():
    middleware = DummyPrepareMiddleware()
    seen = {}

    async def handler(request: ModelRequest[None]) -> ModelResponse[Any]:
        seen["system_prompt"] = request.system_prompt
        return cast(ModelResponse[Any], MagicMock())

    request = type(
        "Request",
        (),
        {
            "state": {
                "rendered_system_prompt": "prepared prompt",
                "messages": [HumanMessage("hi")],
            },
            "system_message": None,
            "override": lambda self, **kwargs: type(
                "Request",
                (),
                {
                    "state": self.state,
                    "system_prompt": kwargs["system_message"].text,
                    "override": self.override,
                },
            )(),
        },
    )()
    await middleware.awrap_model_call(cast(ModelRequest[None], request), handler)
    assert seen["system_prompt"] == "prepared prompt"


def _sender_message(sender_id: str, text: str = "ship it") -> HumanMessage:
    content = human_input(
        text,
        {"sender_id": sender_id, "surface": "web", "kind": "human"},
    )["content"]
    return HumanMessage(content=cast(str, content))


def _participant(login: str, *, instructions: str = "") -> ThreadParticipant:
    identity = CollaboratorIdentity(
        display_name=login,
        commit_name=login,
        commit_email=f"{login}@users.noreply.github.com",
        github_login=login,
    )
    return ThreadParticipant(
        identity=identity, person_id=f"user:{login}", instructions=instructions
    )


def _participant_block(participant: ThreadParticipant) -> HumanMessage:
    content = person_introduction(participant.as_person())["content"]
    return HumanMessage(content=cast(str, content))


def test_sender_subject_id_is_none_without_a_human_message():
    assert (
        PrepareAgentRunMiddleware._sender_subject_id(cast(PrepareRunState, {"messages": []}), None)
        is None
    )


def test_sender_subject_id_prefers_the_latest_human_sender():
    state = cast(
        PrepareRunState,
        {"messages": [_sender_message("github:ramon"), _sender_message("github:alice")]},
    )

    assert PrepareAgentRunMiddleware._sender_subject_id(state, None) == "github:alice"


def test_sender_subject_id_honors_an_explicit_bot_sender():
    bot_id = "system:slack-bot-B123"
    state = cast(PrepareRunState, {"messages": [_sender_message("github:someone-else")]})

    assert PrepareAgentRunMiddleware._sender_subject_id(state, bot_id) == bot_id


def test_each_participant_is_introduced_by_their_own_context_block():
    alice, bob = _participant("alice"), _participant("bob")

    messages = PrepareAgentRunMiddleware._participants_messages(
        cast(PrepareRunState, {"messages": []}), [bob, alice]
    )

    assert len(messages) == 2
    blocks = [ElementTree.fromstring(cast(str, m["content"])) for m in messages]
    assert [b.attrib["kind"] for b in blocks] == ["person", "person"]
    assert [b.attrib["id"] for b in blocks] == ["user:alice", "user:bob"]
    body = (blocks[0].text or "").strip().splitlines()
    assert "commit_name: alice" in body
    assert "commit_email: alice@users.noreply.github.com" in body


def test_a_visible_participant_is_not_repeated_when_someone_joins():
    alice, bob = _participant("alice"), _participant("bob")
    state = cast(PrepareRunState, {"messages": [_participant_block(alice)]})

    messages = PrepareAgentRunMiddleware._participants_messages(state, [alice, bob])

    assert len(messages) == 1
    assert ElementTree.fromstring(cast(str, messages[0]["content"])).attrib["id"] == "user:bob"


def test_only_the_changed_participant_is_resent():
    alice, bob = _participant("alice"), _participant("bob")
    state = cast(
        PrepareRunState, {"messages": [_participant_block(alice), _participant_block(bob)]}
    )
    bob_now = _participant("bob", instructions="Never use ripgrep.")

    messages = PrepareAgentRunMiddleware._participants_messages(state, [alice, bob_now])

    assert len(messages) == 1
    block = ElementTree.fromstring(cast(str, messages[0]["content"]))
    assert block.attrib["id"] == "user:bob"
    assert "standing_instructions: Never use ripgrep." in (block.text or "")


def test_participant_blocks_are_restored_after_compaction():
    alice = _participant("alice")
    state = cast(
        PrepareRunState,
        {
            "messages": [_participant_block(alice), _sender_message("user:alice")],
            "_summarization_event": {"cutoff_index": 1},
        },
    )

    assert len(PrepareAgentRunMiddleware._participants_messages(state, [alice])) == 1


@pytest.mark.asyncio
async def test_ttl_cache_single_flight_and_stale_while_error():
    ttl_cache.clear()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return calls

    results = await asyncio.gather(*(ttl_cache.cached("k", 60, loader) for _ in range(10)))
    assert results == [1] * 10
    assert calls == 1

    ttl_cache.set_cached("k", "stale", -1)

    async def failing_loader():
        raise RuntimeError("boom")

    assert await ttl_cache.cached("k", 60, failing_loader) == "stale"


@pytest.mark.asyncio
async def test_ttl_cache_stale_while_revalidate_refreshes_in_background():
    ttl_cache.clear()
    ttl_cache.set_cached("k", "stale", -1)
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        refresh_started.set()
        await allow_refresh.wait()
        return "fresh"

    assert await ttl_cache.cached_stale_while_revalidate("k", 60, loader) == "stale"
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert calls == 1

    allow_refresh.set()
    for _ in range(20):
        if await ttl_cache.cached_stale_while_revalidate("k", 60, loader) == "fresh":
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("stale cache entry was not refreshed")


@pytest.mark.asyncio
async def test_ttl_cache_exception_without_stale_is_not_cached():
    ttl_cache.clear()
    calls = 0

    async def failing_loader():
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await ttl_cache.cached("k", 60, failing_loader)
    with pytest.raises(RuntimeError):
        await ttl_cache.cached("k", 60, failing_loader)
    assert calls == 2


def test_recent_context_audience_fails_closed_for_shared_destinations() -> None:
    middleware = object.__new__(PrepareAgentRunMiddleware)
    middleware._profile_login = "alice"
    middleware._credential_login = "alice"
    middleware._recent_thread_context_enabled = True
    middleware._source = "github"

    assert middleware._recent_context_audience(RunConfig()) is None

    middleware._source = "dashboard"
    middleware._credential_login = None
    assert middleware._recent_context_audience(RunConfig()) is None


def test_recent_context_audience_distinguishes_dm_and_shared_slack() -> None:
    middleware = object.__new__(PrepareAgentRunMiddleware)
    middleware._profile_login = "alice"
    middleware._credential_login = "alice"
    middleware._recent_thread_context_enabled = False
    middleware._source = "slack"

    assert middleware._recent_context_audience(RunConfig()) is None
    middleware._recent_thread_context_enabled = True

    dm = RunConfig(slack_thread=SlackThreadRef(channel_context=SlackChannelContext(is_im=True)))
    assert middleware._recent_context_audience(dm) == "private"

    shared = RunConfig(
        slack_thread=SlackThreadRef(
            channel_id="C1",
            team_id="T1",
            channel_context=SlackChannelContext(is_im=False, is_mpim=False),
        )
    )
    assert middleware._recent_context_audience(shared) == "shared_slack"

    group_dm = RunConfig(
        slack_thread=SlackThreadRef(
            channel_id="G1",
            team_id="T1",
            channel_context=SlackChannelContext(is_im=False, is_mpim=True),
        )
    )
    assert middleware._recent_context_audience(group_dm) is None
    assert middleware._recent_context_audience(RunConfig(background_task_completion=True)) is None


@pytest.mark.asyncio
async def test_fork_preserves_prepared_context() -> None:
    middleware = DummyPrepareMiddleware()
    state = cast(
        AgentState,
        {
            "messages": [HumanMessage("new delegated task")],
            "_deepagents_forked_context": True,
            "run_prepared": True,
            "run_prepared_for": "parent fingerprint",
            "rendered_system_prompt": "parent prompt",
        },
    )
    assert await middleware.abefore_agent(state, cast(Runtime[None], MagicMock())) is None
    assert middleware.calls == 0


@pytest.mark.asyncio
async def test_parallel_forks_keep_prepared_context_without_overwriting_parent() -> None:
    middleware = DummyPrepareMiddleware()
    fork_prompts: list[str] = []
    fingerprints: list[str] = []

    @wrap_model_call
    async def scripted_model(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | ExtendedModelResponse:
        if request.state.get("_deepagents_forked_context"):
            assert request.state.get("run_prepared") is True
            assert request.state.get("work_dir") == "/tmp/work"
            fingerprint = request.state.get("run_prepared_for")
            assert isinstance(fingerprint, str)
            fingerprints.append(fingerprint)
            assert request.system_message is not None
            fork_prompts.append(request.system_message.text)
            task = request.messages[-1].text
            return ExtendedModelResponse(
                model_response=ModelResponse(result=[AIMessage(content=task)]),
                command=Command(
                    update={
                        "run_prepared_for": task,
                        "work_dir": f"/tmp/{task}",
                        "rendered_system_prompt": task,
                        "reply_surface": "web",
                        "reply_nudges": 2,
                        "reply_nudge_pending": True,
                        "conversation_offloading": {"status": task},
                    }
                ),
            )
        if isinstance(request.messages[-1], HumanMessage):
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "task",
                                "args": {"description": task, "subagent_type": "worker"},
                                "id": task,
                            }
                            for task in ("first", "second")
                        ],
                    )
                ]
            )
        return ModelResponse(result=[AIMessage(content="done")])

    model = FakeListChatModel(responses=["unused"])
    backend = StateBackend()
    graph = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[
            middleware,
            ConversationOffloadingMiddleware(model, backend),
            RequireUserReplyMiddleware("reply", "no_reply", initial_surface="web"),
            scripted_model,
        ],
        subagents=[{"name": "worker", "description": "worker", "mode": "fork", "model": model}],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "parallel-forks"}}
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage("delegate")],
            "conversation_offloading": {"status": "parent"},
        },
        config,
    )

    assert middleware.calls == 1
    assert len(fork_prompts) == 2
    assert all("prepared prompt" in prompt for prompt in fork_prompts)
    assert {
        message.tool_call_id for message in result["messages"] if isinstance(message, ToolMessage)
    } == {"first", "second"}
    state = (await graph.aget_state(config)).values
    assert state["run_prepared"] is True
    assert fingerprints == [state["run_prepared_for"]] * 2
    assert state["work_dir"] == "/tmp/work"
    assert state["rendered_system_prompt"] == "prepared prompt"
    assert state["reply_surface"] == "web"
    assert state["reply_nudges"] == 0
    assert state["reply_nudge_pending"] is False
    assert state["conversation_offloading"] == {"status": "parent"}
