"""The MDA deployment's GitHub channel handler (``mda/rt/github.py``)."""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.github import GITHUB


@dataclass(frozen=True)
class _Event:
    """The fields of MDA's ``GitHubEvent`` the handler reads."""

    key: str = "issue_comment.created"
    delivery_id: str = "delivery-1"
    payload: dict[str, Any] = field(default_factory=lambda: {"action": "created"})


@dataclass(frozen=True)
class _Thread:
    """The fields of MDA's ``GitHubChannelThread`` the handler reads."""

    id: str = "00000000-0000-4000-8000-0000000000aa"
    interrupted: bool = False


def _backend(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> list[httpx2.Request]:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.test/")
    monkeypatch.setenv("MDA_SERVICE_SECRET", "service-secret")
    seen: list[httpx2.Request] = []
    real_client = httpx2.AsyncClient

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=answer)

    monkeypatch.setattr(
        "rt.intake.httpx2.AsyncClient",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(handler), **kwargs),
    )
    return seen


async def test_the_run_starts_as_the_backend_prepared_it(monkeypatch: pytest.MonkeyPatch) -> None:
    run = {
        "input": {"messages": [{"role": "user", "content": "Fix it"}]},
        "context": {"graph": "agent", "__run_token": "signed"},
        "metadata": {"channel": "github"},
    }
    seen = _backend(monkeypatch, {"run": run, "acts": True})

    result = await GITHUB.start_run(_Event(), _Thread())  # type: ignore[arg-type]

    assert result == (run["input"], {"context": run["context"], "metadata": run["metadata"]})
    [request] = seen
    assert str(request.url) == "https://backend.test/github/channel/runs"
    assert request.headers["x-mda-service-secret"] == "service-secret"
    assert json.loads(request.content) == {
        "thread_id": "00000000-0000-4000-8000-0000000000aa",
        "interrupted": False,
        "delivery_id": "delivery-1",
        "event": "issue_comment.created",
        "payload": {"action": "created"},
    }


async def test_an_event_the_backend_declines_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _backend(monkeypatch, {"run": None, "acts": False})

    assert await GITHUB.start_run(_Event(), _Thread()) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("acts", [True, False])
async def test_a_waiting_thread_is_told_only_when_open_swe_acts(
    monkeypatch: pytest.MonkeyPatch, acts: bool
) -> None:
    seen = _backend(monkeypatch, {"run": None, "acts": acts})

    result = await GITHUB.start_run(_Event(), _Thread(interrupted=True))  # type: ignore[arg-type]

    assert (result is not None) is acts
    assert json.loads(seen[0].content)["interrupted"] is True


async def test_the_handler_needs_the_backend_and_its_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.test")
    monkeypatch.delenv("MDA_SERVICE_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="MDA_SERVICE_SECRET"):
        await GITHUB.start_run(_Event(), _Thread())  # type: ignore[arg-type]
