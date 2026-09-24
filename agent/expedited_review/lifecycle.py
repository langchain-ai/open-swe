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
from agent.slack.blocks import Block, block_payload
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    add_slack_reaction,
    delete_slack_message,
    post_slack_thread_reply_with_ts,
    update_slack_message,
    upload_slack_thread_file,
    wait_for_slack_file,
)
from agent.slack.code_channels import is_code_channel_session

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
    shown, _ = ChangedFile.split(files)
    if not shown:
        return None
    try:
        png = await asyncio.to_thread(render_diff_png, shown)
    except Exception:
        logger.warning(
            "Failed to render expedited review diff image; posting the text diff",
            extra={"approval_id": str(approval.id)},
            exc_info=True,
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
    """Post the card into the approval's thread: ``(message_ts, slack_error)``.

    Sets ``slack_diff_file_id`` on ``approval``; the caller saves it with the message ts.
    """
    location = approval.slack_location
    if location is None:
        return None, "no Slack thread"
    approval.slack_diff_file_id = await _diff_image_id(approval, files) or ""
    text, blocks = card.open_card(
        approval,
        title=title,
        author=await approval.author_mention(),
        files=files,
        diff_image_id=approval.slack_diff_file_id or None,
        channel=await _broadcast_channel(approval),
    )
    return await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=approval.thread_id or None,
    )


async def _broadcast_channel(approval: ExpeditedApproval) -> str | None:
    """``#name`` of the channel the card could be broadcast to; ``None`` outside a thread."""
    if not approval.slack_thread_ts or is_code_channel_session(approval.slack_thread_ts):
        return None
    channel = await SlackChannel.load(approval.slack_channel_id)
    return f"#{channel.name}" if channel is not None and channel.name else None


async def _render(approval: ExpeditedApproval, outcome: str | None) -> tuple[str, list[Block]]:
    pr = approval.pull_request
    token = await repo_token(pr.owner, pr.repo)
    files = await _files_for(approval, token) if token else []
    diff_image_id = approval.slack_diff_file_id or None
    author = await approval.author_mention()
    if outcome is None:
        return card.open_card(
            approval,
            title=pr.title,
            author=author,
            files=files,
            diff_image_id=diff_image_id,
            channel=await _broadcast_channel(approval),
        )
    return card.closed_card(
        approval,
        title=pr.title,
        author=author,
        files=files,
        outcome=outcome,
        diff_image_id=diff_image_id,
    )


async def refresh_card(approval: ExpeditedApproval, *, outcome: str | None = None) -> None:
    """Re-render the posted card from current state; used after votes and outcomes."""
    if not approval.slack_channel_id or not approval.slack_message_ts:
        return
    text, blocks = await _render(approval, outcome)
    ok, error = await update_slack_message(
        approval.slack_channel_id, approval.slack_message_ts, text, blocks=block_payload(blocks)
    )
    if not ok:
        logger.warning(
            "Failed to update expedited review card",
            extra={"approval_id": str(approval.id), "slack_error": error},
        )


async def _repost(
    approval: ExpeditedApproval, *, broadcast: bool, outcome: str | None = None
) -> bool:
    """Replace the posted card with a fresh thread reply, sent to the channel if ``broadcast``.

    The new card is posted before the old one is deleted, so a failure leaves one card up.
    """
    location = approval.slack_location
    if location is None or not approval.slack_message_ts:
        return False
    old_ts = approval.slack_message_ts
    approval.slack_broadcast = broadcast
    text, blocks = await _render(approval, outcome)
    message_ts, error = await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=approval.thread_id or None,
        reply_broadcast=broadcast,
    )
    if not message_ts:
        logger.warning(
            "Failed to repost expedited review card",
            extra={"approval_id": str(approval.id), "slack_error": error, "broadcast": broadcast},
        )
        return False
    async with ExpeditedApproval.locked(approval.id) as (_, row):
        # An open card that closed meanwhile keeps its closing render; the new copy is the stray.
        kept = (
            row is not None
            and row.slack_message_ts == old_ts
            and (outcome is not None or row.state == "open")
        )
        if kept:
            row.slack_message_ts = message_ts
            row.slack_broadcast = broadcast
    await delete_slack_message(location[0], old_ts if kept else message_ts)
    return kept


async def broadcast_card(approval: ExpeditedApproval) -> bool:
    """Send the open card to the channel as well as its thread."""
    if approval.slack_broadcast or await _broadcast_channel(approval) is None:
        return False
    return await _repost(approval, broadcast=True)


async def notify_agent(approval: ExpeditedApproval, prompt: str) -> bool:
    """Wake the agent thread that posted the card once with ``prompt``; whether it was queued."""
    if not approval.thread_id:
        return False
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
        return False
    return True


async def retire(
    approval: ExpeditedApproval,
    state: ApprovalState,
    outcome: str,
) -> ExpeditedApproval | None:
    """Close an open approval and mark its card; ``None`` if it was already closed."""
    updated = await transition(approval.id, expected=("open",), state=state, detail=outcome)
    if updated is None:
        return None
    # A closed card leaves the channel and stays in the thread only.
    if not updated.slack_broadcast or not await _repost(updated, broadcast=False, outcome=outcome):
        await refresh_card(updated, outcome=outcome)
    return updated


async def remove_superseded_cards(approval: ExpeditedApproval) -> None:
    """Delete older cards for ``approval``'s PR, so its thread only ever shows one."""
    for stale in await ExpeditedApproval.superseded_on_slack(approval.pull_request_id):
        if stale.id == approval.id:
            continue
        if not await delete_slack_message(stale.slack_channel_id, stale.slack_message_ts):
            continue
        async with ExpeditedApproval.locked(stale.id) as (_, row):
            if row is not None:
                row.slack_message_ts = ""


async def mark_merged(approval: ExpeditedApproval) -> None:
    updated = await retire(approval, "merged", "merged")
    if updated is None:
        return
    location = updated.slack_location
    if location is not None:
        if not await add_slack_reaction(location[0], location[1], "merged"):
            await add_slack_reaction(location[0], location[1], "white_check_mark")
