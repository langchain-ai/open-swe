import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
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

from openswe.input_messages import human_input, person_introduction
from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware
from openswe.middleware.model_selection import ModelSelectionMiddleware
from openswe.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
from openswe.middleware.require_user_reply import RequireUserReplyMiddleware
from openswe.run_config import RunConfig
from openswe.server import (
    PrepareAgentRunMiddleware,
    _DisableInheritedMiddleware,
    _thread_participants,
)
from openswe.utils import ttl_cache
from openswe.utils.authorship import CollaboratorIdentity, ThreadParticipant


class DummyPrepareMiddleware(BasePrepareRunMiddleware):
    def __init__(self) -> None:
        self.calls = 0

    async def _prepare(self, state, runtime):
        self.calls += 1
        return {"work_dir": "/tmp/work", "rendered_system_prompt": "prepared prompt"}


@pytest.mark.asyncio
async def test_prepare_latch_reruns_when_fingerprint_changes():
    middleware = DummyPrepareMiddleware()

    assert await middleware.abefore_agent(
        cast(AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": "stale"}),
        cast(Runtime[None], MagicMock()),
    )
    assert middleware.calls == 1


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


@pytest.mark.asyncio
@pytest.mark.parametrize("sender_login", ["alice", "Alice"])
async def test_a_participant_block_does_not_depend_on_who_sent_the_turn(
    sender_login: str,
) -> None:
    alice, bob = _participant("alice"), _participant("bob")
    users = {
        login: SimpleNamespace(
            id=login,
            github_login=login,
            display_name=login,
            email="",
            slack_user_id=f"U-{login}",
        )
        for login in ("alice", "bob")
    }

    async def participant_user(login: str, person_id: str | None) -> SimpleNamespace:
        return users[(person_id or login).removeprefix("user:").lower()]

    async def participants_for(
        sender: CollaboratorIdentity, person_id: str
    ) -> list[ThreadParticipant]:
        return await _thread_participants(
            "thread-id",
            {},
            sender,
            sender_person_id=person_id,
            sender_timezone="America/New_York",
            sender_slack_user_id=f"U-{sender.github_login.lower()}",
        )

    with (
        patch(
            "openswe.server._thread_participant_identities",
            AsyncMock(return_value=[alice.identity, bob.identity]),
        ),
        patch("openswe.server._participant_user", participant_user),
        patch("openswe.server.get_slack_user_timezone", AsyncMock(return_value="America/New_York")),
        patch("openswe.server.load_profile", AsyncMock(return_value={})),
        patch("openswe.server.participant_is_admin", AsyncMock(return_value=False)),
        patch("openswe.server._resolve_user_custom_instructions", AsyncMock(return_value=None)),
    ):
        alice_sends = await participants_for(
            replace(
                alice.identity,
                github_login=sender_login,
                commit_email=f"123+{sender_login}@users.noreply.github.com",
            ),
            alice.person_id,
        )
        bob_sends = await participants_for(bob.identity, bob.person_id)

    assert [p.person_id for p in alice_sends] == ["user:alice", "user:bob"]
    alice_as_sender = next(p for p in alice_sends if p.person_id == "user:alice")
    alice_as_other = next(p for p in bob_sends if p.person_id == "user:alice")
    assert person_introduction(alice_as_other.as_person()) == person_introduction(
        alice_as_sender.as_person()
    )


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
    middleware._source = "dashboard"

    assert middleware._recent_context_audience(RunConfig()) == "private"
    assert middleware._recent_context_audience(RunConfig(background_task_completion=True)) is None

    middleware._source = "github"
    assert middleware._recent_context_audience(RunConfig()) is None

    middleware._source = "dashboard"
    middleware._credential_login = None
    assert middleware._recent_context_audience(RunConfig()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_model", [None, "openai:gpt-5.4"])
async def test_parallel_forks_keep_prepared_context_without_overwriting_parent(
    requested_model: str | None,
) -> None:
    middleware = DummyPrepareMiddleware()
    fork_prompts: list[str] = []
    fingerprints: list[str] = []

    @wrap_model_call
    async def scripted_model(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | ExtendedModelResponse:
        if request.state.get("_deepagents_forked_context"):
            assert request.state.get("run_prepared") is True
            assert request.state["requested_model"] == requested_model
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
            ModelSelectionMiddleware({}, model, routing_mode=None),
            ConversationOffloadingMiddleware(model, backend),
            RequireUserReplyMiddleware("reply", "no_reply", initial_surface="web"),
            scripted_model,
        ],
        subagents=[
            {
                "name": "worker",
                "description": "worker",
                "mode": "fork",
                "model": model,
                "middleware": [_DisableInheritedMiddleware(ModelSelectionMiddleware.__name__)],
            }
        ],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "parallel-forks"}}
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage("delegate")],
            "requested_model": requested_model,
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
    assert state["requested_model"] == requested_model
    assert "requested_model" not in result
    assert fingerprints == [state["run_prepared_for"]] * 2
    assert state["work_dir"] == "/tmp/work"
    assert state["rendered_system_prompt"] == "prepared prompt"
    assert state["reply_surface"] == "web"
    assert state["reply_nudges"] == 0
    assert state["conversation_offloading"] == {"status": "parent"}
