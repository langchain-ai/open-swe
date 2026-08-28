"""Non-blocking command execution in the thread sandbox."""

import base64
import json
import logging
import shlex
import textwrap
import uuid
from typing import Any, Literal

from langgraph.config import get_config

from agent.run_config import RunConfig

from ..utils.sandbox_state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)

TASK_ROOT = "/tmp/open-swe-background-tasks"
LAUNCH_LOCK = f"{TASK_ROOT}/.launch-lock"
DEFAULT_TIMEOUT_SECONDS = 3600
MAX_TIMEOUT_SECONDS = 86_400
MAX_ACTIVE_TASKS = 4
MAX_OUTPUT_BYTES = 1_048_576
MAX_INLINE_OUTPUT_BYTES = 65_536
TASK_TTL_SECONDS = 604_800


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _runner(task_id: str, command: str, timeout: int) -> str:
    return textwrap.dedent(
        f"""
        import base64, json, os, selectors, signal, subprocess, time

        root = {TASK_ROOT!r}
        task_id = {task_id!r}
        task_dir = os.path.join(root, task_id)
        state_path = os.path.join(task_dir, "state.json")
        output_path = os.path.join(task_dir, "output.log")
        stop_path = os.path.join(task_dir, "stop")
        command = base64.b64decode({_encoded(command)!r}).decode()
        try:
            os.remove(__file__)
        except OSError:
            pass
        limit = {MAX_OUTPUT_BYTES}
        timeout = {timeout}
        started_at = time.time()
        started = time.monotonic()
        head = bytearray()
        tail = bytearray()
        omitted = 0

        def write_state(status, pid=None, exit_code=None):
            payload = {{
                "task_id": task_id,
                "status": status,
                "pid": pid,
                "runner_pid": os.getpid(),
                "exit_code": exit_code,
                "started_at": started_at,
                "finished_at": time.time() if status != "running" else None,
                "output_path": output_path,
            }}
            tmp = state_path + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(payload, handle)
            os.replace(tmp, state_path)

        def capture(chunk):
            global omitted
            half = limit // 2
            if len(head) < half:
                used = min(half - len(head), len(chunk))
                head.extend(chunk[:used])
                chunk = chunk[used:]
            if chunk:
                tail.extend(chunk)
                if len(tail) > half:
                    dropped = len(tail) - half
                    del tail[:dropped]
                    omitted += dropped
        def flush():
            with open(output_path + ".tmp", "wb") as handle:
                handle.write(head)
                if omitted:
                    handle.write(f"\\n[{{omitted}} bytes omitted]\\n".encode())
                handle.write(tail)
            os.replace(output_path + ".tmp", output_path)

        process = subprocess.Popen(
            ["/bin/sh", "-c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        write_state("running", process.pid)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        status = None
        last_flush = 0.0
        while status is None:
            for key, _ in selector.select(0.25):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    capture(chunk)
                else:
                    selector.unregister(key.fileobj)
            if time.time() - last_flush >= 1:
                flush()
                last_flush = time.time()
            if os.path.exists(stop_path):
                status = "stopped"
            elif time.monotonic() - started >= timeout:
                status = "timed_out"
            elif process.poll() is not None:
                status = "completed" if process.returncode == 0 else "failed"
            if status in {{"stopped", "timed_out"}}:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(2)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
            try:
                while chunk := os.read(process.stdout.fileno(), 65536):
                    capture(chunk)
            except BlockingIOError:
                pass
        exit_code = process.wait()
        flush()
        write_state(status, process.pid, exit_code)
        """
    ).strip()


def _launch_command(task_id: str, command: str, timeout: int) -> str:
    task_dir = f"{TASK_ROOT}/{task_id}"
    runner = _encoded(_runner(task_id, command, timeout))
    lock = shlex.quote(LAUNCH_LOCK)
    return (
        "command -v setsid >/dev/null || { echo 'background execution requires setsid' >&2; exit 69; }; "
        f"mkdir -p {shlex.quote(TASK_ROOT)}; "
        f"acquired=; for _ in 1 2 3 4 5 6 7 8 9 10; do mkdir {lock} 2>/dev/null && acquired=1 && break; sleep .1; done; "
        "[ \"$acquired\" ] || { echo 'background launch is busy' >&2; exit 71; }; "
        f"trap 'rmdir {lock}' 0; active=0; "
        f'for state in {shlex.quote(TASK_ROOT)}/*/state.json; do [ -f "$state" ] || continue; grep -q \'"status": "running"\' "$state" && active=$((active + 1)); done; '
        f"[ \"$active\" -lt {MAX_ACTIVE_TASKS} ] || {{ echo 'active task limit reached' >&2; exit 72; }}; "
        f"mkdir {shlex.quote(task_dir)} || exit 73; "
        f"printf %s {shlex.quote(runner)} | base64 -d > {shlex.quote(task_dir + '/runner.py')}; "
        f"setsid python3 {shlex.quote(task_dir + '/runner.py')} </dev/null >/dev/null 2>&1 & "
        f"for _ in 1 2 3 4 5 6 7 8 9 10; do [ -f {shlex.quote(task_dir + '/state.json')} ] && break; sleep .1; done; "
        f"[ -f {shlex.quote(task_dir + '/state.json')} ] || {{ echo 'background runner did not start' >&2; exit 70; }}; "
        f"rmdir {lock}; trap - 0; cat {shlex.quote(task_dir + '/state.json')}"
    )


def _control_script(action: str, task_id: str | None) -> str:
    return textwrap.dedent(
        f"""
        import json, os, shutil, signal, sys, time

        root = {TASK_ROOT!r}
        action = {action!r}
        task_id = {task_id!r}

        def load(path):
            try:
                with open(path) as handle:
                    state = json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError):
                return None
            if state.get("status") == "running":
                try:
                    os.kill(state.get("runner_pid"), 0)
                except (ProcessLookupError, PermissionError, TypeError):
                    if time.time() - os.path.getmtime(path) >= 5:
                        try:
                            os.killpg(state.get("pid"), signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, TypeError):
                            pass
                        state["status"] = "lost"
                        state["finished_at"] = time.time()
                        tmp = path + ".tmp"
                        with open(tmp, "w") as handle:
                            json.dump(state, handle)
                        os.replace(tmp, path)
            state["duration_seconds"] = round(
                (state.get("finished_at") or time.time()) - state.get("started_at", time.time()), 2
            )
            return state

        def output(state):
            try:
                with open(state["output_path"], "rb") as handle:
                    data = handle.read()
                if len(data) > {MAX_INLINE_OUTPUT_BYTES}:
                    half = {MAX_INLINE_OUTPUT_BYTES} // 2
                    data = data[:half] + f"\\n[{{len(data) - half * 2}} inline bytes omitted]\\n".encode() + data[-half:]
                state["output"] = data.decode(errors="replace")
            except (FileNotFoundError, KeyError):
                state["output"] = ""
            return state

        if action == "list":
            states = []
            if os.path.isdir(root):
                for lock, stale_after in ((".launch-lock", 30), ("monitor.lock", 300)):
                    path = os.path.join(root, lock)
                    if os.path.isdir(path) and time.time() - os.path.getmtime(path) > stale_after:
                        shutil.rmtree(path, ignore_errors=True)
                for name in sorted(os.listdir(root)):
                    task_dir = os.path.join(root, name)
                    state = load(os.path.join(task_dir, "state.json"))
                    if state:
                        if state.get("status") != "running" and time.time() - state.get("finished_at", time.time()) > {TASK_TTL_SECONDS}:
                            shutil.rmtree(task_dir, ignore_errors=True)
                            continue
                        claim = os.path.join(task_dir, "notify.claim")
                        done = os.path.join(task_dir, "notify.done")
                        if os.path.isdir(claim) and time.time() - os.path.getmtime(claim) > 300:
                            shutil.rmtree(claim, ignore_errors=True)
                        state["notification"] = "done" if os.path.isdir(done) else "claimed" if os.path.isdir(claim) else "pending"
                        state.pop("pid", None)
                        state.pop("runner_pid", None)
                        states.append(state)
            print(json.dumps({{"tasks": states}}))
            sys.exit()

        if not task_id or not task_id.replace("-", "").isalnum():
            print(json.dumps({{"error": "invalid task_id"}}))
            sys.exit(2)
        task_dir = os.path.join(root, task_id)
        state_path = os.path.join(task_dir, "state.json")
        state = load(state_path)
        if not state:
            print(json.dumps({{"error": "task not found"}}))
            sys.exit(3)
        if action == "stop" and state.get("status") == "running":
            open(os.path.join(task_dir, "stop"), "a").close()
            try:
                os.killpg(state["pid"], signal.SIGTERM)
            except (ProcessLookupError, PermissionError, KeyError, TypeError):
                pass
            deadline = time.time() + 3
            while state.get("status") == "running" and time.time() < deadline:
                time.sleep(.1)
                state = load(state_path) or state
            if state.get("status") == "running":
                state["status"] = "stop_requested"
        state.pop("pid", None)
        state.pop("runner_pid", None)
        print(json.dumps(output(state)))
        """
    ).strip()


async def _execute(backend: Any, command: str, *, timeout: int = 15) -> Any:
    response = await backend.aexecute(command, timeout=timeout)
    output = getattr(response, "output", "")
    exit_code = getattr(response, "exit_code", None)
    if exit_code not in (0, None):
        raise RuntimeError(output.strip() or f"sandbox command failed with exit code {exit_code}")
    return json.loads(output.strip().splitlines()[-1])


def _current_backend() -> tuple[str, Any]:
    config = get_config()
    thread_id = RunConfig.from_config(config).thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise RuntimeError("No thread_id in current run config")
    backend = SANDBOX_BACKENDS.get(thread_id)
    if backend is None:
        raise RuntimeError("No sandbox is bound to this thread")
    return thread_id, backend


async def background_execute(
    command: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Start a long-running, non-interactive sandbox command and return immediately.

    Use this for tests, builds, and waits while useful foreground work remains. Do not use it
    for commands that edit files concurrently with the agent, installs, commits, or pushes.
    Completion is delivered automatically; do not poll. Output is capped and saved in the sandbox.
    """
    if not command.strip():
        return {"success": False, "error": "command must not be empty"}
    if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        return {"success": False, "error": f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}s"}
    try:
        thread_id, backend = _current_backend()
        script = _control_script("list", None)
        current = await _execute(
            backend, f"printf %s {shlex.quote(_encoded(script))} | base64 -d | python3"
        )
        active = sum(task.get("status") == "running" for task in current.get("tasks", []))
        if active >= MAX_ACTIVE_TASKS:
            return {"success": False, "error": "active task limit reached"}
        from ..background_tasks import MONITOR_LOCK, ensure_background_task_cron

        wait_for_monitor = f"while [ -d {shlex.quote(MONITOR_LOCK)} ]; do sleep .1; done"
        wait = await backend.aexecute(wait_for_monitor, timeout=15)
        if getattr(wait, "exit_code", None) != 0:
            raise RuntimeError("background-task monitor is busy")
        task_id = str(uuid.uuid4())
        state = await _execute(backend, _launch_command(task_id, command, timeout))
        wait = await backend.aexecute(wait_for_monitor, timeout=15)
        if getattr(wait, "exit_code", None) != 0:
            return {
                "success": False,
                **state,
                "error": "command started, but automatic completion monitoring is busy",
            }
        try:
            await ensure_background_task_cron(thread_id)
        except Exception:
            logger.warning("Failed to schedule background-task monitor", exc_info=True)
            return {
                "success": False,
                **state,
                "error": "command started, but automatic completion monitoring could not be scheduled",
            }
        return {"success": True, **state}
    except Exception as exc:
        logger.exception("Failed to start background command")
        return {"success": False, "error": str(exc)}


async def background_task(
    action: Literal["status", "list", "stop"], task_id: str | None = None
) -> dict[str, Any]:
    """Inspect or stop background sandbox commands.

    `status` and `stop` require `task_id`; `list` does not. Status reads are for explicit user
    requests or when completion needs inspection, not polling loops.
    """
    if action in {"status", "stop"} and not task_id:
        return {"success": False, "error": f"task_id is required for {action}"}
    try:
        _, backend = _current_backend()
        script = _control_script(action, task_id)
        result = await _execute(
            backend, f"printf %s {shlex.quote(_encoded(script))} | base64 -d | python3"
        )
        return {"success": True, **result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
