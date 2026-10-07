from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from openswe.slack import channel_memory


@pytest.mark.parametrize(
    "approver,revision,message_text,member,expected",
    [
        ("author", 0, "Exact proposal", True, False),
        ("peer", 0, "Exact proposal", True, True),
        ("peer", 1, "Exact proposal", True, False),
        ("peer", 0, "Edited proposal", True, False),
        ("peer", 0, "Exact proposal", False, False),
    ],
)
async def test_memory_requires_other_member_and_current_revision(
    monkeypatch, approver, revision, message_text, member, expected
):
    records = {
        "slack_channel_memory_proposals": {
            "memory": "New memory",
            "patch": "-Old memory\n+New memory",
            "proposer": "author",
            "base_revision": 0,
            "message_text": "Exact proposal",
            "status": "pending",
        },
        "slack_channel_memory": {"memory": "Old memory", "revision": revision},
    }

    async def get_value(namespace, key):
        return records.get(namespace[0])

    async def put_value(namespace, key, value):
        records[namespace[0]] = value

    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield

    async def patch_memory(channel, memory, base_revision, **audit):
        assert audit == {
            "patch": "-Old memory\n+New memory",
            "proposed_by": "author",
            "approved_by": approver,
            "proposal_ts": "1.0",
        }
        row = records["slack_channel_memory"]
        if row["revision"] != base_revision:
            return False
        row.update(memory=memory, revision=base_revision + 1)
        return True

    monkeypatch.setattr(channel_memory.SlackChannel, "patch_memory", patch_memory)
    monkeypatch.setattr(channel_memory, "get_value", get_value)
    monkeypatch.setattr(channel_memory, "put_value", put_value)
    monkeypatch.setattr(channel_memory, "slack_thread_mutation_lock", lock)
    monkeypatch.setattr(channel_memory, "langgraph_client", lambda: None)
    monkeypatch.setattr(channel_memory, "channel_member", AsyncMock(return_value=member))
    monkeypatch.setattr(
        channel_memory,
        "fetch_slack_message_by_ts",
        AsyncMock(return_value={"text": message_text}),
    )
    monkeypatch.setattr(channel_memory, "post_slack_thread_reply_with_ts", AsyncMock())
    event = {
        "reaction": "+1",
        "user": approver,
        "item": {"type": "message", "channel": "C123", "ts": "1.0"},
    }
    assert await channel_memory.approve_channel_memory(event)
    assert (records["slack_channel_memory"]["memory"] == "New memory") is expected
