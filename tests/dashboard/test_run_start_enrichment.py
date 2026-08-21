"""What the dashboard does to a user's message before the agent sees it.

A message reaches a thread one of two ways -- a ``run.start`` command through
``proxy_dashboard_thread_commands`` when the thread is idle, or the queue via
``send_dashboard_message`` when it is already running -- and both share the
model resolution, image validation and sender attribution asserted here. Every
test drives one of those two handlers, never the enrichment helper underneath.
"""

import base64
import json
from typing import Any, cast
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException
from support.httpx_fakes import FakeHttpx
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import thread_api

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.6-sol"
_RUN_STARTED = b'{"type":"success","id":1,"result":{"run_id":"run-1"}}'


def _image() -> thread_api.DashboardImageBody:
    return thread_api.DashboardImageBody(
        base64=base64.b64encode(b"image").decode("ascii"),
        mimeType="image/png",
    )


def _install_client(monkeypatch, **kwargs: Any) -> FakeLangGraphClient:
    client = FakeLangGraphClient(**kwargs)
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    return client


def _install_proxy(monkeypatch) -> FakeHttpx:
    proxy = FakeHttpx(content=_RUN_STARTED)
    monkeypatch.setattr(thread_api.httpx, "AsyncClient", proxy.client)
    return proxy


async def _run_start(
    proxy: FakeHttpx,
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    email: str | None = None,
) -> dict[str, Any]:
    """Post ``command`` and return the enriched command the proxy forwarded."""
    status, body, _ = await thread_api.proxy_dashboard_thread_commands(
        thread_id, login, json.dumps(command).encode(), email=email
    )
    assert (status, body) == (200, _RUN_STARTED)
    return cast(dict[str, Any], proxy.payloads[-1])


def _stamped(client: FakeLangGraphClient, thread_id: str = "new-tid") -> dict[str, Any]:
    """The metadata the thread ended up with, after create plus any updates."""
    return client.threads.threads[thread_id]["metadata"]


def _update_carrying(client: FakeLangGraphClient, key: str) -> dict[str, Any]:
    """The last metadata patch that touched ``key``.

    A successful ``run.start`` always writes the started run's id afterwards, so
    the newest patch is never the one a test is asking about.
    """
    patches = [patch for patch in client.threads.updates if key in patch]
    assert patches, f"expected a metadata patch carrying {key!r}"
    return patches[-1]


def _patch_enrich_deps(monkeypatch, *, profile: dict[str, object] | None = None) -> None:
    """Stub the per-user lookups the run-start path makes."""

    async def fake_profile(login: str) -> dict[str, object]:
        return dict(profile or {})

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, prof: dict[str, object]) -> str:
        return f"{login}@example.com"

    monkeypatch.setattr(thread_api, "get_profile", fake_profile)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", fake_ensure_token)
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)


def _patch_new_thread_deps(monkeypatch, *, profile: dict[str, object]) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    _patch_enrich_deps(monkeypatch, profile=profile)
    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)


async def test_run_start_creates_and_stamps_new_thread(monkeypatch) -> None:
    _patch_new_thread_deps(monkeypatch, profile={})
    client = _install_client(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "new-tid",
        "octocat",
        {
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
        },
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


async def test_run_start_uses_vision_fallback_for_text_only_model(monkeypatch) -> None:
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    client = _install_client(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    image = _image()
    enriched = await _run_start(
        proxy,
        "new-tid",
        "octocat",
        {
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
        },
    )

    stamped = _stamped(client)
    assert stamped["model"] == _VISION_MODEL
    assert stamped["effort"] == "medium"
    assert stamped["resolved_model"] == _VISION_MODEL
    assert stamped["resolved_effort"] == "medium"
    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"


async def test_run_start_applies_profile_model_before_team_default(monkeypatch) -> None:
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    client = _install_client(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    await _run_start(
        proxy,
        "new-tid",
        "octocat",
        {
            "method": "run.start",
            "params": {
                "input": {"messages": [{"type": "human", "content": "go"}]},
                "config": {"configurable": {}},
            },
        },
    )

    stamped = _stamped(client)
    assert (stamped["resolved_model"], stamped["resolved_effort"]) == (_TEXT_ONLY_MODEL, "high")


async def test_run_start_applies_requested_model_before_profile(monkeypatch) -> None:
    _patch_new_thread_deps(
        monkeypatch,
        profile={"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
    )
    client = _install_client(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    await _run_start(
        proxy,
        "new-tid",
        "octocat",
        {
            "method": "run.start",
            "params": {
                "input": {"messages": [{"type": "human", "content": "go"}]},
                "config": {
                    "configurable": {
                        "agent_model_id": "anthropic:claude-opus-5",
                        "agent_effort": "high",
                    }
                },
            },
        },
    )

    stamped = _stamped(client)
    assert (stamped["resolved_model"], stamped["resolved_effort"]) == (
        "anthropic:claude-opus-5",
        "high",
    )


async def test_run_start_migrates_deprecated_requested_model(monkeypatch) -> None:
    _patch_new_thread_deps(monkeypatch, profile={})
    client = _install_client(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    await _run_start(
        proxy,
        "new-tid",
        "octocat",
        {
            "method": "run.start",
            "params": {
                "input": {"messages": [{"type": "human", "content": "go"}]},
                "config": {
                    "configurable": {"agent_model_id": "openai:gpt-5.5", "agent_effort": "high"}
                },
            },
        },
    )

    stamped = _stamped(client)
    assert (stamped["resolved_model"], stamped["resolved_effort"]) == ("openai:gpt-5.6-sol", "high")


async def test_run_start_attributes_non_owner_message(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "owner",
            "participant_logins": ["owner"],
        },
    )
    _patch_enrich_deps(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "teammate",
        {
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "fix the bug"}]}},
        },
        email="teammate@example.com",
    )

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:teammate"
    assert last.findtext("content") == "fix the bug"
    assert _update_carrying(client, "participant_logins")["participant_logins"] == [
        "owner",
        "teammate",
    ]
    assert {call["thread_id"] for _, call in client.calls} == {"tid"}


async def test_run_start_adds_web_handoff_for_slack_thread(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={"source": "slack", "github_login": "owner"},
        messages=[{"id": "existing-message"}],
    )
    _patch_enrich_deps(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "teammate",
        {
            "method": "run.start",
            "params": {
                "input": {
                    "messages": [
                        {"role": "user", "content": "continue here", "id": "existing-message"}
                    ]
                }
            },
        },
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


async def test_run_start_adds_web_handoff_before_image_blocks(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "slack", "github_login": "owner"})
    _patch_enrich_deps(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "teammate",
        {
            "method": "run.start",
            "params": {
                "input": {
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "continue here"}]}
                    ]
                }
            },
        },
        email="teammate@example.com",
    )

    messages = enriched["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    content = messages[-1]["content"]
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    user_message = ElementTree.fromstring(content[0]["text"])
    assert user_message.attrib["sender"] == "github:teammate"
    assert user_message.findtext("content") == "continue here"


async def test_run_start_does_not_attribute_owner_message(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})
    _patch_enrich_deps(monkeypatch)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "owner",
        {
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "fix the bug"}]}},
        },
        email="owner@example.com",
    )

    last = ElementTree.fromstring(enriched["params"]["input"]["messages"][-1]["content"])
    assert last.attrib["sender"] == "github:owner"
    assert last.findtext("content") == "fix the bug"


async def test_run_start_allowlists_client_configurable(monkeypatch) -> None:
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "repo_owner": "octo",
            "repo_name": "repo",
        },
    )

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
    monkeypatch.setattr(thread_api, "_resolve_run_email", fake_resolve_email)
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "octocat",
        {
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
        },
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["github_login"] == "octocat"
    assert configurable["user_email"] == "octocat@example.com"
    assert configurable["source"] == "dashboard"
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert configurable["agent_model_id"] == _VISION_MODEL
    assert configurable["agent_effort"] == "medium"
    assert _update_carrying(client, "model")["model"] == _VISION_MODEL


async def test_run_start_unresolves_thread(monkeypatch) -> None:
    _patch_new_thread_deps(monkeypatch, profile={})
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "octocat",
            "resolved": True,
            "resolved_at_ms": 1700,
        },
    )
    proxy = _install_proxy(monkeypatch)

    async def fake_build(thread_id, login, metadata, *, overrides):
        return {"github_login": login, "source": "dashboard"}

    monkeypatch.setattr(thread_api, "_build_dashboard_configurable", fake_build)

    await _run_start(
        proxy,
        "tid",
        "octocat",
        {
            "method": "run.start",
            "params": {
                "input": {"messages": [{"type": "human", "content": "follow up"}]},
                "config": {"configurable": {}},
            },
        },
    )

    patch = _update_carrying(client, "resolved")
    assert patch["resolved"] is False
    assert patch["resolved_at_ms"] is None


async def test_run_start_from_slack_thread_updates_trace_reply(monkeypatch) -> None:
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

    async def fake_update_trace_reply(channel_id: str, message_ts: str, thread_id: str) -> bool:
        captured["handoff_update"] = {
            "channel_id": channel_id,
            "message_ts": message_ts,
            "thread_id": thread_id,
        }
        return True

    _patch_enrich_deps(monkeypatch)
    monkeypatch.setattr(thread_api, "_now_ms", lambda: 123_456)
    monkeypatch.setattr(
        thread_api, "update_slack_trace_reply_for_web_handoff", fake_update_trace_reply
    )
    proxy = _install_proxy(monkeypatch)

    enriched = await _run_start(
        proxy,
        "tid",
        "octocat",
        {
            "method": "run.start",
            "params": {"input": {"messages": [{"role": "user", "content": "continue here"}]}},
        },
    )

    messages = enriched["params"]["input"]["messages"]
    handoff = ElementTree.fromstring(messages[-2]["content"])
    user_message = ElementTree.fromstring(messages[-1]["content"])
    assert "conversation has moved to Web" in (handoff.findtext("content") or "")
    assert user_message.findtext("content") == "continue here"
    assert captured["handoff_update"] == {
        "channel_id": "C1",
        "message_ts": "123.46",
        "thread_id": "tid",
    }
    assert enriched["params"]["metadata"]["dashboard_ttft_started_at_ms"] == 123_456
    assert client.threads.updates[-1] == {
        "latest_run_id": "run-1",
        "latest_run_status": "pending",
        "updated_at_ms": 123_456,
    }


def _patch_queue(monkeypatch, captured: dict[str, object]) -> None:
    async def active(thread_id: str) -> bool:
        return True

    async def fake_queue(thread_id: str, payload: dict[str, object]) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(thread_api, "get_thread_active_status", active)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue)


async def test_queued_message_returns_502_when_activity_unknown(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})

    async def unknown_activity(thread_id: str) -> None:
        assert thread_id == "tid"
        return None

    monkeypatch.setattr(thread_api, "get_thread_active_status", unknown_activity)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid", "octocat", thread_api.ThreadMessageBody(content="hello")
        )

    assert exc_info.value.status_code == 502


async def test_queued_message_rejects_non_admin_on_admin_thread(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    client = _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "workspace-admin",
            "admin_thread": True,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid", "teammate", thread_api.ThreadMessageBody(content="ship it")
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in admin threads"
    assert client.threads.updates == []


async def test_queued_message_accepts_configured_admin_on_admin_thread(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "someone-else",
            "admin_thread": True,
        },
    )
    captured: dict[str, object] = {}
    _patch_queue(monkeypatch, captured)

    await thread_api.send_dashboard_message(
        "tid", "workspace-admin", thread_api.ThreadMessageBody(content="ship it")
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"


async def test_queued_message_attributes_non_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})
    _patch_queue(monkeypatch, captured)

    await thread_api.send_dashboard_message(
        "tid", "teammate", thread_api.ThreadMessageBody(content="ship it")
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"
    assert cast(dict[str, object], payload["sender"])["id"] == "github:teammate"
    assert payload["from_owner"] is False


async def test_queued_message_does_not_attribute_owner(monkeypatch) -> None:
    captured: dict[str, object] = {}
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})
    _patch_queue(monkeypatch, captured)

    await thread_api.send_dashboard_message(
        "tid", "owner", thread_api.ThreadMessageBody(content="ship it")
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "ship it"
    assert payload["from_owner"] is True


async def test_queued_message_rejects_images_for_text_only_model(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "owner",
            "model": _TEXT_ONLY_MODEL,
        },
    )
    _patch_queue(monkeypatch, {})

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "tid",
            "owner",
            thread_api.ThreadMessageBody(content="see attached", images=[_image()]),
        )

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail


async def test_queued_message_allows_images_for_vision_model(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={"source": "dashboard", "github_login": "owner", "model": _VISION_MODEL},
    )
    captured: dict[str, object] = {}
    _patch_queue(monkeypatch, captured)

    await thread_api.send_dashboard_message(
        "tid",
        "owner",
        thread_api.ThreadMessageBody(content="see attached", images=[_image()]),
    )

    payload = cast(dict[str, object], captured["payload"])
    assert payload["text"] == "see attached"
    images = cast(list[dict[str, Any]], payload["images"])
    assert len(images) == 1
    assert images[0]["type"] == "image"
    assert images[0]["mime_type"] == "image/png"
    assert images[0]["base64"] == base64.b64encode(b"image").decode("ascii")
