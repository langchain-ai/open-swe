"""Routes for MDA's Slack channel: the Slack conversations the backend handed to MDA.

Open SWE's Slack app keeps its URLs at ``/webhooks/slack``, which forwards the
conversations that belong to MDA to Trigger Server (``openswe.slack.mda_forwarding``).
The deployment's Slack channel owns those conversations' threads and starts their
runs; its ``on_event`` names this endpoint, which it asks what each message starts.
The backend gated each message when Slack delivered it and remembered the request;
here it builds the run from that request as it would for its own Slack runs, with
the signed run token, and answers the channel with it.
"""

import logging
from typing import Annotated, Final, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from managed_deepagents import EndpointAnswer, EndpointThread
from managed_deepagents import JsonValue as AnswerJson
from pydantic import JsonValue
from typing_extensions import TypedDict

from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.mda_channels.auth import require_mda_channel
from openswe.slack import mda_forwarding
from openswe.slack import webhook as service
from openswe.slack.request import SlackRequest
from openswe.threads import index
from openswe.threads.router import channel_run_metadata, channel_run_starting
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks import common

logger = logging.getLogger(__name__)

_MESSAGE_KEYS: Final = frozenset({"mention", "directMessage", "message"})

router = APIRouter(prefix="/slack/channel", dependencies=[Depends(require_mda_channel)])


class SlackChannelCall(TypedDict):
    """What MDA's Slack channel POSTs: the event (MDA's ``SlackEndpointEvent``) and its thread.

    The event stays a plain object here: its fields are read one by one below.
    """

    event: dict[str, JsonValue]
    thread: EndpointThread


def _no_run(reason: str, **fields: str) -> None:
    """Answer the channel that the event starts nothing, with one log line saying why."""
    logger.info(
        "MDA's Slack channel endpoint answers no run",
        extra={"endpoint": "slack", "no_run_reason": reason, **fields},
    )


@router.post("/events")
async def slack_channel_event(
    call: SlackChannelCall, webhook_id: Annotated[str, Header(alias="webhook-id")]
) -> EndpointAnswer | None:
    """The run a forwarded Slack message starts on the channel's thread, or ``None``.

    ``None`` when the message wasn't one the backend handed over (the backend
    gates every message before it forwards one), when gating posted the reply
    itself, such as the prompt to connect an account, or when building the run
    failed, which the Slack thread hears about. MDA retrying a call it got no
    answer to, under the same ``webhook_id``, gets the run built again.
    """
    event, thread = call["event"], call["thread"]
    key = event.get("key")
    if key not in _MESSAGE_KEYS:
        # Button clicks in MDA-owned threads aren't forwarded yet.
        return _no_run("not a message", slack_channel_event=str(key))
    try:
        thread_id = str(UUID(thread["id"]))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid thread") from error
    message_ts = event.get("messageTs")
    if not isinstance(message_ts, str) or not message_ts:
        raise HTTPException(status_code=422, detail="The event names no message")
    fields = {"agent_thread_id": thread_id, "slack_message_ts": message_ts}

    remembered = await mda_forwarding.recall(thread_id, message_ts, webhook_id)
    if remembered is None:
        logger.warning(
            "MDA's Slack channel asked about a message the backend didn't hand over",
            extra=fields,
        )
        return _no_run("not handed over", **fields)
    request, repo = remembered
    try:
        answer = await _prepare_run(thread_id, request, repo)
    except Exception as exc:  # noqa: BLE001 - the person hears about it instead
        # The request was taken: nothing else will tell the Slack thread.
        await service.notify_slack_processing_error(request, repo.repo if repo else None, exc)
        return _no_run("building the run failed", **fields)
    if answer is None:
        return _no_run("no run prepared", **fields)
    return answer


async def _prepare_run(
    thread_id: str, request: SlackRequest, repo: common.SlackRepoResolution | None
) -> EndpointAnswer | None:
    prepared = await service.prepare_slack_channel_run(request, repo)
    if prepared is None:
        return None

    run_config = await prepare_durable_run(
        "agent",
        {"configurable": prepared.configurable},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: mda_forwarding.CHANNEL,
            "slack_trigger_ts": prepared.trigger_ts,
        },
        source="slack",
    )
    indexed = await index.get(thread_id)
    rewritten = await RUNTIMES.mda_runtime.run_payload(
        thread_id,
        {
            "assistant_id": "agent",
            "config": cast(JsonValue, run_config),
            # So the attribution the run's hooks read rides in its run token.
            "metadata": cast(JsonValue, run_config["metadata"]),
        },
        thread_metadata=indexed.metadata if indexed else {},
    )
    answer: EndpointAnswer = {
        "input": cast(dict[str, AnswerJson], prepared.input),
        "context": cast(dict[str, AnswerJson], rewritten["context"]),
        "metadata": cast(dict[str, AnswerJson], channel_run_metadata(run_config["metadata"])),
    }
    if COMPLETION_WEBHOOK_URL:
        answer["webhook"] = COMPLETION_WEBHOOK_URL
    await channel_run_starting(thread_id)
    return answer
