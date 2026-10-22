"""Read-only git for walkthroughs: a PR's diff and files, through refs pinned in a sandbox checkout.

Refs under ``refs/walkthrough/`` pin the base, head and merge base a walkthrough
is at. The sandbox may be shared with the thread that is changing the code, so
everything is read through those refs and the working tree is never touched.
"""

import re
import shlex
import uuid
from dataclasses import dataclass
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.repo_prep import BLOBLESS_CLONE_ARGS
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
            f"{shlex.quote(name)} -- --quiet {BLOBLESS_CLONE_ARGS}\n"
            f"cd {shlex.quote(name)}\n"
            # The scout's repo prep has usually fetched both already; each fetch is a GitHub round trip.
            f"for sha in {base} {head}; do\n"
            '  git cat-file -e "$sha^{commit}" 2>/dev/null'
            ' || git fetch --quiet origin "$sha" 2>/dev/null || true\n'
            "done\n"
            f"git cat-file -e {head}^{{commit}} 2>/dev/null"
            f" || git fetch --quiet origin {pull_ref} 2>/dev/null || true\n"
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

    async def diff(self) -> str:
        """The PR's diff from its merge base, with git's default context."""
        return await self._run(f"{_DIFF} {MERGE_BASE_REF} {HEAD_REF}")

    async def changes(self) -> list[FileChange]:
        return parse(await self.diff())

    async def head_files(self, paths: list[str]) -> dict[str, list[str]]:
        """``paths`` at the head, as lines, read in one sandbox round trip."""
        if not paths:
            return {}
        marker = f"walkthrough-file-{uuid.uuid4().hex}"
        output = await self._run(
            "".join(
                f"printf '%s\\n' {marker}\ngit show {HEAD_REF}:{shlex.quote(path)}\nprintf '\\n'\n"
                for path in paths
            )
        )
        # Each file is followed by one extra newline, so a missing final newline can't
        # glue its last line to the next marker.
        texts = [text.removesuffix("\n") for text in output.split(f"{marker}\n")[1:]]
        return {path: text.splitlines() for path, text in zip(paths, texts, strict=True)}

    async def render(self, refs: list[LineRef], changes: list[FileChange]) -> str:
        """``refs`` as a reader sees a chunk: its own lines only, at real line numbers."""
        return (await self.render_chunks([refs], changes))[0]

    async def render_chunks(
        self, chunks: list[list[LineRef]], changes: list[FileChange]
    ) -> list[str]:
        """Each chunk's lines rendered as by ``render``, reading the head once for all of them."""
        index = {LineRef.of(line): line for change in changes for line in change.lines}
        chunk_lines = [[index[ref] for ref in refs if ref in index] for refs in chunks]
        paths = {line.path for lines in chunk_lines for line in lines}
        head = await self.head_files(
            [change.path for change in changes if change.path in paths and not change.deleted]
        )
        added = {
            change.path: {line.lineno for line in change.lines if line.sign == "+"}
            for change in changes
        }
        return [render_chunk(lines, head, added) for lines in chunk_lines]
