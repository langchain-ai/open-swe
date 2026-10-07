"""What the dashboard can be told went stale, and which session may hear each.

A ``Topic`` is invalidated as a whole; a ``KeyedTopic`` also has one topic per
record, ``<topic>/<key>``. An invalidation carries nothing but the topic, so
hearing one reveals only that something the session could already read has
changed; each topic still answers who may subscribe, because a key can name
something private.
"""

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agent.dashboard.admin import is_admin
from agent.github.pull_request_key import PullRequestKey
from agent.ui_invalidations import outbox

if TYPE_CHECKING:
    from agent.incidents.models import IncidentId
    from agent.review.styles import RepoFullName

logger = logging.getLogger(__name__)

type Session = dict[str, Any]
type Connection = AsyncConnection | AsyncSession
type KeysAuthorizer = Callable[[Session, set[str]], Awaitable[set[str]]]

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
    async def audible(cls, session: Session, topics: Iterable[str]) -> set[str]:
        """The ``topics`` ``session`` may hear, each kind authorizing all its keys at once."""
        keys_by_kind: dict[BaseTopic, set[str]] = {}
        for topic in topics:
            name, _, key = topic.partition("/")
            if name in cls._by_name:
                keys_by_kind.setdefault(cls._by_name[name], set()).add(key)
        allowed = await asyncio.gather(
            *(kind.authorize(session, keys) for kind, keys in keys_by_kind.items())
        )
        return {
            f"{kind}/{key}" if key else kind.name
            for kind, keys in zip(keys_by_kind, allowed, strict=True)
            for key in keys
        }

    @abstractmethod
    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        """Which of ``keys`` ``session`` may hear; the empty key is this topic itself."""

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

    PULL_REQUESTS: ClassVar[KeyedTopic[PullRequestKey]]
    """Keyed: one pull request's live state and review page, from its GitHub
    reviews, checks and comments to the reviewer's findings and the walkthrough."""

    async def invalidate(self, conn: Connection | None = None) -> None:
        """Mark this topic stale.

        With ``conn`` it takes effect when that transaction commits, so call it
        last there. Without, it follows a write Postgres had no part in, such as
        the Store, and never raises.
        """
        await self._publish(conn, self.name)

    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        return keys & {""}


class KeyedTopic[K: str](BaseTopic):
    """A topic whose records each have one of their own, ``<topic>/<key>``."""

    def __init__(self, name: str, *, authorize_keys: KeysAuthorizer | None = None) -> None:
        super().__init__(name)
        self._authorize_keys = authorize_keys

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

    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        records = keys - {""}
        if self._authorize_keys is None or not records:
            return keys
        return (keys & {""}) | await self._authorize_keys(session, records)


async def _readable_incidents(session: Session, incident_ids: set[str]) -> set[str]:
    # The incident service invalidates through this module.
    from agent.incidents import service
    from agent.incidents.models import IncidentId

    admin = is_admin(session.get("email"), login=session.get("sub"))
    ordered = sorted(incident_ids)
    visible = await asyncio.gather(
        *(service.visible(IncidentId(key), include_setup=admin) for key in ordered)
    )
    return {key for key, ok in zip(ordered, visible, strict=True) if ok}


async def _readable_pull_requests(session: Session, keys: set[str]) -> set[str]:
    # The repository listing reaches the Store, which imports this module.
    from agent.github.repos import accessible_repo_full_names

    try:
        accessible = await accessible_repo_full_names(session["sub"])
    except Exception:
        logger.warning("Could not list repositories for pull request invalidations", exc_info=True)
        return set()
    return {
        key
        for key in keys
        if (parsed := PullRequestKey.parse(key)) is not None
        and parsed == key
        and parsed.repo_full_name in accessible
    }


Topic.WORKSPACES = Topic("workspaces")
Topic.REVIEW_STYLES = KeyedTopic("review-styles")
Topic.INCIDENTS = KeyedTopic("incidents", authorize_keys=_readable_incidents)
Topic.INCIDENT_SETTINGS = Topic("incident-settings")
Topic.PULL_REQUESTS = KeyedTopic("pull-requests", authorize_keys=_readable_pull_requests)
