"""Fixtures shared by the dashboard tests."""

from collections.abc import Callable
from typing import Any

import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import authz
from agent.dashboard.threads import listing as thread_listing
from agent.dashboard.threads import runs as thread_runs


@pytest.fixture
def dashboard_client(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> Callable[..., FakeLangGraphClient]:
    """Seed one in-memory LangGraph client behind every dashboard handler.

    ``authz`` and the two thread modules are the only places in the dashboard
    that hold their own ``langgraph_client``, so patching all three spares each
    test from knowing which of them the handler it drives happens to reach.
    Keyword arguments are the ``FakeLangGraphClient`` constructor's.
    """

    def _install(**kwargs: Any) -> FakeLangGraphClient:
        return patched_langgraph_client(
            authz, thread_listing, thread_runs, client=FakeLangGraphClient(**kwargs)
        )

    return _install
