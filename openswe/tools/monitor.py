"""Sandbox commands whose filtered output reaches the thread as events."""

import logging
import uuid
from collections.abc import Sequence
from typing import Literal

from pydantic import JsonValue

from openswe.monitors.budget import ThreadMonitorBudget
from openswe.monitors.delivery import ThreadMonitorDelivery, ensure_monitor_polling
from openswe.monitors.sandbox import ID_PREFIX, MonitorError, SandboxMonitors
from openswe.run_config import RunConfig
from openswe.tools.background_execute import current_backend, uses_completion_callback
from openswe.utils.background_task_state import RUNNING_MONITORS_KEY, update_background_task_state
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

type MonitorAction = Literal["start", "output", "stop", "list"]

MAX_TIMEOUT_SECONDS = 86_400
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 3600
_MAX_DESCRIPTION_CHARS = 200


def _error(message: str) -> dict[str, JsonValue]:
    return {"success": False, "error": message}


async def _track(
    thread_id: str, *, running: Sequence[str] = (), finished: Sequence[str] = ()
) -> bool:
    """Note running monitors on the thread, which is what lets the dashboard list them."""
    try:
        await update_background_task_state(
            langgraph_client(),
            thread_id,
            running=running,
            finished=finished,
            key=RUNNING_MONITORS_KEY,
        )
    except Exception:
        logger.warning(
            "Could not track monitors", extra={"agent_thread_id": thread_id}, exc_info=True
        )
        return False
    return True


async def monitor(
    action: MonitorAction,
    command: str = "",
    filter: str = "",
    description: str = "",
    monitor_id: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    interval: int | None = None,
    *,
    timeout: int | None = None,
) -> dict[str, JsonValue]:
    """Implement the `monitor` tool."""
    if action == "start":
        if not command.strip():
            return _error("command must not be empty")
        if not description.strip():
            return _error("description must name what is being monitored")
        if len(description) > _MAX_DESCRIPTION_CHARS:
            return _error(f"description must be at most {_MAX_DESCRIPTION_CHARS} characters")
        if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            return _error(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}s")
        if interval is not None and not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
            return _error(
                f"interval must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS}s"
            )
    elif action in {"output", "stop"} and not monitor_id:
        return _error(f"monitor_id is required for {action}")
    try:
        thread_id, backend = current_backend()
        monitors = SandboxMonitors(backend, thread_id)
        if action == "list":
            return {"success": True, "monitors": list(await monitors.listing())}
        if action == "output":
            result = await monitors.output(monitor_id, start_line=start_line, end_line=end_line)
            return {"success": True, **result}
        if action == "stop":
            stopped = await monitors.stop(monitor_id)
            if stopped.get("status") != "running":
                await _track(thread_id, finished=[monitor_id])
            return {"success": True, **stopped}
        if timeout is None:
            return _error("timeout is required for start")
        window = await ThreadMonitorBudget(langgraph_client(), thread_id).start()
        new_id = f"{ID_PREFIX}-{uuid.uuid4()}"
        callback = await uses_completion_callback()
        origin = RunConfig.from_runtime().origin
        async with ThreadMonitorDelivery.lock(thread_id):
            if not await _track(thread_id, running=[new_id]):
                return _error("the monitor could not be tracked, so it was not started; try again")
            try:
                state = await monitors.start(
                    new_id,
                    command=command,
                    filter=filter.strip(),
                    description=description.strip(),
                    timeout=min(timeout, window),
                    callback=callback,
                    interval=interval,
                    origin=origin,
                )
            except BaseException:
                await _track(thread_id, finished=[new_id])
                raise
        started = state.model_dump(mode="json")
        if window < timeout:
            started["note"] = (
                f"timeout clipped to {window}s: monitors may run for 24 hours after a human message"
            )
        if not state.callback:
            try:
                await ensure_monitor_polling(thread_id)
            except Exception:
                logger.exception("Failed to schedule monitor polling")
                return {
                    "success": False,
                    **started,
                    "error": "monitor started, but its events could not be scheduled for "
                    "delivery; stop it and start it again",
                }
        return {"success": True, **started}
    except MonitorError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception("Monitor action failed", extra={"monitor_action": action})
        return _error(str(exc))
