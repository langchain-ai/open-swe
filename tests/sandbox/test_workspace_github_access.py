"""Exercise the credential boundary from workspace lookup to the sandbox proxy."""

import base64
import importlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import pytest
from githubkit.exception import RequestError

from agent.github import app, proxy, sandbox_access
from agent.sandboxes import lifecycle
from agent.sandboxes.providers.langsmith import LangSmithProvider
from agent.workspaces.refresh import _create_builder_sandbox
from agent.workspaces.store import WORKSPACES, Workspace
from tests.conftest import FakeStore, post_signed_github_webhook
from tests.support.github_sdk import mock_github_sdk


@pytest.fixture
def github_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def github_expiry() -> str | None:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat()


@pytest.fixture
def github_repositories() -> list[dict[str, str | int]]:
    return [
        {"id": 11, "full_name": "acme/api"},
        {"id": 22, "full_name": "acme/internal"},
    ]


@pytest.fixture
def rejected_repository_ids() -> set[int]:
    return set()


@pytest.fixture
def github(
    monkeypatch: pytest.MonkeyPatch,
    github_requests: list[httpx.Request],
    github_expiry: str | None,
    github_repositories: list[dict[str, str | int]],
    rejected_repository_ids: set[int],
    fake_store: FakeStore,
) -> Iterator[list[dict[str, object]]]:
    """GitHub issues synthetic tokens encoding their repository permissions."""
    payloads: list[dict[str, object]] = []
    client = httpx2.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        github_requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            body = json.loads(request.content or b"{}")
            ids = body.get("repository_ids", [11, 22])
            if set(body.get("repository_ids", [])) & rejected_repository_ids:
                return httpx.Response(422, json={"message": "Repository is no longer accessible"})
            return httpx.Response(
                201,
                json={
                    "token": "repos:" + ",".join(str(repo_id) for repo_id in ids),
                    "expires_at": github_expiry,
                },
            )
        if request.url.path == "/installation/repositories":
            return httpx.Response(
                200,
                json={"repositories": github_repositories},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def handle_proxy(request: httpx2.Request) -> httpx2.Response:
        if request.method == "PATCH":
            payloads.append(json.loads(request.content))
            return httpx2.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def make_client(**kwargs: object) -> httpx2.AsyncClient:
        return client(transport=httpx2.MockTransport(handle_proxy))

    monkeypatch.setattr(httpx2, "AsyncClient", make_client)
    mock_github_sdk(monkeypatch, handle)
    monkeypatch.setattr(app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(app, "GITHUB_APP_PRIVATE_KEY", "test-key")
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(
        lifecycle,
        "create_sandbox",
        AsyncMock(return_value=MagicMock(id="sandbox", aexecute=AsyncMock())),
    )
    monkeypatch.setattr(lifecycle, "maybe_start_update", AsyncMock())
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value={}))
    app.clear_app_token_cache()
    proxy.clear_proxy_token_expiry("thread")
    yield payloads
    app.clear_app_token_cache()
    proxy.clear_proxy_token_expiry("thread")


def injected_auth(payloads: list[dict[str, object]]) -> list[str]:
    # Decode only synthetic test credentials sent to the fake sandbox service.
    values = []
    for payload in payloads:
        config = payload["proxy_config"]
        assert isinstance(config, dict)
        rule = next((rule for rule in config["rules"] if rule["name"] == "github"), None)
        value = rule["headers"][0]["value"] if rule and rule["headers"] else ""
        values.append(base64.b64decode(value.removeprefix("Basic ")).decode() if value else "")
    return values


@pytest.mark.parametrize(
    ("repos", "expected"),
    [
        (["acme/api"], "x-access-token:repos:11"),
        (["other/api"], ""),
        ([], ""),
    ],
)
async def test_sandbox_only_receives_workspace_repository_access(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    repos: list[str],
    expected: str,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=repos))
    )

    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace", thread_id="thread")

    assert injected_auth(github) == [expected]


async def test_workspace_lookup_failure_cannot_grant_installation_access(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(WORKSPACES, "get", AsyncMock(side_effect=RuntimeError("unavailable")))

    with pytest.raises(RuntimeError):
        await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace")

    assert github == []


async def test_followup_reuses_workspace_repository_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", thread_id="thread"
    )
    await lifecycle._connect_existing_sandbox(
        "thread",
        cached=backend,
        sandbox_id=backend.id,
        github_proxy_repositories=None,
        base_proxy_config=None,
        workspace_slug="workspace",
    )

    assert injected_auth(github) == ["x-access-token:repos:11", "x-access-token:repos:11"]
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1


async def test_followup_on_another_worker_reuses_repository_discovery(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    fake_store: FakeStore,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    access = await sandbox_access.workspace_token("workspace")
    assert access.token == "repos:11"
    app.clear_app_token_cache()
    importlib.reload(sandbox_access)

    access = await sandbox_access.workspace_token("workspace")

    assert access.token == "repos:11"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1
    assert sum(r.method == "POST" for r in github_requests) == 3
    persisted = json.dumps({str(key): value for key, value in fake_store.items.items()})
    assert "repos:11" not in persisted


@pytest.mark.parametrize("operation", ["get_item", "put_item"])
async def test_repository_cache_outage_falls_back_to_github(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    fake_store: FakeStore,
    operation: str,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    monkeypatch.setattr(fake_store, operation, AsyncMock(side_effect=RuntimeError("unavailable")))

    access = await sandbox_access.workspace_token("workspace")

    assert access.token == "repos:11"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1


@pytest.mark.parametrize("repository_id", [True, "22", -1])
async def test_malformed_repository_cache_is_replaced_from_github(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    fake_store: FakeStore,
    repository_id: object,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    namespace = sandbox_access._WORKSPACE_REPOSITORIES.namespace
    fake_store.values(namespace)["workspace"]["repositories"] = {"acme/api": repository_id}

    access = await sandbox_access.workspace_token("workspace")

    assert access.token == "repos:11"
    assert fake_store.values(namespace)["workspace"]["repositories"] == {"acme/api": 11}
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 2


@pytest.mark.parametrize("changed_scope", ["app", "installation", "workspace"])
async def test_shared_repository_cache_cannot_cross_scope_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    changed_scope: str,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    slug = "workspace"
    if changed_scope == "app":
        monkeypatch.setattr(app, "GITHUB_APP_ID", "3")
    elif changed_scope == "installation":
        monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "3")
    else:
        slug = "other"
        monkeypatch.setattr(
            WORKSPACES, "get", AsyncMock(return_value=Workspace(slug=slug, repos=["acme/api"]))
        )

    access = await sandbox_access.workspace_token(slug)

    assert access.token == "repos:11"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 2


@pytest.mark.parametrize("minutes", [51, 61])
async def test_token_renewal_reuses_workspace_repository_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    minutes: int,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    later = datetime.now(UTC) + timedelta(minutes=minutes)
    clock = MagicMock(wraps=datetime, now=lambda tz: later)
    monkeypatch.setattr(sandbox_access, "datetime", clock)
    monkeypatch.setattr(app, "datetime", clock)

    access = await sandbox_access.workspace_token("workspace")

    assert access.token == "repos:11"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1
    assert sum(r.method == "POST" for r in github_requests) == 3


@pytest.mark.parametrize("github_expiry", [None, "invalid"])
async def test_tokens_without_valid_expiry_are_not_reused(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    for _ in range(2):
        access = await sandbox_access.workspace_token("workspace")
        assert access.token == "repos:11"

    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1
    assert sum(r.method == "POST" for r in github_requests) == 3


async def test_workspace_access_cannot_bypass_failed_workspace_lookup(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    monkeypatch.setattr(WORKSPACES, "get", AsyncMock(side_effect=RuntimeError("unavailable")))

    with pytest.raises(RuntimeError, match="unavailable"):
        await sandbox_access.workspace_token("workspace")


async def test_expired_workspace_mapping_cannot_hide_github_failure(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    later = datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr(sandbox_access, "datetime", MagicMock(wraps=datetime, now=lambda tz: later))

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("GitHub unavailable")

    mock_github_sdk(monkeypatch, unavailable)
    with pytest.raises(RequestError, match="GitHub unavailable"):
        await sandbox_access.workspace_token("workspace")


async def test_workspace_access_does_not_reuse_a_broader_permission_scope(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace", permissions={"contents": "write"})
    await sandbox_access.workspace_token("workspace", permissions={"contents": "read"})

    bodies = [json.loads(r.content) for r in github_requests if r.method == "POST"]
    assert bodies[-1] == {"repository_ids": [11], "permissions": {"contents": "read"}}


async def test_new_tasks_and_reviewers_reuse_full_workspace_repository_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(
        WORKSPACES,
        "get",
        AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api", "acme/internal"])),
    )
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", thread_id="review", github_proxy_repositories=["acme/api"]
    )
    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace", thread_id="first")
    app.clear_app_token_cache()
    importlib.reload(sandbox_access)
    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace", thread_id="second")

    assert injected_auth(github) == [
        "x-access-token:repos:11",
        "x-access-token:repos:11,22",
        "x-access-token:repos:11,22",
    ]
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1


async def test_workspace_repository_addition_refreshes_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    workspace = Workspace(slug="workspace", repos=["acme/api"])
    monkeypatch.setattr(WORKSPACES, "get", AsyncMock(return_value=workspace))
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:11"
    workspace.repos = ["acme/api", "acme/internal"]
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:11,22"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 2


@pytest.mark.parametrize(
    ("event", "action"),
    [
        ("installation_repositories", "removed"),
        ("installation", "unsuspend"),
        ("repository", "renamed"),
        ("repository", "deleted"),
        ("repository", "transferred"),
    ],
)
async def test_signed_repository_change_invalidates_mapping_across_workers(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    github_repositories: list[dict[str, str | int]],
    event: str,
    action: str,
) -> None:
    from agent.webhooks import common

    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:11"
    github_repositories[:] = [{"id": 33, "full_name": "acme/api"}]

    response = await post_signed_github_webhook(
        event, {"action": action, "installation": {"id": 2}}, secret="test-secret"
    )
    assert response.status_code == 200
    importlib.reload(sandbox_access)
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:33"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 2


@pytest.mark.parametrize(
    ("installation_id", "action", "secret", "status"),
    [
        (3, "renamed", "test-secret", 200),
        (2, "edited", "test-secret", 200),
        (2, "renamed", "wrong-secret", 401),
    ],
)
async def test_unrelated_or_unsigned_events_do_not_invalidate_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
    installation_id: int,
    action: str,
    secret: str,
    status: int,
) -> None:
    from agent.webhooks import common

    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    response = await post_signed_github_webhook(
        "repository", {"action": action, "installation": {"id": installation_id}}, secret=secret
    )
    assert response.status_code == status
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:11"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 1


async def test_invalidation_failure_is_not_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
    fake_store: FakeStore,
) -> None:
    from agent.webhooks import common

    monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        await post_signed_github_webhook(
            "installation_repositories",
            {"action": "added", "installation": {"id": 2}},
            secret="test-secret",
        )


async def test_inflight_discovery_cannot_undo_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_repositories: list[dict[str, str | int]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    discover = sandbox_access._discover_repositories

    async def invalidated_during_discovery(allowed: set[str]) -> dict[str, int]:
        mapping = await discover(allowed)
        await sandbox_access.invalidate_repository_discovery(
            "installation_repositories", {"installation": {"id": 2}}
        )
        github_repositories[:] = [{"id": 33, "full_name": "acme/api"}]
        return mapping

    monkeypatch.setattr(sandbox_access, "_discover_repositories", invalidated_during_discovery)
    await sandbox_access.workspace_token("workspace")
    monkeypatch.setattr(sandbox_access, "_discover_repositories", discover)
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:33"


async def test_missing_webhook_is_reconciled_after_a_day(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_repositories: list[dict[str, str | int]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    github_repositories[:] = [{"id": 33, "full_name": "acme/api"}]
    later = datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr(sandbox_access, "datetime", MagicMock(wraps=datetime, now=lambda tz: later))
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:33"


async def test_previously_unavailable_repository_is_retried_without_workspace_edit(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_repositories: list[dict[str, str | int]],
    github_requests: list[httpx.Request],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/new"]))
    )
    assert (await sandbox_access.workspace_token("workspace")).token is None
    github_repositories.append({"id": 33, "full_name": "acme/new"})
    assert (await sandbox_access.workspace_token("workspace")).token is None
    later = datetime.now(UTC) + timedelta(minutes=6)
    monkeypatch.setattr(sandbox_access, "datetime", MagicMock(wraps=datetime, now=lambda tz: later))
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:33"
    assert sum(r.url.path == "/installation/repositories" for r in github_requests) == 2


async def test_rejected_cached_ids_are_rediscovered_before_retrying_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_repositories: list[dict[str, str | int]],
    rejected_repository_ids: set[int],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await sandbox_access.workspace_token("workspace")
    app.clear_app_token_cache()
    rejected_repository_ids.add(11)
    github_repositories[:] = [{"id": 33, "full_name": "acme/api"}]
    assert (await sandbox_access.workspace_token("workspace")).token == "repos:33"


async def test_refresh_applies_repository_removal(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    workspace = Workspace(slug="workspace", repos=["acme/api", "acme/internal"])
    monkeypatch.setattr(WORKSPACES, "get", AsyncMock(return_value=workspace))
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", thread_id="thread"
    )
    monkeypatch.setitem(proxy.SANDBOX_BACKENDS, "thread", backend)
    workspace.repos = ["acme/api"]

    assert await proxy.refresh_proxy_token("thread")
    workspace.repos = []
    assert await proxy.refresh_proxy_token("thread")

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:11", ""]


@pytest.mark.parametrize("cached", [False, True])
async def test_reconnect_limits_existing_sandbox_to_current_workspace(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    cached: bool,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    backend = MagicMock(id="sandbox", aexecute=AsyncMock())
    monkeypatch.setattr(
        lifecycle,
        "get_sandbox_metadata",
        AsyncMock(
            return_value={
                "sandbox_id": "sandbox",
                "sandbox_base_proxy_config": {
                    "rules": [
                        {
                            "name": "github",
                            "match_hosts": ["github.com"],
                            "headers": [
                                {"name": "Authorization", "type": "opaque", "value": "old-token"}
                            ],
                        }
                    ]
                },
            }
        ),
    )
    monkeypatch.setattr(lifecycle, "create_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())
    if cached:
        monkeypatch.setitem(lifecycle.SANDBOX_CONNECTIONS, "sandbox", backend)

    await lifecycle.ensure_sandbox_for_thread("thread", workspace_slug="workspace")

    assert injected_auth(github) == ["x-access-token:repos:11"]


async def test_base_image_retains_workspace_access_and_reviewer_can_only_narrow_it(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace", source="base")
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", github_proxy_repositories=["acme/internal"]
    )

    assert injected_auth(github) == ["x-access-token:repos:11", ""]


@pytest.mark.parametrize("repos", [["acme/api"], []])
async def test_workspace_builder_uses_same_access_boundary(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    repos: list[str],
) -> None:
    monkeypatch.setattr(
        LangSmithProvider,
        "get_or_create",
        AsyncMock(return_value=MagicMock(id="sandbox", aexecute=AsyncMock())),
    )

    await _create_builder_sandbox(Workspace(slug="workspace", repos=repos), None)

    assert injected_auth(github) == (["x-access-token:repos:11"] if repos else [""])


@pytest.mark.parametrize("repos", [["acme/api"], []])
@pytest.mark.parametrize(
    "path",
    [
        "/acme/api.git/info/refs",
        "/acme/API/git-upload-pack",
        "/ACME/api.git/git-receive-pack",
        "/aCmE/aPi.git/info/lfs/objects/batch",
        "/aCmE/aPi/archive/refs/heads/main.zip",
    ],
)
async def test_git_auth_preserves_repository_scope_for_mixed_case_remotes(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    repos: list[str],
    path: str,
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=repos))
    )
    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace")
    config = github[-1]["proxy_config"]
    assert isinstance(config, dict)
    rules = config["rules"]

    # The service's suffix /* matcher treats the text before /* as a prefix.
    def matches(path: str, pattern: str) -> bool:
        return path.startswith(pattern[:-2]) if pattern.endswith("/*") else path == pattern

    rule = next(
        (
            rule
            for rule in rules
            if any(fnmatchcase("github.com", host) for host in rule["match_hosts"])
            and (
                not rule.get("match_paths")
                or any(matches(path, match) for match in rule["match_paths"])
            )
        ),
        None,
    )
    auth = next(
        (
            header["value"]
            for header in (rule["headers"] if rule else [])
            if header["name"].lower() == "authorization"
        ),
        "",
    )
    if repos:
        assert base64.b64decode(auth.removeprefix("Basic ")).decode() == "x-access-token:repos:11"
    else:
        assert not auth


async def test_expiry_refresh_keeps_workspace_and_custom_proxy_rules(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    custom = {"name": "external", "match_hosts": ["example.com"]}
    monkeypatch.setattr(
        WORKSPACES,
        "get",
        AsyncMock(
            return_value=Workspace(
                slug="workspace",
                repos=["acme/api"],
                create_params={"proxy_config": {"rules": [custom]}},
            )
        ),
    )
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", thread_id="thread"
    )
    monkeypatch.setitem(proxy.SANDBOX_BACKENDS, "thread", backend)

    assert await proxy.maybe_refresh_proxy_token(
        "thread", now=datetime.now(UTC) + timedelta(hours=1)
    )

    assert injected_auth(github) == ["x-access-token:repos:11", "x-access-token:repos:11"]
    config = github[-1]["proxy_config"]
    assert isinstance(config, dict)
    assert custom in config["rules"]


async def test_missing_workspace_does_not_inherit_default_access(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    async def load(slug: str) -> Workspace | None:
        return Workspace(slug="default", repos=["acme/internal"]) if slug == "default" else None

    monkeypatch.setattr(WORKSPACES, "get", load)
    with pytest.raises(ValueError, match="does not exist"):
        await lifecycle._create_sandbox_with_proxy(workspace_slug="deleted")
    assert github == []


async def test_analyzer_resolves_repository_workspace_without_using_user_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    from agent.analyzer import PrepareAnalyzerRunMiddleware

    async def load(slug: str) -> Workspace:
        return Workspace(slug=slug, repos=["acme/api"] if slug == "oss" else [])

    monkeypatch.setattr(WORKSPACES, "get", load)
    monkeypatch.setattr(WORKSPACES, "owner_of_repo", AsyncMock(return_value="oss"))
    monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())
    monkeypatch.setattr(
        "agent.analyzer.resolve_sandbox_work_dir", AsyncMock(return_value="/workspace")
    )
    middleware = PrepareAnalyzerRunMiddleware(
        thread_id="thread",
        config={
            "configurable": {
                "thread_id": "thread",
                "review_style_full_name": "acme/api",
                "review_style_github_token": "user-token-with-broader-access",
            }
        },
    )

    await middleware._prepare({"messages": []}, MagicMock())

    assert injected_auth(github) == ["x-access-token:repos:11"]


async def test_scoped_token_failure_does_not_inject_discovery_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "get", AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api"]))
    )
    monkeypatch.setattr(
        sandbox_access,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(side_effect=[("discovery-token", None), (None, None)]),
    )
    with pytest.raises(RuntimeError, match="repository token is unavailable"):
        await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace")
    assert github == []


async def test_repository_access_uses_later_installation_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            requests.append(body)
            return httpx.Response(
                201, json={"token": "test-token", "expires_at": "2099-01-01T00:00:00Z"}
            )
        page = int(request.url.params.get("page", "1"))
        repos = (
            [{"id": repo_id, "full_name": f"acme/other-{repo_id}"} for repo_id in range(1, 101)]
            if page == 1
            else [{"id": 101, "full_name": "Acme/API"}]
        )
        headers = (
            {
                "Link": '<https://api.github.com/installation/repositories?per_page=100&page=2>; rel="next"'
            }
            if page == 1
            else {}
        )
        return httpx.Response(200, json={"repositories": repos}, headers=headers)

    mock_github_sdk(monkeypatch, handle)
    monkeypatch.setattr(app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(app, "GITHUB_APP_PRIVATE_KEY", "test-key")
    app.clear_app_token_cache()
    try:
        access = await sandbox_access.repository_token(["acme/api"])
        assert access.token == "test-token"
        assert requests[-1] == {"repository_ids": [101]}
    finally:
        app.clear_app_token_cache()
