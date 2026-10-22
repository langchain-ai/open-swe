"""Turn-scoped git evidence, reconciled without assuming the configured repository."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from time import time_ns
from typing import Final

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, JsonValue, ValidationError

from openswe.run_config import RunConfig
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.scripts import SandboxScript
from openswe.sandboxes.state import get_sandbox_backend

logger = logging.getLogger(__name__)
BASELINE: Final = "git_trace_baseline"
PR_PREFIX: Final = "git_trace_pr:"
WORK_PREFIX: Final = "git_trace_work:"
OBSERVED_TOOLS: Final = frozenset({"execute", "write_file", "edit_file", "delete"})


class GitTarget(BaseModel):
    repo: str
    branch: str


class GitCheckout(GitTarget):
    head: str = ""
    dirty: str = ""


class GitSnapshot(BaseModel):
    repos: dict[str, GitCheckout] = {}

    @classmethod
    def parse(cls, value: object) -> GitSnapshot | None:
        if value is None:
            return None
        try:
            return cls.model_validate(value)
        except ValidationError:
            logger.warning("Ignoring invalid git snapshot", exc_info=True)
            return None

    def changes_since(self, before: GitSnapshot) -> list[GitTarget]:
        return [
            GitTarget(repo=checkout.repo, branch=checkout.branch)
            for path, checkout in self.repos.items()
            if checkout != before.repos.get(path)
        ]


class GitObservation(BaseModel):
    invocation: str | None
    observed_at: int = 0
    targets: list[GitTarget]


_pr_targets: ContextVar[list[GitTarget] | None] = ContextVar("git_trace_pr_targets", default=None)


@contextmanager
def collect_pr_targets() -> Iterator[list[GitTarget]]:
    targets: list[GitTarget] = []
    token = _pr_targets.set(targets)
    try:
        yield targets
    finally:
        _pr_targets.reset(token)


def record_pr_target(repo: str, branch: str) -> None:
    targets = _pr_targets.get()
    if targets is not None and repo and branch:
        targets.append(GitTarget(repo=repo, branch=branch))


class TurnGitMetadata:
    """The actor and observed checkout/PR targets of one dispatched turn."""

    def __init__(self, config: RunnableConfig) -> None:
        self.config = RunConfig.from_config(config)
        self.attribution = config.get("metadata", {}).get("ls_attribution_identifier")

    @property
    def actor(self) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {"ls_repo_provider": "github"}
        if isinstance(self.attribution, str) and self.attribution:
            metadata["ls_attribution_identifier"] = self.attribution
        return metadata

    async def snapshot(self) -> GitSnapshot | None:
        if not self.config.thread_id:
            return None
        try:
            backend = await get_sandbox_backend(self.config.thread_id)
            root = await resolve_sandbox_work_dir(backend)
            result = await SandboxScript("git_trace.py", f"{root}/.open-swe/scripts").run(
                backend, {"root": root}
            )
            return GitSnapshot.parse(result)
        except Exception:
            logger.warning("Could not capture turn git evidence", exc_info=True)
            return None

    async def begin(self) -> dict[str, JsonValue]:
        snapshot = await self.snapshot()
        return {
            BASELINE: {
                "invocation": self.config.invocation_id,
                "snapshot": snapshot.model_dump() if snapshot is not None else None,
            }
        }

    def evidence(
        self,
        targets: list[GitTarget],
        call_id: str,
        *,
        pr: bool = False,
        observed_at: int | None = None,
    ) -> dict[str, JsonValue]:
        if not targets:
            return {}
        observation = GitObservation(
            invocation=self.config.invocation_id,
            observed_at=observed_at if observed_at is not None else time_ns(),
            targets=targets,
        )
        return {f"{PR_PREFIX if pr else WORK_PREFIX}{call_id}": observation.model_dump()}

    async def work_evidence(
        self, before: GitSnapshot | None, call_id: str, observed_at: int
    ) -> dict[str, JsonValue]:
        if before is None or (after := await self.snapshot()) is None:
            return {}
        return self.evidence(after.changes_since(before), call_id, observed_at=observed_at)

    def observations(self, state: Mapping[str, object]) -> Iterator[tuple[bool, GitObservation]]:
        for key, value in state.items():
            if (
                key.startswith((PR_PREFIX, WORK_PREFIX))
                and isinstance(value, dict)
                and value.get("invocation") == self.config.invocation_id
            ):
                try:
                    observation = GitObservation.model_validate(value)
                except ValidationError:
                    logger.warning("Ignoring invalid git observation", exc_info=True)
                    continue
                yield key.startswith(PR_PREFIX), observation

    def work_baseline(self, state: Mapping[str, object]) -> GitSnapshot | None:
        """Observe only until the first work target, without another pre-tool scan."""
        if any(observation.targets for _, observation in self.observations(state)):
            return None
        baseline = state.get(BASELINE)
        if isinstance(baseline, dict) and baseline.get("invocation") == self.config.invocation_id:
            return GitSnapshot.parse(baseline.get("snapshot"))
        return None

    async def reconcile(self, state: Mapping[str, object]) -> dict[str, JsonValue]:
        """Select the earliest work target, preferring the repository's exact PR head ref."""
        metadata = self.actor
        observations = sorted(self.observations(state), key=lambda item: item[1].observed_at)
        observed: list[GitTarget] = []
        pr_branches: dict[str, list[str]] = {}
        for pr, observation in observations:
            observed.extend(observation.targets)
            if pr:
                for target in observation.targets:
                    pr_branches.setdefault(target.repo, []).append(target.branch)
        baseline = state.get(BASELINE)
        if isinstance(baseline, dict) and baseline.get("invocation") == self.config.invocation_id:
            before = GitSnapshot.parse(baseline.get("snapshot"))
            after = await self.snapshot()
            if before is not None and after is not None:
                observed.extend(after.changes_since(before))
        targets: dict[tuple[str, str], None] = {}
        for target in observed:
            branches = pr_branches.get(target.repo, [])
            branch = target.branch if not branches or target.branch in branches else branches[0]
            targets.setdefault((target.repo, branch), None)
        if targets:
            metadata["open_swe_git_targets"] = [
                {"repo": repo, "branch": branch} for repo, branch in targets
            ]
            repo, branch = next(iter(targets))
            metadata["ls_git_repo"] = repo
            if branch:
                metadata["ls_git_branch"] = branch
        return metadata
