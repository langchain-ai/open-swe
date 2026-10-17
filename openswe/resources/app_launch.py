"""Runs inside a sandbox to start a saved app unless something already answers on its port.

Delivered as a heredoc by ``openswe.apps.models``; ``__PAYLOAD__`` is
substituted with a base64 JSON blob before execution. Prints a single JSON line.
"""

import base64
import json
import os
import socket
import subprocess
import time

PAYLOAD = json.loads(base64.b64decode("__PAYLOAD__").decode())
PORT = PAYLOAD["port"]
LOG_PATH = PAYLOAD["log_path"]
WAIT_SECONDS = 60
LOG_TAIL_BYTES = 4000


def listening():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
    except OSError:
        return False
    return True


def log_tail():
    try:
        with open(LOG_PATH, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            return handle.read().decode(errors="replace")
    except OSError:
        return ""


def main():
    if listening():
        return {"status": "running"}
    workdir = PAYLOAD["workdir"]
    if not os.path.isdir(workdir):
        return {"error": f"{workdir} does not exist in the sandbox", "log": ""}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "ab") as log:
        process = subprocess.Popen(
            ["sh", "-c", PAYLOAD["command"]],
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if listening():
            return {"status": "started"}
        # A command that daemonizes exits 0 before its server listens.
        code = process.poll()
        if code not in (None, 0):
            return {"error": f"the start command exited with status {code}", "log": log_tail()}
        time.sleep(0.5)
    return {"error": f"nothing listened on port {PORT} within {WAIT_SECONDS}s", "log": log_tail()}


print(json.dumps(main()))
