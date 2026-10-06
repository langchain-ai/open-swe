"""What the dashboard can be told went stale, and which session may hear each.

A topic is a ``Topic`` or ``<topic>/<key>``. An invalidation carries nothing but
the topic, so hearing one reveals only that something the session could already
read has changed; each topic still answers who may subscribe, because a key can
name something private.
"""

import re
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agent.dashboard.admin import is_admin
from agent.ui_invalidations import outbox

type Session = dict[str, Any]

_WELL_FORMED = re.compile(r"^[a-z][a-z-]*(?:/[A-Za-z0-9._:@-]+)*$")
_MAX_LENGTH = 200


class Topic(StrEnum):
    WORKSPACES = "workspaces"
    """Any workspace's definition, bindings, settings, snapshot or refresh state."""

    REVIEW_STYLES = "review-styles"
    """Any repository's review style record, including its analysis status."""

    INCIDENTS = "incidents"
    """Any incident's listing entry. Keyed by incident id: that incident's detail
    and documents, including whether a run is investigating it."""

    INCIDENT_SETTINGS = "incident-settings"
    """The incident policy, which decides which incidents anyone can see."""

    async def invalidate(
        self, conn: AsyncConnection | AsyncSession | None = None, *, key: str | None = None
    ) -> None:
        """Mark this topic stale, and with ``key`` that one record under it too.

        With ``conn`` it takes effect when that transaction commits, so call it
        last there. Without, it follows a write Postgres had no part in, such as
        the Store, and never raises.
        """
        topics = [self] if key is None else [self, f"{self}/{key}"]
        if conn is None:
            await outbox.invalidate_standalone(*topics)
        else:
            await outbox.invalidate(conn, *topics)

    @staticmethod
    def well_formed(topic: str) -> bool:
        return len(topic) <= _MAX_LENGTH and _WELL_FORMED.fullmatch(topic) is not None

    @classmethod
    async def may_hear(cls, session: Session, topic: str) -> bool:
        name, _, key = topic.partition("/")
        try:
            kind = cls(name)
        except ValueError:
            return False
        return await kind._authorize(session, key)

    async def _authorize(self, session: Session, key: str) -> bool:
        match self:
            case Topic.WORKSPACES | Topic.REVIEW_STYLES | Topic.INCIDENT_SETTINGS:
                return True
            case Topic.INCIDENTS:
                if not key:
                    return True
                # The incident service invalidates through this module.
                from agent.incidents import service

                return await service.visible(key, include_setup=_is_admin(session))


def _is_admin(session: Session) -> bool:
    return is_admin(session.get("email"), login=session.get("sub"))
