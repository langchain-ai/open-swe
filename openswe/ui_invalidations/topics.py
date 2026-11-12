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

from openswe.github.pull_request_key import PullRequestKey
from openswe.ui_invalidations import outbox

if TYPE_CHECKING:
    from openswe.github.repo_names import RepoFullName

logger = logging.getLogger(__name__)

type Session = dict[str, Any]
type Connection = AsyncConnection | AsyncSession
type KeysAuthorizer = Callable[[Session, set[str]], Awaitable[set[str]]]

# GitHub deliveries that name a pull request only through its checks.
_CHECK_EVENTS = frozenset({"check_run", "check_suite", "workflow_run"})
_CHECKS_MIN_INTERVAL_SECONDS = 15.0

# Keys carry GitHub names, and a repository's may hold "_".
_WELL_FORMED = re.compile(r"^[a-z][a-z-]*(?:/[\w.:@-]+)*$", re.ASCII)
_MAX_LENGTH = 200


class BaseTopic(ABC):
    _by_name: ClassVar[dict[str, BaseTopic]] = {}

    def __init__(self, name: str, *, min_interval_seconds: float = 0.0) -> None:
        self.name = name
        self.min_interval_seconds = min_interval_seconds
        BaseTopic._by_name[name] = self

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    @classmethod
    def min_interval(cls, topic: str) -> float:
        """How long a stream waits after sending ``topic`` before sending it again."""
        kind = cls._by_name.get(topic.partition("/")[0])
        return kind.min_interval_seconds if kind is not None else 0.0

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

    INCIDENT_SETTINGS: ClassVar[Topic]
    """The incident channel policy."""

    REQUIRED_CHECKS: ClassVar[KeyedTopic[str]]

    PULL_REQUESTS: ClassVar[KeyedTopic[PullRequestKey]]
    """Keyed: one pull request's live state and review page, from its GitHub
    reviews and comments to the reviewer's findings and the walkthrough."""

    PULL_REQUEST_CHECKS: ClassVar[KeyedTopic[PullRequestKey]]
    """Keyed: one pull request's check runs, check suites and workflow runs. A CI
    run changes them many times a second, so a stream sends this at most every 15s."""

    THREAD_QUEUES: ClassVar[KeyedTopic[str]]
    """Keyed by thread id: the messages waiting in that thread's queue for its agent."""

    THREAD_BROWSERS: ClassVar[KeyedTopic[str]]
    """Keyed by thread id: the browser window that thread's agent opened, and who controls it."""
    THREAD_BACKGROUND_WORK: ClassVar[KeyedTopic[str]]
    """Keyed by thread id: the sandbox commands and monitors running for that thread."""

    TASK_WORKERS: ClassVar[KeyedTopic[str]]
    """Keyed by coordinator thread id: the workers its task delegated to, as the
    sidebar nests them beneath it."""

    ANNOUNCEMENTS: ClassVar[Topic]
    """Admin announcements and their delivery progress; admins only."""

    NOTIFICATIONS: ClassVar[KeyedTopic[str]]

    SLACK_CHANNELS: ClassVar[Topic]
    """The Slack channel directory, after each walk over Slack."""

    PLUGIN_CHAT_CALLS: ClassVar[KeyedTopic[str]]
    """Keyed by thread id: the calls that thread's agent made to the tools of the plugin it chats beside."""

    APP_PREVIEWS: ClassVar[KeyedTopic[str]]
    """Keyed by thread id: the build of an app that thread's agent is previewing."""

    PAGE_COMMANDS: ClassVar[KeyedTopic[str]]
    """Keyed by ``<owner users.id>/<page session>``: the commands Robin queued for one
    browser tab's page, and which of them it has carried out."""

    PLUGIN_DOCUMENTS: ClassVar[KeyedTopic[str]]
    """Keyed: one plugin's collection in one scope, ``<digest>/<owner>/<repo>`` for documents
    shared through a repository and ``<digest>/user/<user id>`` for one person's, the digest
    folding the plugin and collection together (``DocumentScope.topic_key``)."""

    async def invalidate(self, conn: Connection | None = None) -> None:
        """Mark this topic stale.

        With ``conn`` it takes effect when that transaction commits, so call it
        last there. Without, it follows a write Postgres had no part in, such as
        one to the Agent Server, and never raises.
        """
        await self._publish(conn, self.name)

    @staticmethod
    def for_pull_request_event(github_event: str) -> KeyedTopic[PullRequestKey]:
        """The topic a GitHub delivery about a pull request makes stale."""
        return Topic.PULL_REQUEST_CHECKS if github_event in _CHECK_EVENTS else Topic.PULL_REQUESTS

    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        return keys & {""}


class AdminTopic(Topic):
    """Data only admins may read, invalidated as a whole."""

    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        from openswe.dashboard.deps import session_is_admin

        admin = session_is_admin(session)
        return keys & {""} if admin else set()


class KeyedTopic[K: str](BaseTopic):
    """A topic whose records each have one of their own, ``<topic>/<key>``."""

    def __init__(
        self,
        name: str,
        *,
        authorize_keys: KeysAuthorizer | None = None,
        min_interval_seconds: float = 0.0,
    ) -> None:
        super().__init__(name, min_interval_seconds=min_interval_seconds)
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


async def _readable_required_checks(session: Session, keys: set[str]) -> set[str]:
    from openswe.github.repos import accessible_repo_full_names

    try:
        return keys & set(await accessible_repo_full_names(session["sub"]))
    except Exception:
        logger.warning("Could not authorize required-check invalidations", exc_info=True)
        return set()


async def _readable_pull_requests(session: Session, keys: set[str]) -> set[str]:
    # Imported on use: most of the app imports this module, which stays light.
    from openswe.github.repos import accessible_repo_full_names

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


async def _readable_plugin_documents(session: Session, keys: set[str]) -> set[str]:
    # Imported on use: most of the app imports this module, which stays light.
    from openswe.dashboard.oauth import session_user_id
    from openswe.users.models import User

    # A key is "<digest>/<scope>"; the scope is "user/<id>" or "owner/repo".
    scopes = {key: parts[1] for key in keys if len(parts := key.split("/", 1)) == 2}
    user = await User.for_session(session_user_id(session), session["sub"]) if scopes else None
    if user is None:
        return set()
    readable = {key for key, scope in scopes.items() if scope == f"user/{user.id}"}
    repositories = sorted({s for s in scopes.values() if not s.startswith("user/")})
    try:
        allowed = await asyncio.gather(*(user.can_read_repository(repo) for repo in repositories))
    except Exception:
        logger.warning("Could not check repositories for plugin invalidations", exc_info=True)
        return readable
    visible = {repo for repo, ok in zip(repositories, allowed, strict=True) if ok}
    return readable | {key for key, scope in scopes.items() if scope in visible}


async def _readable_threads(session: Session, thread_ids: set[str]) -> set[str]:
    # Thread summaries import this module.
    from openswe.threads.summary import thread_is_readable
    from openswe.utils.json_types import thread_metadata
    from openswe.utils.thread_ops import langgraph_client

    client = langgraph_client()
    ordered = sorted(thread_ids)
    threads = await asyncio.gather(
        *(client.threads.get(thread_id) for thread_id in ordered), return_exceptions=True
    )
    readable: set[str] = set()
    for thread_id, thread in zip(ordered, threads, strict=True):
        if isinstance(thread, BaseException):
            logger.warning(
                "Could not read thread for queue invalidations",
                exc_info=thread,
                extra={"agent_thread_id": thread_id},
            )
            continue
        if thread_is_readable(thread_metadata(thread), session.get("sub"), session.get("email")):
            readable.add(thread_id)
    return readable


Topic.WORKSPACES = Topic("workspaces")
Topic.REVIEW_STYLES = KeyedTopic("review-styles")
Topic.INCIDENT_SETTINGS = Topic("incident-settings")
Topic.REQUIRED_CHECKS = KeyedTopic("required-checks", authorize_keys=_readable_required_checks)
Topic.PULL_REQUESTS = KeyedTopic("pull-requests", authorize_keys=_readable_pull_requests)
Topic.PULL_REQUEST_CHECKS = KeyedTopic(
    "pull-request-checks",
    authorize_keys=_readable_pull_requests,
    min_interval_seconds=_CHECKS_MIN_INTERVAL_SECONDS,
)
Topic.THREAD_QUEUES = KeyedTopic("thread-queues", authorize_keys=_readable_threads)
Topic.THREAD_BROWSERS = KeyedTopic("thread-browsers", authorize_keys=_readable_threads)
Topic.THREAD_BACKGROUND_WORK = KeyedTopic(
    "thread-background-work", authorize_keys=_readable_threads
)
Topic.TASK_WORKERS = KeyedTopic("task-workers", authorize_keys=_readable_threads)
Topic.ANNOUNCEMENTS = AdminTopic("announcements")
Topic.SLACK_CHANNELS = Topic("slack-channels")
Topic.PLUGIN_DOCUMENTS = KeyedTopic("plugin-documents", authorize_keys=_readable_plugin_documents)
Topic.APP_PREVIEWS = KeyedTopic("app-previews", authorize_keys=_readable_threads)
Topic.PLUGIN_CHAT_CALLS = KeyedTopic("plugin-chat-calls", authorize_keys=_readable_threads)


class RecipientTopic(KeyedTopic[str]):
    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        from openswe.dashboard.oauth import session_user_id

        user_id = session_user_id(session)
        return keys & {str(user_id)} if user_id is not None else set()


Topic.NOTIFICATIONS = RecipientTopic("notifications")


class OwnedTopic(KeyedTopic[str]):
    """Keys that start with the ``users.id`` of the only person who may hear them."""

    async def authorize(self, session: Session, keys: set[str]) -> set[str]:
        from openswe.dashboard.oauth import session_user_id

        user_id = session_user_id(session)
        if user_id is None:
            return set()
        return {key for key in keys if key.partition("/")[0] == str(user_id)}


Topic.PAGE_COMMANDS = OwnedTopic("page-commands")
