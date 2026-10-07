"""Post a human review card, keep it in step with its request, and close it.

Every Slack write here edits or posts the one card, and only because someone
just acted, a GitHub event arrived, or one of the request's deadlines passed.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx2
from langgraph_sdk import get_client

from openswe.dispatch import dispatch_agent_run
from openswe.expedited_review import card as expedited_card
from openswe.expedited_review.channels import (
    channel_choices,
    own_choices,
    sendable_channel,
    still_internal,
)
from openswe.expedited_review.diff_image import render_diff_png
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import (
    PullRequestSnapshot,
    latest_review_states,
    review_authors,
)
from openswe.expedited_review.reviews import dismiss_approval
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.github.repo_files import RepoSettings
from openswe.github.repositories import Repository
from openswe.human_review import card as standard_card
from openswe.human_review.people import Outcome
from openswe.human_review.requests import (
    ChannelChoice,
    HumanReviewParticipant,
    HumanReviewRequest,
    RequestState,
)
from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, block_payload, context, escape, section
from openswe.slack.cards import origin_footer, repost_thread_card
from openswe.slack.channels import SlackChannel
from openswe.slack.client import (
    add_slack_reaction,
    delete_slack_message,
    get_slack_permalink,
    post_slack_thread_reply_with_ts,
    remove_slack_reaction,
    update_slack_message,
    upload_slack_thread_file,
    wait_for_slack_file,
)
from openswe.slack.dm import note_for_concierge, send_dm, send_dm_with_location
from openswe.slack.http import SlackRequestError
from openswe.users import User
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)

LEGACY_CRON_TASK = "expedited_review"
_LEGACY_CRON_KIND = "expedited_review_watch"


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
    request_id: UUID, *, expected: tuple[RequestState, ...], **changes: Any
) -> HumanReviewRequest | None:
    """Apply ``changes`` if the row is still in one of ``expected``; else ``None``."""
    async with HumanReviewRequest.locked(request_id) as (_, row):
        if row is None or row.state not in expected:
            return None
        for name, value in changes.items():
            setattr(row, name, value)
        return row


async def _files_for(request: HumanReviewRequest) -> list[ChangedFile] | None:
    """The PR's changed files; ``None`` when the App cannot read them."""
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            return await ChangedFile.of_pull(pull)
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to read the changed files", extra={"request_id": str(request.id)}
        )
        return None


async def _diff_image_id(approval: HumanReviewRequest, files: list[ChangedFile]) -> str | None:
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
    try:
        file_id = await upload_slack_thread_file(
            None, None, f"diff-{approval.head_sha[:12]}.png", png, title="Diff"
        )
    except SlackRequestError as exc:
        logger.warning(
            "Failed to upload expedited review diff image",
            extra={"approval_id": str(approval.id), "slack_error": exc.code},
        )
        return None
    if not await wait_for_slack_file(file_id):
        logger.warning(
            "Slack did not finish processing the expedited review diff image",
            extra={"approval_id": str(approval.id), "slack_file_id": file_id},
        )
        return None
    return file_id


async def _warn_target(
    request: HumanReviewRequest, card: tuple[str, list[Block]]
) -> tuple[str, list[Block]]:
    pr = request.pull_request
    if not pr.base_ref:
        return card
    default_branch = await Repository.resolve_default_branch(pr.owner, pr.repo)
    if not default_branch or pr.base_ref == default_branch:
        return card
    warning = (
        f":warning: Targets non-default branch `{escape(pr.base_ref)}` "
        f"(default: `{escape(default_branch)}`)."
    )
    text, blocks = card
    return f"{text}\n{warning}", [context(warning), *blocks]


def _requester_login(request: HumanReviewRequest) -> str:
    if request.requested_by is not None:
        return request.requested_by.github_login
    return RunConfig.parse(request.run_config).github_login or ""


async def post_card(approval: HumanReviewRequest, *, title: str, files: list[ChangedFile]) -> str:
    """Post an expedited card and return its timestamp."""
    if approval.awaiting_ready:
        raise SlackRequestError("draft card is author-only")
    location = approval.slack_location
    if location is None:
        raise SlackRequestError("no Slack thread")
    approval.slack_diff_file_id = await _diff_image_id(approval, files) or ""
    if not approval.slack_channel_choices:
        approval.slack_channel_choices = await channel_choices(approval)
    text, blocks = expedited_card.open_card(
        approval,
        title=title,
        author=await approval.author_mention(),
        files=files,
        diff_image_id=approval.slack_diff_file_id or None,
        choices=await _channel_choices(approval),
    )
    text, blocks = await _warn_target(approval, (text, blocks))
    return await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=approval.thread_id or None,
        login=_requester_login(approval),
    )


async def prompt_author_ready(approval: HumanReviewRequest) -> str | None:
    """Ask only the author to undraft; return a delivery problem, if any."""
    if not approval.awaiting_ready:
        return None
    pr = approval.pull_request
    author = (
        await User.get(pr.author_user_id)
        if pr.author_user_id
        else await User.for_login("github", pr.author)
    )
    if author is None or not author.slack_user_id:
        return "The author has no linked Slack identity; ask them to mark it ready on GitHub."
    files = await _files_for(approval)
    if files is None:
        return "Could not read the diff for the author-only card; try again."
    approval.slack_diff_file_id = await _diff_image_id(approval, files) or ""
    await approval.save()
    text, blocks = expedited_card.readiness_prompt(
        approval,
        title=pr.title,
        author=await approval.author_mention(),
        files=files,
        diff_image_id=approval.slack_diff_file_id or None,
    )
    text, blocks = await _warn_target(approval, (text, blocks))
    dm_location = await send_dm_with_location(
        author.slack_user_id,
        text,
        blocks=block_payload([*blocks, *await origin_footer(approval.thread_id)]),
    )
    if dm_location is None:
        return "Slack could not deliver the author-only prompt; ask the author to mark it ready on GitHub."
    approval.slack_dm_channel_id, approval.slack_dm_message_ts = dm_location
    await approval.save()
    return None


async def post_standard_card(request: HumanReviewRequest) -> str:
    """Post a standard card: a thread reply also sent to the channel, or a top-level post."""
    text, blocks = await render(request, None)
    location = request.slack_location
    if location is not None:
        return await post_slack_thread_reply_with_ts(
            location[0],
            location[1],
            text,
            blocks=block_payload(blocks),
            agent_thread_id=request.thread_id or None,
            reply_broadcast=True,
            login=_requester_login(request),
        )
    channel = await SlackChannel.load(request.slack_channel_id)
    if channel is None:
        raise SlackRequestError("channel_not_found")
    return await channel.post(text, blocks=block_payload(blocks), login=_requester_login(request))


async def _channel_choices(approval: HumanReviewRequest) -> list[ChannelChoice]:
    pr = approval.pull_request
    if (await RepoSettings.cached(pr.owner, pr.repo)).review_channel.strip():
        return []
    return approval.slack_channel_choices or await own_choices(approval)


async def _review_states(request: HumanReviewRequest) -> dict[str, str]:
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            return await latest_review_states(pull, pr.author) or {}
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to read review states", extra={"request_id": str(request.id)}
        )
        return {}


async def _render_standard(
    request: HumanReviewRequest, outcome: str | None
) -> tuple[str, list[Block]]:
    pr = request.pull_request
    author = await request.author_mention()
    states = await _review_states(request) if outcome in (None, "merged") else {}
    if outcome is not None:
        return standard_card.closed_card(
            request, title=pr.title, author=author, outcome=outcome, review_states=states
        )
    from openswe.human_review.standard import merge_wait

    if (
        "CHANGES_REQUESTED" not in states.values()
        and merge_wait(
            [reviewer.github_login for reviewer in request.reviewers],
            request.created_at,
            states,
            datetime.now(UTC),
        )
        is None
    ):
        return standard_card.closed_card(
            request, title=pr.title, author=author, outcome="approved", review_states=states
        )
    requester = request.requested_by
    return standard_card.open_card(
        request,
        title=pr.title,
        author=author,
        requester=standard_card.mention(requester) if requester is not None else None,
        review_states=states,
    )


async def render(
    request: HumanReviewRequest, outcome: str | None, *, copy: bool = False, dm: bool = False
) -> tuple[str, list[Block]]:
    text, blocks = await _render(request, outcome, copy=copy)
    if copy or dm or (request.kind == "standard" and not request.slack_thread_ts):
        blocks.extend(await origin_footer(request.thread_id))
    return text, blocks


async def _render(
    request: HumanReviewRequest, outcome: str | None, *, copy: bool = False
) -> tuple[str, list[Block]]:
    """The card's text and blocks; ``copy`` renders the open copy posted in another channel."""
    return await _warn_target(request, await _render_card(request, outcome, copy=copy))


async def _render_card(
    request: HumanReviewRequest, outcome: str | None, *, copy: bool
) -> tuple[str, list[Block]]:
    pr = request.pull_request
    if request.kind == "standard":
        return await _render_standard(request, outcome)
    files = await _files_for(request) or []
    diff_image_id = request.slack_diff_file_id or None
    author = await request.author_mention()
    if outcome is None:
        return expedited_card.open_card(
            request,
            title=pr.title,
            author=author,
            files=files,
            diff_image_id=diff_image_id,
            choices=[] if copy else await _channel_choices(request),
            thread_url=(
                await get_slack_permalink(request.slack_channel_id, request.slack_thread_ts) or ""
            )
            if copy
            else None,
        )
    return expedited_card.closed_card(
        request,
        title=pr.title,
        author=author,
        files=files,
        outcome=outcome,
        diff_image_id=diff_image_id,
    )


async def _refresh_dm_card(request: HumanReviewRequest, outcome: str | None) -> None:
    if not request.slack_dm_channel_id or not request.slack_dm_message_ts:
        return
    if request.awaiting_ready and outcome is None:
        return
    channel_id = request.slack_dm_channel_id
    if not await delete_slack_message(channel_id, request.slack_dm_message_ts):
        logger.warning("Could not delete author DM card", extra={"request_id": str(request.id)})
        return
    request.slack_dm_channel_id = ""
    request.slack_dm_message_ts = ""
    await request.save()
    pr = request.pull_request
    author = await User.get(pr.author_user_id) if pr.author_user_id else None
    if author is not None and author.slack_user_id:
        await note_for_concierge(
            author.slack_user_id, channel_id, f"Removed the author-only draft card for {pr.url}."
        )


async def broadcast_configured(approval: HumanReviewRequest) -> None:
    if approval.sent_elsewhere or approval.approved or approval.awaiting_ready:
        return
    pr = approval.pull_request
    configured = (await RepoSettings.cached(pr.owner, pr.repo)).review_channel.strip()
    if not configured:
        return
    channel = await SlackChannel.resolve(configured)
    if channel is None:
        logger.warning(
            "Configured expedited review channel is unavailable", extra={"channel": configured}
        )
        return
    if not await still_internal(approval.slack_channel_id):
        logger.warning("Cannot broadcast expedited review from an externally shared channel")
        return
    if (channel := await sendable_channel(channel.id)) is None:
        logger.warning("Configured expedited review channel cannot receive cards")
        return
    if channel.id == approval.slack_channel_id:
        if not await broadcast_card(approval):
            logger.warning("Could not broadcast expedited review card to its channel")
    elif error := await copy_card(approval, channel):
        logger.warning("Could not broadcast expedited review card", extra={"slack_error": error})


async def refresh_card(request: HumanReviewRequest, *, outcome: str | None = None) -> None:
    """Re-render the posted card from current state; used after clicks and outcomes."""
    await _refresh_dm_card(request, outcome)
    if (
        request.kind == "expedited"
        and request.state == "open"
        and not request.awaiting_ready
        and not request.slack_message_ts
        and outcome is None
    ):
        files = await _files_for(request)
        if files is None:
            logger.warning("Could not publish ready expedited card without its changed files")
            return
        try:
            message_ts = await post_card(request, title=request.pull_request.title, files=files)
        except SlackRequestError as exc:
            logger.warning(
                "Could not publish ready expedited card", extra={"slack_error": exc.code}
            )
        else:
            request.slack_message_ts = message_ts
            await request.save()
            await broadcast_configured(request)
        return
    if not request.has_card or not request.slack_channel_id or not request.slack_message_ts:
        return
    text, blocks = await render(request, outcome)
    try:
        await update_slack_message(
            request.slack_channel_id,
            request.slack_message_ts,
            text,
            blocks=block_payload(blocks),
            login=_requester_login(request),
        )
    except SlackRequestError as exc:
        logger.warning(
            "Failed to update human review card",
            extra={"request_id": str(request.id), "kind": request.kind, "slack_error": exc.code},
        )
    if outcome is None and (copy := request.slack_copy) is not None:
        text, blocks = await render(request, None, copy=True)
        try:
            await update_slack_message(
                *copy, text, blocks=block_payload(blocks), login=_requester_login(request)
            )
        except SlackRequestError as exc:
            logger.warning(
                "Failed to update the copy of a human review card",
                extra={"request_id": str(request.id), "slack_error": exc.code},
            )


async def _repost(
    request: HumanReviewRequest, *, broadcast: bool, outcome: str | None = None
) -> bool:
    """Replace a thread card with a fresh reply, sent to the channel if ``broadcast``."""
    location = request.slack_location
    if not request.has_card or location is None or not request.slack_message_ts:
        return False
    old_ts = request.slack_message_ts
    request.slack_broadcast = broadcast
    text, blocks = await render(request, outcome)

    async def adopt(message_ts: str) -> bool:
        async with HumanReviewRequest.locked(request.id) as (_, row):
            # An open card that closed meanwhile keeps its closing render; the new copy is the stray.
            kept = (
                row is not None
                and row.slack_message_ts == old_ts
                and (outcome is not None or row.state == "open")
                and not (broadcast and row.slack_copy is not None)
            )
            if kept:
                row.slack_message_ts = message_ts
                row.slack_broadcast = broadcast
            return kept

    await _refresh_dm_card(request, outcome)
    return await repost_thread_card(
        location,
        old_ts,
        text,
        blocks,
        broadcast=broadcast,
        agent_thread_id=request.thread_id or None,
        adopt=adopt,
        login=_requester_login(request),
    )


async def broadcast_card(approval: HumanReviewRequest) -> bool:
    """Send the open card to the channel as well as its thread."""
    if approval.sent_elsewhere or not await own_choices(approval):
        return False
    return await _repost(approval, broadcast=True)


async def copy_card(approval: HumanReviewRequest, channel: SlackChannel) -> str | None:
    """Post the open card at the top of another channel; why it was not, or ``None``."""
    text, blocks = await render(approval, None, copy=True)
    try:
        message_ts = await channel.post(
            text, blocks=block_payload(blocks), login=_requester_login(approval)
        )
    except SlackRequestError as exc:
        logger.warning(
            "Could not copy an expedited review card to another channel",
            extra={"approval_id": str(approval.id), "slack_error": exc.code},
        )
        return f"Slack refused the post: {exc.code or 'unknown error'}."
    async with HumanReviewRequest.locked(approval.id) as (_, row):
        kept = (
            row is not None and row.state == "open" and not row.approved and not row.sent_elsewhere
        )
        if kept:
            row.slack_copy_channel_id = channel.id
            row.slack_copy_ts = message_ts
    if not kept:
        await _delete_copy(approval.id, (channel.id, message_ts))
        return "The card changed while it was being sent. Try again."
    if (current := await HumanReviewRequest.get(approval.id)) is not None:
        await refresh_card(current)
    return None


async def _delete_copy(request_id: UUID, copy: tuple[str, str]) -> bool:
    if await delete_slack_message(*copy):
        return True
    logger.warning(
        "Could not delete the copy of a human review card",
        extra={"request_id": str(request_id), "slack_channel": copy[0], "slack_ts": copy[1]},
    )
    return False


async def _withdraw_copy(request: HumanReviewRequest) -> None:
    """Delete the card's copy in another channel; the channel id stays for later choices."""
    copy = request.slack_copy
    if copy is None or not await _delete_copy(request.id, copy):
        return
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is not None and row.slack_copy_ts == copy[1]:
            row.slack_copy_ts = ""
    request.slack_copy_ts = ""


async def notify_agent(request: HumanReviewRequest, prompt: str) -> bool:
    """Wake the agent thread that asked for the review once with ``prompt``; whether it was queued."""
    if not request.thread_id:
        return False
    pr = request.pull_request
    configurable = dict(request.run_config)
    configurable.update(
        {
            "source": configurable.get("source") or "slack",
            "repo": {"owner": pr.owner, "name": pr.repo},
            "pr_number": pr.number,
        }
    )
    try:
        await dispatch_agent_run(
            request.thread_id,
            prompt,
            configurable,
            source=str(configurable["source"]),
            thread_title=None,
            metadata={},
            multitask_strategy="enqueue",
        )
    except Exception:
        logger.warning(
            "Failed to notify agent about a human review request",
            extra={"request_id": str(request.id), "kind": request.kind},
            exc_info=True,
        )
        return False
    return True


async def retire(
    request: HumanReviewRequest,
    state: RequestState,
    outcome: str,
) -> HumanReviewRequest | None:
    """Close an open request and mark its card; ``None`` if it was already closed."""
    updated = await transition(request.id, expected=("open",), state=state, detail=outcome)
    if updated is None:
        return None
    await update_blocked_reactions(updated)
    if state != "merged" and updated.kind == "expedited":
        await withdraw_reviews(updated)
    await refresh_card_in_thread(updated, outcome=outcome)
    return updated


async def reopen(request: HumanReviewRequest) -> None:
    """Undo superseding a request whose replacement never made it to Slack."""
    updated = await transition(request.id, expected=("superseded",), state="open", detail="")
    if updated is not None:
        await refresh_card(updated)


async def dismiss_request(request: HumanReviewRequest, slack_user_id: str) -> Outcome:
    """Anyone who can see the card may take it down; it needs no GitHub link or access."""
    if not await dismiss_by(request, f"<@{slack_user_id}>", ""):
        return Outcome("This review request is already closed.")
    return Outcome("Dismissed.")


async def dismiss_by(request: HumanReviewRequest, by: str, reason: str) -> bool:
    """Take the card down for ``by``, as a Dismiss click would; ``False`` if already closed."""
    detail = f": {escape(reason.strip())}" if reason.strip() else ""
    return await retire(request, "cancelled", f"dismissed by {by}{detail}") is not None


async def refresh_card_in_thread(
    request: HumanReviewRequest, *, outcome: str | None = None
) -> None:
    """Re-render a card, reposting it into the thread only if it was also in the channel.

    The channel keeps no finished cards: its copy is deleted and the thread keeps the card.
    So does another channel the card was copied to.
    """
    await _withdraw_copy(request)
    if not request.slack_broadcast or not await _repost(request, broadcast=False, outcome=outcome):
        await refresh_card(request, outcome=outcome)


async def remove_superseded_cards(approval: HumanReviewRequest) -> None:
    """Delete older cards for ``approval``'s PR, so its thread only ever shows one."""
    for stale in await HumanReviewRequest.superseded_on_slack(approval.pull_request_id):
        if stale.id == approval.id or not stale.has_card:
            continue
        if not await delete_slack_message(stale.slack_channel_id, stale.slack_message_ts):
            continue
        async with HumanReviewRequest.locked(stale.id) as (_, row):
            if row is not None:
                row.slack_message_ts = ""


async def _react(request: HumanReviewRequest, emoji: str, fallback: str | None = None) -> None:
    """React to the thread root, or to the message itself; ``fallback`` if the workspace lacks ``emoji``."""
    if skip_on_preview("pr_reaction"):
        return
    location = request.slack_location or (
        (request.slack_channel_id, request.slack_message_ts)
        if request.slack_channel_id and request.slack_message_ts
        else None
    )
    if location is None or await add_slack_reaction(location[0], location[1], emoji):
        return
    if fallback is not None:
        await add_slack_reaction(location[0], location[1], fallback)


async def update_blocked_reactions(
    request: HumanReviewRequest, snapshot: PullRequestSnapshot | None = None
) -> None:
    """Keep a watched post's failure and conflict reactions in step with GitHub."""
    if skip_on_preview("update_blocked_reactions"):
        return
    if request.kind != "posted" or not request.slack_channel_id or not request.slack_message_ts:
        return
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None:
            return
        failing = conflicted = False
        if (
            row.state == "open"
            and snapshot is not None
            and snapshot.state == "open"
            and not snapshot.merged
        ):
            author = snapshot.author or row.pull_request.author
            preferences = await User.preferences_for_login(author)
            failing = preferences.pr_failure_reactions and snapshot.check_state in {
                "failure",
                "blocked",
            }
            conflicted = snapshot.mergeable is False or snapshot.mergeable_state == "dirty"
        for emoji, blocked in (("x", failing), ("construction", conflicted)):
            react = add_slack_reaction if blocked else remove_slack_reaction
            await react(row.slack_channel_id, row.slack_message_ts, emoji)


async def mark_merged(request: HumanReviewRequest) -> None:
    updated = await retire(request, "merged", "merged")
    if updated is not None:
        # ✅ means approved, so a workspace without :merged: gets 🔀 instead.
        await _react(updated, "merged", "twisted_rightwards_arrows")
        await release_picks(updated, "it was merged")


async def mark_closed(request: HumanReviewRequest) -> None:
    updated = await retire(request, "cancelled", "the pull request was closed")
    if updated is not None:
        await release_picks(updated, "it was closed")


async def mark_approved(request: HumanReviewRequest) -> None:
    """React to a posted request's message the first time its pull request is approved."""
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None or row.state != "open" or row.approved_at is not None:
            return
        row.approved_at = datetime.now(UTC)
    await _react(request, "white_check_mark")


def idle_picks(
    reviewers: list[HumanReviewParticipant], reviewed: set[str]
) -> list[HumanReviewParticipant]:
    """Reviewers Open SWE picked whose lowercased login is not among ``reviewed``."""
    return [
        reviewer
        for reviewer in reviewers
        if reviewer.assigned_by_agent and reviewer.github_login.lower() not in reviewed
    ]


async def _unrequest_github_review(
    request: HumanReviewRequest, pull: PullRequestClient, login: str
) -> None:
    try:
        await pull.repo.delete(f"pulls/{pull.number}/requested_reviewers", {"reviewers": [login]})
    except httpx2.HTTPError:
        logger.warning(
            "GitHub review request removal did not complete",
            extra={"request_id": str(request.id)},
            exc_info=True,
        )


async def release_picks(request: HumanReviewRequest, reason: str) -> HumanReviewRequest:
    """Take reviewers Open SWE picked who have not reviewed off the pull request, and tell them."""
    if not any(reviewer.assigned_by_agent for reviewer in request.reviewers + request.picks):
        return request
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            return await _release_picks(request, pull, reason)
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to release Open SWE's reviewer picks",
            extra={"request_id": str(request.id)},
        )
        return request


async def _release_picks(
    request: HumanReviewRequest, pull: PullRequestClient, reason: str
) -> HumanReviewRequest:
    pr = request.pull_request
    reviewed = await review_authors(pull)
    if reviewed is None:
        logger.warning(
            "Could not read reviews to release Open SWE's reviewer picks",
            extra={"request_id": str(request.id)},
        )
        return request
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None:
            return request
        released = idle_picks(row.reviewers + row.picks, reviewed)
        for reviewer in released:
            row.participants.remove(reviewer)
    if not released:
        return request
    current = await HumanReviewRequest.get(request.id) or request
    if current.state == "open":
        await refresh_card(current)
    label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
    for reviewer in released:
        logger.info(
            "Released a reviewer Open SWE picked",
            extra={"request_id": str(request.id), "github_login": reviewer.github_login},
        )
        await _unrequest_github_review(request, pull, reviewer.github_login)
        if reviewer.user.slack_user_id:
            text = (
                f"You no longer need to review {label} *{escape(pr.title)}*: {reason}. "
                "Open SWE removed you as a reviewer."
            )
            await send_dm(
                reviewer.user.slack_user_id,
                text,
                blocks=block_payload([section(text), *await origin_footer(request.thread_id)]),
            )
    return await HumanReviewRequest.get(request.id) or current


async def drop_picks(
    request: HumanReviewRequest, user_ids: set[UUID], message: str, *, expired: bool = False
) -> list[HumanReviewParticipant]:
    """Withdraw pending picks of ``user_ids`` from the card and GitHub, and DM each ``message``.

    An ``expired`` pick stays on the request so it is never picked for it again.
    """
    async with HumanReviewRequest.locked(request.id) as (_, row):
        if row is None:
            return []
        dropped = [pick for pick in row.picks if pick.user_id in user_ids]
        for pick in dropped:
            if expired:
                pick.decision = "expired"
            else:
                row.participants.remove(pick)
    if not dropped:
        return []
    pr = request.pull_request
    try:
        async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
            for pick in dropped:
                await _unrequest_github_review(request, pull, pick.github_login)
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to withdraw review requests for dropped picks",
            extra={"request_id": str(request.id)},
        )
    for pick in dropped:
        logger.info(
            "Withdrew a pending reviewer pick",
            extra={
                "request_id": str(request.id),
                "github_login": pick.github_login,
                "expired": expired,
            },
        )
        if pick.user.slack_user_id:
            await send_dm(
                pick.user.slack_user_id,
                message,
                blocks=block_payload([section(message), *await origin_footer(request.thread_id)]),
            )
    current = await HumanReviewRequest.get(request.id)
    if current is not None and current.state == "open":
        await refresh_card(current)
    return dropped


async def withdraw_reviews(approval: HumanReviewRequest) -> None:
    """Dismiss the GitHub reviews every closed, unmerged card of this PR still has standing.

    Covers earlier cards too, so a dismissal GitHub refused is retried here.
    """
    pr = approval.pull_request
    try:
        async with GitHubClient.as_app(pr.owner, pr.repo) as github:
            repo = github.repo(pr.owner, pr.repo)
            for stale in await HumanReviewRequest.with_standing_reviews(approval.pull_request_id):
                async with HumanReviewRequest.locked(stale.id) as (_, row):
                    if row is None or row.state in {"open", "merged"}:
                        continue
                    for vote in row.approvals:
                        await dismiss_approval(row, vote, repo, row.detail)
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to dismiss expedited review approvals",
            extra={"approval_id": str(approval.id)},
        )


async def close_for_pull_request(owner: str, repo: str, number: int) -> None:
    """Settle a PR's open card once the PR is closed on GitHub, whoever closed it.

    Reads the PR's current state, so a late webhook cannot close a card for a reopened
    PR. Only the card and the merged reaction change: nothing new is posted and the
    agent is not woken.
    """
    request = await HumanReviewRequest.active_for(owner, repo, number)
    if request is None:
        return
    try:
        async with PullRequestClient.as_app(owner, repo, number) as pull:
            payload = await pull.pull()
    except GitHubAppUnavailable:
        payload = None
    if payload is None:
        logger.warning(
            "Could not read a closed pull request to settle its review card",
            extra={"request_id": str(request.id), "kind": request.kind},
        )
        return
    current = PullRequestPayload.model_validate(payload)
    if current.merged:
        await mark_merged(request)
    elif current.state == "closed":
        await mark_closed(request)
