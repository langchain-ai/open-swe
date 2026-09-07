"""scripts/create_github_app.py without GitHub: manifest, callback server, sinks."""

import json
import os
import stat
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest
from dotenv import dotenv_values

from scripts import create_github_app as script


def test_public_url_gets_webhook_and_events() -> None:
    manifest = script.build_manifest(
        url="https://swe.example.com/", redirect_url="http://127.0.0.1:1/callback", name="Open SWE"
    )
    assert manifest["hook_attributes"] == {
        "url": "https://swe.example.com/webhooks/github",
        "active": True,
    }
    assert manifest["default_events"] == list(script.EVENTS)
    assert manifest["callback_urls"] == ["https://swe.example.com/dashboard/api/auth/callback"]
    assert manifest["request_oauth_on_install"] is False
    assert manifest["default_permissions"]["contents"] == "write"


def test_local_url_omits_webhook_and_events() -> None:
    manifest = script.build_manifest(
        url="http://localhost:2024",
        redirect_url="http://127.0.0.1:1/callback",
        name="Open SWE",
        extra_callbacks=("https://swe.example.com/dashboard/api/auth/callback",),
    )
    assert "hook_attributes" not in manifest
    assert "default_events" not in manifest
    assert manifest["callback_urls"] == [
        "http://localhost:2024/dashboard/api/auth/callback",
        "https://swe.example.com/dashboard/api/auth/callback",
    ]


def test_manifest_target_for_user_and_org() -> None:
    assert script.manifest_target("s1") == "https://github.com/settings/apps/new?state=s1"
    assert script.manifest_target("s1", " my org ") == (
        "https://github.com/organizations/my%20org/settings/apps/new?state=s1"
    )


def test_credentials_generate_webhook_secret_when_github_has_none() -> None:
    values = script.credentials_from_conversion(
        {"id": 7, "client_id": "Iv1.x", "client_secret": "cs", "pem": "-----BEGIN RSA-----\nk\n"}
    )
    assert values["GITHUB_APP_ID"] == "7"
    assert values["GITHUB_APP_PRIVATE_KEY"].startswith("-----BEGIN")
    assert len(values["GITHUB_WEBHOOK_SECRET"]) == 64


def test_credentials_reject_incomplete_response() -> None:
    with pytest.raises(ValueError, match="GITHUB_APP_CLIENT_SECRET"):
        script.credentials_from_conversion({"id": 7, "client_id": "x", "pem": "k"})


def test_write_env_values_replaces_and_appends(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('OPENAI_API_KEY="keep"\nexport GITHUB_APP_ID=old\n')
    pem = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
    script.write_env_values(env, {"GITHUB_APP_ID": "123", "GITHUB_APP_PRIVATE_KEY": pem})
    loaded = dotenv_values(env)
    assert loaded == {
        "OPENAI_API_KEY": "keep",
        "GITHUB_APP_ID": "123",
        "GITHUB_APP_PRIVATE_KEY": pem,
    }
    assert env.read_text().count("GITHUB_APP_ID=") == 1


def test_write_env_values_creates_missing_file(tmp_path: Path) -> None:
    env = tmp_path / "sub" / ".env"
    env.parent.mkdir()
    script.write_env_values(env, {"GITHUB_APP_INSTALLATION_ID": "9"})
    assert dotenv_values(env) == {"GITHUB_APP_INSTALLATION_ID": "9"}


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_write_env_values_keeps_the_file_owner_only(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    env.chmod(0o644)
    script.write_env_values(env, {"GITHUB_WEBHOOK_SECRET": "s"})
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_merge_secrets_overrides_and_keeps_others() -> None:
    merged = script.merge_secrets(
        [{"name": "OPENAI_API_KEY", "value": "k"}, {"name": "GITHUB_APP_ID", "value": "old"}],
        {"GITHUB_APP_ID": "new", "GITHUB_WEBHOOK_SECRET": "w"},
    )
    assert merged == [
        {"name": "OPENAI_API_KEY", "value": "k"},
        {"name": "GITHUB_APP_ID", "value": "new"},
        {"name": "GITHUB_WEBHOOK_SECRET", "value": "w"},
    ]


@pytest.fixture
def server():
    srv = script._CallbackServer(
        ("127.0.0.1", 0), {"name": "Open SWE"}, "https://github.com/x", "nonce"
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()


def _get(srv: script._CallbackServer, path: str) -> tuple[int, str]:
    try:
        with urlopen(f"http://127.0.0.1:{srv.server_address[1]}{path}") as response:
            return response.status, response.read().decode()
    except Exception as exc:  # urllib raises on 4xx/5xx
        return exc.code, exc.read().decode()  # type: ignore[attr-defined]


def test_root_serves_form_posting_the_manifest_to_github(server) -> None:
    status, body = _get(server, "/")
    assert status == 200
    assert 'action="https://github.com/x"' in body
    assert 'name="manifest"' in body
    assert json.dumps({"name": "Open SWE"}).replace('"', "&quot;") in body


def test_callback_exchanges_code_when_state_matches(server) -> None:
    server.exchange = lambda code: {"id": 1, "code": code}
    status, body = _get(server, "/callback?code=abc&state=nonce")
    assert status == 200
    assert "created" in body
    assert server.conversion == {"id": 1, "code": "abc"}


def test_callback_rejects_wrong_state(server) -> None:
    server.exchange = lambda code: pytest.fail("must not exchange")
    status, _ = _get(server, "/callback?code=abc&state=other")
    assert status == 400
    assert server.conversion is None
    assert server.error


def test_callback_reports_exchange_failure(server) -> None:
    def boom(code: str):
        raise RuntimeError("github said no")

    server.exchange = boom
    status, body = _get(server, "/callback?code=abc&state=nonce")
    assert status == 502
    assert "github said no" in body
    assert server.error


def test_wait_for_installation_returns_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fetch(app_id: str, key: str):
        calls.append(1)
        return [] if len(calls) < 3 else [{"id": 42, "account": {"login": "acme"}}]

    monkeypatch.setattr(script.time, "sleep", lambda _s: None)
    assert script.wait_for_installation("1", "k", timeout=60, poll=0, fetch=fetch) == {
        "id": 42,
        "account": {"login": "acme"},
    }
    assert len(calls) == 3


def test_wait_for_installation_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 0.0, 100.0, 100.0])
    monkeypatch.setattr(script.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(script.time, "sleep", lambda _s: None)
    assert script.wait_for_installation("1", "k", timeout=10, poll=0, fetch=lambda *_: []) is None


def test_args_require_exactly_one_sink() -> None:
    with pytest.raises(SystemExit):
        script._parse_args(["--url", "https://x"])
    with pytest.raises(SystemExit):
        script._parse_args(["--url", "https://x", "--env-file", ".env", "--deployment", "d"])
    args = script._parse_args(["--url", "https://x", "--deployment", "d", "--org", "acme"])
    assert args.deployment == "d" and args.org == "acme"
