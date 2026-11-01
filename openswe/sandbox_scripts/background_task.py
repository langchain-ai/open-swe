"""Runs inside a thread's sandbox to launch, run, read and stop background commands.

Written to the sandbox by ``openswe.tools.background_execute`` and invoked as
``python3 background_task-<hash>.py <base64 JSON request>``. Every action prints
one JSON line; ``run`` is the detached runner ``launch`` spawns.

Each command lives in ``<root>/<task_id>/``: ``state.json`` (status, exit code,
owner), ``output.log`` (stdout and stderr, capped to a head and a tail), and the
``stop`` file and ``notify.claim``/``notify.done`` markers the backend uses.
Tasks launched before this script, by the runner it replaced, keep that layout,
so ``list`` and ``status`` read them too. Standard library only, Python 3.8.
"""

import base64
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time

# The backend writes this script into the task root, so the root is its directory.
ROOT = os.path.dirname(os.path.abspath(__file__))
LAUNCH_LOCK = os.path.join(ROOT, ".launch-lock")
MAX_ACTIVE_TASKS = 4
MAX_OUTPUT_BYTES = 1048576
MAX_INLINE_OUTPUT_BYTES = 65536
TASK_TTL_SECONDS = 604800
# Detached processes do not count as sandbox activity, so without a heartbeat
# the provider's idle stop kills long commands.
HEARTBEAT_SECONDS = 900
CALLBACK_RETRY_DELAYS = (0, 5, 15, 30, 60, 120, 300, 300, 300)


def emit(value, code=0):
    print(json.dumps(value))
    sys.exit(code)


def fail(message, code=2):
    emit({"error": message}, code)


def encode(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(value, handle)
    os.replace(tmp, path)


def valid_task_id(task_id):
    return bool(task_id) and task_id.replace("-", "").isalnum()


def callback_url(config, task_id, event):
    if not config.get("callback"):
        return None
    base = os.environ.get(config["tools_url_env"])
    query = ""
    if not base:
        try:
            with open(config["tools_url_file"]) as handle:
                base, _, query = handle.read().strip().partition("?")
        except OSError:
            return None
    url = base.rstrip("/") + "/background-tasks/" + task_id + "/" + event
    return url + "?" + query if query else url


def post(config, task_id, event):
    url = callback_url(config, task_id, event)
    if not url:
        return None
    # The URL goes through stdin so a file-provisioned token never shows in ps.
    proc = subprocess.Popen(
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-X",
            "POST",
            "--max-time",
            "60",
            "-K",
            "-",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if proc.stdin is not None:
        proc.stdin.write('url = "' + url + '"\n')
        proc.stdin.close()
    return proc


class Task:
    """One background command's directory."""

    def __init__(self, task_id):
        self.id = task_id
        self.dir = os.path.join(ROOT, task_id)
        self.config_path = os.path.join(self.dir, "config.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self.output_path = os.path.join(self.dir, "output.log")
        self.stop_path = os.path.join(self.dir, "stop")

    def load(self):
        """The saved state, marking a runner that died without saying so as ``lost``."""
        try:
            with open(self.state_path) as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            return None
        if state.get("status") == "running":
            try:
                os.kill(state.get("runner_pid"), 0)
            except (ProcessLookupError, PermissionError, TypeError):
                if time.time() - os.path.getmtime(self.state_path) >= 5:
                    try:
                        os.killpg(state.get("pid"), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, TypeError):
                        pass
                    state["status"] = "lost"
                    state["finished_at"] = time.time()
                    write_json(self.state_path, state)
        state["duration_seconds"] = round(
            (state.get("finished_at") or time.time()) - state.get("started_at", time.time()), 2
        )
        return state

    def with_output(self, state):
        try:
            with open(state["output_path"], "rb") as handle:
                data = handle.read()
            if len(data) > MAX_INLINE_OUTPUT_BYTES:
                half = MAX_INLINE_OUTPUT_BYTES // 2
                omitted = len(data) - half * 2
                data = data[:half] + f"\n[{omitted} inline bytes omitted]\n".encode() + data[-half:]
            state["output"] = data.decode(errors="replace")
        except (OSError, KeyError):
            state["output"] = ""
        return state


def public(state):
    state.pop("pid", None)
    state.pop("runner_pid", None)
    return state


class Runner:
    """The detached process that runs one command until it ends."""

    def __init__(self, task):
        self.task = task
        with open(task.config_path) as handle:
            self.config = json.load(handle)
        self.started_at = time.time()
        self.started = time.monotonic()
        self.head = bytearray()
        self.tail = bytearray()
        self.omitted = 0
        self.process = None

    def write_state(self, status, exit_code=None):
        write_json(
            self.task.state_path,
            {
                "task_id": self.task.id,
                "owner_thread_id": self.config["owner_thread_id"],
                "command": self.config["command"],
                "display_description": self.config.get("display_description"),
                "status": status,
                "pid": self.process.pid if self.process else None,
                "runner_pid": os.getpid(),
                "exit_code": exit_code,
                "started_at": self.started_at,
                "finished_at": time.time() if status != "running" else None,
                "output_path": self.task.output_path,
            },
        )

    def capture(self, chunk):
        half = MAX_OUTPUT_BYTES // 2
        if len(self.head) < half:
            used = min(half - len(self.head), len(chunk))
            self.head.extend(chunk[:used])
            chunk = chunk[used:]
        if chunk:
            self.tail.extend(chunk)
            if len(self.tail) > half:
                dropped = len(self.tail) - half
                del self.tail[:dropped]
                self.omitted += dropped

    def flush(self):
        with open(self.task.output_path + ".tmp", "wb") as handle:
            handle.write(self.head)
            if self.omitted:
                handle.write(f"\n[{self.omitted} bytes omitted]\n".encode())
            handle.write(self.tail)
        os.replace(self.task.output_path + ".tmp", self.task.output_path)

    def notify_complete(self):
        for delay in CALLBACK_RETRY_DELAYS:
            time.sleep(delay)
            proc = post(self.config, self.task.id, "complete")
            if proc is None:
                return
            try:
                proc.wait(90)
                code = int((proc.stdout.read() if proc.stdout else "") or 0)
            except (subprocess.TimeoutExpired, ValueError):
                proc.kill()
                code = 0
            if 200 <= code < 300 or (400 <= code < 500 and code not in (408, 429)):
                return

    def run(self):
        # Detach from the launching shell's session here rather than with
        # `setsid(1)`, which macOS does not ship.
        try:
            os.setsid()
        except OSError:
            pass
        timeout = self.config["timeout"]
        self.process = subprocess.Popen(
            ["/bin/sh", "-c", self.config["command"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.write_state("running")
        selector = selectors.DefaultSelector()
        if self.process.stdout is not None:
            selector.register(self.process.stdout, selectors.EVENT_READ)
        status = None
        last_flush = 0.0
        last_heartbeat = time.monotonic()
        heartbeat = None
        while status is None:
            if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                if heartbeat is None or heartbeat.poll() is not None:
                    heartbeat = post(self.config, self.task.id, "heartbeat")
            for key, _ in selector.select(0.25):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    self.capture(chunk)
                else:
                    selector.unregister(key.fileobj)
            if time.time() - last_flush >= 1:
                self.flush()
                last_flush = time.time()
            if os.path.exists(self.task.stop_path):
                status = "stopped"
            elif time.monotonic() - self.started >= timeout:
                status = "timed_out"
            elif self.process.poll() is not None:
                status = "completed" if self.process.returncode == 0 else "failed"
            if status in ("stopped", "timed_out"):
                # macOS answers EPERM, not ESRCH, for a group left with only zombies.
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(2)
                except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
                    pass
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self.process.stdout is not None:
            os.set_blocking(self.process.stdout.fileno(), False)
            try:
                while True:
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    self.capture(chunk)
            except BlockingIOError:
                pass
        exit_code = self.process.wait()
        self.flush()
        self.write_state(status, exit_code)
        self.notify_complete()


def launch(request):
    task = Task(request["task_id"])
    if not valid_task_id(task.id):
        fail("invalid task_id")
    callback = bool(request.get("callback"))
    if callback and shutil.which("curl") is None:
        fail("background execution requires curl")
    tools_url = request["tools_url_env"], request["tools_url_file"]
    if callback and not (os.environ.get(tools_url[0]) or nonempty(tools_url[1])):
        fail(
            "background execution needs the sandbox callback URL, which this deployment does not provide"
        )
    os.makedirs(ROOT, exist_ok=True)
    for _ in range(10):
        try:
            os.mkdir(LAUNCH_LOCK)
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        fail("background launch is busy")
    try:
        active = sum(1 for state in states() if state.get("status") == "running")
        if active >= MAX_ACTIVE_TASKS:
            fail("active task limit reached")
        try:
            os.mkdir(task.dir)
        except OSError:
            fail("task already exists")
        write_json(
            task.config_path,
            {
                "command": request["command"],
                "display_description": request.get("display_description"),
                "timeout": int(request["timeout"]),
                "callback": callback,
                "owner_thread_id": request["owner_thread_id"],
                "tools_url_env": tools_url[0],
                "tools_url_file": tools_url[1],
            },
        )
        with open(os.devnull, "rb") as devnull_in, open(os.devnull, "wb") as devnull_out:
            subprocess.Popen(
                [
                    sys.executable,
                    os.path.abspath(__file__),
                    encode({"action": "run", "task_id": task.id}),
                ],
                stdin=devnull_in,
                stdout=devnull_out,
                stderr=devnull_out,
                close_fds=True,
            )
        for _ in range(10):
            state = task.load()
            if state:
                emit(public(state))
            time.sleep(0.1)
        fail("background runner did not start")
    finally:
        try:
            os.rmdir(LAUNCH_LOCK)
        except OSError:
            pass


def nonempty(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def states():
    if not os.path.isdir(ROOT):
        return []
    found = []
    for name in sorted(os.listdir(ROOT)):
        state = Task(name).load() if os.path.isdir(os.path.join(ROOT, name)) else None
        if state:
            found.append(state)
    return found


def listing(_request):
    if os.path.isdir(ROOT):
        for lock, stale_after in ((".launch-lock", 30), ("monitor.lock", 300)):
            path = os.path.join(ROOT, lock)
            if os.path.isdir(path) and time.time() - os.path.getmtime(path) > stale_after:
                shutil.rmtree(path, ignore_errors=True)
    tasks = []
    for state in states():
        task = Task(state["task_id"])
        finished = state.get("finished_at") or time.time()
        if state.get("status") != "running" and time.time() - finished > TASK_TTL_SECONDS:
            shutil.rmtree(task.dir, ignore_errors=True)
            continue
        claim = os.path.join(task.dir, "notify.claim")
        done = os.path.join(task.dir, "notify.done")
        if os.path.isdir(claim) and time.time() - os.path.getmtime(claim) > 300:
            shutil.rmtree(claim, ignore_errors=True)
        state["notification"] = (
            "done" if os.path.isdir(done) else "claimed" if os.path.isdir(claim) else "pending"
        )
        tasks.append(public(state))
    emit({"tasks": tasks})


def status(request):
    task = known(request)
    emit(task.with_output(public(task.load() or {})))


def stop(request):
    task = known(request)
    state = task.load() or {}
    if state.get("status") == "running":
        open(task.stop_path, "a").close()
        try:
            os.killpg(state["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError, KeyError, TypeError):
            pass
        deadline = time.time() + 3
        while state.get("status") == "running" and time.time() < deadline:
            time.sleep(0.1)
            state = task.load() or state
        if state.get("status") == "running":
            state["status"] = "stop_requested"
    emit(task.with_output(public(state)))


def known(request):
    task_id = request.get("task_id")
    if not valid_task_id(task_id):
        fail("invalid task_id")
    task = Task(task_id)
    if not task.load():
        fail("task not found", 3)
    return task


def main():
    request = json.loads(base64.b64decode(sys.argv[1]).decode())
    action = request.get("action")
    if action == "run":
        Runner(Task(request["task_id"])).run()
        return
    actions = {"launch": launch, "list": listing, "status": status, "stop": stop}
    if action not in actions:
        fail("unknown action")
    actions[action](request)


if __name__ == "__main__":
    main()
