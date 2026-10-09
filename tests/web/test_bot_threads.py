from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.threads import handlers, listing, plan_api, summary, workflow_approval_api

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
    # Slack and GitHub webhooks share this check; only the web app is read-only.
    assert summary.thread_is_promptable(metadata, "bob")
    with pytest.raises(HTTPException) as exc:
        summary._assert_thread_promptable(metadata, "bob")
    assert exc.value.status_code == 403
    assert exc.value.detail == summary.BOT_THREAD_READ_ONLY


@pytest.mark.parametrize("login", ["bob", "admin"])
async def test_web_cannot_act_on_a_bot_thread(bot_thread, login):
    _, client = bot_thread
    session = {"sub": login}
    operations = [
        lambda: handlers.get_web_terminal_sandbox("bot-thread", login),
        lambda: handlers.cancel_web_thread("bot-thread", login),
        lambda: handlers.delete_web_thread("bot-thread", login),
        lambda: plan_api.post_plan_comment(
            "bot-thread", plan_api.CommentBody(body="comment"), session
        ),
        lambda: plan_api.update_plan(
            "bot-thread", plan_api.PlanUpdate(html="<p>edit</p>"), session
        ),
        lambda: workflow_approval_api.approve_workflow_push("bot-thread", "fp", session),
        lambda: workflow_approval_api.reject_workflow_push("bot-thread", "fp", session),
    ]
    for operation in operations:
        with pytest.raises(HTTPException) as exc:
            await operation()
        assert exc.value.status_code == 403
    client.threads.update.assert_not_awaited()
    client.runs.cancel_many.assert_not_awaited()


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
    page = await listing.list_web_threads_page("bob", scope="bot")
    assert searches == [{"trigger_kind": "slack_bot"}]
    assert {item["id"] for item in page["items"]} == {"bot-thread", "other-bot-thread"}

    searches.clear()
    page = await listing.list_web_threads_page("bob", scope="bot", bot="T123:B123")
    assert searches == [{"trigger_kind": "slack_bot", "triggering_bot": "T123:B123"}]
    assert [item["id"] for item in page["items"]] == ["bot-thread"]
