"""The ``run_completed`` row: how a run was configured, and how it ended."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, TypedDict

from managed_deepagents._telemetry.event import TelemetryEvent

SCHEMA_VERSION = "2026-10-29"
EVENT_TYPE = "run_completed"

#: ``mda dev`` has no LangSmith tenant, so those runs share one id rather than
#: inflating the organization count, as the CLI folds CI runs into one ``ci``.
LOCAL_DISTINCT_ID = "local"

RunEnvironment = Literal["local", "deployed"]

RunOutcome = Literal["success", "failure", "interrupted", "cancelled"]

#: Whether the run is an agent's own or one of its async subagents'.
SubagentRole = Literal["lead", "async_subagent"]

MemoryLayerType = Literal["agent", "user"]

#: ``resolve_user_memory_access`` returns the same ``None`` for several distinct
#: denials, so adoption measured from the presence of ``memory.py`` overcounts.
#: These name the paths it collapses.
UserMemoryDeniedReason = Literal[
    "not_a_person",
    "not_slack_dm",
    "policy_denied",
    "no_context",
]

#: Whether ``run_id`` is the platform's or one we minted for a run without it.
RunIdSource = Literal["platform", "minted"]

#: Which filesystem a run's unrouted paths resolve against.
BackendKind = Literal["sandbox", "state", "trial_disk"]

#: Where ``instructions.md`` and ``/skills/`` are read from, if anywhere.
ContextSource = Literal["hub", "local", "none"]


#: ``user_denied_reason`` is always present because the Node runtime drops
#: ``undefined`` keys from JSON, and a key present in one runtime but not the
#: other becomes a sometimes-missing warehouse column.
class MemoryObservations(TypedDict):
    layers_declared: list[MemoryLayerType]
    layers_mounted: list[MemoryLayerType]
    user_denied_reason: UserMemoryDeniedReason | None


class RunIdentity(TypedDict):
    tenant_id: str | None
    workspace_id: str | None
    org_id: str | None
    # Canonical Agent Auth principal UUID, never a provider subject or deployer.
    principal_id: str | None
    langsmith_host_url: str | None
    deployment_id: str | None
    deployment_name: str | None
    environment: RunEnvironment
    sdk_language: Literal["python"]
    sdk_version: str


class RunCompletedEvent(TelemetryEvent, RunIdentity):
    """One completed warehouse row, created when the run ends.

    Per-op counts are 0..N per run, so they get their own stream keyed on ``run_id``.
    """

    run_id: str
    run_id_source: RunIdSource
    thread_id: str | None
    channel: str
    sandbox_declared: bool
    identity_declared: bool
    skills_declared: bool
    backend_kind: BackendKind
    context_source: ContextSource
    memory: MemoryObservations
    #: The provider's event type, such as ``issues.opened``; ``None`` when none.
    provider_event_type: str | None
    #: How ingress chose the run's user; ``None`` when no channel started it.
    user_resolution: str | None
    subagent_role: SubagentRole
    browser_declared: bool
    #: Whether the agent called a browser tool during the run.
    browser_used: bool
    outcome: RunOutcome
    duration_ms: int


@dataclass(frozen=True)
class RunConfiguration:
    """How a run was configured, all of it known before the first model call."""

    channel: str
    sandbox_declared: bool
    identity_declared: bool
    skills_declared: bool
    backend_kind: BackendKind
    context_source: ContextSource
    memory: MemoryObservations
    provider_event_type: str | None
    user_resolution: str | None
    subagent_role: SubagentRole
    browser_declared: bool


@dataclass
class PendingRun:
    """Application state only; it cannot be recorded as a telemetry event."""

    identity: RunIdentity
    configuration: RunConfiguration
    run_id: str
    run_id_source: RunIdSource
    thread_id: str | None
    event_id: str
    started_monotonic_ms: float
    #: The caller's emitted-once guard; finalization does not change it.
    emitted: bool = False
    #: Set by the caller when the agent calls a browser tool.
    browser_used: bool = False


def start_run(
    identity: RunIdentity,
    configuration: RunConfiguration,
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    started_monotonic_ms: float | None = None,
    event_id: str | None = None,
) -> PendingRun:
    """Capture start facts using ``time.monotonic() * 1000``, never epoch time."""
    resolved_start = (
        time.monotonic() * 1000 if started_monotonic_ms is None else started_monotonic_ms
    )
    return PendingRun(
        started_monotonic_ms=resolved_start,
        identity=identity.copy(),
        configuration=replace(
            configuration,
            memory=MemoryObservations(
                layers_declared=configuration.memory["layers_declared"].copy(),
                layers_mounted=configuration.memory["layers_mounted"].copy(),
                user_denied_reason=configuration.memory["user_denied_reason"],
            ),
        ),
        event_id=str(uuid.uuid4()) if event_id is None else event_id,
        run_id=str(uuid.uuid4()) if run_id is None else run_id,
        run_id_source="minted" if run_id is None else "platform",
        thread_id=thread_id,
    )


def finish_run(
    pending: PendingRun,
    outcome: RunOutcome,
    *,
    ended_monotonic_ms: float | None = None,
    completed_at_ms: int | None = None,
    event_id: str | None = None,
) -> RunCompletedEvent:
    """Create the event at completion; wall time never determines elapsed time.

    ``completed_at_ms`` is wall time in milliseconds since the Unix epoch.
    """
    resolved_end = time.monotonic() * 1000 if ended_monotonic_ms is None else ended_monotonic_ms
    resolved_completion = (
        time.time_ns() // 1_000_000 if completed_at_ms is None else completed_at_ms
    )
    identity, configuration = pending.identity, pending.configuration
    return RunCompletedEvent(
        event_id=pending.event_id if event_id is None else event_id,
        event_type=EVENT_TYPE,
        schema_version=SCHEMA_VERSION,
        created_at=format_timestamp(resolved_completion),
        distinct_id=identity["tenant_id"] or LOCAL_DISTINCT_ID,
        tenant_id=identity["tenant_id"],
        workspace_id=identity["workspace_id"],
        org_id=identity["org_id"],
        principal_id=identity["principal_id"],
        langsmith_host_url=identity["langsmith_host_url"],
        deployment_id=identity["deployment_id"],
        deployment_name=identity["deployment_name"],
        environment=identity["environment"],
        sdk_language=identity["sdk_language"],
        sdk_version=identity["sdk_version"],
        run_id=pending.run_id,
        run_id_source=pending.run_id_source,
        thread_id=pending.thread_id,
        channel=configuration.channel,
        sandbox_declared=configuration.sandbox_declared,
        identity_declared=configuration.identity_declared,
        skills_declared=configuration.skills_declared,
        backend_kind=configuration.backend_kind,
        context_source=configuration.context_source,
        memory=configuration.memory,
        provider_event_type=configuration.provider_event_type,
        user_resolution=configuration.user_resolution,
        subagent_role=configuration.subagent_role,
        browser_declared=configuration.browser_declared,
        browser_used=pending.browser_used,
        outcome=outcome,
        duration_ms=max(0, math.floor(resolved_end - pending.started_monotonic_ms)),
    )


def format_timestamp(epoch_ms: int) -> str:
    """Must match ``formatTimestamp`` in the Node runtime exactly."""
    seconds, millis = divmod(epoch_ms, 1000)
    base = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{millis:03d}Z"
