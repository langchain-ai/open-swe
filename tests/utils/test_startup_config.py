"""Startup configuration report and deprecation warnings."""

import logging

import pytest

from agent.utils import startup_config

_MINIMUM = {
    "LANGSMITH_API_KEY_PROD": "lsv2-secret",
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
    assert "Linear: disabled" in lines
    assert "Dashboard: disabled" in lines


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
        ("LANGSMITH_TRACING_PROJECT_ID_PROD", "by name"),
        ("LANGCHAIN_PROJECT", "no effect"),
        ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY_PROD"),
        ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"),
    ],
)
def test_deprecated_variable_warns(name: str, fragment: str) -> None:
    warnings = startup_config.deprecated_env_warnings({name: "x"})

    assert len(warnings) == 1
    assert warnings[0].startswith(f"{name} is deprecated")
    assert fragment in warnings[0]


def test_no_deprecations_for_minimum_config() -> None:
    assert startup_config.deprecated_env_warnings(_MINIMUM) == []


def test_log_startup_configuration_uses_levels(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in startup_config.__dict__["_SURFACES"]:
        for var in name[1]:
            monkeypatch.delenv(var, raising=False)
    for name, _ in startup_config.__dict__["_DEPRECATIONS"]:
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
