"""Topic names, and which session may hear each one.

A topic is ``<kind>`` or ``<kind>/<key>``. An invalidation carries nothing but
the topic, so hearing one reveals only that something the session could already
read has changed; each kind still answers who may subscribe, because a key can name
something private.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph_sdk.errors import NotFoundError

from agent.threads.summary import thread_is_readable
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

type Session = dict[str, Any]
type Authorizer = Callable[[Session, str], Awaitable[bool]]

WORKSPACES = "workspaces"
"""Any workspace's definition, bindings, settings, snapshot or refresh state."""

THREAD = "thread"
"""``thread/<thread_id>``: that thread's summary, such as its pending wakeup."""

TOPIC_PATTERN = re.compile(r"^[a-z][a-z-]*(?:/[A-Za-z0-9._:@-]+)*$")
MAX_TOPIC_LENGTH = 200


async def _signed_in(_session: Session, _key: str) -> bool:
    return True


async def _reads_thread(session: Session, thread_id: str) -> bool:
    # A missing thread is denied too: some thread ids are derived (Slack threads),
    # so a reader could otherwise subscribe before a private thread exists.
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except NotFoundError:
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "Authorizing a thread invalidation topic failed",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
        return False
    return thread_is_readable(thread_metadata(thread), session.get("sub"), session.get("email"))


_AUTHORIZERS: dict[str, Authorizer] = {
    WORKSPACES: _signed_in,
    THREAD: _reads_thread,
}


def thread_topic(thread_id: str) -> str:
    return f"{THREAD}/{thread_id}"


def valid(topic: str) -> bool:
    return len(topic) <= MAX_TOPIC_LENGTH and TOPIC_PATTERN.fullmatch(topic) is not None


async def may_hear(session: Session, topic: str) -> bool:
    kind, _, key = topic.partition("/")
    authorizer = _AUTHORIZERS.get(kind)
    return authorizer is not None and await authorizer(session, key)
