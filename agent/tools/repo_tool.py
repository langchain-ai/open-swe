"""Tool: ``repo``. Get repositories into the sandbox.

Cloning goes through this tool rather than a raw ``gh repo clone`` so that the
clone is recorded -- the ledger that picks which repos the nightly snapshot
bakes in is built from these calls. A thread may clone as many repos as it
needs; nothing here assumes one repo per thread.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.config import get_config

from ..dashboard.repo_clone_stats import record_repo_clone
from ..utils.repo_clone import build_clone_script, parse_clone_result
from ..utils.sandbox_paths import aresolve_sandbox_work_dir

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 300

RepoAction = Literal["clone"]


def _split_full_name(value: str) -> tuple[str, str]:
    cleaned = (value or "").strip().strip("/")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    owner, sep, name = cleaned.partition("/")
    if not sep or not owner or not name or "/" in name:
        return "", ""
    return owner, name


async def repo(
    action: RepoAction,
    repo: str,
    ref: str | None = None,
) -> dict[str, Any]:
    """Get a repository into the sandbox, ready to work in.

    Always use this instead of running ``git clone`` / ``gh repo clone`` yourself.
    Commonly used repos are pre-baked into the sandbox image, so this is usually
    near-instant; it works the same for a repo that isn't, just slower. Either
    way it fetches origin before returning.

    Safe to call again for a repo you already have — it refreshes the checkout
    instead of clobbering it, so use it to pull in new commits too. Call it once
    per repository you need; a task may span several.

    Args:
        action: ``clone``.
        repo: Repository as ``owner/name``.
        ref: Optional branch, tag, or commit sha to check out. Defaults to the
            repository's default branch.

    Returns:
        ``{success, path, source, fetched, head, repo}`` on success, where
        ``source`` is ``cache`` (pre-baked), ``github`` (network clone), or
        ``existing``. **``fetched: False`` means the fetch from origin failed and
        the checkout may be behind** — up to a day behind for a pre-baked repo.
        Re-run before trusting it for anything that depends on recent commits.
        On failure: ``{success: False, error}``.
    """
    owner, name = _split_full_name(repo)
    if not owner or not name:
        return {"success": False, "error": f"repo must be 'owner/name', got {repo!r}"}
    if action != "clone":
        return {"success": False, "error": f"unknown action {action!r}; use 'clone'"}

    try:
        config = get_config()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"Unable to read the current run config: {exc}"}

    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    from ..server import ensure_sandbox_for_thread

    try:
        sandbox_backend = await ensure_sandbox_for_thread(thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
    except Exception as exc:  # noqa: BLE001
        logger.warning("repo tool could not reach the sandbox", exc_info=True)
        return {"success": False, "error": f"sandbox unavailable: {exc}"}

    script = build_clone_script(work_dir=work_dir, owner=owner, name=name, ref=ref or "")

    try:
        result = await sandbox_backend.aexecute(script, timeout=CLONE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("repo %s failed for %s/%s", action, owner, name, exc_info=True)
        return {"success": False, "error": str(exc)}

    exit_code = getattr(result, "exit_code", None)
    output = getattr(result, "output", "") or ""
    if exit_code not in (0, None):
        return {"success": False, "error": output[-2000:] or f"exit code {exit_code}"}

    fields = parse_clone_result(output)
    if not fields.get("path"):
        return {"success": False, "error": output[-2000:] or "clone produced no result"}

    # Record only what actually landed on disk, so the nightly snapshot bakes in
    # repos that were really cloned rather than ones a run merely referenced.
    await record_repo_clone(owner, name)

    fetched = fields.get("fetched") == "true"
    if not fetched:
        logger.warning("repo clone for %s/%s could not fetch origin", owner, name)

    return {
        "success": True,
        "repo": f"{owner}/{name}",
        "path": fields["path"],
        "source": fields.get("source", "unknown"),
        "fetched": fetched,
        "head": fields.get("head", ""),
    }
