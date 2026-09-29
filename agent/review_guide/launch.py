"""Open a review guide code channel for a pull request, and wake it when the PR moves."""

import logging
from uuid import uuid4

from pydantic import BaseModel

from agent.dashboard.profiles import get_valid_access_token
from agent.dispatch import create_durable_run
from agent.expedited_review.reviews import github_token_hint
from agent.github.pull_requests import PullRequest, PullRequestEvent
from agent.input_messages import build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import prompt
from agent.review_guide.github import fetch_head
from agent.review_guide.sessions import ASSISTANT_ID, GuideMode, ReviewGuideSession
from agent.slack.client import bind_slack_thread_id, invite_to_slack_channel, slack_user_ids
from agent.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    archive_code_channel,
    create_code_channel,
    repo_context_bar_items,
    set_context_bar,
    set_session_status,
)
from agent.source_context import SlackThreadRef, SourceContext
from agent.users import User
from agent.utils.thread_ops import langgraph_client
from agent.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

_SENDER_ID = "system:review-guide"


class GuideStart(BaseModel):
    owner: str
    repo: str
    number: int
    requester_slack_id: str
    origin_channel_id: str
    origin_message_ts: str
    team_id: str = ""
    workspace_slug: str | None = None
    invite: list[str] = []


class StartedGuide(BaseModel):
    thread_id: str
    channel_id: str


class GuideStartError(RuntimeError):
    """The review guide could not be opened; the message says why."""


async def start_review_guide(start: GuideStart) -> StartedGuide:
    user = await User.for_identity("slack", start.requester_slack_id)
    login = user.github_login if user else ""
    if user is None or not login:
        raise GuideStartError("the requester has no GitHub account linked to Open SWE")
    # The channel shows the PR's code, so only someone who can read it may open one.
    user_token = await get_valid_access_token(login)
    if not user_token:
        raise GuideStartError(f"Open SWE has no GitHub token for @{login}. {github_token_hint()}")
    head = await fetch_head(start.owner, start.repo, start.number, token=user_token)
    if head is None:
        raise GuideStartError(f"@{login} cannot read that pull request on GitHub")
    if head.state != "open":
        raise GuideStartError("the pull request is not open")
    pull_request = await PullRequest(
        owner=start.owner, repo=start.repo, number=start.number
    ).ensure()
    mode: GuideMode = (
        "author"
        if head.author.lower() == login.lower() or await pull_request.is_authored_by(login)
        else "reviewer"
    )
    thread_id = str(uuid4())
    channel_id, error = await create_code_channel(
        name=f"Review {start.repo}#{start.number}: {head.title}"[:200],
        session_id=thread_id,
        origin_channel_id=start.origin_channel_id,
        origin_message_ts=start.origin_message_ts,
        team_id=start.team_id,
    )
    if channel_id is None:
        raise GuideStartError(error or "Slack could not create the code channel")
    location = SlackThreadRef(
        channel_id=channel_id,
        thread_ts=CODE_CHANNEL_SESSION_TS,
        triggering_event_ts=start.origin_message_ts,
        team_id=start.team_id,
    )
    try:
        client = langgraph_client()
        if not await upsert_agent_thread_metadata(
            thread_id,
            source="slack",
            repo_config={"owner": start.owner, "name": start.repo},
            github_login=login,
            title=f"Review {start.repo}#{start.number}: {head.title}",
            static_title=True,
            source_context=SourceContext(slack_thread=location),
            workspace=start.workspace_slug,
            owner_login=login,
            # Web messages run the main agent, not the guide, so keep it off the dashboard.
            unlisted=True,
        ):
            raise GuideStartError("could not create the review thread")
        await bind_slack_thread_id(client, channel_id, CODE_CHANNEL_SESSION_TS, thread_id)
        await ReviewGuideSession.create(
            thread_id=thread_id,
            pull_request=pull_request,
            user_id=user.id,
            slack_channel_id=channel_id,
            workspace_slug=start.workspace_slug,
            mode=mode,
        )
    except Exception:
        if not (await archive_code_channel(channel_id))[0]:
            logger.warning(
                "Could not archive an unbound review guide channel",
                extra={"slack_channel": channel_id},
            )
        raise
    _, invite_error = await invite_to_slack_channel(
        channel_id, slack_user_ids([start.requester_slack_id, *start.invite])
    )
    if invite_error:
        logger.warning(
            "Could not invite everyone to a review guide channel",
            extra={"slack_channel": channel_id, "slack_error": invite_error},
        )
    await set_context_bar(
        channel_id,
        repo_context_bar_items({"owner": start.owner, "name": start.repo}, pr_url=pull_request.url),
    )
    await dispatch_guide_run(
        thread_id,
        location,
        prompt("review-guide/kickoff", pr_number=start.number),
        workspace_slug=start.workspace_slug,
    )
    return StartedGuide(thread_id=thread_id, channel_id=channel_id)


async def dispatch_guide_run(
    thread_id: str, location: SlackThreadRef, text: str, *, workspace_slug: str | None
) -> None:
    """Run the guide on a system turn: no person triggered it, so it cannot approve."""
    configurable: dict[str, object] = {
        "thread_id": thread_id,
        "slack_thread": location.dump(),
        "source": "slack",
    }
    if workspace_slug:
        configurable["workspace"] = workspace_slug
    if location.channel_id:
        await set_session_status(location.channel_id, "processing")
    await create_durable_run(
        thread_id,
        ASSISTANT_ID,
        input=build_run_input(
            text,
            {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
            systems=[{"id": _SENDER_ID, "display_name": "Review guide", "platform": "open-swe"}],
        ),
        source="review-guide",
        thread_title=None,
        config={"configurable": with_invocation_id(configurable, new_invocation_id())},
        multitask_strategy="enqueue",
    )


async def notify_pr_updated(payload: dict[str, object]) -> None:
    """Wake every guide on a pull request that just moved so it shows the new code."""
    event = PullRequestEvent.parse(payload)
    identity = event.identity if event else None
    if identity is None:
        return
    owner, repo, number = identity
    for session in await ReviewGuideSession.for_pull_request(owner, repo, number):
        location = SlackThreadRef(
            channel_id=session.slack_channel_id, thread_ts=CODE_CHANNEL_SESSION_TS
        )
        try:
            await dispatch_guide_run(
                session.thread_id,
                location,
                prompt("review-guide/pr-updated"),
                workspace_slug=session.workspace_slug,
            )
        except Exception:
            logger.exception(
                "Could not wake a review guide for an updated pull request",
                extra={"agent_thread_id": session.thread_id, "pr_number": number},
            )
