"""Auto-fix CI failures and review feedback on agent-authored pull requests.

This is the shared core for "PR babysitting": when a CI check fails (or a
reviewer leaves actionable feedback) on a PR that Open SWE opened, locate the
originating agent thread and dispatch a confidence-gated fix run on it.

Both the GitHub webhook path (:mod:`agent.webapp`) and the polling fallback
(:mod:`agent.ci_monitor`) call into here, so all the skip-rules, dedupe, and
loop-capping live in one place. Skip-rules mirror Cursor/Claude Code:

* Only PRs Open SWE authored (an agent thread with this ``pr_url`` exists).
* Skip failures inherited from the base branch.
* Skip when the latest commit was authored by a human (don't fight pushes).
* Dedupe per (head SHA + failing-check set); cap total attempts.
* Honor team ``autofix_enabled`` / ``trigger_mode`` and the per-PR opt-out.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph_sdk import get_client

from .dashboard.autofix_state import is_pr_autofix_disabled
from .dashboard.enabled_repos import is_review_repo_enabled
from .dashboard.team_settings import get_autofix_settings
from .reviewer_findings import REVIEWER_THREAD_KIND
from .utils.dashboard_links import dashboard_thread_url
from .utils.github_app import get_github_app_installation_token
from .utils.github_checks import post_autofix_status_check
from .utils.github_ci import (
    fetch_open_pr_for_branch,
    fetch_pr,
    head_commit_author_login,
    list_failing_check_runs,
    list_failing_statuses,
    names_failing_on_base,
)
from .utils.github_org_membership import INTERNAL_BOT_LOGINS
from .utils.thread_ops import (
    is_thread_active,
    langgraph_client,
    queue_message_for_thread,
)

logger = logging.getLogger(__name__)

# Hard cap on auto-fix follow-ups per PR so a failure the agent can't resolve
# doesn't loop forever (Cursor caps at 10).
MAX_AUTOFIX_ATTEMPTS = 10
# Keep the dedupe list bounded on thread metadata.
_MAX_HANDLED_KEYS = 30


def _dedupe_key(head_sha: str, failing_names: list[str]) -> str:
    return f"{head_sha}:" + ",".join(sorted(failing_names))


async def find_agent_thread_for_pr(pr_url: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(thread_id, metadata)`` of the agent thread that opened ``pr_url``.

    Reviewer threads are skipped — only the coding-agent thread can push fixes.
    """
    if not pr_url:
        return None
    client = get_client()
    try:
        threads = await client.threads.search(metadata={"pr_url": pr_url}, limit=10)
    except Exception:  # noqa: BLE001
        logger.debug("Could not search threads for PR %s", pr_url, exc_info=True)
        return None
    for thread in threads or []:
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        if not isinstance(metadata, dict):
            continue
        if metadata.get("kind") == REVIEWER_THREAD_KIND:
            continue
        if metadata.get("agent_kind") != "agent":
            continue
        thread_id = thread.get("thread_id") or thread.get("id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id, metadata
    return None


def _build_ci_fix_prompt(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    branch: str,
    head_sha: str,
    failing_checks: list[dict[str, Any]],
) -> str:
    lines = []
    for check in failing_checks:
        name = check.get("name", "check")
        conclusion = check.get("conclusion", "failure")
        details = check.get("details_url") or ""
        suffix = f" — {details}" if details else ""
        lines.append(f"- {name} ({conclusion}){suffix}")
    failing_block = "\n".join(lines)
    return (
        "An automated CI check failed on a pull request you opened. Please "
        "investigate and fix it.\n\n"
        f"## Repository: {owner}/{repo}\n\n"
        f"## Pull Request: {pr_url} (#{pr_number})\n\n"
        f"## Branch: {branch}\n\n"
        f"## Head commit: {head_sha}\n\n"
        f"## Failing checks:\n{failing_block}\n\n"
        "Instructions:\n"
        "1. Make sure you are on the PR branch, then read the failing logs "
        "(e.g. `GH_TOKEN=dummy gh pr checks` and `GH_TOKEN=dummy gh run view "
        "<run-id> --log-failed`).\n"
        "2. Confidence gating — fix autonomously ONLY when the cause is clear "
        "and deterministic (lint/format, type errors, missing imports, failed "
        "assertions, snapshot updates, build errors). Commit and push to the "
        "existing branch; do NOT open a new PR.\n"
        "3. If the failure is ambiguous, flaky, infrastructure-related, appears "
        "pre-existing, or needs an architectural/design decision, do NOT guess. "
        "Post a short PR comment explaining what you found and what input you "
        "need, then stop.\n"
        "4. Never force-push. Never weaken or delete test assertions just to go "
        "green unless the behavior change is intentional and correct.\n"
        "5. After you push, CI re-runs automatically — you don't need to merge."
    )


def _build_review_feedback_prompt(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    reviewer: str,
    body: str,
) -> str:
    return (
        "A reviewer left feedback on a pull request you opened. Please respond.\n\n"
        f"## Repository: {owner}/{repo}\n\n"
        f"## Pull Request: {pr_url} (#{pr_number})\n\n"
        f"## Reviewer: {reviewer}\n\n"
        f"## Feedback:\n{body}\n\n"
        "Instructions:\n"
        "1. If the requested change is unambiguous (rename, typo, missing null "
        "check, small refactor, add a test), make it, commit, and push to the "
        "existing branch.\n"
        "2. If the comment is ambiguous, opinion-based, or needs a design "
        "decision, reply on the PR asking for clarification instead of guessing.\n"
        "3. Never force-push. Reply to the reviewer on GitHub to explain what "
        "you changed."
    )


async def _thread_autofix_state(metadata: dict[str, Any]) -> tuple[int, list[str], str]:
    attempts = metadata.get("autofix_attempts")
    attempts = attempts if isinstance(attempts, int) and attempts >= 0 else 0
    handled = metadata.get("autofix_handled")
    handled = [h for h in handled if isinstance(h, str)] if isinstance(handled, list) else []
    github_login = metadata.get("github_login")
    github_login = github_login if isinstance(github_login, str) else ""
    return attempts, handled, github_login


async def _record_attempt(
    thread_id: str, *, attempts: int, handled: list[str], dedupe_key: str, head_sha: str
) -> None:
    new_handled = [*handled, dedupe_key][-_MAX_HANDLED_KEYS:]
    try:
        await get_client().threads.update(
            thread_id=thread_id,
            metadata={
                "autofix_attempts": attempts + 1,
                "autofix_handled": new_handled,
                "autofix_last_head_sha": head_sha,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record auto-fix attempt for thread %s", thread_id, exc_info=True)


# Run sources the agent's GitHub-token resolver knows how to authenticate.
_AUTH_RESOLVABLE_SOURCES = frozenset(["github", "slack", "dashboard", "linear", "schedule"])


def _run_configurable(
    metadata: dict[str, Any], *, repo_config: dict[str, str], pr_number: int
) -> dict[str, Any]:
    """Build the run config for a fix run by reusing the PR thread's identity.

    The agent's GitHub-token resolver only authenticates known sources, so a
    bespoke ``github_ci`` source would fail in non-bot-token deployments. Reuse
    the originating thread's ``source`` + login/email so auth resolves exactly
    as it did for the run that opened the PR.
    """
    source = metadata.get("source")
    if source not in _AUTH_RESOLVABLE_SOURCES:
        source = "github"
    configurable: dict[str, Any] = {
        "source": source,
        "repo": repo_config,
        "pr_number": pr_number,
    }
    login = metadata.get("github_login")
    if isinstance(login, str) and login:
        configurable["github_login"] = login
    email = metadata.get("triggering_user_email")
    if isinstance(email, str) and email:
        configurable["user_email"] = email
    return configurable


async def _dispatch_or_queue(thread_id: str, prompt: str, *, configurable: dict[str, Any]) -> str:
    if await is_thread_active(thread_id):
        logger.info("Agent thread %s busy; queuing auto-fix message", thread_id)
        await queue_message_for_thread(thread_id, prompt)
        return "queued"
    client = langgraph_client()
    await client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable},
        if_not_exists="create",
    )
    logger.info(
        "Created auto-fix run for thread %s (source=%s)", thread_id, configurable.get("source")
    )
    return "dispatched"


async def handle_ci_failure(
    *,
    repo_config: dict[str, str],
    branch: str,
    head_sha: str,
    token: str | None = None,
    source: str = "github_ci",
    failing_checks: list[dict[str, Any]] | None = None,
    pr: dict[str, Any] | None = None,
) -> str:
    """Auto-fix failing CI on an agent-authored PR. Returns a status string."""
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    if not owner or not repo:
        return "missing_repo"

    settings = await get_autofix_settings()
    if not settings["autofix_enabled"]:
        return "autofix_disabled_team"
    if not await is_review_repo_enabled(owner, repo):
        return "repo_not_enabled"

    if token is None:
        token = await get_github_app_installation_token()
    if not token:
        logger.warning("No GitHub App token for CI auto-fix on %s/%s", owner, repo)
        return "no_token"

    if pr is None:
        if not branch:
            return "no_branch"
        pr = await fetch_open_pr_for_branch(owner=owner, repo=repo, branch=branch, token=token)
    if not pr:
        return "no_open_pr"

    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        return "no_pr_number"
    pr_url = pr.get("html_url") or pr.get("url") or ""
    base_sha = (pr.get("base") or {}).get("sha", "")
    branch = branch or (pr.get("head") or {}).get("ref", "")
    head_sha = head_sha or (pr.get("head") or {}).get("sha", "")
    if not head_sha:
        return "no_head_sha"

    if await is_pr_autofix_disabled(owner, repo, pr_number):
        return "pr_disabled"

    found = await find_agent_thread_for_pr(pr_url)
    if found is None:
        return "no_agent_thread"
    thread_id, metadata = found

    attempts, handled, github_login = await _thread_autofix_state(metadata)

    if settings["trigger_mode"] == "manual":
        return "trigger_manual"
    if settings["trigger_mode"] == "once_per_pr" and attempts >= 1:
        return "once_per_pr_done"
    if attempts >= MAX_AUTOFIX_ATTEMPTS:
        await post_autofix_status_check(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            token=token,
            title="Auto-fix limit reached",
            summary=(
                f"Open SWE has attempted {attempts} auto-fixes on this PR and "
                "stopped to avoid a loop. Push a commit or comment to continue."
            ),
            details_url=dashboard_thread_url(thread_id),
        )
        return "max_attempts"

    if failing_checks is None:
        runs = await list_failing_check_runs(owner=owner, repo=repo, ref=head_sha, token=token)
        statuses = await list_failing_statuses(owner=owner, repo=repo, ref=head_sha, token=token)
        if runs is None and statuses is None:
            return "ci_read_failed"
        failing_checks = (runs or []) + (statuses or [])
    if not failing_checks:
        return "no_failing_checks"

    base_failing = await names_failing_on_base(
        owner=owner, repo=repo, base_sha=base_sha, token=token
    )
    actionable = [c for c in failing_checks if c.get("name") not in base_failing]
    if not actionable:
        return "all_failing_on_base"

    failing_names = [c.get("name", "") for c in actionable]
    dedupe_key = _dedupe_key(head_sha, failing_names)
    if dedupe_key in handled:
        return "already_handled"

    author_login = await head_commit_author_login(owner=owner, repo=repo, sha=head_sha, token=token)
    if (
        author_login is not None
        and author_login not in INTERNAL_BOT_LOGINS
        and (not github_login or author_login.lower() != github_login.lower())
    ):
        logger.info(
            "Skipping CI auto-fix on %s/%s#%s: head commit %s authored by human %s",
            owner,
            repo,
            pr_number,
            head_sha,
            author_login,
        )
        return "human_commit"

    prompt = _build_ci_fix_prompt(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        branch=branch,
        head_sha=head_sha,
        failing_checks=actionable,
    )
    result = await _dispatch_or_queue(
        thread_id,
        prompt,
        configurable=_run_configurable(
            metadata, repo_config={"owner": owner, "name": repo}, pr_number=pr_number
        ),
    )
    await _record_attempt(
        thread_id, attempts=attempts, handled=handled, dedupe_key=dedupe_key, head_sha=head_sha
    )
    await post_autofix_status_check(
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        token=token,
        title=f"Auto-fixing {len(actionable)} failing check(s)",
        summary=(
            "Open SWE is investigating the failing checks and will push a fix if "
            "the cause is clear. Track progress in the linked run."
        ),
        details_url=dashboard_thread_url(thread_id),
    )
    return result


async def handle_review_feedback(
    *,
    repo_config: dict[str, str],
    pr_number: int,
    pr_url: str,
    reviewer: str,
    body: str,
    token: str | None = None,
    source: str = "github_review",
) -> str:
    """Auto-respond to a human review comment on an agent-authored PR."""
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    if not owner or not repo or not pr_url:
        return "missing_repo"

    settings = await get_autofix_settings()
    if not settings["autofix_enabled"]:
        return "autofix_disabled_team"
    if settings["trigger_mode"] == "manual":
        return "trigger_manual"
    if not await is_review_repo_enabled(owner, repo):
        return "repo_not_enabled"
    if await is_pr_autofix_disabled(owner, repo, pr_number):
        return "pr_disabled"

    found = await find_agent_thread_for_pr(pr_url)
    if found is None:
        return "no_agent_thread"
    thread_id, metadata = found

    prompt = _build_review_feedback_prompt(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        reviewer=reviewer,
        body=body,
    )
    return await _dispatch_or_queue(
        thread_id,
        prompt,
        configurable=_run_configurable(
            metadata, repo_config={"owner": owner, "name": repo}, pr_number=pr_number
        ),
    )


async def sweep_open_prs() -> dict[str, int]:
    """Poll open agent-authored PRs and auto-fix failing CI / flag conflicts.

    The polling fallback for deployments without reliable CI webhooks, and the
    only path that can react to base-branch merge conflicts (GitHub emits no
    webhook for those).
    """
    counts = {"scanned": 0, "dispatched": 0, "queued": 0, "conflicts": 0}
    token = await get_github_app_installation_token()
    if not token:
        logger.warning("CI monitor sweep: no GitHub App token")
        return counts
    client = get_client()
    try:
        threads = await client.threads.search(
            metadata={"agent_kind": "agent", "pr_state": "open"}, limit=100
        )
    except Exception:  # noqa: BLE001
        logger.warning("CI monitor sweep: thread search failed", exc_info=True)
        return counts

    for thread in threads or []:
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        if not isinstance(metadata, dict):
            continue
        repo = metadata.get("repo")
        pr_number = metadata.get("pr_number")
        branch = metadata.get("branch_name")
        if not isinstance(repo, dict) or not isinstance(pr_number, int):
            continue
        owner = repo.get("owner", "")
        name = repo.get("name", "")
        if not owner or not name:
            continue
        counts["scanned"] += 1
        pr = await fetch_pr(owner=owner, repo=name, pr_number=pr_number, token=token)
        if not pr:
            continue
        head_sha = (pr.get("head") or {}).get("sha", "")
        branch = (pr.get("head") or {}).get("ref", "") or (
            branch if isinstance(branch, str) else ""
        )
        if pr.get("mergeable_state") == "dirty":
            counts["conflicts"] += 1
            await _flag_merge_conflict(
                owner=owner,
                repo=name,
                pr_number=pr_number,
                pr_url=pr.get("html_url") or "",
                head_sha=head_sha,
                token=token,
            )
            continue
        result = await handle_ci_failure(
            repo_config={"owner": owner, "name": name},
            branch=branch,
            head_sha=head_sha,
            token=token,
            source="ci_monitor",
            pr=pr,
        )
        if result == "dispatched":
            counts["dispatched"] += 1
        elif result == "queued":
            counts["queued"] += 1
    logger.info("CI monitor sweep complete: %s", counts)
    return counts


async def _flag_merge_conflict(
    *, owner: str, repo: str, pr_number: int, pr_url: str, head_sha: str, token: str
) -> None:
    """Ask the agent to rebase a PR that has merge conflicts with its base."""
    if await is_pr_autofix_disabled(owner, repo, pr_number):
        return
    found = await find_agent_thread_for_pr(pr_url)
    if found is None:
        return
    thread_id, metadata = found
    if metadata.get("autofix_conflict_head") == head_sha:
        return
    prompt = (
        f"The pull request you opened (#{pr_number}, {pr_url}) now has merge "
        "conflicts with its base branch. Rebase or merge the base branch into "
        "the PR branch, resolve the conflicts carefully, and push. If a "
        "conflict resolution is ambiguous, comment on the PR and ask before "
        "guessing. Never force-push over commits already on the remote."
    )
    await _dispatch_or_queue(
        thread_id,
        prompt,
        configurable=_run_configurable(
            metadata, repo_config={"owner": owner, "name": repo}, pr_number=pr_number
        ),
    )
    try:
        await get_client().threads.update(
            thread_id=thread_id, metadata={"autofix_conflict_head": head_sha}
        )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to record conflict head for thread %s", thread_id, exc_info=True)
