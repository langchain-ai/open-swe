import base64

import pytest
from fastapi import HTTPException

from agent.dashboard import thread_api
from agent.dashboard.options import model_supports_images

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.5"


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


async def test_enrich_run_start_command_rejects_images_for_resolved_text_only_model(
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

    with pytest.raises(HTTPException) as exc_info:
        await thread_api._enrich_run_start_command(
            "new-tid",
            "octocat",
            command,
            metadata={},
            creating=True,
        )

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail


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


async def test_proxy_commands_run_start_by_non_owner_is_rejected(monkeypatch) -> None:
    class OwnedThreads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {
                "thread_id": thread_id,
                "metadata": {"source": "dashboard", "github_login": "owner"},
            }

    class OwnedClient:
        threads = OwnedThreads()

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: OwnedClient())

    # An existing thread owned by someone else is never lazily re-created — a
    # run.start from a non-owner is a 404, not a takeover.
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands(
            "tid", "intruder", b'{"method": "run.start"}'
        )
    assert exc_info.value.status_code == 404


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


async def test_proxy_endpoints_enforce_thread_ownership(monkeypatch) -> None:
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
        await thread_api.get_dashboard_thread_state("tid", "intruder")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_commands("tid", "intruder", b"{}")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_history("tid", "intruder", b"{}")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.proxy_dashboard_thread_run_cancel("tid", "run-1", "intruder")
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await anext(thread_api.proxy_dashboard_thread_stream_events("tid", "intruder", b"{}"))
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
