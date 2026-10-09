"""Read-only git for walkthroughs: a PR's diff and files, through refs pinned in a sandbox checkout.

Refs under ``refs/walkthrough/`` pin the base, head and merge base a walkthrough
is at. The sandbox may be shared with the thread that is changing the code, so
everything is read through those refs and the working tree is never touched.
"""

import re
import shlex
from dataclasses import dataclass
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.walkthrough.diff import FileChange, parse
from openswe.walkthrough.plan import LineRef
from openswe.walkthrough.render import render_chunk

GIT_TIMEOUT_SECONDS = 300
BASE_REF = "refs/walkthrough/base"
HEAD_REF = "refs/walkthrough/head"
MERGE_BASE_REF = "refs/walkthrough/merge-base"
_DIFF = "git -c core.quotePath=false diff --no-color --no-ext-diff --no-renames --full-index"
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class CheckoutError(RuntimeError):
    """A git step in the walkthrough checkout failed."""


@dataclass(frozen=True)
class PinnedCheckout:
    backend: SandboxBackendProtocol
    repo_dir: str

    @classmethod
    async def locate(cls, backend: SandboxBackendProtocol, repo: str) -> Self:
        if not _REPO_NAME_RE.fullmatch(repo):
            raise CheckoutError("walkthrough repository name is invalid")
        return cls(backend, f"{await resolve_sandbox_work_dir(backend)}/{repo}")

    async def _run(self, script: str, *, cwd: str = "") -> str:
        result = await self.backend.aexecute(
            f"set -e\ncd {shlex.quote(cwd or self.repo_dir)}\n{script}",
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if result.exit_code not in (0, None):
            raise CheckoutError(result.output.strip()[-2000:])
        return result.output

    async def pinned(self) -> tuple[str, str] | None:
        """The ``(base, head)`` this checkout is pinned to, or ``None`` when there is none."""
        result = await self.backend.aexecute(
            f"cd {shlex.quote(self.repo_dir)} 2>/dev/null && "
            f"git rev-parse -q --verify {BASE_REF} && git rev-parse -q --verify {HEAD_REF}",
            timeout=GIT_TIMEOUT_SECONDS,
        )
        lines = result.output.split()
        if result.exit_code not in (0, None) or len(lines) != 2:
            return None
        return lines[0], lines[1]

    async def pin(self, *, full_name: str, pr_number: int, base_sha: str, head_sha: str) -> bool:
        """Fetch and pin the PR's base and head, cloning the repository if it is missing.

        Returns whether anything changed; a checkout already pinned there is left alone.
        """
        if await self.pinned() == (base_sha, head_sha):
            return False
        work_dir, _, name = self.repo_dir.rpartition("/")
        base, head = shlex.quote(base_sha), shlex.quote(head_sha)
        pull_ref = shlex.quote(f"refs/pull/{pr_number}/head")
        await self.backend.aexecute(
            f"mkdir -p {shlex.quote(work_dir)}", timeout=GIT_TIMEOUT_SECONDS
        )
        await self._run(
            f"[ -d {shlex.quote(name)}/.git ] || gh repo clone {shlex.quote(full_name)} "
            f"{shlex.quote(name)} -- --quiet\n"
            f"cd {shlex.quote(name)}\n"
            f"for ref in {base} {head} {pull_ref}; do\n"
            '  git fetch --quiet origin "$ref" 2>/dev/null || true\n'
            "done\n"
            f"git cat-file -e {base}^{{commit}}\n"
            f"git cat-file -e {head}^{{commit}}\n"
            f"git update-ref {MERGE_BASE_REF} $(git merge-base {base} {head})\n"
            f"git update-ref {BASE_REF} {base}\n"
            f"git update-ref {HEAD_REF} {head}",
            cwd=work_dir,
        )
        return True

    async def merge_base(self) -> str:
        return (await self._run(f"git rev-parse {MERGE_BASE_REF}")).strip()

    async def diff(self, *, path: str = "", zero: bool = True) -> str:
        """The PR's diff from its merge base, zero-context unless ``zero`` is false."""
        scope = f" -- {shlex.quote(path)}" if path else ""
        return await self._run(
            f"{_DIFF}{' -U0' if zero else ''} {MERGE_BASE_REF} {HEAD_REF}{scope}"
        )

    async def changes(self) -> list[FileChange]:
        return parse(await self.diff())

    async def head_file(self, path: str) -> str:
        return await self._run(f"git show {HEAD_REF}:{shlex.quote(path)}")

    async def render(
        self,
        refs: list[LineRef],
        changes: list[FileChange],
        *,
        head_files: dict[str, list[str]] | None = None,
    ) -> str:
        """``refs`` as a reader sees a chunk: its own lines only, at real line numbers.

        ``head_files`` caches files read at the head across calls.
        """
        index = {LineRef.of(line): line for change in changes for line in change.lines}
        lines = [index[ref] for ref in refs if ref in index]
        paths = {line.path for line in lines}
        cache = {} if head_files is None else head_files
        for change in changes:
            if change.path in paths and not change.deleted and change.path not in cache:
                cache[change.path] = (await self.head_file(change.path)).splitlines()
        head = {path: cache[path] for path in paths if path in cache}
        added = {
            change.path: {line.lineno for line in change.lines if line.sign == "+"}
            for change in changes
        }
        return render_chunk(lines, head, added)
