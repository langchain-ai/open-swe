import pytest

from agent.utils import langsmith as ls_utils
from agent.utils.tracing import AGENT_TRACING_PROJECT, REVIEW_TRACING_PROJECT

_REAL_DISCOVER_TENANT_ID = ls_utils._discover_tenant_id


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ls_utils._PROJECT_ID_CACHE.clear()
    ls_utils._TENANT_ID_CACHE = None
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: None)


def _resolver(ids: dict[str, str], *, default: str | None = None):
    async def _resolve(name: str) -> str | None:
        return ids.get(name, default)

    return _resolve


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example")
    monkeypatch.setenv("LANGSMITH_TENANT_ID_PROD", "tenant-1")
    monkeypatch.delenv("LANGSMITH_TRACING_PROJECT_ID_PROD", raising=False)


async def test_trace_url_resolves_project_id_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(
        ls_utils,
        "_resolve_project_id_by_name",
        _resolver({AGENT_TRACING_PROJECT: "agent-pid"}, default="review-pid"),
    )

    agent_url = await ls_utils.get_langsmith_trace_url("t1")
    review_url = await ls_utils.get_langsmith_trace_url("t2", project_name=REVIEW_TRACING_PROJECT)

    assert agent_url == "https://smith.example/o/tenant-1/projects/p/agent-pid/t/t1"
    assert review_url == "https://smith.example/o/tenant-1/projects/p/review-pid/t/t2"


async def test_trace_url_falls_back_to_env_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING_PROJECT_ID_PROD", "env-pid")
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}))

    url = await ls_utils.get_langsmith_trace_url("t3")

    assert url == "https://smith.example/o/tenant-1/projects/p/env-pid/t/t3"


async def test_trace_url_none_when_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}))

    assert await ls_utils.get_langsmith_trace_url("t4") is None


async def test_resolve_project_id_caches_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeProject:
        id = "pid-123"

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def read_project(self, *, project_name: str) -> _FakeProject:
            calls.append(project_name)
            return _FakeProject()

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _FakeClient())

    first = await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT)
    second = await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT)

    assert first == "pid-123"
    assert second == "pid-123"
    assert calls == [AGENT_TRACING_PROJECT]


async def test_resolve_project_id_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def read_project(self, *, project_name: str) -> None:
            calls.append(project_name)
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _FakeClient())

    assert await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert calls == [AGENT_TRACING_PROJECT, AGENT_TRACING_PROJECT]


async def test_trace_url_none_when_tenant_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)

    def _boom() -> None:
        raise AssertionError("must not build a client when the tenant id is unset")

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", _boom)

    assert await ls_utils.get_langsmith_trace_url("t5") is None


async def test_tenant_id_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TENANT_ID_PROD", "tenant-env")
    ls_utils._TENANT_ID_CACHE = "tenant-cached"

    assert await ls_utils.resolve_tenant_id() == "tenant-env"


async def test_tenant_id_is_learned_from_project_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)

    class _Project:
        id = "pid"
        tenant_id = "tenant-from-project"

    class _Client:
        async def read_project(self, *, project_name: str) -> _Project:
            return _Project()

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _Client())

    assert await ls_utils._resolve_project_id_by_name("open-swe-agent") == "pid"
    assert await ls_utils.resolve_tenant_id() == "tenant-from-project"


async def test_tenant_id_falls_back_to_listing_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)
    calls = 0

    def _discover() -> str:
        nonlocal calls
        calls += 1
        return "tenant-listed"

    monkeypatch.setattr(ls_utils, "_discover_tenant_id", _discover)

    assert await ls_utils.resolve_tenant_id() == "tenant-listed"
    assert await ls_utils.resolve_tenant_id() == "tenant-listed"
    assert calls == 1


async def test_tenant_id_none_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGSMITH_TENANT_ID_PROD",
        "LANGSMITH_API_KEY_PROD",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", _REAL_DISCOVER_TENANT_ID)

    assert ls_utils._discover_tenant_id() is None
    assert await ls_utils.resolve_tenant_id() is None


async def test_tenant_discovery_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)

    def _boom() -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(ls_utils, "_discover_tenant_id", _boom)

    assert await ls_utils.resolve_tenant_id() is None


async def test_trace_url_uses_discovered_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example")
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: "tenant-d")
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}, default="pid"))

    assert await ls_utils.get_langsmith_trace_url("t9") == (
        "https://smith.example/o/tenant-d/projects/p/pid/t/t9"
    )


def test_host_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example/")

    assert ls_utils.langsmith_host_url() == "https://smith.example"


def test_host_url_derived_from_self_hosted_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_URL_PROD", raising=False)
    monkeypatch.delenv("LANGSMITH_ENDPOINT_PROD", raising=False)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://langsmith.acme.internal/api")

    assert ls_utils.langsmith_host_url() == "https://langsmith.acme.internal"


def test_host_url_derived_from_regional_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_URL_PROD", raising=False)
    monkeypatch.setenv("LANGSMITH_ENDPOINT_PROD", "https://eu.api.smith.langchain.com")

    assert ls_utils.langsmith_host_url() == "https://eu.smith.langchain.com"


def test_host_url_default_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LANGSMITH_URL_PROD", "LANGSMITH_ENDPOINT_PROD", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)

    assert ls_utils.langsmith_host_url() == "https://smith.langchain.com"
