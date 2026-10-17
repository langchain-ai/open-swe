"""HTTP channel authentication, run payloads, and reply extraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from managed_deepagents._channels.assistant_text import extract_assistant_content
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._ingress import (
    _create_ingress_loopback_client,
    _has_loopback_credential,
    build_ingress_run_context,
)
from managed_deepagents._ingress_runs import (
    StartedIngressRun,
    observe_ingress_run,
    start_ingress_run,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.types import MessageContent


class HttpIngressResult(StartedIngressRun):
    reply_content: MessageContent | None


async def start_http_ingress_agent(
    *,
    user_id: str,
    source_thread_key: str,
    binding: ChannelBinding,
    thread_id: str,
    input: MessageContent,
    assistant_id: str,
    source_provider: str,
) -> StartedIngressRun:
    if not _has_loopback_credential():
        raise ChannelError(
            "channel_run_failed", "managed loopback authentication is not configured"
        )
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    return await start_ingress_run(
        client=client,
        thread_id=thread_id,
        assistant_id=assistant_id,
        messages=[normalize_http_message(input)],
        **build_ingress_run_context(binding=binding),
    )


async def observe_http_ingress_agent(
    *,
    user_id: str,
    source_thread_key: str,
    source_provider: str,
    thread_id: str,
    run_id: str,
) -> HttpIngressResult:
    client = _create_ingress_loopback_client(
        user_id=user_id,
        source_thread_key=source_thread_key,
        source_provider=source_provider,
    )
    observed = await observe_ingress_run(client=client, thread_id=thread_id, run_id=run_id)
    if observed["status"] == "interrupted":
        return {"reply_content": None, "thread_id": thread_id, "run_id": run_id}
    if observed["status"] != "success":
        raise ChannelError("channel_run_failed", "channel run did not complete successfully")
    values = observed["values"]
    state = values if isinstance(values, Mapping) else {}
    interrupts = state.get("__interrupt__")
    interrupted = isinstance(interrupts, (list, Mapping)) and bool(interrupts)
    return {
        "reply_content": None if interrupted else extract_assistant_content(state),
        "thread_id": thread_id,
        "run_id": run_id,
    }


def normalize_http_message(content: MessageContent) -> dict[str, object]:
    return {
        "role": "human",
        "content": content,
        "response_metadata": {} if isinstance(content, str) else {"output_version": "v1"},
    }
