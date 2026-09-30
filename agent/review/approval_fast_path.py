"""Deterministic approval before reviewer sandbox or scout startup."""

import asyncio
import hashlib
import logging
import re
from pathlib import PurePosixPath
from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import text

from agent.database import postgres
from agent.github.ci import fetch_required_checks
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_status import fetch_unresolved_review_threads
from agent.github.pull_requests import PullRequest
from agent.review.approval_rules import (
    COMPILER_VERSION,
    ApprovalProgram,
    DocumentationRule,
    cached_approval_program,
    policy_hash,
)
from agent.review.approvals import approval_mode_for, fetch_approvals_md
from agent.review.diff import fetch_pr_diff
from agent.review.findings import get_thread_metadata, list_findings, set_reviewer_thread_metadata
from agent.review.publish import (
    clear_review_started_comment,
    post_pull_request_review,
    settle_review_check_run,
)
from agent.run_config import RunConfig
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)


class ChangedFile(BaseModel):
    model_config = ConfigDict(strict=True)

    filename: str
    status: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    patch: str


class _Ref(BaseModel):
    sha: str
    ref: str


class _User(BaseModel):
    login: str


class _PullRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    state: str
    draft: bool
    head: _Ref
    base: _Ref
    user: _User
    changed_files: int = Field(ge=1, le=50)


class _Review(BaseModel):
    user: _User
    state: str


class _CheckApp(BaseModel):
    id: int


class _Check(BaseModel):
    id: int
    name: str
    status: str
    conclusion: str | None
    app: _CheckApp


class _Status(BaseModel):
    context: str
    state: str


class ApprovalAttempt(BaseModel):
    status: Literal["publishing", "published"]
    head_sha: str
    policy_hash: str
    created_at: str
    review_id: int | None = None


ATTEMPTS = TypedStore(("deterministic_approval_attempts",), ApprovalAttempt)


def _allowed_path(path: str, rule: DocumentationRule) -> bool:
    parts = path.split("/")
    if (
        not (path == "README.md" or path.startswith("docs/"))
        or any(not part or part.startswith(".") for part in parts)
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        return False
    if PurePosixPath(path).suffix != ".md" or any(
        word in path.lower()
        for word in (
            "agents.md",
            "claude.md",
            "approval",
            "security",
            "auth",
            "token",
            "secret",
            "credential",
            "permission",
            "session",
            "webhook",
            "sandbox",
            "migration",
            "openwiki",
            "prompt",
            "reviewer",
        )
    ):
        return False
    return path in rule.paths or any(
        prefix.endswith("/") and path.startswith(prefix) for prefix in rule.prefixes
    )


def _complete_patch(lines: list[str]) -> bool:
    remaining_old = remaining_new = 0
    in_hunk = False
    for line in lines:
        if match := re.fullmatch(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@.*", line):
            if remaining_old or remaining_new:
                return False
            remaining_old = int(match[1]) if match[1] is not None else 1
            remaining_new = int(match[2]) if match[2] is not None else 1
            in_hunk = True
        elif in_hunk and line.startswith((" ", "-", "+")):
            remaining_old -= line[0] in {" ", "-"}
            remaining_new -= line[0] in {" ", "+"}
            if remaining_old < 0 or remaining_new < 0:
                return False
        elif not in_hunk or line != "\\ No newline at end of file":
            return False
    return in_hunk and remaining_old == remaining_new == 0


def matching_rule(
    program: ApprovalProgram, files: list[ChangedFile], diff: str
) -> DocumentationRule | None:
    """Require a complete regular-file diff, not just reassuring filenames or line counts."""
    if not files or len(files) > 50 or len(diff) > 250_000:
        return None
    sections = re.split(r"(?m)^diff --git ", diff)
    if sections[0] or len(sections) - 1 != len(files):
        return None
    by_name = {file.filename: file for file in files}
    seen: set[str] = set()
    for section in sections[1:]:
        lines = section.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return None
        header = re.fullmatch(r"a/(.+) b/(.+)", lines[0])
        if header is None or header[1] != header[2]:
            return None
        name = header[1]
        file = by_name.get(name)
        if file is None or name in seen or file.status not in {"added", "modified"}:
            return None
        seen.add(name)
        patch_start = next((i for i, line in enumerate(lines) if line.startswith("@@ ")), None)
        if patch_start is None:
            return None
        metadata = lines[1:patch_start]
        if file.status == "added":
            if len(metadata) != 4 or metadata[0] != "new file mode 100644":
                return None
            if re.fullmatch(r"index 0+\.\.[0-9a-f]+", metadata[1]) is None:
                return None
            old_path = "/dev/null"
        else:
            if (
                len(metadata) != 3
                or re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+ 100644", metadata[0]) is None
            ):
                return None
            old_path = f"a/{name}"
        if metadata[-2:] != [f"--- {old_path}", f"+++ b/{name}"]:
            return None
        patch = lines[patch_start:]
        if (
            not _complete_patch(patch)
            or sum(line.startswith("+") for line in patch) != file.additions
            or sum(line.startswith("-") for line in patch) != file.deletions
        ):
            return None
        if "\n".join(patch) != file.patch.removesuffix("\n"):
            return None
    changed = sum(file.additions + file.deletions for file in files)
    if not changed:
        return None
    return next(
        (
            rule
            for rule in program.rules
            if changed <= rule.max_changed_lines
            and all(_allowed_path(file.filename, rule) for file in files)
        ),
        None,
    )


async def _get(client: httpx2.AsyncClient, path: str) -> object:
    response = await github_request(client, "GET", f"{GITHUB_API_BASE}{path}")
    response.raise_for_status()
    return response.json()


async def _pages(client: httpx2.AsyncClient, path: str, key: str | None = None) -> list[object]:
    result: list[object] = []
    for page in range(1, 31):
        separator = "&" if "?" in path else "?"
        data = await _get(client, f"{path}{separator}per_page=100&page={page}")
        if key is not None:
            if not isinstance(data, dict) or key not in data:
                raise ValueError("Missing GitHub collection")
            data = data[key]
        if not isinstance(data, list):
            raise ValueError("Incomplete GitHub collection")
        result.extend(data)
        if len(data) < 100:
            return result
    raise ValueError("GitHub collection exceeded approval budget")


async def _eligible_snapshot(
    client: httpx2.AsyncClient, cfg: RunConfig, token: str
) -> _PullRequest | None:
    if not cfg.repo or not cfg.thread_id or not cfg.pr_number:
        return None
    owner, repo, number = cfg.repo.owner, cfg.repo.name, cfg.pr_number
    root = f"/repos/{owner}/{repo}"
    pr = _PullRequest.model_validate(await _get(client, f"{root}/pulls/{number}"))
    if pr.state != "open" or pr.draft or pr.head.sha != cfg.head_sha or pr.base.sha != cfg.base_sha:
        return None
    if any(
        finding.get("status", "open") == "open"
        for finding in await list_findings(cfg.thread_id, strict=True)
    ):
        return None
    threads = await fetch_unresolved_review_threads(client, owner, repo, number, strict=True)
    if threads is None or threads:
        return None
    reviews = TypeAdapter(list[_Review]).validate_python(
        await _pages(client, f"{root}/pulls/{number}/reviews")
    )
    latest: dict[str, str] = {}
    for review in reviews:
        if review.state not in {"COMMENTED", "PENDING"}:
            latest[review.user.login.lower()] = review.state
    if any(state not in {"APPROVED", "DISMISSED"} for state in latest.values()):
        return None
    checks = TypeAdapter(list[_Check]).validate_python(
        await _pages(client, f"{root}/commits/{pr.head.sha}/check-runs?filter=latest", "check_runs")
    )
    statuses = TypeAdapter(list[_Status]).validate_python(
        await _pages(client, f"{root}/commits/{pr.head.sha}/statuses")
    )
    metadata = await get_thread_metadata(cfg.thread_id)
    own_check = metadata.get("review_check_run_id")
    external_checks = [check for check in checks if check.id != own_check]
    latest_statuses: dict[str, str] = {}
    for status in statuses:
        latest_statuses.setdefault(status.context, status.state)
    if not external_checks and not latest_statuses:
        return None
    if any(
        check.status != "completed" or check.conclusion != "success" for check in external_checks
    ) or any(state != "success" for state in latest_statuses.values()):
        return None
    required = await fetch_required_checks(owner=owner, repo=repo, branch=pr.base.ref, token=token)
    if required is None:
        return None
    if any(
        not any(
            check.name == requirement.name
            and (requirement.app_id is None or check.app.id == requirement.app_id)
            for check in checks
        )
        and not (requirement.app_id is None and requirement.name in latest_statuses)
        for requirement in required
    ):
        return None
    current = _PullRequest.model_validate(await _get(client, f"{root}/pulls/{number}"))
    return current if current == pr else None


async def try_deterministic_approval(cfg: RunConfig, *, token: str) -> bool:
    """Serialize fast-path publications across workers; any uncertainty falls back to review."""
    if not cfg.repo or not cfg.pr_number:
        return False
    identity = f"approval:{cfg.repo.full_name}:{cfg.pr_number}"
    lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
    try:
        async with asyncio.timeout(125), postgres.transaction() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            return await _try_deterministic_approval(cfg, token=token)
    except Exception:
        logger.exception("Deterministic approval failed", extra={"repository": cfg.repo.full_name})
        return False


async def _try_deterministic_approval(cfg: RunConfig, *, token: str) -> bool:
    if (
        not cfg.repo
        or not cfg.pr_number
        or not cfg.thread_id
        or not cfg.base_sha
        or not cfg.head_sha
    ):
        return False
    if cfg.is_eval or cfg.reviewer_event or cfg.finding_reply_id:
        return False
    owner, repo, number = cfg.repo.owner, cfg.repo.name, cfg.pr_number
    published = False
    try:
        async with asyncio.timeout(120):
            mode = await approval_mode_for(owner, repo)
            if mode == "off":
                return False
            policy = await fetch_approvals_md(owner, repo, cfg.base_sha, token=token)
            if policy is None:
                return False
            program = await cached_approval_program(owner, repo, policy)
            if (
                program is None
                or not program.rules
                or program.policy_hash != policy_hash(policy)
                or program.compiler_version != COMPILER_VERSION
            ):
                return False
            async with github_client(token=token) as client:
                pr = await _eligible_snapshot(client, cfg, token)
                if pr is None:
                    return False
                files = TypeAdapter(list[ChangedFile]).validate_python(
                    await _pages(client, f"/repos/{owner}/{repo}/pulls/{number}/files")
                )
                if len(files) != pr.changed_files:
                    return False
                diff = await fetch_pr_diff(owner=owner, repo=repo, pr_number=number, token=token)
                rule = matching_rule(program, files, diff) if diff else None
                if rule is None:
                    return False
                key = f"{owner}/{repo}:{number}:{cfg.head_sha}:{program.policy_hash}:{mode}"
                attempt = await ATTEMPTS.get(key)
                if attempt is not None and attempt.status != "published":
                    return False
                if (
                    await fetch_approvals_md(owner, repo, cfg.base_sha, token=token) != policy
                    or await approval_mode_for(owner, repo) != mode
                    or await cached_approval_program(owner, repo, policy) != program
                    or await _eligible_snapshot(client, cfg, token) is None
                ):
                    return False
                body = (
                    "### Deterministic approval assessment\n\n"
                    + ("Approved" if mode == "approve" else "Would approve (dry run)")
                    + ": every changed file matches the compiled Markdown documentation rule. "
                    "Checks passed and no outstanding findings or review objections were present. "
                    "No agent code review was performed.\n\n"
                    f"Policy SHA-256: `{program.policy_hash}`; head: `{cfg.head_sha}`."
                )
                if attempt is None:
                    attempt = ApprovalAttempt(
                        status="publishing",
                        head_sha=cfg.head_sha,
                        policy_hash=program.policy_hash,
                        created_at=now_iso(),
                    )
                    await ATTEMPTS.put(key, attempt)
                    response = await post_pull_request_review(
                        owner=owner,
                        repo=repo,
                        pr_number=number,
                        head_sha=cfg.head_sha,
                        body=body,
                        inline_comments=[],
                        token=token,
                        event="APPROVE" if mode == "approve" else "COMMENT",
                    )
                    if response is None or not isinstance(response.get("id"), int):
                        return False
                    published = True
                    attempt.status = "published"
                    attempt.review_id = response["id"]
                    await ATTEMPTS.put(key, attempt)
    except Exception:
        logger.exception(
            "Deterministic approval pipeline failed",
            extra={"repository": f"{owner}/{repo}", "pr_number": number, "published": published},
        )
        if not published:
            return False
    if program is None:
        return False
    try:
        await PullRequest(owner=owner, repo=repo, number=number).link_review(
            reviewer_thread_id=cfg.thread_id, head_sha=cfg.head_sha, finding_count=0
        )
        await set_reviewer_thread_metadata(
            cfg.thread_id,
            last_reviewed_sha=cfg.head_sha,
            extra={"deterministic_approval_policy_hash": program.policy_hash},
        )
        await clear_review_started_comment(
            thread_id=cfg.thread_id, owner=owner, repo=repo, token=token
        )
        await settle_review_check_run(
            thread_id=cfg.thread_id,
            owner=owner,
            repo=repo,
            token=token,
            conclusion="success",
            title="Deterministic approval assessment",
            summary=body,
        )
    except Exception:
        logger.exception(
            "Deterministic approval published but bookkeeping failed",
            extra={"repository": f"{owner}/{repo}", "pr_number": number},
        )
    return True
