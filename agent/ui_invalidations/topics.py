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

from fastapi import HTTPException

from agent.dashboard import repo_access

logger = logging.getLogger(__name__)

type Session = dict[str, Any]
type Authorizer = Callable[[Session, str], Awaitable[bool]]

WORKSPACES = "workspaces"
"""Any workspace's definition, bindings, settings, snapshot or refresh state."""

PULL_REQUEST = "pull-request"
"""One mirrored pull request's details, files or check runs."""

TOPIC_PATTERN = re.compile(r"^[a-z][a-z-]*(?:/[A-Za-z0-9._:@-]+)*$")
MAX_TOPIC_LENGTH = 200


def pull_request_topic(owner: str, repo: str, number: int) -> str:
    return f"{PULL_REQUEST}/{owner.lower()}/{repo.lower()}/{number}"


async def _signed_in(_session: Session, _key: str) -> bool:
    return True


async def _may_read_repository(session: Session, key: str) -> bool:
    owner, _, rest = key.partition("/")
    repo, _, number = rest.partition("/")
    if not owner or not repo or not number.isdigit():
        return False
    try:
        await repo_access.require_repo_access_for_user(str(session["sub"]), f"{owner}/{repo}")
    except HTTPException:
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "Checking repository access for a UI topic failed",
            extra={"repository": f"{owner}/{repo}"},
            exc_info=True,
        )
        return False
    return True


_AUTHORIZERS: dict[str, Authorizer] = {
    WORKSPACES: _signed_in,
    PULL_REQUEST: _may_read_repository,
}


def valid(topic: str) -> bool:
    return len(topic) <= MAX_TOPIC_LENGTH and TOPIC_PATTERN.fullmatch(topic) is not None


async def may_hear(session: Session, topic: str) -> bool:
    kind, _, key = topic.partition("/")
    authorizer = _AUTHORIZERS.get(kind)
    return authorizer is not None and await authorizer(session, key)
