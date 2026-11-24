"""Exercise repository credential boundaries through the sandbox proxy."""

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from deepagents.backends.protocol import ExecuteResponse

from openswe.agent_api.hosted import run_kinds
from openswe.agent_api.run_sandbox import RunSandbox
from openswe.agent_api.runs import run_config
from openswe.agent_api.tokens import RemoteRun
from openswe.dashboard.execution_defaults import ExecutionDefaults
from openswe.github import app, sandbox_access
from openswe.github.metering import MeteredTransport
from openswe.github.repositories import Repository
from openswe.sandboxes import lifecycle
from openswe.sandboxes.image_build import SnapshotBuild
from openswe.sandboxes.providers.langsmith import LangSmithProvider
from openswe.sandboxes.proxy_callback import CallingSandbox
from tests.support.github_sdk import mock_github_sdk


class ListedRepository(TypedDict):
    id: int
    full_name: str


@pytest.fixture
def github_requests() -> list[httpx2.Request]:
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
    github_requests: list[httpx2.Request],
    github_repositories: list[ListedRepository],
) -> Iterator[list[dict[str, object]]]:
    """GitHub issues synthetic tokens encoding their repository permissions."""
    payloads: list[dict[str, object]] = []
    client = httpx2.AsyncClient

    def handle(request: httpx2.Request) -> httpx2.Response:
        github_requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            body = json.loads(request.content or b"{}")
            requested = body.get("repository_ids")
            # GitHub refuses the whole token once any id is outside the installation.
            if requested is not None and not set(requested) <= {
                repo["id"] for repo in github_repositories
            }:
                return httpx2.Response(422, json={"message": "Validation Failed"})
            ids = requested if requested is not None else [11, 22]
            token = "repos:" + ",".join(str(repo_id) for repo_id in ids)
            if permissions := body.get("permissions"):
                token += ";" + ",".join(f"{k}={v}" for k, v in sorted(permissions.items()))
            return httpx2.Response(
                201,
                json={
                    "token": token,
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        if request.url.path == "/installation/repositories":
            return httpx2.Response(200, json={"repositories": github_repositories})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def handle_proxy(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json={"proxy_config": {}})
        if request.method == "PATCH":
            payloads.append(json.loads(request.content))
            return httpx2.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def make_client(**kwargs: object) -> httpx2.AsyncClient:
        transport = kwargs.get("transport")
        return client(
            transport=transport
            # The GitHub SDK's mock, under its metering.
            if isinstance(transport, (httpx2.MockTransport, MeteredTransport))
            else httpx2.MockTransport(handle_proxy)
        )

    monkeypatch.setattr(httpx2, "AsyncClient", make_client)
    mock_github_sdk(monkeypatch, handle)
    monkeypatch.setattr(app, "GITHUB_APP_ID", "1")
    monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setattr(app, "GITHUB_APP_PRIVATE_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(
        lifecycle,
        "connect_existing_sandbox",
        AsyncMock(
            return_value=MagicMock(
                id="sandbox",
                aexecute=AsyncMock(return_value=ExecuteResponse(output="", exit_code=0)),
            )
        ),
    )
    monkeypatch.setattr("openswe.execution_images.EXECUTION_IMAGES.maybe_start_update", AsyncMock())
    # The thread's sandbox is not bound yet, so its config comes from the pinned global
    # Defaults; the thread itself grants the whole installation.
    monkeypatch.setattr(
        lifecycle, "get_sandbox_metadata", AsyncMock(return_value={"sandbox_owner": "mda"})
    )
    monkeypatch.setattr(lifecycle, "thread_token_repositories", AsyncMock(return_value=None))
    app.clear_app_token_cache()
    yield payloads
    app.clear_app_token_cache()


async def _connect(payloads: list[dict[str, object]]) -> None:
    """Answer the thread's sandbox's proxy callback for a request to GitHub, recorded as
    LangSmith's proxy gets it."""
    metadata = await lifecycle.get_sandbox_metadata("thread")
    sandbox = CallingSandbox(name="sandbox", thread_id="thread", metadata=metadata)
    payloads.append((await sandbox.headers_for("github.com")).model_dump(mode="json"))


def injected_auth(payloads: list[dict[str, object]]) -> list[str]:
    # Decode only synthetic test credentials sent to the fake sandbox service.
    values = []
    for payload in payloads:
        if isinstance(headers := payload.get("headers"), dict):
            value = str(headers.get("Authorization", ""))
            values.append(base64.b64decode(value.removeprefix("Basic ")).decode() if value else "")
            continue
        config = payload["proxy_config"]
        assert isinstance(config, dict)
        rule = next((rule for rule in config["rules"] if rule["name"] == "github"), None)
        value = rule["headers"][0]["value"] if rule and rule["headers"] else ""
        values.append(base64.b64decode(value.removeprefix("Basic ")).decode() if value else "")
    return values


async def test_every_sandbox_receives_the_installation_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    """Preload preferences do not limit the installation's repository access."""

    await _connect(github)

    assert injected_auth(github) == ["x-access-token:repos:11,22"]


async def test_a_thread_scoped_to_its_repository_keeps_that_scope_across_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    """A thread an event on a public repository started never gets the installation token."""
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )

    await _connect(github)
    await _connect(github)

    assert injected_auth(github) == ["x-access-token:repos:11", "x-access-token:repos:11"]


@pytest.mark.parametrize("kind", ["reviewer", "reviewer-shadow", "review-scout"])
async def test_a_reviewers_sandbox_can_read_but_never_write_github(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    kind: str,
) -> None:
    """A review's sandbox reaches only the pull request its thread records, read-only."""
    read_only = (
        "actions=read,checks=read,contents=read,issues=read,"
        "metadata=read,pull_requests=read,statuses=read"
    )
    metadata: dict[str, object] = {
        "kind": kind,
        "pr": {"owner": "acme", "name": "api"},
        "sandbox_owner": "mda",
    }
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value=metadata))

    await _connect(github)

    assert injected_auth(github) == [f"x-access-token:repos:11;{read_only}"]


@pytest.mark.parametrize("kind", ["reviewer", "review-scout"])
async def test_a_reconnecting_reviewer_without_a_known_pr_gets_no_repository_at_all(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    kind: str,
) -> None:
    """A review with no recorded PR gets no repository."""
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value={"kind": kind}))

    await _connect(github)

    assert injected_auth(github) == [""]


async def test_a_sandbox_without_a_github_scope_gets_no_token_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread whose GitHub scope names no repository gets no token from the callback, so its
    sandbox's ``GH_TOKEN`` placeholder is empty, which ``gh`` reads as none, instead of one it
    would send and GitHub refuse."""
    metadata = AsyncMock(return_value={"sandbox_owner": "mda"})
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", metadata)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", metadata)
    monkeypatch.setattr(lifecycle, "thread_token_repositories", AsyncMock(return_value=[]))
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://robin.invalid")
    run = RemoteRun(thread_id="thread", assistant_id="agent", configurable={})

    spec = await RunSandbox(run_config(run), reviews=run_kinds()["agent"].reviews).spec()

    assert spec.create.env_vars["GH_TOKEN"] == ""
    assert spec.create.env_vars["OPEN_SWE_TOOLS_URL"].startswith("https://robin.invalid/")
    access = await lifecycle.SandboxCredentialPolicy.proxy_token("thread", {"sandbox_owner": "mda"})
    assert not access.token


@pytest.mark.parametrize("kind", ["reviewer", "review-scout", "agent"])
async def test_the_proxy_config_mda_creates_the_sandbox_with_carries_no_credential(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    kind: str,
) -> None:
    """The backend names the sandbox's proxy config as it plans the run: it carries the proxy
    callback, never a token, and the backend writes none of it."""
    metadata = AsyncMock(return_value={"sandbox_owner": "mda"})
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", metadata)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", metadata)
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://robin.invalid")
    parts = run_kinds()[kind]
    run = RemoteRun(
        thread_id="thread",
        assistant_id=kind,
        configurable={"repo": {"owner": "acme", "name": "api"}},
    )

    spec = await RunSandbox(run_config(run), reviews=parts.reviews).spec()

    # MDA creates the sandbox with its commands' environment in its own run config.
    assert spec.create.env_vars["GH_TOKEN"] == "proxy-injected"
    assert spec.proxy_config is not None
    assert "repos:" not in json.dumps(spec.proxy_config)
    callbacks = spec.proxy_config["callbacks"]
    assert isinstance(callbacks, list) and isinstance(callbacks[0], dict)
    assert callbacks[0]["url"] == "https://robin.invalid/dashboard/api/sandbox-proxy-callback"
    assert github == []


@pytest.mark.parametrize("answer", ["unavailable", "not-found"])
async def test_a_run_ends_when_its_sandboxs_cloud_identity_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """LangSmith refuses a proxy config without the exact cloud identity rules an existing
    sandbox was created with, so a run whose sandbox's rules can't be read is not planned at
    all, and the person is told why: a sandbox LangSmith can't find is gone, and only a new
    thread goes on from it."""
    from openswe.agent_api import run_sandbox
    from openswe.sandboxes.providers.langsmith import LiveSandboxProxy
    from openswe.sandboxes.state import SandboxDeletedError, SandboxUnreachableError

    metadata = AsyncMock(return_value={"sandbox_id": "box-1", "sandbox_owner": "mda"})
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", metadata)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", metadata)
    monkeypatch.setenv("LANGSMITH_API_KEY", "key")
    request = httpx2.Request("GET", "https://sandbox.invalid/boxes/box-1")
    failure = (
        httpx2.ConnectError("unavailable")
        if answer == "unavailable"
        else httpx2.HTTPStatusError(
            "404", request=request, response=httpx2.Response(404, request=request)
        )
    )
    monkeypatch.setattr(LiveSandboxProxy, "live", AsyncMock(side_effect=failure))
    told = AsyncMock()
    monkeypatch.setattr(run_sandbox, "post_sandbox_unreachable_notification", told)
    run = RemoteRun(thread_id="thread", assistant_id="agent", configurable={})

    with pytest.raises(SandboxUnreachableError) as ended:
        await RunSandbox(run_config(run), reviews=run_kinds()["agent"].reviews).spec()

    deleted = answer == "not-found"
    assert isinstance(ended.value, SandboxDeletedError) is deleted
    assert told.await_args is not None
    assert told.await_args.kwargs["sandbox_id"] == "box-1"
    assert told.await_args.kwargs["deleted"] is deleted
    if not deleted:
        assert "cloud identity" in told.await_args.kwargs["reason"]


async def test_an_external_sandbox_gets_no_proxy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An eval harness's sandbox keeps its own proxy config, so its capability goes to no run."""
    metadata = AsyncMock(
        return_value={
            "sandbox_id": "tb-task-1",
            "sandbox_owner": "external",
            "owner_type": "system",
        }
    )
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", metadata)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", metadata)
    parts = run_kinds()["agent"]
    run = RemoteRun(thread_id="thread", assistant_id="agent", configurable={})

    spec = await RunSandbox(run_config(run), reviews=parts.reviews).spec()

    assert spec.proxy_config is None


async def test_restricted_app_token_never_falls_back_to_a_developer_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app, "GITHUB_APP_ID", None)
    monkeypatch.setattr(app, "_local_dev_token", AsyncMock(return_value="developer-write-token"))

    assert await app.get_github_app_installation_token_with_expiry() == (
        "developer-write-token",
        None,
    )
    assert await app.get_github_app_installation_token_with_expiry(
        permissions=sandbox_access.READ_ONLY_REVIEW_PERMISSIONS
    ) == (None, None)


async def test_an_unreadable_thread_scope_fails_the_sandbox_rather_than_widening(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(side_effect=RuntimeError("down"))
    )

    with pytest.raises(RuntimeError):
        await _connect(github)

    assert github == []


@pytest.mark.parametrize("repos", [["acme/api"], []])
async def test_image_builder_gets_the_installation_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    repos: list[str],
) -> None:
    monkeypatch.setattr(
        LangSmithProvider,
        "get_or_create",
        AsyncMock(return_value=MagicMock(id="sandbox", aexecute=AsyncMock())),
    )

    await SnapshotBuild.create_builder_sandbox(ExecutionDefaults(repos=repos), None)

    assert injected_auth(github) == ["x-access-token:repos:11,22"]


@pytest.mark.parametrize("historical_defaults", [False, True])
async def test_pinned_preload_preferences_never_expand_repository_scope(
    monkeypatch: pytest.MonkeyPatch, github: list[dict[str, object]], historical_defaults: bool
) -> None:
    from openswe.execution import ExecutionProfile
    from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions

    profile = ExecutionProfile(
        uses_defaults=historical_defaults,
        create=LangSmithCreateOptions.resolve(snapshot_id=None, create_params={}),
        repos=["acme/internal"],
    )
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )
    with ExecutionProfile.bind(profile):
        await _connect(github)
    assert injected_auth(github) == ["x-access-token:repos:11"]


async def test_scoped_token_failure_does_not_inject_discovery_token(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
) -> None:
    monkeypatch.setattr(
        sandbox_access,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(side_effect=[("discovery-token", None), (None, None)]),
    )
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )
    with pytest.raises(RuntimeError, match="repository token is unavailable"):
        await _connect(github)
    assert github == []


async def test_repository_access_uses_later_installation_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            requests.append(body)
            return httpx2.Response(
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
        return httpx2.Response(200, json={"repositories": repos}, headers=headers)

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


def listing_calls(requests: list[httpx2.Request]) -> int:
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
@pytest.mark.parametrize("known", [True, False])
async def test_first_use_lists_the_installation_once_and_stores_the_id(
    github_requests: list[httpx2.Request], known: bool
) -> None:
    """A repository Robin has no row for yet gets one, or every token would list again."""
    if known:
        await stored_repository("acme/api")

    first = await sandbox_access.repository_token(["acme/api"])
    app.clear_app_token_cache()
    second = await sandbox_access.repository_token(["acme/api"])

    assert first.token == second.token == "repos:11"
    assert listing_calls(github_requests) == 1
    assert await stored_github_id("acme/api") == 11


@pytest.mark.usefixtures("registry_db", "github")
async def test_repository_outside_the_installation_is_looked_for_again_after_the_interval(
    github_requests: list[httpx2.Request],
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
    github_requests: list[httpx2.Request],
) -> None:
    await stored_repository("acme/api", github_id=99)

    access = await sandbox_access.repository_token(["acme/api"])

    assert access.token == "repos:11"
    assert listing_calls(github_requests) == 1
    assert await stored_github_id("acme/api") == 11


@pytest.mark.usefixtures("registry_db", "github")
async def test_a_name_follows_the_repository_listed_under_it_after_a_rename(
    github_repositories: list[ListedRepository],
) -> None:
    """A private repository renamed away from a name never reaches whoever creates a new one
    under it, even while the old id is stored for the name."""
    await stored_repository("acme/api", github_id=11)
    github_repositories[:] = [
        {"id": 11, "full_name": "acme/renamed"},
        {"id": 33, "full_name": "acme/api"},
    ]
    # Until its id is due a recheck, the stored one is used without listing.
    assert (await sandbox_access.repository_token(["acme/api"])).token == "repos:11"
    old = datetime.now(UTC) - sandbox_access.RECHECK_FOUND_AFTER - timedelta(seconds=1)
    await Repository.record_github_ids({"acme/api": 11}, checked_at=old)
    app.clear_app_token_cache()

    access = await sandbox_access.repository_token(["acme/api"])

    assert access.token == "repos:33"
    assert await stored_github_id("acme/api") == 33


@pytest.mark.usefixtures("registry_db", "github")
async def test_refused_ids_fail_closed_after_one_listing(
    monkeypatch: pytest.MonkeyPatch,
    github_requests: list[httpx2.Request],
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
    github_requests: list[httpx2.Request],
) -> None:
    await stored_repository("acme/api", github_id=11)
    monkeypatch.setattr(
        Repository, "by_keys", AsyncMock(side_effect=OSError("database unavailable"))
    )

    assert (await sandbox_access.repository_token(["acme/api"])).token == "repos:11"
    assert listing_calls(github_requests) == 1


@pytest.mark.usefixtures("registry_db")
async def test_a_run_restricted_to_one_repository_mints_from_stored_ids(
    monkeypatch: pytest.MonkeyPatch,
    github: list[dict[str, object]],
    github_requests: list[httpx2.Request],
) -> None:
    await stored_repository("acme/api")
    await stored_repository("acme/internal")
    await _connect(github)
    app.clear_app_token_cache()
    monkeypatch.setattr(
        lifecycle, "thread_token_repositories", AsyncMock(return_value=["acme/api"])
    )

    await _connect(github)

    assert injected_auth(github) == ["x-access-token:repos:11,22", "x-access-token:repos:11"]
    assert listing_calls(github_requests) == 1
