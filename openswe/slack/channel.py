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
from typing import Any, Final, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from managed_deepagents import EndpointAnswer, EndpointThread
from managed_deepagents import JsonValue as AnswerJson
from pydantic import JsonValue
from typing_extensions import TypedDict

from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.mda_channels.auth import require_mda_channel
from openswe.slack import mda_forwarding
from openswe.slack import webhook as service
from openswe.threads import index
from openswe.threads.router import channel_run_starting
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks import common

logger = logging.getLogger(__name__)

_MESSAGE_KEYS: Final = frozenset({"mention", "directMessage", "message"})

router = APIRouter(prefix="/slack/channel", dependencies=[Depends(require_mda_channel)])


class SlackChannelCall(TypedDict):
    """What MDA's Slack channel POSTs: the event (MDA's ``SlackEndpointEvent``) and its thread.

    The event stays a plain object here: its fields are read one by one below.
    """

    event: dict[str, Any]
    thread: EndpointThread


@router.post("/events")
async def slack_channel_event(call: SlackChannelCall) -> EndpointAnswer | None:
    """The run a forwarded Slack message starts on the channel's thread, or ``None``.

    ``None`` when the message wasn't one the backend handed over (the backend
    gates every message before it forwards one), or when gating posted the reply
    itself, such as the prompt to connect an account.
    """
    event, thread = call["event"], call["thread"]
    key = event.get("key")
    if key not in _MESSAGE_KEYS:
        # Button clicks in MDA-owned threads aren't forwarded yet.
        logger.info(
            "MDA's Slack channel event starts no run", extra={"slack_channel_event": str(key)}
        )
        return None
    try:
        thread_id = str(UUID(thread["id"]))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid thread") from error
    message_ts = event.get("messageTs")
    if not isinstance(message_ts, str) or not message_ts:
        raise HTTPException(status_code=422, detail="The event names no message")

    remembered = await mda_forwarding.recall(thread_id, message_ts)
    if remembered is None:
        logger.warning(
            "MDA's Slack channel asked about a message the backend didn't hand over",
            extra={"agent_thread_id": thread_id},
        )
        return None
    request, repo = remembered
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
        {"assistant_id": "agent", "config": cast(JsonValue, run_config)},
        thread_metadata=indexed.metadata if indexed else {},
    )
    answer: EndpointAnswer = {
        "input": cast(dict[str, AnswerJson], prepared.input),
        "context": cast(dict[str, AnswerJson], rewritten["context"]),
        "metadata": cast(dict[str, AnswerJson], run_config["metadata"]),
    }
    if COMPLETION_WEBHOOK_URL:
        answer["webhook"] = COMPLETION_WEBHOOK_URL
    await channel_run_starting(thread_id)
    return answer
