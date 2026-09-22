"""Drive an expedited approval from ``waiting`` to a posted card, and retire it.

Evaluation is re-entrant and cheap when nothing changed. GitHub webhooks call
it for the affected repository as events arrive; a per-approval cron is the
fallback. Network reads happen unlocked; the state transition is applied under
a row lock with the expected prior state as a precondition.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from langgraph_sdk import get_client

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.dispatch import dispatch_agent_run
from agent.expedited_review import card
from agent.expedited_review.approvals import (
    REQUIRED_APPROVALS,
    ApprovalState,
    ExpeditedApproval,
)
from agent.expedited_review.diff_image import render_diff_png
from agent.expedited_review.eligibility import (
    ChangedFile,
    Ineligible,
    assess_eligibility,
    fetch_changed_files,
)
from agent.expedited_review.readiness import Readiness, assess_readiness
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.ci import head_sha_from_check_payload
from agent.github.pull_requests import PullRequest
from agent.prompts import render_prompt
from agent.slack.blocks import block_payload
from agent.slack.client import (
    add_slack_reaction,
    post_slack_thread_reply,
    post_slack_thread_reply_with_ts,
    update_slack_message,
    upload_slack_thread_file,
)

logger = logging.getLogger(__name__)

CRON_TASK = "expedited_review"
CRON_KIND = "expedited_review_watch"
CRON_SCHEDULE = "*/5 * * * *"
WATCHED_GITHUB_EVENTS = frozenset(
    {
        "check_run",
        "check_suite",
        "workflow_run",
        "status",
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
    }
)


async def repo_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(installation_id=installation_id)


async def _create_cron(approval_id: UUID) -> str:
    key = str(approval_id)
    cron = await get_client().crons.create(
        "scheduler",
        schedule=CRON_SCHEDULE,
        input={"task": CRON_TASK, "watch_key": key},
        config={"configurable": {"task": CRON_TASK, "watch_key": key}},
        metadata={"kind": CRON_KIND, "watch_key": key},
        timezone="UTC",
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError("expedited review cron creation did not return a cron_id")
    return cron_id


async def _delete_cron(cron_id: str) -> None:
    if not cron_id:
        return
    try:
        await get_client().crons.delete(cron_id)
    except Exception:
        logger.warning("Failed to delete expedited review cron", extra={"cron_id": cron_id})


async def start_approval(
    *,
    pull_request: PullRequest,
    thread_id: str,
    head_sha: str,
    diff_fingerprint: str,
    slack_channel_id: str,
    slack_thread_ts: str,
    run_config: dict[str, Any],
) -> ExpeditedApproval:
    """Open a ``waiting`` approval for this revision; an active one is reused."""
    existing = await ExpeditedApproval.active_for(
        pull_request.owner, pull_request.repo, pull_request.number
    )
    if existing is not None:
        if existing.head_sha == head_sha:
            return existing
        await retire(existing, "superseded", "A newer revision was enrolled.")
    approval = ExpeditedApproval(
        pull_request_id=pull_request.id,
        thread_id=thread_id,
        head_sha=head_sha,
        diff_fingerprint=diff_fingerprint,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
        run_config=run_config,
    )
    approval = await approval.save()
    try:
        approval.cron_id = await _create_cron(approval.id)
        return await approval.save()
    except Exception:
        async with ExpeditedApproval.locked(approval.id) as (session, row):
            if row is not None:
                await session.delete(row)
        raise


async def transition(
    approval_id: UUID, *, expected: tuple[ApprovalState, ...], **changes: Any
) -> ExpeditedApproval | None:
    """Apply ``changes`` if the row is still in one of ``expected``; else ``None``."""
    async with ExpeditedApproval.locked(approval_id) as (_, row):
        if row is None or row.state not in expected:
            return None
        for name, value in changes.items():
            setattr(row, name, value)
        return row


async def _files_for(approval: ExpeditedApproval, token: str) -> list[ChangedFile]:
    pr = approval.pull_request
    files = await fetch_changed_files(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    return files or []


async def _diff_image_id(approval: ExpeditedApproval, files: list[ChangedFile]) -> str | None:
    """A hosted-but-unposted PNG of the diff, which the card renders inline."""
    shown, _ = ChangedFile.rendered(files)
    if not shown:
        return None
    try:
        png = await asyncio.to_thread(render_diff_png, shown)
    except (ValueError, OSError) as exc:
        logger.warning(
            "Failed to render expedited review diff image",
            extra={"approval_id": str(approval.id), "render_error": str(exc)},
        )
        return None
    file_id, error = await upload_slack_thread_file(
        None, None, f"diff-{approval.head_sha[:12]}.png", png, title="Diff"
    )
    if not file_id:
        logger.warning(
            "Failed to upload expedited review diff image",
            extra={"approval_id": str(approval.id), "slack_error": error},
        )
    return file_id


async def _update_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile], outcome: str | None
) -> None:
    if not approval.slack_channel_id or not approval.slack_message_ts:
        return
    diff_image_id = await _diff_image_id(approval, files)
    if outcome is None:
        text, blocks = card.open_card(
            approval,
            title=title,
            files=files,
            failing_checks=approval.advisory_failures,
            diff_image_id=diff_image_id,
        )
    else:
        text, blocks = card.closed_card(
            approval, title=title, files=files, outcome=outcome, diff_image_id=diff_image_id
        )
    ok, error = await update_slack_message(
        approval.slack_channel_id, approval.slack_message_ts, text, blocks=block_payload(blocks)
    )
    if not ok:
        logger.warning(
            "Failed to update expedited review card",
            extra={"approval_id": str(approval.id), "slack_error": error},
        )


async def refresh_card(approval: ExpeditedApproval, *, outcome: str | None = None) -> None:
    """Re-render the card from current state; used after votes and outcomes."""
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    files = await _files_for(approval, token) if token else []
    await _update_card(approval, title=pr.title, files=files, outcome=outcome)


async def notify_agent(approval: ExpeditedApproval, prompt: str) -> None:
    """Wake the enrolling agent thread once with ``prompt``."""
    if not approval.thread_id:
        return
    pr = approval.pull_request
    configurable = dict(approval.run_config)
    configurable.update(
        {
            "source": configurable.get("source") or "slack",
            "repo": {"owner": pr.owner, "name": pr.repo},
            "pr_number": pr.number,
        }
    )
    try:
        await dispatch_agent_run(
            approval.thread_id,
            prompt,
            configurable,
            source=str(configurable["source"]),
            metadata={},
            multitask_strategy="enqueue",
        )
    except Exception:
        logger.warning(
            "Failed to notify agent about expedited review outcome",
            extra={"approval_id": str(approval.id)},
            exc_info=True,
        )


async def retire(
    approval: ExpeditedApproval,
    state: ApprovalState,
    outcome: str,
    *,
    expected: tuple[ApprovalState, ...] = ("waiting", "open", "merging"),
    agent_prompt: str | None = None,
) -> ExpeditedApproval | None:
    updated = await transition(approval.id, expected=expected, state=state, detail=outcome)
    if updated is None:
        return None
    await _delete_cron(updated.cron_id)
    if updated.slack_message_ts:
        await refresh_card(updated, outcome=outcome)
    elif (location := updated.slack_location) is not None:
        await post_slack_thread_reply(
            location[0], location[1], f"*Expedited review:* {outcome} {updated.pull_request.url}"
        )
    if agent_prompt:
        await notify_agent(updated, agent_prompt)
    return updated


async def _post_card(
    approval: ExpeditedApproval, readiness: Readiness, files: list[ChangedFile]
) -> str:
    location = approval.slack_location
    if location is None:
        await retire(approval, "failed", "No Slack thread to post the review card in.")
        return "failed"
    advisory = [] if readiness.snapshot.failures_are_required else readiness.snapshot.failing_checks
    text, blocks = card.open_card(
        approval,
        title=readiness.snapshot.title,
        files=files,
        failing_checks=advisory,
        diff_image_id=await _diff_image_id(approval, files),
    )
    message_ts, error = await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=approval.thread_id or None,
        reply_broadcast=True,
    )
    if not message_ts:
        logger.warning(
            "Failed to post expedited review card",
            extra={"approval_id": str(approval.id), "slack_error": error},
        )
        return "error"
    updated = await transition(
        approval.id,
        expected=("waiting",),
        state="open",
        slack_message_ts=message_ts,
        advisory_failures=advisory,
    )
    if updated is None:
        # Another evaluation won the transition. Retract the card this pass just
        # posted — never ``location[1]``, which is the thread's root message.
        await update_slack_message(
            location[0], message_ts, "This expedited review is no longer active.", blocks=None
        )
        return "stale"
    return "open"


async def _resume_quorum(approval: ExpeditedApproval, readiness: Readiness, token: str) -> str:
    """Merge an ``open`` approval that already has quorum.

    A transient blocker (mergeability still recomputing, a check rerunning) sends
    a ``merging`` row back to ``open`` with its votes intact. Nobody can vote it
    forward from there — both voters are refused as having already approved — so
    readiness recovering has to pick it back up.
    """
    if len(approval.approvals) < REQUIRED_APPROVALS:
        return "open"
    from agent.expedited_review.voting import complete_merge

    resumed = await transition(approval.id, expected=("open",), state="merging")
    if resumed is None:
        return "open"
    return await complete_merge(resumed, readiness, token)


async def evaluate_approval(key: str) -> str:
    """One readiness pass for the approval ``key`` (its id)."""
    try:
        approval_id = UUID(key)
    except ValueError:
        return "invalid"
    approval = await ExpeditedApproval.get(approval_id)
    if approval is None:
        return "missing"
    if not approval.active:
        await _delete_cron(approval.cron_id)
        return approval.state
    if (
        approval.state != "merging"
        and not (await get_workspace_settings()).expedited_review_enabled
    ):
        await retire(approval, "failed", "Expedited review was disabled for this instance.")
        return "failed"
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return "no_token"
    readiness = await assess_readiness(
        owner=pr.owner, repo=pr.repo, pr_number=pr.number, token=token
    )
    if readiness is None:
        return "error"
    snapshot = readiness.snapshot

    if snapshot.merged:
        await retire(approval, "merged", "Merged.")
        return "merged"
    if snapshot.state != "open":
        await retire(approval, "failed", "The pull request was closed.")
        return "failed"
    if snapshot.head_sha != approval.head_sha:
        await retire(
            approval,
            "superseded",
            f"A new commit ({snapshot.head_sha[:12]}) replaced the reviewed revision; "
            "votes were discarded.",
        )
        return "superseded"

    # Ahead of the merge, not just the card: retargeting the base branch changes
    # a pull request's diff without changing its head SHA, so the SHA check
    # above is not enough to know the votes still apply to what would land.
    files = await _files_for(approval, token)
    verdict = assess_eligibility(files)
    if isinstance(verdict, Ineligible):
        await retire(approval, "failed", f"No longer eligible: {verdict.reason}.")
        return "failed"
    if verdict.fingerprint != approval.diff_fingerprint:
        await retire(approval, "superseded", "The diff changed; votes were discarded.")
        return "superseded"

    if approval.state == "merging":
        from agent.expedited_review.voting import complete_merge

        return await complete_merge(approval, readiness, token)

    if approval.state == "open":
        if readiness.ready:
            return await _resume_quorum(approval, readiness, token)
        if readiness.terminal or snapshot.check_state == "failure":
            reason = "; ".join(readiness.blockers)
            await retire(
                approval,
                "failed",
                f"Withdrawn: {reason}.",
                agent_prompt=render_prompt(
                    "runs/expedited-review-withdrawn.md", pr_url=pr.url, reason=reason
                ),
            )
            return "failed"
        return "open"

    if readiness.terminal:
        await retire(approval, "failed", f"Cannot proceed: {'; '.join(readiness.blockers)}.")
        return "failed"
    if not readiness.ready:
        return "waiting"
    return await _post_card(approval, readiness, files)


def _repo_from_payload(payload: dict[str, Any]) -> tuple[str, str] | None:
    repository = payload.get("repository")
    owner_node = repository.get("owner") if isinstance(repository, dict) else None
    owner = owner_node.get("login") if isinstance(owner_node, dict) else None
    repo = repository.get("name") if isinstance(repository, dict) else None
    if isinstance(owner, str) and owner and isinstance(repo, str) and repo:
        return owner, repo
    return None


def _pr_number_from_payload(payload: dict[str, Any]) -> int | None:
    pull_request = payload.get("pull_request")
    number = pull_request.get("number") if isinstance(pull_request, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


async def handle_github_event(payload: dict[str, Any], event_type: str) -> dict[str, int]:
    """Re-evaluate active approvals in the event's repository that it could affect."""
    if event_type not in WATCHED_GITHUB_EVENTS:
        return {"matched": 0}
    repo = _repo_from_payload(payload)
    if repo is None:
        return {"matched": 0}
    try:
        approvals = await ExpeditedApproval.active_in_repo(*repo)
    except Exception:
        logger.warning("Expedited approval lookup failed", extra={"repo": "/".join(repo)})
        return {"matched": 0}
    if not approvals:
        return {"matched": 0}
    pr_number = _pr_number_from_payload(payload)
    head_sha = head_sha_from_check_payload(payload, event_type)
    matched = [
        approval
        for approval in approvals
        if (pr_number is not None and approval.pull_request.number == pr_number)
        or (head_sha and approval.head_sha == head_sha)
        or (pr_number is None and not head_sha)
    ]
    for approval in matched:
        try:
            await evaluate_approval(str(approval.id))
        except Exception:
            logger.warning(
                "Expedited approval evaluation failed",
                extra={"approval_id": str(approval.id)},
                exc_info=True,
            )
    return {"matched": len(matched)}


async def mark_merged(approval: ExpeditedApproval) -> None:
    """Card, reaction and cron once GitHub confirmed the merge."""
    updated = await retire(approval, "merged", "Merged.", expected=("merging", "open"))
    if updated is None:
        return
    location = updated.slack_location
    if location is not None:
        if not await add_slack_reaction(location[0], location[1], "merged"):
            await add_slack_reaction(location[0], location[1], "white_check_mark")
