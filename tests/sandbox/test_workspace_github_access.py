"""Exercise the credential boundary from workspace lookup to the sandbox proxy."""

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import pytest

from agent.github import app, proxy, sandbox_access
from agent.sandboxes import lifecycle
from agent.sandboxes.providers.langsmith import LangSmithProvider
from agent.workspaces.refresh import _create_builder_sandbox
from agent.workspaces.store import WORKSPACES, Workspace
from tests.support.github_sdk import mock_github_sdk


@pytest.fixture
def installation_repositories() -> list[dict[str, object]]:
    return [
        {"id": 11, "full_name": "acme/api", "private": True},
        {"id": 22, "full_name": "acme/internal", "private": True},
        {"id": 33, "full_name": "acme/public", "private": False},
        {"id": 44, "full_name": "acme/unknown"},
    ]


@pytest.fixture
def github(
    monkeypatch: pytest.MonkeyPatch, installation_repositories: list[dict[str, object]]
) -> Iterator[list[dict[str, object]]]:
    """GitHub issues synthetic tokens encoding their repository permissions."""
    payloads: list[dict[str, object]] = []
    client = httpx2.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            body = json.loads(request.content or b"{}")
            ids = body.get("repository_ids", [11, 22])
            return httpx.Response(
                201,
                json={
                    "token": "repos:" + ",".join(str(repo_id) for repo_id in ids),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        if request.url.path == "/installation/repositories":
            return httpx.Response(
                200,
                json={"repositories": installation_repositories},
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


@pytest.mark.parametrize("explicit_public", [False, True])
async def test_default_grants_unassigned_private_repos_and_explicit_public_repos(
    registry_db: None,
    github: list[dict[str, object]],
    explicit_public: bool,
) -> None:
    await WORKSPACES.put("internal", Workspace(slug="internal", repos=["ACME/Internal"]))
    if explicit_public:
        await WORKSPACES.put("default", Workspace(slug="default", repos=["acme/public"]))

    await lifecycle._create_sandbox_with_proxy(workspace_slug="default", thread_id="thread")

    assert injected_auth(github) == [
        "x-access-token:repos:11,33" if explicit_public else "x-access-token:repos:11"
    ]


@pytest.mark.parametrize(
    ("repositories", "expected"),
    [(["Acme/API"], "x-access-token:repos:11"), (["acme/public"], ""), ([], "")],
)
async def test_default_reviewer_scope_can_only_narrow_implicit_access(
    registry_db: None,
    github: list[dict[str, object]],
    repositories: list[str],
    expected: str,
) -> None:
    await lifecycle._create_sandbox_with_proxy(
        workspace_slug="default", github_proxy_repositories=repositories
    )

    assert injected_auth(github) == [expected]


async def test_default_refresh_revokes_repositories_claimed_by_another_workspace(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="default", thread_id="thread"
    )
    monkeypatch.setitem(proxy.SANDBOX_BACKENDS, "thread", backend)
    await WORKSPACES.put("internal", Workspace(slug="internal", repos=["acme/internal"]))
    assert await proxy.refresh_proxy_token("thread")

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:11"]


async def test_default_excludes_bindings_even_when_workspace_record_is_unreadable(
    registry_db: None,
    github: list[dict[str, object]],
) -> None:
    from sqlalchemy import update

    from agent.database import postgres
    from agent.workspaces.rows import WorkspaceRow

    await WORKSPACES.put("internal", Workspace(slug="internal", repos=["acme/internal"]))
    async with postgres.session() as session:
        await session.execute(
            update(WorkspaceRow).where(WorkspaceRow.slug == "internal").values(create_params=[])
        )

    await lifecycle._create_sandbox_with_proxy(workspace_slug="default")

    assert injected_auth(github) == ["x-access-token:repos:11"]


async def test_default_excludes_repositories_pending_workspace_import(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(WORKSPACES, "unimported_repos", frozenset({"acme/internal"}))

    await lifecycle._create_sandbox_with_proxy(workspace_slug="default")

    assert injected_auth(github) == ["x-access-token:repos:11"]


@pytest.mark.parametrize("populated", [False, True])
async def test_default_cannot_grant_access_before_workspace_import(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    populated: bool,
) -> None:
    if populated:
        await WORKSPACES.put("internal", Workspace(slug="internal", repos=["acme/internal"]))
    monkeypatch.setattr(WORKSPACES, "import_completed", False)

    with pytest.raises(RuntimeError, match="import"):
        await lifecycle._create_sandbox_with_proxy(workspace_slug="default")

    assert github == []


async def test_default_refresh_tracks_visibility_changes_and_new_private_repos(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    installation_repositories: list[dict[str, object]],
) -> None:
    backend = await lifecycle._create_sandbox_with_proxy(
        workspace_slug="default", thread_id="thread"
    )
    monkeypatch.setitem(proxy.SANDBOX_BACKENDS, "thread", backend)
    installation_repositories[0]["private"] = False
    installation_repositories.append({"id": 55, "full_name": "acme/new", "private": True})

    assert await proxy.refresh_proxy_token("thread")

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:22,55"]


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
