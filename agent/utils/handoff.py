"""CLI handoff state-envelope helpers.

The handoff bundle moves an in-flight run between local and cloud sandboxes.
See cli/DESIGN.md "Handoff" for the envelope shape.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shlex
from datetime import UTC, datetime
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

# 5 MB bundle ceiling (DESIGN.md: "A handoff that exceeds 5 MB is rejected").
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
# Skip embedding untracked files larger than this; agent can re-fetch.
MAX_UNTRACKED_FILE_BYTES = 256 * 1024
# Hard timeout (seconds) for cooperative pause before returning 409.
PAUSE_TIMEOUT_SECONDS = 30.0
PAUSE_POLL_INTERVAL = 1.0


def _stdout_from_result(result: object) -> str:
    """Best-effort extraction of stdout from a sandbox execute() result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("stdout", "output", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    for attr in ("output", "stdout", "text"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _exit_code(result: object) -> int | None:
    if isinstance(result, dict):
        ec = result.get("exit_code")
        return ec if isinstance(ec, int) else None
    ec = getattr(result, "exit_code", None)
    return ec if isinstance(ec, int) else None


async def _exec(sandbox: SandboxBackendProtocol, command: str) -> tuple[int | None, str]:
    result = await asyncio.to_thread(sandbox.execute, command)
    return _exit_code(result), _stdout_from_result(result)


async def _find_repo_dir(sandbox: SandboxBackendProtocol, work_dir: str) -> str | None:
    """Find a git checkout inside ``work_dir`` (one level deep)."""
    safe = shlex.quote(work_dir)
    cmd = (
        f"for d in {safe}/*/; do "
        f'  if [ -d "$d/.git" ]; then printf "%s" "${{d%/}}"; exit 0; fi; '
        f"done; exit 1"
    )
    ec, out = await _exec(sandbox, cmd)
    if ec == 0 and out.strip():
        return out.strip()
    return None


async def _read_untracked_file(
    sandbox: SandboxBackendProtocol, repo_dir: str, rel_path: str
) -> dict[str, Any] | None:
    """Read an untracked file from the sandbox. Returns None if too large."""
    safe_repo = shlex.quote(repo_dir)
    safe_rel = shlex.quote(rel_path)
    # Check size first.
    ec, out = await _exec(
        sandbox, f"cd {safe_repo} && stat -c '%s' {safe_rel} 2>/dev/null || wc -c < {safe_rel}"
    )
    try:
        size = int(out.strip().split()[0]) if out.strip() else 0
    except (ValueError, IndexError):
        size = 0
    if size > MAX_UNTRACKED_FILE_BYTES:
        return {
            "path": rel_path,
            "skipped": True,
            "reason": f"file too large ({size} bytes)",
        }

    ec, b64_out = await _exec(sandbox, f"cd {safe_repo} && base64 < {safe_rel}")
    if ec != 0:
        return None

    raw = base64.b64decode(b64_out.replace("\n", "").replace(" ", ""), validate=False)
    try:
        text = raw.decode("utf-8")
        return {"path": rel_path, "content": text, "encoding": "utf-8"}
    except UnicodeDecodeError:
        return {
            "path": rel_path,
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        }


async def build_git_state(sandbox: SandboxBackendProtocol, work_dir: str) -> dict[str, Any]:
    """Capture the sandbox repo's git state for a handoff bundle.

    TODO(handoff): verify against real LangSmith sandbox — the shell helpers
    here assume bash/coreutils availability and a single git checkout one
    directory below ``work_dir``.
    """
    repo_dir = await _find_repo_dir(sandbox, work_dir)
    if not repo_dir:
        raise RuntimeError(f"No git checkout found under {work_dir}")

    safe_repo = shlex.quote(repo_dir)

    _, remote_url = await _exec(sandbox, f"cd {safe_repo} && git remote get-url origin")
    _, branch = await _exec(sandbox, f"cd {safe_repo} && git rev-parse --abbrev-ref HEAD")
    _, head_sha = await _exec(sandbox, f"cd {safe_repo} && git rev-parse HEAD")
    _, diff = await _exec(sandbox, f"cd {safe_repo} && git diff HEAD")
    _, untracked_list = await _exec(
        sandbox, f"cd {safe_repo} && git ls-files --others --exclude-standard"
    )

    untracked_files: list[dict[str, Any]] = []
    for rel_path in untracked_list.splitlines():
        rel_path = rel_path.strip()
        if not rel_path:
            continue
        entry = await _read_untracked_file(sandbox, repo_dir, rel_path)
        if entry is not None:
            untracked_files.append(entry)

    return {
        "repo_dir": repo_dir,
        "remote_url": remote_url.strip(),
        "branch": branch.strip(),
        "head_sha": head_sha.strip(),
        "uncommitted_diff": diff,
        "untracked_files": untracked_files,
    }


async def wait_for_run_pause(
    client: Any,
    thread_id: str,
    timeout_seconds: float = PAUSE_TIMEOUT_SECONDS,
) -> bool:
    """Cooperatively pause the run: send interrupt, then wait until idle.

    Returns True if the thread settled to a non-busy state within ``timeout``.

    TODO(handoff): verify against real run — relies on thread.status flipping
    away from "busy" after `runs.cancel(action=interrupt)`.
    """
    try:
        runs = await client.runs.list(thread_id, limit=1)
    except Exception:
        logger.exception("Failed to list runs for thread %s during pause", thread_id)
        runs = []
    run_id: str | None = None
    if runs:
        first = runs[0]
        if isinstance(first, dict):
            rid = first.get("run_id")
            if isinstance(rid, str):
                run_id = rid

    if run_id:
        try:
            await client.runs.cancel(thread_id, run_id, action="interrupt")
        except Exception:
            logger.exception("Failed to send interrupt to %s/%s", thread_id, run_id)

    elapsed = 0.0
    while elapsed < timeout_seconds:
        try:
            thread = await client.threads.get(thread_id)
        except Exception:
            logger.exception("Failed to poll thread %s during pause", thread_id)
            return False
        status = thread.get("status") if isinstance(thread, dict) else None
        if status != "busy":
            return True
        await asyncio.sleep(PAUSE_POLL_INTERVAL)
        elapsed += PAUSE_POLL_INTERVAL
    return False


async def fetch_thread_conversation(client: Any, thread_id: str) -> tuple[list[Any], list[Any]]:
    """Fetch the message history and any pending-queue items for a thread."""
    messages: list[Any] = []
    try:
        state = await client.threads.get_state(thread_id)
        values = state.get("values") if isinstance(state, dict) else None
        if isinstance(values, dict):
            msgs = values.get("messages")
            if isinstance(msgs, list):
                messages = msgs
    except Exception:
        logger.exception("Failed to fetch thread state for %s", thread_id)

    pending: list[Any] = []
    try:
        item = await client.store.get_item(("queue", thread_id), "pending_messages")
        if item and isinstance(item, dict):
            value = item.get("value") or {}
            if isinstance(value, dict):
                msgs = value.get("messages")
                if isinstance(msgs, list):
                    pending = msgs
    except Exception:
        logger.debug("No pending queue for thread %s", thread_id)

    return messages, pending


def build_bundle(
    *,
    thread_id: str,
    source: str,
    conversation: list[Any],
    pending_queue: list[Any],
    git_state: dict[str, Any],
    agent_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "source": source,
        "taken_at": datetime.now(tz=UTC).isoformat(),
        "conversation": conversation,
        "pending_queue": pending_queue,
        "git": {
            "remote_url": git_state.get("remote_url", ""),
            "branch": git_state.get("branch", ""),
            "head_sha": git_state.get("head_sha", ""),
            "uncommitted_diff": git_state.get("uncommitted_diff", ""),
            "untracked_files": git_state.get("untracked_files", []),
        },
        "agent": agent_meta or {},
    }


def bundle_size_bytes(bundle: dict[str, Any]) -> int:
    return len(json.dumps(bundle, default=str).encode("utf-8"))


def validate_bundle_shape(bundle: Any) -> tuple[bool, str]:
    """Return (ok, error). Required envelope fields per DESIGN.md."""
    if not isinstance(bundle, dict):
        return False, "Bundle must be a JSON object"
    for key in ("source", "conversation", "git"):
        if key not in bundle:
            return False, f"Missing required field: {key}"
    if bundle.get("source") not in ("local", "cloud"):
        return False, "source must be 'local' or 'cloud'"
    if not isinstance(bundle.get("conversation"), list):
        return False, "conversation must be a list"
    git = bundle.get("git")
    if not isinstance(git, dict):
        return False, "git must be an object"
    for key in ("remote_url", "branch", "head_sha"):
        v = git.get(key)
        if not isinstance(v, str) or not v:
            return False, f"git.{key} is required"
    if "uncommitted_diff" in git and not isinstance(git.get("uncommitted_diff"), str):
        return False, "git.uncommitted_diff must be a string"
    if "untracked_files" in git and not isinstance(git.get("untracked_files"), list):
        return False, "git.untracked_files must be a list"
    return True, ""


def parse_repo_from_remote(remote_url: str) -> dict[str, str] | None:
    """Parse owner/name from a GitHub remote URL (https or ssh)."""
    url = remote_url.strip()
    if not url:
        return None
    if url.endswith(".git"):
        url = url[:-4]
    # git@github.com:owner/repo
    if url.startswith("git@"):
        _, _, path = url.partition(":")
    else:
        # https://github.com/owner/repo
        for prefix in ("https://", "http://", "ssh://git@", "git://"):
            if url.startswith(prefix):
                url = url[len(prefix) :]
                break
        # strip host
        _, _, path = url.partition("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return {"owner": parts[0], "name": parts[1]}


async def apply_bundle_to_sandbox(
    sandbox: SandboxBackendProtocol,
    work_dir: str,
    bundle: dict[str, Any],
    github_token: str | None,
) -> None:
    """Materialize a bundle into a freshly-created cloud sandbox.

    TODO(handoff): verify end-to-end against a real LangSmith sandbox. The
    GitHub-proxy auth (configured in server._create_sandbox_with_proxy) should
    let `gh repo clone` work without a real token in the sandbox, but the
    fallback uses ``github_token`` directly if provided.
    """
    git = bundle.get("git") or {}
    remote_url = git.get("remote_url", "")
    head_sha = git.get("head_sha", "")
    diff = git.get("uncommitted_diff", "") or ""
    untracked = git.get("untracked_files") or []

    repo_info = parse_repo_from_remote(remote_url)
    if not repo_info:
        raise ValueError(f"Could not parse remote_url: {remote_url!r}")
    repo_dir = f"{work_dir.rstrip('/')}/{repo_info['name']}"
    safe_repo = shlex.quote(repo_dir)
    safe_work = shlex.quote(work_dir)

    # Ensure work_dir exists. Real LangSmith sandboxes seed it at boot, but
    # other providers might not — fail loudly here rather than letting the
    # clone fail with a cryptic message.
    ec, out = await _exec(sandbox, f"mkdir -p {safe_work} && [ -d {safe_work} ]")
    if ec not in (0, None):
        raise RuntimeError(f"work_dir {work_dir!r} not usable: {out[:500]}")

    # Clone via gh (proxy handles auth in langsmith).
    clone_cmd = (
        f"cd {safe_work} && "
        f"GH_TOKEN={shlex.quote(github_token or 'dummy')} "
        f"gh repo clone {shlex.quote(repo_info['owner'] + '/' + repo_info['name'])}"
    )
    ec, out = await _exec(sandbox, clone_cmd)
    if ec not in (0, None):
        raise RuntimeError(f"git clone failed (exit {ec}): {out[:500]}")

    if head_sha:
        ec, out = await _exec(
            sandbox, f"cd {safe_repo} && git fetch --all && git checkout {shlex.quote(head_sha)}"
        )
        if ec not in (0, None):
            raise RuntimeError(f"git checkout {head_sha} failed: {out[:500]}")

    if diff.strip():
        # Pipe the diff via stdin using a here-doc — base64-encode to keep
        # quoting safe.
        diff_b64 = base64.b64encode(diff.encode("utf-8")).decode("ascii")
        apply_cmd = f"cd {safe_repo} && echo {shlex.quote(diff_b64)} | base64 -d | git apply -"
        ec, out = await _exec(sandbox, apply_cmd)
        if ec not in (0, None):
            raise RuntimeError(f"git apply failed: {out[:500]}")

    for entry in untracked:
        if not isinstance(entry, dict) or entry.get("skipped"):
            continue
        rel_path = entry.get("path")
        content = entry.get("content", "")
        encoding = entry.get("encoding", "utf-8")
        if not isinstance(rel_path, str) or not rel_path:
            continue
        # Reject paths that would escape the repo dir. The bundle is
        # untrusted (anyone with a CLI session can POST it to /cli/runs/adopt),
        # so "../../etc/passwd"-style entries must not write outside repo_dir.
        if _is_unsafe_path(rel_path):
            logger.warning("Skipping unsafe untracked path in bundle: %r", rel_path)
            continue
        if encoding == "utf-8":
            raw = content.encode("utf-8")
        else:
            raw = base64.b64decode(content)
        b64 = base64.b64encode(raw).decode("ascii")
        safe_rel = shlex.quote(rel_path)
        write_cmd = (
            f'cd {safe_repo} && mkdir -p "$(dirname {safe_rel})" && '
            f"echo {shlex.quote(b64)} | base64 -d > {safe_rel}"
        )
        ec, out = await _exec(sandbox, write_cmd)
        if ec not in (0, None):
            raise RuntimeError(f"write untracked {rel_path} failed: {out[:500]}")


def _is_unsafe_path(rel_path: str) -> bool:
    """Reject absolute paths or paths whose normalized form escapes ``.``."""
    if not rel_path or rel_path.startswith("/"):
        return True
    parts = rel_path.replace("\\", "/").split("/")
    depth = 0
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        else:
            depth += 1
    return False
