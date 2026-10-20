"""Reconcile the live turn tree, including calls already sent to LangSmith."""

import asyncio
import logging
from collections.abc import Mapping

from langchain_core.callbacks import AsyncCallbackManager, CallbackManager
from langchain_core.runnables.config import ensure_config
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import get_current_run_tree
from langsmith.run_trees import RunTree
from pydantic import JsonValue, TypeAdapter

logger = logging.getLogger(__name__)


class TurnTrace:
    """The actual runtime's turn trace, never a backend hook or a previous turn."""

    def __init__(self) -> None:
        self.root: RunTree | None = None

    @staticmethod
    def current_root() -> RunTree | None:
        current = get_current_run_tree()
        callbacks = ensure_config().get("callbacks")
        if isinstance(callbacks, (AsyncCallbackManager, CallbackManager)):
            for tracer in callbacks.handlers:
                if not isinstance(tracer, LangChainTracer):
                    continue
                run = tracer.run_map.get(str(callbacks.parent_run_id))
                if run is not None:
                    current = run
                    while current.parent_run_id is not None:
                        parent = tracer.run_map.get(str(current.parent_run_id))
                        if parent is None or parent.trace_id != current.trace_id:
                            break
                        current = parent
                    break
        if current is None:
            return None
        while current.parent_run is not None and current.parent_run.trace_id == current.trace_id:
            current = current.parent_run
        return current

    def start(self, metadata: Mapping[str, JsonValue]) -> None:
        self.root = self.current_root()
        if self.root is not None:
            self.root.metadata.update(metadata)

    @staticmethod
    def record_evidence(update: Mapping[str, JsonValue]) -> None:
        evidence = {
            key: value
            for key, value in update.items()
            if key.startswith(("git_trace_pr:", "git_trace_work:"))
        }
        if evidence and (root := TurnTrace.current_root()) is not None:
            held = root.metadata.setdefault("open_swe_git_evidence", {})
            if isinstance(held, dict):
                held.update(evidence)

    @property
    def evidence(self) -> dict[str, JsonValue]:
        if self.root is None:
            return {}
        return TypeAdapter(dict[str, JsonValue]).validate_python(
            self.root.metadata.get("open_swe_git_evidence", {})
        )

    async def reconcile(self, metadata: Mapping[str, JsonValue]) -> None:
        if self.root is None:
            return
        pending = [self.root]
        runs: list[RunTree] = []
        while pending:
            run = pending.pop()
            if run.trace_id != self.root.trace_id:
                continue
            run.metadata.update(metadata)
            runs.append(run)
            pending.extend(run.child_runs)

        def patch() -> None:
            for run in runs:
                if not run.extra.get("__disabled"):
                    run.client.update_run(
                        run.id,
                        trace_id=run.trace_id,
                        dotted_order=run.dotted_order,
                        extra=run.extra.copy(),
                    )

        try:
            await asyncio.to_thread(patch)
        except Exception:
            logger.warning("Could not reconcile turn trace metadata", exc_info=True)
