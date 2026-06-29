from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_queue as queue
from agent import delivery_runner as runner
from agent import (
    project_model_endpoints,
    project_model_routing,
    project_registry,
    project_secrets,
)
from agent.dashboard import provider_pat_vault


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    async def create(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        if_exists: str = "raise",
    ) -> dict[str, Any]:
        self.metadata.setdefault(thread_id, dict(metadata))
        return {"thread_id": thread_id, "metadata": self.metadata[thread_id]}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.metadata.setdefault(thread_id, {}).update(metadata)

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "status": "idle", "metadata": {}}


class _FakeRuns:
    async def list(
        self,
        thread_id: str,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        assistant_id: str = "agent",
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"thread_id": thread_id, "configurable": configurable, "metadata": metadata}
        )
        return {"run_id": "run-worker-1"}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    from cryptography.fernet import Fernet

    client = _FakeClient()
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(project_model_endpoints, "_client", lambda: client, raising=False)
    monkeypatch.setattr(project_model_routing, "_client", lambda: client)
    monkeypatch.setattr(project_secrets, "_client", lambda: client)
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(provider_pat_vault, "_client", lambda: client)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return client


@pytest.fixture
def dispatch_recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr(runner, "dispatch_agent_run", recorder)
    return recorder


async def _stored_project(**overrides: Any) -> dict[str, Any]:
    project = project_registry.default_delivery_project(
        project_id="sports-cms",
        name="Sports CMS",
        tracker_config={"project_id": "linear-sports"},
        vcs_config={"owner": "example", "repo": "sports-cms"},
        ai_hub_policy={
            "enabled": False,
            "model_ids": [
                "openai:gpt-5.5",
                "anthropic:claude-opus-4-8",
                "google_genai:gemini-3.5-flash",
            ],
        },
        credential_policy={
            "provider": "github",
            "scope": "user",
            "requires_user_pat": True,
            "identity": "github:user:octocat",
        },
    )
    project.update(overrides)
    return await project_registry.upsert_delivery_project(project)


def _ready_preflight() -> queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": True,
        "issue_context": True,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


async def _queued_item() -> dict[str, Any]:
    return await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "SPORT-123",
            "title": "Fix Sports CMS teaser",
            "description": "The teaser CTA is broken on mobile.",
            "github_login": "octocat",
            "credential_identity": "github:user:octocat",
            "model_snapshot": "openai:gpt-5.5",
            "repo": {"owner": "example", "name": "sports-cms"},
            "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        },
        preflight=_ready_preflight(),
    )


async def test_project_model_routing_default_override_fallback_and_audit(
    fake_client: _FakeClient,
) -> None:
    await _stored_project()

    updated = await project_model_routing.set_project_model_routing(
        "sports-cms",
        {
            "default": {"model_id": "openai:gpt-5.5", "effort": "high"},
            "roles": {
                "executor": {"model_id": "anthropic:claude-opus-4-8", "effort": "xhigh"},
                "helper": {"model_id": "google_genai:gemini-3.5-flash", "effort": "medium"},
            },
            "fallback": {"model_id": "openai:gpt-5.5", "effort": "medium"},
        },
        actor="octocat",
    )

    assert updated["model_routing"]["roles"]["executor"]["model_id"] == (
        "anthropic:claude-opus-4-8"
    )
    assert project_model_routing.resolve_model_for_role(updated, "executor") == {
        "role": "executor",
        "model_id": "anthropic:claude-opus-4-8",
        "effort": "xhigh",
        "source": "role",
    }
    assert project_model_routing.resolve_model_for_role(updated, "drupal_backend") == {
        "role": "drupal_backend",
        "model_id": "openai:gpt-5.5",
        "effort": "high",
        "source": "default",
    }

    fallback_only = {
        **updated,
        "model_routing": {"roles": {}, "fallback": updated["model_routing"]["fallback"]},
    }
    assert project_model_routing.resolve_model_for_role(fallback_only, "design") == {
        "role": "design",
        "model_id": "openai:gpt-5.5",
        "effort": "medium",
        "source": "fallback",
    }

    audit = await project_model_routing.list_model_routing_audit("sports-cms")
    assert {entry["role"] for entry in audit} == {"default", "executor", "helper", "fallback"}
    assert all(entry["actor"] == "octocat" for entry in audit)


async def test_invalid_project_model_routing_blocks_worker_dispatch(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project(
        model_routing={
            "default": {"model_id": "unsupported:model", "effort": "medium"},
            "roles": {},
        }
    )
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "refused"
    assert result["reason"] == "model_routing"
    assert dispatch_recorder.calls == []


async def test_delivery_worker_records_project_model_routing_snapshot(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    await project_model_routing.set_project_model_routing(
        "sports-cms",
        {
            "default": {"model_id": "openai:gpt-5.5", "effort": "medium"},
            "roles": {
                "executor": {"model_id": "anthropic:claude-opus-4-8", "effort": "xhigh"},
                "helper": {"model_id": "google_genai:gemini-3.5-flash", "effort": "medium"},
            },
        },
        actor="octocat",
    )
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "launched"
    call = dispatch_recorder.calls[0]
    assert call["configurable"]["agent_model_id"] == "anthropic:claude-opus-4-8"
    assert call["configurable"]["agent_effort"] == "xhigh"
    assert call["configurable"]["agent_subagent_model_id"] == "google_genai:gemini-3.5-flash"
    updated = await queue.read_delivery_queue_item(record["id"])
    snapshot = updated["model_routing_snapshot"]
    assert snapshot["roles"]["executor"]["model_id"] == "anthropic:claude-opus-4-8"
    assert updated["latest_run"]["model_routing_snapshot"] == snapshot


async def test_endpoint_model_routing_snapshot_records_provider_metadata(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="DEEPSEEK_API_KEY",
        value="deepseek-secret-1234",
        updated_by="octocat",
    )
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("deepseek"),
            "id": "deepseek-main",
            "model_capabilities": {
                "deepseek-chat": {
                    "tool_calling": True,
                    "context_window": 64000,
                    "cost": {"input_per_million": 0.2, "currency": "USD"},
                }
            },
        },
    )
    project = await project_model_routing.set_project_model_routing(
        "sports-cms",
        {
            "environment": "default",
            "roles": {
                "executor": {
                    "endpoint_id": "deepseek-main",
                    "model_id": "deepseek-chat",
                    "effort": "high",
                    "capabilities": {
                        "tool_calling": True,
                        "reasoning": True,
                        "context_window": 64000,
                    },
                },
                "helper": {"endpoint_id": "deepseek-main", "model_id": "deepseek-reasoner"},
                "qa": {"endpoint_id": "deepseek-main", "model_id": "deepseek-chat"},
                "reviewer": {"endpoint_id": "deepseek-main", "model_id": "deepseek-chat"},
                "browser_proof": {
                    "endpoint_id": "deepseek-main",
                    "model_id": "deepseek-chat",
                },
                "subagent": {"endpoint_id": "deepseek-main", "model_id": "deepseek-reasoner"},
            },
        },
        actor="octocat",
    )
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "launched"
    call = dispatch_recorder.calls[0]
    assert call["configurable"]["agent_model_id"] == "deepseek-chat"
    snapshot = (await queue.read_delivery_queue_item(record["id"]))["model_routing_snapshot"]
    executor = snapshot["roles"]["executor"]
    assert executor["endpoint_id"] == "deepseek-main"
    assert executor["provider_type"] == "deepseek"
    assert executor["base_url_fingerprint"]
    assert executor["capabilities"] == {
        "tool_calling": True,
        "reasoning": True,
        "context_window": 64000,
    }
    assert "deepseek-secret-1234" not in str(snapshot)
    assert "qa" in snapshot["roles"]
    assert "reviewer" in snapshot["roles"]
    assert "browser_proof" in snapshot["roles"]
    assert "subagent" in snapshot["roles"]
    payload = project_model_routing.model_routing_payload(project)
    assert "fallback" in payload["roles"]
    assert "qa" in payload["roles"]
    assert "reviewer" in payload["roles"]
    assert payload["endpoints"][0]["models"][0]["capabilities"]["tool_calling"] is True


async def test_disabled_endpoint_blocks_model_routing(
    fake_client: _FakeClient,
) -> None:
    await _stored_project()
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("deepseek"),
            "id": "deepseek-main",
            "disabled": True,
        },
    )

    with pytest.raises(ValueError, match="disabled"):
        await project_model_routing.set_project_model_routing(
            "sports-cms",
            {
                "roles": {
                    "executor": {
                        "endpoint_id": "deepseek-main",
                        "model_id": "deepseek-chat",
                    }
                }
            },
            actor="octocat",
        )


async def test_missing_endpoint_secret_blocks_worker_preflight(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    await _stored_project()
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={**project_model_endpoints.endpoint_preset("deepseek"), "id": "deepseek-main"},
    )
    await project_model_routing.set_project_model_routing(
        "sports-cms",
        {
            "roles": {
                "executor": {
                    "endpoint_id": "deepseek-main",
                    "model_id": "deepseek-chat",
                }
            }
        },
        actor="octocat",
    )
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="github",
        token="ghp_octocat-token-1234",
    )
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "refused"
    assert result["reason"] == "model_routing"
    assert result["blockers"][0]["message"] == "Project model routing is invalid."
    assert dispatch_recorder.calls == []
