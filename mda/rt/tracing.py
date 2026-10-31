"""Reconcile the live turn tree, including calls already sent to LangSmith."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from langchain.agents.middleware import TracePolicy, omit_payload
from langchain_core.callbacks import AsyncCallbackManager, CallbackManager
from langchain_core.runnables.config import ensure_config
from langchain_core.tracers.langchain import LangChainTracer
from langsmith.run_trees import RunTree
from pydantic import JsonValue

logger = logging.getLogger(__name__)

# Baggage that describes the span itself rather than where its children belong.
_SPAN_ONLY_BAGGAGE: Final = ("langsmith-metadata=", "langsmith-tags=")
# Turns whose trace is still being reconciled after their run ended.
_ENDING: Final[set[asyncio.Task[None]]] = set()


def _ended(task: asyncio.Task[None]) -> None:
    _ENDING.discard(task)
    if task.cancelled():
        logger.warning("A turn's trace was not reconciled before the runtime stopped")


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
        self._ending: asyncio.Task[None] | None = None

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

    def end(
        self,
        metadata: Mapping[str, JsonValue],
        evidence: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> None:
        """Reconcile the turn's trace with ``metadata`` and what ``evidence`` finds, in the
        background: the run ends, and its thread takes the next message, without waiting."""
        if self.root is None:
            return
        self._ending = asyncio.ensure_future(self._end(self.root, metadata, evidence))
        _ENDING.add(self._ending)
        self._ending.add_done_callback(_ended)

    async def ended(self) -> None:
        """Wait for the turn's trace to be reconciled."""
        if self._ending is not None:
            await asyncio.shield(self._ending)

    @staticmethod
    async def _end(
        root: RunTree,
        metadata: Mapping[str, JsonValue],
        evidence: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> None:
        try:
            found = await evidence()
        except Exception:
            logger.warning("Could not gather the turn's trace metadata", exc_info=True)
            found = {}
        await TurnTrace._reconcile(root, {**metadata, **found})

    @staticmethod
    async def _reconcile(root: RunTree, metadata: Mapping[str, JsonValue]) -> None:
        try:
            pending = [root]
            runs: list[RunTree] = []
            while pending:
                run = pending.pop()
                if run.trace_id != root.trace_id or run.metadata.get(
                    "invocation_id"
                ) != root.metadata.get("invocation_id"):
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
