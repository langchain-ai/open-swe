"""Exercise the credential boundary from workspace lookup to the sandbox proxy."""

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import pytest

from agent.github import app, proxy, sandbox_access
from agent.github.repositories import Repository
from agent.sandboxes import lifecycle
from agent.sandboxes.providers.langsmith import LangSmithProvider
from agent.workspaces.refresh import _create_builder_sandbox
from agent.workspaces.store import WORKSPACES, Workspace
from tests.support.github_sdk import mock_github_sdk


class ListedRepository(TypedDict):
    id: int
    full_name: str


@pytest.fixture
def github_requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def github_repositories() -> list[ListedRepository]:
    """What the App installation can reach, in its listing's order."""
    return [
        {"id": 11, "full_name": "acme/api"},
        {"id": 22, "full_name": "acme/internal"},
    ]


@pytest.fixture
def github(
    monkeypatch: pytest.MonkeyPatch,
    github_requests: list[httpx.Request],
    github_repositories: list[ListedRepository],
) -> Iterator[list[dict[str, object]]]:
    """GitHub issues synthetic tokens encoding their repository permissions."""
    payloads: list[dict[str, object]] = []
    client = httpx2.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        github_requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            body = json.loads(request.content or b"{}")
            requested = body.get("repository_ids")
            # GitHub refuses the whole token once any id is outside the installation.
            if requested is not None and not set(requested) <= {
                repo["id"] for repo in github_repositories
            }:
                return httpx.Response(422, json={"message": "Validation Failed"})
            ids = requested if requested is not None else [11, 22]
            return httpx.Response(
                201,
                json={
                    "token": "repos:" + ",".join(str(repo_id) for repo_id in ids),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": github_repositories})
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
    monkeypatch.setattr(WORKSPACES, "slug_exists", AsyncMock(return_value=True))
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


@pytest.mark.parametrize("slug", ["workspace", "default"])
async def test_every_sandbox_receives_the_installation_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    slug: str,
) -> None:
    """A workspace's preferred repositories route work; they do not limit access."""
    monkeypatch.setattr(WORKSPACES, "slug_exists", AsyncMock(return_value=True))

    await lifecycle._create_sandbox_with_proxy(workspace_slug=slug, thread_id="thread")

    assert injected_auth(github) == ["x-access-token:repos:11,22"]


async def test_a_thread_scoped_to_its_repository_keeps_that_scope_across_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    """A thread an event on a public repository started never gets the installation token."""
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )
    monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())

    backend = await lifecycle.ensure_sandbox_for_thread("thread", workspace_slug="workspace")
    monkeypatch.setitem(proxy.SANDBOX_BACKENDS, "thread", backend)
    assert await proxy.maybe_refresh_proxy_token(
        "thread", now=datetime.now(UTC) + timedelta(hours=1)
    )

    assert injected_auth(github) == ["x-access-token:repos:11", "x-access-token:repos:11"]


async def test_a_callers_scope_cannot_widen_the_threads(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )
    monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())

    await lifecycle.ensure_sandbox_for_thread(
        "thread",
        workspace_slug="workspace",
        github_proxy_repositories=["acme/api", "acme/internal"],
    )

    assert injected_auth(github) == ["x-access-token:repos:11"]


async def test_an_unreadable_thread_scope_fails_the_sandbox_rather_than_widening(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(side_effect=RuntimeError("down"))
    )

    with pytest.raises(RuntimeError):
        await lifecycle.ensure_sandbox_for_thread("thread", workspace_slug="workspace")

    assert github == []


async def test_workspace_lookup_failure_cannot_grant_installation_access(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        WORKSPACES, "slug_exists", AsyncMock(side_effect=RuntimeError("unavailable"))
    )

    with pytest.raises(RuntimeError):
        await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace")

    assert github == []


@pytest.mark.parametrize("cached", [False, True])
async def test_reconnect_gives_an_existing_sandbox_the_installation_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    cached: bool,
) -> None:
    monkeypatch.setattr(WORKSPACES, "slug_exists", AsyncMock(return_value=True))
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

    assert injected_auth(github) == ["x-access-token:repos:11,22"]


async def test_base_image_keeps_installation_access_and_callers_can_narrow_it(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(WORKSPACES, "slug_exists", AsyncMock(return_value=True))
    await lifecycle._create_sandbox_with_proxy(workspace_slug="workspace", source="base")
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", github_proxy_repositories=["acme/internal"]
    )
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", github_proxy_repositories=[]
    )

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:22", ""]


@pytest.mark.parametrize("repos", [["acme/api"], []])
async def test_workspace_builder_gets_the_installation_token(
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

    assert injected_auth(github) == ["x-access-token:repos:11,22"]


@pytest.mark.parametrize("narrowed", [["acme/api"], []])
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
    narrowed: list[str],
    path: str,
) -> None:
    monkeypatch.setattr(WORKSPACES, "slug_exists", AsyncMock(return_value=True))
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", github_proxy_repositories=narrowed
    )
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
    if narrowed:
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

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:11,22"]
    config = github[-1]["proxy_config"]
    assert isinstance(config, dict)
    assert custom in config["rules"]


async def test_missing_workspace_does_not_inherit_default_access(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    async def exists(slug: str) -> bool:
        return slug == "default"

    monkeypatch.setattr(WORKSPACES, "slug_exists", exists)
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
        await lifecycle._create_sandbox_with_proxy(
            workspace_slug="workspace", github_proxy_repositories=["acme/api"]
        )
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


def listing_calls(requests: list[httpx.Request]) -> int:
    return sum(request.url.path == "/installation/repositories" for request in requests)


async def stored_repository(full_name: str, github_id: int | None = None) -> None:
    await Repository(full_name=full_name).save()
    if github_id is not None:
        await Repository.record_github_ids(
            {full_name.lower(): github_id}, checked_at=datetime.now(UTC)
        )


async def stored_github_id(full_name: str) -> int | None:
    return (await Repository.by_keys([full_name.lower()]))[full_name.lower()].github_id


@pytest.mark.usefixtures("registry_db", "github")
async def test_first_use_lists_the_installation_once_and_stores_the_id(
    github_requests: list[httpx.Request],
) -> None:
    await stored_repository("acme/api")

    first = await sandbox_access.repository_token(["acme/api"])
    app.clear_app_token_cache()
    second = await sandbox_access.repository_token(["acme/api"])

    assert first.token == second.token == "repos:11"
    assert listing_calls(github_requests) == 1
    assert await stored_github_id("acme/api") == 11


@pytest.mark.usefixtures("registry_db", "github")
async def test_repository_outside_the_installation_is_looked_for_again_after_the_interval(
    github_requests: list[httpx.Request],
    github_repositories: list[ListedRepository],
) -> None:
    await stored_repository("acme/api")
    await stored_repository("acme/new")
    repos = ["acme/api", "acme/new"]

    assert (await sandbox_access.repository_token(repos)).token == "repos:11"
    app.clear_app_token_cache()
    assert (await sandbox_access.repository_token(repos)).token == "repos:11"
    assert listing_calls(github_requests) == 1

    github_repositories.append({"id": 33, "full_name": "acme/new"})
    stale = datetime.now(UTC) - sandbox_access.RECHECK_MISSING_AFTER - timedelta(seconds=1)
    await Repository.record_github_ids({"acme/new": None}, checked_at=stale)
    app.clear_app_token_cache()

    assert (await sandbox_access.repository_token(repos)).token == "repos:11,33"
    assert listing_calls(github_requests) == 2


@pytest.mark.usefixtures("registry_db", "github")
async def test_refused_stored_id_is_replaced_from_the_listing(
    github_requests: list[httpx.Request],
) -> None:
    await stored_repository("acme/api", github_id=99)

    access = await sandbox_access.repository_token(["acme/api"])

    assert access.token == "repos:11"
    assert listing_calls(github_requests) == 1
    assert await stored_github_id("acme/api") == 11


@pytest.mark.usefixtures("registry_db", "github")
async def test_renamed_repository_keeps_its_stored_id(
    github_repositories: list[ListedRepository],
) -> None:
    await stored_repository("acme/api", github_id=11)
    await stored_repository("acme/internal")
    github_repositories[:] = [
        {"id": 11, "full_name": "acme/renamed"},
        {"id": 22, "full_name": "acme/internal"},
        {"id": 33, "full_name": "acme/api"},
    ]

    access = await sandbox_access.repository_token(["acme/api", "acme/internal"])

    assert access.token == "repos:11,22"
    assert await stored_github_id("acme/api") == 11


@pytest.mark.usefixtures("registry_db", "github")
async def test_refused_ids_fail_closed_after_one_listing(
    monkeypatch: pytest.MonkeyPatch,
    github_requests: list[httpx.Request],
) -> None:
    await stored_repository("acme/api", github_id=11)
    monkeypatch.setattr(
        sandbox_access,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(side_effect=[(None, None), ("discovery-token", None), (None, None)]),
    )

    with pytest.raises(RuntimeError, match="repository token is unavailable"):
        await sandbox_access.repository_token(["acme/api"])

    assert listing_calls(github_requests) == 1


@pytest.mark.usefixtures("registry_db", "github")
async def test_unreadable_stored_ids_fall_back_to_the_listing(
    monkeypatch: pytest.MonkeyPatch,
    github_requests: list[httpx.Request],
) -> None:
    await stored_repository("acme/api", github_id=11)
    monkeypatch.setattr(
        Repository, "by_keys", AsyncMock(side_effect=OSError("database unavailable"))
    )

    assert (await sandbox_access.repository_token(["acme/api"])).token == "repos:11"
    assert listing_calls(github_requests) == 1


@pytest.mark.usefixtures("registry_db")
async def test_reconnect_restricted_to_one_repository_mints_from_stored_ids(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx.Request],
) -> None:
    await stored_repository("acme/api")
    await stored_repository("acme/internal")
    monkeypatch.setattr(
        WORKSPACES,
        "get",
        AsyncMock(return_value=Workspace(slug="workspace", repos=["acme/api", "acme/internal"])),
    )
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="workspace", thread_id="thread"
    )
    app.clear_app_token_cache()

    await lifecycle._connect_existing_sandbox(
        "thread",
        cached=backend,
        sandbox_id=backend.id,
        github_proxy_repositories=["acme/api"],
        workspace_slug="workspace",
    )

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:11"]
    assert listing_calls(github_requests) == 1
