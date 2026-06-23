import pytest

from agent.utils import langsmith as ls_utils
from agent.utils.tracing import AGENT_TRACING_PROJECT, REVIEW_TRACING_PROJECT


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ls_utils._PROJECT_ID_CACHE.clear()


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example")
    monkeypatch.setenv("LANGSMITH_TENANT_ID_PROD", "tenant-1")
    monkeypatch.delenv("LANGSMITH_TRACING_PROJECT_ID_PROD", raising=False)


def test_trace_url_resolves_project_id_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(
        ls_utils,
        "_resolve_project_id_by_name",
        lambda name: "agent-pid" if name == AGENT_TRACING_PROJECT else "review-pid",
    )

    agent_url = ls_utils.get_langsmith_trace_url("t1")
    review_url = ls_utils.get_langsmith_trace_url("t2", project_name=REVIEW_TRACING_PROJECT)

    assert agent_url == "https://smith.example/o/tenant-1/projects/p/agent-pid/t/t1"
    assert review_url == "https://smith.example/o/tenant-1/projects/p/review-pid/t/t2"


def test_trace_url_falls_back_to_env_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING_PROJECT_ID_PROD", "env-pid")
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", lambda name: None)

    url = ls_utils.get_langsmith_trace_url("t3")

    assert url == "https://smith.example/o/tenant-1/projects/p/env-pid/t/t3"


def test_trace_url_none_when_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", lambda name: None)

    assert ls_utils.get_langsmith_trace_url("t4") is None


def test_resolve_project_id_caches_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeProject:
        id = "pid-123"

    class _FakeClient:
        def read_project(self, *, project_name: str) -> _FakeProject:
            calls.append(project_name)
            return _FakeProject()

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _FakeClient())

    first = ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT)
    second = ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT)

    assert first == "pid-123"
    assert second == "pid-123"
    assert calls == [AGENT_TRACING_PROJECT]


def test_resolve_project_id_caches_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeClient:
        def read_project(self, *, project_name: str) -> None:
            calls.append(project_name)
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _FakeClient())

    assert ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert calls == [AGENT_TRACING_PROJECT]


def test_trace_url_none_when_tenant_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)

    def _boom() -> None:
        raise AssertionError("must not build a client when the tenant id is unset")

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", _boom)

    assert ls_utils.get_langsmith_trace_url("t5") is None
