"""Read-only git for the review guide: the PR's diff and files, from its sandbox checkout.

Refs under ``refs/review-guide/`` pin the base, head and merge base the
checkout was prepared for, so a later run can tell when the pull request moved.
"""

import shlex

from deepagents.backends.protocol import SandboxBackendProtocol

GIT_TIMEOUT_SECONDS = 300
_DIFF = "git -c core.quotePath=false diff --no-color --no-ext-diff --no-renames"
_BASE_REF = "refs/review-guide/base"
_HEAD_REF = "refs/review-guide/head"
_MERGE_BASE_REF = "refs/review-guide/merge-base"


class GuideGitError(RuntimeError):
    """A git step in the review guide sandbox failed."""


async def _run(backend: SandboxBackendProtocol, repo_dir: str, script: str) -> str:
    result = await backend.aexecute(
        f"set -e\ncd {shlex.quote(repo_dir)}\n{script}", timeout=GIT_TIMEOUT_SECONDS
    )
    if result.exit_code not in (0, None):
        raise GuideGitError(result.output.strip()[-2000:])
    return result.output


async def built_for(backend: SandboxBackendProtocol, repo_dir: str) -> tuple[str, str] | None:
    """The ``(base, head)`` this checkout was pinned to, or ``None`` when there is none."""
    result = await backend.aexecute(
        f"cd {shlex.quote(repo_dir)} 2>/dev/null && "
        f"git rev-parse -q --verify {_BASE_REF} && git rev-parse -q --verify {_HEAD_REF}",
        timeout=GIT_TIMEOUT_SECONDS,
    )
    lines = result.output.split()
    if result.exit_code not in (0, None) or len(lines) != 2:
        return None
    return lines[0], lines[1]


async def pin(
    backend: SandboxBackendProtocol, repo_dir: str, *, base_sha: str, head_sha: str
) -> None:
    base, head = shlex.quote(base_sha), shlex.quote(head_sha)
    await _run(
        backend,
        repo_dir,
        f"git update-ref {_MERGE_BASE_REF} $(git merge-base {base} {head})\n"
        f"git update-ref {_BASE_REF} {base}\n"
        f"git update-ref {_HEAD_REF} {head}",
    )


async def pr_diff(
    backend: SandboxBackendProtocol, repo_dir: str, *, path: str = "", zero: bool = True
) -> str:
    """The PR's diff from its merge base, zero-context unless ``zero`` is false."""
    scope = f" -- {shlex.quote(path)}" if path else ""
    return await _run(
        backend,
        repo_dir,
        f"{_DIFF}{' -U0' if zero else ''} {_MERGE_BASE_REF} {_HEAD_REF}{scope}",
    )


async def head_file(backend: SandboxBackendProtocol, repo_dir: str, path: str) -> str:
    return await _run(backend, repo_dir, f"git show {_HEAD_REF}:{shlex.quote(path)}")
