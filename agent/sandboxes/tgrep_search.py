"""Indexed literal search for sandbox repositories."""

import base64
import json
import logging
from typing import Any

from deepagents.backends.protocol import GrepResult

logger = logging.getLogger(__name__)

_TGREP_SCRIPT = r"""
import base64
import hashlib
import json
import os
import subprocess
import sys

payload = json.loads(base64.b64decode(sys.argv[1]))
tgrep = "/usr/local/bin/tgrep"
if not os.path.isfile(tgrep) or not os.access(tgrep, os.X_OK):
    print(json.dumps({"status": "unavailable"}))
    raise SystemExit
path = os.path.realpath(payload["path"])
probe = path if os.path.isdir(path) else os.path.dirname(path)
root_result = subprocess.run(
    ["git", "-C", probe, "rev-parse", "--show-toplevel"],
    capture_output=True,
    text=True,
    timeout=5,
)
if root_result.returncode:
    print(json.dumps({"status": "unavailable"}))
    raise SystemExit
root = os.path.realpath(root_result.stdout.strip())
try:
    if os.path.commonpath([root, path]) != root:
        raise ValueError
except ValueError:
    print(json.dumps({"status": "unavailable"}))
    raise SystemExit
key = hashlib.sha256(root.encode()).hexdigest()
index = os.path.join("/opt/open-swe/tgrep-indexes", key)
if not os.path.isfile(os.path.join(index, "lookup.bin")):
    print(json.dumps({"status": "unavailable"}))
    raise SystemExit
command = [tgrep, "--json", "--fixed-strings", "--color", "never"]
if payload["glob"] is not None:
    command.extend(["--glob", payload["glob"]])
command.extend(["--regexp", payload["pattern"], path, "--index-path", index])
limit = payload["max_count"]
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
matches = []
try:
    for line in process.stdout:
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if frame.get("type") != "match":
            continue
        data = frame.get("data", {})
        file_path = data.get("path", {}).get("text")
        line_number = data.get("line_number")
        if not isinstance(file_path, str) or not isinstance(line_number, int):
            continue
        matches.append({
            "path": file_path,
            "line": line_number,
            "text": data.get("lines", {}).get("text", "").rstrip("\n"),
        })
        if limit is not None and len(matches) > limit:
            process.terminate()
            break
    try:
        returncode = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = process.wait(timeout=2)
finally:
    if process.stdout is not None:
        process.stdout.close()
stderr = process.stderr.read(1000) if process.stderr is not None else ""
if process.stderr is not None:
    process.stderr.close()
truncated = limit is not None and len(matches) > limit
if returncode not in (0, 1, -15) and not truncated:
    print(json.dumps({"status": "error", "detail": stderr}))
else:
    print(json.dumps({
        "status": "ok",
        "matches": matches[:limit] if limit is not None else matches,
        "truncated": truncated,
    }))
"""


def build_tgrep_command(
    pattern: str,
    path: str | None,
    glob: str | None,
    max_count: int | None = None,
) -> str:
    """Build a sandbox command without interpolating model-controlled input."""
    payload = base64.b64encode(
        json.dumps(
            {"pattern": pattern, "path": path or ".", "glob": glob, "max_count": max_count}
        ).encode()
    ).decode()
    script = base64.b64encode(_TGREP_SCRIPT.encode()).decode()
    return f"python -c \"import base64;exec(base64.b64decode('{script}'))\" '{payload}'"


def parse_tgrep_result(output: str) -> GrepResult | None:
    """Return an indexed result, or `None` when regular grep should run."""
    try:
        data: Any = json.loads(output.strip())
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        return None
    matches = data.get("matches")
    if not isinstance(matches, list):
        return None
    try:
        return GrepResult(matches=matches, truncated=bool(data.get("truncated")))
    except TypeError, ValueError:
        logger.warning("Ignoring malformed tgrep sandbox response")
        return None
