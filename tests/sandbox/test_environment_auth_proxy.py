"""Environment credentials reach only the proxy, and refresh with its auth."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from cryptography.fernet import Fernet

from agent.dashboard.environment_auth import EnvironmentAuthUpdate, save_environment_auth
from agent.dashboard.environments import ENVIRONMENTS, Environment, EnvironmentCreate
from agent.github import proxy
from agent.sandboxes import lifecycle
from agent.sandboxes.providers import langsmith
from agent.sandboxes.state import SandboxBackendProxy
from tests.conftest import FakeStore


def auth_rule(value: str = "test-credential") -> dict[str, Any]:
    return {
        "name": "environment-auth-staging",
        "match_hosts": ["api.staging.example.com"],
        "headers": [{"name": "Authorization", "type": "opaque", "value": f"Bearer {value}"}],
    }


@pytest.fixture
def proxy_http(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-control-key")
    monkeypatch.delenv("STAGEHAND_MODEL_API_KEY", raising=False)
    client = MagicMock()
    client.patch = AsyncMock(
        return_value=httpx2.Response(200, request=httpx2.Request("PATCH", "https://example.com"))
    )
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(langsmith.httpx2, "AsyncClient", factory)
    return client.patch


async def test_proxy_resolves_rotation_and_removal_without_persisting_values(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock
) -> None:
    resolve = AsyncMock(side_effect=[[auth_rule()], [auth_rule("rotated")], []])
    monkeypatch.setattr(langsmith, "resolve_environment_auth_rules", resolve, raising=False)
    base = {"rules": [{"name": "public", "match_hosts": ["public.example.com"]}], "callbacks": None}
    for _ in range(3):
        await langsmith.configure_github_proxy(
            "sandbox", "test-github", base_proxy_config=base, environment_slug="staging"
        )

    first, rotated, removed = [
        call.kwargs["json"]["proxy_config"] for call in proxy_http.await_args_list
    ]
    assert first["rules"][0] == base["rules"][0]
    assert auth_rule() in first["rules"]
    assert auth_rule("rotated") in rotated["rules"]
    assert all(rule["name"] != "environment-auth-staging" for rule in removed["rules"])
    assert "test-credential" not in str(base)
    assert "environment_slug" not in first
    assert all(call.args == ("staging",) for call in resolve.await_args_list)


@pytest.mark.parametrize(
    "conflict",
    [
        {"name": "custom", "match_hosts": ["*.example.com"]},
        {"name": "custom", "match_hosts": ["api.staging.example.com"]},
        {"name": "environment-auth-staging", "match_hosts": ["unrelated.example.com"]},
    ],
)
async def test_conflicting_proxy_rules_fail_before_patching(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock, conflict: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        langsmith,
        "resolve_environment_auth_rules",
        AsyncMock(return_value=[auth_rule()]),
        raising=False,
    )
    with pytest.raises(ValueError, match="conflict"):
        await langsmith.configure_github_proxy(
            "sandbox", "github", base_proxy_config={"rules": [conflict]}, environment_slug="staging"
        )
    proxy_http.assert_not_awaited()


async def test_managed_proxy_host_conflict_is_rejected(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock
) -> None:
    rule = auth_rule()
    rule["match_hosts"] = ["api.anthropic.com"]
    monkeypatch.setenv("STAGEHAND_MODEL", "anthropic/claude-sonnet-4-5")
    monkeypatch.setenv("STAGEHAND_MODEL_API_KEY", "test-stagehand")
    monkeypatch.setattr(
        langsmith, "resolve_environment_auth_rules", AsyncMock(return_value=[rule]), raising=False
    )
    with pytest.raises(ValueError, match="conflict"):
        await langsmith.configure_github_proxy("sandbox", "github", environment_slug="staging")
    proxy_http.assert_not_awaited()


@pytest.mark.parametrize(
    "host, config",
    [
        (
            "api.staging.example.com",
            {"callbacks": [{"match_hosts": ["*.example.com"], "url": "https://auth.example.net"}]},
        ),
        ("storage.googleapis.com", {"rules": [{"name": "google-auth", "type": "gcp"}]}),
        ("storage.googleapis.com", {"rules": [{"name": "google-auth", "type": " GCP "}]}),
        (
            "api.staging.example.com",
            {
                "callbacks": [
                    {
                        "match_hosts": ["*.example.com"],
                        "url": "https://auth.example.net",
                        "enabled": False,
                    }
                ]
            },
        ),
        ("s3.us-west-2.amazonaws.com", {"rules": [{"name": "aws-auth", "type": "aws"}]}),
    ],
)
async def test_provider_and_callback_auth_cannot_be_shadowed(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock, host: str, config: dict[str, Any]
) -> None:
    rule = auth_rule()
    rule["match_hosts"] = [host]
    monkeypatch.setattr(langsmith, "resolve_environment_auth_rules", AsyncMock(return_value=[rule]))
    with pytest.raises(ValueError, match="conflict"):
        await langsmith.configure_github_proxy(
            "sandbox", "github", base_proxy_config=config, environment_slug="staging"
        )
    proxy_http.assert_not_awaited()


async def test_disabled_rules_do_not_conflict_with_environment_auth(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock
) -> None:
    monkeypatch.setattr(
        langsmith, "resolve_environment_auth_rules", AsyncMock(return_value=[auth_rule()])
    )
    await langsmith.configure_github_proxy(
        "sandbox",
        "github",
        base_proxy_config={
            "rules": [{"name": "disabled", "match_hosts": ["*.example.com"], "enabled": False}]
        },
        environment_slug="staging",
    )
    assert auth_rule() in proxy_http.await_args.kwargs["json"]["proxy_config"]["rules"]


async def test_credential_lookup_failure_does_not_patch_proxy(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock
) -> None:
    monkeypatch.setattr(
        langsmith,
        "resolve_environment_auth_rules",
        AsyncMock(side_effect=RuntimeError("credential unavailable")),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="credential unavailable"):
        await langsmith.configure_github_proxy("sandbox", "github", environment_slug="staging")
    proxy_http.assert_not_awaited()


async def test_proxy_errors_do_not_expose_echoed_environment_credentials(
    monkeypatch: pytest.MonkeyPatch, proxy_http: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        langsmith,
        "resolve_environment_auth_rules",
        AsyncMock(return_value=[auth_rule()]),
        raising=False,
    )
    proxy_http.return_value = httpx2.Response(
        400,
        text="invalid credential test-credential",
        request=httpx2.Request("PATCH", "https://example.com"),
    )
    with pytest.raises(RuntimeError, match="configure environment auth proxy") as error:
        await langsmith.configure_github_proxy("sandbox", "github", environment_slug="staging")
    assert "test-credential" not in str(error.value)
    assert "test-credential" not in caplog.text
    assert error.value.__suppress_context__


async def test_reused_sandbox_binds_environment_and_refreshes_without_replacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    sandbox = MagicMock(id="existing", aexecute=AsyncMock())
    base = {"rules": [{"name": "public", "match_hosts": ["public.example.com"]}]}
    with (
        patch.dict(
            lifecycle.SANDBOX_BACKENDS,
            {"thread": SandboxBackendProxy(sandbox, thread_id="thread")},
            clear=True,
        ),
        patch.object(lifecycle, "get_sandbox_id_from_metadata", AsyncMock(return_value="existing")),
        patch.object(
            lifecycle,
            "get_sandbox_metadata",
            AsyncMock(return_value={"sandbox_base_proxy_config": base}),
        ),
        patch.object(
            lifecycle,
            "get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("github", None)),
        ),
        patch.object(lifecycle, "configure_github_proxy", AsyncMock()) as configure,
        patch.object(lifecycle.client.threads, "update", AsyncMock()) as update,
        patch.object(lifecycle, "_create_sandbox_with_proxy", AsyncMock()) as create,
    ):
        await lifecycle.ensure_sandbox_for_thread("thread", environment_slug="staging")
    configure.assert_awaited_once_with(
        "existing", "github", base_proxy_config=base, environment_slug="staging"
    )
    create.assert_not_awaited()
    assert update.await_args.kwargs["metadata"]["sandbox_environment_slug"] == "staging"
    assert "credential" not in str(update.await_args.kwargs)
    proxy.clear_proxy_token_expiry("thread")


async def test_mid_run_token_refresh_preserves_environment_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    backend = MagicMock(id="sandbox")
    proxy.record_proxy_token_expiry("thread", datetime.now(UTC), environment_slug="staging")
    with (
        patch.dict(proxy.SANDBOX_BACKENDS, {"thread": backend}, clear=True),
        patch.object(
            proxy,
            "get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("github", None)),
        ),
        patch.object(langsmith, "configure_github_proxy", AsyncMock()) as configure,
    ):
        assert await proxy.refresh_proxy_token("thread")
    configure.assert_awaited_once_with("sandbox", "github", environment_slug="staging")
    assert proxy.get_recorded_proxy_environment("thread") == "staging"
    proxy.clear_proxy_token_expiry("thread")
    assert proxy.get_recorded_proxy_environment("thread") is None


async def test_snapshot_builder_uses_environment_auth() -> None:
    from agent.dashboard.environment_refresh import _create_builder_sandbox

    with (
        patch(
            "agent.github.app.get_github_app_installation_token", AsyncMock(return_value="github")
        ),
        patch.object(langsmith, "create_langsmith_sandbox", AsyncMock()) as create,
    ):
        await _create_builder_sandbox(Environment(slug="staging"), "snapshot")
    assert create.await_args.kwargs["environment_slug"] == "staging"
    assert "credential" not in str(create.await_args.kwargs["create_params"])


async def test_reset_preserves_environment_auth_after_process_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    old = MagicMock(id="old-sandbox")
    new = MagicMock(id="new-sandbox", aexecute=AsyncMock())
    with (
        patch.dict(lifecycle.SANDBOX_BACKENDS, {}, clear=True),
        patch.object(lifecycle, "get_sandbox_id_from_metadata", AsyncMock(return_value=old.id)),
        patch.object(
            lifecycle,
            "get_sandbox_metadata",
            AsyncMock(return_value={"sandbox_environment_slug": "staging"}),
        ),
        patch.object(
            lifecycle, "create_langsmith_sandbox_from_params", AsyncMock(return_value=new)
        ),
        patch.object(
            lifecycle, "_resolve_proxy_token", AsyncMock(return_value=("github", None, None))
        ),
        patch.object(lifecycle, "configure_github_proxy", AsyncMock()) as configure,
        patch.object(lifecycle.client.threads, "update", AsyncMock()) as update,
    ):
        assert await lifecycle.reset_sandbox_for_thread("reset-thread", {}) == (old.id, new.id)
    configure.assert_awaited_once_with("new-sandbox", "github", environment_slug="staging")
    assert update.await_args.kwargs["metadata"]["sandbox_environment_slug"] == "staging"
    assert proxy.get_recorded_proxy_environment("reset-thread") == "staging"
    proxy.clear_proxy_token_expiry("reset-thread")


@pytest.mark.parametrize("resolved_slug", ["staging", None])
async def test_recreation_replaces_or_clears_the_persisted_environment(
    resolved_slug: str | None,
) -> None:
    with (
        patch.dict(lifecycle.SANDBOX_BACKENDS, {}, clear=True),
        patch.object(lifecycle, "get_sandbox_id_from_metadata", AsyncMock(return_value="old")),
        patch.object(
            lifecycle,
            "_create_sandbox_with_proxy",
            AsyncMock(return_value=MagicMock(id="new", aexecute=AsyncMock())),
        ),
        patch.object(lifecycle, "get_recorded_proxy_environment", return_value=resolved_slug),
        patch.object(lifecycle.client.threads, "update", AsyncMock()) as update,
    ):
        await lifecycle.recreate_sandbox_for_thread("recreated-thread")
    assert "sandbox_environment_slug" in update.await_args.kwargs["metadata"]
    assert update.await_args.kwargs["metadata"]["sandbox_environment_slug"] == resolved_slug


async def test_new_sandbox_injects_stored_credentials_and_persists_only_environment_slug(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, proxy_http: AsyncMock
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("SANDBOX_CREATE_EXTRA_JSON", raising=False)
    await ENVIRONMENTS.create(EnvironmentCreate(name="staging"), "admin")
    await save_environment_auth(
        "staging",
        EnvironmentAuthUpdate.model_validate(
            {
                "rules": [
                    {
                        "id": "api",
                        "host": "api.staging.example.com",
                        "header": "X-API-Key",
                        "scheme": "api_key",
                        "credential": "test-staging-credential",
                    }
                ]
            }
        ),
    )
    sandbox = MagicMock(id="new-sandbox", aexecute=AsyncMock())
    with (
        patch.dict(lifecycle.SANDBOX_BACKENDS, {}, clear=True),
        patch.object(lifecycle, "get_sandbox_id_from_metadata", AsyncMock(return_value=None)),
        patch.object(lifecycle, "create_sandbox", AsyncMock(return_value=sandbox)) as create,
        patch.object(
            lifecycle,
            "get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("test-github", None)),
        ),
        patch.object(lifecycle.client.threads, "update", AsyncMock()) as update,
    ):
        await lifecycle.ensure_sandbox_for_thread("new-thread", environment_slug="staging")
    rule = proxy_http.await_args.kwargs["json"]["proxy_config"]["rules"][-1]
    assert rule == {
        "name": "environment-auth-api",
        "match_hosts": ["api.staging.example.com"],
        "headers": [{"name": "X-API-Key", "type": "opaque", "value": "test-staging-credential"}],
    }
    assert update.await_args.kwargs["metadata"] == {
        "sandbox_id": "new-sandbox",
        "sandbox_environment_slug": "staging",
    }
    assert "test-staging-credential" not in str(create.await_args)
    assert "test-staging-credential" not in str(fake_store.items)
    assert proxy.get_recorded_proxy_environment("new-thread") == "staging"
    proxy.clear_proxy_token_expiry("new-thread")
