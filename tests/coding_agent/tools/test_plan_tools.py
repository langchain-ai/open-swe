"""The plan-mode tools against a fake plan store.

The tools decide when plan mode is entered and left, and what the model is told;
where the plan is stored and who is recorded as approving it belong to the store.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool as as_tool
from langgraph.types import Command

from coding_agent.plans import ApprovedPlan, PlanNotApprovable, PlanStore
from coding_agent.tools.plan_mode import plan_tools

_PLAN_PATH = "/workspace/plans/2026-06-29-add-webhook-retries.html"
_PLAN_HTML = "<h1>Plan</h1>"


@dataclass
class FakeStore:
    """Both the factory the tools are bound to and the single store it hands out."""

    approved: ApprovedPlan = field(default_factory=lambda: ApprovedPlan(document=_PLAN_HTML))
    active: bool = False
    error: Exception | None = None
    threads: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    published: dict[str, Any] = field(default_factory=dict)

    def __call__(self, thread_id: str) -> PlanStore:
        self.threads.append(thread_id)
        return self

    def _record(self, call: str) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(call)

    async def begin(self) -> None:
        self._record("begin")

    async def publish(self, **kwargs: Any) -> None:
        self._record("publish")
        self.published = kwargs

    async def approve(self) -> ApprovedPlan:
        self._record("approve")
        return self.approved

    async def is_active(self) -> bool:
        self.calls.append("is_active")
        return self.active


def _tool(name: str, store: FakeStore) -> Any:
    return next(tool for tool in plan_tools(store) if tool.__name__ == name)


class _Backend:
    def __init__(self, content: str = _PLAN_HTML) -> None:
        self.content = content
        self.reads: list[str] = []

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        self.reads.append(file_path)
        return {"file_data": {"encoding": "utf-8", "content": self.content}}


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> _Backend:
    backend = _Backend()

    async def fake_backend(thread_id: str) -> _Backend:
        return backend

    monkeypatch.setattr("coding_agent.tools.plan_mode.get_sandbox_backend", fake_backend)
    return backend


def _configurable(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    monkeypatch.setattr(
        "coding_agent.run_config.get_config", lambda: {"configurable": dict(values)}
    )


@pytest.mark.parametrize("store_error", [None, RuntimeError("store down")])
async def test_enter_plan_mode_turns_plan_mode_on(
    monkeypatch: pytest.MonkeyPatch, store_error: Exception | None
) -> None:
    """A bookkeeping failure must not undo a run that is already planning."""
    _configurable(monkeypatch, thread_id="t1")
    store = FakeStore(error=store_error)

    # Wrapped as the agent does so the InjectedToolCallId is supplied from the call.
    result = await as_tool(_tool("enter_plan_mode", store)).ainvoke(
        {"name": "enter_plan_mode", "args": {}, "id": "call-1", "type": "tool_call"}
    )

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is True
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert store.threads == ["t1"]
    assert store.calls == ([] if store_error else ["begin"])


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("   ", "empty"),
        ("/workspace/plans/plan.txt", "HTML"),
        ("/workspace/plan.html", "/workspace/plans"),
    ],
)
async def test_save_plan_rejects_paths_outside_the_plans_directory(
    path: str, expected: str
) -> None:
    result = await _tool("save_plan", FakeStore())(path)

    assert result["success"] is False
    assert expected in result["error"]


@pytest.mark.parametrize("planning", [None, "state", "config"])
async def test_save_plan_publishes_the_sandbox_file(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend, planning: str | None
) -> None:
    _configurable(
        monkeypatch, thread_id="t1", **({"plan_mode": True} if planning == "config" else {})
    )
    store = FakeStore()

    result = await _tool("save_plan", store)(
        _PLAN_PATH, state={"plan_mode": True} if planning == "state" else None
    )

    assert result == {"success": True, "path": _PLAN_PATH}
    assert sandbox.reads == [_PLAN_PATH]
    assert store.published["source_path"] == _PLAN_PATH
    # Under review while planning; outside plan mode the document is only shared.
    assert store.published["plan_mode"] is (planning is not None)
    assert "<title>Add webhook retries</title>" in store.published["document"]
    assert _PLAN_HTML in store.published["document"]


async def test_save_plan_reports_a_store_failure(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend
) -> None:
    _configurable(monkeypatch, thread_id="t1")
    store = FakeStore(error=RuntimeError("store down"))

    result = await _tool("save_plan", store)(_PLAN_PATH)

    assert result["success"] is False
    assert "failed to save plan" in result["error"]
    assert "store down" in result["error"]


async def test_approve_plan_exits_plan_mode_and_hands_over_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch, thread_id="t1", plan_mode=True)
    store = FakeStore(
        approved=ApprovedPlan(
            document="<html><head><title>Plan</title></head><body>Do it</body></html>",
            reviewer_feedback="1. add tests",
        )
    )

    result = await _tool("approve_plan", store)(state={"plan_mode": True}, tool_call_id="call-1")

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is False
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert "<title>Plan</title>" in message.content
    assert "add tests" in message.content
    assert "reasonable engineering judgment" in message.content
    assert store.calls == ["approve"]


@pytest.mark.parametrize("active", [True, False])
async def test_approve_plan_falls_back_to_the_store_for_plan_mode(
    monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    """Nothing in state or the configurable says whether planning is still on."""
    _configurable(monkeypatch, thread_id="t1")
    store = FakeStore(active=active)

    result = await _tool("approve_plan", store)(tool_call_id="call-1")

    if active:
        assert isinstance(result, Command)
        assert store.calls == ["is_active", "approve"]
    else:
        assert result == {"success": False, "error": "plan mode is not active for this thread"}
        assert "approve" not in store.calls


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PlanNotApprovable("shared content is not a plan"), "shared content is not a plan"),
        (RuntimeError("store down"), "failed to approve plan: store down"),
    ],
    ids=["not-approvable-verbatim", "store-failure"],
)
async def test_approve_plan_reports_why_it_could_not_approve(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    _configurable(monkeypatch, thread_id="t1", plan_mode=True)
    store = FakeStore(error=error)

    result = await _tool("approve_plan", store)(state={"plan_mode": True}, tool_call_id="call-1")

    assert result == {"success": False, "error": expected}


async def test_approve_plan_without_a_thread_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch)
    store = FakeStore()

    result = await _tool("approve_plan", store)(tool_call_id="call-1")

    assert result == {"success": False, "error": "no thread_id in run config"}
    assert store.threads == []
