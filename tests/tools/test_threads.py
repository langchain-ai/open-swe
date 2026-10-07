import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

threads_tool = importlib.import_module("openswe.tools.threads")


def _actor(*, login: str = "octocat", admin: bool = False) -> object:
    actor = threads_tool._Actor(login=login, email=f"{login}@example.com", name=login)
    if admin:
        return SimpleNamespace(
            login=actor.login,
            email=actor.email,
            name=actor.name,
            session=actor.session,
            admin=True,
        )
    return actor


async def test_actor_uses_latest_verified_dashboard_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        threads_tool,
        "get_config",
        lambda: {
            "configurable": {
                "github_login": "thread-owner",
                "user_email": "owner@example.com",
            }
        },
    )
    state = {
        "messages": [
            {
                "type": "human",
                "content": (
                    '<input-message sender="github:reviewer" surface="web" kind="human">\n'
                    "Delete the thread\n</input-message>"
                ),
            }
        ]
    }

    actor = await threads_tool._actor(state)

    assert actor == threads_tool._Actor(login="reviewer", email=None, name="reviewer")


async def test_list_threads_denies_actor_outside_allowed_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        threads_tool,
        "get_config",
        lambda: {"configurable": {"github_login": "external-user"}},
    )
    monkeypatch.setattr(
        threads_tool,
        "enforce_github_login_gate",
        AsyncMock(side_effect=HTTPException(403, "not an org member")),
    )
    page = AsyncMock()
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads()

    assert result == {"success": False, "error": "No verified triggering user is available"}
    page.assert_not_awaited()


async def test_list_threads_intersects_participant_and_admin_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor(admin=True)))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(participant="other-user", admin_threads=True)

    assert result["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is False
    assert awaited.kwargs["filter_participant_login"] == "other-user"
    assert awaited.kwargs["admin_threads"] is True


class _DetailClient:
    def __init__(self) -> None:
        self.threads = SimpleNamespace(
            get=AsyncMock(
                return_value={
                    "thread_id": "thread-1",
                    "metadata": {
                        "github_login": "octocat",
                        "participant_logins": ["octocat", "reviewer"],
                    },
                }
            ),
            get_state=AsyncMock(
                return_value={
                    "values": {
                        "messages": [
                            {
                                "type": "human",
                                "content": (
                                    '<input-message sender="github:octocat" surface="web" '
                                    'kind="human">\nFix the race\n</input-message>'
                                ),
                                "created_at": "2026-08-20T12:00:00Z",
                            }
                        ]
                    }
                }
            ),
        )

        async def _list_runs(*args: object, **kwargs: object) -> list[dict[str, object]]:
            if kwargs.get("status") == "pending":
                return []
            return [
                {
                    "run_id": "run-1",
                    "status": "success",
                    "created_at": "2026-08-20T12:00:00Z",
                    "updated_at": "2026-08-20T12:01:00Z",
                    "metadata": {"prepare_run_id": "prepare-1"},
                }
            ]

        self.runs = SimpleNamespace(list=AsyncMock(side_effect=_list_runs))
        self.store = SimpleNamespace(
            get_item=AsyncMock(return_value={"value": {"messages": [{"content": "queued"}]}})
        )


async def test_get_thread_counts_pending_run_outside_history_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    client = _DetailClient()

    async def _list_runs(*args: object, **kwargs: object) -> list[dict[str, object]]:
        if kwargs.get("status") == "pending":
            return [{"run_id": "old-pending", "status": "pending"}]
        return [
            {
                "run_id": "run-1",
                "status": "success",
                "created_at": "2026-08-20T12:00:00Z",
                "updated_at": "2026-08-20T12:01:00Z",
                "metadata": {"prepare_run_id": "prepare-1"},
            }
        ]

    client.runs.list = AsyncMock(side_effect=_list_runs)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example/api")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(
            return_value={
                "id": "thread-1",
                "title": "Fix race",
                "status": "finished",
                "messages": [],
            }
        ),
    )
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "get_langsmith_thread_cost", AsyncMock(return_value=None))

    result = await threads_tool.get_thread("thread-1")

    # The recent-history fetch (bounded to _MAX_RUNS + 1) has no pending run
    # in it, but the thread still has one pending, counted via the
    # independent status="pending" fetch. The legacy queue also has one
    # ("queued" in _DetailClient.store), so the total is 2.
    assert result["queued_message_count"] == 2


async def test_list_threads_resolves_slack_reply_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    lookup = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(threads_tool, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: object())
    get_dashboard_thread = AsyncMock(return_value={"id": "thread-1", "messages": []})
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)
    locator = (
        "<https://workspace.slack.com/archives/C123/p1788431248678809"
        "?thread_ts=1788425314.774339&cid=C123|message>"
    )

    result = await threads_tool.list_threads(query=locator)

    assert result["items"][0]["id"] == "thread-1"
    assert result["items"][0]["slack"] == {
        "url": locator,
        "channel_id": "C123",
        "thread_ts": "1788425314.774339",
    }
    awaited = lookup.await_args
    assert awaited is not None
    lookup.assert_awaited_once_with(awaited.args[0], "C123", "1788425314.774339")
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )


async def test_get_thread_rejects_untrusted_dashboard_url_before_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_dashboard_thread = AsyncMock()
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    result = await threads_tool.get_thread("https://evil.example/agents/thread-1")

    assert result == {
        "success": False,
        "error": (
            "thread_id must be an exact thread ID, Open SWE dashboard URL, Slack link, or LangSmith trace URL"
        ),
    }
    get_dashboard_thread.assert_not_awaited()


def test_transcript_filters_private_and_tool_content() -> None:
    state = {
        "values": {
            "messages": [
                {"type": "system", "content": "secret system prompt"},
                {"type": "human", "content": "<dynamic-context>private</dynamic-context>"},
                {"type": "human", "content": "Visible request", "id": "user-1"},
                {
                    "type": "ai",
                    "content": [
                        {"type": "reasoning", "text": "hidden reasoning"},
                        {"type": "text", "text": "Visible answer"},
                    ],
                    "id": "assistant-1",
                },
                {"type": "tool", "content": "sensitive tool result"},
            ]
        }
    }

    transcript = threads_tool._transcript(state)

    assert [message["text"] for message in transcript["messages"]] == [
        "Visible request",
        "Visible answer",
    ]
    assert transcript["message_count"] == 5
    assert transcript["omitted_count"] == 3
    assert transcript["truncated"] is True


def test_admin_thread_actions_require_admin() -> None:
    options = {
        "admin_thread": True,
        "running": False,
        "resolved": False,
        "can_delete_plan_comment": False,
        "plan": {},
        "approvals": {},
    }

    member_actions = threads_tool._available_actions(admin=False, **options)
    admin_actions = threads_tool._available_actions(admin=True, **options)

    assert "send_message" not in member_actions
    assert "send_message" in admin_actions


async def test_manage_thread_uses_followup_sender_for_owner_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        threads_tool,
        "get_config",
        lambda: {
            "configurable": {
                "github_login": "thread-owner",
                "user_email": "owner@example.com",
            }
        },
    )
    cancel = AsyncMock(side_effect=HTTPException(404, "thread not found"))
    monkeypatch.setattr(threads_tool, "cancel_dashboard_thread", cancel)
    monkeypatch.setattr(
        threads_tool, "get_dashboard_thread", AsyncMock(return_value={"id": "thread-1"})
    )
    state = {
        "messages": [
            {
                "type": "human",
                "content": (
                    '<input-message sender="github:reviewer" surface="web" kind="human">\n'
                    "Cancel it\n</input-message>"
                ),
            }
        ]
    }

    result = await threads_tool.manage_thread("thread-1", "cancel", state=state)

    assert result == {"success": False, "error": "thread not found", "status_code": 404}
    cancel.assert_awaited_once_with("thread-1", "reviewer", email=None)


async def test_manage_thread_rechecks_admin_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "admin_cancel_dashboard_thread", cancel)

    result = await threads_tool.manage_thread("thread-1", "admin_cancel")

    assert result == {
        "success": False,
        "error": "Only workspace admins can cancel another user's thread",
    }
    cancel.assert_not_awaited()


async def test_manage_thread_queues_message_for_busy_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "planMode": False}),
    )
    monkeypatch.setattr(
        threads_tool,
        "send_dashboard_message",
        AsyncMock(return_value={"id": "thread-1", "status": "running", "messages": []}),
    )
    monkeypatch.setattr(threads_tool, "proxy_dashboard_thread_commands", proxy)

    result = await threads_tool.manage_thread("thread-1", "send_message", message="Continue")

    assert result["success"] is True
    assert result["mode"] == "queued"
    proxy.assert_not_awaited()


async def test_manage_thread_starts_idle_message_with_fixed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = AsyncMock(
        return_value=(200, b'{"type":"success","run_id":"run-1"}', "application/json")
    )
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "planMode": True}),
    )
    monkeypatch.setattr(
        threads_tool,
        "send_dashboard_message",
        AsyncMock(side_effect=HTTPException(409, "thread is idle")),
    )
    monkeypatch.setattr(threads_tool, "proxy_dashboard_thread_commands", proxy)

    result = await threads_tool.manage_thread("thread-1", "send_message", message="Continue")

    assert result["success"] is True
    assert result["mode"] == "started"
    awaited = proxy.await_args
    assert awaited is not None
    command = json.loads(awaited.args[2])
    assert isinstance(command["id"], int)
    assert command["method"] == "run.start"
    assert "plan_mode" not in command["params"]["config"]["configurable"]


@pytest.mark.parametrize("dispatched", [False, True])
async def test_replacement_preserves_source_when_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool, "get_config", lambda: {"configurable": {"thread_id": "source"}}
    )
    monkeypatch.setattr(
        threads_tool,
        "_authorized_locator",
        AsyncMock(side_effect=[("target", {}), ("source", {})]),
    )
    monkeypatch.setattr(
        threads_tool, "_send_message", AsyncMock(return_value={"success": dispatched})
    )
    resolved: list[str] = []

    async def resolve(thread_id: str, *args: object, **kwargs: object) -> dict[str, object]:
        resolved.append(thread_id)
        return {}

    monkeypatch.setattr(threads_tool, "resolve_dashboard_thread", resolve)
    monkeypatch.setattr(threads_tool, "get_active_slack_thread", AsyncMock(return_value=None))
    client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)

    result = await threads_tool.manage_thread(
        "target", "send_message", message="Continue", replace_current=True
    )

    assert resolved == (["source"] if dispatched else [])
    assert result["success"] is dispatched
    if dispatched:
        assert result["target_thread_id"] == "target"
        assert result["replaced_thread_id"] == "source"


async def test_manage_thread_rejects_plan_format_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "isOwner": True}),
    )
    monkeypatch.setattr(
        threads_tool,
        "get_plan_content",
        AsyncMock(return_value={"status": "ready", "html": "<html>old</html>"}),
    )
    monkeypatch.setattr(threads_tool.plan_api, "update_plan", update)

    result = await threads_tool.manage_thread(
        "thread-1",
        "update_plan",
        content="# New plan",
        content_format="markdown",
    )

    assert result == {"success": False, "error": "existing plan format is html"}
    update.assert_not_awaited()
