import base64
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.dashboard import routes, thread_api
from agent.dashboard.agent_overrides import resolve_agent_model_id
from agent.dashboard.options import model_supports_images

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.5"
_FABLE = "anthropic:claude-fable-5"
_PAIR = ("openai:gpt-5.6-sol", "medium")


def _image() -> thread_api.DashboardImageBody:
    return thread_api.DashboardImageBody(
        base64=base64.b64encode(b"image").decode("ascii"),
        mimeType="image/png",
    )


def test_model_supports_images_marks_text_only_fireworks_models() -> None:
    assert not model_supports_images(_TEXT_ONLY_MODEL)
    assert model_supports_images(_VISION_MODEL)


def test_user_message_content_rejects_images_for_text_only_model() -> None:
    with pytest.raises(HTTPException) as exc_info:
        thread_api._user_message_content("see attached", [_image()], model_id=_TEXT_ONLY_MODEL)

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail


def test_user_message_content_allows_images_for_vision_model() -> None:
    content = thread_api._user_message_content("see attached", [_image()], model_id=_VISION_MODEL)

    assert isinstance(content, list)
    assert content[-1] == {"type": "text", "text": "see attached"}
    assert any(block.get("type") != "text" for block in content)


def test_langgraph_proxy_headers_include_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")

    headers = thread_api._langgraph_proxy_headers(accept="text/event-stream")

    assert headers["X-API-Key"] == "ls-key"
    assert headers["Accept"] == "text/event-stream"


async def test_resolve_agent_model_choice_applies_profile_before_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
        None,
        None,
    )

    assert (model_id, effort) == (_TEXT_ONLY_MODEL, "high")


async def test_resolve_agent_model_choice_applies_request_before_profile(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
        "anthropic:claude-opus-4-8",
        "high",
    )

    assert (model_id, effort) == ("anthropic:claude-opus-4-8", "high")


async def test_resolve_agent_model_id_defaults_to_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None)
    assert model_id == _TEXT_ONLY_MODEL


async def test_resolve_agent_model_id_applies_profile_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)

    async def fake_load_profile(login: str) -> dict:
        return {"default_model": _VISION_MODEL, "reasoning_effort": "medium"}

    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", fake_load_profile)

    model_id = await resolve_agent_model_id("someuser")
    assert model_id == _VISION_MODEL


async def test_resolve_agent_model_id_applies_per_thread_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None, per_thread_model_id="anthropic:claude-opus-4-8")
    assert model_id == "anthropic:claude-opus-4-8"


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

    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, prof: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "get_profile", fake_profile)
    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)


async def test_enrich_run_start_command_creates_and_stamps_new_thread(monkeypatch) -> None:
    created: dict[str, object] = {}
    _patch_new_thread_deps(monkeypatch, profile={})
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: _new_thread_client(created))

    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "config": {
                "configurable": {
                    "repo": "octo/repo",
                    "agent_model_id": _VISION_MODEL,
                    "agent_effort": "medium",
                }
            },
        },
    }

    enriched = await thread_api._enrich_run_start_command(
        "new-tid",
        "octocat",
        command,
        metadata={},
        creating=True,
    )

    stamped = created["metadata"]
    assert isinstance(stamped, dict)
    assert stamped["source"] == "dashboard"
    assert stamped["github_login"] == "octocat"
    assert stamped["title"] == "Fix the flaky test"
    assert stamped["repo_owner"] == "octo"
    assert stamped["repo_name"] == "repo"

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["github_login"] == "octocat"
    assert configurable["source"] == "dashboard"
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"
    # Dashboard-only creation hints must not leak into the run config.
    assert "repo_explicitly_none" not in configurable
    assert enriched["params"]["assistant_id"] == "agent"


async def test_enrich_run_start_command_uses_vision_fallback_for_text_only_model(
    monkeypatch,
) -> None:
    created: dict[str, object] = {}
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: _new_thread_client(created))

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

    enriched = await thread_api._enrich_run_start_command(
        "new-tid",
        "octocat",
        command,
        metadata={},
        creating=True,
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


def _thread_with_metadata(metadata: dict) -> dict:
    return {"thread_id": "t1", "status": "idle", "metadata": metadata}


def test_thread_summary_includes_pr_and_diff_stats() -> None:
    summary = thread_api._thread_summary(
        _thread_with_metadata(
            {
                "repo_full_name": "langchain-ai/open-swe",
                "title": "Add feature",
                "pr_number": 42,
                "pr_url": "https://github.com/langchain-ai/open-swe/pull/42",
                "pr_state": "draft",
                "pr_title": "feat: add feature",
                "branch_name": "open-swe/feature",
                "base_branch": "main",
                "diff_stats": {"files": 3, "additions": 10, "deletions": 2},
            }
        )
    )

    assert summary["pr"] == {
        "number": 42,
        "title": "feat: add feature",
        "state": "draft",
        "headRef": "open-swe/feature",
        "baseRef": "main",
        "url": "https://github.com/langchain-ai/open-swe/pull/42",
    }
    assert summary["diffStats"] == {"files": 3, "additions": 10, "deletions": 2}


def test_thread_summary_defaults_unknown_pr_state_to_open() -> None:
    summary = thread_api._thread_summary(
        _thread_with_metadata(
            {
                "pr_number": 7,
                "pr_url": "https://example.com/pull/7",
                "pr_state": "bogus",
            }
        )
    )

    assert summary["pr"]["state"] == "open"


def test_thread_summary_omits_pr_when_no_pr_metadata() -> None:
    summary = thread_api._thread_summary(_thread_with_metadata({"title": "No PR"}))

    assert "pr" not in summary
    assert "diffStats" not in summary


def test_thread_summary_exposes_sandbox_id() -> None:
    summary = thread_api._thread_summary(_thread_with_metadata({"sandbox_id": "sb-abc123"}))

    assert summary["sandboxId"] == "sb-abc123"


def test_thread_summary_hides_creating_sandbox_sentinel() -> None:
    summary = thread_api._thread_summary(_thread_with_metadata({"sandbox_id": "__creating__"}))

    assert summary["sandboxId"] is None


async def test_recovery_patch_requires_thread_owner(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {"source": "dashboard", "github_login": "owner", "sandbox_id": "sbx"},
            }

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "intruder")

    assert exc_info.value.status_code == 404


async def test_recovery_patch_requires_sandbox(monkeypatch) -> None:
    async def fake_authorized_thread(thread_id: str, login: str, *, email: str | None = None):
        return {"thread_id": thread_id, "metadata": {"source": "dashboard", "github_login": login}}

    monkeypatch.setattr(thread_api, "_authorized_thread", fake_authorized_thread)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 404
    assert "sandbox" in exc_info.value.detail


async def test_recovery_patch_downloads_generated_patch(monkeypatch) -> None:
    async def fake_authorized_thread(thread_id: str, login: str, *, email: str | None = None):
        return {
            "thread_id": thread_id,
            "metadata": {
                "source": "dashboard",
                "github_login": login,
                "sandbox_id": "sbx",
                "repo_owner": "octo",
                "repo_name": "repo",
                "base_branch": "main",
            },
        }

    class FakeSandbox:
        def execute(self, command: str, *, timeout: int | None = None):
            assert "repo" in command
            assert timeout == thread_api._RECOVERY_PATCH_TIMEOUT_SECONDS
            return SimpleNamespace(
                output=json.dumps({"ok": True, "path": "/tmp/open-swe-tid.patch", "size": 11}),
                exit_code=0,
            )

        def download_files(self, paths: list[str]):
            assert paths == ["/tmp/open-swe-tid.patch"]
            return [SimpleNamespace(content=b"patch bytes")]

    monkeypatch.setattr(thread_api, "_authorized_thread", fake_authorized_thread)
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=FakeSandbox()))

    content, filename = await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert content == b"patch bytes"
    assert filename == "open-swe-tid.patch"


async def test_recovery_patch_rejects_empty_patch(monkeypatch) -> None:
    async def fake_authorized_thread(thread_id: str, login: str, *, email: str | None = None):
        return {"thread_id": thread_id, "metadata": {"sandbox_id": "sbx", "github_login": login}}

    class FakeSandbox:
        def execute(self, command: str, *, timeout: int | None = None):
            return SimpleNamespace(
                output=json.dumps({"ok": True, "path": "/tmp/open-swe-tid.patch", "size": 0}),
                exit_code=0,
            )

    monkeypatch.setattr(thread_api, "_authorized_thread", fake_authorized_thread)
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=FakeSandbox()))

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 404
    assert "changes" in exc_info.value.detail


async def test_recovery_patch_enforces_size_limit(monkeypatch) -> None:
    async def fake_authorized_thread(thread_id: str, login: str, *, email: str | None = None):
        return {"thread_id": thread_id, "metadata": {"sandbox_id": "sbx", "github_login": login}}

    class FakeSandbox:
        def execute(self, command: str, *, timeout: int | None = None):
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "ok": True,
                        "path": "/tmp/open-swe-tid.patch",
                        "size": thread_api._RECOVERY_PATCH_LIMIT_BYTES + 1,
                    }
                ),
                exit_code=0,
            )

    monkeypatch.setattr(thread_api, "_authorized_thread", fake_authorized_thread)
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=FakeSandbox()))

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "octocat")

    assert exc_info.value.status_code == 413


def test_recovery_patch_searches_command_cwd_before_workspace_fallback() -> None:
    command = thread_api._recovery_patch_command(
        {"repo_name": "repo", "base_branch": "main"},
        "tid",
    )

    assert "Path.cwd().resolve()" in command
    assert "WORKSPACE_FALLBACK = Path('/workspace')" in command
    assert "roots = [Path.cwd().resolve(), WORKSPACE_FALLBACK]" in command


async def test_proxy_commands_lazily_creates_missing_thread_only_for_run_start(
    monkeypatch,
) -> None:
    class MissingThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            raise RuntimeError("thread not found")

    class MissingClient:
        threads = MissingThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: MissingClient())

    # A non-run.start command against a thread that doesn't exist yet is a 404.
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "ghost", "octocat", b'{"method": "run.cancel"}'
        )
    assert exc_info.value.status_code == 404


async def test_enrich_run_start_command_attributes_non_owner_message(monkeypatch) -> None:
    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        pass

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "fix the bug"}]}},
    }

    enriched = await thread_api._enrich_run_start_command(
        "tid",
        "teammate",
        command,
        metadata={"source": "dashboard", "github_login": "owner"},
        email="teammate@example.com",
    )

    # A non-owner's message is forwarded but tagged with their login.
    last = enriched["params"]["input"]["messages"][-1]
    assert last["content"] == "@teammate: fix the bug"


async def test_enrich_run_start_command_adds_web_handoff_for_slack_thread(monkeypatch) -> None:
    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        pass

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "continue here"}]}},
    }

    enriched = await thread_api._enrich_run_start_command(
        "tid",
        "teammate",
        command,
        metadata={"source": "slack", "github_login": "owner"},
        email="teammate@example.com",
    )

    content = enriched["params"]["input"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": thread_api.DASHBOARD_HANDOFF_INSTRUCTION}
    assert content[1] == {"type": "text", "text": "@teammate: continue here"}
    assert content[0]["text"].startswith("<open_swe_web_handoff>\n")
    assert content[0]["text"].endswith("\n</open_swe_web_handoff>")


async def test_enrich_run_start_command_adds_web_handoff_before_image_blocks(monkeypatch) -> None:
    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        pass

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "continue here"}],
                    }
                ]
            }
        },
    }

    enriched = await thread_api._enrich_run_start_command(
        "tid",
        "teammate",
        command,
        metadata={"source": "slack", "github_login": "owner"},
        email="teammate@example.com",
    )

    content = enriched["params"]["input"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": thread_api.DASHBOARD_HANDOFF_INSTRUCTION}
    assert content[1] == {"type": "text", "text": "@teammate:"}
    assert content[2] == {"type": "text", "text": "continue here"}


async def test_enrich_run_start_command_does_not_attribute_owner_message(monkeypatch) -> None:
    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        pass

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "fix the bug"}]}},
    }

    enriched = await thread_api._enrich_run_start_command(
        "tid",
        "owner",
        command,
        metadata={"source": "dashboard", "github_login": "owner"},
        email="owner@example.com",
    )

    last = enriched["params"]["input"]["messages"][-1]
    assert last["content"] == "fix the bug"


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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)

    command = {
        "method": "run.start",
        "params": {
            "config": {
                "configurable": {
                    "github_login": "attacker",
                    "user_email": "attacker@example.com",
                    "source": "github",
                    "repo": {"owner": "evil", "name": "repo"},
                    "agent_model_id": _VISION_MODEL,
                    "agent_effort": "medium",
                }
            }
        },
    }

    enriched = await thread_api._enrich_run_start_command(
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
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"
    assert updates[-1]["model"] == _VISION_MODEL


async def test_proxy_run_start_from_slack_thread_updates_trace_reply(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {
                "thread_id": "tid",
                "metadata": {
                    "source": "slack",
                    "github_login": "octocat",
                    "source_context": {
                        "slack_thread": {
                            "channel_id": "C1",
                            "thread_ts": "123.45",
                            "trace_message_ts": "123.46",
                        }
                    },
                },
                "status": "idle",
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates = cast(list[dict[str, object]], captured.setdefault("updates", []))
            updates.append(metadata)

    class FakeClient:
        threads = FakeThreads()

    class FakeResponse:
        status_code = 200
        content = b'{"run_id":"run-1"}'
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> FakeResponse:
            captured["url"] = url
            captured["outgoing"] = json.loads(content)
            return FakeResponse()

    async def fake_get_profile(login: str) -> dict[str, object]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        pass

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        return f"{login}@example.com"

    async def fake_update_trace_reply(channel_id: str, message_ts: str, thread_id: str) -> bool:
        captured["handoff_update"] = {
            "channel_id": channel_id,
            "message_ts": message_ts,
            "thread_id": thread_id,
        }
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)
    monkeypatch.setattr(thread_api.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        thread_api, "update_slack_trace_reply_for_web_handoff", fake_update_trace_reply
    )

    status, body, _ = await thread_api.proxy_dashboard_thread_commands(
        "tid",
        "octocat",
        b'{"method":"run.start","params":{"input":{"messages":[{"role":"user","content":"continue here"}]}}}',
    )

    assert status == 200
    assert body == b'{"run_id":"run-1"}'
    outgoing = captured["outgoing"]
    assert isinstance(outgoing, dict)
    content = outgoing["params"]["input"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": thread_api.DASHBOARD_HANDOFF_INSTRUCTION}
    assert content[1] == {"type": "text", "text": "continue here"}
    assert captured["handoff_update"] == {
        "channel_id": "C1",
        "message_ts": "123.46",
        "thread_id": "tid",
    }


async def test_proxy_commands_rejects_non_object_body(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "github_login": "octocat"},
            }

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands("tid", "octocat", b"[]")

    assert exc_info.value.status_code == 400


async def test_proxy_commands_non_run_start_by_non_owner_is_rejected(monkeypatch) -> None:
    """Non-owners may only post via the attributed run.start path; other write
    commands (e.g. input.respond) carry unattributed input and stay owner-only."""

    class OwnedThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

    class OwnedClient:
        threads = OwnedThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: OwnedClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "tid", "intruder", b'{"method": "input.respond"}'
        )
    assert exc_info.value.status_code == 404


async def test_run_cancel_enforces_thread_ownership(monkeypatch) -> None:
    """Cancelling a run still requires thread ownership (it is not "posting")."""

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_run_cancel("tid", "run-1", "intruder")
    assert exc_info.value.status_code == 404


async def test_read_endpoints_accessible_by_non_owner(monkeypatch) -> None:
    """Read endpoints (state, stream, history) are accessible by any org member."""

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {
                "thread_id": "tid",
                "metadata": {"source": "slack", "github_login": "owner"},
            }

        async def get_state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": []}}

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    # Read endpoints succeed for non-owners (org members).
    state = await thread_api.get_dashboard_thread_state("tid", "teammate")
    assert "values" in state

    # stream/events preflight should not raise.
    await thread_api.proxy_dashboard_thread_stream_events(
        "tid", "teammate", b"{}", content_type="application/json"
    )

    # history preflight should not raise; mock the proxied HTTP call.
    class FakeResponse:
        status_code = 200
        content = b"{}"
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, *a: object, **kw: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(thread_api.httpx, "AsyncClient", FakeAsyncClient)
    await thread_api.proxy_dashboard_thread_history("tid", "teammate", b"{}")


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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_state("tid", "owner")
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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_thread_active_status", unknown_activity)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid",
            "octocat",
            thread_api.ThreadMessageBody(content="hello"),
        )

    assert exc_info.value.status_code == 502


async def test_send_dashboard_message_attributes_non_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def active(thread_id: str) -> bool:
        return True

    async def fake_queue(thread_id: str, payload: dict[str, object]) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_thread_active_status", active)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue)

    await thread_api.send_dashboard_message(
        "tid",
        "teammate",
        thread_api.ThreadMessageBody(content="ship it"),
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "@teammate: ship it"


async def test_send_dashboard_message_does_not_attribute_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": "tid",
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            pass

    class FakeClient:
        threads = FakeThreads()

    async def active(thread_id: str) -> bool:
        return True

    async def fake_queue(thread_id: str, payload: dict[str, object]) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "get_thread_active_status", active)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue)

    await thread_api.send_dashboard_message(
        "tid",
        "owner",
        thread_api.ThreadMessageBody(content="ship it"),
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"


def test_thread_summary_exposes_resolved_state() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "tid",
            "metadata": {
                "source": "dashboard",
                "github_login": "octocat",
                "resolved": True,
                "resolved_at_ms": 1700,
            },
        }
    )

    assert summary["resolved"] is True
    assert summary["resolvedAt"] == 1700


def test_thread_summary_defaults_to_not_resolved() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "dashboard", "github_login": "octocat"}}
    )

    assert summary["resolved"] is False
    assert summary["resolvedAt"] is None


def test_thread_summary_is_owner_true_for_matching_login() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
        owner_login="octocat",
    )

    assert summary["isOwner"] is True


def test_thread_summary_is_owner_false_for_non_owner() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
        owner_login="teammate",
    )

    assert summary["isOwner"] is False


def test_thread_summary_is_owner_true_for_matching_email() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "tid",
            "metadata": {
                "source": "slack",
                "github_login": "octocat",
                "triggering_user_email": "octo@example.com",
            },
        },
        owner_login="someone-else",
        owner_email="OCTO@example.com",
    )

    assert summary["isOwner"] is True


def test_thread_summary_is_owner_defaults_true_without_owner_login() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
    )

    assert summary["isOwner"] is True


async def test_resolve_dashboard_thread_marks_resolved(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {"source": "dashboard", "github_login": "octocat"},
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=True)

    assert updates[-1]["resolved"] is True
    assert isinstance(updates[-1]["resolved_at_ms"], int)
    assert summary["resolved"] is True


async def test_resolve_dashboard_thread_clears_resolved(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {
                    "source": "dashboard",
                    "github_login": "octocat",
                    "resolved": True,
                    "resolved_at_ms": 1700,
                },
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=False)

    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None
    assert summary["resolved"] is False


async def test_resolve_dashboard_thread_enforces_ownership(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.resolve_dashboard_thread("tid", "intruder", resolved=True)
    assert exc_info.value.status_code == 404


async def test_enrich_run_start_command_unresolves_thread(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    _patch_new_thread_deps(monkeypatch, profile={})
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    async def fake_build(thread_id, login, metadata, *, overrides):
        return {"github_login": login, "source": "dashboard"}

    monkeypatch.setattr(thread_api, "_build_dashboard_configurable", fake_build)

    command = {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "follow up"}]},
            "config": {"configurable": {}},
        },
    }

    await thread_api._enrich_run_start_command(
        "tid",
        "octocat",
        command,
        metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "resolved": True,
            "resolved_at_ms": 1700,
        },
    )

    assert updates, "expected metadata update to clear resolved state"
    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None


def test_summary_matches_filters() -> None:
    summary = {
        "resolved": True,
        "viewed": False,
        "source": "github",
        "status": "finished",
        "title": "Fix the flaky test",
    }

    assert thread_api._summary_matches_filters(
        summary, resolved=True, viewed=None, source=None, status=None, query=None
    )
    assert not thread_api._summary_matches_filters(
        summary, resolved=False, viewed=None, source=None, status=None, query=None
    )
    assert thread_api._summary_matches_filters(
        summary, resolved=None, viewed=None, source="github", status=None, query="flaky"
    )
    assert not thread_api._summary_matches_filters(
        summary, resolved=None, viewed=None, source=None, status=None, query="missing"
    )


def test_metadata_matches_filters() -> None:
    metadata = {"source": "dashboard", "title": "Fix login bug", "resolved": True}

    assert thread_api._metadata_matches_filters(metadata, resolved=True, source=None, query=None)
    assert not thread_api._metadata_matches_filters(
        metadata, resolved=False, source=None, query=None
    )
    assert thread_api._metadata_matches_filters(
        metadata, resolved=None, source="dashboard", query="login"
    )
    assert not thread_api._metadata_matches_filters(
        metadata, resolved=None, source="github", query=None
    )


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
    page_size = thread_api._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 50, resolved_before=page_size)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    offsets: list[int] = []
    run_list_calls = 0

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            offsets.append(offset)
            assert select == thread_api._THREAD_LIST_SELECT
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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, resolved=False
    )

    assert result["hasMore"] is True
    assert len(result["items"]) == 25
    assert all(item["resolved"] is False for item in result["items"])
    assert page_size in offsets
    assert run_list_calls == 0


async def test_list_dashboard_threads_sidebar_fills_buckets_with_one_endpoint(monkeypatch) -> None:
    page_size = thread_api._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 10, resolved_before=page_size)
    searches: list[dict[str, object]] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            searches.append({"metadata": metadata, "offset": offset})
            assert select == thread_api._THREAD_LIST_SELECT
            return threads[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            return None

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat", email=None, active_limit=5, resolved_limit=5
    )

    assert len(result["active"]["items"]) == 5
    assert len(result["resolved"]["items"]) == 5
    assert result["active"]["hasMore"] is True
    assert result["resolved"]["hasMore"] is True
    assert {call["offset"] for call in searches} == {0, page_size}


async def test_list_dashboard_threads_page_refreshes_only_unsettled_threads(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    cast(dict[str, object], threads[0]["metadata"])["latest_run_status"] = "success"
    cast(dict[str, object], threads[1]["metadata"])["latest_run_status"] = "pending"
    cast(dict[str, object], threads[2]["metadata"])["latest_run_status"] = "error"
    run_list_thread_ids: list[str] = []
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            return threads[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            updates.append({"thread_id": thread_id, "metadata": metadata})

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            run_list_thread_ids.append(thread_id)
            return [{"id": "run-1", "status": "success"}]

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_page("octocat", email=None, limit=3, offset=0)

    assert run_list_thread_ids == ["t1"]
    assert updates == [
        {
            "thread_id": "t1",
            "metadata": {"latest_run_status": "success", "latest_run_id": "run-1"},
        }
    ]
    assert [item["status"] for item in result["items"]] == ["finished", "finished", "error"]


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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, status="finished"
    )

    assert {item["id"] for item in result["items"]} == {"t0"}
    assert result["items"][0]["status"] == "finished"
    assert set(run_list_thread_ids) == {"t0", "t1"}


@pytest.mark.asyncio
async def test_get_my_profile_preserves_gpt_5_5_models() -> None:
    with patch(
        "agent.dashboard.routes.get_profile",
        new_callable=AsyncMock,
        return_value={
            "default_model": "openai:gpt-5.5",
            "reasoning_effort": "medium",
            "default_subagent_model": "openai:gpt-5.5",
            "subagent_reasoning_effort": "low",
        },
    ):
        payload = await routes.get_my_profile({"sub": "octocat"})

    assert payload["default_model"] == "openai:gpt-5.5"
    assert payload["reasoning_effort"] == "medium"
    assert payload["default_subagent_model"] == "openai:gpt-5.5"
    assert payload["subagent_reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_options_omits_fable_when_disabled() -> None:
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
    ):
        payload = await routes.options()
    assert _FABLE not in [m["id"] for m in payload["models"]]


@pytest.mark.asyncio
async def test_options_includes_fable_when_enabled() -> None:
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
    ):
        payload = await routes.options()
    assert _FABLE in [m["id"] for m in payload["models"]]
    gpt_5_5 = next(m for m in payload["models"] if m["id"] == _VISION_MODEL)
    assert gpt_5_5["context_window"] == 1_050_000


@pytest.mark.asyncio
async def test_options_gates_stale_fable_default_when_disabled() -> None:
    # A stale Fable team default must not be advertised as the default while Fable
    # is omitted from the selectable list, or the Cloud Agents page would offer a
    # default that PUT /profile then rejects.
    fable_pair = (_FABLE, "high")
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=fable_pair,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=fable_pair,
        ),
    ):
        payload = await routes.options()
    model_ids = [m["id"] for m in payload["models"]]
    assert _FABLE not in model_ids
    assert payload["default_agent_model"] != _FABLE
    assert payload["default_agent_subagent_model"] != _FABLE
    assert payload["default_agent_model"] in model_ids
    assert payload["default_agent_subagent_model"] in model_ids


async def test_admin_cancel_dashboard_thread_interrupts_all_active_runs(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {
            "title": "Runaway thread",
            "latest_run_status": "running",
            "updated_at_ms": 1,
        },
    }

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "thread-1"
            return thread

        async def update(self, **kwargs: object) -> None:
            calls.append(("update", kwargs))
            metadata = kwargs["metadata"]
            assert isinstance(metadata, dict)
            thread["metadata"].update(metadata)

    class FakeRuns:
        async def cancel_many(self, **kwargs: object) -> None:
            calls.append(("cancel_many", kwargs))

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert calls[0] == (
        "cancel_many",
        {"thread_id": "thread-1", "status": "all", "action": "interrupt"},
    )
    assert calls[1][0] == "update"
    assert thread["metadata"]["latest_run_status"] == "interrupted"
    assert result["id"] == "thread-1"


async def test_admin_cancel_dashboard_thread_does_not_update_on_cancel_failure(monkeypatch) -> None:
    updated = False

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "status": "busy", "metadata": {}}

        async def update(self, **kwargs: object) -> None:
            nonlocal updated
            updated = True

    class FakeRuns:
        async def cancel_many(self, **kwargs: object) -> None:
            raise RuntimeError("runtime unavailable")

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert exc_info.value.status_code == 502
    assert updated is False


async def test_admin_cancel_thread_route_delegates_without_owner_identity(monkeypatch) -> None:
    cancel = AsyncMock(return_value={"id": "thread-1", "status": "interrupted"})
    monkeypatch.setattr(routes, "admin_cancel_dashboard_thread", cancel)

    result = await routes.admin_cancel_thread("thread-1", _admin={"sub": "admin"})

    assert result == {"id": "thread-1", "status": "interrupted"}
    cancel.assert_awaited_once_with("thread-1")


def test_admin_cancel_thread_dependency_rejects_non_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    with pytest.raises(HTTPException) as exc_info:
        routes._require_admin({"sub": "not-admin", "email": "user@example.com"})

    assert exc_info.value.status_code == 403
