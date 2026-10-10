"""Post a human review card, keep it in step with its request, and close it.

Every Slack write here edits or posts the one card, and only because someone
just acted, a GitHub event arrived, or one of the request's deadlines passed.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
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
from openswe.expedited_review.eligibility import ChangedFile, ExpeditedDiff
from openswe.expedited_review.readiness import (
    PullRequestSnapshot,
    latest_review_states,
    review_authors,
)
from openswe.expedited_review.reviews import dismiss_approval
from openswe.github.http import GitHubAppUnavailable, or_none
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
from openswe.prompts import prompt
from openswe.review.assessment_feedback import AutoApproval
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
from openswe.threads.pr_fixes import dispatch_pull_request_prompt
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


@dataclass
class ReviewCard:
    """A request's card in Slack, its author DM, and the request's open-to-closed lifecycle."""

    request: HumanReviewRequest

    async def _files(self) -> list[ChangedFile] | None:
        """The PR's changed files; ``None`` when the App cannot read them."""
        pr = self.request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                return await ChangedFile.of_pull(pull)
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read the changed files",
                extra={"request_id": str(self.request.id)},
            )
            return None

    async def _diff_image_id(self, files: list[ChangedFile]) -> str | None:
        """A hosted-but-unposted PNG of the diff, which the card renders inline."""
        shown = ExpeditedDiff(files, self.request.excluded_hunks).shown
        if not shown:
            return None
        approval_id = str(self.request.id)
        try:
            png = await asyncio.to_thread(render_diff_png, shown)
        except Exception:
            logger.warning(
                "Failed to render expedited review diff image; posting the text diff",
                extra={"approval_id": approval_id},
                exc_info=True,
            )
            return None
        try:
            file_id = await upload_slack_thread_file(
                None, None, f"diff-{self.request.head_sha[:12]}.png", png, title="Diff"
            )
        except SlackRequestError as exc:
            logger.warning(
                "Failed to upload expedited review diff image",
                extra={"approval_id": approval_id, "slack_error": exc.code},
            )
            return None
        if not await wait_for_slack_file(file_id):
            logger.warning(
                "Slack did not finish processing the expedited review diff image",
                extra={"approval_id": approval_id, "slack_file_id": file_id},
            )
            return None
        return file_id

    async def _warn_target(self, card: tuple[str, list[Block]]) -> tuple[str, list[Block]]:
        pr = self.request.pull_request
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

    async def post_expedited(self, *, title: str, files: list[ChangedFile]) -> str:
        """Post an expedited card and return its timestamp."""
        approval = self.request
        if approval.awaiting_ready:
            raise SlackRequestError("draft card is author-only")
        if not approval.slack_channel_id:
            raise SlackRequestError("no Slack channel")
        approval.slack_diff_file_id = await self._diff_image_id(files) or ""
        if not approval.slack_channel_choices:
            approval.slack_channel_choices = await channel_choices(approval)
        text, blocks = expedited_card.open_card(
            approval,
            title=title,
            author=await approval.author_mention(),
            files=files,
            diff_image_id=approval.slack_diff_file_id or None,
            choices=await self._channel_choices(),
        )
        text, blocks = await self._warn_target((text, blocks))
        if not approval.slack_thread_ts:
            channel = await SlackChannel.load(approval.slack_channel_id)
            if channel is None:
                raise SlackRequestError("channel_not_found")
            pr = approval.pull_request
            approval.slack_thread_ts = await channel.post(
                prompt(
                    "slack/expedited-review-requested",
                    pr_url=pr.url,
                    label=f"{pr.owner}/{pr.repo}#{pr.number}",
                    title=escape(title),
                )
            )
        return await post_slack_thread_reply_with_ts(
            approval.slack_channel_id,
            approval.slack_thread_ts,
            text,
            blocks=block_payload(blocks),
            agent_thread_id=approval.thread_id or None,
            login=self.request.requester_login,
        )

    async def prompt_author_ready(self) -> str | None:
        """Ask only the author to undraft; return a delivery problem, if any."""
        self.request = await HumanReviewRequest.get(self.request.id) or self.request
        approval = self.request
        if approval.state != "open" or not approval.awaiting_ready:
            await self.refresh_author_dm(None)
            return None
        if approval.slack_dm_channel_id and approval.slack_dm_message_ts:
            return None
        pr = approval.pull_request
        author = (
            await User.get(pr.author_user_id)
            if pr.author_user_id
            else await User.for_login("github", pr.author)
        )
        if author is None or not author.slack_user_id:
            return "The author has no linked Slack identity; ask them to mark it ready on GitHub."
        files = await self._files()
        if files is None:
            return "Could not read the diff for the author-only card; try again."
        approval.slack_diff_file_id = await self._diff_image_id(files) or ""
        await approval.save()
        text, blocks = expedited_card.readiness_prompt(
            approval,
            title=pr.title,
            author=await approval.author_mention(),
            files=files,
            diff_image_id=approval.slack_diff_file_id or None,
        )
        text, blocks = await self._warn_target((text, blocks))
        origin = approval.dm_origin
        dm_location = await send_dm_with_location(
            author.slack_user_id,
            text,
            blocks=block_payload(
                [
                    *blocks,
                    *await origin_footer(approval.thread_id, origin.location if origin else None),
                ]
            ),
            origin=origin,
        )
        if dm_location is None:
            return "Slack could not deliver the author-only prompt; ask the author to mark it ready on GitHub."
        approval.slack_dm_channel_id, approval.slack_dm_message_ts = dm_location
        await approval.save()
        return None

    async def post_standard(self) -> str:
        """Post a standard card: a thread reply also sent to the channel, or a top-level post."""
        request = self.request
        text, blocks = await self.render(None)
        location = request.slack_location
        if location is not None:
            return await post_slack_thread_reply_with_ts(
                location[0],
                location[1],
                text,
                blocks=block_payload(blocks),
                agent_thread_id=request.thread_id or None,
                reply_broadcast=True,
                login=self.request.requester_login,
            )
        channel = await SlackChannel.load(request.slack_channel_id)
        if channel is None:
            raise SlackRequestError("channel_not_found")
        return await channel.post(
            text, blocks=block_payload(blocks), login=self.request.requester_login
        )

    async def _channel_choices(self) -> list[ChannelChoice]:
        pr = self.request.pull_request
        if (await RepoSettings.cached(pr.owner, pr.repo)).review_channel.strip():
            return []
        return self.request.slack_channel_choices or await own_choices(self.request)

    async def _review_states(self) -> dict[str, str]:
        pr = self.request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                return await latest_review_states(pull, pr.author) or {}
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read review states",
                extra={"request_id": str(self.request.id)},
            )
            return {}

    async def _auto_approval(self, states: dict[str, str]) -> AutoApproval | None:
        """Open SWE's standing automatic approval; only an App's approval can be one."""
        if not any(
            state == "APPROVED" and login.endswith("[bot]") for login, state in states.items()
        ):
            return None
        pr = self.request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                reviews = await or_none(pull.reviews())
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read automatic approvals",
                extra={"request_id": str(self.request.id)},
            )
            return None
        return await AutoApproval.standing(reviews) if reviews is not None else None

    async def _render_standard(self, outcome: str | None) -> tuple[str, list[Block]]:
        request = self.request
        pr = request.pull_request
        author = await request.author_mention()
        states = await self._review_states() if outcome in (None, "merged") else {}
        auto_approval = await self._auto_approval(states)
        if outcome is not None:
            return standard_card.closed_card(
                request,
                title=pr.title,
                author=author,
                outcome=outcome,
                review_states=states,
                auto_approval=auto_approval,
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
            return standard_card.approved_card(
                request,
                title=pr.title,
                author=author,
                review_states=states,
                auto_approval=auto_approval,
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
        self, outcome: str | None, *, copy: bool = False, dm: bool = False
    ) -> tuple[str, list[Block]]:
        """The card's text and blocks; ``copy`` renders the open copy posted in another channel."""
        text, blocks = await self._warn_target(await self._render_card(outcome, copy=copy))
        if copy or dm or (self.request.kind == "standard" and not self.request.slack_thread_ts):
            blocks.extend(await origin_footer(self.request.thread_id))
        return text, blocks

    async def _render_card(self, outcome: str | None, *, copy: bool) -> tuple[str, list[Block]]:
        request = self.request
        pr = request.pull_request
        if request.kind == "standard":
            return await self._render_standard(outcome)
        files = await self._files() or []
        diff_image_id = request.slack_diff_file_id or None
        author = await request.author_mention()
        if outcome is None:
            return expedited_card.open_card(
                request,
                title=pr.title,
                author=author,
                files=files,
                diff_image_id=diff_image_id,
                choices=[] if copy else await self._channel_choices(),
                thread_url=(
                    await get_slack_permalink(request.slack_channel_id, request.slack_thread_ts)
                    or ""
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

    async def refresh_author_dm(self, outcome: str | None) -> bool:
        request = self.request
        if not request.slack_dm_channel_id or not request.slack_dm_message_ts:
            return False
        async with HumanReviewRequest.locked(request.id) as (_, current):
            if (
                current is None
                or not current.slack_dm_channel_id
                or not current.slack_dm_message_ts
            ):
                return False
            if current.state == "open" and current.awaiting_ready and outcome is None:
                return False
            status = (
                current.detail or current.state
                if current.state != "open"
                else outcome
                or (
                    "Approved. Waiting to merge."
                    if current.approved
                    else "Ready for review. Someone else can approve it now."
                )
            )
            status_key = [
                status,
                current.pull_request.url,
                current.pull_request.title,
                current.slack_channel_id,
                current.slack_thread_ts,
                current.slack_dm_channel_id,
                current.slack_dm_message_ts,
            ]
            if current.run_config.get("author_dm_status") == status_key:
                request.run_config = current.run_config
                return True
            origin_url = (
                await get_slack_permalink(current.slack_channel_id, current.slack_thread_ts)
                if current.slack_channel_id and current.slack_thread_ts
                else None
            )
            text, blocks = expedited_card.author_status(current, status, origin_url=origin_url)
            try:
                await update_slack_message(
                    current.slack_dm_channel_id,
                    current.slack_dm_message_ts,
                    text,
                    blocks=block_payload(blocks),
                    login=current.requester_login,
                )
            except SlackRequestError:
                logger.warning(
                    "Could not update author DM card",
                    extra={"request_id": str(current.id)},
                    exc_info=True,
                )
                return False
            pr = current.pull_request
            author = (
                await User.get(pr.author_user_id)
                if pr.author_user_id
                else await User.for_login("github", pr.author)
            )
            if author is not None and author.slack_user_id:
                await note_for_concierge(author.slack_user_id, current.slack_dm_channel_id, text)
            current.run_config = {**current.run_config, "author_dm_status": status_key}
            request.run_config = current.run_config
            return True

    async def broadcast_configured(self) -> None:
        approval = self.request
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
            if not await self.broadcast():
                logger.warning("Could not broadcast expedited review card to its channel")
        elif error := await self.copy_to(channel):
            logger.warning(
                "Could not broadcast expedited review card", extra={"slack_error": error}
            )

    async def refresh(self, *, outcome: str | None = None) -> None:
        """Re-render the posted card from current state; used after clicks and outcomes."""
        request = self.request
        await self.refresh_author_dm(outcome)
        if (
            request.kind == "expedited"
            and request.state == "open"
            and not request.awaiting_ready
            and not request.slack_message_ts
            and outcome is None
        ):
            files = await self._files()
            if files is None:
                logger.warning("Could not publish ready expedited card without its changed files")
                return
            try:
                message_ts = await self.post_expedited(
                    title=request.pull_request.title, files=files
                )
            except SlackRequestError as exc:
                logger.warning(
                    "Could not publish ready expedited card", extra={"slack_error": exc.code}
                )
            else:
                request.slack_message_ts = message_ts
                await request.save()
                await self.broadcast_configured()
            return
        if not request.has_card or not request.slack_channel_id or not request.slack_message_ts:
            return
        text, blocks = await self.render(outcome)
        try:
            await update_slack_message(
                request.slack_channel_id,
                request.slack_message_ts,
                text,
                blocks=block_payload(blocks),
                login=self.request.requester_login,
            )
        except SlackRequestError as exc:
            logger.warning(
                "Failed to update human review card",
                extra={
                    "request_id": str(request.id),
                    "kind": request.kind,
                    "slack_error": exc.code,
                },
            )
        if outcome is None and (copy := request.slack_copy) is not None:
            text, blocks = await self.render(None, copy=True)
            try:
                await update_slack_message(
                    *copy, text, blocks=block_payload(blocks), login=self.request.requester_login
                )
            except SlackRequestError as exc:
                logger.warning(
                    "Failed to update the copy of a human review card",
                    extra={"request_id": str(request.id), "slack_error": exc.code},
                )

    async def _repost(self, *, broadcast: bool, outcome: str | None = None) -> bool:
        """Replace a thread card with a fresh reply, sent to the channel if ``broadcast``."""
        request = self.request
        location = request.slack_location
        if not request.has_card or location is None or not request.slack_message_ts:
            return False
        old_ts = request.slack_message_ts
        request.slack_broadcast = broadcast
        text, blocks = await self.render(outcome)

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

        await self.refresh_author_dm(outcome)
        return await repost_thread_card(
            location,
            old_ts,
            text,
            blocks,
            broadcast=broadcast,
            agent_thread_id=request.thread_id or None,
            adopt=adopt,
            login=self.request.requester_login,
        )

    async def broadcast(self) -> bool:
        """Send the open card to the channel as well as its thread."""
        if self.request.sent_elsewhere or not await own_choices(self.request):
            return False
        return await self._repost(broadcast=True)

    async def copy_to(self, channel: SlackChannel) -> str | None:
        """Post the open card at the top of another channel; why it was not, or ``None``."""
        approval = self.request
        text, blocks = await self.render(None, copy=True)
        try:
            message_ts = await channel.post(
                text, blocks=block_payload(blocks), login=self.request.requester_login
            )
        except SlackRequestError as exc:
            logger.warning(
                "Could not copy an expedited review card to another channel",
                extra={"approval_id": str(approval.id), "slack_error": exc.code},
            )
            return f"Slack refused the post: {exc.code or 'unknown error'}."
        async with HumanReviewRequest.locked(approval.id) as (_, row):
            kept = (
                row is not None
                and row.state == "open"
                and not row.approved
                and not row.sent_elsewhere
            )
            if kept:
                row.slack_copy_channel_id = channel.id
                row.slack_copy_ts = message_ts
        if not kept:
            await self._delete_copy((channel.id, message_ts))
            return "The card changed while it was being sent. Try again."
        if (current := await HumanReviewRequest.get(approval.id)) is not None:
            await ReviewCard(current).refresh()
        return None

    async def _delete_copy(self, copy: tuple[str, str]) -> bool:
        if await delete_slack_message(*copy):
            return True
        logger.warning(
            "Could not delete the copy of a human review card",
            extra={
                "request_id": str(self.request.id),
                "slack_channel": copy[0],
                "slack_ts": copy[1],
            },
        )
        return False

    async def _withdraw_copy(self) -> None:
        """Delete the card's copy in another channel; the channel id stays for later choices."""
        request = self.request
        copy = request.slack_copy
        if copy is None or not await self._delete_copy(copy):
            return
        async with HumanReviewRequest.locked(request.id) as (_, row):
            if row is not None and row.slack_copy_ts == copy[1]:
                row.slack_copy_ts = ""
        request.slack_copy_ts = ""

    async def notify_agent(self, prompt: str, *, title: str) -> bool:
        """Wake the agent thread that asked for the review, or start one for whoever asked; whether it was queued."""
        request = self.request
        pr = request.pull_request
        if not request.thread_id:
            login = request.requester_login
            if not login:
                logger.info(
                    "Review request has no agent thread or requester to wake",
                    extra={"request_id": str(request.id)},
                )
                return False

            async def record_thread(thread_id: str) -> None:
                async with HumanReviewRequest.locked(request.id) as (_, row):
                    if row is not None:
                        row.thread_id = thread_id
                request.thread_id = thread_id

            await dispatch_pull_request_prompt(
                pr.owner,
                pr.repo,
                pr.number,
                login,
                prompt,
                title=title,
                before_dispatch=record_thread,
            )
            return True
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

    async def retire(self, state: RequestState, outcome: str) -> HumanReviewRequest | None:
        """Close an open request and mark its card; ``None`` if it was already closed."""
        updated = await HumanReviewRequest.transition(
            self.request.id, expected=("open",), state=state, detail=outcome
        )
        if updated is None:
            return None
        self.request = updated
        await self.update_blocked_reactions()
        if state != "merged" and updated.kind == "expedited":
            await self.withdraw_reviews()
        await self.refresh_in_thread(outcome=outcome)
        return updated

    async def reopen(self) -> None:
        """Undo superseding a request whose replacement never made it to Slack."""
        updated = await HumanReviewRequest.transition(
            self.request.id, expected=("superseded",), state="open", detail=""
        )
        if updated is not None:
            await ReviewCard(updated).refresh()

    async def dismiss(self, slack_user_id: str) -> Outcome:
        """Anyone who can see the card may take it down; it needs no GitHub link or access."""
        if not await self.dismiss_by(f"<@{slack_user_id}>", ""):
            return Outcome("This review request is already closed.", dm_card_success=True)
        return Outcome("Dismissed.", dm_card_success=True)

    async def dismiss_by(self, by: str, reason: str) -> bool:
        """Take the card down for ``by``, as a Dismiss click would; ``False`` if already closed."""
        detail = f": {escape(reason.strip())}" if reason.strip() else ""
        return await self.retire("cancelled", f"dismissed by {by}{detail}") is not None

    async def refresh_in_thread(self, *, outcome: str | None = None) -> None:
        """Re-render a card, reposting it into the thread only if it was also in the channel.

        The channel keeps no finished cards: its copy is deleted and the thread keeps the card.
        So does another channel the card was copied to.
        """
        await self._withdraw_copy()
        if not self.request.slack_broadcast or not await self._repost(
            broadcast=False, outcome=outcome
        ):
            await self.refresh(outcome=outcome)

    async def remove_superseded(self) -> None:
        """Delete older cards for this request's PR, so its thread only ever shows one."""
        for stale in await HumanReviewRequest.superseded_on_slack(self.request.pull_request_id):
            if stale.id == self.request.id or not stale.has_card:
                continue
            if not await delete_slack_message(stale.slack_channel_id, stale.slack_message_ts):
                continue
            async with HumanReviewRequest.locked(stale.id) as (_, row):
                if row is not None:
                    row.slack_message_ts = ""

    async def _react(self, emoji: str, fallback: str | None = None) -> None:
        """React to the thread root, or to the message itself; ``fallback`` if the workspace lacks ``emoji``."""
        if skip_on_preview("pr_reaction"):
            return
        request = self.request
        location = request.slack_location or (
            (request.slack_channel_id, request.slack_message_ts)
            if request.slack_channel_id and request.slack_message_ts
            else None
        )
        if location is None or await add_slack_reaction(location[0], location[1], emoji):
            return
        if fallback is not None:
            await add_slack_reaction(location[0], location[1], fallback)

    async def update_blocked_reactions(self, snapshot: PullRequestSnapshot | None = None) -> None:
        """Keep a watched post's failure and conflict reactions in step with GitHub."""
        if skip_on_preview("update_blocked_reactions"):
            return
        request = self.request
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

    async def mark_merged(self) -> None:
        updated = await self.retire("merged", "merged")
        if updated is not None:
            # ✅ means approved, so a workspace without :merged: gets 🔀 instead.
            await self._react("merged", "twisted_rightwards_arrows")
            await ReviewPicks(updated).release("it was merged")

    async def mark_closed(self) -> None:
        updated = await self.retire("cancelled", "the pull request was closed")
        if updated is not None:
            await ReviewPicks(updated).release("it was closed")

    async def mark_approved(self) -> None:
        """React to a posted request's message the first time its pull request is approved."""
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is None or row.state != "open" or row.approved_at is not None:
                return
            row.approved_at = datetime.now(UTC)
        await self._react("white_check_mark")

    async def withdraw_reviews(self) -> None:
        """Dismiss the GitHub reviews every closed, unmerged card of this PR still has standing.

        Covers earlier cards too, so a dismissal GitHub refused is retried here.
        """
        pr = self.request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                for stale in await HumanReviewRequest.with_standing_reviews(
                    self.request.pull_request_id
                ):
                    async with HumanReviewRequest.locked(stale.id) as (_, row):
                        if row is None or row.state in {"open", "merged"}:
                            continue
                        for vote in row.approvals:
                            await dismiss_approval(row, vote, pull, row.detail)
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to dismiss expedited review approvals",
                extra={"approval_id": str(self.request.id)},
            )


def _idle(
    reviewers: list[HumanReviewParticipant], reviewed: set[str]
) -> list[HumanReviewParticipant]:
    """Reviewers Open SWE picked whose lowercased login is not among ``reviewed``."""
    return [
        reviewer
        for reviewer in reviewers
        if reviewer.assigned_by_agent and reviewer.github_login.lower() not in reviewed
    ]


@dataclass
class ReviewPicks:
    """The reviewers Open SWE picked for a request, as GitHub and their pick DMs show them."""

    request: HumanReviewRequest

    async def _unrequest(self, pull: PullRequestClient, login: str) -> None:
        try:
            await pull.remove_requested_reviewers([login])
        except httpx2.HTTPError:
            logger.warning(
                "GitHub review request removal did not complete",
                extra={"request_id": str(self.request.id)},
                exc_info=True,
            )

    async def _close(self, participant: HumanReviewParticipant, text: str) -> None:
        """Say on a pending pick's DM why it ended, which does not notify; otherwise DM ``text``."""
        slack_user_id = participant.user.slack_user_id
        if not slack_user_id:
            return
        # Someone who accepted may be partway through the review, so they get a notification.
        message = participant.pick_message if participant.decision != "review" else None
        if message is not None and await message.show(text):
            await note_for_concierge(
                slack_user_id,
                message.channel_id,
                prompt("slack/concierge-dm-edited", text=message.text, status=text),
            )
            return
        origin = self.request.dm_origin
        await send_dm(
            slack_user_id,
            text,
            blocks=block_payload(
                [
                    section(text),
                    *await origin_footer(
                        self.request.thread_id, origin.location if origin else None
                    ),
                ]
            ),
            origin=origin,
        )

    async def release(self, reason: str) -> HumanReviewRequest:
        """Take reviewers Open SWE picked who have not reviewed off the pull request, and tell them."""
        request = self.request
        if not any(reviewer.assigned_by_agent for reviewer in request.reviewers + request.picks):
            return request
        pr = request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                return await self._release(pull, reason)
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to release Open SWE's reviewer picks",
                extra={"request_id": str(request.id)},
            )
            return request

    async def _release(self, pull: PullRequestClient, reason: str) -> HumanReviewRequest:
        request = self.request
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
            released = _idle(row.reviewers + row.picks, reviewed)
            for reviewer in released:
                row.participants.remove(reviewer)
        if not released:
            return request
        current = await HumanReviewRequest.get(request.id) or request
        if current.state == "open":
            await ReviewCard(current).refresh()
        label = f"<{pr.url}|{pr.owner}/{pr.repo}#{pr.number}>"
        for reviewer in released:
            logger.info(
                "Released a reviewer Open SWE picked",
                extra={"request_id": str(request.id), "github_login": reviewer.github_login},
            )
            await self._unrequest(pull, reviewer.github_login)
            await self._close(
                reviewer,
                f"You no longer need to review {label} *{escape(pr.title)}*: {reason}. "
                "Open SWE removed you as a reviewer.",
            )
        return await HumanReviewRequest.get(request.id) or current

    async def drop(
        self, user_ids: set[UUID], message: str | None, *, expired: bool = False
    ) -> list[HumanReviewParticipant]:
        """Withdraw pending picks of ``user_ids`` from the card and GitHub, and tell each ``message``.

        An ``expired`` pick stays on the request so it is never picked for it again. Without a
        ``message`` nobody is told, for a pick the person ended themselves.
        """
        request = self.request
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
                    await self._unrequest(pull, pick.github_login)
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
            if message is not None:
                await self._close(pick, message)
        current = await HumanReviewRequest.get(request.id)
        if current is not None and current.state == "open":
            await ReviewCard(current).refresh()
        return dropped


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
            payload = await or_none(pull.pull())
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
        await ReviewCard(request).mark_merged()
    elif current.state == "closed":
        await ReviewCard(request).mark_closed()
