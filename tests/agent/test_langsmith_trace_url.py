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


async def test_trace_url_uses_discovered_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example/api")
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: "tenant-d")
    monkeypatch.setattr(ls_utils, "_resolve_project_id_by_name", _resolver({}, default="pid"))

    assert await ls_utils.get_langsmith_trace_url("t9") == (
        "https://smith.example/o/tenant-d/projects/p/pid/t/t9"
    )
