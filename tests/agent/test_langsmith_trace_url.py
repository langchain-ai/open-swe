import pytest

from agent.utils import langsmith as ls_utils
from agent.utils.tracing import AGENT_TRACING_PROJECT, REVIEW_TRACING_PROJECT


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ls_utils._PROJECT_ID_CACHE.clear()


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
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def read_project(self, *, project_name: str) -> _FakeProject:
            calls.append(project_name)
            return _FakeProject()

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: _FakeClient())

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
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def read_project(self, *, project_name: str) -> None:
            calls.append(project_name)
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: _FakeClient())

    assert await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert await ls_utils._resolve_project_id_by_name(AGENT_TRACING_PROJECT) is None
    assert calls == [AGENT_TRACING_PROJECT, AGENT_TRACING_PROJECT]


async def test_create_thread_feedback_posts_thread_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    class _FakeClient:
        async def _arequest_with_retries(
            self, method: str, endpoint: str, **kwargs: object
        ) -> None:
            requests.append((method, endpoint, kwargs))

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: _FakeClient())
    monkeypatch.setattr(
        ls_utils,
        "_resolve_project_id_by_name",
        _resolver({AGENT_TRACING_PROJECT: "project-id"}),
    )

    result = await ls_utils.create_langsmith_thread_feedback(
        "thread-1",
        "github_pr_merged:https://github.com/lc/repo/pull/7",
        score=1.0,
        comment="merged",
        source_info={"source": "github_pr_merged"},
    )

    assert result is True
    assert requests == [
        (
            "POST",
            "/feedback",
            {
                "json": {
                    "id": str(
                        ls_utils._feedback_id(
                            "thread-1",
                            "github_pr_merged:https://github.com/lc/repo/pull/7",
                        )
                    ),
                    "key": "github_pr_merged:https://github.com/lc/repo/pull/7",
                    "score": 1.0,
                    "comment": "merged",
                    "session_id": "project-id",
                    "feedback_thread_id": "thread-1",
                    "feedback_source": {
                        "type": "api",
                        "metadata": {"source": "github_pr_merged"},
                    },
                }
            },
        )
    ]


async def test_trace_url_none_when_tenant_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)

    def _boom() -> None:
        raise AssertionError("must not build a client when the tenant id is unset")

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", _boom)

    assert await ls_utils.get_langsmith_trace_url("t5") is None
