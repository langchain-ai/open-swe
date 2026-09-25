"""Ask a participant before Open SWE opens a PR as them in a shared thread.

In a thread with more than one participant anyone can steer the run, so the
person the PR opens as gets the final say, once per thread. They get a DM card
and the tool waits for their answer; an answer that comes later still applies to
the next attempt. "Always allow" skips the card, and a thread with a single
participant never asks.
"""

import asyncio
import logging
from typing import Literal, TypedDict

from agent.act_as.records import ActAsRequest, ThreadActAs
from agent.act_as.slack import card_blocks
from agent.credential_scope import pr_author_login
from agent.prompts import render_prompt
from agent.slack.blocks import block_payload
from agent.slack.client import open_slack_dm, post_slack_top_level_message_with_ts
from agent.slack.dm import note_for_concierge
from agent.users import User
from agent.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

_WAIT_SECONDS = 120.0
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
            f"{login} did not answer within {int(_WAIT_SECONDS)} seconds. Their answer stays "
            "recorded for this thread, so calling open_pull_request again after they approve "
            "opens the PR."
        ),
        "denied": f"{login} denied Open SWE acting as them in this thread.",
        "unreachable": f"{login} could not be sent the approval DM in Slack.",
    }[refusal]
    return {
        "success": False,
        "error": (
            "This thread has more than one participant, so the person the PR opens as must "
            f"approve it. {reason} PR created: no."
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
) -> ActAsRefusal | None:
    """``None`` when the PR may open as its author; otherwise why not."""
    if token_kind != "user" or not thread_id:
        return None
    thread = await ThreadActAs.load(thread_id)
    if not thread.is_shared:
        return None
    login = await pr_author_login(author)
    if not login:
        return None
    person = await User.for_login("github", login)
    if person is not None and person.typed_preferences.act_as_always_allowed:
        return None
    existing = thread.for_login(login)
    if existing is not None and existing.status != "pending":
        return None if existing.status == "approved" else _refusal(login, "denied", token_kind)

    slack_user_id = person.slack_user_id if person is not None else ""
    if not slack_user_id:
        return _refusal(login, "unreachable", token_kind)
    request = await thread.request(login, owner=owner, repo=repo, head=head, base=base, title=title)
    if not request.notified:
        if not await _send_card(slack_user_id, request, thread_id):
            return _refusal(login, "unreachable", token_kind)
        await thread.mark_notified(request)
    return await _wait_for_answer(thread_id, login, token_kind)


async def _send_card(slack_user_id: str, request: ActAsRequest, thread_id: str) -> bool:
    thread_url = dashboard_thread_url(thread_id)
    thread_link = f"<{thread_url}|this thread>" if thread_url else "a shared thread"
    repo = f"{request.owner}/{request.repo}"
    message = (
        f":raised_hand: Open SWE wants to open a PR as you in {thread_link}.\n\n"
        f"*{request.title}*\n`{repo}` — `{request.head}` → `{request.base}`\n\n"
        "Approve, always allow, or deny."
    )
    dm_channel_id, error = await open_slack_dm(slack_user_id)
    message_ts = None
    if dm_channel_id:
        message_ts, error = await post_slack_top_level_message_with_ts(
            dm_channel_id, message, blocks=block_payload(card_blocks(message, request, thread_id))
        )
    if not dm_channel_id or not message_ts:
        logger.error(
            "Could not DM the act-as card",
            extra={"login": request.login, "thread_id": thread_id, "error": error},
        )
        return False
    await note_for_concierge(
        slack_user_id,
        dm_channel_id,
        render_prompt(
            "slack/concierge-act-as-requested.md",
            thread_url=thread_url or thread_id,
            title=request.title,
            repo=repo,
            head=request.head,
            base=request.base,
        ),
    )
    return True


async def _wait_for_answer(thread_id: str, login: str, token_kind: str) -> ActAsRefusal | None:
    for _ in range(int(_WAIT_SECONDS / _POLL_SECONDS)):
        await asyncio.sleep(_POLL_SECONDS)
        request = (await ThreadActAs.load(thread_id)).for_login(login)
        if request is not None and request.status == "approved":
            return None
        if request is not None and request.status == "denied":
            return _refusal(login, "denied", token_kind)
    return _refusal(login, "pending", token_kind)
