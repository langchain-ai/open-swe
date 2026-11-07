from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.slack import client as slack_client
from openswe.threads import handlers, listing, plan_api, summary, workflow_approval_api
from openswe.users import User

_ADMINS = {"admin"}


@pytest.fixture
def bot_thread(monkeypatch):
    thread = {
        "thread_id": "bot-thread",
        "status": "idle",
        "metadata": {
            "source": "slack",
            "visibility": "public",
            "owner_type": "system",
            "trigger_kind": "slack_bot",
            "triggering_bot": "T123:B123",
            "sandbox_id": "sbx",
            "source_context": {
                "slack_thread": {
                    "team_id": "T123",
                    "triggering_bot_id": "B123",
                    "triggering_user_name": "Release bot",
                }
            },
        },
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value=thread), update=AsyncMock()),
        runs=SimpleNamespace(cancel_many=AsyncMock(), list=AsyncMock(return_value=[])),
    )
    for module in (handlers, listing):
        monkeypatch.setattr(module, "langgraph_client", lambda: client)
    for module in (plan_api, workflow_approval_api):
        monkeypatch.setattr(
            module, "fetch_thread_metadata", AsyncMock(return_value=thread["metadata"])
        )
    monkeypatch.setattr(summary, "is_admin", lambda email, login=None: login in _ADMINS)
    return thread, client


def test_bot_thread_is_readable_and_still_promptable_from_slack(bot_thread):
    metadata = bot_thread[0]["metadata"]
    assert summary.thread_is_readable(metadata, "bob")
    # Slack and GitHub webhooks share this check; only the dashboard is read-only.
    assert summary.thread_is_promptable(metadata, "bob")
    with pytest.raises(HTTPException) as exc:
        summary._assert_thread_promptable(metadata, "bob")
    assert exc.value.status_code == 403
    assert exc.value.detail == summary.BOT_THREAD_READ_ONLY


@pytest.mark.parametrize("login", ["bob", "admin"])
async def test_dashboard_bot_thread_nonapproval_actions_remain_read_only(
    bot_thread, login, monkeypatch
):
    _, client = bot_thread
    session = {"sub": login}
    operations = [
        lambda: handlers.get_dashboard_terminal_sandbox("bot-thread", login),
        lambda: handlers.delete_dashboard_thread("bot-thread", login),
        lambda: plan_api.post_plan_comment(
            "bot-thread", plan_api.CommentBody(body="comment"), session
        ),
        lambda: plan_api.update_plan(
            "bot-thread", plan_api.PlanUpdate(html="<p>edit</p>"), session
        ),
    ]
    for operation in operations:
        with pytest.raises(HTTPException) as exc:
            await operation()
        assert exc.value.status_code == 403
    client.threads.update.assert_not_awaited()
    client.runs.cancel_many.assert_not_awaited()

    monkeypatch.setattr(handlers, "_thread_summary", AsyncMock(return_value={}))
    await handlers.cancel_dashboard_thread("bot-thread", login)
    assert client.threads.update.await_args.kwargs["metadata"]["latest_run_status"] == "interrupted"


async def test_summary_names_the_starting_bot(bot_thread, monkeypatch):
    monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
    result = await summary._thread_summary(bot_thread[0])
    assert result["triggerKind"] == "slack_bot"
    assert result["triggeringBot"] == {"key": "T123:B123", "name": "Release bot"}
    other = {**bot_thread[0], "metadata": {"source": "slack", "trigger_kind": "user"}}
    assert (await summary._thread_summary(other))["triggeringBot"] is None


async def test_bot_scope_lists_bot_threads_for_any_user(bot_thread, monkeypatch):
    thread, _ = bot_thread
    person = {"thread_id": "person-thread", "metadata": {"source": "slack"}}
    other_bot = {
        "thread_id": "other-bot-thread",
        "metadata": {**thread["metadata"], "triggering_bot": "T123:B999"},
    }
    searches: list[dict] = []

    async def search(_client, metadata, **_kwargs):
        searches.append(metadata)
        return [thread, person, other_bot]

    monkeypatch.setattr(listing, "_search_threads_batch", search)
    monkeypatch.setattr(
        listing,
        "_summarize_threads",
        AsyncMock(
            side_effect=lambda _client, threads, **_: [{"id": t["thread_id"]} for t in threads]
        ),
    )
    page = await listing.list_dashboard_threads_page("bob", scope="bot")
    assert searches == [{"trigger_kind": "slack_bot"}]
    assert {item["id"] for item in page["items"]} == {"bot-thread", "other-bot-thread"}

    searches.clear()
    page = await listing.list_dashboard_threads_page("bob", scope="bot", bot="T123:B123")
    assert searches == [{"trigger_kind": "slack_bot", "triggering_bot": "T123:B123"}]
    assert [item["id"] for item in page["items"]] == ["bot-thread"]


@pytest.mark.parametrize("login", ["bob", "admin"])
def test_nobody_posts_in_a_memory_curators_thread(monkeypatch, login):
    monkeypatch.setattr(summary, "is_admin", lambda email, login=None: login in _ADMINS)
    metadata = {
        "source": "dashboard",
        "visibility": "public",
        "owner_type": "system",
        "curator": "platform",
    }
    with pytest.raises(HTTPException) as exc:
        summary.assert_thread_postable(metadata, login)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize(
    "identity",
    [
        "participant",
        "unlinked",
        "outsider",
        "bot",
        "requester",
        "other-requester",
        "absent-requester",
    ],
)
async def test_bot_workflow_decisions_require_linked_human_participant(
    bot_thread, monkeypatch, approved, identity
):
    metadata = bot_thread[0]["metadata"]
    slack_thread = metadata["source_context"]["slack_thread"]
    slack_thread.update(channel_id="C123", thread_ts="1")
    if identity in {"requester", "other-requester", "absent-requester"}:
        slack_thread["triggering_user_id"] = "U123"
    person = SimpleNamespace(
        id="person", github_login="bob", slack_user_id="U123", slack_team_id="T123"
    )
    actor = (
        SimpleNamespace(id="other", github_login="eve", slack_user_id="U999", slack_team_id="T123")
        if identity in {"other-requester", "absent-requester"}
        else person
    )
    monkeypatch.setattr(
        User, "for_login", AsyncMock(return_value=None if identity == "unlinked" else actor)
    )
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=person))
    messages = [{"user": "U123"}, {"user": "U999"}] if identity != "outsider" else []
    if identity == "absent-requester":
        messages = [{"user": "U999"}]
    if identity == "bot":
        messages = [{"user": "U123", "bot_id": "B123"}]
    monkeypatch.setattr(
        slack_client, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )
    decide = AsyncMock(return_value=({"status": "approved" if approved else "rejected"}, True))
    dispatch = AsyncMock()
    monkeypatch.setattr(workflow_approval_api, "decide_workflow_push_approval", decide)
    monkeypatch.setattr(workflow_approval_api, "dispatch_followup", dispatch)
    operation = (
        workflow_approval_api.approve_workflow_push
        if approved
        else workflow_approval_api.reject_workflow_push
    )
    if identity not in {"participant", "requester"}:
        with pytest.raises(HTTPException) as exc:
            await operation("bot-thread", "fp", {"sub": "bob"})
        assert exc.value.status_code == 403
        decide.assert_not_awaited()
        dispatch.assert_not_awaited()
        return
    result = await operation("bot-thread", "fp", {"sub": "bob"})
    assert result["status"] == ("approved" if approved else "rejected")
    decide.assert_awaited_once_with("bot-thread", "fp", approved=approved, actor="bob")
    assert dispatch.await_count == int(approved)
