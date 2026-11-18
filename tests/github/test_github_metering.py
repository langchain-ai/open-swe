import logging
from collections.abc import Callable, Coroutine
from typing import Any, cast

import httpx2
import pytest

from openswe.github.metering import MeteredTransport
from openswe.github.revalidation import RevalidatingTransport

_CLIENT = """
import asyncio

async def read():
    return Metered.caller()

async def load():
    with Metered.attributed():
        return await asyncio.gather(read(), read())
"""


def test_route_folds_ids_and_shas() -> None:
    a, b = "a" * 40, "b" * 40
    routes = {
        f"/repos/acme/app/commits/{a}/check-runs?page=2": "/repos/acme/app/commits/:sha/check-runs",
        f"/repos/acme/app/compare/{a}...{b}": "/repos/acme/app/compare/:sha...:sha",
        "/repos/acme/app/pulls/42/reviews": "/repos/acme/app/pulls/:n/reviews",
    }
    for path, expected in routes.items():
        assert MeteredTransport.route(httpx2.URL(f"https://api.github.com{path}")) == expected


@pytest.mark.asyncio
async def test_caller_names_robin_code_past_the_client_and_its_gathered_tasks() -> None:
    client: dict[str, Any] = {
        "__name__": "openswe.github.pull_request_status",
        "Metered": MeteredTransport,
    }
    exec(_CLIENT, client)
    poller: dict[str, Any] = {"__name__": "openswe.fake.poller", "load": client["load"]}
    exec("async def poll():\n    return await load()", poller)
    poll = cast(Callable[[], Coroutine[object, object, list[str]]], poller["poll"])

    assert await poll() == ["openswe.fake.poller.poll"] * 2


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "GitHub API request"]


@pytest.mark.asyncio
async def test_logs_each_github_request_with_its_cost(caplog: pytest.LogCaptureFixture) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("If-None-Match"):
            return httpx2.Response(304, headers={"ETag": '"v1"', "x-ratelimit-used": "7"})
        return httpx2.Response(
            200,
            json={"number": 1},
            headers={
                "ETag": '"v1"',
                "x-ratelimit-resource": "core",
                "x-ratelimit-used": "7",
                "x-ratelimit-remaining": "14993",
                "x-ratelimit-limit": "15000",
                "x-ratelimit-reset": "1800000000",
                "retry-after": "60",
                "x-github-request-id": "request-id",
                "x-accepted-github-permissions": "members=read",
                "set-cookie": "private-cookie",
            },
        )

    transport = MeteredTransport(RevalidatingTransport(httpx2.MockTransport(handle)))
    caplog.set_level(logging.INFO, logger="openswe.github.metering")
    async with httpx2.AsyncClient(
        transport=transport, headers={"Authorization": "Bearer ghs_secret"}
    ) as http:
        await http.get("https://api.github.com/repos/acme/app/pulls/1")
        await http.get("https://api.github.com/repos/acme/app/pulls/1")

    first, second = _records(caplog)
    assert first.github_route == "/repos/acme/app/pulls/:n"
    assert first.github_method == "GET"
    assert first.github_status == 200
    assert first.github_revalidated is False
    assert first.github_token_kind == "installation"
    assert first.github_quota_resource == "core"
    assert first.github_quota_used == 7
    assert first.github_quota_remaining == 14993
    assert first.github_quota_limit == 15000
    assert first.github_quota_reset == 1800000000
    assert first.github_retry_after == 60
    assert first.github_request_id == "request-id"
    assert first.github_accepted_permissions == "members=read"
    assert "private-cookie" not in str(first.__dict__)
    assert "ghs_secret" not in str(first.__dict__)
    assert second.github_revalidated is True
    assert "ghs_secret" not in caplog.text


@pytest.mark.asyncio
async def test_other_hosts_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    transport = MeteredTransport(httpx2.MockTransport(lambda _request: httpx2.Response(200)))
    caplog.set_level(logging.INFO, logger="openswe.github.metering")
    async with httpx2.AsyncClient(transport=transport) as http:
        await http.get("https://github.com/acme/app.git/info/refs")

    assert _records(caplog) == []


@pytest.mark.asyncio
async def test_a_logging_failure_still_returns_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(MeteredTransport, "_log", staticmethod(explode))
    transport = MeteredTransport(httpx2.MockTransport(lambda _request: httpx2.Response(204)))
    async with httpx2.AsyncClient(transport=transport) as http:
        response = await http.get("https://api.github.com/rate_limit")

    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "kind", "installation"),
    [("ghs_app", "installation", 456), ("gho_local_gh", "user", None)],
)
async def test_as_app_names_the_installation_only_for_an_installation_token(
    token: str,
    kind: str,
    installation: int | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Under ``langgraph dev`` the App falls back to the person's ``gh`` token."""
    from openswe.github import app as github_app
    from openswe.github import http as github_http
    from openswe.run_config import RunConfig

    async def mint(**_kwargs: object) -> str:
        return token

    monkeypatch.setattr(github_app, "get_github_app_installation_token", mint)
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig(thread_id="thread-id", run_id="run-id", invocation_id="invocation-id"),
    )
    monkeypatch.setattr(
        github_http,
        "GITHUB_TRANSPORT",
        MeteredTransport(httpx2.MockTransport(lambda _request: httpx2.Response(200))),
    )
    caplog.set_level(logging.INFO, logger="openswe.github.metering")
    async with github_http.GitHubClient.as_app(installation_id=456) as github:
        await github.http.get("https://api.github.com/rate_limit")

    (record,) = _records(caplog)
    assert record.github_token_kind == kind
    assert getattr(record, "github_installation_id", None) == installation
    assert record.github_duration_ms >= 0
    assert (record.thread_id, record.run_id, record.invocation_id) == (
        "thread-id",
        "run-id",
        "invocation-id",
    )


@pytest.mark.asyncio
async def test_a_transport_error_is_logged_and_raised(caplog: pytest.LogCaptureFixture) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("private-detail", request=request)

    transport = MeteredTransport(httpx2.MockTransport(fail))
    caplog.set_level(logging.INFO, logger="openswe.github.metering")
    async with httpx2.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx2.ConnectError):
            await http.get("https://api.github.com/rate_limit")

    (record,) = _records(caplog)
    assert record.github_error_type == "ConnectError"
    assert not hasattr(record, "github_status")
    assert "private-detail" not in str(record.__dict__)
