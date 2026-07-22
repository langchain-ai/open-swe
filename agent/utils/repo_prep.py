"""Deterministic repo prep for the reviewer sandbox.

The reviewer reviews a single PR, so we clone its repo and check out the PR
head during agent init -- before the first model call -- instead of asking the
LLM to narrate ``gh repo clone`` mid-run. Pre-cloning also lets ``SkillsMiddleware``
discover the repo's ``.agents/skills`` / ``.claude/skills`` at its one-shot
``before_agent`` scan.

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

TRUSTED_SKILLS_DIRNAME = ".review-skills"


def _prep_command(
    work_dir: str,
    repo_owner: str,
    repo_name: str,
    head_sha: str,
    pr_number: int | None,
    base_sha: str,
) -> str:
    repo_dir = posixpath.join(work_dir, repo_name)
    q_work_dir = shlex.quote(work_dir)
    q_repo_dir = shlex.quote(repo_dir)
    q_full_name = shlex.quote(f"{repo_owner}/{repo_name}")
    q_repo_name = shlex.quote(repo_name)
    q_head = shlex.quote(head_sha) if head_sha else ""

    lines = [
        "set -e",
        f"if [ -d {q_repo_dir}/.git ]; then",
        # Tolerate fetch-all failures: the targeted head/base fetches below
        # are what the checkout actually needs.
        f"  cd {q_repo_dir} && {{ GH_TOKEN=dummy git fetch --all --quiet || true; }}",
        "else",
        f"  cd {q_work_dir} && GH_TOKEN=dummy gh repo clone {q_full_name} && cd {q_repo_name}",
        "fi",
    ]
    if base_sha:
        q_base = shlex.quote(base_sha)
        lines.append(f"GH_TOKEN=dummy git fetch origin {q_base} --quiet 2>/dev/null || true")
    if q_head:
        # Direct sha fetch covers same-repo PRs; the pull ref covers fork PRs
        # whose head commit is not reachable from origin's branches.
        lines.append(f"GH_TOKEN=dummy git fetch origin {q_head} --quiet 2>/dev/null || true")
        if pr_number is not None:
            pull_ref = shlex.quote(f"refs/pull/{pr_number}/head")
            lines.append(f"GH_TOKEN=dummy git fetch origin {pull_ref} --quiet 2>/dev/null || true")
        # --force: a reused sandbox can have a dirty worktree from a previous
        # run, which would otherwise block the checkout and silently leave the
        # tree at the old head. Strict on purpose: a failed checkout must fail
        # the prep so callers know the tree is NOT at the PR head.
        lines.append(f"git checkout --force {q_head} --quiet")
        lines.append(f'[ "$(git rev-parse HEAD)" = {q_head} ]')
    return "\n".join(lines)


async def prepare_review_repo(
    sandbox_backend: SandboxBackendProtocol,
    *,
    work_dir: str,
    repo_owner: str,
    repo_name: str,
    head_sha: str,
    pr_number: int | None = None,
    base_sha: str = "",
) -> bool:
    """Clone-or-fetch the repo and check out ``head_sha`` in the sandbox.

    Returns ``True`` only when the repo is prepped at ``work_dir/repo_name``
    and (when ``head_sha`` is given) actually checked out at the PR head.
    """
    if not repo_owner or not repo_name:
        return False

    command = _prep_command(work_dir, repo_owner, repo_name, head_sha, pr_number, base_sha)
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


async def materialize_trusted_skills(
    sandbox_backend: SandboxBackendProtocol,
    *,
    repo_dir: str,
    trusted_ref: str,
    skill_dirs: Sequence[str] = DEFAULT_SKILL_DIRS,
) -> list[str]:
    """Extract skill dirs from ``trusted_ref`` into a path outside the checkout.

    Skills are sourced from the PR's base sha -- never the PR head, which the
    PR author controls -- so a PR cannot inject instructions into the reviewer
    prompt by adding or editing a ``SKILL.md``. Returns the extracted source
    dirs (with a trailing slash, as SkillsMiddleware expects).
    """
    if not trusted_ref:
        return []
    dest_root = posixpath.join(posixpath.dirname(repo_dir), TRUSTED_SKILLS_DIRNAME)
    q_repo_dir = shlex.quote(repo_dir)
    q_ref = shlex.quote(trusted_ref)

    sources: list[str] = []
    for skill_dir in skill_dirs:
        dest = posixpath.join(dest_root, skill_dir)
        q_dest = shlex.quote(dest)
        q_dir = shlex.quote(skill_dir)
        depth = len(skill_dir.split("/"))
        command = (
            f"cd {q_repo_dir} && "
            f"git cat-file -e {q_ref}:{q_dir} 2>/dev/null && "
            f"rm -rf {q_dest} && mkdir -p {q_dest} && "
            f"git archive {q_ref} {q_dir} | tar -x --strip-components={depth} -C {q_dest} && "
            f"echo {q_dest}"
        )
        try:
            result = await asyncio.to_thread(sandbox_backend.execute, command)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to extract trusted skills %s", skill_dir, exc_info=True)
            continue
        output = getattr(result, "output", "") or ""
        if dest in output.splitlines():
            sources.append(f"{dest}/")
    return sources
