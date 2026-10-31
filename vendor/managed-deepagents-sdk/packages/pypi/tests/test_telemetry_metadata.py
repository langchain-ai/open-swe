"""One verified auth lookup per process; caller identity stays per run."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from managed_deepagents._telemetry import metadata

if TYPE_CHECKING:
    from managed_deepagents.identity import RuntimeIdentity

ENV = {
    "LANGSMITH_AUTH_ENDPOINT": "https://dev.api.smith.langchain.com",
    "LANGSMITH_API_KEY": "test-key",
    "LANGSMITH_TENANT_ID": "workspace",
    "LANGSMITH_WORKSPACE_ID": "ignored",
    "LANGSMITH_HOST_PROJECT_ID": "deployment",
    "LANGSMITH_HOST_PROJECT_NAME": "agent",
}


@pytest.fixture
def auth_request(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(metadata, "_auth_resolved", False)
    monkeypatch.setattr(metadata, "_auth_metadata", None)
    mock = Mock(
        return_value=(
            200,
            {
                "tenant_id": "workspace",
                "organization_id": "org",
                "ls_user_id": "deployer",
                "user_email": "excluded",
            },
        )
    )
    monkeypatch.setattr(metadata, "request_agent_auth_json", mock)
    return mock


def test_concurrent_runs_share_auth_not_callers(auth_request: Mock) -> None:
    def slow_response(*args: object, **kwargs: object) -> object:
        time.sleep(0.01)
        return auth_request.return_value

    auth_request.side_effect = slow_response

    def detect(principal: str) -> metadata.RunIdentity:
        caller: RuntimeIdentity = {
            "user": {"kind": "person", "id": "provider-subject"},
            "source": {"provider": "slack"},
            "agent_auth_principal_id": principal,
        }
        return metadata.detect_run_identity(env=ENV, caller=caller)

    with ThreadPoolExecutor(max_workers=4) as executor:
        events = list(executor.map(detect, ["first", "second"]))
    assert [event["principal_id"] for event in events] == ["first", "second"]
    assert events[0]["workspace_id"] == events[0]["tenant_id"] == "workspace"
    assert events[0]["org_id"] == "org"
    assert events[0]["deployment_id"] == "deployment"
    assert events[0]["deployment_name"] == "agent"
    for excluded in ("deployer", "excluded", "provider-subject", "test-key"):
        assert excluded not in str(events)
    assert metadata.detect_run_identity(env=ENV)["principal_id"] is None
    assert auth_request.call_count == 1


@pytest.mark.parametrize(
    "key", ["LANGSMITH_CONTROL_PLANE_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"]
)
def test_hosted_credential_pairing(auth_request: Mock, key: str) -> None:
    metadata.detect_run_identity(env={**ENV, key: "selected"})
    args, kwargs = auth_request.call_args
    assert args == ("GET", f"{ENV['LANGSMITH_AUTH_ENDPOINT']}/auth/public")
    assert kwargs["headers"]["x-api-key"] == "selected"
    assert kwargs["headers"]["x-tenant-id"] == "workspace"
    assert kwargs["timeout"] == 1.0


@pytest.mark.parametrize("prefix", ["LANGSMITH", "LANGCHAIN"])
def test_local_credential_pairing(auth_request: Mock, prefix: str) -> None:
    env = {
        "LANGSMITH_CONTROL_PLANE_API_KEY": "unused",
        f"{prefix}_API_KEY": "selected",
        f"{prefix}_ENDPOINT": "http://localhost:1980",
    }
    assert metadata.detect_run_identity(env=env)["langsmith_host_url"] == "http://localhost:1980"
    assert auth_request.call_args.kwargs["headers"]["x-api-key"] == "selected"


@pytest.mark.parametrize("mode", ["failure", "timeout", "mismatch", "malformed", "service"])
def test_failure_and_success_are_cached(auth_request: Mock, mode: str) -> None:
    if mode == "timeout":
        auth_request.side_effect = TimeoutError
    else:
        auth_request.return_value = (
            401 if mode == "failure" else 200,
            {}
            if mode == "malformed"
            else {
                "tenant_id": "other" if mode == "mismatch" else "workspace",
                "organization_id": "org",
                "user_id": "service",
            },
        )
    event = metadata.detect_run_identity(env=ENV)
    assert event["org_id"] == ("org" if mode == "service" else None)
    assert event["principal_id"] is None
    metadata.detect_run_identity(env=ENV)
    assert auth_request.call_count == 1


def test_opt_out_and_inspection_do_not_consume_initialization(auth_request: Mock) -> None:
    metadata.detect_run_identity(env=ENV, is_run=False)
    for key in ("DO_NOT_TRACK", "MDA_TELEMETRY_DISABLED"):
        metadata.detect_run_identity(env={**ENV, key: "1"})
    auth_request.assert_not_called()
    assert metadata.detect_run_identity(env=ENV)["org_id"] == "org"


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("https://EXAMPLE.com:443", "https://example.com"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("file:///tmp/private", None),
        ("https://user:secret@example.com", None),
    ],
)
def test_origin_validation(auth_request: Mock, host: str, origin: str | None) -> None:
    assert (
        metadata.detect_run_identity(env={**ENV, "LANGSMITH_AUTH_ENDPOINT": host})[
            "langsmith_host_url"
        ]
        == origin
    )
    assert auth_request.call_count == (1 if origin else 0)


def test_absent_auth_and_local_principal(
    auth_request: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metadata, "_cached_version", None)
    monkeypatch.setattr(metadata, "version", lambda _: "test-version")
    env = {"MDA_LOCAL_DEV": "1", "MDA_AGENT_AUTH_PRINCIPAL_ID": "developer"}
    event = metadata.detect_run_identity(env=env)
    assert event["org_id"] is event["workspace_id"] is event["principal_id"] is None
    assert event["sdk_version"] == "test-version"
    assert event["environment"] == "local"
    caller: RuntimeIdentity = {
        "user": {"kind": "person", "id": "mda:local-dev"},
        "source": {"provider": "studio"},
    }
    assert metadata.detect_run_identity(env=env, caller=caller)["principal_id"] == "developer"
    auth_request.assert_not_called()
