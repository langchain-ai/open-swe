from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.utils import langsmith as ls_utils

_REAL_DISCOVER_TENANT_ID = ls_utils._discover_tenant_id


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ls_utils._PROJECT_ID_CACHE.clear()
    ls_utils._TENANT_ID_CACHE.clear()
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: None)


def _resolver(ids: dict[str, str], *, default: str | None = None):
    async def _resolve(name: str) -> str | None:
        return ids.get(name, default)

    return _resolve


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

    assert await ls_utils._resolve_project_id_by_name("my-deployment") is None
    assert await ls_utils._resolve_project_id_by_name("my-deployment") is None
    assert calls == ["my-deployment", "my-deployment"]


@pytest.mark.parametrize("score", [1.0, None])
async def test_create_thread_feedback_posts_thread_scope(
    monkeypatch: pytest.MonkeyPatch,
    score: float | None,
) -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setenv("LANGSMITH_PROJECT", "my-deployment")

    class _FakeClient:
        async def _arequest_with_retries(
            self, method: str, endpoint: str, **kwargs: object
        ) -> None:
            requests.append((method, endpoint, kwargs))

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: _FakeClient())
    monkeypatch.setattr(
        ls_utils,
        "_resolve_project_id_by_name",
        _resolver({"my-deployment": "project-id"}),
    )

    result = await ls_utils.create_langsmith_thread_feedback(
        "thread-1",
        "github_pr_merged:https://github.com/lc/repo/pull/7",
        score=score,
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
                    "score": score,
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


async def test_cached_ids_belong_to_the_credentials_that_produced_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotating the key or pointing at another workspace must not reuse the old ids."""
    monkeypatch.delenv("LANGSMITH_TENANT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "key-a")
    projects = {"key-a": ("pid-a", "tenant-a"), "key-b": ("pid-b", "tenant-b")}

    class _Client:
        async def read_project(self, *, project_name: str) -> object:
            project_id, tenant_id = projects[ls_utils.ENV.LANGSMITH_API_KEY.get()]
            return type("P", (), {"id": project_id, "tenant_id": tenant_id})()

    monkeypatch.setattr(ls_utils, "_build_langsmith_client", lambda: _Client())

    assert await ls_utils._resolve_project_id_by_name("proj") == "pid-a"
    assert await ls_utils.resolve_tenant_id() == "tenant-a"

    monkeypatch.setenv("LANGSMITH_API_KEY", "key-b")
    assert await ls_utils._resolve_project_id_by_name("proj") == "pid-b"
    assert await ls_utils.resolve_tenant_id() == "tenant-b"


async def test_tenant_id_falls_back_to_listing_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def _discover() -> str:
        nonlocal calls
        calls += 1
        return "tenant-listed"

    monkeypatch.setattr(ls_utils, "_discover_tenant_id", _discover)

    assert await ls_utils.resolve_tenant_id() == "tenant-listed"
    assert await ls_utils.resolve_tenant_id() == "tenant-listed"
    assert calls == 1


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("recovered-tool", "failed-model"), ("ambiguous", "root"), ("no-message", "root")],
)
async def test_error_url_follows_terminal_failure(
    monkeypatch: pytest.MonkeyPatch, legacy: bool, scenario: str, expected: str
) -> None:
    thread_url = "https://smith.example/o/tenant/projects/p/pid/t/thread"
    monkeypatch.setattr(ls_utils, "get_langsmith_trace_url", AsyncMock(return_value=thread_url))
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}, default="pid"))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(ls_utils, "_project_lookup_start", AsyncMock(return_value=start))
    queries: list[dict[str, object]] = []

    def query(**kwargs: object) -> AsyncIterator[SimpleNamespace]:
        queries.append(kwargs)

        async def results() -> AsyncIterator[SimpleNamespace]:
            if legacy and len(queries) == 1:
                return
            for run_id, order, error in [
                ("root", "1", "TimeoutError('model timed out')"),
                ("model-call", "1.2", "TimeoutError('model timed out')"),
                ("failed-model", "1.2.3", "TimeoutError('model timed out')"),
                ("recovered-tool", "1.4", "ValueError('bad tool input')"),
            ]:
                yield SimpleNamespace(id=run_id, dotted_order=order, error=error)
            if scenario == "ambiguous":
                yield SimpleNamespace(
                    id="recovered-timeout",
                    dotted_order="1.5",
                    error="TimeoutError('model timed out')",
                )

        return results()

    monkeypatch.setattr(
        ls_utils,
        "_build_langsmith_client",
        lambda: SimpleNamespace(runs=SimpleNamespace(query=query)),
    )

    terminal_error = None if scenario == "no-message" else "model timed out"
    assert await ls_utils.get_langsmith_error_url("thread", "invocation", terminal_error) == (
        f"https://smith.example/o/tenant/projects/p/pid/r/{expected}"
    )
    assert len(queries) == (2 if legacy else 1)
    for query_args in queries:
        assert query_args["project_ids"] == ["pid"]
        assert query_args["has_error"] is True
        assert query_args["min_start_time"] == start
        assert ls_utils._langsmith_metadata_filter("thread_id", "thread") in str(
            query_args["filter"]
        )
        field = "prepare_run_id" if legacy and query_args is queries[-1] else "invocation_id"
        assert ls_utils._langsmith_metadata_filter(field, "invocation") in str(query_args["filter"])


async def test_trace_url_uses_discovered_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example/api")
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: "tenant-d")
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}, default="pid"))

    assert await ls_utils.get_langsmith_trace_url("t9") == (
        "https://smith.example/o/tenant-d/projects/p/pid/t/t9"
    )
