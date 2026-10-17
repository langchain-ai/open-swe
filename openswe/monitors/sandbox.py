"""One thread's monitors, as its sandbox holds them."""

import base64
import hashlib
import json
import shlex
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, JsonValue

from openswe.sandboxes.tool_access import TOOLS_URL_ENV, TOOLS_URL_FILE

if TYPE_CHECKING:
    # The dashboard reads monitors too, and must not load the agent stack.
    from deepagents.backends.protocol import SandboxBackendProtocol

ROOT = "/tmp/open-swe-monitors"
ID_PREFIX = "mon"
_MISSING_SCRIPT = "open-swe-monitor-script-missing"

type MonitorStatus = Literal[
    "running", "completed", "failed", "timed_out", "stopped", "filter_exited", "too_noisy", "lost"
]


class MonitorError(RuntimeError):
    """The sandbox refused a monitor action, with a reason fit for the agent."""


class MonitorState(BaseModel):
    monitor_id: str
    description: str = ""
    command: str = ""
    filter: str = ""
    status: MonitorStatus
    stop_reason: str | None = None
    exit_code: int | None = None
    filter_exit_code: int | None = None
    timeout: int | None = None
    started_at: float | None = None
    raw_lines: int = 0
    first_raw_line: int = 1
    events: int = 0
    dropped_filter_bytes: int = 0
    callback: bool = False
    raw_log: str = ""


class MonitorEvent(BaseModel):
    seq: int
    at_raw_line: int
    lines: list[str]


class MonitorEnd(BaseModel):
    tail: list[tuple[int, str]]
    filter_errors: str = ""


class PendingMonitor(MonitorState):
    """A monitor with the events and end notice the backend has not recorded yet."""

    events_pending: list[MonitorEvent] = []
    behind: bool = False
    """More events wait past ``events_pending``; read again after acking these."""
    end: MonitorEnd | None = None


class _Pending(BaseModel):
    monitors: list[PendingMonitor]


@cache
def _script() -> tuple[str, str]:
    """The script's file name, carrying its hash, and its source."""
    source = resources.files("openswe.resources").joinpath("sandbox_monitor.py").read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()[:16]
    return f"sandbox_monitor-{digest}.py", source


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class SandboxMonitors:
    """Starts, reads and stops the monitors ``thread_id`` owns in ``backend``."""

    def __init__(self, backend: SandboxBackendProtocol, thread_id: str) -> None:
        self._backend = backend
        self._thread_id = thread_id

    async def start(
        self,
        monitor_id: str,
        *,
        command: str,
        filter: str,
        description: str,
        timeout: int,
        callback: bool,
    ) -> MonitorState:
        result = await self._call(
            "start",
            monitor_id=monitor_id,
            command=command,
            filter=filter,
            description=description,
            timeout=timeout,
            callback=callback,
            tools_url_env=TOOLS_URL_ENV,
            tools_url_file=TOOLS_URL_FILE,
        )
        return MonitorState.model_validate(result)

    async def listing(self) -> list[dict[str, JsonValue]]:
        result = await self._call("list")
        monitors = result.get("monitors")
        return (
            [item for item in monitors if isinstance(item, dict)]
            if isinstance(monitors, list)
            else []
        )

    async def output(
        self, monitor_id: str, *, start_line: int | None, end_line: int | None
    ) -> dict[str, JsonValue]:
        return await self._call(
            "output", monitor_id=monitor_id, start_line=start_line, end_line=end_line
        )

    async def stop(self, monitor_id: str, *, reason: str = "") -> dict[str, JsonValue]:
        return await self._call("stop", monitor_id=monitor_id, reason=reason)

    async def pending(self) -> list[PendingMonitor]:
        return _Pending.model_validate(await self._call("sync")).monitors

    async def ack(self, monitor_id: str, *, seq: int, end: bool) -> None:
        await self._call("ack", monitor_id=monitor_id, seq=seq, end=end)

    async def _call(self, action: str, **fields: JsonValue) -> dict[str, JsonValue]:
        return await _run(self._backend, action, owner_thread_id=self._thread_id, **fields)


async def owner_of(backend: SandboxBackendProtocol, monitor_id: str) -> str:
    """The thread that started ``monitor_id`` in ``backend``; raises ``LookupError`` if none."""
    try:
        result = await _run(backend, "owner", monitor_id=monitor_id)
    except MonitorError as exc:
        raise LookupError(str(exc)) from exc
    owner = result.get("owner_thread_id")
    if not isinstance(owner, str) or not owner:
        raise LookupError("monitor has no owner")
    return owner


async def _run(
    backend: SandboxBackendProtocol, action: str, **fields: JsonValue
) -> dict[str, JsonValue]:
    request = _encoded(json.dumps({"action": action, **fields}))
    name, source = _script()
    script = shlex.quote(f"{ROOT}/{name}")
    run = (
        "command -v python3 >/dev/null || "
        '{ echo \'{"error": "monitors require python3 in the sandbox"}\'; exit 0; }; '
        f"python3 {script} {shlex.quote(request)}"
    )
    response = await backend.aexecute(
        f"[ -f {script} ] || {{ echo {_MISSING_SCRIPT}; exit 0; }}; {run}", timeout=30
    )
    if response.output.strip() == _MISSING_SCRIPT:
        # Named by its hash, so a sandbox always runs the script this backend ships.
        response = await backend.aexecute(
            f"mkdir -p {shlex.quote(ROOT)} && "
            f"printf %s {shlex.quote(_encoded(source))} | base64 -d > {script}.$$ && "
            f"mv {script}.$$ {script}; {run}",
            timeout=30,
        )
    lines = response.output.strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict):
        raise RuntimeError(
            response.output.strip()
            or f"monitor {action} failed with exit code {response.exit_code}"
        )
    error = result.get("error")
    if isinstance(error, str):
        raise MonitorError(error)
    return result
