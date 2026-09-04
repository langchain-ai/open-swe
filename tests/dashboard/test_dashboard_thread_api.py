import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException

from agent.dashboard import routes, thread_api
from agent.dashboard.agent_overrides import resolve_agent_model_id
from agent.dashboard.options import model_supports_images
from agent.dashboard.ttft import AssistantTextObservation
from agent.media import MediaRef, media_refs_from_content

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.6-sol"
_FABLE = "anthropic:claude-fable-5"
_PAIR = ("openai:gpt-5.6-sol", "medium")


@asynccontextmanager
async def _unlocked(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _empty_thread_pins(monkeypatch) -> None:
    async def empty_pins(login: str) -> list[str]:
        return []

    monkeypatch.setattr(thread_api, "list_thread_pin_ids", empty_pins)


def _image() -> thread_api.DashboardImageBody:
    return thread_api.DashboardImageBody(
        base64=base64.b64encode(b"image").decode("ascii"),
        mimeType="image/png",
    )


def test_model_supports_images_marks_text_only_fireworks_models() -> None:
    assert not model_supports_images(_TEXT_ONLY_MODEL)
    assert model_supports_images(_VISION_MODEL)


def test_dashboard_media_uploads_rejects_images_for_text_only_model() -> None:
    with pytest.raises(HTTPException) as exc_info:
        thread_api._dashboard_media_uploads([_image()], model_id=_TEXT_ONLY_MODEL)

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail


def test_dashboard_media_uploads_decodes_images_for_vision_model() -> None:
    uploads = thread_api._dashboard_media_uploads([_image()], model_id=_VISION_MODEL)

    assert [(upload.data, upload.mime_type) for upload in uploads] == [(b"image", "image/png")]


def test_dashboard_media_uploads_rejects_malformed_images() -> None:
    bad = thread_api.DashboardImageBody(base64="not base64!", mimeType="image/png")
    with pytest.raises(HTTPException) as exc_info:
        thread_api._dashboard_media_uploads([bad], model_id=_VISION_MODEL)

    assert exc_info.value.status_code == 422


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


async def test_resolve_agent_model_choice_deprecated_request_uses_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {"default_model": "anthropic:claude-opus-5", "reasoning_effort": "high"},
        "fireworks:accounts/fireworks/models/glm-5p2",
        "high",
    )

    assert (model_id, effort) == (_VISION_MODEL, "medium")


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


async def test_resolve_agent_model_id_deprecated_override_uses_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(
        None, per_thread_model_id="fireworks:accounts/fireworks/models/glm-5p2"
    )
    assert model_id == _TEXT_ONLY_MODEL


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
    assert stamped["origin"] == "dashboard"
    assert stamped["thread_category"] == "interactive"
    assert stamped["trigger_kind"] == "user"
    assert stamped["participant_logins"] == {"octocat": True}
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
    messages = enriched["params"]["input"]["messages"]
    assert messages[-1]["content"].startswith(
        '<input-message sender="github:octocat" surface="web" kind="human">'
    )
    assert "<content>Fix the flaky test</content>" in messages[-1]["content"]
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
    attached: dict[str, object] = {}

    async def fake_attach(thread_id: str, uploads: list, *, environment_slug=None):
        attached["thread_id"] = thread_id
        attached["uploads"] = uploads
        return [
            MediaRef(
                path=f"/uploads/{'b' * 64}.png",
                mime_type="image/png",
                sha256="b" * 64,
                size=len(upload.data),
            )
            for upload in uploads
        ]

    monkeypatch.setattr(thread_api, "attach_thread_media", fake_attach)

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
    # The bytes go to the sandbox; the run input carries only the reference.
    assert attached["thread_id"] == "new-tid"
    assert [upload.data for upload in cast(list, attached["uploads"])] == [b"image"]
    user_message = enriched["params"]["input"]["messages"][-1]["content"]
    assert isinstance(user_message, str)
    assert image.base64 not in user_message
    assert ElementTree.fromstring(user_message).findtext("content") == "see attached"
    assert [ref.sha256 for ref in media_refs_from_content(user_message)] == ["b" * 64]


def _thread_with_metadata(metadata: dict) -> dict:
    return {"thread_id": "t1", "status": "idle", "metadata": metadata}


async def test_thread_summary_exposes_attention_reason() -> None:
    flagged = await thread_api._thread_summary(
        _thread_with_metadata({"title": "Ship it", "attention_reason": "prs_closed"})
    )
    quiet = await thread_api._thread_summary(_thread_with_metadata({"title": "Ship it"}))

    assert flagged["attentionReason"] == "prs_closed"
    assert quiet["attentionReason"] is None


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


async def test_terminal_sandbox_requires_existing_sandbox(monkeypatch) -> None:
    metadata = {
        "source": "dashboard",
        "sandbox_id": "sandbox-123",
        "repo_name": "repo",
    }

    class FakeThreads:
        async def get(self, thread_id: str):
            return {"thread_id": thread_id, "metadata": metadata}

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    assert await thread_api.get_dashboard_terminal_sandbox("tid", "teammate") == (
        "sandbox-123",
        "repo",
    )

    metadata["sandbox_id"] = "__creating__"
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_terminal_sandbox("tid", "teammate")
    assert exc_info.value.status_code == 404


async def test_thread_summary_includes_slack_source_urls_for_private_repo(monkeypatch) -> None:
    monkeypatch.setattr(thread_api, "SLACK_TEAM_ID", "T workspace")
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "repo_private": True,
                "source_context": {
                    "slack_thread": {
                        "channel_id": "C channel",
                        "thread_ts": "123.456",
                        "permalink": "https://slack.example/thread",
                    }
                },
            }
        )
    )

    assert summary["sourceUrl"] == "https://slack.example/thread"
    assert summary["sourceAppUrl"] == (
        "slack://channel?team=T+workspace&id=C+channel&message=123.456"
    )


async def test_thread_summary_includes_slack_source_urls_for_public_repo(monkeypatch) -> None:
    monkeypatch.setattr(thread_api, "SLACK_TEAM_ID", "T-workspace")
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "repo_private": False,
                "source_context": {
                    "slack_thread": {
                        "channel_id": "C-channel",
                        "thread_ts": "123.456",
                        "permalink": "https://slack.example/thread",
                    }
                },
            }
        )
    )

    assert summary["sourceUrl"] == "https://slack.example/thread"
    assert summary["sourceAppUrl"] == (
        "slack://channel?team=T-workspace&id=C-channel&message=123.456"
    )


async def test_thread_summary_includes_code_channel_url(monkeypatch) -> None:
    monkeypatch.setattr(thread_api, "SLACK_TEAM_ID", "T team&workspace")
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "source_context": {
                    "slack_thread": {"channel_id": "C code&channel", "thread_ts": "0"}
                },
            }
        )
    )

    assert summary["codeChannelUrl"] == (
        "https://slack.com/app_redirect?channel=C+code%26channel&team=T+team%26workspace"
    )


async def test_thread_summary_omits_code_channel_url_without_team() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "source_context": {"slack_thread": {"channel_id": "C123", "thread_ts": "0"}},
            }
        )
    )

    assert summary["codeChannelUrl"] is None


async def test_thread_summary_omits_code_channel_url_for_slack_thread() -> None:
    summary = await thread_api._thread_summary(
        _thread_with_metadata(
            {
                "source": "slack",
                "source_context": {
                    "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"}
                },
            }
        )
    )

    assert summary["codeChannelUrl"] is None


async def test_recovery_patch_reaches_any_authenticated_user(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "metadata": {"source": "dashboard"}}

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_recovery_patch("tid", "teammate")

    assert exc_info.value.status_code == 404
    assert "sandbox" in exc_info.value.detail


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
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            assert thread_id == "tid"
            updates.append(metadata)

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
        metadata={
            "source": "dashboard",
            "participant_logins": {"first": True},
        },
        email="teammate@example.com",
    )

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:teammate"
    assert last.findtext("content") == "fix the bug"
    assert updates[-1]["participant_logins"] == {"first": True, "teammate": True}


async def test_enrich_run_start_command_adds_web_handoff_for_slack_thread(monkeypatch) -> None:
    class FakeThreads:
        async def get_state(self, thread_id: str) -> dict[str, object]:
            return {"values": {"messages": [{"id": "existing-message"}]}}

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

    messages = enriched["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    content = messages[-1]["content"]
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    user_message = ElementTree.fromstring(content[0]["text"])
    assert user_message.attrib["sender"] == "github:teammate"
    assert user_message.findtext("content") == "continue here"


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

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:owner"
    assert last.findtext("content") == "fix the bug"


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
        content = b'{"type":"success","id":1,"result":{"run_id":"run-1"}}'
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
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
    updates = captured["updates"]
    assert isinstance(updates, list)
    assert updates[-1] == {
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

        async def __aenter__(self) -> FakeAsyncClient:
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


async def test_proxy_commands_rejects_unsurfaced_thread(monkeypatch) -> None:
    class InternalThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "metadata": {"source": "reviewer"}}

    class InternalClient:
        threads = InternalThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: InternalClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "tid", "teammate", b'{"method": "input.respond"}'
        )
    assert exc_info.value.status_code == 404


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
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: AdminClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
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
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: AdminClient())
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


async def test_run_cancel_rejects_unsurfaced_thread(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "tid"
            return {"thread_id": "tid", "metadata": {"source": "reviewer"}}

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_run_cancel("tid", "run-1", "teammate")
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

        async def __aenter__(self) -> FakeAsyncClient:
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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    state = await thread_api.get_dashboard_thread_state("tid", "owner")

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
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: AdminClient())

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid",
            "teammate",
            thread_api.ThreadMessageBody(content="ship it"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in this thread"


def test_assert_thread_postable_allows_configured_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")

    thread_api._assert_thread_postable(
        {"source": "dashboard", "admin_thread": True},
        "workspace-admin",
    )


def test_assert_thread_postable_restricts_workspace_automation(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")

    with pytest.raises(HTTPException) as exc_info:
        thread_api._assert_thread_postable(
            {"source": "schedule", "thread_category": "automation"},
            "teammate",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in this thread"


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
    assert payload["text"] == "ship it"
    assert cast(dict[str, object], payload["sender"])["id"] == "github:teammate"


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


async def test_resolve_dashboard_thread_marks_resolved(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {
                    "source": "dashboard",
                    "github_login": "octocat",
                    "auto_resolved_by_prs": True,
                },
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "agent_thread_pr_state_lock", _unlocked)

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=True)

    assert updates[-1]["resolved"] is True
    assert isinstance(updates[-1]["resolved_at_ms"], int)
    assert updates[-1]["auto_resolved_by_prs"] is False
    assert updates[-1]["attention_reason"] is None
    assert summary["resolved"] is True
    assert summary["attentionReason"] is None


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
                    "auto_resolved_by_prs": True,
                },
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "agent_thread_pr_state_lock", _unlocked)

    summary = await thread_api.resolve_dashboard_thread("tid", "octocat", resolved=False)

    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None
    assert updates[-1]["auto_resolved_by_prs"] is False
    assert summary["resolved"] is False


async def test_resolve_dashboard_thread_rejects_unsurfaced_thread(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "metadata": {"source": "reviewer"}}

    class FakeClient:
        threads = FakeThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "agent_thread_pr_state_lock", _unlocked)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.resolve_dashboard_thread("tid", "teammate", resolved=True)
    assert exc_info.value.status_code == 404


async def test_enrich_run_start_command_unresolves_thread(monkeypatch) -> None:
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
                    "auto_resolved_by_prs": True,
                    "attention_reason": "prs_closed",
                },
            }

        async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
            updates.append(dict(metadata))

    class FakeClient:
        threads = FakeThreads()

    _patch_new_thread_deps(monkeypatch, profile={})
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())
    monkeypatch.setattr(thread_api, "agent_thread_pr_state_lock", _unlocked)

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
            "auto_resolved_by_prs": True,
        },
    )

    assert updates, "expected metadata update to clear resolved state"
    assert updates[-1]["resolved"] is False
    assert updates[-1]["resolved_at_ms"] is None
    assert updates[-1]["auto_resolved_by_prs"] is False
    assert updates[-1]["attention_reason"] is None


def test_summary_matches_filters() -> None:
    summary = {
        "resolved": True,
        "viewed": False,
        "source": "github",
        "status": "finished",
        "title": "Fix the flaky test",
        "repoFullName": "langchain-ai/open-swe",
        "branch": "open-swe/fix-flake",
        "pr": {"number": 123, "url": "https://github.com/langchain-ai/open-swe/pull/123"},
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
    assert thread_api._summary_matches_filters(
        summary, resolved=None, viewed=None, source=None, status=None, query="pull/123"
    )


def test_metadata_matches_filters() -> None:
    metadata = {
        "source": "dashboard",
        "title": "Fix login bug",
        "resolved": True,
        "repo_owner": "langchain-ai",
        "repo_name": "open-swe",
        "branch_name": "open-swe/fix-login",
        "pull_requests": [
            {
                "repo_full_name": "langchain-ai/open-swe",
                "number": 123,
                "url": "https://github.com/langchain-ai/open-swe/pull/123",
            }
        ],
    }
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
    for query in ("langchain-ai/open-swe", "fix-login", "pull/123"):
        assert thread_api._metadata_matches_filters(
            metadata, resolved=None, source=None, query=query
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
            "thread_id": "other-participant",
            "metadata": {
                "source": "schedule",
                "schedule_id": "schedule-1",
                "latest_run_status": "success",
                "updated_at_ms": 10,
            },
        }
    )

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
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

    interactive = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, scope="interactive"
    )
    automation = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, scope="automation", automation_id="schedule-1"
    )

    assert [item["id"] for item in interactive["items"]] == ["t2"]
    assert [item["id"] for item in automation["items"]] == ["other-participant", "t0"]
    assert automation["items"][0]["automationId"] == "schedule-1"


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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_page(
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

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads(), runs=FakeRuns()),
    )

    result = await thread_api.list_dashboard_threads_page("octocat", email=None, ownerless=True)

    assert [item["id"] for item in result["items"]] == ["t2"]


async def test_list_dashboard_threads_page_filters_flat_and_legacy_repo_metadata(
    monkeypatch,
) -> None:
    threads = _make_threads(4, resolved_before=0)
    for thread in threads:
        cast(dict[str, object], thread["metadata"])["latest_run_status"] = "success"
    cast(dict[str, object], threads[0]["metadata"]).update(
        {"repo_owner": "langchain-ai", "repo_name": "open-swe"}
    )
    cast(dict[str, object], threads[1]["metadata"])["repo"] = {
        "owner": "LANGCHAIN-AI",
        "name": "OPEN-SWE",
    }
    cast(dict[str, object], threads[2]["metadata"]).update(
        {"repo_owner": "langchain-ai", "repo_name": "langgraph"}
    )
    searches: list[dict[str, object]] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            searches.append(metadata)
            matches = [
                thread
                for thread in threads
                if all(
                    cast(dict[str, object], thread["metadata"]).get(key) == value
                    for key, value in metadata.items()
                )
            ]
            return matches[offset : offset + limit]

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads(), runs=FakeRuns()),
    )

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, repo="langchain-ai/open-swe"
    )

    assert [item["id"] for item in result["items"]] == ["t0", "t1"]
    assert searches == [
        {"participant_logins": {"octocat": True}},
        {"github_login": "octocat"},
    ]


async def test_list_dashboard_thread_projects_discovers_metadata_without_summaries(
    monkeypatch,
) -> None:
    threads = _make_threads(5, resolved_before=0)
    cast(dict[str, object], threads[0]["metadata"]).update(
        {"repo_owner": "langchain-ai", "repo_name": "open-swe", "updated_at_ms": 50}
    )
    cast(dict[str, object], threads[1]["metadata"])["repo"] = {
        "owner": "LANGCHAIN-AI",
        "name": "OPEN-SWE",
    }
    cast(dict[str, object], threads[2]["metadata"]).update(
        {"repo_owner": "langchain-ai", "repo_name": "langgraph", "updated_at_ms": 30}
    )
    cast(dict[str, object], threads[3]["metadata"]).update(
        {
            "repo_owner": "langchain-ai",
            "repo_name": "private",
            "resolved": True,
            "updated_at_ms": 60,
        }
    )

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            return threads[offset : offset + limit]

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads()),
    )

    result = await thread_api.list_dashboard_thread_projects("octocat")

    assert result == [
        {
            "repoFullName": "langchain-ai/open-swe",
            "name": "open-swe",
            "updatedAt": 50,
        },
        {
            "repoFullName": "langchain-ai/langgraph",
            "name": "langgraph",
            "updatedAt": 30,
        },
    ]


async def test_pin_dashboard_thread_allows_readable_non_owner(monkeypatch) -> None:
    pinned: list[tuple[str, str]] = []

    class FakeThreads:
        async def get(self, thread_id):
            return {
                "thread_id": thread_id,
                "metadata": {"source": "slack", "github_login": "other"},
            }

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads()),
    )

    async def fake_pin(login: str, thread_id: str) -> None:
        pinned.append((login, thread_id))

    monkeypatch.setattr(thread_api, "pin_thread", fake_pin)

    await thread_api.pin_dashboard_thread("shared-thread", "octocat")

    assert pinned == [("octocat", "shared-thread")]


async def test_pin_dashboard_thread_returns_not_found_for_lookup_failure(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id):
            raise RuntimeError("thread missing")

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads()),
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.pin_dashboard_thread("missing-thread", "octocat")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "thread not found"


async def test_pin_dashboard_thread_rejects_unreadable_thread(monkeypatch) -> None:
    class FakeThreads:
        async def get(self, thread_id):
            return {"thread_id": thread_id, "metadata": {"source": "internal"}}

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads()),
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.pin_dashboard_thread("private-thread", "octocat")

    assert exc_info.value.status_code == 404


async def test_list_dashboard_pinned_threads_returns_only_readable_threads(
    monkeypatch,
) -> None:
    threads = {
        "shared-thread": {
            "thread_id": "shared-thread",
            "metadata": {
                "source": "slack",
                "github_login": "teammate",
                "title": "Pinned teammate thread",
                "updated_at_ms": 100,
                "latest_run_status": "success",
            },
        },
        "private-thread": {
            "thread_id": "private-thread",
            "metadata": {"source": "reviewer"},
        },
    }

    class FakeThreads:
        async def get(self, thread_id):
            if thread_id == "missing-thread":
                raise RuntimeError("missing")
            return threads[thread_id]

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    monkeypatch.setattr(
        thread_api,
        "langgraph_client",
        lambda: SimpleNamespace(threads=FakeThreads(), runs=FakeRuns()),
    )

    async def fake_pin_ids(login: str) -> list[str]:
        assert login == "octocat"
        return ["shared-thread", "private-thread", "missing-thread"]

    monkeypatch.setattr(thread_api, "list_thread_pin_ids", fake_pin_ids)

    result = await thread_api.list_dashboard_pinned_threads("octocat")

    assert [item["id"] for item in result] == ["shared-thread"]


async def test_list_dashboard_threads_page_can_sort_by_creation_time(monkeypatch) -> None:
    threads = _make_threads(2, resolved_before=0)
    older = cast(dict[str, object], threads[0]["metadata"])
    older.update({"created_at_ms": 1, "updated_at_ms": 3, "latest_run_status": "success"})
    newer = cast(dict[str, object], threads[1]["metadata"])
    newer.update({"created_at_ms": 2, "updated_at_ms": 2, "latest_run_status": "success"})
    requested_sorts: list[str] = []

    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            requested_sorts.append(sort_by)
            field = f"{sort_by}_ms"
            ordered = sorted(
                threads,
                key=lambda thread: cast(int, cast(dict[str, object], thread["metadata"])[field]),
                reverse=True,
            )
            return ordered[offset : offset + limit]

        async def update(self, *, thread_id, metadata):
            return None

    class FakeRuns:
        async def list(self, thread_id, limit=1):
            return []

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.list_dashboard_threads_page(
        "octocat", email=None, limit=2, offset=0, sort_by="created_at"
    )

    assert set(requested_sorts) == {"created_at"}
    assert [item["id"] for item in result["items"]] == ["t1", "t0"]


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
async def test_get_my_profile_drops_deprecated_models() -> None:
    with patch(
        "agent.dashboard.routes.get_profile",
        new_callable=AsyncMock,
        return_value={
            "default_model": "fireworks:accounts/fireworks/models/glm-5p2",
            "reasoning_effort": "high",
        },
    ):
        payload = await routes.get_my_profile({"sub": "octocat"})

    assert "default_model" not in payload
    assert "reasoning_effort" not in payload


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


async def test_working_tree_diff_reads_live_sandbox_against_head(monkeypatch) -> None:
    metadata = {
        "sandbox_id": "sandbox-1",
        "repo_owner": "acme",
        "repo_name": "repo",
    }
    live = {
        "status": "ready",
        "files": [{"path": "new.py", "additions": 1, "deletions": 0}],
        "truncated": False,
        "summary": {"files": 1, "additions": 1, "deletions": 0},
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    sandbox = object()
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    monkeypatch.setattr(
        "agent.sandboxes.paths.resolve_sandbox_work_dir",
        AsyncMock(return_value="/work"),
    )
    read_diff = AsyncMock(return_value=live)
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    result = await thread_api.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert result == live
    read_diff.assert_awaited_once_with(sandbox, "/work", "HEAD", None, repo_path="/work/repo")


async def test_working_tree_diff_raises_when_the_sandbox_is_unreachable(monkeypatch) -> None:
    metadata = {"sandbox_id": "sandbox-1"}
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(side_effect=RuntimeError))

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Could not connect to the workspace."


async def test_branch_diff_uses_repository_from_pr_url(monkeypatch) -> None:
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

    await thread_api.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert build_diff.await_args is not None
    assert build_diff.await_args.args[1:] == ("langchain-ai/open-swe", 1925)


async def test_branch_diff_without_a_pull_request_compares_against_the_base(monkeypatch) -> None:
    metadata = {
        "repo_owner": "langchain-ai",
        "repo_name": "open-swe",
        "base_branch": "main",
        "branch_name": "open-swe/feature",
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(thread_api, "_github_token_for_login", AsyncMock(return_value="token"))
    build_compare = AsyncMock(
        return_value={"base_sha": "merge-base", "head_sha": "head", "truncated": False, "files": []}
    )
    monkeypatch.setattr(thread_api, "build_compare_diff_files", build_compare)

    result = await thread_api.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert build_compare.await_args is not None
    assert build_compare.await_args.args[1:] == (
        "langchain-ai/open-swe",
        "main",
        "open-swe/feature",
    )
    assert result["prNumber"] is None
    assert result["baseSha"] == "merge-base"


async def test_branch_diff_rejects_an_unsafe_branch_name(monkeypatch) -> None:
    metadata = {
        "repo_owner": "langchain-ai",
        "repo_name": "open-swe",
        "base_branch": "main",
        "branch_name": "../../etc/passwd",
    }
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(thread_api, "_github_token_for_login", AsyncMock(return_value="token"))
    build_compare = AsyncMock()
    monkeypatch.setattr(thread_api, "build_compare_diff_files", build_compare)

    with pytest.raises(HTTPException) as excinfo:
        await thread_api.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert excinfo.value.status_code == 404
    build_compare.assert_not_awaited()


async def test_cancel_dashboard_thread_interrupts_runs_it_did_not_start(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    thread = {
        "thread_id": "thread-1",
        "status": "busy",
        "metadata": {
            "title": "Slack-triggered thread",
            "github_login": "owner",
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
        async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, str]]:
            calls.append(("list", {"thread_id": thread_id, **kwargs}))
            return [{"run_id": f"{kwargs['status']}-run"}]

        async def cancel_many(self, **kwargs: object) -> None:
            calls.append(("cancel_many", kwargs))

    class FakeStore:
        async def get_item(self, namespace: tuple[str, str], key: str) -> None:
            return None

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()
        store = FakeStore()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.cancel_dashboard_thread("thread-1", "owner")

    assert calls[2] == (
        "cancel_many",
        {
            "thread_id": "thread-1",
            "run_ids": ["pending-run", "running-run"],
            "action": "interrupt",
        },
    )
    # Reported as interrupted even though the platform still says busy.
    assert result["status"] == "interrupted"


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

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    with pytest.raises(HTTPException):
        await thread_api.cancel_dashboard_thread("thread-1", "someone-else")

    assert cancelled is False


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
        async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, str]]:
            calls.append(("list", {"thread_id": thread_id, **kwargs}))
            return [{"run_id": f"{kwargs['status']}-run"}]

        async def cancel_many(self, **kwargs: object) -> None:
            calls.append(("cancel_many", kwargs))

    class FakeClient:
        threads = FakeThreads()
        runs = FakeRuns()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: FakeClient())

    result = await thread_api.admin_cancel_dashboard_thread("thread-1")

    assert calls[2] == (
        "cancel_many",
        {
            "thread_id": "thread-1",
            "run_ids": ["pending-run", "running-run"],
            "action": "interrupt",
        },
    )
    assert calls[3][0] == "update"
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
        async def list(self, thread_id: str, **kwargs: object) -> list[dict[str, str]]:
            return [{"run_id": f"{kwargs['status']}-run"}]

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
