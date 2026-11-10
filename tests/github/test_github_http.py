"""Unit tests for the shared GitHub HTTP helper."""

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import httpx2
import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.dashboard import profiles
from openswe.github import http as github_http
from openswe.github.http import (
    GitHubClient,
    GitHubSignInRequired,
    _Retry,
    github_request,
)
from openswe.github.revalidation import RevalidatingTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("listing", ["runs", "jobs", "artifacts"])
async def test_validation_listings_reject_incomplete_responses(listing: str) -> None:
    async def handle(request: httpx2.Request) -> httpx2.Response:
        key = "workflow_runs" if listing == "runs" else listing
        return httpx2.Response(200, json={"total_count": 1, key: []})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        with pytest.raises(ValueError):
            if listing == "runs":
                await repo.validation_runs("merge_queue_validate.yml", datetime.now(UTC))
            elif listing == "jobs":
                await repo.validation_jobs(42)
            else:
                await repo.validation_artifacts(42)


@pytest.mark.parametrize("listing", ["jobs", "artifacts"])
@pytest.mark.parametrize("fault", [None, "duplicate", "changed_total"])
async def test_validation_lists_all_pages_without_accepting_partial_evidence(listing, fault):
    async def handle(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        ids = range(1, 101) if page == 1 else [100 if fault == "duplicate" else 101]
        items = [
            {
                "id": i,
                "name": f"item-{i}",
                "expired": False,
                "run_id": 42,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
            }
            for i in ids
        ]
        return httpx2.Response(
            200,
            json={
                "total_count": 102 if page == 2 and fault == "changed_total" else 101,
                listing: items,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        fetch = repo.validation_jobs if listing == "jobs" else repo.validation_artifacts
        if fault:
            with pytest.raises(ValueError):
                await fetch(42)
        else:
            assert [item.id for item in await fetch(42)] == list(range(1, 102))


@pytest.mark.parametrize("fault", [None, "changed", "missing", "duplicate", "truncated", "symlink"])
async def test_service_source_fingerprint_matches_policy_format(fault):
    entries = [
        {"path": path, "mode": mode, "type": kind, "sha": sha * 40}
        for path, mode, kind, sha in [
            ("langdev", "100755", "blob", "c"),
            ("cmd", "040000", "tree", "b"),
            (".github", "040000", "tree", "a"),
            ("smith-backend", "040000", "tree", "d"),
        ]
    ]
    payload = {"tree": entries, "truncated": fault == "truncated"}
    approved = "9546f5ca226a05e2efc02fd3be6d2af71e638fad5092d15df3283382b3986515"
    if fault == "changed":
        entries[2]["sha"] = "e" * 40
    elif fault == "missing":
        entries.pop(1)
    elif fault == "duplicate":
        entries.append(entries[0])
    elif fault == "symlink":
        entries[0]["mode"] = "120000"

    async def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == f"/repos/acme/widgets/git/trees/{'f' * 40}"
        assert not request.url.params
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        if fault in {"missing", "duplicate", "truncated", "symlink"}:
            with pytest.raises(ValueError):
                await repo.validation_source_digest("f" * 40)
        else:
            actual = await repo.validation_source_digest("f" * 40)
            assert (actual == approved) is (fault is None)


async def test_pages_read_the_rest_of_a_counted_list_concurrently() -> None:
    later_pages = asyncio.Barrier(2)
    requested: list[int] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        requested.append(page)
        if page > 1:
            await asyncio.wait_for(later_pages.wait(), timeout=1)
        size = 50 if page == 3 else 100
        runs = [{"id": page * 1000 + i} for i in range(size)]
        return httpx2.Response(200, json={"total_count": 250, "check_runs": runs})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        runs = await GitHubClient(http).pages("check-runs", key="check_runs")

    assert len(runs) == 250
    assert sorted(requested) == [1, 2, 3]


def _make_response(status_code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status_code, headers=headers or {})


class TestComputeBackoff:
    def test_caps_retry_after_at_max(self) -> None:
        resp = _make_response(429, {"Retry-After": "120"})
        delay = _Retry.after_response(resp, "GET", attempt=0)
        assert delay is not None and delay <= 60.0


@pytest.mark.asyncio
async def test_github_request_does_not_retry_a_spent_hourly_quota() -> None:
    spent = httpx2.Response(
        403,
        text="API rate limit exceeded for installation ID 1.",
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1800)},
    )
    client = AsyncMock()
    client.request = AsyncMock(return_value=spent)

    response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 403
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_repeated_reads_revalidate_and_never_cross_tokens() -> None:
    sent: list[tuple[str, str | None]] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        token = request.headers["Authorization"]
        sent.append((token, request.headers.get("If-None-Match")))
        if request.headers.get("If-None-Match") == f'"{token}"':
            return httpx2.Response(304, headers={"ETag": f'"{token}"'})
        return httpx2.Response(200, json={"for": token}, headers={"ETag": f'"{token}"'})

    transport = RevalidatingTransport(httpx2.MockTransport(handle))
    url = "https://api.github.com/repos/acme/app/pulls/1"
    answers = []
    for token in ("Bearer a", "Bearer a", "Bearer b"):
        async with httpx2.AsyncClient(
            transport=transport, headers={"Authorization": token}
        ) as http:
            response = await http.get(url)
            answers.append((response.status_code, response.json()))

    assert answers == [(200, {"for": "Bearer a"})] * 2 + [(200, {"for": "Bearer b"})]
    assert sent == [("Bearer a", None), ("Bearer a", '"Bearer a"'), ("Bearer b", None)]


@pytest.mark.asyncio
async def test_github_request_retries_on_secondary_rate_limit() -> None:
    responses = [
        httpx2.Response(403, text="secondary rate limit", headers={}),
        _make_response(200),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_transport_error_on_post() -> None:
    """Transport errors on POST must not retry — the server may have already
    processed the write, and retrying would duplicate the resource."""
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.TimeoutException("timeout"))

    with pytest.raises(httpx2.TimeoutException):
        await github_request(client, "POST", "https://api.github.com/test")

    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_on_429_even_for_post() -> None:
    """429 is safe to retry for any method — the server explicitly did not
    process the request."""
    responses = [
        _make_response(429, {"Retry-After": "0"}),
        _make_response(201),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_retries_on_503_even_for_post() -> None:
    responses = [
        _make_response(503),
        _make_response(201),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 201
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_does_not_retry_502_on_post() -> None:
    """502 is ambiguous — the upstream may have processed the write before the
    gateway returned an error.  Must not retry for non-idempotent methods."""
    response_502 = _make_response(502)
    client = AsyncMock()
    client.request = AsyncMock(return_value=response_502)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 502
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_does_not_retry_504_on_post() -> None:
    """504 is ambiguous — the upstream may have processed the write before the
    gateway timed out.  Must not retry for non-idempotent methods."""
    response_504 = _make_response(504)
    client = AsyncMock()
    client.request = AsyncMock(return_value=response_504)

    response = await github_request(client, "POST", "https://api.github.com/test")

    assert response.status_code == 504
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_github_request_retries_502_on_get() -> None:
    """502 is safe to retry for idempotent methods."""
    responses = [
        _make_response(502),
        _make_response(200),
    ]
    client = AsyncMock()
    client.request = AsyncMock(side_effect=responses)

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        response = await github_request(client, "GET", "https://api.github.com/test")

    assert response.status_code == 200
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_raises_after_exhausting_transport_retries() -> None:
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.ConnectTimeout("timeout"))

    with patch("openswe.github.http.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx2.ConnectTimeout):
            await github_request(client, "GET", "https://api.github.com/test", max_retries=1)

    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_github_request_propagates_non_retryable_http_error() -> None:
    client = AsyncMock()
    client.request = AsyncMock(side_effect=httpx2.HTTPError("boom"))

    with pytest.raises(httpx2.HTTPError):
        await github_request(client, "GET", "https://api.github.com/test")

    assert client.request.await_count == 1


async def test_as_app_looks_up_a_reinstalled_repository_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.github import app as github_app

    lookup = AsyncMock(side_effect=[123, 456])
    forget = AsyncMock()

    async def mint(*, installation_id: int, **kwargs: object) -> str:
        if installation_id == 123:
            raise github_http.GitHubAppUnavailable("installation removed", gone=True)
        return f"token-{installation_id}"

    monkeypatch.setattr(github_app, "get_github_app_installation_id_for_repo", lookup)
    monkeypatch.setattr(github_app, "get_github_app_installation_token", mint)
    monkeypatch.setattr(github_app.INSTALLATIONS, "forget", forget)

    async with GitHubClient.as_app("acme", "app") as github:
        assert github.http.headers["authorization"].endswith("token-456")
    forget.assert_awaited_once_with("acme", "app")


async def test_as_user_refreshes_a_rejected_token_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = AsyncMock(side_effect=["stale", "fresh"])
    monkeypatch.setattr(profiles, "get_valid_access_token", tokens)
    sent: list[str] = []

    async def request(client: httpx2.AsyncClient, method: str, url: str, **_kwargs: object):
        sent.append(client.headers["Authorization"])
        status = 401 if len(sent) == 1 else 200
        return httpx2.Response(status, json={}, request=httpx2.Request(method, url))

    monkeypatch.setattr(github_http, "github_request", request)
    async with GitHubClient.as_user("octocat") as github:
        assert await github.get("user") == {}
    assert sent == ["Bearer stale", "Bearer fresh"]
    tokens.assert_awaited_with("octocat", force_refresh=True)


async def test_as_user_without_a_usable_token_asks_for_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    with pytest.raises(GitHubSignInRequired):
        async with GitHubClient.as_user("octocat"):
            pass


async def test_as_user_asks_for_sign_in_when_github_rejects_the_refreshed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = AsyncMock(side_effect=["stale", "still-rejected"])
    monkeypatch.setattr(profiles, "get_valid_access_token", tokens)

    async def request(_client: httpx2.AsyncClient, method: str, url: str, **_kwargs: object):
        return httpx2.Response(
            401, json={"message": "Bad credentials"}, request=httpx2.Request(method, url)
        )

    monkeypatch.setattr(github_http, "github_request", request)
    async with GitHubClient.as_user("octocat") as github:
        with pytest.raises(GitHubSignInRequired):
            await asyncio.gather(github.get("user"), github.get("user/repos"))
    assert tokens.await_count == 2


@pytest.mark.parametrize("conclusion", ["cancelled", "stale", "failure"])
@pytest.mark.parametrize("replacement", ["success", "failure", "skipped", None])
async def test_check_runs_discards_superseded_results(
    monkeypatch: pytest.MonkeyPatch, conclusion: str, replacement: str | None
) -> None:
    runs = [
        {
            "id": suite * 10,
            "name": "validate README",
            "status": "completed" if result else "in_progress",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, conclusion), (20, replacement)]
    ]
    workflows = [
        {"id": suite, "check_suite_id": suite, "workflow_id": 100, "event": "pull_request"}
        for suite in (10, 20)
    ]
    pages = AsyncMock(side_effect=[runs, workflows, runs])
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        repo = GitHubClient(http).repo("acme", "app")
        result = await repo.check_runs("a" * 40)
        assert await repo.check_runs("a" * 40, include_superseded=True) == runs
    assert result == [runs[1]]


@pytest.mark.parametrize("conclusion", ["cancelled", "failure"])
@pytest.mark.parametrize(
    "distinct",
    ["workflow", "app", "event", "unknown_order", "older_success", "unavailable"],
)
async def test_check_runs_retains_results_without_a_known_replacement(
    monkeypatch: pytest.MonkeyPatch, conclusion: str, distinct: str
) -> None:
    runs = [
        {
            "id": suite * 10,
            "name": "test",
            "status": "completed",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, conclusion), (20, "success")]
    ]
    workflows = [
        {"id": suite, "check_suite_id": suite, "workflow_id": 100, "event": "pull_request"}
        for suite in (10, 20)
    ]
    if distinct == "workflow":
        workflows[1]["workflow_id"] = 200
    elif distinct == "app":
        runs[1]["app"] = {"id": 123, "slug": "github-actions"}
    elif distinct == "event":
        workflows[1]["event"] = "push"
    elif distinct == "unknown_order":
        runs[1].pop("id")
    elif distinct == "older_success":
        runs[1]["id"] = 50
    pages = AsyncMock(
        side_effect=[
            runs,
            ValueError("Actions unavailable") if distinct == "unavailable" else workflows,
        ]
    )
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        result = await GitHubClient(http).repo("acme", "app").check_runs("a" * 40)
    assert result == ([runs[0]] if distinct == "older_success" else runs)


@pytest.mark.parametrize("outcome", ["success", "proxy_failure", "cancelled"])
async def test_candidate_sandbox_keeps_credentials_in_proxy_and_stops_on_exit(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from openswe.sandboxes.providers import langsmith

    sandbox = Mock(spec=SandboxBackendProtocol, id="candidate-sandbox")
    sandbox.aexecute = AsyncMock(return_value=ExecuteResponse(output="", exit_code=0))
    configure = AsyncMock(
        side_effect=RuntimeError("Proxy unavailable") if outcome == "proxy_failure" else None
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    monkeypatch.setattr(langsmith, "create_langsmith_sandbox", AsyncMock(return_value=sandbox))
    monkeypatch.setattr(langsmith, "configure_sandbox_proxy", configure)
    monkeypatch.setattr(langsmith, "get_async_sandbox_client", Mock(return_value=client))

    async def use_candidate() -> None:
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer test-controller-token"}
        ) as http:
            async with GitHubClient(http).repo("acme", "widgets").candidate_git() as git:
                assert git.sandbox is sandbox
                if outcome == "cancelled":
                    raise asyncio.CancelledError

    if outcome == "success":
        await use_candidate()
    else:
        with pytest.raises(RuntimeError if outcome == "proxy_failure" else asyncio.CancelledError):
            await use_candidate()
    configure.assert_awaited_once_with("candidate-sandbox", "test-controller-token")
    client.stop_sandbox.assert_awaited_once_with("candidate-sandbox")
    assert all(
        "test-controller-token" not in call.args[0] for call in sandbox.aexecute.await_args_list
    )


@pytest.mark.parametrize(
    "result", ["present", "missing", "prefix_only", "unavailable", "malformed"]
)
async def test_ref_lookup_distinguishes_absence_from_api_failure(
    monkeypatch: pytest.MonkeyPatch, result: str
) -> None:
    ref = "refs/merge-queue/candidates/example"
    payload: object = []
    if result in {"present", "prefix_only"}:
        name = ref if result == "present" else f"{ref}-other"
        payload = [{"ref": name, "object": {"sha": "a" * 40}}]
    elif result == "malformed":
        payload = [{"ref": ref}]
    request = AsyncMock(
        return_value=httpx2.Response(
            404 if result == "unavailable" else 200,
            json=payload,
            request=httpx2.Request(
                "GET", "https://api.github.com/repos/acme/widgets/git/matching-refs"
            ),
        )
    )
    monkeypatch.setattr(github_http, "github_request", request)
    async with httpx2.AsyncClient() as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        if result in {"unavailable", "malformed"}:
            with pytest.raises(github_http.GitHubError if result == "unavailable" else ValueError):
                await repo.ref_sha(ref)
        else:
            assert await repo.ref_sha(ref) == ("a" * 40 if result == "present" else None)
    assert request.call_args.args[2] == (
        "https://api.github.com/repos/acme/widgets/git/matching-refs/"
        "merge-queue%2Fcandidates%2Fexample"
    )


@pytest.mark.parametrize(
    "path",
    [
        "../../../../repos/acme/private/contents/x.png",
        "docs/./x.png",
        "docs//x.png",
        "..\\x.png",
        "%2e%2e/%2E%2e/repos/acme/private/contents/x.png",
    ],
)
async def test_a_file_path_never_leaves_its_repository(path: str) -> None:
    # httpx resolves dot segments, so these would read another repository with the same token.
    sent: list[str] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(200, content=b"png")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        repo = GitHubClient(http).repo("me", "shared")
        with pytest.raises(github_http.UnsafeRepoPath):
            await repo.raw_file(path, "main")
        with pytest.raises(github_http.UnsafeRepoPath):
            await repo.delete_branch(path)
        assert await repo.raw_file("docs/a b.png", "main") == b"png"
        with pytest.raises(github_http.UnsafeRepoPath):
            GitHubClient(http).repo("..", "users")
    assert sent == ["https://api.github.com/repos/me/shared/contents/docs/a%20b.png?ref=main"]
