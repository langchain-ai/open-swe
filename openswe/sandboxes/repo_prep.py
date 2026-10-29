"""Deterministic repo prep for the reviewer sandbox.

The reviewer reviews a single PR, so we clone its repo and check out the PR
head during agent init -- before the first model call -- instead of asking the
LLM to narrate ``gh repo clone`` mid-run. Pre-cloning also lets ``SkillsMiddleware``
discover the repo's ``.agents/skills`` / ``.claude/skills`` at its one-shot
``before_agent`` scan.

Best-effort: any failure leaves the sandbox usable (the review still works off
the fetched diff) and returns ``False`` so callers can skip skill wiring.
"""

import logging
import posixpath
import shlex
from collections.abc import Sequence

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 240
PREP_LOCK_TIMEOUT_SECONDS = 120

DEFAULT_SKILL_DIRS = (".agents/skills", ".claude/skills")

TRUSTED_SKILLS_DIRNAME = ".review-skills"

# A review reads the PR's trees, not its history's contents: a blobless clone
# downloads commits and trees only and fetches blobs as checkout, diff or show
# first touch them, which cuts a large repo's clone several-fold.
BLOBLESS_CLONE_ARGS = "--filter=blob:none"


def _fetch_missing(shas: Sequence[str]) -> list[str]:
    """Shell lines fetching whichever of ``shas`` the clone lacks, and nothing else.

    Workspace images preload a full clone, and refreshing all of its branches
    (langchainplus has over 12,000) took minutes; a reused sandbox usually has
    every commit already and fetches nothing.
    """
    return [
        "missing=",
        f"for sha in {' '.join(shlex.quote(sha) for sha in shas)}; do",
        '  git cat-file -e "$sha^{commit}" 2>/dev/null || missing="$missing $sha"',
        "done",
        # One round trip for all of them, then one each if a commit is unreachable.
        'if [ -n "$missing" ] && ! git fetch --quiet --no-tags origin $missing 2>/dev/null; then',
        '  for sha in $missing; do git fetch --quiet --no-tags origin "$sha" 2>/dev/null || true; done',
        "fi",
    ]


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
    clone_args = f"{BLOBLESS_CLONE_ARGS} --no-checkout" if q_head else BLOBLESS_CLONE_ARGS

    lines = [
        "set -e",
        f"mkdir -p {q_work_dir}",
        f"exec 9>{shlex.quote(repo_dir + '.review-prep.lock')}",
        f'flock -w {PREP_LOCK_TIMEOUT_SECONDS} 9 || {{ echo "Timed out waiting for review repository preparation lock" >&2; exit 1; }}',
        f"if [ -d {q_repo_dir}/.git ]; then",
        f"  cd {q_repo_dir}",
        "  git_process_status=0",
        "  pgrep -x git >/dev/null || git_process_status=$?",
        '  if [ "$git_process_status" -eq 1 ]; then',
        "    rm -f .git/index.lock .git/shallow.lock",
        "  fi",
        "else",
        # The head checkout below replaces the default branch's, so skip fetching its blobs.
        f"  cd {q_work_dir} && gh repo clone {q_full_name} -- {clone_args} && cd {q_repo_name}",
        "fi",
    ]
    shas = [sha for sha in (base_sha, head_sha) if sha]
    if shas:
        lines.extend(_fetch_missing(shas))
    if q_head:
        if pr_number is not None:
            # The pull ref covers fork PRs whose head is not reachable from origin's branches.
            pull_ref = shlex.quote(f"refs/pull/{pr_number}/head")
            lines.append(
                f"git cat-file -e {q_head}^{{commit}} 2>/dev/null"
                f" || git fetch --quiet --no-tags origin {pull_ref} 2>/dev/null || true"
            )
        # --force: a reused sandbox can have a dirty worktree from a previous
        # run, which would otherwise block the checkout and silently leave the
        # tree at the old head. Strict on purpose: a failed checkout must fail
        # the prep so callers know the tree is NOT at the PR head.
        lines.append(f"git checkout --force {q_head} --quiet")
        lines.append(f'[ "$(git rev-parse HEAD)" = {q_head} ]')
    return "\n".join(lines)


class ReviewRepoPreparationError(RuntimeError):
    """Repository preparation failed with bounded sandbox diagnostics."""

    def __init__(self, output: str) -> None:
        super().__init__(output.strip()[-2000:])


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
    """Best-effort preparation; return whether the repo is at the requested head."""
    try:
        await prepare_review_repo_or_raise(
            sandbox_backend,
            work_dir=work_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
        )
    except ReviewRepoPreparationError:
        logger.warning(
            "Failed to prep review repo",
            extra={"repo_owner": repo_owner, "repo_name": repo_name},
            exc_info=True,
        )
        return False
    return True


async def prepare_review_repo_or_raise(
    sandbox_backend: SandboxBackendProtocol,
    *,
    work_dir: str,
    repo_owner: str,
    repo_name: str,
    head_sha: str,
    pr_number: int | None = None,
    base_sha: str = "",
) -> None:
    """Prepare the repository or raise with the available failure output."""
    if not repo_owner or not repo_name:
        raise ReviewRepoPreparationError("Missing repository owner or name")
    command = _prep_command(work_dir, repo_owner, repo_name, head_sha, pr_number, base_sha)
    try:
        result = await sandbox_backend.aexecute(
            command, timeout=CLONE_TIMEOUT_SECONDS + PREP_LOCK_TIMEOUT_SECONDS
        )
    except Exception as exc:
        raise ReviewRepoPreparationError(str(exc)) from exc
    if result.exit_code not in (0, None):
        raise ReviewRepoPreparationError(result.output or f"Preparation exited {result.exit_code}")
    logger.info(
        "Prepped review repo",
        extra={"repo_owner": repo_owner, "repo_name": repo_name, "head_sha": head_sha},
    )


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
    q_ref = shlex.quote(trusted_ref)
    dests = {skill_dir: posixpath.join(dest_root, skill_dir) for skill_dir in skill_dirs}
    # One round trip for every dir; each extraction stands alone, so one failing
    # leaves the others.
    extractions = [
        f"( git cat-file -e {q_ref}:{shlex.quote(skill_dir)} 2>/dev/null && "
        f"rm -rf {shlex.quote(dest)} && mkdir -p {shlex.quote(dest)} && "
        f"git archive {q_ref} {shlex.quote(skill_dir)} | "
        f"tar -x --strip-components={len(skill_dir.split('/'))} -C {shlex.quote(dest)} && "
        f"echo {shlex.quote(dest)} )"
        for skill_dir, dest in dests.items()
    ]
    command = f"cd {shlex.quote(repo_dir)} || exit 1\n" + "\n".join(extractions)
    try:
        result = await sandbox_backend.aexecute(command)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to extract trusted skills", extra={"repo_dir": repo_dir}, exc_info=True
        )
        return []
    extracted = set(result.output.splitlines())
    return [f"{dest}/" for dest in dests.values() if dest in extracted]
