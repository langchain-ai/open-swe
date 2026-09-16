"""Tool: ``add_repos``. Records repositories on the thread and reports their checkouts."""

import base64
import logging
import posixpath
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Self

from fastapi import HTTPException
from langchain_core.tools import tool
from langgraph_sdk import get_client
from pydantic import BaseModel, Field

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.repositories import Repository
from agent.prompts import load_prompt
from agent.review.styles import normalize_repo_full_name
from agent.run_config import RunConfig
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.state import get_sandbox_backend
from agent.thread_repos import (
    REPOSITORY_IDS_METADATA_KEY,
    repository_ids_metadata,
    thread_repositories,
)
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_url
from agent.webhooks.common import is_repo_allowed

logger = logging.getLogger(__name__)

RepoAction = Literal["cloned", "verified", "conflict", "error"]

_SENTINEL = "===OPEN-SWE-REPO==="
_FIELD_SEPARATOR = "\x1f"
_OPEN_SWE_BRANCH_PREFIX = "open-swe/"
_MAX_STATUS_FILES = 20
_MAX_LOCAL_BRANCHES = 30
_EXECUTE_TIMEOUT_SECONDS = 900
_CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})

_SCRIPT_PREAMBLE = """
emit() { printf '%s=%s\\n' "$1" "$2"; }
b64() { base64 2>/dev/null | tr -d '\\n'; }
lower() { printf '%s' "$1" | tr 'A-Z' 'a-z'; }
norm_remote() {
  lower "$1" | sed -e 's#\\.git$##' \\
    -e 's#^https://github\\.com/##' \\
    -e 's#^http://github\\.com/##' \\
    -e 's#^ssh://git@github\\.com/##' \\
    -e 's#^git://github\\.com/##' \\
    -e 's#^git@github\\.com:##'
}
facts() {
  dir=$1
  full_name=$2
  branch=$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
  emit branch "$branch"
  if [ -n "$branch" ]; then emit detached 0; else emit detached 1; fi
  emit head_sha "$(git -C "$dir" rev-parse HEAD 2>/dev/null || true)"
  default_branch=$(git -C "$dir" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
  default_branch=${default_branch#origin/}
  if [ -z "$default_branch" ]; then
    default_branch=$(gh repo view "$full_name" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || true)
  fi
  emit default_branch "$default_branch"
  upstream=$(git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
  emit upstream "$upstream"
  if [ -n "$upstream" ]; then
    counts=$(git -C "$dir" rev-list --left-right --count "$upstream...HEAD" 2>/dev/null || true)
    emit behind "$(printf '%s' "$counts" | awk '{print $1}')"
    emit ahead "$(printf '%s' "$counts" | awk '{print $2}')"
  fi
  emit status_b64 "$(git -C "$dir" status --porcelain=v1 2>/dev/null | b64)"
  emit stash_count "$(git -C "$dir" stash list 2>/dev/null | wc -l | tr -d ' ')"
  emit last_commit_b64 "$(git -C "$dir" log -1 --format='%H%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s' 2>/dev/null | b64)"
  emit commit_count "$(git -C "$dir" rev-list --count HEAD 2>/dev/null || true)"
  emit describe "$(git -C "$dir" describe --tags --always 2>/dev/null || true)"
  emit branches_b64 "$(git -C "$dir" for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads 2>/dev/null | b64)"
  git_dir=$(git -C "$dir" rev-parse --absolute-git-dir 2>/dev/null || true)
  if [ -n "$git_dir" ] && [ -f "$git_dir/FETCH_HEAD" ]; then
    emit last_fetch_epoch "$(stat -c %Y "$git_dir/FETCH_HEAD" 2>/dev/null || stat -f %m "$git_dir/FETCH_HEAD" 2>/dev/null || true)"
  fi
  if [ -f "$dir/.gitmodules" ]; then emit submodules 1; else emit submodules 0; fi
  if [ -f "$dir/AGENTS.md" ]; then emit has_agents_md 1; else emit has_agents_md 0; fi
  if [ -f "$dir/CLAUDE.md" ]; then emit has_claude_md 1; else emit has_claude_md 0; fi
}
repo_block() {
  full_name=$1
  dir=$2
  printf '%s\\n' "$SENTINEL"
  emit full_name "$full_name"
  emit path "$dir"
  if [ ! -d "$dir" ]; then
    clone_out=$(cd "$WORK_DIR" && gh repo clone "$full_name" "$dir" 2>&1)
    clone_rc=$?
    if [ "$clone_rc" -ne 0 ]; then
      emit action error
      emit error_b64 "$(printf '%s' "$clone_out" | b64)"
      return 0
    fi
    emit action cloned
    emit remote_url "$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
    emit remote_matches 1
    facts "$dir" "$full_name"
    return 0
  fi
  if [ ! -e "$dir/.git" ]; then
    emit action conflict
    emit remote_url ""
    emit remote_matches 0
    emit error_b64 "$(printf '%s' "$dir exists but is not a git repository" | b64)"
    return 0
  fi
  remote=$(git -C "$dir" remote get-url origin 2>/dev/null || true)
  emit remote_url "$remote"
  if [ "$(norm_remote "$remote")" != "$(lower "$full_name")" ]; then
    emit action conflict
    emit remote_matches 0
    return 0
  fi
  emit remote_matches 1
  emit action verified
  fetch_out=$(git -C "$dir" fetch --all --prune --quiet 2>&1)
  fetch_rc=$?
  if [ "$fetch_rc" -ne 0 ]; then
    emit fetch_error_b64 "$(printf '%s' "$fetch_out" | b64)"
  fi
  facts "$dir" "$full_name"
}
"""


def _decode(raw: str) -> str:
    try:
        return base64.b64decode(raw.encode(), validate=True).decode(errors="replace")
    except ValueError:
        logger.warning("Undecodable sandbox report value")
        return ""


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


class WorkingTree(BaseModel):
    """``git status --porcelain=v1`` summarized."""

    clean: bool = True
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicted: int = 0
    files: list[str] = Field(default_factory=list)

    @classmethod
    def parse_porcelain(cls, text: str) -> Self:
        lines = [line for line in text.splitlines() if line.strip()]
        staged = unstaged = untracked = conflicted = 0
        for line in lines:
            code = line[:2]
            if code == "??":
                untracked += 1
                continue
            if code in _CONFLICT_CODES:
                conflicted += 1
                continue
            index, worktree = code[:1], code[1:2]
            if index not in {" ", "?", ""}:
                staged += 1
            if worktree not in {" ", "?", ""}:
                unstaged += 1
        return cls(
            clean=not lines,
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
            conflicted=conflicted,
            files=lines[:_MAX_STATUS_FILES],
        )


class LastCommit(BaseModel):
    """The tip commit of the current checkout."""

    sha: str = ""
    author_name: str = ""
    author_email: str = ""
    authored_at: str = ""
    committed_at: str = ""
    subject: str = ""

    @classmethod
    def parse_record(cls, text: str) -> Self | None:
        parts = text.strip("\n").split(_FIELD_SEPARATOR)
        if len(parts) < 6 or not parts[0]:
            return None
        return cls(
            sha=parts[0],
            author_name=parts[1],
            author_email=parts[2],
            authored_at=parts[3],
            committed_at=parts[4],
            subject=parts[5],
        )


class RepoReport(BaseModel):
    """What one repository's checkout looks like in the sandbox right now."""

    full_name: str
    id: str = ""
    path: str = ""
    action: RepoAction = "error"
    error: str | None = None
    remote_url: str | None = None
    remote_matches: bool = False
    default_branch: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    working_tree: WorkingTree | None = None
    stash_count: int = 0
    last_commit: LastCommit | None = None
    last_fetch_at: str | None = None
    fetch_error: str | None = None
    local_branches: list[str] = Field(default_factory=list)
    open_swe_branches: list[str] = Field(default_factory=list)
    submodules: bool = False
    has_agents_md: bool = False
    has_claude_md: bool = False
    commit_count: int | None = None
    describe: str | None = None
    custom_instructions: str | None = None

    @classmethod
    def probe_script(cls, work_dir: str, repositories: Sequence[Repository]) -> str:
        """The shell script whose output :meth:`parse_output` reads back."""
        calls = "\n".join(
            f"repo_block {shlex.quote(repository.full_name)} "
            f"{shlex.quote(posixpath.join(work_dir, repository.name))}"
            for repository in repositories
        )
        header = f"WORK_DIR={shlex.quote(work_dir)}\nSENTINEL='{_SENTINEL}'\n"
        return f"{header}{_SCRIPT_PREAMBLE}\n{calls}\n"

    @classmethod
    def parse_block(cls, block: str) -> Self | None:
        """One ``KEY=value`` block from :meth:`probe_script`'s output."""
        values: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip():
                values[key.strip()] = value
        full_name = values.get("full_name", "").strip()
        if not full_name:
            return None

        status_b64 = values.get("status_b64")
        commit_b64 = values.get("last_commit_b64")
        branches = (
            [name for name in _decode(values["branches_b64"]).splitlines() if name.strip()]
            if values.get("branches_b64")
            else []
        )
        return cls(
            full_name=full_name,
            path=values.get("path", "").strip(),
            action=cls._parse_action(values.get("action", "")),
            error=_decode(values["error_b64"]) or None if values.get("error_b64") else None,
            remote_url=values.get("remote_url", "").strip() or None,
            remote_matches=values.get("remote_matches", "0").strip() == "1",
            default_branch=values.get("default_branch", "").strip() or None,
            branch=values.get("branch", "").strip() or None,
            head_sha=values.get("head_sha", "").strip() or None,
            detached=values.get("detached", "0").strip() == "1",
            upstream=values.get("upstream", "").strip() or None,
            ahead=_int_or_none(values.get("ahead")),
            behind=_int_or_none(values.get("behind")),
            working_tree=(
                WorkingTree.parse_porcelain(_decode(status_b64)) if status_b64 is not None else None
            ),
            stash_count=_int_or_none(values.get("stash_count")) or 0,
            last_commit=LastCommit.parse_record(_decode(commit_b64)) if commit_b64 else None,
            last_fetch_at=cls._iso_timestamp(values.get("last_fetch_epoch")),
            fetch_error=(
                _decode(values["fetch_error_b64"]) or None
                if values.get("fetch_error_b64")
                else None
            ),
            local_branches=branches[:_MAX_LOCAL_BRANCHES],
            open_swe_branches=[
                name for name in branches if name.startswith(_OPEN_SWE_BRANCH_PREFIX)
            ][:_MAX_LOCAL_BRANCHES],
            submodules=values.get("submodules", "0").strip() == "1",
            has_agents_md=values.get("has_agents_md", "0").strip() == "1",
            has_claude_md=values.get("has_claude_md", "0").strip() == "1",
            commit_count=_int_or_none(values.get("commit_count")),
            describe=values.get("describe", "").strip() or None,
        )

    @staticmethod
    def _parse_action(raw: str) -> RepoAction:
        match raw.strip():
            case "cloned":
                return "cloned"
            case "verified":
                return "verified"
            case "conflict":
                return "conflict"
            case _:
                return "error"

    @classmethod
    def parse_output(
        cls, output: str, repositories: Sequence[Repository], work_dir: str
    ) -> list[Self]:
        """One report per repository, in thread order, filling in what the script missed."""
        parsed: dict[str, Self] = {}
        for block in output.split(_SENTINEL):
            report = cls.parse_block(block)
            if report is not None:
                parsed[report.full_name.lower()] = report
        reports: list[Self] = []
        for repository in repositories:
            report = parsed.get(repository.key) or cls(
                full_name=repository.full_name,
                path=posixpath.join(work_dir, repository.name),
                action="error",
                error="The sandbox reported nothing for this repository",
            )
            report.id = str(repository.id)
            reports.append(report)
        return reports

    @staticmethod
    def _iso_timestamp(raw: str | None) -> str | None:
        epoch = _int_or_none(raw)
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, UTC).isoformat()

    async def load_custom_instructions(self, repository: Repository) -> None:
        from agent.dashboard.agent_instructions import get_repo_agent_instructions

        try:
            self.custom_instructions = await get_repo_agent_instructions(
                repository.owner, repository.name
            )
        except Exception:
            logger.warning(
                "Failed to load repository custom agent instructions",
                extra={"repository_full_name": repository.full_name},
                exc_info=True,
            )

    async def record_probed_facts(self, repository: Repository) -> None:
        """Persist onto the row what the checkout taught us about the repository."""
        if not self.default_branch:
            return
        try:
            await repository.update_default_branch(self.default_branch)
        except Exception:
            logger.warning(
                "Failed to record repository default branch",
                extra={"repository_full_name": repository.full_name},
                exc_info=True,
            )


class RejectedRepo(BaseModel):
    """A repository the thread will not work in, and why."""

    full_name: str
    reason: str

    @classmethod
    async def check_access(cls, full_name: str, github_login: str) -> Self | None:
        """``None`` when the thread may work in ``full_name``, otherwise the rejection."""
        owner, _, name = full_name.partition("/")
        if not is_repo_allowed({"owner": owner, "name": name}):
            return cls(full_name=full_name, reason="not on the deployment allowlist")
        login = github_login.strip()
        if not login:
            return cls(
                full_name=full_name,
                reason="cannot verify access: this thread has no github_login",
            )
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            return cls(full_name=full_name, reason=f"access denied: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to verify repository access",
                extra={"repository_full_name": full_name},
                exc_info=True,
            )
            return cls(full_name=full_name, reason=f"failed to verify access: {exc}")
        return None


class AddReposResult(BaseModel):
    """What ``add_repos`` hands back to the model."""

    ok: bool = True
    error: str | None = None
    work_dir: str = ""
    added: list[str] = Field(default_factory=list)
    already_present: list[str] = Field(default_factory=list)
    rejected: list[RejectedRepo] = Field(default_factory=list)
    repos: list[RepoReport] = Field(default_factory=list)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@tool("add_repos", description=load_prompt("tools/add_repos.md"))
async def add_repos(full_names: list[str]) -> dict[str, Any]:
    """Implement the `add_repos` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return AddReposResult(ok=False, error="No thread_id in the current run config").dump()

    result = AddReposResult()
    requested: list[str] = []
    for raw in full_names or []:
        try:
            full_name = normalize_repo_full_name(str(raw))
        except ValueError:
            result.rejected.append(
                RejectedRepo(
                    full_name=raw if isinstance(raw, str) else str(raw),
                    reason="not a simple owner/name repository string",
                )
            )
            continue
        if full_name.lower() not in {name.lower() for name in requested}:
            requested.append(full_name)

    client = get_client(url=langgraph_url())
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read thread repositories", exc_info=True)
        result.ok = False
        result.error = f"Failed to read this thread's repositories: {exc}"
        return result.dump()

    existing = await thread_repositories(thread_metadata(thread))
    existing_keys = {repository.key for repository in existing}
    added: list[Repository] = []
    for full_name in requested:
        if full_name.lower() in existing_keys:
            result.already_present.append(full_name)
            continue
        rejection = await RejectedRepo.check_access(full_name, cfg.github_login or "")
        if rejection is not None:
            result.rejected.append(rejection)
            continue
        added.append(await Repository.ensure(full_name))

    repositories = [*existing, *added]
    if added:
        try:
            await client.threads.update(
                thread_id=thread_id,
                metadata={REPOSITORY_IDS_METADATA_KEY: repository_ids_metadata(repositories)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record thread repositories", exc_info=True)
            result.ok = False
            names = ", ".join(repository.full_name for repository in added)
            result.error = f"Failed to record {names}: {exc}"
            return result.dump()
        result.added = [repository.full_name for repository in added]

    try:
        backend = await get_sandbox_backend(thread_id)
        result.work_dir = await resolve_sandbox_work_dir(backend)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to reach the thread sandbox", exc_info=True)
        result.ok = False
        result.error = f"Failed to reach this thread's sandbox: {exc}"
        return result.dump()

    if not repositories:
        return result.dump()

    script = RepoReport.probe_script(result.work_dir, repositories)
    try:
        executed = await backend.aexecute(script, timeout=_EXECUTE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to verify repository checkouts", exc_info=True)
        result.ok = False
        result.error = f"Failed to verify repository checkouts: {exc}"
        return result.dump()

    result.repos = RepoReport.parse_output(executed.output or "", repositories, result.work_dir)
    for repository, report in zip(repositories, result.repos, strict=True):
        await report.load_custom_instructions(repository)
        await report.record_probed_facts(repository)
    return result.dump()
