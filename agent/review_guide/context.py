import re
from dataclasses import dataclass
from typing import Self

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.review_guide.sessions import ReviewGuideSession
from agent.run_config import RunConfig
from agent.runtime import get_cached_sandbox_backend
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.users import User

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

    @classmethod
    async def current(cls) -> Self:
        thread_id = RunConfig.from_runtime().thread_id
        session = await ReviewGuideSession.get(thread_id) if thread_id else None
        if thread_id is None or session is None:
            raise GuideUnavailableError("this thread is not a review guide session")
        backend = get_cached_sandbox_backend(thread_id)
        return cls(
            session=session,
            backend=backend,
            repo_dir=await guide_repo_dir(backend, session.pull_request.repo),
        )
