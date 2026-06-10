"""Deterministic repo prep for the reviewer sandbox.

The reviewer reviews a single PR, so we clone its repo and check out the PR
head during agent init -- before the first model call -- instead of asking the
LLM to narrate ``gh repo clone`` mid-run. Pre-cloning also lets ``SkillsMiddleware``
discover the repo's ``.agents/skills`` / ``.claude/skills`` from the real
sandbox filesystem at its one-shot ``before_agent`` scan.

Best-effort: any failure leaves the sandbox usable (the review still works off
the fetched diff) and returns ``False`` so callers can skip skill wiring.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
from collections.abc import Sequence

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 240

DEFAULT_SKILL_DIRS = (".agents/skills", ".claude/skills")


def _prep_command(work_dir: str, repo_owner: str, repo_name: str, head_sha: str) -> str:
    repo_dir = posixpath.join(work_dir, repo_name)
    q_work_dir = shlex.quote(work_dir)
    q_repo_dir = shlex.quote(repo_dir)
    q_full_name = shlex.quote(f"{repo_owner}/{repo_name}")
    q_repo_name = shlex.quote(repo_name)
    q_head = shlex.quote(head_sha) if head_sha else ""

    lines = [
        "set -e",
        f"if [ -d {q_repo_dir}/.git ]; then",
        f"  cd {q_repo_dir} && GH_TOKEN=dummy git fetch --all --quiet",
        "else",
        f"  cd {q_work_dir} && GH_TOKEN=dummy gh repo clone {q_full_name} && cd {q_repo_name}",
        "fi",
    ]
    if q_head:
        # Fetch the head sha explicitly (covers same-repo PRs); fall back
        # gracefully when it is not reachable (e.g. fork PRs) so skills still
        # load from the default branch.
        lines.append(f"GH_TOKEN=dummy git fetch origin {q_head} --quiet 2>/dev/null || true")
        lines.append(f"git checkout {q_head} --quiet 2>/dev/null || true")
    return "\n".join(lines)


async def prepare_review_repo(
    sandbox_backend: SandboxBackendProtocol,
    *,
    work_dir: str,
    repo_owner: str,
    repo_name: str,
    head_sha: str,
) -> bool:
    """Clone-or-fetch the repo and check out ``head_sha`` in the sandbox.

    Returns ``True`` when the repo is prepped at ``work_dir/repo_name``.
    """
    if not repo_owner or not repo_name:
        return False

    command = _prep_command(work_dir, repo_owner, repo_name, head_sha)
    try:
        result = await asyncio.to_thread(
            sandbox_backend.execute, command, timeout=CLONE_TIMEOUT_SECONDS
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to prep review repo %s/%s", repo_owner, repo_name, exc_info=True)
        return False

    exit_code = getattr(result, "exit_code", None)
    if exit_code not in (0, None):
        logger.warning(
            "Review repo prep for %s/%s exited %s: %s",
            repo_owner,
            repo_name,
            exit_code,
            getattr(result, "output", ""),
        )
        return False

    logger.info(
        "Prepped review repo %s/%s at %s (head=%s)",
        repo_owner,
        repo_name,
        posixpath.join(work_dir, repo_name),
        head_sha or "<none>",
    )
    return True


async def discover_skill_sources(
    sandbox_backend: SandboxBackendProtocol,
    *,
    repo_dir: str,
    skill_dirs: Sequence[str] = DEFAULT_SKILL_DIRS,
) -> list[str]:
    """Return the skill source dirs that actually exist under ``repo_dir``.

    Wiring SkillsMiddleware at a missing path produces noisy load warnings, so
    only return directories present on disk (with a trailing slash, as the
    middleware expects).
    """
    candidates = [posixpath.join(repo_dir, d) for d in skill_dirs]
    test = " ".join(f"[ -d {shlex.quote(p)} ] && echo {shlex.quote(p)};" for p in candidates)
    try:
        result = await asyncio.to_thread(sandbox_backend.execute, test)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to probe skill dirs under %s", repo_dir, exc_info=True)
        return []
    output = getattr(result, "output", "") or ""
    found = {line.strip() for line in output.splitlines() if line.strip()}
    return [f"{p}/" for p in candidates if p in found]
