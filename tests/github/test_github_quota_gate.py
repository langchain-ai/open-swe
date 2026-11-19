import logging
import time

import httpx2
import pytest

from openswe.github import http as github_http
from openswe.github.http import GitHubClient, GitHubError, github_client
from openswe.github.metering import MeteredTransport
from openswe.github.quota_gate import QuotaGate

_INSTALLATION = 70905168


class _Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_a_spent_installation_quota_is_held_until_its_reset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = _Clock()
    reset = int(clock.now) + 1800
    sent: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(f"{request.headers['Authorization']} {request.url.path}")
        if len(sent) == 1:
            return httpx2.Response(
                403,
                json={"message": "API rate limit exceeded for installation ID 70905168."},
                headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(reset),
                    "x-ratelimit-resource": "core",
                },
            )
        return httpx2.Response(200, json={})

    monkeypatch.setattr(
        github_http,
        "GITHUB_TRANSPORT",
        MeteredTransport(QuotaGate(httpx2.MockTransport(handle), clock=clock)),
    )
    caplog.set_level(logging.INFO, logger="openswe.github")

    async def get(path: str, token: str = "ghs_app") -> None:
        installation = _INSTALLATION if token.startswith("ghs_") else None
        async with github_client(token=token, installation_id=installation) as http:
            await GitHubClient(http).get(path)

    for _ in range(2):
        with pytest.raises(GitHubError) as refused:
            await get("repos/acme/app/pulls/1")
        assert refused.value.failure_reason == "primary_rate_limit"
        assert refused.value.response.headers["x-ratelimit-reset"] == str(reset)
    # Another quota of the installation, a person's quota and the quota sampler still go.
    await get("graphql")
    await get("repos/acme/app/pulls/1", token="gho_person")
    await get("rate_limit")
    assert sent == [
        "Bearer ghs_app /repos/acme/app/pulls/1",
        "Bearer ghs_app /graphql",
        "Bearer gho_person /repos/acme/app/pulls/1",
        "Bearer ghs_app /rate_limit",
    ]
    held = [r for r in caplog.records if r.getMessage() == "GitHub API request" and r.github_held]
    assert [(r.github_status, r.github_installation_id) for r in held] == [(403, _INSTALLATION)]

    clock.now = reset
    await get("repos/acme/app/pulls/1")
    assert sent[-1] == "Bearer ghs_app /repos/acme/app/pulls/1"
    tripped, cleared = (r for r in caplog.records if r.name == "openswe.github.quota_gate")
    assert (tripped.github_quota_resource, tripped.github_quota_reset) == ("core", reset)
    assert cleared.github_held_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", ["4000", "0"])
async def test_a_secondary_limit_holds_the_installation_for_its_retry_after(
    remaining: str,
) -> None:
    clock = _Clock()
    sent: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url.path)
        if len(sent) == 1:
            return httpx2.Response(
                403,
                json={"message": "You have exceeded a secondary rate limit."},
                headers={"Retry-After": "60", "x-ratelimit-remaining": remaining},
            )
        return httpx2.Response(200, json={})

    transport = QuotaGate(httpx2.MockTransport(handle), clock=clock)
    async with httpx2.AsyncClient(
        transport=transport,
        headers={"Authorization": "Bearer ghs_app"},
        event_hooks={"request": [lambda r: MeteredTransport.tag_installation(_INSTALLATION, r)]},
    ) as http:
        await http.post("https://api.github.com/repos/acme/app/issues/1/comments")
        held = await http.get("https://api.github.com/graphql")
        clock.now += 60
        await http.get("https://api.github.com/graphql")

    assert GitHubError(held).rate_limited
    assert held.headers["Retry-After"] == "60"
    assert sent == ["/repos/acme/app/issues/1/comments", "/graphql"]
