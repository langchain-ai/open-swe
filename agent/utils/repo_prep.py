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
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 240

DEFAULT_SKILL_DIRS = (".agents/skills", ".claude/skills")

TRUSTED_SKILLS_DIRNAME = ".review-skills"
MAIN_AGENT_SKILLS_DIRNAME = ".agent-skills"

_REPO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_TRUSTED_REF_PREFIX = "OPEN_SWE_TRUSTED_REF="
_SKILLS_SOURCE_PREFIX = "OPEN_SWE_SKILLS_SOURCE="
_SKILLS_CACHE_PREFIX = "OPEN_SWE_SKILLS_CACHE="


@dataclass(frozen=True)
class PreparedRepoSkills:
    trusted_ref: str = ""
    sources: tuple[str, ...] = ()


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
    dest_dirname: str = TRUSTED_SKILLS_DIRNAME,
) -> list[str]:
    """Extract skill dirs from ``trusted_ref`` into a path outside the checkout.

    Skills are sourced from the PR's base sha -- never the PR head, which the
    PR author controls -- so a PR cannot inject instructions into the reviewer
    prompt by adding or editing a ``SKILL.md``. Returns the extracted source
    dirs (with a trailing slash, as SkillsMiddleware expects).
    """
    if not trusted_ref:
        return []
    dest_root = posixpath.join(posixpath.dirname(repo_dir), dest_dirname)
    q_repo_dir = shlex.quote(repo_dir)
    q_ref = shlex.quote(trusted_ref)

    sources: list[str] = []
    for skill_dir in skill_dirs:
        dest = posixpath.join(dest_root, skill_dir)
        q_dest = shlex.quote(dest)
        marker = posixpath.join(dest, ".trusted-ref")
        q_marker = shlex.quote(marker)
        q_dir = shlex.quote(skill_dir)
        depth = len(skill_dir.split("/"))
        command = (
            "set -e\n"
            f"cd {q_repo_dir}\n"
            f"if ! git cat-file -e {q_ref}:{q_dir} 2>/dev/null; then\n"
            f"  rm -rf {q_dest}\n"
            "  exit 0\n"
            "fi\n"
            f'if [ -d {q_dest} ] && [ "$(cat {q_marker} 2>/dev/null || true)" = {q_ref} ]; then\n'
            f"  printf '{_SKILLS_CACHE_PREFIX}hit\\n'\n"
            "else\n"
            f"  rm -rf {q_dest} && mkdir -p {q_dest}\n"
            f"  git archive {q_ref} {q_dir} | "
            f"tar -x --strip-components={depth} -C {q_dest}\n"
            f"  printf '%s\\n' {q_ref} > {q_marker}\n"
            f"  printf '{_SKILLS_CACHE_PREFIX}miss\\n'\n"
            "fi\n"
            f"chmod -R a-w {q_dest}\n"
            f"printf '{_SKILLS_SOURCE_PREFIX}%s\\n' {q_dest}"
        )
        try:
            result = await asyncio.to_thread(sandbox_backend.execute, command)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to extract trusted skills %s", skill_dir, exc_info=True)
            continue
        output = getattr(result, "output", "") or ""
        lines = output.splitlines()
        if f"{_SKILLS_SOURCE_PREFIX}{dest}" in lines:
            sources.append(f"{dest}/")
            cache_status = next(
                (
                    line.removeprefix(_SKILLS_CACHE_PREFIX)
                    for line in lines
                    if line.startswith(_SKILLS_CACHE_PREFIX)
                ),
                "unknown",
            )
            logger.info(
                "Prepared trusted skill source repo=%s dir=%s cache=%s",
                posixpath.basename(repo_dir),
                skill_dir,
                cache_status,
            )
    return sources


def _valid_repo_component(value: str) -> bool:
    return value not in {".", ".."} and bool(_REPO_COMPONENT_RE.fullmatch(value))


async def prepare_main_agent_repo_skills(
    sandbox_backend: SandboxBackendProtocol,
    *,
    work_dir: str,
    repo_owner: str,
    repo_name: str,
    base_sha: str = "",
) -> PreparedRepoSkills:
    """Prepare a configured repo and its trusted ``.agents/skills`` snapshot."""
    if not _valid_repo_component(repo_owner) or not _valid_repo_component(repo_name):
        logger.warning("Skipping repository skill preparation: invalid repository identifier")
        return PreparedRepoSkills()
    if base_sha and not _COMMIT_SHA_RE.fullmatch(base_sha):
        logger.warning(
            "Skipping repository skill preparation for %s/%s: invalid base ref",
            repo_owner,
            repo_name,
        )
        return PreparedRepoSkills()

    repo_dir = posixpath.join(work_dir, repo_name)
    full_name = f"{repo_owner}/{repo_name}"
    canonical_url = f"https://github.com/{full_name}.git"
    q_work_dir = shlex.quote(work_dir)
    q_repo_dir = shlex.quote(repo_dir)
    q_full_name = shlex.quote(full_name)
    q_repo_name = shlex.quote(repo_name)
    q_canonical_url = shlex.quote(canonical_url)
    lines = [
        "set -e",
        f"mkdir -p {q_work_dir}",
        f"if [ -d {q_repo_dir}/.git ]; then",
        f"  cd {q_repo_dir}",
        f"  git remote set-url origin {q_canonical_url}",
        "else",
        f"  cd {q_work_dir}",
        f"  GH_TOKEN=dummy gh repo clone {q_full_name} {q_repo_name} -- --quiet",
        f"  cd {q_repo_dir}",
        "fi",
    ]
    if base_sha:
        q_base_sha = shlex.quote(base_sha)
        lines.extend(
            [
                f"GH_TOKEN=dummy git fetch origin {q_base_sha} --quiet",
                f"trusted_ref=$(git rev-parse --verify {q_base_sha}^{{commit}})",
            ]
        )
    else:
        lines.extend(
            [
                "GH_TOKEN=dummy git fetch origin HEAD --quiet",
                "trusted_ref=$(git rev-parse --verify FETCH_HEAD^{commit})",
            ]
        )
    lines.append(f"printf '{_TRUSTED_REF_PREFIX}%s\\n' \"$trusted_ref\"")

    try:
        result = await asyncio.to_thread(
            sandbox_backend.execute,
            "\n".join(lines),
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to prepare repository skills for %s/%s", repo_owner, repo_name)
        return PreparedRepoSkills()
    if getattr(result, "exit_code", None) not in (0, None):
        logger.warning("Repository skill preparation failed for %s/%s", repo_owner, repo_name)
        return PreparedRepoSkills()

    output = getattr(result, "output", "") or ""
    trusted_ref = next(
        (
            line.removeprefix(_TRUSTED_REF_PREFIX)
            for line in output.splitlines()
            if line.startswith(_TRUSTED_REF_PREFIX)
        ),
        "",
    )
    if not _COMMIT_SHA_RE.fullmatch(trusted_ref):
        logger.warning(
            "Repository skill preparation returned no trusted ref for %s/%s", repo_owner, repo_name
        )
        return PreparedRepoSkills()

    sources = await materialize_trusted_skills(
        sandbox_backend,
        repo_dir=repo_dir,
        trusted_ref=trusted_ref,
        skill_dirs=(".agents/skills",),
        dest_dirname=posixpath.join(MAIN_AGENT_SKILLS_DIRNAME, repo_owner, repo_name),
    )
    logger.info(
        "Repository skill preparation complete repo=%s/%s sources=%d",
        repo_owner,
        repo_name,
        len(sources),
    )
    return PreparedRepoSkills(trusted_ref=trusted_ref, sources=tuple(sources))
