"""Each pull request's dashboard status, read from GitHub once per change.

The dashboard refetches a pull request's status whenever its UI topics fire, in every
open tab of everyone looking at it, and each read spends that viewer's own GitHub token
on a heavy GraphQL query and the check APIs: enough to trip GitHub's secondary rate
limit, which then stalls everything else the token does for a minute. A status read
after GitHub last reported a change to the pull request (``Topic.PULL_REQUEST_STATE``) is
still the current one, so it is served to anyone who can read the repository until GitHub
reports another; Robin's own records of the pull request, such as its reviewer's findings,
refresh its views many times a minute without changing what GitHub says.

Only the GraphQL read is rate limited that way, and only CI changes with no webhook
saying so (Robin drops CI deliveries), so a kept status is rechecked against its head
commit's CI over REST once ``_FRESH`` passes, and read in full again only when that CI
moved or ``_FULL_READ_FOR`` passed.

Every open tab refetches on the same change, and those requests land on different
replicas, so the first replica to read leaves a claim in the shared cache that the
others wait on (single-flight across replicas, against a cache stampede).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import ClassVar, Final, Self

from openswe.github.http import CommitCI, or_none
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.ui_invalidations.topics import Topic

logger = logging.getLogger(__name__)

# CI, which no webhook reports, shows within this.
_FRESH: Final = timedelta(seconds=60)
# What else GitHub works out on its own, such as mergeability once the base branch
# moves, shows within this.
_FULL_READ_FOR: Final = timedelta(minutes=5)
# How long another replica's read is waited on: reads take a few seconds, and one whose
# replica died must not hold anyone up for longer than this.
_CLAIM_FOR: Final = timedelta(seconds=12)
_POLL_SECONDS: Final = 0.25


@dataclass(frozen=True, slots=True)
class _Kept:
    """A shared status, when the full read that produced it began, and when its head
    commit's CI (``ci``, a fingerprint) was last found unchanged."""

    read_at: datetime
    checked_at: datetime
    ci: str | None
    status: OpenPullRequest

    def stored(self) -> dict[str, object]:
        return {
            "read_at": self.read_at.isoformat(),
            "checked_at": self.checked_at.isoformat(),
            "ci": self.ci,
            "status": self.status.model_dump(mode="json"),
        }

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        if (
            not isinstance(raw, Mapping)
            or not isinstance(read_at := raw.get("read_at"), str)
            or not isinstance(checked_at := raw.get("checked_at"), str)
        ):
            return None
        ci = raw.get("ci")
        return cls(
            datetime.fromisoformat(read_at),
            datetime.fromisoformat(checked_at),
            ci if isinstance(ci, str) else None,
            OpenPullRequest.model_validate(raw["status"]),
        )

    def current(self, changed_at: datetime | None) -> bool:
        """Read since the pull request's topics last fired."""
        return changed_at is None or self.read_at >= changed_at


@dataclass(frozen=True, slots=True)
class _Claim:
    """A replica's read in progress, or ``done`` once it has finished, shared or not."""

    started_at: datetime
    done: bool = False

    def stored(self) -> dict[str, object]:
        return {"started_at": self.started_at.isoformat(), "done": self.done}

    @classmethod
    def parse(cls, raw: object) -> Self | None:
        if not isinstance(raw, Mapping) or not isinstance(started := raw.get("started_at"), str):
            return None
        return cls(datetime.fromisoformat(started), raw.get("done") is True)

    def live(self, changed_at: datetime | None, now: datetime) -> bool:
        """Still running, begun since the last change, and not given up on."""
        return (
            not self.done
            and (changed_at is None or self.started_at >= changed_at)
            and now - self.started_at < _CLAIM_FOR
        )


@dataclass(frozen=True, slots=True)
class _Read:
    """A status, and whether every part of it was read, so it is worth handing to others."""

    status: OpenPullRequest | None
    complete: bool


@dataclass(frozen=True, slots=True)
class _Loading:
    started_at: datetime
    task: asyncio.Task[_Read]


class SharedPullRequestStatus:
    """The status ``PullRequestClient.load`` gives, shared by every replica and viewer.

    A complete read is kept until one of the pull request's topics fires after it began,
    or ``_FRESH`` passes and its head commit's CI changed, or ``_FULL_READ_FOR`` passes. Reads on one replica that ask while a current one is in flight
    wait for it rather than asking GitHub again, and a replica whose read would duplicate
    one another replica has claimed waits for that one's result. Callers check the viewer
    may read the repository first: a kept status was read with someone else's token.
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
        if kept is None or not kept.current(changed_at):
            return await cls._join(pull, key, changed_at, now, None)
        if now - kept.checked_at < _FRESH:
            return kept.status
        return await cls._join(pull, key, changed_at, now, kept)

    @classmethod
    async def _join(
        cls,
        pull: PullRequestClient,
        key: PullRequestKey,
        changed_at: datetime | None,
        now: datetime,
        kept: _Kept | None,
    ) -> OpenPullRequest | None:
        """Wait for a read begun since the last change, or begin one at ``now``; one that
        may recheck ``kept``, the current status CI may have moved on from."""
        flight = (key, id(asyncio.get_running_loop()))
        loading = cls._loading.get(flight)
        if loading is None or (changed_at is not None and loading.started_at < changed_at):
            task = asyncio.ensure_future(cls._read_once(pull, key, changed_at, now, kept))
            cls._loading[flight] = _Loading(now, task)
            task.add_done_callback(lambda _: cls._forget(flight, task))
            return (await asyncio.shield(task)).status
        try:
            read = await asyncio.shield(loading.task)
        except Exception:
            logger.debug("A shared status read failed", extra=cls._log_extra(key), exc_info=True)
        else:
            if read.complete:
                return read.status
        # Another viewer's read failed or came back partial, perhaps for their own token
        # or because their request went away; ours may do better, and is shared if so.
        return (await cls._load_now(pull, key)).status

    @classmethod
    async def _read_once(
        cls,
        pull: PullRequestClient,
        key: PullRequestKey,
        changed_at: datetime | None,
        now: datetime,
        kept: _Kept | None,
    ) -> _Read:
        """This replica's one read for the change: another replica's, or its own."""
        claim = await cls._live_claim(key, changed_at, now)
        if claim is None:
            return await cls._claimed(pull, key, now, kept)
        kept = await cls._wait_for(key, claim, changed_at, now)
        return _Read(kept.status, True) if kept is not None else await cls._load_now(pull, key)

    @classmethod
    async def _live_claim(
        cls, key: PullRequestKey, changed_at: datetime | None, now: datetime
    ) -> _Claim | None:
        """Another replica's read of the pull request since its last change, if one runs."""
        from langgraph_api.cache import cache_get

        try:
            claim = _Claim.parse(await cache_get(cls._claim_key(key)))
        except Exception:
            logger.warning(
                "Could not check for another replica's pull request status read",
                extra=cls._log_extra(key),
                exc_info=True,
            )
            return None
        return claim if claim is not None and claim.live(changed_at, now) else None

    @classmethod
    async def _wait_for(
        cls, key: PullRequestKey, claim: _Claim, changed_at: datetime | None, now: datetime
    ) -> _Kept | None:
        """``claim``'s result once it lands; ``None`` if it ends without one, or its time
        runs out first."""
        from langgraph_api.cache import cache_get

        loop = asyncio.get_running_loop()
        deadline = loop.time() + (claim.started_at + _CLAIM_FOR - now).total_seconds()
        try:
            while loop.time() < deadline:
                await asyncio.sleep(_POLL_SECONDS)
                # The claim before the status: a claimant shares its status before marking
                # the claim done, so a done claim seen here has its status in view below.
                current = _Claim.parse(await cache_get(cls._claim_key(key)))
                kept = _Kept.parse(await cache_get(cls._cache_key(key)))
                if (
                    kept is not None
                    and kept.current(changed_at)
                    and kept.checked_at >= claim.started_at
                ):
                    return kept
                if current is None or current.done:
                    # It finished with nothing worth sharing, or its claim expired.
                    return None
        except Exception:
            logger.warning(
                "Could not wait on another replica's pull request status read",
                extra=cls._log_extra(key),
                exc_info=True,
            )
        return None

    @classmethod
    async def _claimed(
        cls,
        pull: PullRequestClient,
        key: PullRequestKey,
        started_at: datetime,
        kept: _Kept | None,
    ) -> _Read:
        """Read under a claim other replicas wait on, marked done however it ends."""
        from langgraph_api.cache import cache_get, cache_set

        claim = _Claim(started_at)
        try:
            await cache_set(cls._claim_key(key), claim.stored(), _CLAIM_FOR)
        except Exception:
            logger.warning(
                "Could not claim a pull request status read",
                extra=cls._log_extra(key),
                exc_info=True,
            )
        try:
            return await cls._load(pull, key, started_at, kept)
        finally:
            try:
                # Only ours: a newer claim's waiters keep waiting on it.
                if _Claim.parse(await cache_get(cls._claim_key(key))) == claim:
                    await cache_set(
                        cls._claim_key(key), _Claim(started_at, done=True).stored(), _CLAIM_FOR
                    )
            except Exception:
                logger.debug(
                    "Could not release a pull request status claim",
                    extra=cls._log_extra(key),
                    exc_info=True,
                )

    @classmethod
    async def _load_now(cls, pull: PullRequestClient, key: PullRequestKey) -> _Read:
        """A read stamped when it begins, not when its caller started waiting: the pull
        request may have changed meanwhile, and a stamp from before that is never served."""
        try:
            _, started_at = await Topic.last_changed(*cls._topics(pull, key))
        except Exception:
            logger.warning(
                "Could not stamp a shared pull request status read",
                extra=cls._log_extra(key),
                exc_info=True,
            )
            return _Read(await pull.load(), False)
        return await cls._load(pull, key, started_at)

    @classmethod
    def _forget(cls, flight: tuple[PullRequestKey, int], task: asyncio.Task[object]) -> None:
        loading = cls._loading.get(flight)
        if loading is not None and loading.task is task:
            del cls._loading[flight]

    @classmethod
    async def _load(
        cls,
        pull: PullRequestClient,
        key: PullRequestKey,
        started_at: datetime,
        kept: _Kept | None = None,
    ) -> _Read:
        if kept is not None and await cls._recheck(pull, key, kept, started_at):
            return _Read(kept.status, True)
        status, ci = await pull.load_with_ci()
        if status is None or not cls._complete(status, ci):
            return _Read(status, False)
        await cls._keep(key, _Kept(started_at, started_at, cls._fingerprint(ci), status))
        return _Read(status, True)

    @classmethod
    async def _recheck(
        cls, pull: PullRequestClient, key: PullRequestKey, kept: _Kept, started_at: datetime
    ) -> bool:
        """Whether ``kept`` is still current: its full read recent enough, and its head
        commit's CI, read over REST, as it was. That read spares the GraphQL one."""
        sha = kept.status.head_sha
        if (
            kept.ci is None
            or sha is None
            or kept.status.mergeable is None
            or started_at - kept.read_at >= _FULL_READ_FOR
        ):
            return False
        ci = await or_none(pull.repo.commit_ci_over_rest(sha))
        if ci is None or cls._fingerprint(ci) != kept.ci:
            return False
        await cls._keep(key, replace(kept, checked_at=started_at))
        return True

    @classmethod
    async def _keep(cls, key: PullRequestKey, kept: _Kept) -> None:
        from langgraph_api.cache import cache_set

        try:
            await cache_set(cls._cache_key(key), kept.stored(), _FULL_READ_FOR)
        except Exception:
            logger.warning(
                "Could not share a pull request status", extra=cls._log_extra(key), exc_info=True
            )

    @staticmethod
    def _fingerprint(ci: CommitCI | None) -> str | None:
        """What of ``ci`` a status shows, so two reads compare equal when CI didn't move."""
        if ci is None:
            return None
        runs = sorted(
            [str(run.get("name")), str(run.get("status")), str(run.get("conclusion"))]
            for run in ci.runs
        )
        statuses = sorted(
            [str(status.get("context")), str(status.get("state"))] for status in ci.statuses
        )
        return hashlib.sha256(json.dumps([runs, statuses]).encode()).hexdigest()

    @staticmethod
    def _complete(status: OpenPullRequest, ci: CommitCI | None) -> bool:
        """Whether every part of ``status`` was read, so it is worth handing to others.

        ``load`` degrades rather than fails: a sub-read GitHub refused leaves its part
        unknown, which one viewer's next refetch fixes but a shared copy would not. Its CI
        was read when ``ci`` came back, even if ``status.ci`` says ``"unknown"``: a
        cancelled or stale run leaves CI inconclusive, and that is an answer to share.
        """
        return (
            status.status_available
            and ci is not None
            and status.reviewers is not None
            and status.unresolved_threads is not None
        )

    @staticmethod
    def _topics(pull: PullRequestClient, key: PullRequestKey) -> tuple[str, ...]:
        return (
            Topic.PULL_REQUEST_STATE.keyed(key),
            Topic.PULL_REQUEST_CHECKS.keyed(key),
            Topic.REQUIRED_CHECKS.keyed(pull.repo.full_name.lower()),
        )

    @staticmethod
    def _cache_key(key: PullRequestKey) -> str:
        return f"openswe:pull-request-status:v3:{key}"

    @staticmethod
    def _claim_key(key: PullRequestKey) -> str:
        return f"openswe:pull-request-status:v3:claim:{key}"

    @staticmethod
    def _log_extra(key: PullRequestKey) -> dict[str, object]:
        return {"pull_request_key": str(key)}
