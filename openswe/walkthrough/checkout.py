"""Read-only git for walkthroughs: a PR's diff and lines, through refs pinned in a sandbox checkout.

Refs under ``refs/walkthrough/`` pin the head and merge base a walkthrough is at.
The sandbox may be shared with the thread that is changing the code, so
everything is read through those refs and the working tree is never touched.
Work on the diff's lines runs in the sandbox (``openswe/sandbox_scripts/walkthrough.py``),
so only an index of hunks and the rendered chunks come back.
"""

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Self

import httpx2
from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import JsonValue

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.repo_prep import BLOBLESS_CLONE_ARGS
from openswe.sandboxes.scripts import SandboxScript, SandboxScriptError
from openswe.walkthrough.diff import Carry, DiffIndex, LineMaps
from openswe.walkthrough.lines import LineSet

GIT_TIMEOUT_SECONDS = 300
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
        """The ``(merge base, head)`` this checkout is pinned to, or ``None`` when there is none."""
        result = await self.backend.aexecute(
            f"cd {shlex.quote(self.repo_dir)} 2>/dev/null && "
            f"git rev-parse -q --verify {MERGE_BASE_REF} && git rev-parse -q --verify {HEAD_REF}",
            timeout=GIT_TIMEOUT_SECONDS,
        )
        lines = result.output.split()
        if result.exit_code not in (0, None) or len(lines) != 2:
            return None
        return lines[0], lines[1]

    async def pin(self, *, full_name: str, pr_number: int, head_sha: str) -> bool:
        """Fetch and pin the PR's head and its merge base, cloning the repository if it is missing.

        The merge base is GitHub's, against the base branch as it is now, so a
        stale ``base.sha`` cannot pull the base branch's own commits into the diff.
        Returns whether anything changed; a checkout already pinned there is left alone.
        """
        owner, _, repo = full_name.partition("/")
        try:
            async with PullRequestClient.as_app(owner, repo, pr_number) as pull:
                merge_base_sha = await pull.merge_base(head_sha)
        except (GitHubAppUnavailable, httpx2.HTTPError, ValueError) as exc:
            raise CheckoutError(f"could not find the pull request's merge base: {exc}") from exc
        if await self.pinned() == (merge_base_sha, head_sha):
            return False
        work_dir, _, name = self.repo_dir.rpartition("/")
        merge_base, head = shlex.quote(merge_base_sha), shlex.quote(head_sha)
        pull_ref = shlex.quote(f"refs/pull/{pr_number}/head")
        await self.backend.aexecute(
            f"mkdir -p {shlex.quote(work_dir)}", timeout=GIT_TIMEOUT_SECONDS
        )
        await self._run(
            f"[ -d {shlex.quote(name)}/.git ] || gh repo clone {shlex.quote(full_name)} "
            f"{shlex.quote(name)} -- --quiet {BLOBLESS_CLONE_ARGS}\n"
            f"cd {shlex.quote(name)}\n"
            # The scout's repo prep has usually fetched both already; each fetch is a GitHub round trip.
            f"for sha in {head} {merge_base}; do\n"
            '  git cat-file -e "$sha^{commit}" 2>/dev/null'
            ' || git fetch --quiet origin "$sha" 2>/dev/null || true\n'
            "done\n"
            f"git cat-file -e {head}^{{commit}} 2>/dev/null"
            f" || git fetch --quiet origin {pull_ref} 2>/dev/null || true\n"
            f"git cat-file -e {merge_base}^{{commit}}\n"
            f"git cat-file -e {head}^{{commit}}\n"
            f"git update-ref {MERGE_BASE_REF} {merge_base}\n"
            f"git update-ref {HEAD_REF} {head}",
            cwd=work_dir,
        )
        return True

    async def diff(self) -> str:
        """The PR's diff from its merge base, with git's default context."""
        return await self._run(f"{_DIFF} {MERGE_BASE_REF} {HEAD_REF}")

    @cached_property
    def _script(self) -> SandboxScript:
        return SandboxScript(
            "walkthrough.py",
            f"{self.repo_dir.rpartition('/')[0]}/.open-swe/scripts",
            reads_request_files=True,
        )

    async def _ask(self, request: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            return await self._script.run(
                self.backend, {"repo": self.repo_dir, **request}, timeout=GIT_TIMEOUT_SECONDS
            )
        except (SandboxScriptError, RuntimeError) as exc:
            raise CheckoutError(str(exc)) from exc

    async def index(self) -> DiffIndex:
        """The PR's changed files and their hunks, from its merge base to its head."""
        return DiffIndex.model_validate(await self._ask({"op": "index"}))

    async def carry(self, head_sha: str, merge_base_sha: str, into: DiffIndex) -> Carry | None:
        """How lines moved since ``head_sha``; ``None`` when that commit can no longer be read."""
        found = await self._ask(
            {"op": "maps", "old_head": head_sha, "old_merge_base": merge_base_sha}
        )
        if "missing" in found:
            return None
        return Carry(LineMaps.model_validate(found), into)

    async def render(self, lines: LineSet) -> str:
        """``lines`` as a reader sees a chunk: its own lines only, at real line numbers."""
        return (await self.render_chunks([lines]))[0]

    async def render_chunks(self, chunks: Sequence[LineSet]) -> list[str]:
        """Each chunk's lines rendered as by ``render``, in one sandbox round trip."""
        if not chunks:
            return []
        found = await self._ask({"op": "render", "sets": [lines.dump() for lines in chunks]})
        codes = found.get("codes")
        if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
            raise CheckoutError("the sandbox rendered no chunks")
        return [str(code) for code in codes]

    async def wordless(self, sets: Sequence[LineSet]) -> list[bool]:
        """Which of ``sets`` hold only braces, punctuation or blank lines."""
        if not sets:
            return []
        found = await self._ask({"op": "wordless", "sets": [lines.dump() for lines in sets]})
        flags = found.get("wordless")
        if not isinstance(flags, list) or not all(isinstance(flag, bool) for flag in flags):
            raise CheckoutError("the sandbox checked no lines")
        return [flag is True for flag in flags]
