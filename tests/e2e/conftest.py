"""Shared fixtures for open-swe end-to-end tests.

The tests assume two local servers are already running:

- ``http://localhost:13765`` — hack/fake_deps (Slack + GitHub + LLM mock)
- ``http://localhost:2025``  — ``make dev`` (langgraph dev with .env loaded)

We preflight both. If either is down we skip the entire module rather than
emitting a wall of failures.

Fixtures do NOT start the servers themselves — starting/stopping a
langgraph dev server reliably from pytest is more pain than it's worth.
Run them in terminals before invoking ``make test-e2e``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Page, expect

FAKE_DEPS_URL = os.environ.get("FAKE_DEPS_URL", "http://localhost:13765")
AGENT_URL = os.environ.get("LANGGRAPH_URL", "http://localhost:2025")

# Everything under test runs locally against the scripted LLM proxy, so
# nothing should legitimately take more than a second or two. 5s is a
# generous single global timeout for both page actions and expect().
_DEFAULT_TIMEOUT_MS = 5_000


def _ping(url: str, path: str = "/", timeout: float = 2.0) -> bool:
    try:
        httpx.get(f"{url}{path}", timeout=timeout).raise_for_status()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session", autouse=True)
def _preflight() -> None:
    if not _ping(FAKE_DEPS_URL, "/health") or not _ping(AGENT_URL, "/ok"):
        pytest.skip(
            f"fake-deps ({FAKE_DEPS_URL}) or langgraph dev ({AGENT_URL}) not "
            "reachable — run `make up-test` first",
            allow_module_level=True,
        )


@pytest.fixture
def fake_deps_url() -> str:
    return FAKE_DEPS_URL


@pytest.fixture
def reset_fake_state() -> Iterator[None]:
    """Wipe Slack messages, GitHub issues/PRs, and any leftover LLM scripts."""
    httpx.post(f"{FAKE_DEPS_URL}/slack/ui/reset", timeout=5.0)
    httpx.post(f"{FAKE_DEPS_URL}/github/ui/reset", timeout=5.0)
    try:
        scripts = httpx.get(
            f"{FAKE_DEPS_URL}/anthropic/ui/scripts", timeout=2.0
        ).json().get("scripts", [])
        for s in scripts:
            httpx.delete(
                f"{FAKE_DEPS_URL}/anthropic/ui/script/{s['test_id']}", timeout=2.0
            )
    except Exception:  # noqa: BLE001
        pass
    yield


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    return {
        **browser_context_args,
        "viewport": {"width": 1400, "height": 900},
    }


@pytest.fixture(autouse=True)
def _short_timeouts(page: Page) -> None:
    page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    page.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)
    expect.set_options(timeout=_DEFAULT_TIMEOUT_MS)
