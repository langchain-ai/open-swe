import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

threads_tool = importlib.import_module("agent.tools.threads")


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


async def test_actor_uses_only_trusted_run_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "configurable": {
            "github_login": "trusted-user",
            "user_email": "trusted@example.com",
            "slack_thread": {"triggering_user_name": "Untrusted Name"},
        }
    }
    monkeypatch.setattr(threads_tool, "get_config", lambda: config)

    actor = await threads_tool._actor()

    assert actor == threads_tool._Actor(
        login="trusted-user",
        email="trusted@example.com",
        name="trusted-user",
    )


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
                    '<input-message sender="github:reviewer" surface="web" kind="human">'
                    "<content>Delete the thread</content></input-message>"
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
        "enforce_org_login_gate",
        AsyncMock(side_effect=HTTPException(403, "not an org member")),
    )
    page = AsyncMock()
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads()

    assert result == {"success": False, "error": "No verified triggering user is available"}
    page.assert_not_awaited()


async def test_list_threads_defaults_to_triggering_user(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _actor()
    page = AsyncMock(
        return_value={
            "items": [{"id": "thread-1", "title": "One", "messages": [], "sandboxId": "sb"}],
            "limit": 25,
            "offset": 0,
            "hasMore": False,
        }
    )
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads()

    assert result["success"] is True
    assert result["items"] == [
        {
            "id": "thread-1",
            "title": "One",
            "webUrl": "https://openswe.vercel.app/agents/thread-1",
        }
    ]
    page.assert_awaited_once_with(
        "octocat",
        email="octocat@example.com",
        limit=25,
        offset=0,
        include_all=False,
        resolved=None,
        viewed=None,
        source=None,
        status=None,
        query=None,
        scope="all",
        automation_id=None,
        filter_participant_login=None,
        surfaced_only=True,
        admin_threads=None,
    )


async def test_list_threads_allows_cross_user_participant_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(participant="other-user")

    assert result["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["filter_participant_login"] == "other-user"


async def test_list_threads_admin_filter_searches_all_admin_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor(admin=True)))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(admin_threads=True)

    assert result["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is True
    assert awaited.kwargs["admin_threads"] is True
    assert awaited.kwargs["surfaced_only"] is True


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


async def test_list_threads_admin_filter_is_readable_by_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(admin_threads=True)

    assert result["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is True
    assert awaited.kwargs["admin_threads"] is True


async def test_list_threads_admin_participant_filter_keeps_admin_as_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 25, "offset": 0, "hasMore": False})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor(admin=True)))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(participant="other-user")

    assert result["success"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.args[0] == "octocat"
    assert awaited.kwargs["filter_participant_login"] == "other-user"
    assert awaited.kwargs["surfaced_only"] is True


async def test_list_threads_all_users_uses_server_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = AsyncMock(return_value={"items": [], "limit": 10, "offset": 20, "hasMore": True})
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(all_users=True, limit=10, offset=20, status="running")

    assert result["success"] is True
    assert result["has_more"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is True
    assert awaited.kwargs["status"] == "running"


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
                                    'kind="human"><content>Fix the race</content></input-message>'
                                ),
                                "created_at": "2026-08-20T12:00:00Z",
                            }
                        ]
                    }
                }
            ),
        )
        self.runs = SimpleNamespace(
            list=AsyncMock(
                return_value=[
                    {
                        "run_id": "run-1",
                        "status": "success",
                        "created_at": "2026-08-20T12:00:00Z",
                        "updated_at": "2026-08-20T12:01:00Z",
                        "metadata": {"prepare_run_id": "prepare-1"},
                    }
                ]
            )
        )
        self.store = SimpleNamespace(
            get_item=AsyncMock(return_value={"value": {"messages": [{"content": "queued"}]}})
        )


async def test_get_thread_returns_links_cost_last_message_and_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _DetailClient()
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(
            return_value={
                "id": "thread-1",
                "title": "Fix race",
                "status": "finished",
                "traceUrl": "https://smith.example/t/thread-1",
                "sourceUrl": "https://slack.example/thread",
                "messages": [],
                "pr": {"url": "https://github.com/acme/repo/pull/1"},
            }
        ),
    )
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        threads_tool,
        "get_plan_content",
        AsyncMock(return_value={"status": "ready", "html": "<html></html>"}),
    )
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        threads_tool,
        "get_workflow_push_approvals",
        AsyncMock(
            return_value={
                "fp": {
                    "fingerprint": "fp",
                    "status": "pending",
                    "repo": "acme/repo",
                    "files": [".github/workflows/ci.yml"],
                }
            }
        ),
    )
    monkeypatch.setattr(
        threads_tool,
        "get_langsmith_thread_cost",
        AsyncMock(
            return_value=SimpleNamespace(
                total_cost=0.42,
                last_end_time=SimpleNamespace(isoformat=lambda: "2026-08-20T12:01:00+00:00"),
            )
        ),
    )

    result = await threads_tool.get_thread("thread-1")

    assert result["success"] is True
    assert result["participant_logins"] == ["octocat", "reviewer"]
    assert result["last_user_message"] == {
        "text": "Fix the race",
        "truncated": False,
        "sender_id": "github:octocat",
        "timestamp": "2026-08-20T12:00:00Z",
    }
    assert result["cost"] == {
        "status": "available",
        "total_usd": 0.42,
        "last_end_time": "2026-08-20T12:01:00+00:00",
    }
    assert result["queued_message_count"] == 1
    assert result["links"]["web"].endswith("/agents/thread-1")
    assert result["links"]["trace"] == "https://smith.example/t/thread-1"
    assert result["langsmith"] == {
        "trace_url": "https://smith.example/t/thread-1",
        "thread_id": "thread-1",
        "run_id": None,
    }
    assert result["thread"]["langsmith"] == result["langsmith"]
    assert "approve_plan" in result["available_actions"]
    assert "approve_workflow_push" in result["available_actions"]
    assert result["transcript"] == {
        "messages": [
            {
                "id": None,
                "role": "user",
                "text": "Fix the race",
                "truncated": False,
                "sender_id": "github:octocat",
                "timestamp": "2026-08-20T12:00:00Z",
            }
        ],
        "message_count": 1,
        "returned_count": 1,
        "omitted_count": 0,
        "truncated": False,
    }
    assert result["recent_runs"]["runs"] == [result["latest_run"]]
    assert result["plan"]["content"] == "<html></html>"
    assert result["plan"]["comments"] == []
    assert result["state"]["message_count"] == 1
    client.runs.list.assert_awaited_once_with("thread-1", limit=threads_tool._MAX_RUNS + 1)


async def test_get_thread_accepts_dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _DetailClient()
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    get_dashboard_thread = AsyncMock(return_value={"id": "thread-1", "status": "finished"})
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value=None))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))

    result = await threads_tool.get_thread(
        "https://dev.open-swe.langchain.dev/agents/thread-1?workflowApproval=fp"
    )

    assert result["success"] is True
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )
    client.threads.get.assert_awaited_once_with("thread-1")
    client.threads.get_state.assert_awaited_once_with("thread-1")


async def test_get_thread_accepts_slack_reply_link(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _DetailClient()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    lookup = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(threads_tool, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    get_dashboard_thread = AsyncMock(return_value={"id": "thread-1", "status": "finished"})
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value=None))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))
    locator = (
        "https://workspace.slack.com/archives/C123/p1788431248678809"
        "?thread_ts=1788425314.774339&cid=C123"
    )

    result = await threads_tool.get_thread(locator)

    assert result["success"] is True
    assert result["slack"] == {
        "url": locator,
        "channel_id": "C123",
        "thread_ts": "1788425314.774339",
    }
    lookup.assert_awaited_once_with(client, "C123", "1788425314.774339")
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )
    client.threads.get.assert_awaited_once_with("thread-1")


async def test_list_threads_reports_unmapped_slack_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(threads_tool, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: object())
    get_dashboard_thread = AsyncMock()
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    result = await threads_tool.list_threads(
        query="https://workspace.slack.com/archives/C123/p1788431248678809"
    )

    assert result == {
        "success": False,
        "error": "Could not resolve Slack link to an Open SWE thread",
    }
    assert [call.args[1:] for call in lookup.await_args_list] == [
        ("C123", "1788431248.678809"),
        ("C123", "0"),
    ]
    get_dashboard_thread.assert_not_awaited()


async def test_get_thread_accepts_langsmith_run_id_when_not_a_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _DetailClient()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    locator = "11111111-1111-4111-8111-111111111111"
    resolver = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(threads_tool, "get_open_swe_thread_id_from_langsmith", resolver)
    get_dashboard_thread = AsyncMock(
        side_effect=[
            threads_tool.HTTPException(status_code=404, detail="Thread not found"),
            {"id": "thread-1", "status": "finished"},
        ]
    )
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value=None))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))

    result = await threads_tool.get_thread(locator)

    assert result["success"] is True
    resolver.assert_awaited_once_with(locator)
    assert get_dashboard_thread.await_args_list[1].args[0] == "thread-1"
    client.threads.get.assert_awaited_once_with("thread-1")


async def test_get_thread_accepts_langsmith_run_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _DetailClient()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    resolver = AsyncMock(return_value="thread-1")
    monkeypatch.setattr(threads_tool, "get_open_swe_thread_id_from_langsmith", resolver)
    get_dashboard_thread = AsyncMock(
        return_value={
            "id": "thread-1",
            "status": "finished",
            "traceUrl": "https://smith.langchain.com/o/org/projects/p/project/t/thread-1",
        }
    )
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: client)
    monkeypatch.setattr(threads_tool, "get_plan_content", AsyncMock(return_value=None))
    monkeypatch.setattr(threads_tool, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(threads_tool, "get_workflow_push_approvals", AsyncMock(return_value={}))

    locator = "https://smith.langchain.com/o/org/projects/p/project/r/run-1?poll=true"
    result = await threads_tool.get_thread(locator)

    assert result["success"] is True
    resolver.assert_awaited_once_with(locator)
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )
    client.threads.get.assert_awaited_once_with("thread-1")
    assert result["langsmith"] == {
        "trace_url": "https://smith.langchain.com/o/org/projects/p/project/t/thread-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
    }


async def test_list_threads_resolves_langsmith_thread_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    get_dashboard_thread = AsyncMock(
        return_value={
            "id": "thread-1",
            "title": "Fix race",
            "messages": [],
            "traceUrl": "https://smith.langchain.com/o/org/projects/p/project/t/thread-1",
        }
    )
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    locator = "https://smith.langchain.com/o/org/projects/p/project/t/thread-1"
    result = await threads_tool.list_threads(query=locator)

    assert result["items"][0]["id"] == "thread-1"
    assert result["items"][0]["langsmith"] == {
        "trace_url": "https://smith.langchain.com/o/org/projects/p/project/t/thread-1",
        "thread_id": "thread-1",
        "run_id": None,
    }
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )


@pytest.mark.parametrize(
    ("filters", "name"),
    [
        ({"participant": "alice"}, "participant"),
        ({"all_users": True}, "all_users"),
        ({"resolved": False}, "resolved"),
        ({"viewed": True}, "viewed"),
        ({"source": "slack"}, "source"),
        ({"status": "running"}, "status"),
        ({"scope": "automation"}, "scope"),
        ({"automation_id": "daily"}, "automation_id"),
        ({"admin_threads": False}, "admin_threads"),
    ],
)
async def test_list_threads_rejects_filters_with_exact_locator(
    monkeypatch: pytest.MonkeyPatch,
    filters: dict[str, object],
    name: str,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    get_dashboard_thread = AsyncMock()
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    result = await threads_tool.list_threads(
        query="https://dev.open-swe.langchain.dev/agents/thread-1", **filters
    )

    assert result == {
        "success": False,
        "error": f"Exact thread locators cannot be combined with filters: {name}",
    }
    get_dashboard_thread.assert_not_awaited()


async def test_list_threads_reports_all_incompatible_exact_locator_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))

    result = await threads_tool.list_threads(
        query="https://dev.open-swe.langchain.dev/agents/thread-1",
        participant="alice",
        resolved=True,
        scope="interactive",
    )

    assert result == {
        "success": False,
        "error": (
            "Exact thread locators cannot be combined with filters: participant, resolved, scope"
        ),
    }


async def test_list_threads_resolves_exact_dashboard_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dev.open-swe.langchain.dev")
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    get_dashboard_thread = AsyncMock(
        return_value={"id": "thread-1", "title": "Fix race", "messages": []}
    )
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    result = await threads_tool.list_threads(
        query="https://dev.open-swe.langchain.dev/agents/thread-1"
    )

    assert result["items"][0]["id"] == "thread-1"
    get_dashboard_thread.assert_awaited_once_with(
        "thread-1", "octocat", email="octocat@example.com", mark_viewed=False
    )


async def test_list_threads_normalizes_and_filters_github_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    page = AsyncMock(
        return_value={
            "items": [
                {
                    "id": "thread-1",
                    "title": "Fix race",
                    "pr": {"url": "https://github.com/Acme/Repo/pull/42"},
                },
                {
                    "id": "thread-2",
                    "title": "Other",
                    "pr": {"url": "https://github.com/acme/repo/pull/420"},
                },
            ],
            "limit": 25,
            "offset": 0,
            "hasMore": False,
        }
    )
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(
        query="https://github.com/acme/repo/pull/42/files?diff=split"
    )

    assert [item["id"] for item in result["items"]] == ["thread-1"]
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["query"] == "https://github.com/acme/repo/pull/42"


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


async def test_list_threads_slack_link_falls_back_to_code_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    lookup = AsyncMock(side_effect=[None, "code-thread"])
    monkeypatch.setattr(threads_tool, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(threads_tool, "langgraph_client", lambda: object())
    monkeypatch.setattr(
        threads_tool,
        "get_dashboard_thread",
        AsyncMock(return_value={"id": "code-thread", "messages": []}),
    )

    result = await threads_tool.list_threads(
        query="https://workspace.slack.com/archives/CODE1/p1788431248678809"
    )

    assert result["items"][0]["id"] == "code-thread"
    assert [call.args[1:] for call in lookup.await_args_list] == [
        ("CODE1", "1788431248.678809"),
        ("CODE1", "0"),
    ]


async def test_list_threads_uses_general_query_and_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    page = AsyncMock(
        return_value={
            "items": [{"id": "thread-2", "title": "Payments bug", "messages": []}],
            "limit": 10,
            "offset": 20,
            "hasMore": True,
        }
    )
    monkeypatch.setattr(threads_tool, "list_dashboard_threads_page", page)

    result = await threads_tool.list_threads(
        query="payments", all_users=True, limit=10, offset=20, scope="interactive"
    )

    assert result["items"][0]["id"] == "thread-2"
    assert result["has_more"] is True
    awaited = page.await_args
    assert awaited is not None
    assert awaited.kwargs["include_all"] is True
    assert awaited.kwargs["query"] == "payments"
    assert awaited.kwargs["scope"] == "interactive"


async def test_get_thread_rejects_pr_locator_before_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_dashboard_thread = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_dashboard_thread)

    result = await threads_tool.get_thread("https://github.com/acme/repo/pull/42")

    assert result == {
        "success": False,
        "error": (
            "thread_id must be an exact thread ID, Open SWE dashboard URL, Slack link, or LangSmith trace URL"
        ),
    }
    get_dashboard_thread.assert_not_awaited()


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


async def test_get_thread_reports_unavailable_cost_without_prepare_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await threads_tool._thread_cost("thread-1", {"status": "success", "metadata": {}}) == {
        "status": "unavailable",
        "total_usd": None,
    }


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
    state = {
        "messages": [
            {
                "type": "human",
                "content": (
                    '<input-message sender="github:reviewer" surface="web" kind="human">'
                    "<content>Cancel it</content></input-message>"
                ),
            }
        ]
    }

    result = await threads_tool.manage_thread("thread-1", "cancel", state=state)

    assert result == {"success": False, "error": "thread not found", "status_code": 404}
    cancel.assert_awaited_once_with("thread-1", "reviewer", email=None)


async def test_manage_thread_requires_delete_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    delete = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "delete_dashboard_thread", delete)

    result = await threads_tool.manage_thread("thread-1", "delete")

    assert result == {"success": False, "error": "delete requires confirm=true"}
    delete.assert_not_awaited()


async def test_manage_thread_rejects_contradictory_arguments_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "cancel_dashboard_thread", cancel)

    result = await threads_tool.manage_thread("thread-1", "cancel", comment="not applicable")

    assert result == {
        "success": False,
        "error": "Unexpected arguments for cancel: comment",
    }
    cancel.assert_not_awaited()


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


async def test_manage_thread_rejects_invalid_model_before_thread_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_thread = AsyncMock()
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    monkeypatch.setattr(threads_tool, "get_dashboard_thread", get_thread)

    result = await threads_tool.manage_thread(
        "thread-1",
        "send_message",
        message="Continue",
        model_id="unknown:model",
        effort="high",
    )

    assert result == {
        "success": False,
        "error": "model_id and effort are not a supported combination",
    }
    get_thread.assert_not_awaited()


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
    command = awaited.args[2]
    assert b'"method": "run.start"' in command
    assert b'"plan_mode": true' in command


async def test_manage_thread_update_plan_preserves_format_and_bounds_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    update = AsyncMock(return_value={"status": "ready", "html": "<html>new</html>"})
    monkeypatch.setattr(threads_tool.plan_api, "update_plan", update)

    result = await threads_tool.manage_thread(
        "thread-1",
        "update_plan",
        content="<html>new</html>",
        content_format="html",
    )

    assert result["success"] is True
    assert result["format"] == "html"
    assert result["content_length"] == 16
    assert "html" not in result
    update.assert_awaited_once()


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


async def test_manage_thread_delegates_plan_and_workflow_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threads_tool, "_actor", AsyncMock(return_value=_actor()))
    approve_plan = AsyncMock(return_value={"status": "approved", "run_id": "run-1"})
    approve_workflow = AsyncMock(return_value={"status": "approved", "fingerprint": "fp"})
    monkeypatch.setattr(threads_tool.plan_api, "approve_plan", approve_plan)
    monkeypatch.setattr(
        threads_tool.workflow_approval_api,
        "approve_workflow_push",
        approve_workflow,
    )

    plan_result = await threads_tool.manage_thread("thread-1", "approve_plan")
    workflow_result = await threads_tool.manage_thread(
        "thread-1", "approve_workflow_push", fingerprint="fp"
    )

    assert plan_result == {"success": True, "status": "approved", "run_id": "run-1"}
    assert workflow_result == {"success": True, "status": "approved", "fingerprint": "fp"}
    approve_plan.assert_awaited_once()
    approve_workflow.assert_awaited_once()
