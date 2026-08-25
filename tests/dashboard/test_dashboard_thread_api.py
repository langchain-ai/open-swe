import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException

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
            "github_login": "owner",
            "participant_logins": ["owner"],
        },
        email="teammate@example.com",
    )

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:teammate"
    assert last.findtext("content") == "fix the bug"
    assert updates[-1]["participant_logins"] == ["owner", "teammate"]


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


def test_assert_thread_postable_allows_configured_admin(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")

    thread_api._assert_thread_postable(
        {"source": "dashboard", "admin_thread": True},
        "workspace-admin",
    )


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
        "agent.utils.sandbox_paths.aresolve_sandbox_work_dir",
        AsyncMock(return_value="/work"),
    )
    read_diff = AsyncMock(return_value=live)
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    result = await thread_api.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert result == live
    read_diff.assert_awaited_once_with(sandbox, "/work", "HEAD", None, repo_path="/work/repo")


async def test_working_tree_diff_returns_missing_when_the_sandbox_is_unreachable(
    monkeypatch,
) -> None:
    metadata = {"sandbox_id": "sandbox-1"}
    monkeypatch.setattr(thread_api, "_readable_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(side_effect=RuntimeError))

    result = await thread_api.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert result == {
        "status": "missing",
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
    }


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
