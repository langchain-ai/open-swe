"""Unit tests for the shared GitHub HTTP helper."""

import asyncio
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
    _compute_backoff,
    github_request,
)


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


def _make_response(status_code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status_code, headers=headers or {})


class TestComputeBackoff:
    def test_caps_retry_after_at_max(self) -> None:
        resp = _make_response(429, {"Retry-After": "120"})
        delay = _compute_backoff(resp, attempt=0)
        assert delay <= 60.0


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
@pytest.mark.parametrize("replacement", ["success", "failure", None])
async def test_check_runs_only_discards_superseded_cancellations(
    monkeypatch: pytest.MonkeyPatch, conclusion: str, replacement: str | None
) -> None:
    runs = [
        {
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
    pages = AsyncMock(side_effect=[runs, workflows])
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        result = await GitHubClient(http).repo("acme", "app").check_runs("a" * 40)
    assert result == (runs if conclusion == "failure" else [runs[1]])


@pytest.mark.parametrize(
    "distinct", ["workflow", "app", "event", "same_run", "older_success", "unavailable"]
)
async def test_check_runs_retains_cancellations_without_a_known_replacement(
    monkeypatch: pytest.MonkeyPatch, distinct: str
) -> None:
    runs = [
        {
            "name": "test",
            "status": "completed",
            "conclusion": result,
            "app": {"id": 15368, "slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        for suite, result in [(10, "cancelled"), (20, "success")]
    ]
    workflows = [
        {"id": suite, "check_suite_id": suite, "workflow_id": 100, "event": "pull_request"}
        for suite in (10, 20)
    ]
    if distinct == "workflow":
        workflows[1]["workflow_id"] = 200
    elif distinct == "app":
        runs[1]["app"] = {"id": 123, "slug": "other-app"}
    elif distinct == "event":
        workflows[1]["event"] = "push"
    elif distinct == "same_run":
        runs[1]["check_suite"] = {"id": 10}
    elif distinct == "older_success":
        workflows[1]["id"] = 5
    pages = AsyncMock(
        side_effect=[
            runs,
            ValueError("Actions unavailable") if distinct == "unavailable" else workflows,
        ]
    )
    monkeypatch.setattr(github_http.RepoClient, "pages", pages)
    async with httpx2.AsyncClient() as http:
        assert await GitHubClient(http).repo("acme", "app").check_runs("a" * 40) == runs


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
