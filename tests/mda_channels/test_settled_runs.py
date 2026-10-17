"""An MDA channel's run has no run-complete webhook: the channel reports it ended."""

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi import FastAPI

from openswe.mda_channels import completion
from openswe.threads import index

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt import intake

_SERVICE_SECRET = "service-secret"  # noqa: S105 - test value


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    api = FastAPI()
    api.include_router(completion.router)
    return api


async def _settle(
    app: FastAPI, body: dict[str, Any], secret: str = _SERVICE_SECRET
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/mda/channel/settled", json=body, headers={"x-mda-service-secret": secret}
        )


@pytest.mark.usefixtures("registry_db")
async def test_a_channel_run_that_ended_is_completed_from_what_mda_recorded(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel_thread, own_thread = str(uuid.uuid4()), str(uuid.uuid4())
    await index.create(channel_thread, runtime="mda", status="busy", metadata={"channel": "linear"})
    await index.create(own_thread, runtime="mda", status="busy", metadata={})
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "complete_ended_run", complete)

    response = await _settle(app, {"thread_id": channel_thread, "run_id": "run-1"})

    assert response.json() == {"completed": True}
    thread, run_id = complete.await_args.args
    assert (thread.thread_id, run_id) == (channel_thread, "run-1")
    # Runs on the backend's own threads have their webhook.
    response = await _settle(app, {"thread_id": own_thread, "run_id": "run-2"})
    assert response.json() == {"completed": False}
    complete.assert_awaited_once()


async def test_only_the_mda_deployment_may_report_a_run(app: FastAPI) -> None:
    body = {"thread_id": str(uuid.uuid4()), "run_id": "run-1"}
    assert (await _settle(app, body, secret="guess")).status_code == 401


@dataclass(frozen=True)
class _Thread:
    id: str = "00000000-0000-4000-8000-0000000000aa"


def _backend(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.test/")
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    seen: list[httpx2.Request] = []
    real_client = httpx2.AsyncClient

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"completed": True})

    monkeypatch.setattr(
        "rt.intake.httpx2.AsyncClient",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(handler), **kwargs),
    )
    return seen


@pytest.mark.parametrize(
    "outcome",
    [
        {"type": "completed", "run_id": "run-1"},
        {"type": "interrupted", "run_id": "run-1"},
        {"type": "cancelled", "run_id": "run-1"},
        {"type": "failed", "run_id": "run-1"},
    ],
)
async def test_the_channel_reports_each_run_that_ended(
    monkeypatch: pytest.MonkeyPatch, outcome: dict[str, str]
) -> None:
    seen = _backend(monkeypatch)

    await intake.settled(object(), _Thread(), outcome)  # type: ignore[arg-type]

    [request] = seen
    assert str(request.url) == "https://backend.test/mda/channel/settled"
    assert request.headers["x-mda-service-secret"] == _SERVICE_SECRET
    assert request.read() == (
        b'{"thread_id":"00000000-0000-4000-8000-0000000000aa","run_id":"run-1"}'
    )


@pytest.mark.parametrize(
    "outcome",
    [
        # MDA lost track of it: the reconcile job completes it once it ends.
        {"type": "unknown", "run_id": "run-1"},
        # No run started: nothing to complete.
        {"type": "failed"},
        {"type": "waiting"},
        {"type": "declined"},
        {"type": "stopped", "cancelled": 1},
    ],
)
async def test_the_channel_reports_nothing_without_a_run_that_ended(
    monkeypatch: pytest.MonkeyPatch, outcome: dict[str, Any]
) -> None:
    seen = _backend(monkeypatch)
    await intake.settled(object(), _Thread(), outcome)  # type: ignore[arg-type]
    assert seen == []
