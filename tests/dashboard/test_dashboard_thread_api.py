import base64
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import routes, thread_api
from agent.dashboard.agent_overrides import resolve_agent_model_id
from agent.dashboard.options import model_supports_images
from agent.dashboard.ttft import AssistantTextObservation

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.6-sol"
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
        "anthropic:claude-opus-5",
        "high",
    )

    assert (model_id, effort) == ("anthropic:claude-opus-5", "high")


async def test_resolve_agent_model_choice_migrates_deprecated_request_model(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {},
        "openai:gpt-5.5",
        "high",
    )

    assert (model_id, effort) == ("openai:gpt-5.6-sol", "high")


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

    model_id = await resolve_agent_model_id(None, per_thread_model_id="anthropic:claude-opus-5")
    assert model_id == "anthropic:claude-opus-5"


async def test_resolve_agent_model_id_migrates_deprecated_per_thread_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None, per_thread_model_id="openai:gpt-5.5")
    assert model_id == "openai:gpt-5.6-sol"


def _install_client(monkeypatch, **kwargs: Any) -> FakeLangGraphClient:
    client = FakeLangGraphClient(**kwargs)
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    return client


def _stamped(client: FakeLangGraphClient, thread_id: str = "new-tid") -> dict[str, object]:
    """The metadata the thread ended up with, after create plus any updates."""
    return client.threads.threads[thread_id]["metadata"]


def _admin_thread_metadata() -> dict[str, object]:
    return {
        "source": "dashboard",
        "github_login": "workspace-admin",
        "admin_thread": True,
    }


def _patch_enrich_deps(monkeypatch, *, profile: dict[str, object] | None = None) -> None:
    """Stub the per-user lookups ``_enrich_run_start_command`` makes."""

    async def fake_profile(login: str) -> dict[str, object]:
        return dict(profile or {})

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, prof: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "get_profile", fake_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "resolve_run_email", fake_resolve_email)


def _patch_new_thread_deps(monkeypatch, *, profile: dict[str, object]) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    _patch_enrich_deps(monkeypatch, profile=profile)
    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)


async def test_enrich_run_start_command_creates_and_stamps_new_thread(monkeypatch) -> None:
    _patch_new_thread_deps(monkeypatch, profile={})
    client = _install_client(monkeypatch)

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

    stamped = _stamped(client)
    assert stamped["source"] == "dashboard"
    assert stamped["origin"] == "dashboard"
    assert stamped["thread_category"] == "interactive"
    assert stamped["trigger_kind"] == "user"
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
    assert configurable["prepare_run_id"] == enriched["params"]["metadata"]["prepare_run_id"]
    assert configurable["prepare_run_id"]
    # Dashboard-only creation hints must not leak into the run config.
    assert "repo_explicitly_none" not in configurable
    assert enriched["params"]["assistant_id"] == "agent"


async def test_enrich_run_start_command_uses_vision_fallback_for_text_only_model(
    monkeypatch,
) -> None:
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    client = _install_client(monkeypatch)

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

    stamped = _stamped(client)
    assert stamped["model"] == _VISION_MODEL
    assert stamped["effort"] == "medium"
    assert stamped["resolved_model"] == _VISION_MODEL
    assert stamped["resolved_effort"] == "medium"
    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"


def _thread_with_metadata(metadata: dict) -> dict:
    return {"thread_id": "t1", "status": "idle", "metadata": metadata}


async def test_thread_summary_includes_pr_and_diff_stats() -> None:
    summary = await thread_api._thread_summary(
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
    assert summary["pullRequests"][0]["repoFullName"] == "langchain-ai/open-swe"


async def test_thread_summary_includes_pull_requests_across_repositories() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "repo_full_name": "langchain-ai/open-swe",
                "title": "Cross-repo change",
                "pull_requests": [
                    {
                        "repo_full_name": "langchain-ai/open-swe",
                        "number": 42,
                        "url": "https://github.com/langchain-ai/open-swe/pull/42",
                        "title": "feat: dashboard",
                        "state": "draft",
                        "head_ref": "feature/dashboard",
                        "base_ref": "main",
                        "author": "octocat",
                        "author_avatar_url": "https://avatars.example/octocat.png",
                        "created_at": "2026-08-18T10:00:00Z",
                        "diff_stats": {"files": 3, "additions": 10, "deletions": 2},
                    },
                    {
                        "repo_full_name": "langchain-ai/langchain",
                        "number": 9,
                        "url": "https://github.com/langchain-ai/langchain/pull/9",
                        "title": "feat: integration",
                        "state": "open",
                        "head_ref": "feature/integration",
                        "base_ref": "master",
                        "author": "hubot",
                        "diff_stats": {"files": 1, "additions": 4, "deletions": 0},
                    },
                ],
            }
        )
    )

    assert [item["repoFullName"] for item in summary["pullRequests"]] == [
        "langchain-ai/open-swe",
        "langchain-ai/langchain",
    ]
    assert summary["pr"] == {
        "number": 9,
        "title": "feat: integration",
        "state": "open",
        "headRef": "feature/integration",
        "baseRef": "master",
        "url": "https://github.com/langchain-ai/langchain/pull/9",
    }
    assert summary["diffStats"] == {"files": 1, "additions": 4, "deletions": 0}


async def test_thread_summary_uses_configured_repo_for_display() -> None:
    metadata = {
        "repo": {"owner": "trusted", "name": "default"},
        "working_repo_full_name": "observed/checkout",
    }

    summary = await thread_api._thread_summary(_thread_with_metadata(metadata))

    assert summary["repo"] == "default"
    assert summary["repoFullName"] == "trusted/default"
    assert "workingRepoFullName" not in summary
    assert metadata["repo"] == {"owner": "trusted", "name": "default"}


async def test_thread_summary_defaults_unknown_pr_state_to_open() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "pr_number": 7,
                "pr_url": "https://example.com/pull/7",
                "pr_state": "bogus",
            }
        )
    )

    assert summary["pr"]["state"] == "open"


async def test_thread_summary_omits_pr_when_no_pr_metadata() -> None:
    summary = await thread_api._thread_summary(_thread_with_metadata({"title": "No PR"}))

    assert "pr" not in summary
    assert "diffStats" not in summary


async def test_thread_summary_exposes_sandbox_id() -> None:
    summary = await thread_api._thread_summary(_thread_with_metadata({"sandbox_id": "sb-abc123"}))

    assert summary["sandboxId"] == "sb-abc123"


async def test_thread_summary_hides_creating_sandbox_sentinel() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata({"sandbox_id": "__creating__"})
    )

    assert summary["sandboxId"] is None


async def test_terminal_sandbox_requires_owner_and_existing_sandbox(monkeypatch) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "owner",
        "sandbox_id": "sandbox-123",
        "repo_name": "repo",
    }

    _install_client(monkeypatch, thread_metadata=metadata)

    assert await thread_api.get_dashboard_terminal_sandbox("tid", "owner") == (
        "sandbox-123",
        "repo",
    )
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_terminal_sandbox("tid", "intruder")
    assert exc_info.value.status_code == 404

    metadata["sandbox_id"] = "__creating__"
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_terminal_sandbox("tid", "owner")
    assert exc_info.value.status_code == 404


async def test_thread_summary_includes_slack_source_url_for_private_repo() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "repo_private": True,
                "source_context": {"slack_thread": {"permalink": "https://slack.example/thread"}},
            }
        )
    )

    assert summary["sourceUrl"] == "https://slack.example/thread"


async def test_thread_summary_omits_slack_source_url_for_public_repo() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "repo_private": False,
                "source_context": {"slack_thread": {"permalink": "https://slack.example/thread"}},
            }
        )
    )

    assert summary["sourceUrl"] is None


async def test_recovery_patch_requires_thread_owner(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={"source": "dashboard", "github_login": "owner", "sandbox_id": "sbx"},
    )

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
        async def aexecute(self, command: str, *, timeout: int | None = None):
            assert "repo" in command
            assert timeout == thread_api._RECOVERY_PATCH_TIMEOUT_SECONDS
            return SimpleNamespace(
                output=json.dumps({"ok": True, "path": "/tmp/open-swe-tid.patch", "size": 11}),
                exit_code=0,
            )

        async def adownload_files(self, paths: list[str]):
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
        async def aexecute(self, command: str, *, timeout: int | None = None):
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
        async def aexecute(self, command: str, *, timeout: int | None = None):
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
    # No thread is seeded, so every ``get`` 404s the way the platform would.
    _install_client(monkeypatch)

    # A non-run.start command against a thread that doesn't exist yet is a 404.
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "ghost", "octocat", b'{"method": "run.cancel"}'
        )
    assert exc_info.value.status_code == 404


async def test_enrich_run_start_command_attributes_non_owner_message(monkeypatch) -> None:
    client = _install_client(monkeypatch)
    _patch_enrich_deps(monkeypatch)

    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"role": "user", "content": "fix the bug"}]}},
    }

    enriched = await thread_api._enrich_run_start_command(
        "tid",
        "teammate",
        command,
        metadata={
            "source": "dashboard",
            "github_login": "owner",
            "participant_logins": ["owner"],
        },
        email="teammate@example.com",
    )

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:teammate"
    assert last.findtext("content") == "fix the bug"
    assert client.threads.updates[-1]["participant_logins"] == ["owner", "teammate"]
    assert {call["thread_id"] for _, call in client.calls} == {"tid"}


async def test_enrich_run_start_command_adds_web_handoff_for_slack_thread(monkeypatch) -> None:
    _install_client(monkeypatch, messages=[{"id": "existing-message"}])
    _patch_enrich_deps(monkeypatch)

    command = {
        "method": "run.start",
        "params": {
            "input": {
                "messages": [{"role": "user", "content": "continue here", "id": "existing-message"}]
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

    messages = enriched["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    user_message = ElementTree.fromstring(messages[-1]["content"])
    assert handoff.attrib == {
        "sender": "system:dashboard-handoff",
        "surface": "automation",
        "kind": "system",
    }
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    assert user_message.attrib["sender"] == "github:teammate"
    assert user_message.findtext("content") == "continue here"
    assert "id" not in messages[-1]
    assert enriched["params"]["config"]["configurable"]["source"] == "dashboard"


async def test_enrich_run_start_command_adds_web_handoff_before_image_blocks(monkeypatch) -> None:
    _install_client(monkeypatch)
    _patch_enrich_deps(monkeypatch)

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

    messages = enriched["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    content = messages[-1]["content"]
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    user_message = ElementTree.fromstring(content[0]["text"])
    assert user_message.attrib["sender"] == "github:teammate"
    assert user_message.findtext("content") == "continue here"


async def test_enrich_run_start_command_does_not_attribute_owner_message(monkeypatch) -> None:
    _install_client(monkeypatch)
    _patch_enrich_deps(monkeypatch)

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

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:owner"
    assert last.findtext("content") == "fix the bug"


async def test_enrich_run_start_command_allowlists_client_configurable(monkeypatch) -> None:
    client = _install_client(monkeypatch)

    async def fake_get_profile(login: str) -> dict[str, object]:
        assert login == "octocat"
        return {}

    async def fake_ensure_token(login: str) -> None:
        assert login == "octocat"

    async def fake_resolve_email(login: str, profile: dict[str, object]) -> str:
        assert login == "octocat"
        return "octocat@example.com"

    monkeypatch.setattr(thread_api, "get_profile", fake_get_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "resolve_run_email", fake_resolve_email)

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
    assert client.threads.updates[-1]["model"] == _VISION_MODEL


async def test_proxy_run_start_from_slack_thread_updates_trace_reply(monkeypatch) -> None:
    captured: dict[str, object] = {}

    client = _install_client(
        monkeypatch,
        thread_metadata={
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
    )

    class FakeResponse:
        status_code = 200
        content = b'{"type":"success","id":1,"result":{"run_id":"run-1"}}'
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

    async def fake_update_trace_reply(channel_id: str, message_ts: str, thread_id: str) -> bool:
        captured["handoff_update"] = {
            "channel_id": channel_id,
            "message_ts": message_ts,
            "thread_id": thread_id,
        }
        return True

    _patch_enrich_deps(monkeypatch)
    monkeypatch.setattr(thread_api, "_now_ms", lambda: 123_456)
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
    assert body == b'{"type":"success","id":1,"result":{"run_id":"run-1"}}'
    outgoing = captured["outgoing"]
    assert isinstance(outgoing, dict)
    messages = outgoing["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    user_message = ElementTree.fromstring(messages[-1]["content"])
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    assert user_message.findtext("content") == "continue here"
    assert captured["handoff_update"] == {
        "channel_id": "C1",
        "message_ts": "123.46",
        "thread_id": "tid",
    }
    outgoing_params = outgoing["params"]
    assert outgoing_params["metadata"]["dashboard_ttft_started_at_ms"] == 123_456
    assert client.threads.updates[-1] == {
        "latest_run_id": "run-1",
        "latest_run_status": "pending",
        "updated_at_ms": 123_456,
    }


async def test_run_ttft_observer_records_first_assistant_text(
    monkeypatch,
) -> None:
    def event(
        method: str,
        data: dict[str, object],
        *,
        namespace: list[str],
        event_id: str,
    ) -> bytes:
        payload = {
            "type": "event",
            "event_id": event_id,
            "method": method,
            "params": {"namespace": namespace, "timestamp": 2_250, "data": data},
        }
        return f"event: {method}\r\ndata: {json.dumps(payload)}\r\n\r\n".encode()

    stream_bytes = event(
        "messages",
        {"event": "message-start", "role": "ai"},
        namespace=["agent"],
        event_id="1-0",
    ) + event(
        "messages",
        {
            "event": "content-block-delta",
            "delta": {"type": "text-delta", "text": "Hello"},
        },
        namespace=["agent"],
        event_id="2-0",
    )
    chunks = [stream_bytes[:35], stream_bytes[35:]]

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self):
            for chunk in chunks:
                yield chunk

    class FakeStreamContext:
        async def __aenter__(self) -> FakeResponse:
            return FakeResponse()

        async def __aexit__(self, *args: object) -> None:
            pass

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def stream(self, method: str, url: str, **kwargs: object) -> FakeStreamContext:
            assert method == "GET"
            assert url.endswith("/threads/thread-1/runs/run-1/stream")
            assert kwargs["headers"] == {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Last-Event-ID": "-1",
            }
            assert kwargs["params"] == {"stream_mode": "messages"}
            return FakeStreamContext()

    record = AsyncMock()
    monkeypatch.setattr(thread_api.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(thread_api, "record_dashboard_thread_ttft", record)

    await thread_api._observe_dashboard_run_ttft("thread-1", "run-1", 1_000)

    record.assert_awaited_once_with(
        AssistantTextObservation(run_id="run-1", event_timestamp_ms=2_250),
        thread_id="thread-1",
        started_at_ms=1_000,
    )


async def test_proxy_commands_rejects_non_object_body(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands("tid", "octocat", b"[]")

    assert exc_info.value.status_code == 400


async def test_proxy_commands_non_run_start_by_non_owner_is_rejected(monkeypatch) -> None:
    """Non-owners may only post via the attributed run.start path; other write
    commands (e.g. input.respond) carry unattributed input and stay owner-only."""

    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "tid", "intruder", b'{"method": "input.respond"}'
        )
    assert exc_info.value.status_code == 404


async def test_proxy_commands_rejects_non_admin_on_admin_thread(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    _install_client(monkeypatch, thread_metadata=_admin_thread_metadata())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "tid", "teammate", b'{"method": "run.start"}'
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in admin threads"


async def test_proxy_commands_preserves_admin_writes_and_owner_reads(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        content = b"{}"
        headers: dict[str, str] = {}

    posted: list[bytes] = []

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> FakeResponse:
            posted.append(content)
            return FakeResponse()

    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin,another-admin")
    _install_client(monkeypatch, thread_metadata=_admin_thread_metadata())
    monkeypatch.setattr(thread_api.httpx, "AsyncClient", FakeAsyncClient)

    status_code, _, _ = await thread_api.proxy_dashboard_thread_commands(
        "tid", "another-admin", b'{"method": "input.respond"}'
    )

    assert status_code == 200

    monkeypatch.setenv("CONFIGURED_ADMINS", "another-admin")
    status_code, _, _ = await thread_api.proxy_dashboard_thread_commands(
        "tid", "workspace-admin", b'{"method": "agent.getTree"}'
    )

    assert status_code == 200
    assert posted == [
        b'{"method": "input.respond"}',
        b'{"method": "agent.getTree"}',
    ]


async def test_run_cancel_enforces_thread_ownership(monkeypatch) -> None:
    """Cancelling a run still requires thread ownership (it is not "posting")."""

    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_run_cancel("tid", "run-1", "intruder")
    assert exc_info.value.status_code == 404


async def test_read_endpoints_accessible_by_non_owner(monkeypatch) -> None:
    """Read endpoints (state, stream, history) are accessible by any org member."""

    _install_client(monkeypatch, thread_metadata={"source": "slack", "github_login": "owner"})

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

        async def post(
            self, url: str, *, json: dict[str, object], headers: dict[str, str]
        ) -> FakeResponse:
            posted.append(json)
            return FakeResponse()

    posted: list[dict[str, object]] = []
    monkeypatch.setattr(thread_api.httpx, "AsyncClient", FakeAsyncClient)
    await thread_api.proxy_dashboard_thread_history("tid", "teammate", b'{"limit": 20}')
    await thread_api.proxy_dashboard_thread_history(
        "tid", "teammate", b'{"limit": 20, "metadata": {"run_id": "run-1"}}'
    )
    assert posted == [
        {"limit": thread_api._DISCOVERY_HISTORY_LIMIT},
        {"limit": 20, "metadata": {"run_id": "run-1"}},
    ]
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_history("tid", "teammate", b"\xff")
    assert exc_info.value.status_code == 400


async def test_thread_state_uses_current_run_status_when_checkpoint_is_stale(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "owner",
            "latest_run_status": "success",
        },
        state={"values": {"messages": []}, "next": []},
        runs=[{"run_id": "run-1", "status": "running"}],
    )

    state = await thread_api.get_dashboard_thread_state("tid", "owner")

    assert "next" not in state


async def test_read_endpoints_reject_non_surfaced_source(monkeypatch) -> None:
    """Threads with an unknown source are not readable by anyone."""
    _install_client(
        monkeypatch, thread_metadata={"source": "unknown-source", "github_login": "owner"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_state("tid", "owner")
    assert exc_info.value.status_code == 404


async def test_send_dashboard_message_returns_502_when_activity_unknown(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})

    async def unknown_activity(thread_id: str) -> None:
        assert thread_id == "tid"
        return None

    monkeypatch.setattr(thread_api, "get_thread_active_status", unknown_activity)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid",
            "octocat",
            thread_api.ThreadMessageBody(content="hello"),
        )

    assert exc_info.value.status_code == 502


async def test_send_dashboard_message_rejects_non_admin_on_admin_thread(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    client = _install_client(monkeypatch, thread_metadata=_admin_thread_metadata())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid",
            "teammate",
            thread_api.ThreadMessageBody(content="ship it"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in admin threads"
    assert client.threads.updates == []


def test_assert_thread_postable_allows_configured_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")

    thread_api._assert_thread_postable(
        {"source": "dashboard", "admin_thread": True},
        "workspace-admin",
    )


async def test_send_dashboard_message_attributes_non_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    async def active(thread_id: str) -> bool:
        return True

    async def fake_queue(thread_id: str, payload: dict[str, object]) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(thread_api, "get_thread_active_status", active)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue)

    await thread_api.send_dashboard_message(
        "tid",
        "teammate",
        thread_api.ThreadMessageBody(content="ship it"),
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"
    assert cast(dict[str, object], payload["sender"])["id"] == "github:teammate"


async def test_send_dashboard_message_does_not_attribute_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    async def active(thread_id: str) -> bool:
        return True

    async def fake_queue(thread_id: str, payload: dict[str, object]) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(thread_api, "get_thread_active_status", active)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue)

    await thread_api.send_dashboard_message(
        "tid",
        "owner",
        thread_api.ThreadMessageBody(content="ship it"),
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"


async def test_thread_summary_exposes_resolved_state() -> None:
    summary = await thread_api._thread_summary(
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


async def test_thread_summary_defaults_to_not_resolved() -> None:
    summary = await thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "dashboard", "github_login": "octocat"}}
    )

    assert summary["resolved"] is False
    assert summary["resolvedAt"] is None


async def test_thread_summary_is_owner_true_for_matching_login() -> None:
    summary = await thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
        owner_login="octocat",
    )

    assert summary["isOwner"] is True


async def test_thread_summary_is_owner_false_for_non_owner() -> None:
    summary = await thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
        owner_login="teammate",
    )

    assert summary["isOwner"] is False


async def test_thread_summary_is_owner_true_for_matching_email() -> None:
    summary = await thread_api._thread_summary(
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


async def test_thread_summary_is_owner_defaults_true_without_owner_login() -> None:
    summary = await thread_api._thread_summary(
        {"thread_id": "tid", "metadata": {"source": "slack", "github_login": "octocat"}},
    )

    assert summary["isOwner"] is True


async def test_resolve_dashboard_thread_marks_resolved(monkeypatch) -> None:
    client = _install_client(
        monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"}
    )

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=True)

    updates = client.threads.updates
    assert updates[-1]["resolved"] is True
    assert isinstance(updates[-1]["resolved_at_ms"], int)
    assert summary["resolved"] is True


async def test_resolve_dashboard_thread_clears_resolved(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "resolved": True,
            "resolved_at_ms": 1700,
        },
    )

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=False)

    updates = client.threads.updates
    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None
    assert summary["resolved"] is False


async def test_resolve_dashboard_thread_enforces_ownership(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.resolve_dashboard_thread("tid", "intruder", resolved=True)
    assert exc_info.value.status_code == 404


async def test_enrich_run_start_command_unresolves_thread(monkeypatch) -> None:
    _patch_new_thread_deps(monkeypatch, profile={})
    client = _install_client(monkeypatch)

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

    updates = client.threads.updates
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
    automation = {
        "source": "schedule",
        "schedule_id": "schedule-1",
        "title": "Scheduled cleanup",
    }

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
    assert thread_api._metadata_matches_filters(
        metadata, resolved=None, source=None, query=None, scope="interactive"
    )
    assert not thread_api._metadata_matches_filters(
        automation, resolved=None, source=None, query=None, scope="interactive"
    )
    assert thread_api._metadata_matches_filters(
        automation,
        resolved=None,
        source=None,
        query=None,
        scope="automation",
        automation_id="schedule-1",
    )
    assert not thread_api._metadata_matches_filters(
        automation,
        resolved=None,
        source=None,
        query=None,
        scope="automation",
        automation_id="schedule-2",
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


def _search_offsets(client: FakeLangGraphClient) -> list[int]:
    return [call["offset"] for call in client.threads.searches]


def _assert_list_select(client: FakeLangGraphClient) -> None:
    searches = client.threads.searches
    assert searches
    assert all(call["select"] == thread_api._THREAD_LIST_SELECT for call in searches)


async def test_list_dashboard_threads_page_pages_beyond_first_search_batch(monkeypatch) -> None:
    page_size = thread_api._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 50, resolved_before=page_size)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, resolved=False
    )

    assert result["hasMore"] is True
    assert len(result["items"]) == 25
    assert all(item["resolved"] is False for item in result["items"])
    _assert_list_select(client)
    assert page_size in _search_offsets(client)
    assert client.runs.list_calls == []


async def test_list_dashboard_threads_page_scopes_automation_runs(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    first = cast(dict[str, object], threads[0]["metadata"])
    first.update({"source": "schedule", "schedule_id": "schedule-1"})
    second = cast(dict[str, object], threads[1]["metadata"])
    second.update({"source": "schedule", "schedule_id": "schedule-2"})
    threads.append(
        {
            "thread_id": "other-owner",
            "metadata": {
                "source": "schedule",
                "github_login": "someone-else",
                "schedule_id": "schedule-1",
                "latest_run_status": "success",
                "updated_at_ms": 10,
            },
        }
    )

    _install_client(monkeypatch, threads=threads)

    interactive = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, scope="interactive"
    )
    automation = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, scope="automation", automation_id="schedule-1"
    )

    assert [item["id"] for item in interactive["items"]] == ["t2"]
    assert [item["id"] for item in automation["items"]] == ["t0"]
    assert automation["items"][0]["automationId"] == "schedule-1"


async def test_list_dashboard_threads_page_separates_filter_owner_from_viewer(monkeypatch) -> None:
    threads = [
        {
            "thread_id": "surfaced",
            "metadata": {
                "source": "dashboard",
                "github_login": "other-user",
                "latest_run_status": "success",
                "updated_at_ms": 2,
            },
        },
        {
            "thread_id": "internal",
            "metadata": {
                "source": "reviewer",
                "github_login": "other-user",
                "latest_run_status": "success",
                "updated_at_ms": 1,
            },
        },
    ]
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_api.list_dashboard_threads_page(
        "admin-user",
        email="admin@example.com",
        filter_owner_login="other-user",
        surfaced_only=True,
    )

    assert [call["metadata"] for call in client.threads.searches] == [
        {"github_login": "other-user"}
    ]
    assert [item["id"] for item in result["items"]] == ["surfaced"]
    assert result["items"][0]["ownerLogin"] == "other-user"
    assert result["items"][0]["isOwner"] is False


async def test_list_dashboard_threads_sidebar_fills_buckets_with_one_endpoint(monkeypatch) -> None:
    page_size = thread_api._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 10, resolved_before=page_size)
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat", email=None, active_limit=5, resolved_limit=5
    )

    assert len(result["active"]["items"]) == 5
    assert len(result["resolved"]["items"]) == 5
    assert result["active"]["hasMore"] is True
    assert result["resolved"]["hasMore"] is True
    _assert_list_select(client)
    assert set(_search_offsets(client)) == {0, page_size}


async def test_list_dashboard_threads_sidebar_excludes_automations_before_limiting(
    monkeypatch,
) -> None:
    page_size = thread_api._THREADS_SEARCH_PAGE
    threads = _make_threads(page_size + 5, resolved_before=0)
    for thread in threads[:page_size]:
        metadata = cast(dict[str, object], thread["metadata"])
        metadata["source"] = "schedule"
        metadata["schedule_id"] = f"schedule-{thread['thread_id']}"
    client = _install_client(monkeypatch, threads=threads)

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat", email=None, active_limit=5, resolved_limit=5
    )

    assert [item["id"] for item in result["active"]["items"]] == [
        f"t{index}" for index in range(page_size, page_size + 5)
    ]
    assert set(_search_offsets(client)) == {0, page_size}


async def test_list_dashboard_threads_sidebar_includes_readable_active_thread(
    monkeypatch,
) -> None:
    threads = _make_threads(1, resolved_before=0)
    shared_thread = {
        "thread_id": "shared-thread",
        "metadata": {
            "source": "slack",
            "github_login": "teammate",
            "title": "Teammate thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
            "sandbox_id": "sandbox-123",
        },
    }
    client = _install_client(monkeypatch, threads=threads)
    # Fetchable by id, but not one of the viewer's own threads that search returns.
    client.threads.threads["shared-thread"] = shared_thread

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="shared-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["shared-thread", "t0"]
    shared = result["active"]["items"][0]
    assert shared["isOwner"] is False
    assert shared["sandboxId"] == "sandbox-123"
    _assert_list_select(client)


async def test_list_dashboard_threads_sidebar_keeps_resolved_active_thread_resolved(
    monkeypatch,
) -> None:
    threads = _make_threads(1, resolved_before=0)
    shared_thread = {
        "thread_id": "shared-resolved-thread",
        "metadata": {
            "source": "slack",
            "github_login": "teammate",
            "title": "Resolved teammate thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
            "resolved": True,
            "sandbox_id": "sandbox-456",
        },
    }
    client = _install_client(monkeypatch, threads=threads)
    client.threads.threads["shared-resolved-thread"] = shared_thread

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="shared-resolved-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["t0"]
    assert [item["id"] for item in result["resolved"]["items"]] == ["shared-resolved-thread"]
    shared = result["resolved"]["items"][0]
    assert shared["isOwner"] is False
    assert shared["resolved"] is True
    assert shared["sandboxId"] == "sandbox-456"
    _assert_list_select(client)


async def test_list_dashboard_threads_sidebar_ignores_unreadable_active_thread(
    monkeypatch,
) -> None:
    threads = _make_threads(1, resolved_before=0)
    private_thread = {
        "thread_id": "private-thread",
        "metadata": {
            "source": "internal",
            "github_login": "teammate",
            "title": "Private thread",
            "updated_at_ms": 100,
            "latest_run_status": "success",
        },
    }

    client = _install_client(monkeypatch, threads=threads)
    client.threads.threads["private-thread"] = private_thread

    result = await thread_api.list_dashboard_threads_sidebar(
        "octocat",
        email=None,
        active_limit=5,
        resolved_limit=5,
        active_thread_id="private-thread",
    )

    assert [item["id"] for item in result["active"]["items"]] == ["t0"]
    _assert_list_select(client)


async def test_list_dashboard_threads_page_refreshes_only_unsettled_threads(monkeypatch) -> None:
    threads = _make_threads(3, resolved_before=0)
    cast(dict[str, object], threads[0]["metadata"])["latest_run_status"] = "success"
    cast(dict[str, object], threads[1]["metadata"])["latest_run_status"] = "pending"
    cast(dict[str, object], threads[2]["metadata"])["latest_run_status"] = "error"
    client = _install_client(
        monkeypatch, threads=threads, runs=[{"id": "run-1", "status": "success"}]
    )

    result = await thread_api.list_dashboard_threads_page("octocat", email=None, limit=3, offset=0)

    assert [call["thread_id"] for call in client.runs.list_calls] == ["t1"]
    assert client.threads.update_calls == [
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
    client = _install_client(
        monkeypatch,
        threads=threads,
        runs={
            "t0": [{"id": "run-t0", "status": "success"}],
            "t1": [{"id": "run-t1", "status": "error"}],
        },
    )

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, limit=25, offset=0, status="finished"
    )

    assert {item["id"] for item in result["items"]} == {"t0"}
    assert result["items"][0]["status"] == "finished"
    assert {call["thread_id"] for call in client.runs.list_calls} == {"t0", "t1"}


@pytest.mark.asyncio
async def test_get_my_profile_migrates_deprecated_models() -> None:
    with patch(
        "agent.dashboard.routes.get_profile",
        new_callable=AsyncMock,
        return_value={
            "default_model": "openai:gpt-5.5",
            "reasoning_effort": "medium",
            "default_subagent_model": "anthropic:claude-opus-4-8",
            "subagent_reasoning_effort": "low",
        },
    ):
        payload = await routes.get_my_profile({"sub": "octocat"})

    assert payload["default_model"] == "openai:gpt-5.6-sol"
    assert payload["reasoning_effort"] == "medium"
    assert payload["default_subagent_model"] == "anthropic:claude-opus-5"
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
    openai_model = next(m for m in payload["models"] if m["id"] == _VISION_MODEL)
    assert openai_model["context_window"] == 272_000


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


async def test_turn_diff_prefers_persisted_run_artifact(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "turn_checkpoints": [
            {"key": "msg-1", "ref": "refs/open-swe/turns/msg-1", "started_at": "t0"}
        ],
    }
    stored = {
        "status": "ready",
        "files": [
            {
                "path": f"{index}.py",
                "originalContent": "before",
                "modifiedContent": "after",
            }
            for index in range(3)
        ],
        "truncated": False,
        "summary": {"files": 3, "additions": 3, "deletions": 0},
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr("agent.dashboard.run_diffs.get_run_diff", AsyncMock(return_value=stored))
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff(
        "thread-1", "owner", turn_key="msg-1", max_files=2, include_content=False
    )

    assert result == {
        **stored,
        "files": [
            {**file, "originalContent": None, "modifiedContent": None}
            for file in stored["files"][:2]
        ],
        "truncated": True,
    }
    create_sandbox.assert_not_awaited()


async def test_turn_diff_hides_plan_mode_checkpoint(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "turn_checkpoints": [
            {
                "key": "msg-1",
                "ref": "refs/open-swe/turns/msg-1",
                "started_at": "t0",
                "repo_path": "/workspace/repo",
                "plan_mode": True,
                "plan_ref": "refs/open-swe/turns/msg-1",
            }
        ],
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    assert result == {
        "status": "ready",
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
    }
    create_sandbox.assert_not_awaited()


async def test_turn_diff_preserves_changes_before_mid_run_plan_mode(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "turn_checkpoints": [
            {
                "key": "msg-1",
                "ref": "refs/open-swe/turns/msg-1",
                "started_at": "t0",
                "repo_path": "/workspace/repo",
                "plan_mode": True,
                "plan_ref": "refs/open-swe/turns/msg-1-plan",
            }
        ],
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    sandbox = object()
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-1-plan",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_reads_the_checkpoint_repository(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "turn_checkpoints": [
            {
                "key": "msg-1",
                "ref": "refs/open-swe/turns/msg-1",
                "started_at": "t0",
                "repo_path": "/workspace/repo",
            },
            {
                "key": "msg-2",
                "ref": "refs/open-swe/turns/msg-2",
                "started_at": "t1",
                "repo_path": "/workspace/repo",
            },
        ],
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    sandbox = object()
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-2",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_rejects_checkpoints_from_different_repositories(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "turn_checkpoints": [
            {
                "key": "msg-1",
                "ref": "refs/open-swe/turns/msg-1",
                "started_at": "t0",
                "repo_path": "/workspace/one",
            },
            {
                "key": "msg-2",
                "ref": "refs/open-swe/turns/msg-2",
                "started_at": "t1",
                "repo_path": "/workspace/two",
            },
        ],
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    assert result == {
        "status": "missing",
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
    }
    create_sandbox.assert_not_awaited()


async def test_pr_diff_uses_repository_from_pr_url(monkeypatch) -> None:
    metadata = {
        "repo_owner": "langchain-ai",
        "repo_name": "deepagents",
        "pr_number": 1925,
        "pr_url": "https://github.com/langchain-ai/open-swe/pull/1925",
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(thread_api, "_github_token_for_login", AsyncMock(return_value="token"))
    build_diff = AsyncMock(
        return_value={"base_sha": "base", "head_sha": "head", "truncated": False, "files": []}
    )
    monkeypatch.setattr(thread_api, "build_pr_diff_files", build_diff)

    await thread_api.get_dashboard_thread_pr_diff("thread-1", "owner")

    assert build_diff.await_args is not None
    assert build_diff.await_args.args[1:] == ("langchain-ai/open-swe", 1925)


_ACTIVE_RUNS = [
    {"run_id": "pending-run", "status": "pending"},
    {"run_id": "running-run", "status": "running"},
]
_INTERRUPT_ALL_ACTIVE = {
    "thread_id": "thread-1",
    "run_ids": ["pending-run", "running-run"],
    "action": "interrupt",
}


async def test_cancel_dashboard_thread_interrupts_runs_it_did_not_start(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[
            {
                "thread_id": "thread-1",
                "status": "busy",
                "metadata": {
                    "title": "Slack-triggered thread",
                    "github_login": "owner",
                    "latest_run_status": "running",
                    "updated_at_ms": 1,
                },
            }
        ],
        runs={"thread-1": _ACTIVE_RUNS},
    )

    result = await thread_api.cancel_dashboard_thread("thread-1", "owner")

    assert client.runs.cancelled == [_INTERRUPT_ALL_ACTIVE]
    # Reported as interrupted even though the platform still says busy.
    assert result["status"] == "interrupted"


async def test_cancel_dashboard_thread_rejects_non_owner(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[
            {
                "thread_id": "thread-1",
                "status": "busy",
                "metadata": {"github_login": "owner"},
            }
        ],
        runs={"thread-1": _ACTIVE_RUNS},
    )

    with pytest.raises(HTTPException):
        await thread_api.cancel_dashboard_thread("thread-1", "someone-else")

    assert client.runs.cancelled == []
    assert client.threads.updates == []


async def test_admin_cancel_dashboard_thread_interrupts_all_active_runs(monkeypatch) -> None:
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {
            "title": "Runaway thread",
            "latest_run_status": "running",
            "updated_at_ms": 1,
        },
    }
    client = _install_client(monkeypatch, threads=[thread], runs={"thread-1": _ACTIVE_RUNS})

    result = await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert client.runs.cancelled == [_INTERRUPT_ALL_ACTIVE]
    # The metadata write lands only after the cancel succeeds.
    assert client.call_names.index("threads.update") > client.call_names.index("runs.cancel_many")
    assert thread["metadata"]["latest_run_status"] == "interrupted"
    assert result["id"] == "thread-1"


async def test_admin_cancel_dashboard_thread_does_not_update_on_cancel_failure(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        threads=[{"thread_id": "thread-1", "status": "busy", "metadata": {}}],
        runs={"thread-1": _ACTIVE_RUNS},
    )
    client.runs.cancel_error = RuntimeError("runtime unavailable")

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert exc_info.value.status_code == 502
    assert client.threads.updates == []


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
