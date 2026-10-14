"""Require the author's consent for delegated PR actions, once per thread."""

import asyncio
import logging
from typing import Literal, TypedDict

from langgraph_sdk import get_client

from openswe.act_as.records import ActAsRequest, Decision, ThreadActAs
from openswe.act_as.slack import card_blocks
from openswe.credential_scope import pr_author_login
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import block_payload, escape
from openswe.slack.cards import origin_footer
from openswe.slack.client import (
    get_active_slack_thread,
    get_slack_permalink,
    post_slack_top_level_message_with_ts,
)
from openswe.slack.dm import note_for_concierge, open_dm
from openswe.slack.http import SlackRequestError
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

_WAIT_SECONDS = 30.0
_POLL_SECONDS = 2.0

Refusal = Literal["pending", "denied", "unreachable"]


class ActAsRefusal(TypedDict):
    success: Literal[False]
    error: str
    act_as: Refusal
    act_as_login: str
    token_kind: str


def _refusal(login: str, refusal: Refusal, token_kind: str) -> ActAsRefusal:
    reason = {
        "pending": (
            f"{login} did not answer within {int(_WAIT_SECONDS)} seconds. Their answer will "
            "arrive as a new message in this thread."
        ),
        "denied": f"{login} denied Open SWE acting as them in this thread.",
        "unreachable": f"{login} could not be sent the approval DM in Slack.",
    }[refusal]
    return {
        "success": False,
        "error": (
            "The PR author must approve Open SWE acting on their behalf. "
            f"{reason} PR action performed: no."
        ),
        "act_as": refusal,
        "act_as_login": login,
        "token_kind": token_kind,
    }


async def require_consent(
    *,
    thread_id: str | None,
    token_kind: str,
    author: str | None,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    action: Literal["open", "close", "merge"] = "open",
    relayed: bool = False,
) -> ActAsRefusal | None:
    """Return a refusal unless the author has authorized the delegated PR action."""
    if action == "open" and token_kind != "user":
        return None
    if not thread_id:
        return _refusal(author or "the PR author", "unreachable", token_kind)
    thread = await ThreadActAs.load(thread_id)
    login = await pr_author_login(author) if action == "open" else author
    if not login:
        return _refusal("the PR author", "unreachable", token_kind)
    cfg = RunConfig.from_runtime()
    if (
        action == "open"
        and not thread.is_shared()
        and not cfg.relayed_from_thread
        and not relayed
        and login.lower() == (cfg.github_login or "").lower()
    ):
        return None
    person = await User.for_login("github", login)
    if person is None:
        return _refusal(login, "unreachable", token_kind)
    if person.typed_preferences.act_as_always_allowed:
        return None
    decision = thread.decision_for(login)
    if decision is not None:
        return _outcome(decision, login, token_kind)

    slack_user_id = person.slack_user_id
    if not slack_user_id:
        return _refusal(login, "unreachable", token_kind)
    request = await thread.request(login, owner=owner, repo=repo, head=head, base=base, title=title)
    if not request.notified:
        if not await _send_card(slack_user_id, request, thread_id):
            return _refusal(login, "unreachable", token_kind)
        await thread.mark_notified(request)
    return await _wait_for_answer(thread, request, token_kind)


async def _send_card(slack_user_id: str, request: ActAsRequest, thread_id: str) -> bool:
    thread_url = dashboard_thread_url(thread_id)
    slack_thread = await get_active_slack_thread(get_client(), thread_id)
    if slack_thread:
        permalink = slack_thread.get("permalink")
        if not isinstance(permalink, str) or not permalink.strip():
            channel_id, thread_ts = slack_thread.get("channel_id"), slack_thread.get("thread_ts")
            if isinstance(channel_id, str) and isinstance(thread_ts, str):
                permalink = await get_slack_permalink(channel_id, thread_ts)
        if isinstance(permalink, str) and permalink.strip():
            thread_url = permalink.strip()
    thread_link = f"<{thread_url}|this thread>" if thread_url else "a shared thread"
    repo = escape(f"{request.owner}/{request.repo}")
    message = (
        f":raised_hand: Open SWE wants to open, close, or merge PRs on your behalf in {thread_link}.\n\n"
        f"*{escape(request.title)}*\n"
        f"`{repo}` — `{escape(request.head)}` → `{escape(request.base)}`\n\n"
        "Approve, always allow, or deny."
    )
    dm_channel_id = await open_dm(slack_user_id)
    if not dm_channel_id:
        logger.error("Could not open act-as DM", extra={"login": request.login})
        return False
    try:
        await post_slack_top_level_message_with_ts(
            dm_channel_id,
            message,
            unfurl_links=False,
            unfurl_media=False,
            blocks=block_payload(
                [*card_blocks(message, request, thread_id), *await origin_footer(thread_id)]
            ),
        )
    except SlackRequestError as exc:
        logger.error(
            "Could not DM the act-as card",
            extra={"login": request.login, "thread_id": thread_id, "error": exc.code},
        )
        return False
    await note_for_concierge(
        slack_user_id,
        dm_channel_id,
        prompt("slack/concierge-act-as-requested", thread_url=thread_url or thread_id),
    )
    return True


def _outcome(decision: Decision, login: str, token_kind: str) -> ActAsRefusal | None:
    return None if decision == "approved" else _refusal(login, "denied", token_kind)


async def _wait_for_answer(
    thread: ThreadActAs, request: ActAsRequest, token_kind: str
) -> ActAsRefusal | None:
    login = request.login
    if request.wake_on_answer:
        await thread.set_wake_on_answer(request, False)
    for _ in range(int(_WAIT_SECONDS / _POLL_SECONDS)):
        await asyncio.sleep(_POLL_SECONDS)
        decision = (await ThreadActAs.load(thread.thread_id)).decision_for(login)
        if decision is not None:
            return _outcome(decision, login, token_kind)
    await thread.set_wake_on_answer(request, True)
    # An answer recorded before the flag landed will not wake the thread, so act on it here.
    decision = (await ThreadActAs.load(thread.thread_id)).decision_for(login)
    if decision is not None:
        return _outcome(decision, login, token_kind)
    return _refusal(login, "pending", token_kind)
