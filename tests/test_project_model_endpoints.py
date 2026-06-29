from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from agent import project_model_endpoints, project_registry, project_secrets


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


async def test_model_endpoint_presets_cover_v1_providers(fake_client: _FakeClient) -> None:
    presets = project_model_endpoints.endpoint_presets()

    assert {preset["provider_type"] for preset in presets} == {
        "ai_hub",
        "deepseek",
        "zai",
        "openai_compatible",
        "opencode",
    }
    assert all(preset["secret_name"] for preset in presets)


async def test_upsert_endpoint_redacts_secret_and_validates_ready(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await project_secrets.upsert_project_secret(
        "sports-cms",
        environment="default",
        name="DEEPSEEK_API_KEY",
        value="deepseek-secret-1234",
        updated_by="octocat",
    )

    endpoint = await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("deepseek"),
            "id": "deepseek-main",
            "default_headers": {"X-Team": "delivery"},
        },
    )
    validation = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="deepseek-main",
    )

    assert endpoint["secret"] == {
        "name": "DEEPSEEK_API_KEY",
        "connected": True,
        "environment": "default",
    }
    assert endpoint["default_headers"] == ["X-Team"]
    assert "deepseek-secret-1234" not in str(endpoint)
    assert validation["ready"] is True
    assert validation["models"] == ["deepseek-chat", "deepseek-reasoner"]


async def test_missing_secret_and_disabled_endpoint_block_validation(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={
            **project_model_endpoints.endpoint_preset("deepseek"),
            "id": "deepseek-main",
            "disabled": True,
        },
    )

    validation = await project_model_endpoints.validate_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="deepseek-main",
    )

    assert validation["ready"] is False
    assert [blocker["code"] for blocker in validation["blockers"]] == [
        "endpoint_disabled",
        "missing_secret",
    ]


async def test_delete_model_endpoint_removes_environment_record(
    fake_client: _FakeClient,
) -> None:
    await _project()
    await project_model_endpoints.upsert_model_endpoint(
        "sports-cms",
        environment="default",
        payload={**project_model_endpoints.endpoint_preset("opencode"), "id": "opencode-local"},
    )

    deleted = await project_model_endpoints.delete_model_endpoint(
        "sports-cms",
        environment="default",
        endpoint_id="opencode-local",
    )
    endpoints = await project_model_endpoints.list_model_endpoints(
        "sports-cms",
        environment="default",
    )

    assert deleted["deleted"] is True
    assert endpoints["items"] == []
