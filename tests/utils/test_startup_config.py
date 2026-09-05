"""Startup configuration report and deprecation warnings."""

import logging

import pytest

from agent.config import ENV
from agent.utils import startup_config

_MINIMUM = {
    "LANGSMITH_API_KEY": "lsv2-secret",
    "GITHUB_APP_CLIENT_ID": "Iv1.client",
    "GITHUB_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\\nsecret",
    "GITHUB_WEBHOOK_SECRET": "webhook-secret",
    "SLACK_BOT_TOKEN": "xoxb-secret",
    "SLACK_SIGNING_SECRET": "signing-secret",
}


def test_minimum_config_enables_github_and_slack_only() -> None:
    lines = startup_config.configuration_summary(_MINIMUM)

    assert "LangSmith: enabled" in lines
    assert "GitHub: enabled" in lines
    assert "Slack: enabled" in lines
    assert "Slack sign-in: disabled" in lines
    assert "Linear: disabled" in lines
    assert "Dashboard: disabled" in lines


def test_slack_sign_in_is_its_own_surface() -> None:
    env = {**_MINIMUM, "SLACK_CLIENT_ID": "123.456", "SLACK_CLIENT_SECRET": "client-secret"}

    lines = startup_config.configuration_summary(env)

    assert "Slack sign-in: enabled" in lines
    assert "client-secret" not in " ".join(lines)


def test_partial_surface_lists_missing_names() -> None:
    env = dict(_MINIMUM)
    del env["GITHUB_WEBHOOK_SECRET"]

    assert "GitHub: missing GITHUB_WEBHOOK_SECRET" in startup_config.configuration_summary(env)


def test_blank_values_count_as_unset() -> None:
    env = dict(_MINIMUM, SLACK_SIGNING_SECRET="   ")

    assert "Slack: missing SLACK_SIGNING_SECRET" in startup_config.configuration_summary(env)


def test_explicit_overrides_are_listed_by_name() -> None:
    env = dict(_MINIMUM, GITHUB_APP_INSTALLATION_ID="123", SLACK_BOT_USER_ID="UBOT")

    assert (
        "Explicit overrides: GITHUB_APP_INSTALLATION_ID, SLACK_BOT_USER_ID"
        in startup_config.configuration_summary(env)
    )


def test_summary_never_contains_secret_values() -> None:
    env = dict(_MINIMUM, GITHUB_APP_INSTALLATION_ID="123")
    text = "\n".join(startup_config.configuration_summary(env))

    for value in env.values():
        assert value not in text


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("GITHUB_APP_ID", "GITHUB_APP_CLIENT_ID"),
        ("SLACK_REPO_OWNER", "Team settings"),
        ("SLACK_REPO_NAME", "Team settings"),
        ("DEFAULT_REPO_OWNER", "Team settings"),
        ("DEFAULT_REPO_NAME", "Team settings"),
        ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"),
        ("LANGSMITH_API_KEY_PROD", "LANGSMITH_API_KEY"),
        ("LANGSMITH_ENDPOINT_PROD", "LANGSMITH_ENDPOINT"),
        ("LANGSMITH_URL_PROD", "LANGSMITH_ENDPOINT"),
        ("LANGSMITH_TENANT_ID_PROD", "LANGSMITH_TENANT_ID"),
        ("LANGGRAPH_URL_PROD", "LANGGRAPH_URL"),
        ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"),
    ],
)
def test_deprecated_variable_warns(name: str, fragment: str) -> None:
    warnings = startup_config.deprecated_env_warnings({name: "x"})

    assert len(warnings) == 1
    assert warnings[0].startswith(f"{name} is deprecated")
    assert fragment in warnings[0]


def test_legacy_prod_key_still_enables_langsmith_with_a_warning() -> None:
    env = dict(_MINIMUM)
    env["LANGSMITH_API_KEY_PROD"] = env.pop("LANGSMITH_API_KEY")

    assert "LangSmith: enabled" in startup_config.configuration_summary(env)
    assert any(
        w.startswith("LANGSMITH_API_KEY_PROD is deprecated")
        for w in startup_config.deprecated_env_warnings(env)
    )


def test_no_deprecations_for_minimum_config() -> None:
    assert startup_config.deprecated_env_warnings(_MINIMUM) == []


def test_log_startup_configuration_uses_levels(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in startup_config.__dict__["_SURFACES"]:
        for var in name[1]:
            monkeypatch.delenv(var, raising=False)
    for var in ENV.variables():
        if var.deprecated or var.aliases:
            for name in (var.name, *var.aliases):
                monkeypatch.delenv(name, raising=False)
    for name, value in _MINIMUM.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GITHUB_APP_ID", "12345")

    with caplog.at_level(logging.INFO, logger="agent.utils.startup_config"):
        startup_config.log_startup_configuration()

    infos = [r.message for r in caplog.records if r.levelno == logging.INFO]
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert "config: GitHub: enabled" in infos
    assert any("GITHUB_APP_ID is deprecated" in w for w in warnings)
    assert all("xoxb-secret" not in r.message for r in caplog.records)
