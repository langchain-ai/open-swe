import re
from dataclasses import dataclass
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.review_guide import git
from openswe.review_guide.diff import ChangedLine, FileChange, parse
from openswe.review_guide.render import MessageRenderer, render_chunk
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import LineRef, Walk
from openswe.run_config import RunConfig
from openswe.runtime import get_cached_sandbox_backend
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.users import User

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class GuideUnavailableError(RuntimeError):
    """The current run is not a review guide session with a checkout."""


async def requester_login() -> str:
    """The GitHub login of the person whose Slack message started this turn."""
    cfg = RunConfig.from_runtime()
    slack_user_id = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if not slack_user_id:
        raise GuideUnavailableError(
            "only a person's own message can do this; ask them to confirm first"
        )
    user = await User.for_identity("slack", slack_user_id)
    if user is None or not user.github_login:
        raise GuideUnavailableError("the person asking has no GitHub account linked to Open SWE")
    return user.github_login


async def guide_repo_dir(backend: SandboxBackendProtocol, repo: str) -> str:
    if not _REPO_NAME_RE.fullmatch(repo):
        raise GuideUnavailableError("review guide repository name is invalid")
    return f"{await resolve_sandbox_work_dir(backend)}/{repo}"


@dataclass
class GuideContext:
    session: ReviewGuideSession
    backend: SandboxBackendProtocol
    repo_dir: str
    head_sha: str

    @classmethod
    async def current(cls) -> Self:
        thread_id = RunConfig.from_runtime().thread_id
        session = await ReviewGuideSession.get(thread_id) if thread_id else None
        if thread_id is None or session is None:
            raise GuideUnavailableError("this thread is not a review guide session")
        backend = get_cached_sandbox_backend(thread_id)
        repo_dir = await guide_repo_dir(backend, session.pull_request.repo)
        built = await git.built_for(backend, repo_dir)
        if built is None:
            raise GuideUnavailableError("the checkout is not ready; try again next turn")
        return cls(session=session, backend=backend, repo_dir=repo_dir, head_sha=built[1])

    async def changes(self) -> list[FileChange]:
        return parse(await git.pr_diff(self.backend, self.repo_dir))

    async def unseen(self, changes: list[FileChange], walk: Walk) -> list[ChangedLine]:
        return walk.unseen(changes, await self.session.seen_lines())

    def walk(self, changes: list[FileChange]) -> Walk:
        """The walkthrough of the checkout's head, started fresh when the head moved."""
        walk = self.session.walk
        if walk is not None and walk.head_sha == self.head_sha:
            return walk
        return Walk.start(self.head_sha, changes)

    async def render(self, refs: list[LineRef]) -> str:
        """``refs`` as the reader sees a chunk: its own lines only, at real line numbers."""
        changes = await self.changes()
        index = {LineRef.of(line): line for change in changes for line in change.lines}
        lines = [index[ref] for ref in refs if ref in index]
        paths = {line.path for line in lines}
        head = {
            change.path: (
                await git.head_file(self.backend, self.repo_dir, change.path)
            ).splitlines()
            for change in changes
            if change.path in paths and not change.deleted
        }
        added = {
            change.path: {line.lineno for line in change.lines if line.sign == "+"}
            for change in changes
        }
        return render_chunk(lines, head, added)

    def renderer(self) -> MessageRenderer:
        async def read_head(path: str) -> str:
            return await git.head_file(self.backend, self.repo_dir, path)

        async def file_diff(path: str) -> str:
            return await git.pr_diff(self.backend, self.repo_dir, path=path, zero=False)

        async def chunk() -> str:
            walk = self.session.walk
            current = walk.on_screen() if walk and walk.head_sha == self.head_sha else None
            if current is None:
                return "_(no chunk is on screen)_"
            return await self.render(current.lines)

        return MessageRenderer(read_head=read_head, file_diff=file_diff, chunk=chunk)
