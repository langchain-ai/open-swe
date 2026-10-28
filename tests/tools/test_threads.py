import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, get_args
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.input_messages import human_input, person_introduction
from openswe.message_queue import QueuedMessage
from openswe.slack import allowed_bots

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
            automation=actor.automation,
        )
    return actor


@pytest.mark.parametrize(
    (
        "sender",
        "surface",
        "context_id",
        "context_login",
        "stored_login",
        "lookup_fails",
        "expected",
    ),
    [
        ("github:123", "github", "github:123", "reviewer", None, False, "reviewer"),
        ("github:123", "github", "github:456", "other", None, False, None),
        ("github:123", "github", "github:123", None, "reviewer", False, "reviewer"),
        ("github:123", "github", "github:123", None, None, False, None),
        ("github:123", "github", "github:123", None, None, True, None),
        ("github:123", "web", "github:456", "other", None, False, "123"),
        ("github:123", "web", "github:456", "other", "different-user", False, "123"),
        ("github:123", "automation", "github:456", "other", None, False, "123"),
        ("github:reviewer", "github", "github:456", "other", None, False, "reviewer"),
        ("slack:U123", "github", "github:123", "reviewer", None, False, "thread-owner"),
    ],
)
async def test_actor_uses_latest_resolvable_github_sender(
    monkeypatch: pytest.MonkeyPatch,
    sender: str,
    surface: Literal["github", "web", "automation"],
    context_id: str,
    context_login: str | None,
    stored_login: str | None,
    lookup_fails: bool,
    expected: str | None,
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
    monkeypatch.setattr(
        threads_tool.User,
        "for_identity",
        AsyncMock(
            return_value=SimpleNamespace(github_login=stored_login) if stored_login else None,
            side_effect=RuntimeError("store unavailable") if lookup_fails else None,
        ),
    )

    async def gate(login: str) -> None:
        if login not in {"thread-owner", expected}:
            raise HTTPException(403, "not authorized")

    monkeypatch.setattr(threads_tool, "enforce_github_login_gate", gate)
    person = person_introduction({"id": context_id, "github_login": context_login or ""})
    state = {
        "messages": [
            human_input(
                "Earlier request",
                {"sender_id": "github:earlier", "surface": "github", "kind": "human"},
            ),
            person,
            human_input(
                "Delete the thread",
                {"sender_id": sender, "surface": surface, "kind": "human"},
            ),
            person,
        ]
    }

    actor = await threads_tool.resolve_thread_actor(state)

    if expected is None:
        assert actor is None
        return
    assert actor == threads_tool._Actor(
        login=expected,
        email="owner@example.com" if expected == "thread-owner" else None,
        name=expected,
    )


@pytest.mark.parametrize("authorized", [True, False])
async def test_github_sender_can_use_thread_tools_only_when_authorized(
    monkeypatch: pytest.MonkeyPatch, authorized: bool
) -> None:
    monkeypatch.setenv(
        "ALLOWED_GITHUB_USERS", "thread-owner,reviewer" if authorized else "thread-owner"
    )
    monkeypatch.setattr(
        threads_tool, "get_config", lambda: {"configurable": {"github_login": "thread-owner"}}
    )
    state = {
        "messages": [
            person_introduction({"id": "github:123", "github_login": "reviewer"}),
            human_input(
                "Send the author a confirmation",
                {
                    "sender_id": "github:123",
                    "surface": "github",
                    "kind": "human",
                },
            ),
        ]
    }
    monkeypatch.setattr(
        threads_tool,
        "list_dashboard_threads_page",
        AsyncMock(return_value={"items": [{"id": "thread-1"}]}),
    )
    monkeypatch.setattr(
        threads_tool, "get_dashboard_thread", AsyncMock(return_value={"id": "thread-1"})
    )
    monkeypatch.setattr(threads_tool, "langgraph_client", _DetailClient)
    for name, value in (
        ("get_plan_content", {}),
        ("list_plan_comments", []),
        ("get_workflow_push_approvals", {}),
        ("_queued_message_count", 0),
        ("get_langsmith_thread_cost", None),
    ):
        monkeypatch.setattr(threads_tool, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        threads_tool,
        "proxy_dashboard_thread_commands",
        AsyncMock(
            return_value=(
                200,
                b'{"id": 1, "type": "success", "result": {"run_id": "run-2"}}',
                "application/json",
            )
        ),
    )

    listed = await threads_tool.list_threads(state=state)
    detail = await threads_tool.get_thread("thread-1", state=state)
    sent = await threads_tool.manage_thread(
        "thread-1", "send_message", message="Please confirm", state=state
    )

    assert listed["success"] is authorized
    assert detail["success"] is authorized
    assert sent["success"] is authorized
    if authorized:
        assert listed["items"][0]["id"] == "thread-1"
        assert detail["thread"]["id"] == "thread-1"
        assert sent["run_id"] == "run-2"


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

    assert result == {
        "success": False,
        "error": "Thread lookup was unavailable. No verified triggering user is available",
    }
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


@pytest.mark.parametrize(
    ("scope", "summary_only", "compact"),
    [
        ("automation", None, True),
        ("automation", False, False),
        ("interactive", None, False),
        ("all", None, False),
        ("interactive", True, True),
    ],
)
async def test_list_threads_outcomes_and_pagination(
    monkeypatch: pytest.MonkeyPatch,
    scope: threads_tool.ThreadScope,
    summary_only: bool | None,
    compact: bool,
) -> None:
    outcome = "Backport investigation. " * 30 + (
        "source=org/repo#123 target=release-2 attempts=3 "
        "last_attempt=2026-10-23T08:00:00Z status=terminal"
    )
    records = [
        {
            "id": f"thread-{index}",
            "status": "idle",
            "title": "Backport",
            "createdAt": "2026-10-23T07:00:00Z",
            "updatedAt": "2026-10-23T08:00:00Z",
            "messages": [],
            "plan": {"content": "large dashboard detail" * 200},
            "pr": {
                "url": "https://github.com/org/repo/pull/123",
                "number": 123,
                "state": "merged",
                "title": "Extra PR metadata",
            },
        }
        for index in range(50)
    ]

    async def page(*args: object, **kwargs: object) -> dict[str, object]:
        offset = kwargs["offset"]
        limit = kwargs["limit"]
        assert isinstance(offset, int) and isinstance(limit, int)
        return {
            "items": records[offset : offset + limit],
            "limit": limit,
            "offset": offset,
            "hasMore": offset + limit < len(records),
        }

    records[0]["final_message"] = outcome
    records[1]["messages"] = [{"role": "assistant", "content": outcome}]

    async def get_state(thread_id: str) -> dict[str, object] | None:
        assert thread_id not in {"thread-0", "thread-1"}
        if thread_id == "thread-48":
            return None
        return {
            "values": {
                "messages": [
                    {
                        "role": "assistant",
                        "content": outcome if thread_id == "thread-2" else "No eligible backports.",
                    },
                    {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file"}]},
                    {"role": "tool", "content": "tool output"},
                ]
                if thread_id != "thread-49"
                else []
            }
        }

    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)
    monkeypatch.setattr(threads_tool.THREADS, "state", get_state)
    result = await threads_tool.list_threads(scope=scope, summary_only=summary_only, limit=50)
    assert result["success"] is True
    assert result["offset"] == 0
    assert result["limit"] == 50
    assert result["has_more"] is False
    collected = result["items"]
    assert len(collected) == 50
    if compact:
        assert len(json.dumps(result)) < 20_000
        assert collected[0] == {
            **{key: records[0][key] for key in ("id", "status", "createdAt", "updatedAt", "title")},
            "webUrl": threads_tool.dashboard_thread_url("thread-0"),
            "pr": {"url": "https://github.com/org/repo/pull/123", "number": 123, "state": "merged"},
            "final_message": outcome[:500],
        }
        assert collected[1]["final_message"] == outcome[:500]
        assert collected[2]["final_message"] == outcome[:500]
        assert collected[3]["final_message"] == "No eligible backports."
        assert collected[-2]["final_message"] is None
        assert collected[-1]["final_message"] is None
    else:
        assert collected[0]["plan"] == records[0]["plan"]
        assert collected[0]["pr"] == records[0]["pr"]
        assert collected[0]["final_message"] == outcome
        assert "final_message" not in collected[2]


_AUTOMATION_ID = "706242c0-edba-4ab7-a342-154c354f9946"


def _automation_run(
    monkeypatch: pytest.MonkeyPatch, thread_metadata: dict[str, object]
) -> AsyncMock:
    monkeypatch.setattr(
        threads_tool,
        "get_config",
        lambda: {
            "configurable": {
                "source": "schedule",
                "schedule_id": _AUTOMATION_ID,
                "thread_id": "automation-thread",
            }
        },
    )
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"thread_id": "automation-thread", "metadata": thread_metadata}
            )
        )
    )
    monkeypatch.setattr(threads_tool.schedules, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        threads_tool.schedules,
        "get_agent_schedule",
        AsyncMock(return_value={"id": _AUTOMATION_ID, "name": "CI watch"}),
    )
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)
    return page


async def test_automation_run_reads_public_threads_but_cannot_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _automation_run(
        monkeypatch,
        {"owner_type": "system", "visibility": "public", "schedule_id": _AUTOMATION_ID},
    )

    listed = await threads_tool.list_threads(scope="automation", automation_id=_AUTOMATION_ID)
    sent = await threads_tool.manage_thread(
        action="send_message", thread_id="dm-thread", message="digest"
    )

    assert listed["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is True
    assert awaited.kwargs["include_private"] is False
    assert sent["success"] is False
    assert "cannot message" in sent["error"]


@pytest.mark.parametrize(
    "thread_metadata",
    [
        {"owner_type": "system", "visibility": "public", "schedule_id": "another-automation"},
        {"owner_type": "user", "visibility": "public", "schedule_id": _AUTOMATION_ID},
        {"owner_type": "system", "visibility": "private", "schedule_id": _AUTOMATION_ID},
    ],
)
async def test_automation_reader_requires_the_automations_own_thread(
    monkeypatch: pytest.MonkeyPatch, thread_metadata: dict[str, object]
) -> None:
    page = _automation_run(monkeypatch, thread_metadata)

    result = await threads_tool.list_threads(scope="automation")

    assert result == {
        "success": False,
        "error": "Thread lookup was unavailable. No verified triggering user is available",
    }
    page.assert_not_awaited()


_SLACK_BOT = {
    "channel_id": "C123",
    "thread_ts": "123.456",
    "team_id": "T123",
    "triggering_bot_id": "B123",
    "triggering_bot_app_id": "A123",
    "triggering_user_id": "U123",
}


def _slack_bot_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata_override: dict[str, object] | None = None,
    config_override: dict[str, object] | None = None,
) -> tuple[dict[str, object], AsyncMock, AsyncMock]:
    metadata = {
        "owner_type": "system",
        "visibility": "public",
        "trigger_kind": "slack_bot",
        "triggering_bot": "T123:B123",
        "source_context": {"slack_thread": _SLACK_BOT},
        **(metadata_override or {}),
    }
    page = _automation_run(monkeypatch, metadata)
    monkeypatch.setattr(
        allowed_bots.THREADS,
        "get",
        AsyncMock(return_value=SimpleNamespace(metadata=metadata)),
    )
    allowed = AsyncMock(
        return_value=allowed_bots.AllowedSlackBot(
            team_id="T123",
            bot_id="B123",
            user_id="U123",
            app_id="A123",
            name="Engine",
            created_at=datetime(2026, 9, 9, tzinfo=UTC),
        )
    )
    monkeypatch.setattr(allowed_bots.AllowedSlackBot, "get", allowed)
    configurable: dict[str, object] = {
        "source": "slack",
        "thread_id": "automation-thread",
        "slack_thread": _SLACK_BOT,
        **(config_override or {}),
    }
    monkeypatch.setattr(threads_tool, "get_config", lambda: {"configurable": configurable})
    return configurable, page, allowed


_SLACK_BOT_READER = "system:slack-bot:T123:B123"


@pytest.mark.parametrize("wakeup", [False, True])
async def test_allowed_slack_bot_finds_pr_work_without_write_privileges(
    monkeypatch: pytest.MonkeyPatch, wakeup: bool
) -> None:
    configurable, page, _ = _slack_bot_run(monkeypatch)
    if wakeup:
        wakeup_tool = importlib.import_module("openswe.tools.schedule_thread_wakeup")
        monkeypatch.setattr(
            wakeup_tool.RunConfig,
            "from_runtime",
            lambda: threads_tool.RunConfig.parse(configurable),
        )
        monkeypatch.setattr(wakeup_tool, "get_client", lambda **kwargs: object())
        monkeypatch.setattr(
            wakeup_tool,
            "get_active_slack_thread",
            AsyncMock(return_value=configurable["slack_thread"]),
        )
        monkeypatch.setattr(wakeup_tool, "_purge_expired_wakeups_best_effort", AsyncMock())
        monkeypatch.setattr(wakeup_tool.THREAD_WAKEUPS, "used", AsyncMock(return_value=("g", 0)))
        monkeypatch.setattr(wakeup_tool.THREAD_WAKEUPS, "record", AsyncMock())
        scheduled = AsyncMock(return_value={"success": True})
        monkeypatch.setattr(wakeup_tool, "_schedule_wakeup", scheduled)
        await wakeup_tool.schedule_thread_wakeup(1)
        assert scheduled.await_args is not None
        configurable = scheduled.await_args.kwargs["configurable"]
        monkeypatch.setattr(threads_tool, "get_config", lambda: {"configurable": configurable})
    pr = "https://github.com/org/repo/pull/42"
    page.return_value = {
        "items": [{"id": "implementation", "pr": {"url": pr}}],
        "hasMore": False,
    }

    result = await threads_tool.list_threads(query=pr)

    assert result["success"] is True
    assert result["items"][0]["id"] == "implementation"
    assert page.await_args is not None
    assert page.await_args.args == (_SLACK_BOT_READER,)
    assert page.await_args.kwargs["include_all"] is True
    assert page.await_args.kwargs["include_private"] is False
    actor = await threads_tool._reader()
    assert actor == threads_tool._Actor(
        login=_SLACK_BOT_READER, email=None, name="Engine", automation=True
    )
    assert actor.admin is False
    for action in get_args(threads_tool.ThreadAction):
        denied = await threads_tool.manage_thread("implementation", action, message="fix")
        assert denied == {"success": False, "error": threads_tool._NO_WRITER}
    started = await threads_tool.start_thread(title="Fix", instructions="fix", repos=["org/repo"])
    assert started == {"success": False, "error": threads_tool._NO_WRITER}


@pytest.mark.parametrize(
    ("metadata_override", "config_override", "allowlisted"),
    [
        ({"owner_type": "user"}, {}, True),
        ({"visibility": "private"}, {}, True),
        ({"trigger_kind": "user"}, {}, True),
        ({"triggering_bot": "T123:B999"}, {}, True),
        ({"source_context": {}}, {}, True),
        ({}, {"slack_thread": {**_SLACK_BOT, "channel_id": "C999"}}, True),
        ({}, {"slack_thread": {**_SLACK_BOT, "triggering_bot_app_id": "A999"}}, True),
        ({}, {"slack_thread": {**_SLACK_BOT, "triggering_user_id": "U999"}}, True),
        ({}, {"github_login": "rejected-user"}, True),
        ({}, {"thread_id": None}, True),
        ({}, {}, False),
    ],
)
async def test_slack_bot_reader_requires_allowed_bot_on_its_own_public_thread(
    monkeypatch: pytest.MonkeyPatch,
    metadata_override: dict[str, object],
    config_override: dict[str, object],
    allowlisted: bool,
) -> None:
    _, page, allowed = _slack_bot_run(
        monkeypatch, metadata_override=metadata_override, config_override=config_override
    )
    if not allowlisted:
        allowed.return_value = None
    monkeypatch.setattr(
        threads_tool,
        "enforce_github_login_gate",
        AsyncMock(side_effect=HTTPException(403, "denied")),
    )

    for result in (await threads_tool.list_threads(), await threads_tool.get_thread("target")):
        assert result == {"success": False, "error": threads_tool._NO_READER}
    page.assert_not_awaited()


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


@pytest.mark.parametrize("visibility", ["public", "private"])
async def test_allowed_slack_bot_inspects_only_public_threads(
    monkeypatch: pytest.MonkeyPatch, visibility: str
) -> None:
    _slack_bot_run(monkeypatch)
    original_get = threads_tool.langgraph_client().threads.get
    client = _DetailClient()

    async def get(thread_id: str) -> dict[str, object]:
        if thread_id == "automation-thread":
            return await original_get(thread_id)
        return {"thread_id": thread_id, "metadata": {"visibility": visibility}}

    client.threads.get = get
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "visibility": visibility, "status": "finished"}),
    )
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "_queued_message_count", AsyncMock(return_value=0))
    monkeypatch.setattr(threads_tool, "_thread_cost", AsyncMock(return_value=None))

    result = await threads_tool.get_thread("thread-1")

    if visibility == "public":
        assert result["success"] is True
        assert result["transcript"]["messages"][0]["text"] == "Fix the race"
        assert result["available_actions"] == []
    else:
        assert result == {
            "success": False,
            "error": "this private thread can only be reached from its owner's private conversation",
            "status_code": 403,
        }
        client.threads.get_state.assert_not_awaited()


async def test_get_thread_counts_pending_run_outside_history_window(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    await QueuedMessage.put("thread-1", "queued")
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
    # independent status="pending" fetch. The message queue also has one, so
    # the total is 2.
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
    lookup.assert_awaited_once_with("C123", "1788425314.774339")
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


@pytest.mark.parametrize("role", ["ai", "assistant"])
@pytest.mark.parametrize("outcome", ["Done", "x" * 5_000 + "\nretry ledger: complete"])
async def test_get_thread_exposes_latest_outcome(
    monkeypatch: pytest.MonkeyPatch, role: str, outcome: str
) -> None:
    client = _DetailClient()
    timestamp = "2026-08-20T12:01:00Z"
    client.threads.get_state.return_value = {
        "values": {
            "messages": [
                {"type": "human", "content": "instructions" * 1_000},
                {"type": "ai", "content": "Older outcome"},
                {"role": role, "content": outcome, "created_at": timestamp},
                {"type": "ai", "content": [{"type": "reasoning", "text": "hidden"}]},
                {"type": "tool", "content": "tool output"},
            ]
        }
    }
    monkeypatch.setattr(threads_tool, "_reader", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "_authorized_locator",
        AsyncMock(return_value=("thread-1", {"id": "thread-1", "status": "finished"})),
    )
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))
    monkeypatch.setattr(threads_tool, "_queued_message_count", AsyncMock(return_value=0))
    monkeypatch.setattr(threads_tool, "get_langsmith_thread_cost", AsyncMock(return_value=None))

    result = await threads_tool.get_thread("thread-1")

    assert result["last_assistant_message"] == {
        "text": outcome[-4_000:],
        "truncated": len(outcome) > 4_000,
        "timestamp": timestamp,
    }


def test_latest_outcome_without_nonempty_assistant_message() -> None:
    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "request"},
                {"type": "ai", "content": "   "},
                {"type": "assistant", "content": [{"type": "reasoning", "text": "hidden"}]},
            ]
        }
    }

    assert threads_tool._last_assistant_message(state) is None


@pytest.mark.parametrize("count, width, retained", [(105, 10, 100), (15, 4_000, 13)])
def test_transcript_budgets_keep_newest_messages(count: int, width: int, retained: int) -> None:
    messages = [
        {"type": "ai", "id": str(index), "content": str(index).zfill(width)}
        for index in range(count)
    ]

    transcript = threads_tool._transcript({"values": {"messages": messages}})
    returned = transcript["messages"]

    assert [message["id"] for message in returned] == [
        str(index) for index in range(count - retained, count)
    ]
    assert returned[-1]["text"] == messages[-1]["content"]
    assert transcript["omitted_count"] == count - retained
    assert transcript["returned_count"] == retained
    assert transcript["message_count"] == count
    assert transcript["truncated"] is True
    assert sum(len(message["text"]) for message in returned) <= 50_000
    if width == 4_000:
        assert returned[0]["truncated"] is True
        assert returned[0]["text"] == messages[count - retained]["content"][-2_000:]
        assert all(not message["truncated"] for message in returned[1:])


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


@pytest.mark.parametrize(
    ("result", "mode"),
    [
        ({"run_id": "run-2", "queued": True}, "queued"),
        ({"run_id": "run-1", "message_id": "m-1", "steered": True}, "steered"),
    ],
)
async def test_manage_thread_sends_to_busy_thread_like_the_dashboard(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, object], mode: str
) -> None:
    reply = json.dumps({"id": 1, "type": "success", "result": result}).encode()
    proxy = AsyncMock(return_value=(200, reply, "application/json"))
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "planMode": False}),
    )
    monkeypatch.setattr(threads_tool, "proxy_dashboard_thread_commands", proxy)

    sent = await threads_tool.manage_thread("thread-1", "send_message", message="Continue")

    assert sent == {"success": True, "mode": mode, "run_id": result["run_id"]}


async def test_manage_thread_starts_idle_message_with_fixed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = AsyncMock(
        return_value=(
            200,
            b'{"id":1,"type":"success","result":{"run_id":"run-1"}}',
            "application/json",
        )
    )
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "thread-1", "planMode": True}),
    )
    monkeypatch.setattr(threads_tool, "proxy_dashboard_thread_commands", proxy)
    monkeypatch.setattr(
        threads_tool, "_config", lambda: {"configurable": {"thread_id": "coordinator"}}
    )

    result = await threads_tool.manage_thread("thread-1", "send_message", message="Continue")

    assert result["success"] is True
    assert result["mode"] == "started"
    assert result["run_id"] == "run-1"
    awaited = proxy.await_args
    assert awaited is not None
    command = json.loads(awaited.args[2])
    assert isinstance(command["id"], int)
    assert command["method"] == "run.start"
    assert "plan_mode" not in command["params"]["config"]["configurable"]
    assert awaited.kwargs["relayed_from"] == "coordinator"


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


@pytest.mark.parametrize(
    ("login", "relayed_from", "action", "queued_run_id", "allowed"),
    [
        ("octocat", "coordinator", "send_message", None, True),
        ("other-user", "coordinator", "send_message", None, False),
        ("octocat", "unrelated", "send_message", None, False),
        ("octocat", "coordinator", "cancel", None, False),
        ("octocat", "coordinator", "send_message", "worker-run", True),
        ("octocat", "coordinator", "send_message", "previous-run", False),
        ("octocat", None, "send_message", "worker-run", False),
    ],
)
async def test_public_worker_can_only_reply_to_its_owners_relaying_private_thread(
    monkeypatch: pytest.MonkeyPatch,
    login: str,
    relayed_from: str | None,
    action: str,
    queued_run_id: str | None,
    allowed: bool,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor(login=login)))
    monkeypatch.setattr(
        threads_tool,
        "_config",
        lambda: {
            "configurable": {
                "thread_id": "worker",
                "run_id": "worker-run",
                "relayed_from_thread_id": relayed_from if queued_run_id is None else None,
            }
        },
    )
    monkeypatch.setattr(
        threads_tool,
        "langgraph_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": {}}))
        ),
    )
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(
            return_value={"id": "coordinator", "visibility": "private", "ownerLogin": "octocat"}
        ),
    )
    proxy = AsyncMock(return_value=(200, b'{"result":{"run_id":"report-run"}}', "application/json"))
    monkeypatch.setattr(threads_tool, "proxy_dashboard_thread_commands", proxy)
    state = (
        {"relay_run_id": queued_run_id, "relayed_from_thread_id": relayed_from}
        if queued_run_id is not None
        else None
    )

    result = await threads_tool.manage_thread(
        "coordinator",
        action,
        message="Pushed the fix" if action == "send_message" else None,
        state=state,
    )

    if allowed:
        assert result == {"success": True, "mode": "started", "run_id": "report-run"}
    else:
        assert result == {
            "success": False,
            "error": "this private thread can only be reached from its owner's private conversation",
            "status_code": 403,
        }
        proxy.assert_not_awaited()
