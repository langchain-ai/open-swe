"""The Slack bot token the backend reads from Agent Auth, where MDA's Slack channel keeps it."""

import logging
from collections.abc import Iterator

import pytest
from managed_deepagents._agent_auth import AgentAuthContext
from managed_deepagents._agent_auth_http import CredentialStoreError

from openswe.slack import http
from openswe.threads import runtimes
from openswe.utils import ttl_cache

DEPLOYMENT_ID = "11111111-1111-4111-8111-111111111111"
WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"


class FakeAgentAuth:
    """Agent Auth holding one deployment's bot token."""

    secret: str | None = "xoxb-from-agent-auth"
    failure: Exception | None = None
    contexts: list[AgentAuthContext] = []
    slugs: list[str] = []

    def __init__(self, context: AgentAuthContext) -> None:
        self.context = context
        self.contexts.append(context)

    def find_workspace_credential_id(self, slug: str) -> str | None:
        self.slugs.append(slug)
        if self.failure is not None:
            raise self.failure
        return "credential-1" if self.secret else None

    def read_secret(self, credential_id: str) -> str | None:
        assert credential_id == "credential-1"
        return self.secret


@pytest.fixture(autouse=True)
def agent_auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeAgentAuth]]:
    ttl_cache.clear()
    monkeypatch.setattr(FakeAgentAuth, "secret", "xoxb-from-agent-auth")
    monkeypatch.setattr(FakeAgentAuth, "failure", None)
    monkeypatch.setattr(FakeAgentAuth, "contexts", [])
    monkeypatch.setattr(FakeAgentAuth, "slugs", [])
    monkeypatch.setattr(runtimes, "AgentAuth", FakeAgentAuth)
    monkeypatch.setenv("MDA_RUNTIME_URL", "http://127.0.0.1:2025")
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-runtime-secret")
    monkeypatch.setenv("MDA_DEPLOYMENT_ID", DEPLOYMENT_ID)
    monkeypatch.setenv("MDA_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.setenv("MDA_RUNTIME_API_KEY", "lsv2_test")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    yield FakeAgentAuth
    ttl_cache.clear()


async def test_reads_the_mda_deployments_token_from_agent_auth(
    agent_auth: type[FakeAgentAuth],
) -> None:
    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"

    (context,) = agent_auth.contexts
    assert context.agent_id == DEPLOYMENT_ID
    assert context.workspace_id == WORKSPACE_ID
    assert context.api_key == "lsv2_test"
    assert agent_auth.slugs == ["mda/slack-bot-token"]


async def test_agent_auth_wins_over_a_token_left_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-revoked")

    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"


async def test_keeps_the_token_between_calls_and_through_a_failed_refresh(
    monkeypatch: pytest.MonkeyPatch, agent_auth: type[FakeAgentAuth]
) -> None:
    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"
    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"
    assert len(agent_auth.contexts) == 1

    ttl_cache.set_cached("slack-bot-token", "xoxb-from-agent-auth", -1)
    monkeypatch.setattr(agent_auth, "failure", CredentialStoreError("unavailable", 503))
    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"


async def test_a_rotated_token_is_read_once_the_kept_one_expires(
    monkeypatch: pytest.MonkeyPatch, agent_auth: type[FakeAgentAuth]
) -> None:
    assert await http.SlackBotToken.read() == "xoxb-from-agent-auth"

    monkeypatch.setattr(agent_auth, "secret", "xoxb-rotated")
    ttl_cache.set_cached("slack-bot-token", "xoxb-from-agent-auth", -1)
    assert await http.SlackBotToken.read() == "xoxb-rotated"


async def test_uses_the_environments_token_while_agent_auth_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: type[FakeAgentAuth],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-environment")
    monkeypatch.setattr(agent_auth, "failure", CredentialStoreError("forbidden", 403))

    with caplog.at_level(logging.WARNING, logger=http.logger.name):
        assert await http.SlackBotToken.read() == "xoxb-environment"
        assert await http.SlackBotToken.read() == "xoxb-environment"

    warnings = [r for r in caplog.records if "using SLACK_BOT_TOKEN" in r.getMessage()]
    assert len(warnings) == 1
    assert "xoxb-environment" not in caplog.text


@pytest.mark.parametrize("failure", [None, CredentialStoreError("forbidden", 403)])
async def test_fails_without_a_token_anywhere(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: type[FakeAgentAuth],
    failure: Exception | None,
) -> None:
    monkeypatch.setattr(agent_auth, "secret", None)
    monkeypatch.setattr(agent_auth, "failure", failure)

    with pytest.raises((http.SlackConfigurationError, CredentialStoreError)):
        await http.SlackBotToken.read()


async def test_reads_the_environment_without_a_deployment_id(
    monkeypatch: pytest.MonkeyPatch, agent_auth: type[FakeAgentAuth]
) -> None:
    monkeypatch.delenv("MDA_DEPLOYMENT_ID")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-environment")

    assert await http.SlackBotToken.read() == "xoxb-environment"
    assert agent_auth.contexts == []
