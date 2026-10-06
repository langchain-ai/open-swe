"""Topic names, and which session may hear each one.

A topic is ``<kind>`` or ``<kind>/<key>``. An invalidation carries nothing but
the topic, so hearing one reveals only that something the session could already
read has changed; each kind still answers who may subscribe, because a key can name
something private.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

type Session = dict[str, Any]
type Authorizer = Callable[[Session, str], Awaitable[bool]]

WORKSPACES = "workspaces"
"""Any workspace's definition, bindings, settings, snapshot or refresh state."""

TOPIC_PATTERN = re.compile(r"^[a-z][a-z-]*(?:/[A-Za-z0-9._:@-]+)*$")
MAX_TOPIC_LENGTH = 200


async def _signed_in(_session: Session, _key: str) -> bool:
    return True


_AUTHORIZERS: dict[str, Authorizer] = {
    WORKSPACES: _signed_in,
}


def valid(topic: str) -> bool:
    return len(topic) <= MAX_TOPIC_LENGTH and TOPIC_PATTERN.fullmatch(topic) is not None


async def may_hear(session: Session, topic: str) -> bool:
    kind, _, key = topic.partition("/")
    authorizer = _AUTHORIZERS.get(kind)
    return authorizer is not None and await authorizer(session, key)
