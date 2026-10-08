"""Tool that posts a Slack approval card for a tiny pull request."""

from collections.abc import Mapping
from typing import Any, Literal

from langgraph.config import get_config

from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.expedited_review.eligibility import (
    MAX_CHANGED_LINES,
    ChangedFile,
    Ineligible,
    assess_eligibility,
    fingerprint_matches,
)
from openswe.github.comments import derive_pr_state
from openswe.github.http import GitHubClient, or_none
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repo_files import RepoSettings
from openswe.github.token import resolve_github_token
from openswe.human_review.lifecycle import (
    broadcast_configured,
    post_card,
    prompt_author_ready,
    refresh_card,
    remove_superseded_cards,
    reopen,
    retire,
    transition,
)
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import summary_line
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import escape
from openswe.slack.cards import run_slack_location
from openswe.slack.channels import SlackChannel
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.slack.http import SlackRequestError
from openswe.tools.manage_baby_sit import dispatch_run_config
from openswe.users import User


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


def _next_step(*, reused: bool, elsewhere: bool, in_thread: bool) -> str:
    """What the agent should do once the card is up."""
    if elsewhere:
        return (
            "This diff already has a card, so it stays in the Slack thread named here, not "
            'the channel you asked for. Cancel with action="cancel" and ask again to move it.'
        )
    posted = (
        "This diff already has an open card; no new one was posted."
        if reused
        else "The approval card is posted in the Slack thread."
    )
    posted += (
        " An approval goes to GitHub as the voter's review when they click. Call "
        "`merge_expedited_pr` once checks and reviews are "
        "clean; keep a `/baby-sit` watch on the PR so you are woken when they are. You are "
        "also woken when someone approves the card. Do not poll."
    )
    if reused or not in_thread:
        return posted
    return (
        f"{posted} The card is this turn's reply to the person who asked: finish with "
        "`slack_no_reply_needed` rather than a message announcing the pull request or "
        "this request."
    )


async def _discard(approval: HumanReviewRequest) -> None:
    async with HumanReviewRequest.locked(approval.id) as (session, row):
        if row is not None:
            await session.delete(row)


async def _post_root_message(channel: SlackChannel, pr_ref: GitHubPrRef, title: str) -> str:
    """Open a thread in ``channel`` for the card."""
    return await channel.post(
        prompt(
            "slack/expedited-review-requested",
            pr_url=pr_ref.url,
            label=f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}",
            title=escape(title),
        )
    )


async def expedite_pr_approval(
    pr_url: str,
    action: Literal["start", "cancel"] = "start",
    channel: str = "",
    inline_summary: str = "",
) -> dict[str, Any]:
    """Implement the `expedite_pr_approval` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    config = get_config()
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if not (await get_workspace_settings()).expedited_review_enabled:
        return _failure(
            "Expedited review is disabled for this Open SWE instance. "
            "An admin can enable it under Settings; ask for a normal review instead."
        )

    if action == "cancel":
        approval = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        if approval is None or approval.kind != "expedited":
            return {"success": True, "cancelled": False}
        if approval.thread_id and approval.thread_id != thread_id:
            return _failure("This expedited review belongs to another agent thread")
        await retire(approval, "cancelled", "cancelled by the agent")
        return {"success": True, "cancelled": True}

    own_channel, own_thread = await run_slack_location(cfg, thread_id)
    channel_id, thread_ts = own_channel, own_thread
    target: SlackChannel | None = None
    if channel.strip():
        target = await SlackChannel.resolve(channel)
        if target is None:
            return _failure(
                f"Slack channel {channel.strip()!r} was not found. Pass a channel name the "
                "bot can see or a channel id."
            )
        # Naming the channel this run already talks in keeps the card in the live
        # thread; a second root there would strand it from the conversation.
        if target.id != own_channel or not own_thread:
            channel_id, thread_ts = target.id, ""
    if not channel_id:
        return _failure(
            "Expedited review posts its approval card in Slack. This thread has no Slack "
            "location, so pass `channel` with the Slack channel name or id to post in."
        )

    try:
        token, _ = await resolve_github_token(
            config if isinstance(config, Mapping) else {}, thread_id
        )
    except Exception as exc:
        return _failure(f"GitHub authentication failed: {exc}")
    async with GitHubClient.connect(token=token) as github:
        pull = github.repo(pr_ref.owner, pr_ref.repo).pull_request(pr_ref.number)
        pr = await or_none(pull.pull())
        files = await ChangedFile.of_pull(pull) if pr else None
    if not pr:
        return _failure("Pull request is unavailable")
    if pr.get("state") != "open":
        return _failure("Pull request is not open")
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not head_sha:
        return _failure("Pull request head SHA is unavailable")

    if files is None:
        return _failure("Could not read the pull request's changed files")
    verdict = assess_eligibility(files)
    if isinstance(verdict, Ineligible):
        return _failure(
            f"Not eligible for expedited review: {verdict.reason}. "
            f"Eligible changes touch at most {MAX_CHANGED_LINES} lines outside tests, and "
            "every one of those files has to have a readable text diff. Test files are "
            "not counted. Ask for a normal review."
        )

    payload = PullRequestPayload.model_validate(pr)
    if await User.for_login("github", payload.author) is None:
        return _failure("Expedited review is only available for PRs authored by Open SWE users.")
    settings = await RepoSettings.cached(pr_ref.owner, pr_ref.repo)
    review_channel = settings.channel_for_files([file.filename for file in files])
    broadcast_target = await SlackChannel.resolve(review_channel) if review_channel else None
    if review_channel and broadcast_target is None:
        return _failure(
            f"The configured review channel {review_channel!r} is unavailable to the bot. "
            "Invite the bot to that channel or update `reviewChannel` in `.open-swe/settings.json`."
        )
    broadcast_choice = (
        [{"id": broadcast_target.id, "name": broadcast_target.name}]
        if broadcast_target is not None and broadcast_target.name
        else []
    )
    active = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    displaced: HumanReviewRequest | None = None
    if active is not None and active.kind != "expedited":
        displaced, active = active, None
    if active is not None and active.thread_id and active.thread_id != thread_id:
        return _failure("This pull request's expedited review belongs to another agent thread")
    if active is not None and fingerprint_matches(files, active.diff_fingerprint):
        if active.awaiting_ready and not payload.draft:
            updated = await transition(active.id, expected=("open",), awaiting_ready=False)
            if updated is not None:
                active = updated
        if not active.awaiting_ready:
            await refresh_card(active)
            await broadcast_configured(active)
        readiness_warning = await prompt_author_ready(active)
        return {
            "success": True,
            "readiness_warning": readiness_warning,
            "approval_id": str(active.id),
            "pr_url": pr_ref.url,
            "head_sha": head_sha,
            "approvers": active.approvers,
            "slack_channel_id": active.slack_channel_id,
            "next": readiness_warning
            or (
                "The full draft card is in the author's DM; no thread card is posted until ready."
                if active.awaiting_ready
                else _next_step(
                    reused=True,
                    elsewhere=active.slack_channel_id != channel_id,
                    in_thread=False,
                )
            ),
        }
    if active is not None:
        await retire(active, "superseded", "Replaced by a card for the newer diff.")

    if not thread_ts:
        target = target or await SlackChannel.load(channel_id)
        if target is None:
            return _failure(f"Slack channel {channel_id} is unavailable")
        try:
            thread_ts = await _post_root_message(target, pr_ref, payload.title)
        except SlackRequestError as exc:
            return _failure(
                f"Could not post in Slack channel {channel_id}: {exc.code or 'unknown error'}. "
                "For a private channel, invite the bot first."
            )

    pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
    pull_request.title = payload.title
    pull_request.body = payload.body or ""
    pull_request.state = derive_pr_state(
        state=payload.state, merged=payload.merged, draft=payload.draft
    )
    pull_request.head_ref = payload.head_ref
    pull_request.base_ref = payload.base_ref
    pull_request.author = payload.author
    pull_request.author_github_id = payload.author_id
    pull_request = await pull_request.save()
    pull_request = await pull_request.link_thread(thread_id, source="expedited_review")
    # One open request per PR, so the displaced one closes before this row is written;
    # it is reopened below if the expedited card cannot be posted.
    if displaced is not None and (
        await retire(displaced, "superseded", "replaced by an expedited review") is None
    ):
        return _failure("The pull request's review request changed meanwhile. Try again.")
    approval = await HumanReviewRequest(
        pull_request_id=pull_request.id,
        thread_id=thread_id,
        head_sha=head_sha,
        kind="expedited",
        diff_fingerprint=verdict.fingerprint,
        tldr=summary_line(inline_summary),
        slack_channel_choices=broadcast_choice,
        awaiting_ready=payload.draft,
        slack_channel_id=channel_id,
        slack_thread_ts=thread_ts,
        run_config=dispatch_run_config(cfg, thread_id, None),
    ).save()
    if approval.awaiting_ready:
        readiness_warning = await prompt_author_ready(approval)
        if readiness_warning:
            await _discard(approval)
            if displaced is not None:
                await reopen(displaced)
            return _failure(readiness_warning)
        await remove_superseded_cards(approval)
        return {
            "success": True,
            "approval_id": str(approval.id),
            "pr_url": pr_ref.url,
            "head_sha": head_sha,
            "slack_channel_id": channel_id,
            "next": "The full draft card was sent only to the author by DM. The thread card "
            "will be posted once they mark it ready. Keep a /baby-sit watch on the PR.",
        }
    try:
        message_ts = await post_card(approval, title=payload.title, files=files)
    except SlackRequestError as exc:
        await _discard(approval)
        if displaced is not None:
            await reopen(displaced)
        return _failure(f"Could not post the approval card in Slack: {exc.code}")
    except BaseException:
        await _discard(approval)
        if displaced is not None:
            await reopen(displaced)
        raise
    approval.slack_message_ts = message_ts
    approval = await approval.save()
    await broadcast_configured(approval)
    await remove_superseded_cards(approval)
    readiness_warning = await prompt_author_ready(approval)
    return {
        "success": True,
        "readiness_warning": readiness_warning,
        "approval_id": str(approval.id),
        "pr_url": pr_ref.url,
        "head_sha": head_sha,
        "changed_lines": verdict.changed_lines,
        "test_lines": verdict.test_lines,
        "slack_channel_id": channel_id,
        "next": readiness_warning
        or _next_step(
            reused=False,
            elsewhere=False,
            in_thread=bool(own_thread) and (channel_id, thread_ts) == (own_channel, own_thread),
        ),
    }
