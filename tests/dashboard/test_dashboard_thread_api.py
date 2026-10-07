import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid7

import pytest
from fastapi import HTTPException

from agent.dashboard import deps
from agent.dashboard.workspace_settings import (
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from agent.message_queue import QueuedMessage
from agent.tasks.store import SidebarTaskMembership
from agent.threads import diffs as thread_diffs
from agent.threads import handlers
from agent.threads import listing as thread_listing
from agent.threads import proxy as thread_proxy
from agent.threads import runs as thread_runs
from agent.threads.summary import TRANSCRIPT_VERSION
from agent.transcript.engine import AppendResult
from agent.transcript.turns import OpenTurn
from agent.users import User, UserPreferences
from agent.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import FakeStore, patch_thread_module

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/kimi-k3"
_VISION_MODEL = "openai:gpt-6.1-sol"
_FABLE = "anthropic:claude-fable-5-1"
_PAIR = ("openai:gpt-6.1-sol", "medium")


@asynccontextmanager
async def _unlocked(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _empty_thread_pins(monkeypatch) -> None:
    async def empty_pins(login: str) -> list[str]:
        return []

    patch_thread_module(monkeypatch, "list_thread_pin_ids", empty_pins)


def _image() -> thread_runs.DashboardImageBody:
    return thread_runs.DashboardImageBody(
        base64=base64.b64encode(b"image").decode("ascii"),
        mimeType="image/png",
    )


def _new_thread_client(created: dict[str, object]) -> object:
    class FakeThreads:
        async def create(
            self, *, thread_id: str, metadata: dict[str, object], if_exists: str
        ) -> None:
            created["thread_id"] = thread_id
            created["metadata"] = dict(metadata)

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            created.setdefault("metadata", {})
            assert isinstance(created["metadata"], dict)
            created["metadata"].update(metadata)

        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "metadata": created.get("metadata", {})}

    class FakeClient:
        threads = FakeThreads()

    return FakeClient()


def _patch_new_thread_deps(monkeypatch, *, profile: dict[str, object]) -> None:
    async def fake_profile(login: str) -> dict[str, object]:
        return dict(profile)

    async def fake_team_default(workspace: str | None = None) -> WorkspaceSettings:
        return WorkspaceSettings(
            {"default_agent_model": _VISION_MODEL, "default_agent_reasoning_effort": "medium"}
        )

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, prof: dict[str, object]) -> str:
        return f"{login}@example.com"

    patch_thread_module(monkeypatch, "get_profile", fake_profile)
    patch_thread_module(monkeypatch, "get_workspace_settings", fake_team_default)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)


async def test_enrich_run_start_command_stamps_workspace_from_repo_owner(
    monkeypatch, fake_store: FakeStore, registry_db
) -> None:
    created: dict[str, object] = {}
    _patch_new_thread_deps(monkeypatch, profile={})
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "octocat")

    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "config": {"configurable": {"repo": "acme/oss"}},
        },
    }

    await thread_runs._enrich_run_start_command(
        "new-tid",
        "octocat",
        command,
        metadata={},
        creating=True,
    )

    created_metadata = created["metadata"]
    assert isinstance(created_metadata, dict)
    assert created_metadata["workspace"] == "oss"


async def test_enrich_run_start_command_resolves_model_from_repos_workspace(
    monkeypatch, fake_store: FakeStore, registry_db
) -> None:
    """A new thread's model default is the repo's workspace override, not the instance record."""
    created: dict[str, object] = {}

    async def fake_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, prof: dict[str, object]) -> str:
        return f"{login}@example.com"

    patch_thread_module(monkeypatch, "get_profile", fake_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))

    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "octocat")
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            default_agent_model="anthropic:claude-opus-5-5",
            default_agent_reasoning_effort="high",
        )
    )
    await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            default_agent_model="openai:gpt-6-astra",
            default_agent_reasoning_effort="low",
        ),
    )

    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "config": {"configurable": {"repo": "acme/oss"}},
        },
    }

    await thread_runs._enrich_run_start_command(
        "new-tid",
        "octocat",
        command,
        metadata={},
        creating=True,
    )

    created_metadata = created["metadata"]
    assert isinstance(created_metadata, dict)
    assert created_metadata["workspace"] == "oss"
    assert created_metadata["resolved_model"] == "openai:gpt-6-astra"
    assert created_metadata["resolved_effort"] == "low"


@pytest.mark.parametrize(
    ("login", "visibility", "requested_admin", "expected_admin"),
    [
        ("workspace-admin", "private", False, True),
        ("workspace-admin", "public", True, False),
        ("teammate", "private", True, False),
    ],
)
async def test_private_threads_created_by_admins_get_admin_permissions(
    monkeypatch,
    login: str,
    visibility: str,
    requested_admin: bool,
    expected_admin: bool,
) -> None:
    created: dict[str, object] = {}
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    _patch_new_thread_deps(monkeypatch, profile={})
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))
    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Update settings"}]},
            "config": {
                "configurable": {
                    "visibility": visibility,
                    "admin_thread": requested_admin,
                }
            },
        },
    }

    enriched = await thread_runs._enrich_run_start_command(
        "new-tid",
        login,
        command,
        metadata={},
        creating=True,
        email=f"{login}@example.com",
    )

    stamped = created["metadata"]
    assert isinstance(stamped, dict)
    assert stamped["visibility"] == visibility
    assert (stamped.get("admin_thread") is True) is expected_admin
    configurable = enriched["params"]["config"]["configurable"]
    assert (configurable.get("admin_thread") is True) is expected_admin


async def test_dashboard_run_stamps_sender_not_original_slack_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = uuid7()
    second = uuid7()

    async def by_login(provider: str, login: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=second) if (provider, login) == ("github", "second-gh") else None

    async def canonical(person: dict[str, str]) -> dict[str, str]:
        return person

    monkeypatch.setattr(User, "for_login", by_login)
    monkeypatch.setattr(User, "canonical_person", canonical)
    created: dict[str, object] = {"metadata": {"source": "slack"}}
    _patch_new_thread_deps(monkeypatch, profile={})
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))
    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "follow up"}]},
            "metadata": {"user_id": str(first)},
            "config": {"metadata": {"user_id": str(first)}},
        },
    }
    enriched = await thread_runs._enrich_run_start_command(
        "existing-tid",
        "second-gh",
        command,
        metadata={
            "source": "slack",
            "source_context": {"slack_thread": {"triggering_user_id": "U123"}},
        },
    )
    assert enriched["params"]["metadata"]["user_id"] == str(second)
    assert enriched["params"]["config"]["metadata"]["user_id"] == str(second)


@pytest.mark.parametrize("selection_changed", [False, True])
async def test_enrich_run_start_command_preserves_explicit_auto_intent(
    monkeypatch: pytest.MonkeyPatch, selection_changed: bool
) -> None:
    metadata = {
        "model": _TEXT_ONLY_MODEL,
        "effort": "high",
        "model_selection": "auto",
        "model_selection_changed": True,
    }
    created: dict[str, object] = {"metadata": metadata}
    _patch_new_thread_deps(monkeypatch, profile={})
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))
    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Fix the typo"}]},
            "config": {
                "configurable": {
                    "model_selection": "auto",
                    **({"model_selection_changed": True} if selection_changed else {}),
                }
            },
        },
    }
    enriched = await thread_runs._enrich_run_start_command(
        "existing-tid", "octocat", command, metadata=metadata
    )
    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["model_selection"] == "auto"
    assert configurable["model_selection_changed"] is selection_changed


@pytest.mark.parametrize("creating", [False, True])
async def test_enrich_run_start_command_uses_vision_fallback_for_text_only_model(
    monkeypatch: pytest.MonkeyPatch,
    creating: bool,
) -> None:
    metadata = {
        "model": _TEXT_ONLY_MODEL,
        "effort": "high",
        "model_selection": "auto",
    }
    created: dict[str, object] = {} if creating else {"metadata": metadata}
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: _new_thread_client(created))

    image = _image()
    command = {
        "method": "run.start",
        "params": {
            "input": {
                "messages": [
                    {
                        "type": "human",
                        "content": [
                            {
                                "type": "image",
                                "base64": image.base64,
                                "mime_type": image.mime_type,
                            },
                            {"type": "text", "text": "see attached"},
                        ],
                    }
                ]
            },
            "config": {"configurable": {}},
        },
    }

    enriched = await thread_runs._enrich_run_start_command(
        "new-tid",
        "octocat",
        command,
        metadata={} if creating else metadata,
        creating=creating,
    )

    stamped = created["metadata"]
    assert isinstance(stamped, dict)
    assert stamped["model"] == _VISION_MODEL
    assert stamped["effort"] == "medium"
    assert stamped["resolved_model"] == _VISION_MODEL
    assert stamped["resolved_effort"] == "medium"
    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"
    assert configurable["model_override_reason"] == "image_input"


async def test_recovery_patch_enforces_size_limit(monkeypatch) -> None:
    async def fake_authorized_thread(thread_id: str, login: str, *, email: str | None = None):
        return {"thread_id": thread_id, "metadata": {"sandbox_id": "sbx", "github_login": login}}

    class FakeSandbox:
        async def aexecute(self, command: str, *, timeout: int | None = None):
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "ok": True,
                        "path": "/tmp/open-swe-tid.patch",
                        "size": thread_diffs._RECOVERY_PATCH_LIMIT_BYTES + 1,
                    }
                ),
                exit_code=0,
            )

    patch_thread_module(monkeypatch, "_authorized_thread", fake_authorized_thread)
    patch_thread_module(monkeypatch, "connect_sandbox", AsyncMock(return_value=FakeSandbox()))

    with pytest.raises(HTTPException) as exc_info:
        await thread_diffs.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 413


async def test_enrich_run_start_command_allowlists_client_configurable(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            assert thread_id == "tid"
            updates.append(metadata)

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        assert login == "octocat"
        return {}

    async def fake_ensure_token(login: str) -> None:
        assert login == "octocat"

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        assert login == "octocat"
        return "octocat@example.com"

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {
            "metadata": {
                "owner_login": "attacker",
                "owner_type": "system",
                "visibility": "private",
                "system_authorization": {
                    "schedule_id": "admin-schedule",
                    "invocation_id": "stolen",
                },
            },
            "config": {
                "configurable": {
                    "github_login": "attacker",
                    "user_email": "attacker@example.com",
                    "source": "github",
                    "invocation_id": "stolen",
                    "prepare_run_id": "stolen",
                    "repo": {"owner": "evil", "name": "repo"},
                    "agent_model_id": _VISION_MODEL,
                    "agent_effort": "medium",
                }
            },
        },
    }

    enriched = await thread_runs._enrich_run_start_command(
        "tid",
        "octocat",
        command,
        metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "repo_owner": "octo",
            "repo_name": "repo",
        },
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["github_login"] == "octocat"
    assert configurable["user_email"] == "octocat@example.com"
    assert configurable["source"] == "dashboard"
    assert configurable["invocation_id"] != "stolen"
    assert not (
        {"owner_login", "owner_type", "visibility", "system_authorization"}
        & enriched["params"]["metadata"].keys()
    )
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"
    assert updates[-1]["model"] == _VISION_MODEL

    offloaded = await thread_runs._enrich_run_start_command(
        "tid",
        "octocat",
        {
            "method": "run.start",
            "params": {"config": {"configurable": {"offload_conversation": True}}},
        },
        metadata=updates[-1],
    )
    assert offloaded["params"]["config"]["configurable"]["model_selection"] == "explicit"
    assert updates[-1]["model_selection"] == "explicit"


async def test_enrich_run_start_command_reuses_a_deduplicated_turn(monkeypatch) -> None:
    """A retried dispatch must join the turn its first request recorded."""

    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            return None

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return "octocat@example.com"

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    monkeypatch.setattr(thread_runs.postgres, "configured", lambda: True)

    recorded = uuid7()
    appended: list[str] = []

    async def fake_append(thread_id: str, commands) -> AppendResult:
        appended.extend(command.command_id for command in commands)
        # The receipt already covers this message: nothing is appended.
        return AppendResult(versions=[1], events=[])

    async def fake_recorded_turn_id(thread_id: str, command_id: str) -> UUID:
        assert command_id == appended[-1]
        return recorded

    patch_thread_module(monkeypatch, "append", fake_append)
    patch_thread_module(monkeypatch, "recorded_turn_id", fake_recorded_turn_id)

    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "retry me"}]},
            "config": {"configurable": {"agent_model_id": _VISION_MODEL}},
        },
    }
    enriched = await thread_runs._enrich_run_start_command(
        "tid",
        "octocat",
        command,
        metadata={"source": "dashboard", "github_login": "octocat", "transcript": "v2"},
    )

    assert enriched["params"]["config"]["configurable"]["transcript_turn_id"] == str(recorded)


async def test_proxy_commands_rejects_non_admin_on_admin_thread(monkeypatch) -> None:
    class AdminThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {
                    "source": "dashboard",
                    "github_login": "workspace-admin",
                    "admin_thread": True,
                },
            }

    class AdminClient:
        threads = AdminThreads()

    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    patch_thread_module(monkeypatch, "langgraph_client", lambda: AdminClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_commands(
            "tid", "teammate", b'{"method": "run.start"}'
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in this thread"


async def test_proxy_commands_preserves_admin_writes_and_owner_reads(monkeypatch) -> None:
    class AdminThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {
                    "source": "dashboard",
                    "github_login": "workspace-admin",
                    "admin_thread": True,
                },
            }

    class AdminClient:
        threads = AdminThreads()

    class FakeResponse:
        status_code = 200
        content = b"{}"
        headers: dict[str, str] = {}

    posted: list[bytes] = []

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> FakeResponse:
            posted.append(content)
            return FakeResponse()

    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin,another-admin")
    patch_thread_module(monkeypatch, "langgraph_client", lambda: AdminClient())
    monkeypatch.setattr(thread_proxy.httpx2, "AsyncClient", FakeAsyncClient)

    status_code, _, _ = await thread_proxy.proxy_dashboard_thread_commands(
        "tid", "another-admin", b'{"method": "input.respond"}'
    )

    assert status_code == 200

    monkeypatch.setenv("CONFIGURED_ADMINS", "another-admin")
    status_code, _, _ = await thread_proxy.proxy_dashboard_thread_commands(
        "tid", "workspace-admin", b'{"method": "agent.getTree"}'
    )

    assert status_code == 200
    assert posted == [
        b'{"method": "input.respond"}',
        b'{"method": "agent.getTree"}',
    ]


@pytest.mark.parametrize("open_turn", [True, False])
async def test_proxy_commands_steers_into_the_open_turn_or_waits_for_an_ending_run(
    monkeypatch, open_turn: bool
) -> None:
    """A run that has closed its turn but not ended has nothing left to steer into."""

    class BusyThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "status": "busy",
                "metadata": {
                    "source": "dashboard",
                    "github_login": "owner",
                    "transcript": TRANSCRIPT_VERSION,
                    "latest_run_id": "run-0",
                },
            }

    class BusyClient:
        threads = BusyThreads()

    turn = OpenTurn(turn_id=uuid7(), run_id="run-1") if open_turn else None
    handled: list[tuple[str, OpenTurn | None]] = []

    async def fake_steer_target(thread_id: str) -> OpenTurn | None:
        return turn

    async def fake_steer(*args: object, turn: OpenTurn | None, **kwargs: object) -> dict[str, str]:
        handled.append(("steer", turn))
        return {}

    async def fake_queue(*args: object, **kwargs: object) -> dict[str, str]:
        handled.append(("queue", None))
        return {}

    patch_thread_module(monkeypatch, "langgraph_client", lambda: BusyClient())
    monkeypatch.setattr(thread_proxy, "steer_target", fake_steer_target)
    monkeypatch.setattr(thread_proxy, "steer_running_thread", fake_steer)
    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue)

    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "and this", "id": "m1"}]}},
    }
    status_code, _, _ = await thread_proxy.proxy_dashboard_thread_commands(
        "tid", "owner", json.dumps(command).encode()
    )

    assert status_code == 200
    assert handled == ([("steer", turn)] if open_turn else [("queue", None)])


async def test_run_cancel_lets_only_the_sender_withdraw_a_queued_follow_up(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "owner_login": "owner", "visibility": "public"},
            }

    class FakeRuns:
        async def get(self, thread_id: str, run_id: str) -> dict[str, object]:
            return {"run_id": run_id, "metadata": {"queued_by": "sender"}}

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_cancel("tid", "run-2", "teammate")
    assert exc_info.value.status_code == 403
    assert "sender" in exc_info.value.detail


async def test_thread_state_uses_current_run_status_when_checkpoint_is_stale(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "status": "idle",
                "metadata": {
                    "source": "dashboard",
                    "github_login": "owner",
                    "latest_run_status": "success",
                },
            }

        async def update(self, **kwargs: object) -> None:
            pass

        async def get_state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": []}, "next": []}

    class FakeRuns:
        async def list(self, thread_id: str, *, limit: int) -> list[dict[str, str]]:
            return [{"run_id": "run-1", "status": "running"}]

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    state = await handlers.get_dashboard_thread_state("tid", "owner")

    assert "next" not in state


async def test_read_endpoints_reject_non_surfaced_source(monkeypatch) -> None:
    """Threads with an unknown source are not readable by anyone."""

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": "tid",
                "metadata": {"source": "unknown-source", "github_login": "owner"},
            }

        async def get_state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": []}}

    class FakeClient:
        threads = FakeThreads()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await handlers.get_dashboard_thread_state("tid", "owner")
    assert exc_info.value.status_code == 404


async def test_send_dashboard_message_returns_502_when_activity_unknown(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "github_login": "octocat"},
            }

    class FakeClient:
        threads = FakeThreads()

    async def unknown_activity(thread_id: str) -> None:
        assert thread_id == "tid"
        return None

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())
    patch_thread_module(monkeypatch, "get_thread_active_status", unknown_activity)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.send_dashboard_message(
            "tid",
            "octocat",
            thread_runs.ThreadMessageBody(content="hello"),
        )

    assert exc_info.value.status_code == 502


async def test_send_dashboard_message_rejects_non_admin_on_admin_thread(monkeypatch) -> None:
    class AdminThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {
                    "source": "dashboard",
                    "github_login": "workspace-admin",
                    "admin_thread": True,
                },
            }

        async def update(self, **kwargs: object) -> None:
            raise AssertionError("must not update")

    class AdminClient:
        threads = AdminThreads()

    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    patch_thread_module(monkeypatch, "langgraph_client", lambda: AdminClient())

    with pytest.raises(HTTPException) as exc_info:
        await handlers.send_dashboard_message(
            "tid",
            "teammate",
            thread_runs.ThreadMessageBody(content="ship it"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in this thread"


def _make_threads(count: int, *, resolved_before: int) -> list[dict[str, object]]:
    threads: list[dict[str, object]] = []
    for index in range(count):
        threads.append(
            {
                "thread_id": f"t{index}",
                "metadata": {
                    "source": "dashboard",
                    "github_login": "octocat",
                    "title": f"Thread {index}",
                    "updated_at_ms": count - index,
                    "resolved": index < resolved_before,
                },
            }
        )
    return threads


async def test_list_dashboard_threads_page_pages_beyond_first_search_batch(monkeypatch) -> None:
    page_size = thread_listing._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 50, resolved_before=page_size)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    offsets: list[int] = []
    run_list_calls = 0

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            offsets.append(offset)
            assert select == thread_listing._THREAD_LIST_SELECT
            return threads[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            return None

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            nonlocal run_list_calls
            run_list_calls += 1
            return []

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, resolved=False
    )

    assert result["hasMore"] is True
    assert len(result["items"]) == 25
    assert all(item["resolved"] is False for item in result["items"])
    assert page_size in offsets
    assert run_list_calls == 0


async def test_task_hierarchy_opt_in_visibility_and_idle_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(thread_listing, "_THREADS_SEARCH_PAGE", 2)
    threads = _make_threads(5, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    memberships = {
        "t0": SidebarTaskMembership("t0", "task", "worker", "t3", "Thread 0"),
        "t1": SidebarTaskMembership("t1", "task", "worker", "t3", "Thread 1"),
        "t3": SidebarTaskMembership("t3", "task", "coordinator", "t3"),
    }

    async def membership_lookup(
        ids: list[str], *, workers_of: bool = False
    ) -> dict[str, SidebarTaskMembership]:
        return {
            key: member
            for key, member in memberships.items()
            if (
                member.role == "worker" and member.coordinator_thread_id in ids
                if workers_of
                else key in ids
            )
        }

    async def search(
        *, ids: list[str] | None = None, offset: int = 0, limit: int = 50, **_: object
    ) -> list[dict[str, object]]:
        return (
            [thread for thread in threads if thread["thread_id"] in ids]
            if ids is not None
            else threads[offset : offset + limit]
        )

    client = SimpleNamespace(
        threads=SimpleNamespace(search=search),
        runs=SimpleNamespace(list=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_listing, "sidebar_memberships", membership_lookup)
    preferences = AsyncMock(return_value=UserPreferences())
    monkeypatch.setattr(User, "preferences_for_login", preferences)
    disabled = await thread_listing.list_dashboard_threads_page("octocat", hierarchy=True, limit=2)
    assert [item["id"] for item in disabled["items"]] == ["t0", "t1"]
    assert all("taskWorkers" not in item for item in disabled["items"])
    assert disabled["items"][0]["taskMembership"]["role"] == "worker"
    flat_parent = {"id": "t3"}
    await thread_listing.attach_task_workers(client, [flat_parent], "octocat", None)
    assert "taskWorkers" not in flat_parent
    preferences.return_value = UserPreferences(experimental_task_coordination=True)
    first = await thread_listing.list_dashboard_threads_page("octocat", hierarchy=True, limit=1)
    assert [item["id"] for item in first["items"]] == ["t3"]
    parent = first["items"][0]
    assert parent["status"] == "finished"
    assert [worker["id"] for worker in parent["taskWorkers"]] == ["t0", "t1"]
    cast(dict[str, object], threads[1]["metadata"]).update(
        visibility="private", owner_login="someone-else"
    )
    threads[0]["status"] = "busy"
    running = await thread_listing.list_dashboard_threads_page("octocat", hierarchy=True)
    assert [worker["id"] for worker in running["items"][0]["taskWorkers"]] == ["t0"]
    assert running["items"][0]["taskWorkers"][0]["status"] == "running"
    assert running["items"][0]["status"] == "finished"
    threads[0]["status"] = "idle"
    memberships["t2"] = SidebarTaskMembership("t2", "task", "worker", "t3")
    refreshed = await thread_listing.list_dashboard_threads_page("octocat", hierarchy=True)
    assert [worker["id"] for worker in refreshed["items"][0]["taskWorkers"]] == ["t0", "t2"]
    assert refreshed["items"][0]["taskWorkers"][0]["status"] == "finished"
    cast(dict[str, object], threads[0]["metadata"])["resolved"] = True
    archived = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, resolved=False
    )
    assert [worker["id"] for worker in archived["items"][0]["taskWorkers"]] == ["t2"]
    cast(dict[str, object], threads[3]["metadata"])["resolved"] = True
    unresolved = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, resolved=False, limit=1
    )
    assert [item["id"] for item in unresolved["items"]] == ["t2"]
    assert unresolved["hasMore"] is True
    resolved = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, resolved=True
    )
    assert [item["id"] for item in resolved["items"]] == ["t3"]
    assert [worker["id"] for worker in resolved["items"][0]["taskWorkers"]] == ["t0"]
    all_threads = await thread_listing.list_dashboard_threads_page("octocat", hierarchy=True)
    assert [item["id"] for item in all_threads["items"]] == ["t3", "t4"]
    assert [worker["id"] for worker in all_threads["items"][0]["taskWorkers"]] == ["t0", "t2"]
    cast(dict[str, object], threads[3]["metadata"]).update(
        visibility="private", owner_login="someone-else"
    )
    standalone = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, query="Thread 0"
    )
    assert [item["id"] for item in standalone["items"]] == ["t0"]
    assert standalone["items"][0]["taskMembership"]["coordinatorThreadId"] is None


async def test_task_hierarchy_stops_at_distinct_root_target_or_scan_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        User,
        "preferences_for_login",
        AsyncMock(return_value=UserPreferences(experimental_task_coordination=True)),
    )
    monkeypatch.setattr(thread_listing, "_THREADS_SEARCH_PAGE", 2)
    threads = _make_threads(12, resolved_before=0)
    for index, thread in enumerate(threads):
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = (
            "error" if index == 4 else "success"
        )
    memberships = {
        f"t{index}": SidebarTaskMembership(f"t{index}", "task", "worker", "t9")
        for index in range(4)
    }
    memberships["t9"] = SidebarTaskMembership("t9", "task", "coordinator", "t9")
    offsets: list[int] = []

    async def membership_lookup(
        ids: list[str], *, workers_of: bool = False
    ) -> dict[str, SidebarTaskMembership]:
        return {
            key: member
            for key, member in memberships.items()
            if (
                member.role == "worker" and member.coordinator_thread_id in ids
                if workers_of
                else key in ids
            )
        }

    async def search(
        *,
        ids: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
        **_: object,
    ) -> list[dict[str, object]]:
        if ids is not None:
            return [thread for thread in threads if thread["thread_id"] in ids]
        offsets.append(offset)
        return threads[offset : offset + limit]

    client = SimpleNamespace(
        threads=SimpleNamespace(search=search),
        runs=SimpleNamespace(list=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_listing, "sidebar_memberships", membership_lookup)
    first = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, limit=1, status="finished"
    )
    assert [item["id"] for item in first["items"]] == ["t9"]
    assert first["hasMore"] is True
    assert max(offsets) < len(threads)
    assert [worker["id"] for worker in first["items"][0]["taskWorkers"]] == ["t0", "t1", "t2", "t3"]
    second = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, limit=1, offset=1, status="finished"
    )
    assert [item["id"] for item in second["items"]] == ["t5"]
    memberships["t8"] = SidebarTaskMembership("t8", "task", "worker", "t9")
    for index in (0, 5, 8):
        cast(dict[str, object], threads[index]["metadata"])["title"] = "Needle"
    filtered = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, limit=1, query="Needle"
    )
    assert [worker["id"] for worker in filtered["items"][0]["taskWorkers"]] == ["t0", "t8"]

    monkeypatch.setattr(thread_listing, "_THREADS_PAGE_SCAN_CAP", 4)
    offsets.clear()
    no_matches = await thread_listing.list_dashboard_threads_page(
        "octocat", hierarchy=True, include_all=True, repo="langchain-ai/another-repo"
    )
    assert no_matches["items"] == []
    assert max(offsets) < 4


async def test_list_dashboard_threads_page_scopes_search_to_requested_participant(
    monkeypatch,
) -> None:
    threads = [
        {
            "thread_id": "surfaced",
            "metadata": {
                "source": "dashboard",
                "participant_logins": {"other-user": True},
                "latest_run_status": "success",
                "updated_at_ms": 2,
            },
        },
        {
            "thread_id": "internal",
            "metadata": {
                "source": "reviewer",
                "participant_logins": {"other-user": True},
                "latest_run_status": "success",
                "updated_at_ms": 1,
            },
        },
    ]
    searches: list[dict[str, object]] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            searches.append(metadata)
            return threads[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            return None

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    result = await thread_listing.list_dashboard_threads_page(
        "admin-user",
        email="admin@example.com",
        filter_participant_login="other-user",
        surfaced_only=True,
    )

    # The legacy owner filter rides along until pre-participant threads age out.
    assert searches == [
        {"participant_logins": {"other-user": True}},
        {"github_login": "other-user"},
    ]
    assert [item["id"] for item in result["items"]] == ["surfaced"]


async def test_list_dashboard_threads_page_filters_ownerless_threads(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    cast(dict[str, object], threads[0]["metadata"]).update(
        {"repo_owner": "langchain-ai", "repo_name": "open-swe"}
    )
    cast(dict[str, object], threads[1]["metadata"])["repo"] = {
        "owner": "langchain-ai",
        "name": "langgraph",
    }

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            return threads[offset : offset + limit]

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    patch_thread_module(
        monkeypatch,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads(), runs=FakeRuns()),
    )

    result = await thread_listing.list_dashboard_threads_page("octocat", email=None, ownerless=True)

    assert [item["id"] for item in result["items"]] == ["t2"]


async def test_pin_dashboard_thread_rejects_unreadable_thread(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id):
            return {"thread_id": thread_id, "metadata": {"source": "internal"}}

    patch_thread_module(
        monkeypatch,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads()),
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_listing.pin_dashboard_thread("private-thread", "octocat")

    assert exc_info.value.status_code == 404


async def test_status_filter_refreshes_threads_missing_run_status(monkeypatch) -> None:
    threads = _make_threads(2, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["source"] = "slack"
    run_statuses = {"t0": "success", "t1": "error"}
    run_list_thread_ids: list[str] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            return threads[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            return None

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            run_list_thread_ids.append(thread_id)
            return [{"id": f"run-{thread_id}", "status": run_statuses[thread_id]}]

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, status="finished"
    )

    assert {item["id"] for item in result["items"]} == {"t0"}
    assert result["items"][0]["status"] == "finished"
    assert set(run_list_thread_ids) == {"t0", "t1"}


@pytest.mark.parametrize("status", ["interrupted", "error"])
async def test_flat_status_filter_applies_task_overrides_before_paging(
    monkeypatch: pytest.MonkeyPatch, status: Literal["interrupted", "error"]
) -> None:
    threads = _make_threads(3, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    memberships = {
        thread_id: SidebarTaskMembership(
            thread_id,
            "task",
            "worker",
            "coordinator",
            cancelled=status == "interrupted",
            launch_error=status == "error",
        )
        for thread_id in ("t0", "t1")
    }

    async def membership_lookup(ids: list[str]) -> dict[str, SidebarTaskMembership]:
        return {key: member for key, member in memberships.items() if key in ids}

    async def search(*, offset: int, limit: int, **_: object) -> list[dict[str, object]]:
        return threads[offset : offset + limit]

    client = SimpleNamespace(threads=SimpleNamespace(search=search))
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_listing, "sidebar_memberships", membership_lookup)

    finished = await thread_listing.list_dashboard_threads_page(
        "octocat", status="finished", limit=1
    )
    assert [item["id"] for item in finished["items"]] == ["t2"]
    assert finished["items"][0]["status"] == "finished"
    assert finished["hasMore"] is False

    first = await thread_listing.list_dashboard_threads_page("octocat", status=status, limit=1)
    assert [item["id"] for item in first["items"]] == ["t0"]
    assert first["items"][0]["status"] == status
    assert first["hasMore"] is True

    second = await thread_listing.list_dashboard_threads_page(
        "octocat", status=status, limit=1, offset=1
    )
    assert [item["id"] for item in second["items"]] == ["t1"]
    assert second["items"][0]["status"] == status
    assert second["hasMore"] is False


async def test_branch_diff_rejects_an_unsafe_branch_name(monkeypatch) -> None:
    metadata = {
        "repo_owner": "langchain-ai",
        "repo_name": "open-swe",
        "base_branch": "main",
        "branch_name": "../../etc/passwd",
    }
    patch_thread_module(monkeypatch, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    patch_thread_module(monkeypatch, "_github_token_for_login", AsyncMock(return_value="token"))
    build_compare = AsyncMock()
    patch_thread_module(monkeypatch, "build_compare_diff_files", build_compare)

    with pytest.raises(HTTPException) as excinfo:
        await thread_diffs.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert excinfo.value.status_code == 404
    build_compare.assert_not_awaited()


async def test_cancel_settles_its_runs_before_the_queued_follow_up(
    monkeypatch, registry_db: None
) -> None:
    """The replacement run's own turn must not be settled as interrupted."""
    order: list[str] = []
    await QueuedMessage.put("thread-1", {"text": "and also this"})
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {"github_login": "owner", "latest_run_status": "running"},
    }

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return thread

        async def update(self, **kwargs: object) -> None:
            return None

    class FakeRuns:
        async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, str]]:
            return [{"run_id": "running-run"}] if kwargs["status"] == "running" else []

        async def cancel_many(self, **kwargs: object) -> None:
            order.append("cancel")

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    async def fake_settle(thread_id: str, run_id: str | None, **kwargs: object) -> None:
        order.append(f"settle:{run_id}")
        return None

    async def fake_configurable(*args: object, **kwargs: object) -> dict[str, object]:
        return {}

    async def fake_dispatch(*args: object, **kwargs: object) -> dict[str, str]:
        order.append("dispatch")
        return {"run_id": "replacement-run"}

    async def fake_summary(thread: dict[str, object]) -> dict[str, object]:
        return {"thread_id": "thread-1"}

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())
    patch_thread_module(monkeypatch, "settle_run_turn", fake_settle)
    patch_thread_module(monkeypatch, "_build_dashboard_configurable", fake_configurable)
    patch_thread_module(monkeypatch, "dispatch_agent_run", fake_dispatch)
    patch_thread_module(monkeypatch, "_thread_summary", fake_summary)

    await handlers.cancel_dashboard_thread("thread-1", "owner")

    assert order == ["cancel", "settle:running-run", "dispatch"]


async def test_cancel_dashboard_thread_rejects_non_owner(monkeypatch) -> None:
    cancelled = False

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "status": "busy",
                "metadata": {"github_login": "owner"},
            }

        async def update(self, **kwargs: object) -> None:
            raise AssertionError("must not update")

    class FakeRuns:
        async def cancel_many(self, **kwargs: object) -> None:
            nonlocal cancelled
            cancelled = True

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException):
        await handlers.cancel_dashboard_thread("thread-1", "someone-else")

    assert cancelled is False


async def test_admin_cancel_dashboard_thread_does_not_update_on_cancel_failure(monkeypatch) -> None:
    updated = False

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "status": "busy", "metadata": {}}

        async def update(self, **kwargs: object) -> None:
            nonlocal updated
            updated = True

    class FakeRuns:
        async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, str]]:
            return [{"run_id": f"{kwargs['status']}-run"}]

        async def cancel_many(self, **kwargs: object) -> None:
            raise RuntimeError("runtime unavailable")

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await handlers.admin_cancel_dashboard_thread("thread-1")

    assert exc_info.value.status_code == 502
    assert updated is False


def test_admin_cancel_thread_dependency_rejects_non_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    with pytest.raises(HTTPException) as exc_info:
        deps.require_admin({"sub": "not-admin", "email": "user@example.com"})

    assert exc_info.value.status_code == 403


async def test_steer_running_thread_records_and_delivers_the_follow_up(
    monkeypatch, registry_db: None
) -> None:
    updates: list[dict[str, object]] = []
    turn = uuid7()

    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            assert thread_id == "tid"
            updates.append(metadata)

        async def get_state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": []}}

    class FakeRuns:
        async def get(self, thread_id: str, run_id: str) -> dict[str, str]:
            return {"run_id": run_id, "status": "running"}

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    appended: list[object] = []

    async def fake_append(thread_id: str, commands) -> AppendResult:
        assert thread_id == "tid"
        appended.extend(commands)
        return AppendResult(versions=[1], events=[])

    patch_thread_module(monkeypatch, "langgraph_client", lambda: FakeClient())
    patch_thread_module(monkeypatch, "append", fake_append)
    monkeypatch.setattr("agent.utils.thread_ops.langgraph_client", lambda: FakeClient())
    monkeypatch.setattr("agent.thread_feedback.note_feedback_activity", AsyncMock())

    result = await thread_runs.steer_running_thread(
        "tid",
        "teammate",
        {
            "id": 7,
            "method": "run.start",
            "params": {
                "input": {
                    "messages": [{"role": "user", "content": "also check the tests", "id": "msg-1"}]
                }
            },
        },
        metadata={
            "source": "dashboard",
            "transcript": "v2",
            # Stale: the proxy has not yet recorded the run that owns the turn.
            "latest_run_id": "run-0",
            "model": "openai:gpt-5",
        },
        turn=OpenTurn(turn_id=turn, run_id="run-1"),
        email="teammate@example.com",
    )

    assert result == {
        "id": 7,
        "type": "success",
        "result": {
            "thread_id": "tid",
            "run_id": "run-1",
            "message_id": "msg-1",
            "steered": True,
        },
    }
    # The running agent finds the message before its next model call.
    [queued] = await QueuedMessage.for_thread("tid")
    assert isinstance(queued.content, dict)
    assert queued.content["queue_id"] == "msg-1"
    assert queued.content["text"] == "also check the tests"
    assert queued.content["sender"]["github_login"] == "teammate"
    assert "source" not in queued.content
    # The transcript shows it on the live turn right away, under the id the
    # middleware will record it with, so the two writes deduplicate.
    [command] = appended
    assert command.command_id == "human:msg-1"
    assert command.turn_id == turn
    assert command.event.role == "human"
    assert command.event.sender.login == "teammate"
    assert "also check the tests" in command.event.text
    assert updates[-1]["participant_logins"] == {"teammate": True}
