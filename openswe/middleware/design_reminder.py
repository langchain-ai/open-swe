"""Remind agents about design guidance after observed frontend changes."""

import json
import logging
import shlex
from pathlib import Path

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)
_SNAPSHOT_SCRIPT = Path(__file__).with_name("design_snapshot.py").read_text()


class DesignReminderMiddleware(OpenSWEMiddleware):
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, str]] = {}

    async def _snapshot(self, thread_id: str) -> dict[str, str] | None:
        backend = SANDBOX_BACKENDS.get(thread_id)
        if backend is None:
            return None
        try:
            response = await backend.aexecute(
                "python3 -c " + shlex.quote(_SNAPSHOT_SCRIPT), timeout=10
            )
            if response.exit_code != 0:
                logger.debug("Design snapshot failed", extra={"thread_id": thread_id})
                return None
            data: object = json.loads(response.output)
            if isinstance(data, dict) and all(
                isinstance(key, str) and isinstance(value, str) for key, value in data.items()
            ):
                return {str(key): str(value) for key, value in data.items()}
            logger.debug("Invalid design snapshot", extra={"thread_id": thread_id})
        except Exception:
            logger.debug("Design snapshot unavailable", exc_info=True)
        return None

    async def abefore_agent(
        self, state: AgentState, runtime: Runtime, config: RunnableConfig
    ) -> None:
        thread_id = RunConfig.from_config(config).thread_id
        if thread_id:
            snapshot = await self._snapshot(thread_id)
            if snapshot is not None:
                self._snapshots[thread_id] = snapshot

    async def abefore_model(
        self, state: AgentState, runtime: Runtime, config: RunnableConfig
    ) -> dict[str, list[SystemMessage]] | None:
        thread_id = RunConfig.from_config(config).thread_id
        if not thread_id:
            return None
        snapshot = await self._snapshot(thread_id)
        if snapshot is None:
            return None
        previous = self._snapshots.get(thread_id)
        self._snapshots[thread_id] = snapshot
        if previous is None:
            return None
        changed = [path for path, digest in snapshot.items() if previous.get(path) != digest]
        if not changed:
            return None
        return {"messages": [SystemMessage(content=prompt("middleware/design-reminder"))]}
