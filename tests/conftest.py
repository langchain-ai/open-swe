"""Shared pytest fixtures."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent import store as agent_store
from agent.review import dispatch as review_dispatch
from agent.utils import ttl_cache
from agent.utils.sandbox_registry import SANDBOX_BACKENDS


@pytest.fixture(autouse=True)
def _reset_ttl_cache() -> Iterator[None]:
    """Keep the process-global TTL cache from leaking team settings between tests."""
    ttl_cache.clear()
    yield
    ttl_cache.clear()


@pytest.fixture(autouse=True)
def _reset_sandbox_backends() -> Iterator[None]:
    """Keep the process-global sandbox registry from leaking backends between tests."""
    SANDBOX_BACKENDS.clear()
    yield
    SANDBOX_BACKENDS.clear()


@pytest.fixture(autouse=True)
def _default_enable_auto_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat automatic reviews as enabled for every repo by default.

    The dashboard's opt-in list (loaded by :func:`agent.settings.enabled_repos.is_review_repo_enabled`)
    is empty in the test environment because there is no live LangGraph Store.

    Tests targeting the automatic-review gate should override this fixture or set
    ``monkeypatch.setattr(review_dispatch, "is_review_repo_enabled", ...)`` to a stricter stub.
    """

    async def _enabled(_owner: str, _name: str) -> bool:
        return True

    monkeypatch.setattr(review_dispatch, "is_review_repo_enabled", _enabled)


@pytest.fixture
def fake_langgraph_client() -> FakeLangGraphClient:
    """An empty in-memory LangGraph client; seed it through its sub-clients."""
    return FakeLangGraphClient()


@pytest.fixture
def patched_langgraph_client(
    monkeypatch: pytest.MonkeyPatch, fake_langgraph_client: FakeLangGraphClient
) -> Callable[..., FakeLangGraphClient]:
    """Install ``fake_langgraph_client`` at the module seams that reach the platform.

    ``agent.store.store_client`` -- the one way into the Store -- is always
    patched. Pass the modules that hold their own ``langgraph_client`` (or
    ``get_client``, via ``attr=``) alongside it, and pass ``client=`` to install
    a differently seeded one::

        client = patched_langgraph_client(authz, threads.runs)
    """

    def _install(
        *modules: Any,
        attr: str = "langgraph_client",
        client: FakeLangGraphClient | None = None,
    ) -> FakeLangGraphClient:
        target = client if client is not None else fake_langgraph_client

        def accessor(*_args: Any, **_kwargs: Any) -> FakeLangGraphClient:
            return target

        monkeypatch.setattr(agent_store, "store_client", accessor)
        for module in modules:
            monkeypatch.setattr(module, attr, accessor)
        return target

    return _install
