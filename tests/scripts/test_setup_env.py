"""Guided installer: secret generation, dotenv merging, prompts, and file mode."""

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from scripts import setup_env

_FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\nabc==\n-----END RSA PRIVATE KEY-----\n"
)


def test_generated_secrets_are_distinct_and_well_formed() -> None:
    jwt_a, jwt_b = (
        setup_env.generate_dashboard_jwt_secret(),
        setup_env.generate_dashboard_jwt_secret(),
    )
    key_a, key_b = (
        setup_env.generate_token_encryption_key(),
        setup_env.generate_token_encryption_key(),
    )

    assert jwt_a != jwt_b and key_a != key_b
    assert len(jwt_a) == 64 and int(jwt_a, 16) >= 0
    Fernet(key_a.encode())
    assert jwt_a != key_a


def test_generated_secrets_respect_existing_unless_forced() -> None:
    existing = {"DASHBOARD_JWT_SECRET": "keep", "TOKEN_ENCRYPTION_KEY": ""}

    fresh = setup_env.generated_secrets(existing, force=False)
    assert set(fresh) == {"TOKEN_ENCRYPTION_KEY"}

    forced = setup_env.generated_secrets(existing, force=True)
    assert set(forced) == {"DASHBOARD_JWT_SECRET", "TOKEN_ENCRYPTION_KEY"}
    assert forced["DASHBOARD_JWT_SECRET"] != "keep"


def test_parse_env_handles_quotes_comments_and_export() -> None:
    text = (
        "# comment\n"
        "FOO=bar\n"
        'QUOTED="a b"\n'
        "SINGLE='x'\n"
        "export EXPORTED=1\n"
        'MULTI="line1\\nline2"\n'
        'ESCAPED="back\\\\slash \\"q\\""\n'
        "\n"
        "TRAILING=value # note\n"
    )

    assert setup_env.parse_env(text) == {
        "FOO": "bar",
        "QUOTED": "a b",
        "SINGLE": "x",
        "EXPORTED": "1",
        "MULTI": "line1\nline2",
        "ESCAPED": 'back\\slash "q"',
        "TRAILING": "value",
    }


def test_render_env_replaces_in_place_and_appends_once() -> None:
    existing = '# LangSmith\nLANGSMITH_API_KEY=""\nFOO=bar\n'
    updates = {"LANGSMITH_API_KEY": "lsv2-x", "SLACK_BOT_TOKEN": "xoxb-x"}

    first = setup_env.render_env(existing, updates)
    second = setup_env.render_env(first, updates)

    assert first.splitlines()[0] == "# LangSmith"
    assert 'LANGSMITH_API_KEY="lsv2-x"' in first
    assert "FOO=bar" in first
    assert first.count("SLACK_BOT_TOKEN=") == 1
    assert first.count(setup_env.MARKER) == 1
    assert second == first
    assert setup_env.parse_env(first)["SLACK_BOT_TOKEN"] == "xoxb-x"


def test_render_env_round_trips_private_key() -> None:
    key = "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"
    rendered = setup_env.render_env("", {"GITHUB_APP_PRIVATE_KEY": key})

    assert setup_env.parse_env(rendered)["GITHUB_APP_PRIVATE_KEY"] == key


def test_read_private_key_flattens_pem(tmp_path: Path) -> None:
    pem = tmp_path / "app.pem"
    pem.write_text(_FAKE_PEM)

    value = setup_env.read_private_key(str(pem))

    assert "\n" not in value
    assert value.startswith("-----BEGIN RSA PRIVATE KEY-----\\n")
    assert value.endswith("\\n-----END RSA PRIVATE KEY-----")


def test_read_private_key_rejects_non_pem(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("hello")

    with pytest.raises(setup_env.SetupError):
        setup_env.read_private_key(str(other))
    with pytest.raises(setup_env.SetupError):
        setup_env.read_private_key(str(tmp_path / "missing.pem"))


def _scripted(answers: dict[str, str], secrets_: dict[str, str]):
    asked: list[str] = []

    def ask(prompt: str, default: str) -> str:
        asked.append(prompt)
        return answers.get(prompt, default)

    def ask_secret(prompt: str) -> str:
        asked.append(prompt)
        return secrets_.get(prompt, "")

    return ask, ask_secret, asked


def test_collect_answers_gateway_skips_model_key(tmp_path: Path) -> None:
    pem = tmp_path / "app.pem"
    pem.write_text(_FAKE_PEM)
    ask, ask_secret, asked = _scripted(
        {
            "Model provider (anthropic / openai / google / gateway)": "gateway",
            "GitHub App client ID (Iv1....)": "Iv1.abc",
            "Path to the GitHub App private key (.pem)": str(pem),
        },
        {
            "LangSmith API key (lsv2_...)": "lsv2-test",
            "Slack bot token (xoxb-...)": "xoxb-test",
            "Slack signing secret": "sig-test",
        },
    )

    answers = setup_env.collect_answers(ask, ask_secret, existing={}, dashboard=False)

    assert answers["LANGSMITH_GATEWAY_ENABLED"] == "true"
    assert "ANTHROPIC_API_KEY" not in answers
    assert answers["GITHUB_APP_CLIENT_ID"] == "Iv1.abc"
    assert answers["GITHUB_APP_PRIVATE_KEY"].startswith("-----BEGIN")
    assert len(answers["GITHUB_WEBHOOK_SECRET"]) == 64
    assert answers["_generated_webhook_secret"] == "1"
    assert not any("Dashboard" in prompt for prompt in asked)
    assert setup_env.missing_minimum(answers) == []


def test_collect_answers_skips_values_already_present(tmp_path: Path) -> None:
    ask, ask_secret, asked = _scripted({}, {})
    existing = {
        "LANGSMITH_API_KEY": "x",
        "ANTHROPIC_API_KEY": "x",
        "GITHUB_APP_CLIENT_ID": "x",
        "GITHUB_APP_PRIVATE_KEY": "x",
        "GITHUB_WEBHOOK_SECRET": "x",
        "SLACK_BOT_TOKEN": "x",
        "SLACK_SIGNING_SECRET": "x",
    }

    assert setup_env.collect_answers(ask, ask_secret, existing=existing, dashboard=False) == {}
    assert asked == []


def test_collect_answers_dashboard_section_uses_defaults() -> None:
    ask, ask_secret, _ = _scripted({}, {"GitHub App client secret": "gh-secret"})
    existing = dict.fromkeys(setup_env.MINIMUM_KEYS, "x") | {"OPENAI_API_KEY": "x"}

    answers = setup_env.collect_answers(ask, ask_secret, existing=existing, dashboard=True)

    assert answers["GITHUB_APP_CLIENT_SECRET"] == "gh-secret"
    assert answers["DASHBOARD_BASE_URL"] == setup_env.DEFAULT_DASHBOARD_BASE_URL
    assert answers["DASHBOARD_API_BASE_URL"] == setup_env.DEFAULT_DASHBOARD_API_BASE_URL
    assert "CONFIGURED_ADMINS" not in answers


def test_missing_minimum_reports_names_only() -> None:
    missing = setup_env.missing_minimum({"SLACK_BOT_TOKEN": "xoxb-secret"})

    assert "SLACK_BOT_TOKEN" not in missing
    assert "SLACK_SIGNING_SECRET" in missing
    assert any("model provider" in item for item in missing)
    assert "xoxb-secret" not in " ".join(missing)


def test_main_non_interactive_writes_secure_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in (*setup_env.MINIMUM_KEYS, *setup_env.DASHBOARD_KEYS, *setup_env.GENERATED_KEYS):
        monkeypatch.delenv(key, raising=False)
    values = {
        "LANGSMITH_API_KEY": "lsv2-secret",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "GITHUB_APP_CLIENT_ID": "Iv1.abc",
        "GITHUB_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----",
        "GITHUB_WEBHOOK_SECRET": "hook-secret",
        "SLACK_BOT_TOKEN": "xoxb-secret",
        "SLACK_SIGNING_SECRET": "sig-secret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / ".env"

    assert setup_env.main(["--output", str(output), "--non-interactive"]) == 0

    mode = stat.S_IMODE(os.stat(output).st_mode)
    assert mode == 0o600
    written = setup_env.parse_env(output.read_text())
    for key, value in values.items():
        assert written[key] == value
    assert written["DASHBOARD_JWT_SECRET"] != written["TOKEN_ENCRYPTION_KEY"]
    Fernet(written["TOKEN_ENCRYPTION_KEY"].encode())
    out = capsys.readouterr().out
    for secret in ("lsv2-secret", "sk-ant-secret", "xoxb-secret", "sig-secret", "hook-secret"):
        assert secret not in out
    assert written["DASHBOARD_JWT_SECRET"] not in out


def test_main_second_run_keeps_generated_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (*setup_env.MINIMUM_KEYS, *setup_env.GENERATED_KEYS):
        monkeypatch.delenv(key, raising=False)
    for key in setup_env.MINIMUM_KEYS:
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    output = tmp_path / ".env"

    setup_env.main(["--output", str(output), "--non-interactive"])
    first = setup_env.parse_env(output.read_text())
    setup_env.main(["--output", str(output), "--non-interactive"])
    second = setup_env.parse_env(output.read_text())
    setup_env.main(["--output", str(output), "--non-interactive", "--force"])
    third = setup_env.parse_env(output.read_text())

    assert first["DASHBOARD_JWT_SECRET"] == second["DASHBOARD_JWT_SECRET"]
    assert first["TOKEN_ENCRYPTION_KEY"] == second["TOKEN_ENCRYPTION_KEY"]
    assert third["DASHBOARD_JWT_SECRET"] != first["DASHBOARD_JWT_SECRET"]


def test_main_non_interactive_generates_webhook_secret_without_printing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in (*setup_env.MINIMUM_KEYS, *setup_env.GENERATED_KEYS):
        monkeypatch.delenv(key, raising=False)
    for key in setup_env.MINIMUM_KEYS:
        if key != "GITHUB_WEBHOOK_SECRET":
            monkeypatch.setenv(key, "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    output = tmp_path / ".env"

    assert setup_env.main(["--output", str(output), "--non-interactive"]) == 0

    written = setup_env.parse_env(output.read_text())
    out = capsys.readouterr().out
    assert len(written["GITHUB_WEBHOOK_SECRET"]) == 64
    assert written["GITHUB_WEBHOOK_SECRET"] not in out
    assert "GITHUB_WEBHOOK_SECRET was generated" in out
    assert "grep '^GITHUB_WEBHOOK_SECRET='" in out


def test_main_non_interactive_fails_when_minimum_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in (*setup_env.MINIMUM_KEYS, *setup_env.GENERATED_KEYS, "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")

    assert setup_env.main(["--output", str(tmp_path / ".env"), "--non-interactive"]) == 2

    err = capsys.readouterr().err
    assert "SLACK_SIGNING_SECRET" in err
    assert "xoxb-secret" not in err
