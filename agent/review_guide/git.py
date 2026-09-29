"""Git plumbing for the review guide, run inside its sandbox checkout.

``HEAD`` holds what the reader approved, the index holds the chunk on screen,
and the working tree holds the PR head. Refs under ``refs/review-guide/`` pin
the base and head the checkout was built for, so a later run can tell when the
pull request moved.
"""

import shlex
from collections import Counter

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import BaseModel

from agent.review_guide.hunks import select_seen
from agent.review_scout.git import ScoutGitError, setup_working_tree

GIT_TIMEOUT_SECONDS = 300
CARRIED_TITLE = "Previously reviewed"
_IDENTITY = "-c user.name='Open SWE Review Guide' -c user.email=review-guide@open-swe.invalid"
_DIFF = "git -c core.quotePath=false diff --no-color --no-ext-diff --no-renames"
_SHOWN_FILE = ".git/review-guide-shown"
_BASE_REF = "refs/review-guide/base"
_HEAD_REF = "refs/review-guide/head"


class GuideGitError(RuntimeError):
    """A git step in the review guide sandbox failed."""


class Rebuild(BaseModel):
    carried_lines: int


async def _run(backend: SandboxBackendProtocol, repo_dir: str, script: str) -> str:
    result = await backend.aexecute(
        f"set -e\ncd {shlex.quote(repo_dir)}\n{script}", timeout=GIT_TIMEOUT_SECONDS
    )
    if result.exit_code not in (0, None):
        raise GuideGitError(result.output.strip()[-2000:])
    return result.output


async def built_for(backend: SandboxBackendProtocol, repo_dir: str) -> tuple[str, str] | None:
    """The ``(base, head)`` this checkout was built for, or ``None`` when there is none."""
    result = await backend.aexecute(
        f"cd {shlex.quote(repo_dir)} 2>/dev/null && "
        f"git rev-parse -q --verify {_BASE_REF} && git rev-parse -q --verify {_HEAD_REF}",
        timeout=GIT_TIMEOUT_SECONDS,
    )
    lines = result.output.split()
    if result.exit_code not in (0, None) or len(lines) != 2:
        return None
    return lines[0], lines[1]


async def rebuild(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    *,
    base_sha: str,
    head_sha: str,
    seen: Counter[str],
    patch_path: str,
) -> Rebuild:
    """Lay the PR head over its merge base and commit every line the reader already approved."""
    try:
        await setup_working_tree(backend, repo_dir, base_sha=base_sha, head_sha=head_sha)
    except ScoutGitError as exc:
        raise GuideGitError(str(exc)) from exc
    selection = select_seen(await _run(backend, repo_dir, f"{_DIFF} -U0"), seen)
    script: list[str] = []
    if selection.patch:
        await backend.aupload_files([(patch_path, selection.patch.encode())])
        script.append(f"git apply --cached --unidiff-zero {shlex.quote(patch_path)}")
    if selection.whole_files:
        script.append(
            "git add -A -- " + " ".join(shlex.quote(path) for path in selection.whole_files)
        )
    script.append(
        "if ! git diff --cached --quiet; then "
        f"git {_IDENTITY} commit --quiet --no-verify -m {shlex.quote(CARRIED_TITLE)}; fi"
    )
    script.append(f"rm -f {_SHOWN_FILE}")
    script.append(f"git update-ref {_BASE_REF} {shlex.quote(base_sha)}")
    script.append(f"git update-ref {_HEAD_REF} {shlex.quote(head_sha)}")
    await _run(backend, repo_dir, "\n".join(script))
    return Rebuild(carried_lines=selection.lines)


async def staged_diff(backend: SandboxBackendProtocol, repo_dir: str, *, zero: bool = False) -> str:
    return await _run(backend, repo_dir, f"{_DIFF} --cached{' -U0' if zero else ''}")


async def staged_stat(backend: SandboxBackendProtocol, repo_dir: str) -> str:
    return await _run(backend, repo_dir, f"{_DIFF} --cached --stat")


async def unstaged_diff(backend: SandboxBackendProtocol, repo_dir: str, path: str) -> str:
    return await _run(backend, repo_dir, f"{_DIFF} -- {shlex.quote(path)}")


async def unstaged_stat(backend: SandboxBackendProtocol, repo_dir: str) -> str:
    return await _run(backend, repo_dir, f"{_DIFF} --stat")


async def head_file(backend: SandboxBackendProtocol, repo_dir: str, path: str) -> str:
    return await _run(backend, repo_dir, f"git show {_HEAD_REF}:{shlex.quote(path)}")


async def mark_shown(backend: SandboxBackendProtocol, repo_dir: str) -> None:
    """Remember the index the reader is looking at, so approval covers exactly that."""
    await _run(backend, repo_dir, f"git write-tree > {_SHOWN_FILE}")


async def shown_matches_index(backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    output = await _run(
        backend,
        repo_dir,
        f'if [ "$(cat {_SHOWN_FILE} 2>/dev/null)" = "$(git write-tree)" ]; then echo SAME; fi',
    )
    return output.strip() == "SAME"


async def commit_approved(backend: SandboxBackendProtocol, repo_dir: str, title: str) -> None:
    await _run(
        backend,
        repo_dir,
        f"git {_IDENTITY} commit --quiet --no-verify -m {shlex.quote(title)}\nrm -f {_SHOWN_FILE}",
    )
