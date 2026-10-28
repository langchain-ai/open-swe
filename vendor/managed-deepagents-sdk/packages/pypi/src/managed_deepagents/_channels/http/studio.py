"""Links from channel notices into the deployment's LangSmith Studio."""

from __future__ import annotations

import os
import re
import uuid
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Mapping

    from managed_deepagents._channels.http.types import HttpDelivery, HttpFinish

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_DEPLOYMENT_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_GRAPH_ID_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
# The namespace the Agent Server derives a graph's default assistant from.
_GRAPH_NAMESPACE = uuid.UUID("6ba7b821-9dad-11d1-80b4-00c04fd430c8")


def studio_url(run: HttpDelivery | None, env: Mapping[str, str] | None = None) -> str | None:
    """The thread in the deployment's Studio.

    The deployment's page when the deployment's name or the thread's assistant
    is unknown. The LangSmith UI is the one ``mda deploy`` recorded for the
    deployment's environment (``MDA_LANGSMITH_URL``); without it there is
    nothing to link to.
    """
    from managed_deepagents._connections import DEPLOYMENT_ID_ENV  # noqa: PLC0415
    from managed_deepagents.runtime import _managed_deployment_name  # noqa: PLC0415

    values = os.environ if env is None else env
    thread_id = (run or {}).get("thread_id", "")
    thread = thread_id if _UUID_PATTERN.fullmatch(thread_id) else None
    workspace = values.get("LANGSMITH_TENANT_ID", "").strip()
    deployment = values.get(DEPLOYMENT_ID_ENV, "").strip()
    if not (_UUID_PATTERN.fullmatch(workspace) and _UUID_PATTERN.fullmatch(deployment)):
        return None
    recorded = _http_url(values.get("MDA_LANGSMITH_URL", ""))
    if recorded is None:
        return None
    url = urlsplit(recorded)
    origin = f"{url.scheme}://{url.netloc}"
    name = _managed_deployment_name(values) or ""
    assistant_id = _assistant_uuid((run or {}).get("assistant_id", ""))
    if not (_DEPLOYMENT_NAME_PATTERN.fullmatch(name) and thread and assistant_id):
        return f"{origin}/o/{workspace}/host/deployments/{deployment}"
    query = urlencode(
        {
            "organizationId": workspace,
            "render": "interact",
            "hostProjectId": deployment,
            "assistantId": assistant_id,
            "mode": "chat",
            "threadId": thread,
        }
    )
    return f"{origin}/o/{workspace}/a/{quote(name, safe='')}/studio/thread?{query}"


def pending_decision(finish: HttpFinish) -> Literal["waiting"] | None:
    """``waiting`` when a finished delivery's message found the thread waiting on a decision.

    A run that stopped on interrupts posts its own ``interrupted`` notice as it
    ends (``paused_notice``), so a ``run`` delivery has none here. ``None`` when
    nothing waits, or the delivery was a cancel or lost.
    """
    if not finish["interrupts"]:
        return None
    return "waiting" if finish["type"] == "skipped" and finish["reason"] == "waiting" else None


def interrupted_notice(
    outcome: Literal["interrupted", "waiting"], delivery: HttpDelivery, again: str
) -> str:
    """What a channel tells the conversation when its thread waits on a decision.

    It links to the thread in Studio, where the decision is made: the run the
    delivery started stopped on interrupts (``interrupted``), or the message
    found the thread waiting (``waiting``). ``again`` says how to continue once
    it is made, such as "comment again".
    """
    url = studio_url(delivery)
    studio = f"[LangSmith Studio]({url})" if url else "LangSmith Studio"
    if outcome == "interrupted":
        return f"I need a decision before I continue. Review the pending step in {studio}."
    return (
        "I'm waiting for a decision on an earlier step in this conversation, so I haven't "
        f"read this yet. Review the pending step in {studio}, then {again}."
    )


def _assistant_uuid(assistant: str) -> str | None:
    """Studio opens an assistant by its ID.

    Channels start runs by graph ID, whose default assistant the Agent Server
    derives from it.
    """
    if _UUID_PATTERN.fullmatch(assistant):
        return assistant
    if _GRAPH_ID_PATTERN.fullmatch(assistant):
        return str(uuid.uuid5(_GRAPH_NAMESPACE, assistant))
    return None


def _http_url(value: str) -> str | None:
    """A plain http(s) URL, without credentials."""
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("http", "https") or not url.hostname:
            return None
    except ValueError:
        return None
    if url.username is not None or url.password is not None:
        return None
    return urlunsplit(url)
