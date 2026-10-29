"""Reconcile the live turn tree, including calls already sent to LangSmith."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Final

from langchain.agents.middleware import TracePolicy, omit_payload
from langchain_core.callbacks import AsyncCallbackManager, CallbackManager
from langchain_core.runnables.config import ensure_config
from langchain_core.tracers.langchain import LangChainTracer
from langsmith.run_trees import RunTree
from pydantic import JsonValue, TypeAdapter

logger = logging.getLogger(__name__)

# Baggage that describes the span itself rather than where its children belong.
_SPAN_ONLY_BAGGAGE: Final = ("langsmith-metadata=", "langsmith-tags=")


MIDDLEWARE_TRACE_POLICY = TracePolicy(process_inputs=omit_payload)

_ENVIRONMENTS: Final = {
    "open-swe-mda-prod": "production",
    "open-swe-mda-staging": "staging",
    "open-swe-mda-preview": "preview",
}


def _deployment_provenance() -> dict[str, JsonValue]:
    """The MDA code and deployment serving this turn.

    Set on the root explicitly, since LangSmith only fills ``revision_id`` when the run's
    metadata lacks one, and a queued run's metadata names the deployment that queued it.
    """
    provenance: dict[str, JsonValue] = {}
    if revision := os.environ.get("LANGSMITH_REVISION_ID"):
        provenance["revision_id"] = revision
    if environment := _ENVIRONMENTS.get(os.environ.get("MDA_DEPLOYMENT_NAME", "")):
        provenance["environment"] = environment
    return provenance


class TurnTrace:
    """The actual runtime's turn trace, never a backend hook or a previous turn."""

    def __init__(self) -> None:
        self.root: RunTree | None = None

    @staticmethod
    def current_root() -> RunTree | None:
        """Find the live tree within this invocation, not its external tracing parent."""
        config = ensure_config()
        invocation_id = config.get("metadata", {}).get("invocation_id")
        callbacks = config.get("callbacks")
        if not isinstance(invocation_id, str) or not isinstance(
            callbacks, (AsyncCallbackManager, CallbackManager)
        ):
            return None
        for tracer in callbacks.handlers:
            if not isinstance(tracer, LangChainTracer):
                continue
            current = tracer.run_map.get(str(callbacks.parent_run_id))
            if current is None or current.metadata.get("invocation_id") != invocation_id:
                continue
            while current.parent_run_id is not None:
                parent = tracer.run_map.get(str(current.parent_run_id))
                if (
                    parent is None
                    or parent.trace_id != current.trace_id
                    or parent.metadata.get("invocation_id") != invocation_id
                ):
                    break
                current = parent
            return current
        return None

    def start(self, metadata: Mapping[str, JsonValue]) -> None:
        self.root = None
        try:
            self.root = self.current_root()
            if self.root is not None:
                self.root.metadata.update({**metadata, **_deployment_provenance()})
                self.root.metadata["open_swe_git_evidence"] = {}
        except Exception:
            logger.warning("Could not tag the turn trace", exc_info=True)

    def record_model(self, model_id: str) -> None:
        """Tag the turn with its most recently attempted model, without relabeling child calls."""
        if self.root is not None:
            self.root.metadata["model"] = model_id

    @staticmethod
    def current_headers() -> dict[str, str]:
        """Distributed-tracing headers for the running call's span, so a backend hook nests under it.

        Only the trace position and destination travel: the span's metadata and tags
        stay out, since the backend sets its own and they can outgrow a header.
        """
        try:
            config = ensure_config()
            callbacks = config.get("callbacks")
            if not isinstance(callbacks, (AsyncCallbackManager, CallbackManager)):
                return {}
            for tracer in callbacks.handlers:
                if not isinstance(tracer, LangChainTracer):
                    continue
                span = tracer.run_map.get(str(callbacks.parent_run_id))
                if (
                    span is None
                    or span.end_time is not None
                    or span.extra.get("__disabled")
                    or not span.trace_id
                ):
                    return {}
                headers = span.to_headers()
                headers["baggage"] = ",".join(
                    item
                    for item in headers.get("baggage", "").split(",")
                    if item and not item.startswith(_SPAN_ONLY_BAGGAGE)
                )
                return headers
        except Exception:
            logger.warning("Could not read the running span's trace headers", exc_info=True)
        return {}

    @staticmethod
    def live_root() -> RunTree | None:
        """The turn's tree as seen from the running call, while that call's run is open."""
        try:
            return TurnTrace.current_root()
        except Exception:
            logger.warning("Could not find the turn trace", exc_info=True)
            return None

    @staticmethod
    def record_evidence(update: Mapping[str, JsonValue], root: RunTree | None = None) -> None:
        """Hold ``update``'s git evidence on ``root``, by default the running call's turn."""
        evidence = {
            key: value
            for key, value in update.items()
            if key == "git_trace_baseline" or key.startswith(("git_trace_pr:", "git_trace_work:"))
        }
        try:
            if evidence and (root := root or TurnTrace.current_root()) is not None:
                held = root.metadata.setdefault("open_swe_git_evidence", {})
                if isinstance(held, dict):
                    if "git_trace_baseline" in evidence and evidence["git_trace_baseline"] is None:
                        held.clear()
                    held.update(evidence)
        except Exception:
            logger.warning("Could not record turn git evidence", exc_info=True)

    @staticmethod
    def _evidence(root: RunTree | None) -> dict[str, JsonValue]:
        if root is None:
            return {}
        return TypeAdapter(dict[str, JsonValue]).validate_python(
            root.metadata.get("open_swe_git_evidence", {})
        )

    @staticmethod
    def current_evidence() -> dict[str, JsonValue]:
        """Share the parent's observation budget with subagents that have their own state."""
        try:
            return TurnTrace._evidence(TurnTrace.current_root())
        except Exception:
            logger.warning("Could not read current turn git evidence", exc_info=True)
            return {}

    @property
    def evidence(self) -> dict[str, JsonValue]:
        try:
            return self._evidence(self.root)
        except Exception:
            logger.warning("Ignoring invalid turn git evidence", exc_info=True)
            return {}

    async def reconcile(self, metadata: Mapping[str, JsonValue]) -> None:
        if self.root is None:
            return
        try:
            pending = [self.root]
            runs: list[RunTree] = []
            while pending:
                run = pending.pop()
                if run.trace_id != self.root.trace_id or run.metadata.get(
                    "invocation_id"
                ) != self.root.metadata.get("invocation_id"):
                    continue
                run.metadata.update(metadata)
                runs.append(run)
                pending.extend(run.child_runs)

            def patch() -> None:
                for run in runs:
                    if run.end_time is not None and not run.extra.get("__disabled"):
                        run.patch(exclude_inputs=True)

            await asyncio.to_thread(patch)
        except Exception:
            logger.warning("Could not reconcile turn trace metadata", exc_info=True)
