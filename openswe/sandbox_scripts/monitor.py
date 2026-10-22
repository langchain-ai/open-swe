"""Runs inside a thread's sandbox to start, run, read and stop monitors.

Written to the sandbox by ``openswe.monitors.sandbox`` and invoked as
``python3 monitor-<hash>.py <base64 JSON request>``. Every action prints one
JSON line; ``run`` is the detached runner that ``start`` spawns.

A monitor runs ``command`` with stdout and stderr merged, numbers every line it
prints into ``raw.log``, and feeds those lines to ``filter`` when there is one.
Each batch of lines the filter prints (or, with no filter, the command prints)
within ``BATCH_SECONDS`` is one event in ``events.jsonl``. The backend reads
events past ``acked.json`` with ``sync`` and records them for the thread.
Standard library only, and no syntax newer than Python 3.8.
"""

import base64
import collections
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time

# The backend writes this script into the monitors root, so the root is its directory.
ROOT = os.path.dirname(os.path.abspath(__file__))
LAUNCH_LOCK = os.path.join(ROOT, ".launch-lock")
ID_PATTERN = re.compile(r"^mon-[0-9a-f-]{1,64}$")
TERMINAL = ("completed", "failed", "timed_out", "stopped", "filter_exited", "too_noisy", "lost")
MAX_ACTIVE_PER_THREAD = 3
# The raw log rotates into segments; the oldest are deleted past MAX_RAW_BYTES.
RAW_SEGMENT_BYTES = 10 * 1024 * 1024
MAX_RAW_BYTES = 50 * 1024 * 1024
RAW_SEGMENT = re.compile(r"^raw-(\d{12})\.log$")
MAX_FILTER_BACKLOG = 4 * 1024 * 1024
BATCH_SECONDS = 0.2
MAX_EVENT_LINES = 50
MAX_LINE_CHARS = 2000
NOISY_WINDOW_SECONDS = 120
NOISY_WINDOW_EVENTS = 40
MAX_EVENTS = 200
SYNC_EVENTS_LIMIT = 100
TAIL_LINES = 20
DEFAULT_OUTPUT_LINES = 200
MAX_OUTPUT_LINES = 500
MAX_OUTPUT_BYTES = 65536
MAX_FILTER_ERR_CHARS = 2000
DRAIN_SECONDS = 5
NUDGE_SECONDS = 2
# Detached processes do not count as sandbox activity; a sync is a sandbox exec that does.
HEARTBEAT_SECONDS = 900
CALLBACK_RETRY_DELAYS = (0, 5, 15, 30, 60, 120, 300, 300, 300)
TTL_SECONDS = 7 * 86400


def emit(value):
    print(json.dumps(value))
    sys.exit(0)


def fail(message):
    emit({"error": message})


def read_json(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(value, handle)
    os.replace(tmp, path)


def alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, TypeError):
        return False
    return True


def kill_group(pid):
    """TERM the process group, then KILL whatever ignored it."""
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 0.0)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, TypeError):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


class Monitor:
    """One monitor's directory: its config, state, logs and delivery cursor."""

    def __init__(self, monitor_id):
        if not ID_PATTERN.match(monitor_id or ""):
            fail("invalid monitor_id")
        self.id = monitor_id
        self.dir = os.path.join(ROOT, monitor_id)
        self.config_path = os.path.join(self.dir, "config.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self.events_path = os.path.join(self.dir, "events.jsonl")
        self.filter_err_path = os.path.join(self.dir, "filter.err")
        self.acked_path = os.path.join(self.dir, "acked.json")
        self.stop_path = os.path.join(self.dir, "stop")

    @classmethod
    def all(cls):
        if not os.path.isdir(ROOT):
            return []
        return [cls(name) for name in sorted(os.listdir(ROOT)) if ID_PATTERN.match(name)]

    def state(self):
        """The saved state, marking a runner that died without saying so as ``lost``."""
        state = read_json(self.state_path)
        if not isinstance(state, dict):
            return None
        if state.get("status") == "running" and not alive(state.get("runner_pid")):
            try:
                stale = time.time() - os.path.getmtime(self.state_path) >= 5
            except OSError:
                stale = True
            if stale:
                kill_group(state.get("pid"))
                state["status"] = "lost"
                state["finished_at"] = time.time()
                write_json(self.state_path, state)
        return state

    def owned_state(self, owner):
        state = self.state()
        if state is None or state.get("owner_thread_id") != owner:
            fail("monitor not found")
        return state

    def acked(self):
        acked = read_json(self.acked_path, {})
        return acked if isinstance(acked, dict) else {}

    def summary(self, state):
        keys = (
            "monitor_id",
            "description",
            "command",
            "filter",
            "status",
            "stop_reason",
            "exit_code",
            "filter_exit_code",
            "started_at",
            "finished_at",
            "timeout",
            "raw_lines",
            "first_raw_line",
            "events",
            "dropped_filter_bytes",
            "callback",
        )
        summary = {key: state.get(key) for key in keys}
        summary["raw_logs"] = os.path.join(self.dir, "raw-*.log")
        summary["events_log"] = self.events_path
        return summary

    def segments(self):
        """``(first line, path)`` of each retained raw log segment, oldest first."""
        try:
            names = os.listdir(self.dir)
        except OSError:
            return []
        found = [
            (int(match.group(1)), name) for name in names if (match := RAW_SEGMENT.match(name))
        ]
        return [(first, os.path.join(self.dir, name)) for first, name in sorted(found)]

    def numbered(self, start, end):
        """``(number, text)`` for retained raw lines ``start..end`` (1-based, inclusive)."""
        segments = self.segments()
        lines = []
        size = 0
        for index, (first, path) in enumerate(segments):
            following = segments[index + 1][0] if index + 1 < len(segments) else None
            if following is not None and following <= start:
                continue
            if first > end:
                break
            try:
                with open(path, "rb") as handle:
                    for number, raw in enumerate(handle, start=first):
                        if number < start:
                            continue
                        if number > end:
                            break
                        text = raw.rstrip(b"\n").decode(errors="replace")
                        size += len(text) + 8
                        if size > MAX_OUTPUT_BYTES or len(lines) >= MAX_OUTPUT_LINES:
                            return lines
                        lines.append((number, text))
            except OSError:
                continue
        return lines

    def first_line(self):
        segments = self.segments()
        return segments[0][0] if segments else 1

    def tail(self, count):
        segments = self.segments()
        if not segments:
            return []
        first, path = segments[-1]
        try:
            with open(path, "rb") as handle:
                last = first + sum(1 for _ in handle) - 1
        except OSError:
            return []
        return self.numbered(max(1, last - count + 1), last)

    def events_after(self, seq, limit):
        events = []
        try:
            with open(self.events_path) as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("seq", 0) > seq:
                        events.append(event)
                        if len(events) >= limit:
                            break
        except OSError:
            pass
        return events

    def filter_errors(self):
        try:
            with open(self.filter_err_path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - MAX_FILTER_ERR_CHARS))
                return handle.read().decode(errors="replace").strip()
        except OSError:
            return ""


class RawLog:
    """Every line the command printed, numbered from 1, in segments named by their first line.

    A segment past ``RAW_SEGMENT_BYTES`` is closed and a new one opened; the
    oldest are deleted while the retained segments exceed ``MAX_RAW_BYTES``.
    """

    def __init__(self, directory):
        self.dir = directory
        self.count = 0
        self.segments = collections.deque()
        self.handle = self.segment(1)

    @property
    def first(self):
        return self.segments[0][0]

    def segment(self, first):
        """Open the segment whose first line is ``first``."""
        path = os.path.join(self.dir, f"raw-{first:012d}.log")
        self.segments.append([first, path, 0])
        return open(path, "ab")

    def append(self, lines):
        if not lines:
            return
        data = b"".join(line + b"\n" for line in lines)
        self.handle.write(data)
        self.count += len(lines)
        self.segments[-1][2] += len(data)
        if self.segments[-1][2] >= RAW_SEGMENT_BYTES:
            self.handle.close()
            self.handle = self.segment(self.count + 1)
            self.prune()

    def prune(self):
        # Never the segment just closed, however large one write made it.
        while len(self.segments) > 2 and sum(size for _, _, size in self.segments) > MAX_RAW_BYTES:
            _, path, _ = self.segments.popleft()
            try:
                os.remove(path)
            except OSError:
                pass

    def flush(self):
        self.handle.flush()


class Lines:
    """Splits a byte stream into lines, holding back a trailing partial one."""

    def __init__(self):
        self.partial = b""

    def feed(self, chunk):
        data = self.partial + chunk
        lines = data.split(b"\n")
        self.partial = lines.pop()
        return lines

    def finish(self):
        rest, self.partial = self.partial, b""
        return [rest] if rest else []


class Runner:
    """The detached process that runs one monitor until it ends."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.config = read_json(monitor.config_path)
        self.raw = RawLog(monitor.dir)
        self.events = open(monitor.events_path, "a")
        self.seq = 0
        self.recent = collections.deque()
        self.batch = []
        self.batch_started = 0.0
        self.batch_anchor = 0
        self.backlog = bytearray()
        self.dropped = 0
        self.status = None
        self.stop_reason = None
        self.started_at = time.time()
        self.started = time.monotonic()
        self.nudge = None
        self.nudged_seq = 0
        self.last_nudge = time.monotonic()

    # -- state -------------------------------------------------------------

    def save(self, status, exit_code=None, filter_exit_code=None):
        write_json(
            self.monitor.state_path,
            {
                "monitor_id": self.monitor.id,
                "owner_thread_id": self.config["owner_thread_id"],
                "description": self.config["description"],
                "command": self.config["command"],
                "filter": self.config["filter"],
                "timeout": self.config["timeout"],
                "callback": self.config["callback"],
                "status": status,
                "stop_reason": self.stop_reason,
                "pid": self.command.pid,
                "runner_pid": os.getpid(),
                "exit_code": exit_code,
                "filter_exit_code": filter_exit_code,
                "started_at": self.started_at,
                "finished_at": None if status == "running" else time.time(),
                "raw_lines": self.raw.count,
                "first_raw_line": self.raw.first,
                "events": self.seq,
                "dropped_filter_bytes": self.dropped,
            },
        )

    # -- events ------------------------------------------------------------

    def event_lines(self, lines, first_number=None):
        for index, line in enumerate(lines):
            if not self.batch:
                self.batch_started = time.monotonic()
                self.batch_anchor = (
                    first_number + index if first_number is not None else self.raw.count
                )
            text = line.decode(errors="replace")
            if len(text) > MAX_LINE_CHARS:
                text = text[:MAX_LINE_CHARS] + " [truncated]"
            self.batch.append(text)
            if len(self.batch) >= MAX_EVENT_LINES:
                self.emit_batch()

    def emit_batch(self):
        if not self.batch or self.status == "too_noisy":
            self.batch = []
            return
        self.seq += 1
        self.events.write(
            json.dumps(
                {
                    "seq": self.seq,
                    "at_raw_line": self.batch_anchor,
                    "lines": self.batch,
                    "ts": time.time(),
                }
            )
            + "\n"
        )
        self.events.flush()
        self.batch = []
        now = time.monotonic()
        self.recent.append(now)
        while self.recent and now - self.recent[0] > NOISY_WINDOW_SECONDS:
            self.recent.popleft()
        if self.seq >= MAX_EVENTS:
            self.end("too_noisy", f"reached the limit of {MAX_EVENTS} events")
        elif len(self.recent) > NOISY_WINDOW_EVENTS:
            self.end(
                "too_noisy",
                f"emitted more than {NOISY_WINDOW_EVENTS} events in {NOISY_WINDOW_SECONDS}s",
            )

    def end(self, status, reason=None):
        if self.status is None:
            self.status = status
            self.stop_reason = reason

    # -- callbacks ---------------------------------------------------------

    def sync_url(self):
        if not self.config["callback"]:
            return None
        base = os.environ.get(self.config["tools_url_env"])
        query = ""
        if not base:
            try:
                with open(self.config["tools_url_file"]) as handle:
                    base, _, query = handle.read().strip().partition("?")
            except OSError:
                return None
        url = base.rstrip("/") + "/monitors/" + self.monitor.id + "/sync"
        return url + "?" + query if query else url

    def post_sync(self):
        url = self.sync_url()
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

    def maybe_nudge(self):
        if not self.config["callback"]:
            return
        if self.nudge is not None and self.nudge.poll() is None:
            return
        now = time.monotonic()
        due = self.seq > self.nudged_seq and now - self.last_nudge >= NUDGE_SECONDS
        if due or now - self.last_nudge >= HEARTBEAT_SECONDS:
            self.nudge = self.post_sync()
            self.nudged_seq = self.seq
            self.last_nudge = now

    def final_sync(self):
        """Retry until the backend has the end notice: it answers 5xx until then."""
        if self.nudge is not None and self.nudge.poll() is None:
            self.nudge.kill()
        for delay in CALLBACK_RETRY_DELAYS:
            time.sleep(delay)
            proc = self.post_sync()
            if proc is None:
                return
            try:
                proc.wait(90)
                code = int(proc.stdout.read() or 0)
            except (subprocess.TimeoutExpired, ValueError):
                proc.kill()
                code = 0
            if 200 <= code < 300 or (400 <= code < 500 and code not in (408, 429)):
                return

    # -- the loop ----------------------------------------------------------

    def start_filter(self):
        if not self.config["filter"]:
            return None
        argv = ["/bin/sh", "-c", self.config["filter"]]
        if shutil.which("stdbuf"):
            # Line-buffers the filter's own stdout and its children's (grep, awk, sed).
            argv = ["stdbuf", "-oL", "-eL", *argv]
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        with open(self.monitor.filter_err_path, "ab") as errors:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                start_new_session=True,
                env=env,
            )
        if proc.stdin is not None:
            os.set_blocking(proc.stdin.fileno(), False)
        return proc

    def feed_filter(self, filt):
        if filt is None or filt.stdin is None or not self.backlog:
            return
        try:
            written = os.write(filt.stdin.fileno(), self.backlog)
            del self.backlog[:written]
        except BlockingIOError:
            pass
        except (BrokenPipeError, OSError):
            self.backlog.clear()
            self.close_filter_input(filt)

    def close_filter_input(self, filt):
        if filt is not None and filt.stdin is not None:
            try:
                filt.stdin.close()
            except OSError:
                pass
            filt.stdin = None

    def command_lines(self, lines, filt):
        first = self.raw.count + 1
        self.raw.append(lines)
        if filt is None:
            self.event_lines(lines, first)
            return
        if filt.stdin is None:
            return
        data = b"".join(line + b"\n" for line in lines)
        room = MAX_FILTER_BACKLOG - len(self.backlog)
        if len(data) > room:
            self.dropped += len(data) - max(room, 0)
            data = data[: max(room, 0)]
        self.backlog.extend(data)

    def run(self):
        try:
            os.setsid()
        except OSError:
            pass
        self.command = subprocess.Popen(
            ["/bin/sh", "-c", self.config["command"]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        filt = self.start_filter()
        self.save("running")
        selector = selectors.DefaultSelector()
        if self.command.stdout is not None:
            selector.register(self.command.stdout, selectors.EVENT_READ, "command")
        if filt is not None and filt.stdout is not None:
            selector.register(filt.stdout, selectors.EVENT_READ, "filter")
        command_lines, filter_lines = Lines(), Lines()
        command_open, filter_open = True, filt is not None
        last_save = time.monotonic()
        drain_deadline = None
        while True:
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fd, 65536)
                if key.data == "command":
                    if chunk:
                        self.command_lines(command_lines.feed(chunk), filt)
                    else:
                        selector.unregister(key.fileobj)
                        command_open = False
                        self.command_lines(command_lines.finish(), filt)
                elif chunk:
                    self.event_lines(filter_lines.feed(chunk))
                else:
                    selector.unregister(key.fileobj)
                    filter_open = False
                    self.event_lines(filter_lines.finish())
            self.feed_filter(filt)
            if self.batch and time.monotonic() - self.batch_started >= BATCH_SECONDS:
                self.emit_batch()
            if os.path.exists(self.monitor.stop_path):
                reason = None
                try:
                    with open(self.monitor.stop_path) as handle:
                        reason = handle.read().strip() or None
                except OSError:
                    pass
                self.end("stopped", reason)
            elif time.monotonic() - self.started >= self.config["timeout"]:
                self.end("timed_out")
            elif filt is not None and command_open and filt.poll() is not None:
                self.end("filter_exited")
            if self.status is not None:
                break
            if not command_open and self.command.poll() is not None:
                # The command is done: let the filter finish what it was given.
                if drain_deadline is None:
                    drain_deadline = time.monotonic() + DRAIN_SECONDS
                if not self.backlog:
                    self.close_filter_input(filt)
                if not filter_open or time.monotonic() >= drain_deadline:
                    break
            if time.monotonic() - last_save >= 1:
                self.raw.flush()
                self.save("running")
                last_save = time.monotonic()
            self.maybe_nudge()
        kill_group(self.command.pid)
        exit_code = self.command.wait()
        filter_exit_code = None
        if filt is not None:
            self.close_filter_input(filt)
            if filt.poll() is None:
                kill_group(filt.pid)
            filter_exit_code = filt.wait()
        if self.status is None:
            self.status = "completed" if exit_code == 0 else "failed"
        self.emit_batch()
        self.raw.flush()
        self.save(self.status, exit_code, filter_exit_code)
        self.final_sync()


def start(request):
    owner = request["owner_thread_id"]
    monitor = Monitor(request["monitor_id"])
    os.makedirs(ROOT, exist_ok=True)
    for _ in range(20):
        try:
            os.mkdir(LAUNCH_LOCK)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LAUNCH_LOCK) > 30:
                    os.rmdir(LAUNCH_LOCK)
                    continue
            except OSError:
                pass
            time.sleep(0.1)
    else:
        fail("monitor launch is busy; try again")
    try:
        active = 0
        for other in Monitor.all():
            state = other.state()
            if state and state.get("owner_thread_id") == owner and state.get("status") == "running":
                active += 1
        if active >= MAX_ACTIVE_PER_THREAD:
            fail(f"at most {MAX_ACTIVE_PER_THREAD} monitors may run per thread; stop one first")
        # Without curl or a tools URL the backend polls instead.
        callback = (
            bool(request.get("callback"))
            and shutil.which("curl") is not None
            and bool(
                os.environ.get(request["tools_url_env"]) or nonempty(request["tools_url_file"])
            )
        )
        os.mkdir(monitor.dir)
        write_json(
            monitor.config_path,
            {
                "owner_thread_id": owner,
                "command": request["command"],
                "filter": request.get("filter") or "",
                "description": request["description"],
                "timeout": int(request["timeout"]),
                "callback": callback,
                "tools_url_env": request["tools_url_env"],
                "tools_url_file": request["tools_url_file"],
            },
        )
        runner_err = open(os.path.join(monitor.dir, "runner.err"), "ab")
        subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                encode({"action": "run", "monitor_id": monitor.id}),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=runner_err,
            start_new_session=True,
            close_fds=True,
        )
        for _ in range(50):
            state = read_json(monitor.state_path)
            if state:
                emit(monitor.summary(state))
            time.sleep(0.1)
        fail("monitor runner did not start")
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


def encode(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def listing(request):
    owner = request["owner_thread_id"]
    monitors = []
    for monitor in Monitor.all():
        state = monitor.state()
        if state is None:
            continue
        finished = state.get("finished_at")
        if finished and time.time() - finished > TTL_SECONDS:
            shutil.rmtree(monitor.dir, ignore_errors=True)
            continue
        if state.get("owner_thread_id") == owner:
            monitors.append(monitor.summary(state))
    emit({"monitors": monitors})


def sync(request):
    """Every monitor of the thread with the events and end notice it has not acked."""
    owner = request["owner_thread_id"]
    wanted = request.get("monitor_id")
    monitors = []
    for monitor in Monitor.all():
        if wanted and monitor.id != wanted:
            continue
        state = monitor.state()
        if state is None or state.get("owner_thread_id") != owner:
            continue
        acked = monitor.acked()
        entry = monitor.summary(state)
        entry["events_pending"] = monitor.events_after(acked.get("seq", 0), SYNC_EVENTS_LIMIT)
        # A full page may not be all of them; the end notice waits until it is.
        entry["behind"] = len(entry["events_pending"]) >= SYNC_EVENTS_LIMIT
        entry["end"] = None
        if state.get("status") in TERMINAL and not acked.get("end") and not entry["behind"]:
            entry["end"] = {
                "tail": monitor.tail(TAIL_LINES),
                "filter_errors": monitor.filter_errors(),
            }
        monitors.append(entry)
    emit({"monitors": monitors})


def owner(request):
    state = Monitor(request["monitor_id"]).state()
    if state is None:
        fail("monitor not found")
    emit({"owner_thread_id": state.get("owner_thread_id")})


def ack(request):
    monitor = Monitor(request["monitor_id"])
    monitor.owned_state(request["owner_thread_id"])
    acked = monitor.acked()
    acked["seq"] = max(acked.get("seq", 0), int(request.get("seq") or 0))
    acked["end"] = bool(acked.get("end") or request.get("end"))
    write_json(monitor.acked_path, acked)
    emit(acked)


def output(request):
    monitor = Monitor(request["monitor_id"])
    state = monitor.owned_state(request["owner_thread_id"])
    start_line, end_line = request.get("start_line"), request.get("end_line")
    if start_line is None and end_line is None:
        lines = monitor.tail(DEFAULT_OUTPUT_LINES)
    else:
        start_line = max(int(start_line or monitor.first_line()), 1)
        end_line = int(end_line) if end_line is not None else start_line + DEFAULT_OUTPUT_LINES - 1
        lines = monitor.numbered(start_line, end_line)
    result = monitor.summary(state)
    result["output"] = "\n".join(f"{number:>6}\t{text}" for number, text in lines)
    result["shown"] = [lines[0][0], lines[-1][0]] if lines else None
    emit(result)


def stop(request):
    monitor = Monitor(request["monitor_id"])
    state = monitor.owned_state(request["owner_thread_id"])
    if state.get("status") == "running":
        with open(monitor.stop_path, "w") as handle:
            handle.write(request.get("reason") or "")
        deadline = time.time() + 5
        while state.get("status") == "running" and time.time() < deadline:
            time.sleep(0.1)
            state = monitor.state() or state
    result = monitor.summary(state)
    result["tail"] = "\n".join(f"{number:>6}\t{text}" for number, text in monitor.tail(TAIL_LINES))
    emit(result)


def main():
    request = json.loads(base64.b64decode(sys.argv[1]).decode())
    action = request.get("action")
    if action == "run":
        Runner(Monitor(request["monitor_id"])).run()
        return
    actions = {
        "start": start,
        "list": listing,
        "sync": sync,
        "ack": ack,
        "owner": owner,
        "output": output,
        "stop": stop,
    }
    if action not in actions:
        fail("unknown action")
    actions[action](request)


if __name__ == "__main__":
    main()
