from __future__ import annotations

from fastapi.testclient import TestClient

from agent import webapp
from agent.utils.tool_policy import filter_disabled_tools, get_tool_name, is_webhook_disabled


class NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


def sample_callable() -> None:
    pass


def test_filter_disabled_tools_uses_names_and_callables(monkeypatch) -> None:
    monkeypatch.setenv("DISABLED_AGENT_TOOLS", "http_request, sample_callable")

    tools = [NamedTool("http_request"), NamedTool("publish_review"), sample_callable]

    assert [get_tool_name(tool) for tool in filter_disabled_tools(tools)] == ["publish_review"]


def test_filter_disabled_tools_is_noop_without_policy(monkeypatch) -> None:
    monkeypatch.delenv("DISABLED_AGENT_TOOLS", raising=False)
    tools = [NamedTool("http_request"), sample_callable]

    assert filter_disabled_tools(tools) == tools


def test_webhook_policy_is_dynamic(monkeypatch) -> None:
    monkeypatch.setenv("DISABLED_WEBHOOKS", "slack, linear")

    assert is_webhook_disabled("slack") is True
    assert is_webhook_disabled("linear") is True
    assert is_webhook_disabled("github") is False


def test_disabled_slack_webhook_returns_before_signature_check(monkeypatch) -> None:
    monkeypatch.setenv("DISABLED_WEBHOOKS", "slack")
    client = TestClient(webapp.app)

    response = client.post("/webhooks/slack", content=b"not signed")

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "Slack webhook disabled by profile"}


def test_disabled_linear_webhook_returns_before_signature_check(monkeypatch) -> None:
    monkeypatch.setenv("DISABLED_WEBHOOKS", "linear")
    client = TestClient(webapp.app)

    response = client.post("/webhooks/linear", content=b"not signed")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "Linear webhook disabled by profile",
    }
