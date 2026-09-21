import asyncio
from typing import Any, cast
from unittest.mock import MagicMock
from xml.etree import ElementTree

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent.input_messages import human_input, participant_introduction
from agent.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
from agent.prompt import participant_context
from agent.server import PrepareAgentRunMiddleware
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
    content = participant_introduction(participant_context(participant))["content"]
    return HumanMessage(content=cast(str, content))


def test_sender_pointer_is_one_system_message_every_turn():
    messages = PrepareAgentRunMiddleware._sender_context_messages("Sent by **Ramon** (`user:1`).")

    assert len(messages) == 1
    envelope = ElementTree.fromstring(cast(str, messages[0]["content"]))
    assert envelope.attrib["sender"] == "system:sender-context"
    assert envelope.attrib["kind"] == "system"
    assert envelope.findtext("content") == "Sent by **Ramon** (`user:1`)."


def test_sender_pointer_escapes_untrusted_text():
    messages = PrepareAgentRunMiddleware._sender_context_messages("Sent by **O'Connor <x>** & co.")

    envelope = ElementTree.fromstring(cast(str, messages[0]["content"]))
    assert envelope.findtext("content") == "Sent by **O'Connor <x>** & co."


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
    assert [b.attrib["kind"] for b in blocks] == ["participant", "participant"]
    assert [b.attrib["id"] for b in blocks] == ["user:alice", "user:bob"]
    assert blocks[0].findtext("git_identity") == (
        "git config user.name alice && git config user.email alice@users.noreply.github.com"
    )


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
    assert block.findtext("standing_instructions") == "Never use ripgrep."


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
