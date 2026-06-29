from __future__ import annotations

from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

from agent import (
    model_endpoint_adapters,
    project_model_endpoints,
    project_registry,
    project_secrets,
)


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)

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


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(project_secrets, "_client", lambda: client)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return client


async def _project() -> None:
    await project_registry.upsert_delivery_project(
        project_registry.default_delivery_project(
            project_id="sports-cms",
            name="Sports CMS",
            tracker_config={"project_id": "linear-sports"},
            vcs_config={"owner": "example", "repo": "sports-cms"},
            membership={"users": ["octocat"]},
        )
    )


async def _secret(name: str, value: str = "provider-secret-1234") -> None:
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name=name,
        value=value,
        updated_by="octocat",
    )


def _models_transport(*models: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer provider-secret-1234"
        return httpx.Response(
            200,
            json={"data": [{"id": model} for model in models]},
        )

    return httpx.MockTransport(handler)


async def test_openai_compatible_adapter_discovers_models_and_keeps_token_server_side(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await _secret("CUSTOM_MODEL_API_KEY")
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("openai_compatible"),
            "id": "custom-main",
            "model_ids": [],
            "default_headers": {"X-Workspace": "sports-cms"},
        },
    )

    validation = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="custom-main",
        requested_model="custom-large",
        transport_factory=lambda: _models_transport("custom-large"),
    )

    assert validation["ready"] is True
    assert validation["models"] == ["custom-large"]
    assert "provider-secret-1234" not in str(validation)


async def test_ai_hub_preset_uses_generic_adapter(fake_client: _FakeClient) -> None:
    await _project()
    await _secret(project_secrets.AI_HUB_API_KEY_SECRET)
    endpoint = await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={**project_model_endpoints.endpoint_preset("ai_hub"), "id": "ai-hub-main"},
    )

    adapter = model_endpoint_adapters.adapter_for_endpoint(endpoint)
    validation = await adapter.validate(
        endpoint,
        project_id="sports-cms",
        environment="default",
        requested_model="gpt-5.5",
        transport_factory=lambda: _models_transport("gpt-5.5"),
    )

    assert isinstance(adapter, model_endpoint_adapters.OpenAICompatibleEndpointAdapter)
    assert validation["ready"] is True
    assert validation["models"] == ["gpt-5.5"]


async def test_runtime_config_supports_chat_and_responses_invocation(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await _secret("CUSTOM_MODEL_API_KEY")
    endpoint = project_model_endpoints.normalize_endpoint(
        {
            **project_model_endpoints.endpoint_preset("openai_compatible"),
            "id": "custom-responses",
            "api_path": "/responses",
            "default_headers": {"X-Workspace": "configured"},
        }
    )
    adapter = model_endpoint_adapters.adapter_for_endpoint(endpoint)
    secret = await adapter.resolve_secret(
        "sports-cms",
        environment="default",
        endpoint=endpoint,
    )

    runtime = adapter.runtime_config(endpoint, model_id="custom-large", secret=secret)

    assert runtime.model == "custom-large"
    assert runtime.invocation_style == "responses"
    assert runtime.langchain_kwargs() == {
        "api_key": "provider-secret-1234",
        "base_url": "https://api.example.com/v1",
        "default_headers": {"X-Workspace": "configured"},
        "timeout": 60,
        "use_responses_api": True,
    }


async def test_missing_and_invalid_tokens_return_actionable_redacted_errors(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("deepseek"),
            "id": "deepseek-main",
            "model_ids": [],
        },
    )

    missing = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="deepseek-main",
    )
    await _secret("DEEPSEEK_API_KEY")
    invalid = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="deepseek-main",
        transport_factory=lambda: httpx.MockTransport(
            lambda _request: httpx.Response(401, json={"error": "bad token"})
        ),
    )

    assert missing["blockers"][0]["code"] == "missing_secret"
    assert invalid["blockers"] == [
        {
            "code": "invalid_token",
            "message": "Provider rejected the endpoint credentials.",
            "provider_status": 401,
        }
    ]
    assert "provider-secret-1234" not in str(invalid)


async def test_invalid_model_and_provider_errors_are_normalized(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await _secret("ZAI_API_KEY")
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("zai"),
            "id": "zai-main",
            "model_ids": [],
        },
    )

    invalid_model = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="zai-main",
        requested_model="glm-missing",
        transport_factory=lambda: _models_transport("glm-4.5"),
    )
    provider_error = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="zai-main",
        transport_factory=lambda: httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"error": "maintenance"})
        ),
    )

    assert invalid_model["blockers"] == [
        {
            "code": "invalid_model",
            "message": "Model glm-missing is not available on this endpoint.",
        }
    ]
    assert provider_error["blockers"] == [
        {
            "code": "provider_error",
            "message": "Provider returned HTTP 503 during endpoint validation.",
            "provider_status": 503,
        }
    ]
