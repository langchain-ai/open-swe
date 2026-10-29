import pytest

from openswe import tools
from openswe.run_config import RunConfig
from tests.support.slack_api import SlackAPI


@pytest.fixture
def search_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig.parse({"source": "slack", "slack_action_token": "event-capability"}),
    )


async def test_search_preserves_sources_context_and_pagination(
    slack_api: SlackAPI, search_run: None
) -> None:
    message = {
        "channel_id": "C123",
        "message_ts": "123.456",
        "thread_ts": "123.000",
        "content": "Grouping discussion",
        "permalink": "https://example.slack.com/archives/C123/p123456",
        "context_messages": {"after": [{"text": "Latest decision", "ts": "124.000"}]},
        "unexpected_sensitive_field": "not exposed",
    }
    slack_api.respond(
        {"ok": True, "results": {"messages": []}, "response_metadata": {"next_cursor": "more"}}
    )
    slack_api.respond({"ok": True, "results": {"messages": [message]}})
    first = await tools.slack_search_messages("grouping")
    assert first == {"success": True, "messages": [], "next_cursor": "more"}
    second = await tools.slack_search_messages("grouping", cursor="more")
    assert second["success"] is True
    assert second["next_cursor"] == ""
    assert "Latest decision" in str(second["messages"])
    assert message["permalink"] in str(second["messages"])
    assert "123.000" in str(second["messages"])
    assert "not exposed" not in str(second)
    assert "event-capability" not in str(second)
    method, params = slack_api.calls[1]
    assert method == "assistant.search.context"
    assert params["action_token"] == "event-capability"
    assert params["channel_types"] == ["public_channel"]
    assert params["content_types"] == ["messages"]
    assert params["cursor"] == "more"


@pytest.mark.parametrize("source,token", [("slack", ""), ("web", "stale-token")])
async def test_search_requires_current_slack_authorization(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, source: str, token: str
) -> None:
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig.parse({"source": source, "slack_action_token": token}),
    )
    assert (await tools.slack_search_messages("grouping"))["success"] is False
    assert slack_api.calls == []


@pytest.mark.parametrize("error", ["missing_scope", "invalid_action_token"])
async def test_search_reports_authorization_failures(
    slack_api: SlackAPI, search_run: None, error: str
) -> None:
    slack_api.respond({"ok": False, "error": error})
    assert await tools.slack_search_messages("grouping") == {"success": False, "error": error}


async def test_search_reports_rate_limit(slack_api: SlackAPI, search_run: None) -> None:
    slack_api.respond(
        {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "30"}
    )
    assert await tools.slack_search_messages("grouping") == {
        "success": False,
        "error": "rate_limited: 30",
    }


@pytest.mark.parametrize("payload", [{"messages": []}, {"results": {"messages": [{}]}}])
async def test_search_rejects_malformed_results(
    slack_api: SlackAPI, search_run: None, payload: dict[str, object]
) -> None:
    slack_api.respond({"ok": True, **payload})
    assert await tools.slack_search_messages("grouping") == {
        "success": False,
        "error": "invalid_slack_response",
    }
