"""Post an expedited review card, keep it in step with its votes, and close it.

Every Slack write here edits or posts the one card, and only because the agent
or a voter just acted: nothing runs in the background.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from langgraph_sdk import get_client

from agent.dispatch import dispatch_agent_run
from agent.expedited_review import card
from agent.expedited_review.approvals import ApprovalState, ExpeditedApproval
from agent.expedited_review.diff_image import render_diff_png
from agent.expedited_review.eligibility import ChangedFile, fetch_changed_files
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.slack.blocks import block_payload
from agent.slack.client import (
    add_slack_reaction,
    post_slack_thread_reply_with_ts,
    update_slack_message,
    upload_slack_thread_file,
    wait_for_slack_file,
)

logger = logging.getLogger(__name__)

LEGACY_CRON_TASK = "expedited_review"
_LEGACY_CRON_KIND = "expedited_review_watch"


async def repo_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(installation_id=installation_id)


async def delete_legacy_crons(watch_key: str) -> dict[str, int]:
    """Delete the per-approval crons that still tick the scheduler for ``watch_key``."""
    client = get_client()
    crons = await client.crons.search(
        metadata={"kind": _LEGACY_CRON_KIND, "watch_key": watch_key}, limit=10
    )
    deleted = 0
    for cron in crons or []:
        cron_id = cron.get("cron_id") if isinstance(cron, dict) else None
        if not isinstance(cron_id, str) or not cron_id:
            continue
        try:
            await client.crons.delete(cron_id)
            deleted += 1
        except Exception:
            logger.warning(
                "Failed to delete legacy expedited review cron",
                extra={"cron_id": cron_id},
                exc_info=True,
            )
    return {"deleted": deleted}


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
        return None
    if not await wait_for_slack_file(file_id):
        logger.warning(
            "Slack did not finish processing the expedited review diff image",
            extra={"approval_id": str(approval.id), "slack_file_id": file_id},
        )
        return None
    return file_id


async def post_card(
    approval: ExpeditedApproval, *, title: str, files: list[ChangedFile]
) -> tuple[str | None, str | None]:
    """Post the card into the approval's thread: ``(message_ts, slack_error)``."""
    location = approval.slack_location
    if location is None:
        return None, "no Slack thread"
    text, blocks = card.open_card(
        approval, title=title, files=files, diff_image_id=await _diff_image_id(approval, files)
    )
    return await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=approval.thread_id or None,
        reply_broadcast=True,
    )


async def refresh_card(approval: ExpeditedApproval, *, outcome: str | None = None) -> None:
    """Re-render the posted card from current state; used after votes and outcomes."""
    if not approval.slack_channel_id or not approval.slack_message_ts:
        return
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    files = await _files_for(approval, token) if token else []
    diff_image_id = await _diff_image_id(approval, files)
    if outcome is None:
        text, blocks = card.open_card(
            approval, title=pr.title, files=files, diff_image_id=diff_image_id
        )
    else:
        text, blocks = card.closed_card(
            approval, title=pr.title, files=files, outcome=outcome, diff_image_id=diff_image_id
        )
    ok, error = await update_slack_message(
        approval.slack_channel_id, approval.slack_message_ts, text, blocks=block_payload(blocks)
    )
    if not ok:
        logger.warning(
            "Failed to update expedited review card",
            extra={"approval_id": str(approval.id), "slack_error": error},
        )


async def notify_agent(approval: ExpeditedApproval, prompt: str) -> None:
    """Wake the agent thread that posted the card once with ``prompt``."""
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
    agent_prompt: str | None = None,
) -> ExpeditedApproval | None:
    """Close an open approval and mark its card; ``None`` if it was already closed."""
    updated = await transition(approval.id, expected=("open",), state=state, detail=outcome)
    if updated is None:
        return None
    await refresh_card(updated, outcome=outcome)
    if agent_prompt:
        await notify_agent(updated, agent_prompt)
    return updated


async def mark_merged(approval: ExpeditedApproval) -> None:
    updated = await retire(approval, "merged", "Merged.")
    if updated is None:
        return
    location = updated.slack_location
    if location is not None:
        if not await add_slack_reaction(location[0], location[1], "merged"):
            await add_slack_reaction(location[0], location[1], "white_check_mark")
