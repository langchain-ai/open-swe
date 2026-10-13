"""Remind agents about design guidance after observed frontend changes."""

import json
import logging
import shlex
from pathlib import Path
from uuid import uuid4

from langchain.agents.middleware.types import AgentState
from langgraph.runtime import Runtime

from openswe.input_messages import RunMessage, dynamic_context_hash, visible_dynamic_context_hashes
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)
_SNAPSHOT_SCRIPT = Path(__file__).with_name("design_snapshot.py").read_text()


class DesignReminderMiddleware(OpenSWEMiddleware):
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, str]] = {}
        self._inactive: set[str] = set()
        self._reminded: set[str] = set()

    async def _snapshot(self, thread_id: str) -> dict[str, str] | None:
        backend = SANDBOX_BACKENDS.get(thread_id)
        if backend is None:
            return None
        snapshot_path = f"/tmp/open-swe-design-{uuid4().hex}.json"
        try:
            work_dir = await resolve_sandbox_work_dir(backend)
            [upload] = await backend.aupload_files(
                [(snapshot_path, json.dumps(self._snapshots.get(thread_id, {})).encode())]
            )
            if upload.error:
                raise RuntimeError(f"Could not upload design snapshot: {upload.error}")
            response = await backend.aexecute(
                f"cd {shlex.quote(work_dir)} && python3 -c "
                + shlex.quote(_SNAPSHOT_SCRIPT)
                + " "
                + shlex.quote(snapshot_path),
                timeout=10,
            )
            if response.output:
                logger.warning(
                    "Design snapshot diagnostics",
                    extra={"thread_id": thread_id, "diagnostics": response.output},
                )
            if response.exit_code != 0:
                logger.warning("Design snapshot failed", extra={"thread_id": thread_id})
                return None
            [download] = await backend.adownload_files([snapshot_path])
            if download.content is None:
                raise RuntimeError(f"Could not download design snapshot: {download.error}")
            data: object = json.loads(download.content)
            if isinstance(data, dict) and all(
                isinstance(key, str) and isinstance(value, str) for key, value in data.items()
            ):
                return {str(key): str(value) for key, value in data.items()}
            logger.warning("Invalid design snapshot", extra={"thread_id": thread_id})
        except Exception:
            logger.warning("Design snapshot unavailable", exc_info=True)
        finally:
            try:
                cleanup = await backend.aexecute(f"rm -f {shlex.quote(snapshot_path)}", timeout=10)
                if cleanup.exit_code != 0:
                    logger.warning("Design snapshot cleanup failed", extra={"thread_id": thread_id})
            except Exception:
                logger.warning("Design snapshot cleanup unavailable", exc_info=True)
        return None

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        thread_id = RunConfig.from_runtime().thread_id
        if thread_id:
            self._inactive.discard(thread_id)
            self._reminded.discard(thread_id)
            self._snapshots.pop(thread_id, None)
            snapshot = await self._snapshot(thread_id)
            if snapshot is not None:
                self._snapshots[thread_id] = snapshot
                if not snapshot:
                    self._inactive.add(thread_id)

    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, list[RunMessage]] | None:
        thread_id = RunConfig.from_runtime().thread_id
        if not thread_id or thread_id in self._inactive or thread_id in self._reminded:
            return None
        snapshot = await self._snapshot(thread_id)
        if snapshot is None:
            return None
        previous = self._snapshots.get(thread_id)
        self._snapshots[thread_id] = snapshot
        if previous is None:
            return None
        roots = [
            Path(path).parent
            for path in snapshot
            if Path(path).name == ".git"
            and previous.get(path) == snapshot[path]
            and path in previous
        ]
        changed = [
            path
            for path, fingerprint in snapshot.items()
            if Path(path).name != ".git"
            and any(Path(path).is_relative_to(root) for root in roots)
            and previous.get(path, "").rsplit(":", 1)[-1] != fingerprint.rsplit(":", 1)[-1]
        ]
        if not changed:
            return None
        self._reminded.add(thread_id)
        content = prompt("middleware/design-reminder")
        if dynamic_context_hash(content) in visible_dynamic_context_hashes(state):
            return None
        return {
            "messages": [{"role": "user", "id": f"design-reminder:{uuid4()}", "content": content}]
        }
