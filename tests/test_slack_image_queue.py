from __future__ import annotations

import asyncio
from typing import Any

from agent import webapp


def test_process_slack_mention_queues_current_message_image_urls(monkeypatch) -> None:
    current_image = "https://files.slack.com/new-screenshot.png"
    current_ts = "1700000099.NEWMSG"
    captured: dict[str, object] = {}

    thread_messages: list[dict[str, object]] = [
        {
            "ts": current_ts,
            "text": f"<@UBOT> here is the bug\n![screenshot]({current_image})",
            "user": "U_USER",
        }
    ]

    async def fake_fetch_slack_thread_messages(
        channel_id: str, thread_ts: str
    ) -> list[dict[str, object]]:
        return thread_messages

    async def fake_is_thread_active(thread_id: str) -> bool:
        return True

    async def fake_get_slack_repo_config(
        message: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_upsert_metadata(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_add_reaction(*args: Any, **kwargs: Any) -> bool:
        return True

    async def fake_get_slack_user_info(user_id: str) -> dict[str, object] | None:
        return None

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        return {}

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        captured["thread_id"] = thread_id
        captured["message_content"] = message_content
        return True

    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", fake_upsert_metadata)
    monkeypatch.setattr(webapp, "add_slack_reaction", fake_add_reaction)
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        webapp.process_slack_mention(
            {
                "channel_id": "C_TEST",
                "thread_ts": current_ts,
                "event_ts": current_ts,
                "user_id": "U_USER",
                "text": f"<@UBOT> here is the bug\n![screenshot]({current_image})",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    message_content = captured["message_content"]
    assert isinstance(message_content, dict)
    assert message_content["image_urls"] == [current_image]


def test_process_slack_mention_queue_excludes_historical_context_images(monkeypatch) -> None:
    historical_image = "https://files.slack.com/old-historical.png"
    current_image = "https://files.slack.com/new-screenshot.png"
    historical_ts = "1700000000.OLD"
    current_ts = "1700000099.NEW"
    captured: dict[str, object] = {}

    thread_messages: list[dict[str, object]] = [
        {
            "ts": historical_ts,
            "text": f"earlier context ![old]({historical_image})",
            "user": "U_HIST",
        },
        {
            "ts": current_ts,
            "text": f"<@UBOT> new issue ![new]({current_image})",
            "user": "U_USER",
        },
    ]

    async def fake_fetch_slack_thread_messages(
        channel_id: str, thread_ts: str
    ) -> list[dict[str, object]]:
        return thread_messages

    async def fake_is_thread_active(thread_id: str) -> bool:
        return True

    async def fake_get_slack_repo_config(
        message: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_upsert_metadata(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_add_reaction(*args: Any, **kwargs: Any) -> bool:
        return True

    async def fake_get_slack_user_info(user_id: str) -> dict[str, object] | None:
        return None

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        return {}

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        captured["message_content"] = message_content
        return True

    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", fake_upsert_metadata)
    monkeypatch.setattr(webapp, "add_slack_reaction", fake_add_reaction)
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        webapp.process_slack_mention(
            {
                "channel_id": "C_TEST",
                "thread_ts": historical_ts,
                "event_ts": current_ts,
                "user_id": "U_USER",
                "text": f"<@UBOT> new issue ![new]({current_image})",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    message_content = captured["message_content"]
    assert isinstance(message_content, dict)
    assert message_content["image_urls"] == [current_image]
    assert historical_image not in message_content["image_urls"]


def test_process_slack_mention_queue_empty_image_urls_for_text_only_follow_up(
    monkeypatch,
) -> None:
    current_ts = "1700000099.TEXTONLY"
    captured: dict[str, object] = {}

    thread_messages: list[dict[str, object]] = [
        {
            "ts": current_ts,
            "text": "<@UBOT> just a text follow-up no image here",
            "user": "U_USER",
        }
    ]

    async def fake_fetch_slack_thread_messages(
        channel_id: str, thread_ts: str
    ) -> list[dict[str, object]]:
        return thread_messages

    async def fake_is_thread_active(thread_id: str) -> bool:
        return True

    async def fake_get_slack_repo_config(
        message: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_upsert_metadata(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_add_reaction(*args: Any, **kwargs: Any) -> bool:
        return True

    async def fake_get_slack_user_info(user_id: str) -> dict[str, object] | None:
        return None

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        return {}

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        captured["message_content"] = message_content
        return True

    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", fake_upsert_metadata)
    monkeypatch.setattr(webapp, "add_slack_reaction", fake_add_reaction)
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue_message_for_thread)

    asyncio.run(
        webapp.process_slack_mention(
            {
                "channel_id": "C_TEST",
                "thread_ts": current_ts,
                "event_ts": current_ts,
                "user_id": "U_USER",
                "text": "<@UBOT> just a text follow-up no image here",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    message_content = captured["message_content"]
    assert isinstance(message_content, dict)
    assert message_content["image_urls"] == []


def test_process_slack_mention_non_busy_path_fetches_image_into_content_blocks(
    monkeypatch,
) -> None:
    current_image = "https://files.slack.com/new-screenshot.png"
    current_ts = "1700000099.NEW"
    captured: dict[str, object] = {}

    thread_messages: list[dict[str, object]] = [
        {
            "ts": current_ts,
            "text": f"<@UBOT> check this ![screenshot]({current_image})",
            "user": "U_USER",
        }
    ]

    async def fake_fetch_slack_thread_messages(
        channel_id: str, thread_ts: str
    ) -> list[dict[str, object]]:
        return thread_messages

    async def fake_is_thread_active(thread_id: str) -> bool:
        return False

    async def fake_get_slack_repo_config(
        message: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_upsert_metadata(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_add_reaction(*args: Any, **kwargs: Any) -> bool:
        return True

    async def fake_get_slack_user_info(user_id: str) -> dict[str, object] | None:
        return None

    async def fake_get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
        return {}

    async def fake_post_slack_trace_reply(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_fetch_image_block(*args: Any, **kwargs: Any) -> dict[str, object] | None:
        return {"type": "image", "source_type": "base64", "data": "fake"}

    class _FakeRunsClient:
        async def create(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            captured["run_input"] = kwargs.get("input")
            return {"run_id": "fake-run"}

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()

    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", fake_fetch_slack_thread_messages)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", fake_upsert_metadata)
    monkeypatch.setattr(webapp, "add_slack_reaction", fake_add_reaction)
    monkeypatch.setattr(webapp, "get_slack_user_info", fake_get_slack_user_info)
    monkeypatch.setattr(webapp, "get_slack_user_names", fake_get_slack_user_names)
    monkeypatch.setattr(webapp, "post_slack_trace_reply", fake_post_slack_trace_reply)
    monkeypatch.setattr(webapp, "fetch_image_block", fake_fetch_image_block)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())

    asyncio.run(
        webapp.process_slack_mention(
            {
                "channel_id": "C_TEST",
                "thread_ts": current_ts,
                "event_ts": current_ts,
                "user_id": "U_USER",
                "text": f"<@UBOT> check this ![screenshot]({current_image})",
                "bot_user_id": "UBOT",
            },
            {"owner": "langchain-ai", "name": "open-swe"},
        )
    )

    run_input = captured["run_input"]
    assert isinstance(run_input, dict)
    content_blocks = run_input["messages"][0]["content"]
    block_types = [block.get("type") for block in content_blocks]
    assert "text" in block_types
    assert "image" in block_types
