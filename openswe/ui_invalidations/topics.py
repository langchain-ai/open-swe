"""What the dashboard can be told went stale, and which session may hear each.

A ``Topic`` is invalidated as a whole; a ``KeyedTopic`` also has one topic per
record, ``<topic>/<key>``. An invalidation carries nothing but the topic, so
hearing one reveals only that something the session could already read has
changed; each topic still answers who may subscribe, because a key can name
something private.
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from openswe.dashboard.admin import is_admin
from openswe.ui_invalidations import outbox

if TYPE_CHECKING:
    from openswe.incidents.models import IncidentId
    from openswe.review.styles import RepoFullName

logger = logging.getLogger(__name__)

type Session = dict[str, Any]
type Connection = AsyncConnection | AsyncSession
type KeyAuthorizer = Callable[[Session, str], Awaitable[bool]]

_WELL_FORMED = re.compile(r"^[a-z][a-z-]*(?:/[A-Za-z0-9._:@-]+)*$")
_MAX_LENGTH = 200


class BaseTopic(ABC):
    _by_name: ClassVar[dict[str, BaseTopic]] = {}

    def __init__(self, name: str) -> None:
        self.name = name
        BaseTopic._by_name[name] = self

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    @staticmethod
    def well_formed(topic: str) -> bool:
        return len(topic) <= _MAX_LENGTH and _WELL_FORMED.fullmatch(topic) is not None

    @classmethod
    async def may_hear(cls, session: Session, topic: str) -> bool:
        name, _, key = topic.partition("/")
        if name not in cls._by_name:
            return False
        return await cls._by_name[name].authorize(session, key)

    @abstractmethod
    async def authorize(self, session: Session, key: str) -> bool:
        """Whether ``session`` may hear this topic, or with ``key`` that record's."""

    @staticmethod
    async def _publish(conn: Connection | None, *topics: str) -> None:
        if conn is None:
            await outbox.invalidate_standalone(*topics)
        else:
            await outbox.invalidate(conn, *topics)


class Topic(BaseTopic):
    """Data any signed-in session may read, invalidated as a whole."""

    WORKSPACES: ClassVar[Topic]
    """Any workspace's definition, bindings, settings, snapshot or refresh state."""

    REVIEW_STYLES: ClassVar[KeyedTopic[RepoFullName]]
    """Any repository's review style, including its analysis status."""

    INCIDENTS: ClassVar[KeyedTopic[IncidentId]]
    """Any incident's listing entry. Keyed: that incident's detail and documents,
    including whether a run is investigating it."""

    INCIDENT_SETTINGS: ClassVar[Topic]
    """The incident policy, which decides which incidents anyone can see."""

    PULL_REQUESTS: ClassVar[KeyedTopic[str]]
    """Any mirrored pull request. Keyed by ``owner/repo/number``: that pull
    request's details, files or check runs."""

    async def invalidate(self, conn: Connection | None = None) -> None:
        """Mark this topic stale.

        With ``conn`` it takes effect when that transaction commits, so call it
        last there. Without, it follows a write Postgres had no part in, such as
        the Store, and never raises.
        """
        await self._publish(conn, self.name)

    async def authorize(self, session: Session, key: str) -> bool:
        return not key


async def _may_read_repository(session: Session, key: str) -> bool:
    # The Store invalidates through this module, and repository access reads the Store.
    from openswe.dashboard import repo_access

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


class KeyedTopic[K: str](BaseTopic):
    """A topic whose records each have one of their own, ``<topic>/<key>``."""

    def __init__(self, name: str, *, authorize_key: KeyAuthorizer | None = None) -> None:
        super().__init__(name)
        self._authorize_key = authorize_key

    async def invalidate(self, conn: Connection | None = None, *, key: K | None = None) -> None:
        """Mark this topic stale, and with ``key`` that one record's topic too.

        ``conn`` works as it does for ``Topic.invalidate``.
        """
        if key is None:
            await self._publish(conn, self.name)
        else:
            await self._publish(conn, self.name, self.keyed(key))

    def keyed(self, key: K) -> str:
        return f"{self.name}/{key}"

    async def authorize(self, session: Session, key: str) -> bool:
        if not key or self._authorize_key is None:
            return True
        return await self._authorize_key(session, key)


async def _may_read_incident(session: Session, incident_id: str) -> bool:
    # The incident service invalidates through this module.
    from openswe.incidents import service
    from openswe.incidents.models import IncidentId

    admin = is_admin(session.get("email"), login=session.get("sub"))
    return await service.visible(IncidentId(incident_id), include_setup=admin)


Topic.WORKSPACES = Topic("workspaces")
Topic.REVIEW_STYLES = KeyedTopic("review-styles")
Topic.INCIDENTS = KeyedTopic("incidents", authorize_key=_may_read_incident)
Topic.INCIDENT_SETTINGS = Topic("incident-settings")
Topic.PULL_REQUESTS = KeyedTopic("pull-request", authorize_key=_may_read_repository)
