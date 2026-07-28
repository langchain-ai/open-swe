"""Tests for the Linear self-trigger guard (OPE-23).

The fixture is a real captured Linear webhook delivery (a forge-bot-authored
comment on OPE-21), so the payload shape is authoritative, not invented.

Run:  .venv/bin/python -m pytest speedbay/tests/test_linear_guard.py -x -q
"""

from __future__ import annotations

import asyncio
import copy
import json
import pathlib

import pytest

from agent.utils import speedbay_linear_guard as guard

FIXTURE = pathlib.Path(__file__).parent / "linear_comment_payload.json"
PAYLOAD = json.loads(FIXTURE.read_text())
AUTHOR_ID = PAYLOAD["actor"]["id"]


def _run(coro):
    return asyncio.run(coro)


async def _fixed_viewer(value):
    return value


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The viewer id caches for the process; tests need isolation."""
    guard._resolved = False
    guard._cached_id = None
    yield
    guard._resolved = False
    guard._cached_id = None


def test_self_comment_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_viewer_id", lambda: _fixed_viewer(AUTHOR_ID))
    assert _run(guard.is_self_comment(PAYLOAD)) is True


def test_human_comment_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_viewer_id", lambda: _fixed_viewer("some-other-user-id"))
    assert _run(guard.is_self_comment(PAYLOAD)) is False


def test_matches_data_userid_when_actor_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """actor and data.userId are separate carriers; either alone must match."""
    monkeypatch.setattr(guard, "_viewer_id", lambda: _fixed_viewer(AUTHOR_ID))
    stripped = copy.deepcopy(PAYLOAD)
    del stripped["actor"]
    assert stripped["data"]["userId"] == AUTHOR_ID  # fixture sanity
    assert _run(guard.is_self_comment(stripped)) is True


def test_fail_open_when_viewer_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No viewer id (key missing / Linear down) must never block human comments."""
    monkeypatch.setattr(guard, "_viewer_id", lambda: _fixed_viewer(None))
    assert _run(guard.is_self_comment(PAYLOAD)) is False


def test_viewer_id_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert _run(guard._viewer_id()) is None


def test_transient_failure_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed resolution must retry on the next delivery, not stick for life."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")

    class Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self):
            raise ConnectionError("linear down")
        async def __aexit__(self, *a): ...

    monkeypatch.setattr(guard.httpx, "AsyncClient", Boom)
    assert _run(guard._viewer_id()) is None
    assert guard._resolved is False  # next call retries


def test_empty_payload_is_not_self(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_viewer_id", lambda: _fixed_viewer(AUTHOR_ID))
    assert _run(guard.is_self_comment({})) is False
