"""Per-turn git checkpoints, and the diffs read back from them.

A turn is one run. At run start we snapshot the sandbox worktree into the object
DB under ``refs/open-swe/turns/<key>`` — without touching HEAD, the index, or the
worktree — so the dashboard can ask *git* what a turn changed instead of
replaying edit tool calls. A ref (not a bare tree) is used so an auto-``git gc``
mid-run cannot reap the snapshot; ``refs/open-swe/*`` is never pushed.

Both the snapshot and the read-back are best effort: on any failure the caller
gets ``None`` / ``status="error"`` and the UI degrades to "diff unavailable".
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

CHECKPOINT_TIMEOUT_SECONDS = 15
DIFF_TIMEOUT_SECONDS = 30
MAX_CHECKPOINTS = 100

_MAX_FILES = 200
_MAX_FILE_BYTES = 400_000
_SECTION = "\x1e"
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9._-]")

# Builds a tree from the current worktree in a scratch index: no lock contention
# with the agent's own git commands, and untracked-but-not-ignored files count.
_WRITE_WORKTREE_TREE = (
    "I=$(mktemp); export GIT_INDEX_FILE=$I; "
    "if git rev-parse --verify -q HEAD >/dev/null; then git read-tree HEAD; "
    "else git read-tree --empty; fi; "
    "git add -A . >/dev/null 2>&1; T=$(git write-tree); "
    "unset GIT_INDEX_FILE; rm -f $I"
)


def checkpoint_ref(turn_key: str) -> str:
    return f"refs/open-swe/turns/{_UNSAFE_KEY.sub('-', turn_key)[:100]}"


def _cd_repo(work_dir: str | None) -> str:
    roots = " ".join(
        shlex.quote(root) for root in ([work_dir] if work_dir else []) + ["/workspace"]
    )
    return (
        f'R=""; for w in {roots} "$PWD"; do for d in "$w" "$w"/*; do '
        'if [ -e "$d/.git" ]; then R="$d"; break 2; fi; done; done; '
        '[ -n "$R" ] || exit 3; cd "$R"'
    )


def _checkpoint_command(work_dir: str | None, ref: str) -> str:
    return (
        f"{_cd_repo(work_dir)}; {_WRITE_WORKTREE_TREE}; "
        "if git rev-parse --verify -q HEAD >/dev/null; then "
        'C=$(git commit-tree "$T" -p HEAD -m open-swe-turn); '
        'else C=$(git commit-tree "$T" -m open-swe-turn); fi; '
        f'git update-ref {shlex.quote(ref)} "$C" && echo "$C"'
    )


def _diff_command(work_dir: str | None, base: str, head: str | None) -> str:
    resolve_head = f"H={shlex.quote(head)}" if head else f"{_WRITE_WORKTREE_TREE}; H=$T"
    rng = f"{shlex.quote(base)} $H"
    return (
        f"{_cd_repo(work_dir)}; {resolve_head}; "
        f"git diff --numstat -z --no-renames {rng}; printf '{_SECTION}'; "
        f"git diff --name-status -z --no-renames {rng}; printf '{_SECTION}%s' \"$H\""
    )


def _contents_command(work_dir: str | None, base: str, head: str, paths: Sequence[str]) -> str:
    payload = base64.b64encode(
        json.dumps({"base": base, "head": head, "paths": list(paths)}).encode()
    ).decode()
    script = r"""python3 - <<'PY'
import base64, json, subprocess

S = json.loads(base64.b64decode('__PAYLOAD__').decode())
MAX = __MAX__
specs = [f'{S[side]}:{path}' for path in S['paths'] for side in ('base', 'head')]
proc = subprocess.run(
    ['git', 'cat-file', '--batch'],
    input=('\n'.join(specs) + '\n').encode(),
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
)
buf, at, blobs = proc.stdout, 0, []
for _ in specs:
    end = buf.find(b'\n', at)
    if end < 0:
        blobs.append(None)
        continue
    header, at = buf[at:end].decode(errors='replace').split(), end + 1
    if len(header) < 3:
        blobs.append(None)
        continue
    size = int(header[2])
    body, at = buf[at : at + size], at + size + 1
    blobs.append(base64.b64encode(body).decode() if size <= MAX else False)
print(json.dumps({
    path: {'base': blobs[i * 2], 'head': blobs[i * 2 + 1]}
    for i, path in enumerate(S['paths'])
}))
PY"""
    script = script.replace("__PAYLOAD__", payload).replace("__MAX__", str(_MAX_FILE_BYTES))
    return f"{_cd_repo(work_dir)}; {script}"


def _output(response: Any) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(response, Mapping):
        value = response.get("output")
        if isinstance(value, str):
            return value
    return str(response or "")


def _ok(response: Any) -> bool:
    exit_code = getattr(response, "exit_code", None)
    if not isinstance(exit_code, int) and isinstance(response, Mapping):
        exit_code = response.get("exit_code")
    return exit_code == 0 if isinstance(exit_code, int) else True


async def _execute(sandbox: Any, command: str, timeout: int) -> Any:
    return await sandbox.aexecute(command, timeout=timeout)


def parse_numstat(raw: str) -> list[tuple[str, int | None, int | None]]:
    """``git diff --numstat -z`` → ``(path, additions, deletions)``; ``None`` is binary."""
    stats: list[tuple[str, int | None, int | None]] = []
    for record in raw.split("\0"):
        parts = record.split("\t", 2)
        if len(parts) != 3 or not parts[2]:
            continue
        added, removed, path = parts
        stats.append(
            (
                path,
                None if added == "-" else int(added),
                None if removed == "-" else int(removed),
            )
        )
    return stats


def parse_name_status(raw: str) -> dict[str, str]:
    """``git diff --name-status -z`` → ``{path: added|removed|modified}``."""
    fields = [field for field in raw.split("\0") if field]
    kinds = {"A": "added", "D": "removed"}
    return {
        fields[i + 1]: kinds.get(fields[i][:1], "modified") for i in range(0, len(fields) - 1, 2)
    }


def _decode(value: Any) -> tuple[str | None, bool]:
    """``(content, unrenderable)`` for one side of a file's ``cat-file`` result."""
    if value is False:
        return None, True
    if not isinstance(value, str):
        return None, False
    try:
        return base64.b64decode(value).decode("utf-8"), False
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return None, True


def build_diff_files(
    numstat_raw: str,
    name_status_raw: str,
    contents: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    statuses = parse_name_status(name_status_raw)
    files: list[dict[str, Any]] = []
    for path, additions, deletions in parse_numstat(numstat_raw)[:_MAX_FILES]:
        sides = contents.get(path) if isinstance(contents, Mapping) else None
        sides = sides if isinstance(sides, Mapping) else {}
        original, original_bad = _decode(sides.get("base"))
        modified, modified_bad = _decode(sides.get("head"))
        files.append(
            {
                "path": path,
                "previousPath": None,
                "status": statuses.get(path, "modified"),
                "additions": additions or 0,
                "deletions": deletions or 0,
                "originalContent": original,
                "modifiedContent": modified,
                "unrenderable": additions is None or original_bad or modified_bad,
            }
        )
    return files


def merge_checkpoint(existing: Any, key: str, ref: str, started_at: str) -> list[dict[str, str]]:
    """Append a checkpoint to the thread's bounded list; the earliest wins per key.

    A resumed or re-dispatched run for the same user message must not move the
    turn's base forward — the first snapshot is the real turn start.
    """
    entries = [
        {
            "key": str(entry["key"]),
            "ref": str(entry.get("ref", "")),
            "started_at": str(entry.get("started_at", "")),
        }
        for entry in (existing if isinstance(existing, list) else [])
        if isinstance(entry, Mapping) and isinstance(entry.get("key"), str)
    ]
    if any(entry["key"] == key for entry in entries):
        return entries
    entries.append({"key": key, "ref": ref, "started_at": started_at})
    return entries[-MAX_CHECKPOINTS:]


async def record_turn_checkpoint(sandbox: Any, work_dir: str | None, turn_key: str) -> str | None:
    """Snapshot the worktree for ``turn_key``; returns the ref, or ``None``."""
    ref = checkpoint_ref(turn_key)
    try:
        response = await _execute(
            sandbox, _checkpoint_command(work_dir, ref), CHECKPOINT_TIMEOUT_SECONDS
        )
    except Exception:
        logger.debug("turn checkpoint failed for %s", turn_key, exc_info=True)
        return None
    if not _ok(response):
        logger.debug("turn checkpoint command failed for %s: %s", turn_key, _output(response))
        return None
    return ref


async def read_turn_diff(
    sandbox: Any, work_dir: str | None, base: str, head: str | None
) -> dict[str, Any]:
    """Files changed between ``base`` and ``head`` (or the live worktree)."""
    try:
        response = await _execute(
            sandbox, _diff_command(work_dir, base, head), DIFF_TIMEOUT_SECONDS
        )
    except Exception:
        logger.debug("turn diff failed for %s", base, exc_info=True)
        return {"status": "error", "files": [], "truncated": False}
    if not _ok(response):
        return {"status": "missing", "files": [], "truncated": False}

    sections = _output(response).split(_SECTION)
    if len(sections) != 3:
        return {"status": "error", "files": [], "truncated": False}
    numstat_raw, name_status_raw, head_tree = sections
    stats = parse_numstat(numstat_raw)
    paths = [path for path, _, _ in stats[:_MAX_FILES]]

    contents: Mapping[str, Any] = {}
    if paths:
        try:
            blobs = await _execute(
                sandbox,
                _contents_command(work_dir, base, head_tree.strip(), paths),
                DIFF_TIMEOUT_SECONDS,
            )
            decoded = json.loads(_output(blobs).strip().splitlines()[-1])
            contents = decoded if isinstance(decoded, dict) else {}
        except Exception:
            logger.debug("turn diff contents failed for %s", base, exc_info=True)

    return {
        "status": "ready",
        "files": build_diff_files(numstat_raw, name_status_raw, contents),
        "truncated": len(stats) > _MAX_FILES,
    }
