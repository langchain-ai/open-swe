"""Turn-scoped git evidence, reconciled without assuming the configured repository."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.run_config import RunConfig
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.scripts import SandboxScript
from openswe.sandboxes.state import get_sandbox_backend

logger = logging.getLogger(__name__)
BASELINE: Final = "git_trace_baseline"
PR_PREFIX: Final = "git_trace_pr:"
_json = TypeAdapter(dict[str, JsonValue])


class GitTarget(BaseModel):
    repo: str
    branch: str


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

    async def snapshot(self) -> dict[str, JsonValue] | None:
        if not self.config.thread_id:
            return None
        try:
            backend = await get_sandbox_backend(self.config.thread_id)
            root = await resolve_sandbox_work_dir(backend)
            result = await SandboxScript("git_trace.py", f"{root}/.open-swe/scripts").run(
                backend, {"root": root}
            )
            return result
        except Exception:
            logger.warning("Could not capture turn git evidence", exc_info=True)
            return None

    async def begin(self) -> dict[str, JsonValue]:
        return {
            BASELINE: {"invocation": self.config.invocation_id, "snapshot": await self.snapshot()}
        }

    def pr_evidence(self, targets: list[GitTarget], call_id: str) -> dict[str, JsonValue]:
        if not targets:
            return {}
        return {
            f"{PR_PREFIX}{call_id}": {
                "invocation": self.config.invocation_id,
                "targets": [target.model_dump() for target in targets],
            }
        }

    async def reconcile(self, state: Mapping[str, object]) -> dict[str, JsonValue]:
        metadata = self.actor
        targets: set[tuple[str, str]] = set()
        for key, value in state.items():
            if (
                key.startswith(PR_PREFIX)
                and isinstance(value, dict)
                and value.get("invocation") == self.config.invocation_id
            ):
                for target in TypeAdapter(list[GitTarget]).validate_python(value.get("targets")):
                    targets.add((target.repo, target.branch))
        pr_repos = {repo for repo, _ in targets}
        baseline = state.get(BASELINE)
        if isinstance(baseline, dict) and baseline.get("invocation") == self.config.invocation_id:
            before = baseline.get("snapshot")
            after = await self.snapshot()
            if isinstance(before, dict) and after is not None:
                old = before.get("repos")
                new = after.get("repos")
                if isinstance(old, dict) and isinstance(new, dict):
                    for path, checkout in new.items():
                        if not isinstance(checkout, dict) or checkout == old.get(path):
                            continue
                        repo, branch = checkout.get("repo"), checkout.get("branch")
                        if (
                            isinstance(repo, str)
                            and isinstance(branch, str)
                            and repo not in pr_repos
                        ):
                            targets.add((repo, branch))
        if targets:
            metadata["open_swe_git_targets"] = [
                {"repo": repo, "branch": branch} for repo, branch in sorted(targets)
            ]
        repos = {repo for repo, _ in targets}
        if len(repos) == 1:
            metadata["ls_git_repo"] = next(iter(repos))
            branches = {branch for _, branch in targets}
            if len(branches) == 1 and (branch := next(iter(branches))):
                metadata["ls_git_branch"] = branch
        return _json.validate_python(metadata)
