"""Runs inside a thread's sandbox to list a directory or read a file.

Delivered as a heredoc by ``agent.threads.files``; ``__PAYLOAD__`` is
substituted with a base64 JSON blob before execution. Prints a single JSON line.
"""

import base64
import json
import os
import subprocess

PAYLOAD = json.loads(base64.b64decode("__PAYLOAD__").decode())
MAX_FILE_BYTES = 1024 * 1024


def ignored_paths(root, paths):
    if not paths:
        return set()
    result = subprocess.run(
        ["git", "-C", root, "check-ignore", "-z", "--stdin"],
        input="\0".join(paths).encode() + b"\0",
        capture_output=True,
    )
    return set(result.stdout.decode(errors="replace").split("\0"))


def list_directory(root, target, relative):
    entries = []
    for child in os.scandir(target):
        if child.name == ".git" or not (child.is_dir() or child.is_file()):
            continue
        path = f"{relative}/{child.name}" if relative else child.name
        entries.append({"path": path, "kind": "directory" if child.is_dir() else "file"})
    ignored = ignored_paths(root, [entry["path"] for entry in entries])
    for entry in entries:
        entry["ignored"] = entry["path"] in ignored
    return {"kind": "directory", "entries": entries}


def read_file(target):
    size = os.path.getsize(target)
    with open(target, "rb") as handle:
        data = handle.read(MAX_FILE_BYTES)
    binary = b"\0" in data
    return {
        "kind": "file",
        "contents": "" if binary else data.decode(errors="replace"),
        "binary": binary,
        "truncated": size > MAX_FILE_BYTES,
        "size": size,
    }


def main():
    roots = [PAYLOAD["root"], PAYLOAD["fallback"]]
    root = os.path.realpath(next(filter(os.path.isdir, roots), roots[-1]))
    relative = PAYLOAD["path"].strip("/")
    target = os.path.realpath(os.path.join(root, relative))
    inside = os.path.commonpath([root, target]) == root
    if not inside or ".git" in os.path.relpath(target, root).split(os.sep):
        return {"error": "Path must be inside the workspace and outside .git."}
    if os.path.isdir(target):
        return list_directory(root, target, relative)
    if os.path.isfile(target):
        return read_file(target)
    return {"error": "Path not found."}


try:
    print(json.dumps(main()))
except OSError as error:
    print(json.dumps({"error": error.strerror or str(error)}))
