"""The run_completed row and the configuration it is opened with."""

from __future__ import annotations

import copy

import pytest

from managed_deepagents._telemetry import (
    MemoryObservations,
    PendingRun,
    RunConfiguration,
    RunIdentity,
    finish_run,
    format_timestamp,
    start_run,
)
from managed_deepagents._telemetry import run as run_module

IDENTITY = RunIdentity(
    tenant_id="tenant-123",
    workspace_id="tenant-123",
    org_id=None,
    principal_id=None,
    langsmith_host_url=None,
    deployment_id="deployment-1",
    deployment_name="support-bot",
    environment="deployed",
    sdk_language="python",
    sdk_version="0.7.2",
)

CONFIGURATION = RunConfiguration(
    channel="slack",
    sandbox_declared=True,
    identity_declared=False,
    skills_declared=True,
    backend_kind="sandbox",
    context_source="hub",
    memory=MemoryObservations(
        layers_declared=["agent", "user"],
        layers_mounted=["agent"],
        user_denied_reason="not_slack_dm",
    ),
    provider_event_type="mention",
    user_resolution="agent_auth_principal",
    subagent_role="lead",
    browser_declared=True,
)


def _pending(**overrides: object) -> PendingRun:
    identity = RunIdentity(**{**IDENTITY, **overrides})  # type: ignore[typeddict-item]
    return start_run(
        identity,
        CONFIGURATION,
        run_id="11111111-1111-4111-8111-111111111111",
        thread_id="44444444-4444-4444-8444-444444444444",
        started_monotonic_ms=1000,
        event_id="22222222-2222-4222-8222-222222222222",
    )


def test_keeps_pending_state_separate_from_the_event_envelope() -> None:
    pending = _pending()

    for key in ("event", "created_at", "event_type", "outcome", "duration_ms"):
        assert not hasattr(pending, key)
    assert pending.emitted is False


def test_mints_a_run_id_when_the_platform_supplies_none() -> None:
    pending = start_run(IDENTITY, CONFIGURATION)

    assert len(pending.run_id) == 36
    assert pending.run_id_source == "minted"


def test_finish_run_has_the_expected_shape() -> None:
    event = finish_run(_pending(), "success", ended_monotonic_ms=2234, completed_at_ms=2234)

    assert event == {
        "event_id": "22222222-2222-4222-8222-222222222222",
        "event_type": "run_completed",
        "schema_version": "2026-10-29",
        "created_at": "1970-01-01T00:00:02.234Z",
        "distinct_id": "tenant-123",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "run_id_source": "platform",
        "thread_id": "44444444-4444-4444-8444-444444444444",
        "tenant_id": "tenant-123",
        "workspace_id": "tenant-123",
        "org_id": None,
        "principal_id": None,
        "langsmith_host_url": None,
        "deployment_id": "deployment-1",
        "deployment_name": "support-bot",
        "environment": "deployed",
        "sdk_language": "python",
        "sdk_version": "0.7.2",
        "channel": "slack",
        "outcome": "success",
        "duration_ms": 1234,
        "sandbox_declared": True,
        "identity_declared": False,
        "skills_declared": True,
        "backend_kind": "sandbox",
        "context_source": "hub",
        "memory": {
            "layers_declared": ["agent", "user"],
            "layers_mounted": ["agent"],
            "user_denied_reason": "not_slack_dm",
        },
        "provider_event_type": "mention",
        "user_resolution": "agent_auth_principal",
        "subagent_role": "lead",
        "browser_declared": True,
        "browser_used": False,
    }


def test_reports_the_browser_use_the_run_recorded() -> None:
    pending = _pending()
    pending.browser_used = True

    assert finish_run(pending, "success")["browser_used"] is True


def test_leaves_the_pending_run_untouched_so_a_second_close_is_identical() -> None:
    pending = _pending()

    first = finish_run(pending, "success", ended_monotonic_ms=2000, completed_at_ms=2000)
    second = finish_run(pending, "success", ended_monotonic_ms=2000, completed_at_ms=2000)

    assert pending.emitted is False
    assert not hasattr(pending, "event")
    assert second == first


def test_falls_back_to_the_local_distinct_id_without_a_tenant() -> None:
    event = finish_run(
        _pending(tenant_id=None), "failure", ended_monotonic_ms=1000, completed_at_ms=1000
    )

    assert event["distinct_id"] == "local"
    assert event["tenant_id"] is None


def test_never_reports_a_negative_duration() -> None:
    event = finish_run(_pending(), "interrupted", ended_monotonic_ms=0, completed_at_ms=0)

    assert event["duration_ms"] == 0


def test_formats_timestamps_the_way_the_node_runtime_does() -> None:
    assert format_timestamp(1234) == "1970-01-01T00:00:01.234Z"
    assert format_timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert format_timestamp(1_757_000_000_123) == "2025-09-04T15:33:20.123Z"


@pytest.mark.parametrize("completed_at_ms", [2000, 1_000_000])
def test_wall_clock_jumps_do_not_change_elapsed_time(
    monkeypatch: pytest.MonkeyPatch, completed_at_ms: int
) -> None:
    monkeypatch.setattr(run_module.time, "time_ns", lambda: 10_000_000_000)
    monkeypatch.setattr(run_module.time, "monotonic", lambda: 1.00075)
    pending = start_run(IDENTITY, CONFIGURATION)

    monkeypatch.setattr(run_module.time, "monotonic", lambda: 2.23525)
    monkeypatch.setattr(run_module.time, "time_ns", lambda: completed_at_ms * 1_000_000)
    event = finish_run(pending, "success")

    assert pending.started_monotonic_ms == pytest.approx(1000.75)
    assert event["duration_ms"] == 1234
    assert event["created_at"] == format_timestamp(completed_at_ms)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(1000.75, 2235.25, 1234), (1000, 1000.5, 0), (1000, 1000, 0), (1000, 999.5, 0)],
)
def test_converts_elapsed_time_to_whole_milliseconds(
    start: float, end: float, expected: int
) -> None:
    pending = start_run(IDENTITY, CONFIGURATION, started_monotonic_ms=start)
    event = finish_run(pending, "success", ended_monotonic_ms=end, completed_at_ms=0)

    assert event["duration_ms"] == expected
    assert event["created_at"] == "1970-01-01T00:00:00.000Z"


def test_preserves_start_time_configuration_when_inputs_change() -> None:
    identity = IDENTITY.copy()
    configuration = copy.deepcopy(CONFIGURATION)
    pending = start_run(identity, configuration)

    identity["sdk_version"] = "changed"
    configuration.memory["layers_mounted"].append("user")
    event = finish_run(pending, "success")

    assert event["sdk_version"] == IDENTITY["sdk_version"]
    assert event["memory"] == CONFIGURATION.memory


def test_event_id_override_does_not_change_pending_state() -> None:
    pending = _pending()
    event = finish_run(pending, "interrupted", event_id="override-id")

    assert event["event_id"] == "override-id"
    assert event["outcome"] == "interrupted"
    assert pending.event_id == "22222222-2222-4222-8222-222222222222"
    assert pending.emitted is False
