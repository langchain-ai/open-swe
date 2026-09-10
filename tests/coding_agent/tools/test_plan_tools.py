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

_PLAN_PATH = "/workspace/plans/2026-06-29-test-plan.html"
_PLAN_HTML = (
    "<!doctype html><html><head><title>Plan</title></head><body><h1>Plan</h1></body></html>"
)


@dataclass
class FakePlanStore:
    thread_id: str
    approved: ApprovedPlan = field(
        default_factory=lambda: ApprovedPlan(document="<h1>Plan</h1>", reviewer_feedback="")
    )
    active: bool = False
    error: Exception | None = None
    calls: list[Any] = field(default_factory=list)

    async def begin(self) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append("begin")

    async def publish(self, *, document: str, source_path: str, plan_mode: bool) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("publish", document, source_path, plan_mode))

    async def approve(self) -> ApprovedPlan:
        if self.error is not None:
            raise self.error
        self.calls.append("approve")
        return self.approved

    async def is_active(self) -> bool:
        self.calls.append("is_active")
        return self.active


@dataclass
class FakeStores:
    """The factory the tools are bound to; keeps every store it handed out."""

    store: FakePlanStore | None = None
    approved: ApprovedPlan | None = None
    active: bool = False
    error: Exception | None = None
    threads: list[str] = field(default_factory=list)

    def __call__(self, thread_id: str) -> PlanStore:
        self.threads.append(thread_id)
        store = FakePlanStore(thread_id, active=self.active, error=self.error)
        if self.approved is not None:
            store.approved = self.approved
        self.store = store
        return store


def _tools(stores: FakeStores) -> tuple[Any, Any, Any]:
    enter_plan_mode, save_plan, approve_plan = plan_tools(stores)
    return enter_plan_mode, save_plan, approve_plan


class _Backend:
    def __init__(self, content: str = _PLAN_HTML) -> None:
        self.content = content
        self.reads: list[tuple[str, int, int]] = []

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        self.reads.append((file_path, offset, limit))
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


def test_tool_names_match_their_prompt_resources() -> None:
    assert [tool.__name__ for tool in plan_tools(FakeStores())] == [
        "enter_plan_mode",
        "save_plan",
        "approve_plan",
    ]


async def test_enter_plan_mode_records_entry_and_turns_plan_mode_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch, thread_id="t1")
    stores = FakeStores()
    enter_plan_mode, _save, _approve = _tools(stores)

    # Wrap as the agent does so the InjectedToolCallId is supplied from the call.
    result = await as_tool(enter_plan_mode).ainvoke(
        {"name": "enter_plan_mode", "args": {}, "id": "call-1", "type": "tool_call"}
    )

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is True
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert stores.threads == ["t1"]
    assert stores.store is not None
    assert stores.store.calls == ["begin"]


async def test_enter_plan_mode_still_enters_when_the_store_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run is already planning; a bookkeeping failure must not undo that."""
    _configurable(monkeypatch, thread_id="t1")
    enter_plan_mode, _save, _approve = _tools(FakeStores(error=RuntimeError("store down")))

    result = await as_tool(enter_plan_mode).ainvoke(
        {"name": "enter_plan_mode", "args": {}, "id": "call-1", "type": "tool_call"}
    )

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is True


async def test_save_plan_requires_run_context() -> None:
    _enter, save_plan, _approve = _tools(FakeStores())

    with pytest.raises(RuntimeError, match="outside of a runnable context"):
        await save_plan(_PLAN_PATH)


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
    _enter, save_plan, _approve = _tools(FakeStores())

    result = await save_plan(path)

    assert result["success"] is False
    assert expected in result["error"]


async def test_save_plan_publishes_the_sandbox_file(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend
) -> None:
    _configurable(monkeypatch, thread_id="thread-1")
    stores = FakeStores()
    _enter, save_plan, _approve = _tools(stores)

    result = await save_plan(_PLAN_PATH)

    assert result == {"success": True, "path": _PLAN_PATH}
    assert sandbox.reads == [(_PLAN_PATH, 0, 20_000)]
    assert stores.store is not None
    # Published outside plan mode: shared, not up for review.
    assert stores.store.calls == [("publish", _PLAN_HTML, _PLAN_PATH, False)]


async def test_save_plan_wraps_a_fragment_with_a_title_from_the_filename(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend
) -> None:
    _configurable(monkeypatch, thread_id="thread-1")
    sandbox.content = "<h1>Plan</h1><script>go()</script>"
    stores = FakeStores()
    _enter, save_plan, _approve = _tools(stores)

    result = await save_plan("/workspace/plans/2026-06-29-add-webhook-retries.html")

    assert result["success"] is True
    assert stores.store is not None
    document = stores.store.calls[0][1]
    assert document.startswith("<!doctype html>")
    assert "<title>Add webhook retries</title>" in document
    assert "<script>go()</script>" in document


@pytest.mark.parametrize("source", ["state", "config"])
async def test_save_plan_publishes_for_review_while_planning(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend, source: str
) -> None:
    _configurable(
        monkeypatch, thread_id="thread-1", **({"plan_mode": True} if source == "config" else {})
    )
    stores = FakeStores()
    _enter, save_plan, _approve = _tools(stores)

    result = await save_plan(_PLAN_PATH, state={"plan_mode": True} if source == "state" else None)

    assert result["success"] is True
    assert stores.store is not None
    assert stores.store.calls[0][3] is True


async def test_save_plan_reports_a_store_failure(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Backend
) -> None:
    _configurable(monkeypatch, thread_id="thread-1")
    _enter, save_plan, _approve = _tools(FakeStores(error=RuntimeError("store down")))

    result = await save_plan(_PLAN_PATH)

    assert result["success"] is False
    assert "failed to save plan" in result["error"]
    assert "store down" in result["error"]


async def test_approve_plan_exits_plan_mode_and_hands_over_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch, thread_id="t1", plan_mode=True)
    stores = FakeStores(
        approved=ApprovedPlan(
            document="<html><head><title>Plan</title></head><body>Do it</body></html>",
            reviewer_feedback="1. add tests",
        )
    )
    _enter, _save, approve_plan = _tools(stores)

    result = await approve_plan(state={"plan_mode": True}, tool_call_id="call-1")

    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["plan_mode"] is False
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert "<title>Plan</title>" in message.content
    assert "add tests" in message.content
    assert "reasonable engineering judgment" in message.content
    assert stores.store is not None
    assert stores.store.calls == ["approve"]


async def test_approve_plan_falls_back_to_the_store_for_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing in state or the configurable says whether planning is still on."""
    _configurable(monkeypatch, thread_id="t1")
    stores = FakeStores(active=True)
    _enter, _save, approve_plan = _tools(stores)

    result = await approve_plan(tool_call_id="call-1")

    assert isinstance(result, Command)
    assert stores.store is not None
    assert stores.store.calls == ["is_active", "approve"]


async def test_approve_plan_refuses_when_plan_mode_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch, thread_id="t1")
    stores = FakeStores(active=False)
    _enter, _save, approve_plan = _tools(stores)

    result = await approve_plan(tool_call_id="call-1")

    assert result == {"success": False, "error": "plan mode is not active for this thread"}
    assert stores.store is not None
    assert "approve" not in stores.store.calls


async def test_approve_plan_reports_an_unapprovable_plan_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch, thread_id="t1", plan_mode=True)
    _enter, _save, approve_plan = _tools(
        FakeStores(error=PlanNotApprovable("shared content is not an implementation plan"))
    )

    result = await approve_plan(state={"plan_mode": True}, tool_call_id="call-1")

    assert result == {
        "success": False,
        "error": "shared content is not an implementation plan",
    }


async def test_approve_plan_reports_a_store_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurable(monkeypatch, thread_id="t1", plan_mode=True)
    _enter, _save, approve_plan = _tools(FakeStores(error=RuntimeError("store down")))

    result = await approve_plan(state={"plan_mode": True}, tool_call_id="call-1")

    assert result["success"] is False
    assert "failed to approve plan" in result["error"]


async def test_approve_plan_without_a_thread_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurable(monkeypatch)
    stores = FakeStores()
    _enter, _save, approve_plan = _tools(stores)

    result = await approve_plan(tool_call_id="call-1")

    assert result == {"success": False, "error": "no thread_id in run config"}
    assert stores.threads == []
