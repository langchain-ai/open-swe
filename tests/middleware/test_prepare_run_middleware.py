import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from openswe.input_messages import human_input, person_introduction
from openswe.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
from openswe.run_config import RunConfig
from openswe.server import PrepareAgentRunMiddleware, _thread_participants
from openswe.slack.client import get_slack_user_timezone, remember_slack_user_timezone
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
async def test_sender_is_matched_by_login_and_others_get_their_slack_timezone(
    sender_login: str,
) -> None:
    alice, bob = _participant("alice"), _participant("bob")
    sender = replace(
        alice.identity,
        github_login=sender_login,
        commit_email=f"123+{sender_login}@users.noreply.github.com",
    )
    bob_user = SimpleNamespace(
        id="bob", github_login="bob", display_name="bob", email="", slack_user_id="U-bob"
    )
    with patch.multiple(
        "openswe.server",
        _thread_participant_identities=AsyncMock(return_value=[alice.identity, bob.identity]),
        _participant_user=AsyncMock(
            side_effect=lambda login, _: bob_user if login == "bob" else None
        ),
        get_slack_user_timezone=AsyncMock(return_value="Europe/Paris"),
        load_profile=AsyncMock(return_value={}),
        participant_is_admin=AsyncMock(return_value=False),
        _resolve_user_custom_instructions=AsyncMock(return_value=None),
    ):
        participants = await _thread_participants(
            "thread-id",
            {},
            sender,
            sender_person_id=alice.person_id,
            sender_timezone="America/New_York",
        )

    assert [(p.identity.github_login, p.timezone) for p in participants] == [
        (sender_login, "America/New_York"),
        ("bob", "Europe/Paris"),
    ]


@pytest.mark.asyncio
async def test_slack_timezone_skips_failed_lookups_and_keeps_a_reported_one():
    ttl_cache.clear()
    with patch(
        "openswe.slack.client.get_slack_user_info",
        AsyncMock(side_effect=[None, {"tz": "Europe/Paris"}]),
    ):
        assert await get_slack_user_timezone("U1") == ""
        assert await get_slack_user_timezone("U1") == "Europe/Paris"

    remember_slack_user_timezone("U1", "America/New_York")
    assert await get_slack_user_timezone("U1") == "America/New_York"


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


@pytest.mark.parametrize(
    "cut",
    # In process, and as the runtime forwards it.
    [{"_summarization_event": {"cutoff_index": 1}}, {"visible_from": 1}],
)
def test_participant_blocks_are_restored_after_compaction(cut: dict[str, object]):
    alice = _participant("alice")
    state = cast(
        PrepareRunState,
        {"messages": [_participant_block(alice), _sender_message("user:alice")], **cut},
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
