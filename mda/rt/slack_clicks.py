"""Clicks on the agent's buttons in this deployment's Slack conversations.

The handler checks who may click and updates the message through ``response_url``;
the backend builds the run (``plan_slack_click``), once per clicked message.
"""

import json
import logging
import time
from typing import Final, cast
from urllib.parse import urlsplit

import httpx2
from managed_deepagents import (
    ChannelRunResult,
    JsonValue,
    SlackChannelThread,
    SlackInteractionEvent,
)
from managed_deepagents._channels.slack.api import (  # noqa: PLC2701
    slack_api,
    slack_bot_token,
)
from managed_deepagents._channels.types import ChannelError  # noqa: PLC2701
from runtime_api.slack import (
    PLAN_SLACK_CLICK,
    OptionClick,
    PlanSlackClick,
    RetryClick,
    SlackClick,
    SlackClickStart,
    WorkflowPushClick,
)

from rt.intake import ask_backend
from rt.slack import run_result

logger = logging.getLogger(__name__)

_OPTION_ACTION_ID: Final = "open_swe_option_select"
_RESPONSE_URL_HOST: Final = "hooks.slack.com"
_TIMEOUT: Final = httpx2.Timeout(10.0, connect=5.0)
_LABEL_LIMIT: Final = 150

_FOR_SOMEONE_ELSE: Final = "This choice is for someone else. No action was taken."
_NOT_YOUR_RETRY: Final = "Only the person whose request failed can retry it. No action was taken."
_RETRY_EXPIRED: Final = (
    "This Retry button has expired or is invalid. Send your request again to retry."
)
_NO_WORKFLOW_PUSH: Final = (
    "I couldn't find that workflow approval request. "
    "Trigger the push again to create a fresh approval."
)
_WORKFLOW_PUSH_DECIDED: Final = {
    "approved": "Workflow push approved. Open SWE will retry the blocked push.",
    "rejected": "Workflow push rejected. No workflow files will be pushed.",
}


async def start_click(
    event: SlackInteractionEvent, thread: SlackChannelThread
) -> ChannelRunResult | None:
    actor = event.actor
    value = _button(event.value)
    # The agent's buttons sit on a message in a Slack thread.
    if actor is None or event.message_ts is None or value is None:
        return None
    click = _Click(event, thread, actor.id, value, (event.thread_ts, event.message_ts))
    match value.get("type"):
        case "open_swe_option":
            return await click.option()
        case "workflow_push_approval":
            return await click.workflow_push()
        case "slack_retry":
            return await click.retry()
    return None


class _Click:
    def __init__(
        self,
        event: SlackInteractionEvent,
        thread: SlackChannelThread,
        clicker: str,
        value: dict[str, JsonValue],
        message: tuple[str, str],
    ) -> None:
        self.event = event
        self.thread = thread
        self.clicker = clicker
        self.value = value
        self.thread_ts, self.message_ts = message
        self.message = _mapping(event.payload.get("message"))
        self.action = _first_option_action(event.payload.get("actions"))
        self.response_url = _response_url(event.payload.get("response_url"))

    async def option(self) -> ChannelRunResult | None:
        recipient = self.value.get("user_id")
        if isinstance(recipient, str) and recipient and recipient != self.clicker:
            await self._tell_clicker(_FOR_SOMEONE_ELSE)
            return None
        response = self.value.get("response")
        text = response.strip() if isinstance(response, str) else ""
        if not text:
            return None
        start = await self._plan(OptionClick(text=text))
        if not start.acted:
            return None
        await self._replace_buttons(f"Selected: {self._label(text)}")
        return run_result(start)

    async def workflow_push(self) -> ChannelRunResult | None:
        fingerprint = self.value.get("fingerprint")
        action = self.value.get("action")
        if (
            not isinstance(fingerprint, str)
            or not fingerprint
            or action not in {"approve", "reject"}
        ):
            return None
        start = await self._plan(
            WorkflowPushClick(fingerprint=fingerprint, approved=action == "approve")
        )
        if start.workflow_push is None:
            await self._tell_clicker(_NO_WORKFLOW_PUSH)
            return None
        await self._replace_buttons(_WORKFLOW_PUSH_DECIDED[start.workflow_push])
        return run_result(start) if start.acted else None

    async def retry(self) -> ChannelRunResult | None:
        fields = {key: self.value.get(key) for key in ("user_id", "channel_id", "thread_ts")}
        if fields["user_id"] != self.clicker or fields["channel_id"] != self.event.channel_id:
            await self._tell_clicker(_NOT_YOUR_RETRY)
            return None
        thread_ts = fields["thread_ts"]
        expires_at = self.value.get("expires_at")
        if not isinstance(thread_ts, str) or not thread_ts or _expired(expires_at):
            await self._tell_clicker(_RETRY_EXPIRED)
            return None
        # Before anything runs, so a second click finds no button.
        await self._replace_buttons("Retrying…")
        try:
            start = await self._plan(
                RetryClick(
                    thread_ts=thread_ts,
                    message_ts=_text(self.value.get("message_ts")),
                    reply_thread_ts=_text(self.value.get("reply_thread_ts")),
                )
            )
        except Exception:
            await self._restore_message()
            raise
        return run_result(start) if start.acted else None

    async def _plan(self, click: SlackClick) -> SlackClickStart:
        team = _mapping(self.event.payload.get("team"))
        return await ask_backend(
            PLAN_SLACK_CLICK,
            PlanSlackClick(
                thread_id=self.thread.id,
                channel_id=self.event.channel_id,
                thread_ts=self.thread_ts,
                message_ts=self.message_ts,
                action_ts=_text(self.action.get("action_ts")) or self.message_ts,
                user_id=self.clicker,
                team_id=_text(team.get("id")),
                click=click,
            ),
            msg_id=f"msg_{self.event.delivery_id}",
        )

    def _label(self, fallback: str) -> str:
        text = _mapping(self.action.get("text")).get("text")
        return ((text if isinstance(text, str) else "") or fallback).strip()[:_LABEL_LIMIT]

    async def _tell_clicker(self, text: str) -> None:
        # Slack shows a response_url note in the channel, even for a click in a thread.
        try:
            await slack_api(
                await slack_bot_token(),
                "chat.postEphemeral",
                {
                    "channel": self.event.channel_id,
                    "user": self.clicker,
                    "thread_ts": self.thread_ts,
                    "text": text,
                },
            )
        except ChannelError as error:
            logger.warning(
                "Could not tell the clicker in the Slack thread",
                extra={"slack_error": getattr(error, "provider_error", "")},
            )
            await self._respond(
                {"response_type": "ephemeral", "replace_original": False, "text": text}
            )

    async def _replace_buttons(self, text: str) -> None:
        blocks = replaced_buttons(self.message.get("blocks"), text)
        if blocks:
            await self._respond(
                {
                    "replace_original": True,
                    "text": _text(self.message.get("text")) or text,
                    "blocks": cast(JsonValue, blocks),
                }
            )

    async def _restore_message(self) -> None:
        blocks = self.message.get("blocks")
        if isinstance(blocks, list):
            await self._respond(
                {
                    "replace_original": True,
                    "text": _text(self.message.get("text")),
                    "blocks": blocks,
                }
            )

    async def _respond(self, body: dict[str, JsonValue]) -> None:
        if self.response_url is None:
            return
        try:
            async with httpx2.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
                response = await client.post(self.response_url, json=body)
            response.raise_for_status()
        except httpx2.HTTPError as error:
            logger.warning("Could not update the clicked Slack message (%s)", type(error).__name__)


def replaced_buttons(blocks: object, text: str) -> list[dict[str, JsonValue]]:
    """The message's blocks with its option buttons replaced by ``text``; empty when it has none."""
    if not isinstance(blocks, list):
        return []
    line: dict[str, JsonValue] = {
        "type": "context",
        "elements": [{"type": "plain_text", "text": text}],
    }
    updated: list[dict[str, JsonValue]] = []
    replaced = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        elements = block.get("elements")
        if block.get("type") != "actions" or not _has_option(elements):
            updated.append(block)
            continue
        if not replaced:
            updated.append(line)
            replaced = True
        remaining = [
            element for element in cast(list[JsonValue], elements) if not _has_option([element])
        ]
        if remaining:
            updated.append({**block, "elements": remaining})
    return updated if replaced else []


def _button(value: JsonValue) -> dict[str, JsonValue] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return cast(dict[str, JsonValue], value) if isinstance(value, dict) else None


def _first_option_action(actions: JsonValue) -> dict[str, JsonValue]:
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and _is_option(action.get("action_id")):
            return cast(dict[str, JsonValue], action)
    return {}


def _is_option(action_id: object) -> bool:
    return isinstance(action_id, str) and (
        action_id == _OPTION_ACTION_ID or action_id.startswith(f"{_OPTION_ACTION_ID}_")
    )


def _has_option(elements: object) -> bool:
    return isinstance(elements, list) and any(
        isinstance(element, dict) and _is_option(element.get("action_id")) for element in elements
    )


def _response_url(value: JsonValue) -> str | None:
    if not isinstance(value, str):
        return None
    url = urlsplit(value)
    return value if url.scheme == "https" and url.hostname == _RESPONSE_URL_HOST else None


def _expired(expires_at: JsonValue) -> bool:
    return isinstance(expires_at, int | float) and 0 < expires_at < time.time()


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value) if isinstance(value, dict) else {}


def _text(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""
