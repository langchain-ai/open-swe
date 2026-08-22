"""Per-turn git checkpoints, and the diffs read back from them.

A turn is one run. At run start we snapshot the sandbox worktree into the object
DB under ``refs/open-swe/turns/<key>`` — without touching HEAD, the index, or the
worktree — so the dashboard can ask *git* what a turn changed instead of
replaying edit tool calls. A ref (not a bare tree) is used so an auto-``git gc``
mid-run cannot reap the snapshot; ``refs/open-swe/*`` is never pushed.

Both the snapshot and the read-back are best effort: on any failure the caller
gets ``None`` / ``status="error"`` and the UI degrades to "diff unavailable".
"""

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

MAX_TURN_DIFF_FILES = 200
_MAX_FILE_BYTES = 400_000
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


def plan_checkpoint_ref(turn_key: str) -> str:
    return f"{checkpoint_ref(turn_key)}-plan"


def _cd_repo(work_dir: str | None, repo_path: str | None = None) -> str:
    roots = " ".join(
        shlex.quote(root) for root in ([work_dir] if work_dir else []) + ["/workspace"]
    )
    preferred = shlex.quote(repo_path) if repo_path else '""'
    return (
        f"R={preferred}; "
        'if [ -z "$R" ] || [ ! -e "$R/.git" ]; then R=""; '
        f'for w in {roots} "$PWD"; do for d in "$w" "$w"/*; do '
        'if [ -e "$d/.git" ]; then R="$d"; break 2; fi; done; done; fi; '
        '[ -n "$R" ] || exit 3; cd "$R"'
    )


def _checkpoint_command(work_dir: str | None, ref: str, repo_path: str | None = None) -> str:
    quoted_ref = shlex.quote(ref)
    return (
        f"{_cd_repo(work_dir, repo_path)}; R=$(git rev-parse --show-toplevel); "
        f"if C=$(git rev-parse --verify -q {quoted_ref}); then :; else "
        f"{_WRITE_WORKTREE_TREE}; "
        "if git rev-parse --verify -q HEAD >/dev/null; then "
        'C=$(git commit-tree "$T" -p HEAD -m open-swe-turn); '
        'else C=$(git commit-tree "$T" -m open-swe-turn); fi; '
        f'git update-ref {quoted_ref} "$C" || exit; fi; printf \'%s\\n%s\' "$C" "$R"'
    )


def _diff_command(
    work_dir: str | None,
    base: str,
    head: str | None,
    max_files: int,
    repo_path: str | None = None,
) -> str:
    resolve_head = f"H={shlex.quote(head)}" if head else f"{_WRITE_WORKTREE_TREE}; H=$T"
    resolve_trees = (
        f"B_INPUT={shlex.quote(base)}; "
        'if B=$(git rev-parse --verify "${B_INPUT}^{tree}" 2>/dev/null); then :; '
        'elif [ "$B_INPUT" = HEAD ]; then B=$(git hash-object -t tree /dev/null); '
        "else exit 4; fi; "
        'H=$(git rev-parse --verify "${H}^{tree}" 2>/dev/null) || exit 4'
    )
    script = r"""python3 - "$B" "$H" __MAX_FILES__ <<'PY'
import json, subprocess, sys

base, head, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
numstat = subprocess.run(
    ['git', 'diff', '--numstat', '-z', '--no-renames', base, head],
    check=True,
    stdout=subprocess.PIPE,
).stdout
name_status = subprocess.run(
    ['git', 'diff', '--name-status', '-z', '--no-renames', base, head],
    check=True,
    stdout=subprocess.PIPE,
).stdout
records = [record for record in numstat.split(b'\0') if record]
fields = [field for field in name_status.split(b'\0') if field]
additions = deletions = 0
for record in records:
    parts = record.split(b'\t', 2)
    if len(parts) != 3:
        continue
    if parts[0].isdigit():
        additions += int(parts[0])
    if parts[1].isdigit():
        deletions += int(parts[1])
print(json.dumps({
    'base': base,
    'head': head,
    'numstat': (b'\0'.join(records[:limit]) + (b'\0' if records else b'')).decode(errors='replace'),
    'nameStatus': (b'\0'.join(fields[:limit * 2]) + (b'\0' if fields else b'')).decode(errors='replace'),
    'summary': {'files': len(records), 'additions': additions, 'deletions': deletions},
}))
PY"""
    script = script.replace("__MAX_FILES__", str(max_files))
    return f"{_cd_repo(work_dir, repo_path)}; {resolve_head}; {resolve_trees}; {script}"


def _contents_command(
    work_dir: str | None,
    base: str,
    head: str,
    paths: Sequence[str],
    repo_path: str | None = None,
) -> str:
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
    return f"{_cd_repo(work_dir, repo_path)}; {script}"


def response_output(response: Any) -> str:
    """A sandbox response's stdout, whichever shape the provider returned it in."""
    output = getattr(response, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(response, Mapping):
        value = response.get("output")
        if isinstance(value, str):
            return value
    return str(response or "")


def response_ok(response: Any) -> bool:
    """Whether the command succeeded; a response with no exit code counts as success."""
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
    for path, additions, deletions in parse_numstat(numstat_raw)[:MAX_TURN_DIFF_FILES]:
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


def _checkpoint_entries(existing: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for value in existing if isinstance(existing, list) else []:
        if not isinstance(value, Mapping) or not isinstance(value.get("key"), str):
            continue
        entry: dict[str, Any] = {
            "key": str(value["key"]),
            "ref": str(value.get("ref", "")),
            "started_at": str(value.get("started_at", "")),
        }
        if isinstance(value.get("repo_path"), str) and value["repo_path"]:
            entry["repo_path"] = value["repo_path"]
        if isinstance(value.get("plan_ref"), str) and value["plan_ref"]:
            entry["plan_ref"] = value["plan_ref"]
        if value.get("plan_mode") is True:
            entry["plan_mode"] = True
        entries.append(entry)
    return entries


def merge_checkpoint(
    existing: Any,
    key: str,
    ref: str,
    started_at: str,
    *,
    repo_path: str | None = None,
    plan_mode: bool = False,
) -> list[dict[str, Any]]:
    """Append a checkpoint to the thread's bounded list; the earliest wins per key."""
    entries = _checkpoint_entries(existing)
    if any(entry["key"] == key for entry in entries):
        return entries
    entry: dict[str, Any] = {"key": key, "ref": ref, "started_at": started_at}
    if repo_path:
        entry["repo_path"] = repo_path
    if plan_mode:
        entry["plan_mode"] = True
        entry["plan_ref"] = ref
    entries.append(entry)
    return entries[-MAX_CHECKPOINTS:]


def mark_checkpoint_plan_mode(
    existing: Any, key: str, plan_ref: str | None = None
) -> list[dict[str, Any]]:
    """Mark one existing turn checkpoint as read-only planning."""
    entries = _checkpoint_entries(existing)
    for entry in entries:
        if entry["key"] == key:
            entry["plan_mode"] = True
            entry["plan_ref"] = plan_ref or entry["ref"]
            break
    return entries


async def record_plan_checkpoint(
    sandbox: Any,
    work_dir: str | None,
    turn_key: str,
    *,
    repo_path: str | None = None,
) -> str | None:
    """Snapshot the worktree where a turn enters plan mode."""
    ref = plan_checkpoint_ref(turn_key)
    try:
        response = await _execute(
            sandbox,
            _checkpoint_command(work_dir, ref, repo_path),
            CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("plan checkpoint failed for %s", turn_key, exc_info=True)
        return None
    return ref if response_ok(response) else None


async def record_turn_checkpoint(
    sandbox: Any,
    work_dir: str | None,
    turn_key: str,
    *,
    repo_path: str | None = None,
) -> tuple[str, str] | None:
    """Snapshot the worktree for ``turn_key``; returns its ref and repository."""
    ref = checkpoint_ref(turn_key)
    try:
        response = await _execute(
            sandbox,
            _checkpoint_command(work_dir, ref, repo_path),
            CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("turn checkpoint failed for %s", turn_key, exc_info=True)
        return None
    if not response_ok(response):
        logger.debug(
            "turn checkpoint command failed for %s: %s", turn_key, response_output(response)
        )
        return None
    lines = response_output(response).strip().splitlines()
    if len(lines) < 2 or not lines[-2] or not lines[-1].startswith("/"):
        logger.debug("turn checkpoint returned invalid output for %s: %s", turn_key, lines)
        return None
    return ref, lines[-1]


async def read_turn_diff(
    sandbox: Any,
    work_dir: str | None,
    base: str,
    head: str | None,
    *,
    max_files: int = MAX_TURN_DIFF_FILES,
    include_content: bool = True,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Files changed between ``base`` and ``head`` (or the live worktree)."""
    max_files = max(1, min(max_files, MAX_TURN_DIFF_FILES))
    empty_summary = {"files": 0, "additions": 0, "deletions": 0}
    try:
        response = await _execute(
            sandbox,
            _diff_command(work_dir, base, head, max_files, repo_path),
            DIFF_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("turn diff failed for %s", base, exc_info=True)
        return {
            "status": "error",
            "files": [],
            "truncated": False,
            "summary": empty_summary,
        }
    if not response_ok(response):
        return {
            "status": "missing",
            "files": [],
            "truncated": False,
            "summary": empty_summary,
        }

    try:
        payload = json.loads(response_output(response).strip().splitlines()[-1])
        if not isinstance(payload, Mapping):
            raise TypeError
        summary_payload = payload["summary"]
        if not isinstance(summary_payload, Mapping):
            raise TypeError
        summary = {
            key: max(0, int(summary_payload[key])) for key in ("files", "additions", "deletions")
        }
        numstat_raw = payload["numstat"]
        name_status_raw = payload["nameStatus"]
        base_tree = payload["base"]
        head_tree = payload["head"]
        if not all(
            isinstance(value, str) for value in (numstat_raw, name_status_raw, base_tree, head_tree)
        ):
            raise TypeError
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "status": "error",
            "files": [],
            "truncated": False,
            "summary": empty_summary,
        }

    stats = parse_numstat(numstat_raw)
    paths = [path for path, _, _ in stats]
    contents: Mapping[str, Any] = {}
    if include_content and paths:
        try:
            blobs = await _execute(
                sandbox,
                _contents_command(work_dir, base_tree, head_tree, paths, repo_path),
                DIFF_TIMEOUT_SECONDS,
            )
            decoded = json.loads(response_output(blobs).strip().splitlines()[-1])
            contents = decoded if isinstance(decoded, dict) else {}
        except Exception:
            logger.debug("turn diff contents failed for %s", base, exc_info=True)

    files = build_diff_files(numstat_raw, name_status_raw, contents)
    return {
        "status": "ready",
        "files": files,
        "truncated": summary["files"] > len(files),
        "summary": summary,
    }
