import json
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import ValidationError

from agent.dashboard import environment_auth, environments, routes
from agent.dashboard.environments import ENVIRONMENTS, EnvironmentCreate
from agent.encryption import decrypt_token


@pytest.fixture(autouse=True)
def encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _create_environment() -> None:
    await ENVIRONMENTS.create(EnvironmentCreate(name="Staging"), "admin")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    return app


async def test_auth_proxy_roundtrip_encrypts_masks_and_resolves(fake_store) -> None:
    await _create_environment()
    body = {
        "rules": [
            {
                "id": "anthropic",
                "host": "api.anthropic.com",
                "header": "x-api-key",
                "scheme": "api_key",
                "credential": "synthetic-anthropic-secret",
            },
            {
                "id": "openai",
                "host": "API.OPENAI.COM",
                "scheme": "bearer",
                "credential": "synthetic-openai-secret",
            },
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.put("/dashboard/api/environments/staging/auth-proxy", json=body)
        fetched = await client.get("/dashboard/api/environments/staging/auth-proxy")

    expected = {
        "rules": [
            {
                "id": "anthropic",
                "host": "api.anthropic.com",
                "header": "x-api-key",
                "scheme": "api_key",
                "has_credential": True,
            },
            {
                "id": "openai",
                "host": "api.openai.com",
                "header": "Authorization",
                "scheme": "bearer",
                "has_credential": True,
            },
        ]
    }
    assert response.status_code == 200
    assert response.json() == expected
    assert fetched.json() == expected
    assert "synthetic" not in response.text
    assert "synthetic" not in fetched.text

    stored = fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE)["staging"]
    assert "synthetic" not in json.dumps(stored)
    assert {
        rule_id: decrypt_token(value) for rule_id, value in stored["encrypted_credentials"].items()
    } == {
        "anthropic": "synthetic-anthropic-secret",
        "openai": "synthetic-openai-secret",
    }
    assert "auth" not in json.dumps(fake_store.values(["environments"])["staging"])
    assert await environment_auth.resolve_environment_auth_rules("staging") == [
        {
            "name": "environment-auth-anthropic",
            "match_hosts": ["api.anthropic.com"],
            "headers": [
                {"name": "x-api-key", "type": "opaque", "value": "synthetic-anthropic-secret"}
            ],
        },
        {
            "name": "environment-auth-openai",
            "match_hosts": ["api.openai.com"],
            "headers": [
                {
                    "name": "Authorization",
                    "type": "opaque",
                    "value": "Bearer synthetic-openai-secret",
                }
            ],
        },
    ]


async def test_full_replacement_retains_rotates_and_deletes_credentials(fake_store) -> None:
    await _create_environment()
    await environment_auth.save_environment_auth(
        "staging",
        environment_auth.EnvironmentAuthUpdate.model_validate(
            {
                "rules": [
                    {
                        "id": "keep",
                        "host": "keep.example.com",
                        "scheme": "api_key",
                        "credential": "synthetic-keep",
                    },
                    {
                        "id": "remove",
                        "host": "remove.example.com",
                        "scheme": "bearer",
                        "credential": "synthetic-remove",
                    },
                ]
            }
        ),
    )

    retained = await environment_auth.save_environment_auth(
        "staging",
        environment_auth.EnvironmentAuthUpdate.model_validate(
            {"rules": [{"id": "keep", "host": "moved.example.com", "scheme": "bearer"}]}
        ),
    )
    assert retained["rules"][0]["has_credential"] is True
    stored = fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE)["staging"]
    assert set(stored["encrypted_credentials"]) == {"keep"}
    assert decrypt_token(stored["encrypted_credentials"]["keep"]) == "synthetic-keep"

    await environment_auth.save_environment_auth(
        "staging",
        environment_auth.EnvironmentAuthUpdate.model_validate(
            {
                "rules": [
                    {
                        "id": "keep",
                        "host": "moved.example.com",
                        "scheme": "bearer",
                        "credential": "synthetic-rotated",
                    }
                ]
            }
        ),
    )
    stored = fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE)["staging"]
    assert decrypt_token(stored["encrypted_credentials"]["keep"]) == "synthetic-rotated"

    await environment_auth.save_environment_auth(
        "staging", environment_auth.EnvironmentAuthUpdate(rules=[])
    )
    stored = fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE)["staging"]
    assert stored == {"rules": [], "encrypted_credentials": {}}


async def test_resolver_aggregates_multiple_headers_for_the_same_host(fake_store) -> None:
    await environment_auth.save_environment_auth(
        "staging",
        environment_auth.EnvironmentAuthUpdate.model_validate(
            {
                "rules": [
                    {
                        "id": "client-id",
                        "host": "api.example.com",
                        "header": "x-client-id",
                        "scheme": "api_key",
                        "credential": "synthetic-client",
                    },
                    {
                        "id": "api-key",
                        "host": "api.example.com",
                        "header": "x-api-key",
                        "scheme": "api_key",
                        "credential": "synthetic-key",
                    },
                ]
            }
        ),
    )

    assert await environment_auth.resolve_environment_auth_rules("staging") == [
        {
            "name": "environment-auth-client-id",
            "match_hosts": ["api.example.com"],
            "headers": [
                {"name": "x-client-id", "type": "opaque", "value": "synthetic-client"},
                {"name": "x-api-key", "type": "opaque", "value": "synthetic-key"},
            ],
        }
    ]


async def test_new_rule_requires_a_credential_without_echoing_request(fake_store) -> None:
    await _create_environment()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            json={
                "rules": [
                    {
                        "id": "new-rule",
                        "host": "secret-marker.example.com",
                        "scheme": "api_key",
                    }
                ]
            },
        )
    assert response.status_code == 422
    assert "credential" in response.json()["detail"].lower()
    assert "secret-marker" not in response.text
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}


@pytest.mark.parametrize(
    "rule",
    [
        {"id": "rule", "host": "https://api.example.com", "scheme": "bearer"},
        {"id": "rule", "host": "127.0.0.1", "scheme": "bearer"},
        {"id": "rule", "host": "*.example.com", "scheme": "bearer"},
        {"id": "rule", "host": "api.example.com:443", "scheme": "bearer"},
        {"id": "rule", "host": "github.com", "scheme": "bearer"},
        {"id": "rule", "host": "api.github.com", "scheme": "bearer"},
        {"id": "rule", "host": "example..com", "scheme": "bearer"},
        {"id": "INVALID ID", "host": "api.example.com", "scheme": "bearer"},
        {"id": "rule", "host": "api.example.com", "header": "Host", "scheme": "bearer"},
        {
            "id": "rule",
            "host": "api.example.com",
            "header": "content-length",
            "scheme": "bearer",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "header": "transfer-encoding",
            "scheme": "bearer",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "header": "bad header",
            "scheme": "bearer",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "scheme": "bearer",
            "credential": "synthetic\ncredential",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "scheme": "bearer",
            "credential": "   ",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "scheme": "bearer",
            "credential": "synthetic\x00credential",
        },
        {
            "id": "rule",
            "host": "api.example.com",
            "scheme": "bearer",
            "credential": "synthetic-\N{SNOWMAN}",
        },
    ],
)
async def test_invalid_rules_are_rejected_without_echoing_values(fake_store, rule) -> None:
    await _create_environment()
    rule.setdefault("credential", "synthetic-credential")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.put(
            "/dashboard/api/environments/staging/auth-proxy", json={"rules": [rule]}
        )
    assert response.status_code == 422
    assert "synthetic" not in response.text
    assert "api.example.com" not in response.text
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}


@pytest.mark.parametrize(
    "rules",
    [
        [
            {
                "id": "duplicate",
                "host": "one.example.com",
                "scheme": "bearer",
                "credential": "synthetic-one",
            },
            {
                "id": "duplicate",
                "host": "two.example.com",
                "scheme": "api_key",
                "credential": "synthetic-two",
            },
        ],
        [
            {
                "id": "one",
                "host": "SAME.EXAMPLE.COM",
                "header": "X-API-Key",
                "scheme": "api_key",
                "credential": "synthetic-one",
            },
            {
                "id": "two",
                "host": "same.example.com",
                "header": "x-api-key",
                "scheme": "api_key",
                "credential": "synthetic-two",
            },
        ],
    ],
)
async def test_duplicate_ids_and_destinations_are_rejected(fake_store, rules) -> None:
    await _create_environment()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.put(
            "/dashboard/api/environments/staging/auth-proxy", json={"rules": rules}
        )
    assert response.status_code == 422
    assert "synthetic" not in response.text
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}


async def test_rule_count_and_credential_size_are_bounded(fake_store) -> None:
    await _create_environment()
    app = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        too_many = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            json={
                "rules": [
                    {
                        "id": f"rule-{index}",
                        "host": f"api-{index}.example.com",
                        "scheme": "api_key",
                        "credential": "synthetic",
                    }
                    for index in range(21)
                ]
            },
        )
        too_large = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            json={
                "rules": [
                    {
                        "id": "rule",
                        "host": "api.example.com",
                        "scheme": "api_key",
                        "credential": "s" * 8193,
                    }
                ]
            },
        )
    assert too_many.status_code == 422
    assert too_large.status_code == 422
    assert "ssss" not in too_large.text
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}


async def test_routes_require_an_admin_and_an_existing_environment(fake_store, monkeypatch) -> None:
    await _create_environment()
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    session = {"sub": "member", "email": "member@example.com"}
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.get("/dashboard/api/environments/staging/auth-proxy")
        ).status_code == 403
        assert (
            await client.put("/dashboard/api/environments/staging/auth-proxy", json={"rules": []})
        ).status_code == 403
        session = {"sub": "admin", "email": "admin@example.com"}
        assert (
            await client.get("/dashboard/api/environments/missing/auth-proxy")
        ).status_code == 404
        assert (
            await client.put("/dashboard/api/environments/missing/auth-proxy", json={"rules": []})
        ).status_code == 404


async def test_malformed_request_errors_are_sanitized(fake_store) -> None:
    await _create_environment()
    app = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        wrong_type = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            json={"rules": [{"credential": {"synthetic-secret": "synthetic-secret"}}]},
        )
        extra_field = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            json={"rules": [], "synthetic-secret": "synthetic-secret"},
        )
        invalid_json = await client.put(
            "/dashboard/api/environments/staging/auth-proxy",
            content=b'{"rules": ["synthetic-secret"',
            headers={"Content-Type": "application/json"},
        )
    for response in (wrong_type, extra_field, invalid_json):
        assert response.status_code == 422
        assert "synthetic-secret" not in response.text
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}


async def test_missing_corrupt_and_undecryptable_records_fail_closed(fake_store) -> None:
    assert await environment_auth.resolve_environment_auth_rules("missing") == []

    fake_store.seed(
        environment_auth.ENVIRONMENT_AUTH_NAMESPACE,
        "corrupt",
        {"rules": "synthetic-secret", "encrypted_credentials": {}},
    )
    with pytest.raises(ValidationError):
        await environment_auth.resolve_environment_auth_rules("corrupt")

    fake_store.seed(
        environment_auth.ENVIRONMENT_AUTH_NAMESPACE,
        "undecryptable",
        {
            "rules": [
                {
                    "id": "rule",
                    "host": "api.example.com",
                    "header": "Authorization",
                    "scheme": "bearer",
                }
            ],
            "encrypted_credentials": {"rule": "synthetic-invalid-ciphertext"},
        },
    )
    with pytest.raises(ValueError, match="could not be decrypted"):
        await environment_auth.resolve_environment_auth_rules("undecryptable")


async def test_removing_environment_deletes_auth_before_the_environment_record(
    fake_store, monkeypatch
) -> None:
    await _create_environment()
    await environment_auth.save_environment_auth(
        "staging",
        environment_auth.EnvironmentAuthUpdate.model_validate(
            {
                "rules": [
                    {
                        "id": "rule",
                        "host": "api.example.com",
                        "scheme": "api_key",
                        "credential": "synthetic-secret",
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr("agent.dashboard.environments._delete_snapshot", AsyncMock())
    monkeypatch.setattr("agent.dashboard.environment_refresh.remove_refresh_cron", AsyncMock())

    assert await ENVIRONMENTS.remove("staging") is True
    assert fake_store.values(environment_auth.ENVIRONMENT_AUTH_NAMESPACE) == {}
    assert fake_store.values(["environments"]) == {}


async def test_auth_cleanup_failure_preserves_the_environment(fake_store, monkeypatch) -> None:
    await _create_environment()
    cleanup = AsyncMock(side_effect=RuntimeError("store unavailable"))
    monkeypatch.setattr(environment_auth, "delete_environment_auth", cleanup)

    with pytest.raises(RuntimeError, match="store unavailable"):
        await ENVIRONMENTS.remove("staging")
    assert "staging" in fake_store.values(["environments"])


async def test_strict_environment_resolution_propagates_store_failures(monkeypatch) -> None:
    get = AsyncMock(side_effect=RuntimeError("store unavailable"))
    monkeypatch.setattr(ENVIRONMENTS, "get", get)

    assert await environments.resolve_default_environment() is None
    assert await environments.resolve_environment("staging") is None
    with pytest.raises(RuntimeError, match="store unavailable"):
        await environments.resolve_default_environment(fail_on_error=True)
    with pytest.raises(RuntimeError, match="store unavailable"):
        await environments.resolve_environment("staging", fail_on_error=True)
