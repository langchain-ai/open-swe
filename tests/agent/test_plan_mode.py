import asyncio
from typing import Any

import pytest

from agent import server
from agent.dashboard.threads import runs as thread_runs
from agent.dashboard.threads import summary as thread_summary
from agent.prompt import construct_system_prompt
from tests.conftest import patch_thread_module


@pytest.mark.parametrize("enabled", [False, True])
def test_construct_system_prompt_gates_active_plan_mode(enabled: bool) -> None:
    prompt = construct_system_prompt(working_dir="/work", plan_mode=enabled)

    assert ("### Plan Mode (ACTIVE)" in prompt) is enabled


@pytest.mark.parametrize(
    "source", ["dashboard", "slack", "linear", "github", "schedule", "desktop", "generic"]
)
def test_plan_mode_requires_an_explicit_request_for_every_source(source: str) -> None:
    prompt = construct_system_prompt(
        working_dir="/work", source=source, slack_context=source == "slack"
    )

    assert "Call `enter_plan_mode` only when the user explicitly asks" in prompt
    assert "Do not infer plan mode from task complexity, size, or ambiguity" in prompt
    assert "If a task would genuinely benefit from a structured plan" not in prompt


def test_plan_mode_prompt_requests_slack_approval_options() -> None:
    prompt = construct_system_prompt(
        working_dir="/work", plan_mode=True, source="slack", slack_context=True
    )

    assert 'options=["Approve & implement", "Request changes"]' in prompt
    assert "do not send approval buttons" not in prompt


def test_plan_mode_excluded_tools_cover_mutating_tools() -> None:
    excluded = server.PLAN_MODE_EXCLUDED_TOOLS
    for tool in (
        "task",
        "manage_baby_sit",
        "manage_thread",
        "open_pull_request",
        "recreate_sandbox",
        "request_pr_review",
        "save_user_skill",
        "delete_user_skill",
        "slack_move_thread",
        "slack_start_new_thread",
    ):
        assert tool in excluded
    # Read-only tools, plan-file editing tools, and explicit plan approval stay available.
    assert "approve_plan" not in excluded
    assert "list_threads" not in excluded
    assert "get_thread" not in excluded
    assert "read_file" not in excluded
    assert "write_file" not in excluded
    assert "edit_file" not in excluded
    assert "execute" not in excluded
    assert "browser_close" not in excluded


class _FakeThreadsClient:
    async def create(
        self, *, thread_id: str, metadata: dict[str, Any], if_exists: str
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": {}}


class _FakeRunsClient:
    def __init__(self) -> None:
        self.configurable: dict[str, Any] | None = None

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: dict[str, Any],
        config: dict[str, Any],
        if_not_exists: str = "reject",
        stream_mode: list[str] | None = None,
        stream_resumable: bool = False,
    ) -> dict[str, str]:
        self.configurable = config["configurable"]
        return {"run_id": "run-id"}


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreadsClient()
        self.runs = _FakeRunsClient()


@pytest.fixture
def dashboard_run_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangGraphClient:
    client = _FakeLangGraphClient()

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, Any]) -> str:
        return "octo@example.com"

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    return client


def _run_start_command(plan_mode: bool | None) -> dict[str, Any]:
    configurable: dict[str, Any] = {}
    if plan_mode is not None:
        configurable["plan_mode"] = plan_mode
    return {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"role": "user", "content": "do work"}]},
            "config": {"configurable": configurable},
        },
    }


def test_run_start_passes_plan_mode_when_enabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    enriched = asyncio.run(
        thread_runs._enrich_run_start_command(
            "thread-id",
            "octo",
            _run_start_command(True),
            metadata={"source": "dashboard", "github_login": "octo"},
            creating=False,
        )
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["plan_mode"] is True


def test_run_start_omits_plan_mode_when_disabled(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    enriched = asyncio.run(
        thread_runs._enrich_run_start_command(
            "thread-id",
            "octo",
            _run_start_command(None),
            metadata={"source": "dashboard", "github_login": "octo"},
            creating=False,
        )
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert "plan_mode" not in configurable


async def test_thread_summary_reports_plan_mode() -> None:
    summary = await thread_summary._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "plan_mode": True}}
    )
    assert summary["planMode"] is True

    summary_off = await thread_summary._thread_summary(
        {"thread_id": "t2", "metadata": {"source": "dashboard"}}
    )
    assert summary_off["planMode"] is False


def _plan_configurable(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    monkeypatch.setattr(
        "coding_agent.run_config.get_config", lambda: {"configurable": dict(values)}
    )


def _patch_plan_store(monkeypatch: pytest.MonkeyPatch, content: dict[str, Any]) -> dict[str, Any]:
    """Stub the store reads/writes ``DashboardPlanStore`` drives, recording the writes."""
    from agent.dashboard import plan_store

    saved: dict[str, Any] = {}

    async def fake_get_content(thread_id: str, *, raise_on_error: bool = False) -> dict[str, Any]:
        assert raise_on_error is True
        return content

    async def fake_list_comments(
        thread_id: str, *, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        assert raise_on_error is True
        return [{"author": "Alice", "body": "add tests"}]

    async def fake_set_status(
        thread_id: str,
        status: str,
        *,
        plan_mode: Any = None,
        approved_by: Any = None,
    ) -> None:
        saved.update(
            thread_id=thread_id, status=status, plan_mode=plan_mode, approved_by=approved_by
        )

    async def fake_save_content(thread_id: str, **kwargs: Any) -> None:
        saved.update(thread_id=thread_id, **kwargs)

    monkeypatch.setattr(plan_store, "get_plan_content", fake_get_content)
    monkeypatch.setattr(plan_store, "list_plan_comments", fake_list_comments)
    monkeypatch.setattr(plan_store, "set_plan_status", fake_set_status)
    monkeypatch.setattr(plan_store, "save_plan_content", fake_save_content)
    return saved


async def test_dashboard_store_approve_returns_the_plan_and_records_the_approver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard.plan_store import DashboardPlanStore

    _plan_configurable(
        monkeypatch, thread_id="t1", github_login="octo", user_email="octo@example.com"
    )
    saved = _patch_plan_store(
        monkeypatch,
        {
            "html": "<html><head><title>Plan</title></head><body>Do it</body></html>",
            "status": "ready",
        },
    )

    approved = await DashboardPlanStore("t1").approve()

    assert "<title>Plan</title>" in approved.document
    assert "add tests" in approved.reviewer_feedback
    assert saved == {
        "thread_id": "t1",
        "status": "approved",
        "plan_mode": False,
        "approved_by": {"id": "octo", "name": "octo", "source": "agent"},
    }


@pytest.mark.parametrize(
    ("configurable", "expected"),
    [
        (
            {"github_login": "current-user", "source": "dashboard"},
            {"id": "current-user", "name": "current-user", "source": "dashboard"},
        ),
        (
            {"github_login": "other", "source": "linear"},
            {"id": "other", "name": "other", "source": "linear"},
        ),
        (
            {
                "source": "slack",
                "github_login": "owner",
                "slack_thread": {
                    "channel_id": "C1",
                    "thread_ts": "1.0",
                    "triggering_user_id": "U9",
                    "triggering_user_name": "Sender",
                },
            },
            {"id": "U9", "name": "Sender", "source": "slack"},
        ),
    ],
)
async def test_dashboard_store_approver_is_whoever_this_run_answers(
    monkeypatch: pytest.MonkeyPatch, configurable: dict[str, Any], expected: dict[str, str]
) -> None:
    from agent.dashboard.plan_store import DashboardPlanStore

    _plan_configurable(monkeypatch, thread_id="t1", **configurable)
    saved = _patch_plan_store(monkeypatch, {"markdown": "# Plan", "status": "ready"})

    await DashboardPlanStore("t1").approve()

    assert saved["approved_by"] == expected


async def test_dashboard_store_refuses_to_approve_shared_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard.plan_store import DashboardPlanStore
    from coding_agent.plans import PlanNotApprovable

    _plan_configurable(monkeypatch, thread_id="t1")
    saved = _patch_plan_store(monkeypatch, {"html": "# Report", "status": "shared"})

    with pytest.raises(PlanNotApprovable, match="not an implementation plan"):
        await DashboardPlanStore("t1").approve()

    assert saved == {}


async def test_dashboard_store_begin_marks_the_thread_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard.plan_store import DashboardPlanStore

    saved = _patch_plan_store(monkeypatch, {})

    await DashboardPlanStore("t1").begin()

    assert saved == {
        "thread_id": "t1",
        "status": "planning",
        "plan_mode": True,
        "approved_by": None,
    }


@pytest.mark.parametrize(
    ("plan_mode", "status", "recorded_plan_mode"),
    [(True, "ready", True), (False, "shared", None)],
)
async def test_dashboard_store_publish_status_follows_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
    plan_mode: bool,
    status: str,
    recorded_plan_mode: bool | None,
) -> None:
    from agent.dashboard.plan_store import DashboardPlanStore

    saved = _patch_plan_store(monkeypatch, {})

    await DashboardPlanStore("t1").publish(
        document="<h1>Plan</h1>", source_path="/workspace/plans/p.html", plan_mode=plan_mode
    )

    assert saved == {
        "thread_id": "t1",
        "html": "<h1>Plan</h1>",
        "status": status,
        "plan_file_path": "/workspace/plans/p.html",
        "plan_mode": recorded_plan_mode,
    }


async def test_dashboard_store_reads_plan_mode_from_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.dashboard import plan_store

    class _Threads:
        def __init__(self, metadata: dict[str, Any]) -> None:
            self._metadata = metadata

        async def get(self, thread_id: str) -> dict[str, Any]:
            assert thread_id == "t1"
            return {"metadata": self._metadata}

    monkeypatch.setattr(
        plan_store,
        "get_client",
        lambda: type("C", (), {"threads": _Threads({"plan_mode": True})})(),
    )
    assert await plan_store.DashboardPlanStore("t1").is_active() is True

    monkeypatch.setattr(
        plan_store, "get_client", lambda: type("C", (), {"threads": _Threads({})})()
    )
    assert await plan_store.DashboardPlanStore("t1").is_active() is False
