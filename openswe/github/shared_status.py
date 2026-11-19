"""Each pull request's dashboard status, read from GitHub once per change.

The dashboard refetches a pull request's status whenever its UI topics fire, in every
open tab of everyone looking at it, and each read spends that viewer's own GitHub token
on a heavy GraphQL query and the check APIs: enough to trip GitHub's secondary rate
limit, which then stalls everything else the token does for a minute. A status read
after the pull request's topics last fired is still the current one, so it is served to
anyone who can read the repository until they fire again.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Final, Self

from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.ui_invalidations.topics import Topic

logger = logging.getLogger(__name__)

# What no webhook reports, such as GitHub settling mergeability, shows within this.
_FRESH: Final = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class _Kept:
    """A shared status and when the read that produced it began."""

    read_at: datetime
    status: OpenPullRequest

    def stored(self) -> dict[str, object]:
        return {"read_at": self.read_at.isoformat(), "status": self.status.model_dump(mode="json")}

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        if not isinstance(raw, Mapping) or not isinstance(read_at := raw.get("read_at"), str):
            return None
        return cls(datetime.fromisoformat(read_at), OpenPullRequest.model_validate(raw["status"]))


@dataclass(frozen=True, slots=True)
class _Loading:
    started_at: datetime
    task: asyncio.Task[OpenPullRequest | None]


class SharedPullRequestStatus:
    """The status ``PullRequestClient.load`` gives, shared by every replica and viewer.

    A complete read is kept until one of the pull request's topics fires after it began,
    or ``_FRESH`` passes. Reads on one replica that ask while a current one is in flight
    wait for it rather than asking GitHub again. Callers check the viewer may read the
    repository first: a kept status was read with someone else's token.
    """

    _loading: ClassVar[dict[tuple[PullRequestKey, int], _Loading]] = {}

    @classmethod
    async def read(cls, pull: PullRequestClient) -> OpenPullRequest | None:
        from langgraph_api.cache import cache_get

        key = PullRequestKey.of(pull.repo.owner, pull.repo.name, pull.number)
        try:
            changed_at, now = await Topic.last_changed(*cls._topics(pull, key))
            kept = _Kept.parse(await cache_get(cls._cache_key(key)))
        except Exception:
            logger.warning(
                "Could not consult the shared pull request status",
                extra=cls._log_extra(key),
                exc_info=True,
            )
            return await pull.load()
        if kept is not None and (changed_at is None or kept.read_at >= changed_at):
            return kept.status
        return await cls._join(pull, key, changed_at, now)

    @classmethod
    async def _join(
        cls,
        pull: PullRequestClient,
        key: PullRequestKey,
        changed_at: datetime | None,
        now: datetime,
    ) -> OpenPullRequest | None:
        """Wait for a read begun since the last change, or begin one at ``now``."""
        flight = (key, id(asyncio.get_running_loop()))
        loading = cls._loading.get(flight)
        if loading is None or (changed_at is not None and loading.started_at < changed_at):
            task = asyncio.ensure_future(cls._load(pull, key, now))
            cls._loading[flight] = _Loading(now, task)
            task.add_done_callback(lambda _: cls._forget(flight, task))
            return await asyncio.shield(task)
        try:
            status = await asyncio.shield(loading.task)
        except Exception:
            logger.debug("A shared status read failed", extra=cls._log_extra(key), exc_info=True)
        else:
            if cls._shareable(status):
                return status
        # Another viewer's read failed or came back partial, perhaps for their own token
        # or because their request went away; ours may do better, and is shared if so.
        # It begins now, not when we started waiting: the pull request may have changed
        # meanwhile, and a stamp from before that would never be served.
        try:
            _, started_at = await Topic.last_changed(*cls._topics(pull, key))
        except Exception:
            logger.warning(
                "Could not stamp a shared pull request status read",
                extra=cls._log_extra(key),
                exc_info=True,
            )
            return await pull.load()
        return await cls._load(pull, key, started_at)

    @classmethod
    def _forget(cls, flight: tuple[PullRequestKey, int], task: asyncio.Task[object]) -> None:
        loading = cls._loading.get(flight)
        if loading is not None and loading.task is task:
            del cls._loading[flight]

    @classmethod
    async def _load(
        cls, pull: PullRequestClient, key: PullRequestKey, started_at: datetime
    ) -> OpenPullRequest | None:
        from langgraph_api.cache import cache_set

        status = await pull.load()
        if status is None or not cls._shareable(status):
            return status
        try:
            await cache_set(cls._cache_key(key), _Kept(started_at, status).stored(), _FRESH)
        except Exception:
            logger.warning(
                "Could not share a pull request status", extra=cls._log_extra(key), exc_info=True
            )
        return status

    @staticmethod
    def _shareable(status: OpenPullRequest | None) -> bool:
        """Whether every part of ``status`` was read, so it is worth handing to others.

        ``load`` degrades rather than fails: a sub-read GitHub refused leaves its part
        unknown, which one viewer's next refetch fixes but a shared copy would not.
        """
        return (
            status is not None
            and status.status_available
            and status.ci != "unknown"
            and status.reviewers is not None
            and status.unresolved_threads is not None
        )

    @staticmethod
    def _topics(pull: PullRequestClient, key: PullRequestKey) -> tuple[str, ...]:
        return (
            Topic.PULL_REQUESTS.keyed(key),
            Topic.PULL_REQUEST_CHECKS.keyed(key),
            Topic.REQUIRED_CHECKS.keyed(pull.repo.full_name.lower()),
        )

    @staticmethod
    def _cache_key(key: PullRequestKey) -> str:
        return f"openswe:pull-request-status:v2:{key}"

    @staticmethod
    def _log_extra(key: PullRequestKey) -> dict[str, object]:
        return {"pull_request_key": str(key)}
