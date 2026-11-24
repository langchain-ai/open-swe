"""Each pull request's head commit, as a thread's status bar last read it.

The status bar reads a pull request's CI alongside its GraphQL status query, so it needs
the head commit before that query answers. GitHub gives an open pull request a new ETag
each time it recomputes mergeability, so asking ``GET pulls/N`` again is rarely a free
304. The head moves only on a push, which GitHub reports (``Topic.PULL_REQUEST_STATE``),
so a head read since the pull request last changed is still its head. A head that went
stale anyway, as on a repository that sends Robin no webhooks, costs only a second CI
read: the status bar compares it with the head its query saw.
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Self

from openswe.github.http import GitHubClient
from openswe.github.pull_request_key import PullRequestKey
from openswe.ui_invalidations.topics import Topic

logger = logging.getLogger(__name__)

# How long a head is trusted without a change reported, for repositories without webhooks.
_KEEP_FOR: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class KnownHead:
    """The head last read for a pull request if it is still current, and the stamp, by the
    database's clock, for a read that begins now."""

    sha: str | None
    now: datetime | None
    cache_key: str | None = None

    @classmethod
    async def of(cls, key: PullRequestKey, github: GitHubClient) -> Self:
        from langgraph_api.cache import cache_get

        authorization = github.http.headers.get("Authorization")
        if not authorization:
            return cls(None, None)
        scope = hashlib.sha256(authorization.encode()).hexdigest()
        cache_key = f"openswe:pull-request-head:v2:{scope}:{key}"
        try:
            changed_at, now = await Topic.last_changed(Topic.PULL_REQUEST_STATE.keyed(key))
            kept = await cache_get(cache_key)
        except Exception:
            logger.warning(
                "Could not look up the pull request's head",
                extra={"pull_request_key": str(key)},
                exc_info=True,
            )
            return cls(None, None)
        if (
            not isinstance(kept, Mapping)
            or not isinstance(sha := kept.get("sha"), str)
            or not isinstance(read_at := kept.get("read_at"), str)
            or (changed_at is not None and datetime.fromisoformat(read_at) < changed_at)
        ):
            return cls(None, now, cache_key)
        return cls(sha, now, cache_key)

    async def remember(self, sha: str) -> None:
        """Keep ``sha``, read by a read that began at ``self.now``. Never raises."""
        from langgraph_api.cache import cache_set

        if self.now is None or self.cache_key is None:
            return
        try:
            await cache_set(
                self.cache_key, {"sha": sha, "read_at": self.now.isoformat()}, _KEEP_FOR
            )
        except Exception:
            logger.warning("Could not keep the pull request's head", exc_info=True)
