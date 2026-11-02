"""Every Slack click Trigger Server forwards: buttons, feedback and submitted modals.

In this deployment's conversations, the handler checks who may click the agent's
options, workflow push approvals and Retry, updates the message through
``response_url``, and the backend builds the run (``plan_slack_click``), once per
clicked message. Every other click, and one in a conversation the backend holds, the
backend acts on as it always has (``act_on_slack_click``).
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
from runtime_api.slack import (
    ACT_ON_SLACK_CLICK,
    PLAN_SLACK_CLICK,
    ActOnSlackClick,
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
    if actor is None:
        return None
    value = _button(event.value) if event.message_ts is not None else None
    if value is not None and not (value.get("type") == "slack_retry" and value.get("response")):
        click = _Click(event, thread, actor.id, value)
        match value.get("type"):
            case "open_swe_option":
                result = await click.option()
            case "workflow_push_approval":
                result = await click.workflow_push()
            case "slack_retry":
                result = await click.retry()
            case _:
                click.backend = True
                result = None
        if not click.backend:
            return result
    await ask_backend(
        ACT_ON_SLACK_CLICK,
        ActOnSlackClick(
            channel_id=event.channel_id,
            thread_ts=event.thread_ts,
            user_id=actor.id,
            payload=event.payload,
        ),
        msg_id=f"msg_{event.delivery_id}",
    )
    return None


class _Click:
    def __init__(
        self,
        event: SlackInteractionEvent,
        thread: SlackChannelThread,
        clicker: str,
        value: dict[str, JsonValue],
    ) -> None:
        self.event = event
        self.thread = thread
        self.clicker = clicker
        self.value = value
        self.message = _mapping(event.payload.get("message"))
        self.action = _first_option_action(event.payload.get("actions"))
        self.response_url = _response_url(event.payload.get("response_url"))
        # The click is the backend's to act on.
        self.backend = False

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
        if start.backend or not start.acted:
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
        if start.backend:
            return None
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
        start = await self._ask(click)
        self.backend = start.backend
        return start

    async def _ask(self, click: SlackClick) -> SlackClickStart:
        team = _mapping(self.event.payload.get("team"))
        return await ask_backend(
            PLAN_SLACK_CLICK,
            PlanSlackClick(
                thread_id=self.thread.id,
                channel_id=self.event.channel_id,
                thread_ts=self.event.thread_ts,
                message_ts=cast(str, self.event.message_ts),
                action_ts=_text(self.action.get("action_ts")) or cast(str, self.event.message_ts),
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
        await self._respond({"response_type": "ephemeral", "replace_original": False, "text": text})

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
