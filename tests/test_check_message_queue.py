from __future__ import annotations

from agent.middleware.check_message_queue import (
    _queued_configurable_update,
    _select_queued_messages,
)


def test_queued_configurable_update_keeps_only_allowed_reviewer_reply_fields() -> None:
    update = _queued_configurable_update(
        {
            "configurable": {
                "reviewer_event": "finding_reply",
                "finding_reply_id": "f_1",
                "finding_reply_allow_prompt_learning": True,
                "repo": {"owner": "acme", "name": "repo", "extra": "ignored"},
                "thread_id": "should-not-change",
            }
        }
    )

    assert update == {
        "reviewer_event": "finding_reply",
        "finding_reply_id": "f_1",
        "finding_reply_allow_prompt_learning": True,
        "repo": {"owner": "acme", "name": "repo"},
    }


def test_select_queued_messages_processes_configurable_message_by_itself() -> None:
    plain_message = {"content": "plain"}
    configurable_message = {
        "content": "reply",
        "configurable": {
            "reviewer_event": "finding_reply",
            "finding_reply_allow_prompt_learning": True,
        },
    }

    selected, remaining, update = _select_queued_messages([plain_message, configurable_message])
    assert selected == [plain_message]
    assert remaining == [configurable_message]
    assert update == {}

    selected, remaining, update = _select_queued_messages([configurable_message, plain_message])
    assert selected == [configurable_message]
    assert remaining == [plain_message]
    assert update == {
        "reviewer_event": "finding_reply",
        "finding_reply_allow_prompt_learning": True,
    }
