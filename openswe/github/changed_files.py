"""Immutable pull-request file snapshots shared across callers and event loops."""

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from pydantic import JsonValue, TypeAdapter

type FileList = list[dict[str, JsonValue]]
type CacheKey = tuple[str, int, str, str]

logger = logging.getLogger(__name__)

_FILES = TypeAdapter(FileList)
_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PullRequestTips:
    """The base and head commits identifying a pull request's file list."""

    base_sha: str
    head_sha: str

    @classmethod
    async def of(cls, payload: object) -> PullRequestTips | None:
        """Read both tips from a REST pull-request payload."""
        if not isinstance(payload, dict):
            return None
        base, head = payload.get("base"), payload.get("head")
        base_sha = base.get("sha") if isinstance(base, dict) else None
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(base_sha, str) or not base_sha:
            return None
        if not isinstance(head_sha, str) or not head_sha:
            return None
        return cls(base_sha, head_sha)

    async def listed(self, files: Sequence[dict[str, JsonValue]]) -> bool:
        """Check that nonremoved files name this head in their contents URLs."""
        present = [file for file in files if file.get("status") != "removed"]
        return bool(present) and all(
            isinstance(url := file.get("contents_url"), str)
            and parse_qs(urlparse(url).query).get("ref") == [self.head_sha]
            for file in present
        )


@dataclass(frozen=True, slots=True)
class _Kept:
    payload: bytes
    max_pages: int | None
    expires_at: float

    async def files(self, max_pages: int | None) -> FileList:
        """Decode a private copy for each caller, limited to its requested pages."""
        files = _FILES.validate_json(self.payload)
        return files if max_pages is None else files[: max_pages * _PAGE_SIZE]


class ChangedFilesCache:
    """An LRU cache bounded by serialized payload bytes, excluding Python overhead."""

    def __init__(
        self,
        *,
        max_payload_bytes: int = 64 * 1024 * 1024,
        max_entry_payload_bytes: int = 8 * 1024 * 1024,
        ttl_seconds: float = 6 * 60 * 60,
    ) -> None:
        self.max_payload_bytes = max_payload_bytes
        self.max_entry_payload_bytes = max_entry_payload_bytes
        self.ttl_seconds = ttl_seconds
        self._kept: OrderedDict[CacheKey, _Kept] = OrderedDict()
        self._payload_bytes = 0
        self._inflight: dict[tuple[CacheKey, int | None], Future[_Kept]] = {}
        self._lock = threading.Lock()

    async def read(
        self,
        full_name: str,
        number: int,
        tips: PullRequestTips,
        max_pages: int | None,
        loader: Callable[[], Awaitable[FileList]],
        confirm: Callable[[], Awaitable[PullRequestTips | None]],
    ) -> FileList:
        """Reuse a snapshot or join an identical read already running on any event loop.

        A fresh list is kept only when ``confirm`` reads the same tips after it was
        listed: its ``contents_url`` proves the head, but nothing in it proves the base.
        """
        key = (full_name.lower(), number, tips.base_sha, tips.head_sha)
        read_key = (key, max_pages)
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None and kept.expires_at <= time.monotonic():
                self._payload_bytes -= len(self._kept.pop(key).payload)
                kept = None
            if kept is not None and kept.max_pages is not None:
                if max_pages is None or max_pages > kept.max_pages:
                    kept = None
            if kept is not None:
                self._kept.move_to_end(key)
            future = self._inflight.get(read_key)
            owner = kept is None and future is None
            if owner:
                future = Future()
                self._inflight[read_key] = future
        if kept is not None:
            return await kept.files(max_pages)
        assert future is not None
        if not owner:
            try:
                snapshot = await asyncio.shield(asyncio.wrap_future(future))
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                # The task that was reading for everyone was cancelled; read again.
                return await self.read(full_name, number, tips, max_pages, loader, confirm)
            except Exception:
                logger.debug(
                    "Shared file read failed; fetching with the caller's loader", exc_info=True
                )
                return await loader()
            return await snapshot.files(max_pages)
        try:
            files = await loader()
            snapshot = _Kept(
                _FILES.dump_json(files),
                None if max_pages is None or len(files) < max_pages * _PAGE_SIZE else max_pages,
                time.monotonic() + self.ttl_seconds,
            )
            if (
                len(snapshot.payload) <= self.max_entry_payload_bytes
                and await tips.listed(files)
                and await confirm() == tips
            ):
                with self._lock:
                    previous = self._kept.get(key)
                    if previous is None or (
                        previous.max_pages is not None
                        and (snapshot.max_pages is None or snapshot.max_pages >= previous.max_pages)
                    ):
                        if previous is not None:
                            self._payload_bytes -= len(self._kept.pop(key).payload)
                        self._kept[key] = snapshot
                        self._payload_bytes += len(snapshot.payload)
                        while self._payload_bytes > self.max_payload_bytes and self._kept:
                            _, evicted = self._kept.popitem(last=False)
                            self._payload_bytes -= len(evicted.payload)
            with self._lock:
                self._inflight.pop(read_key, None)
            future.set_result(snapshot)
            return files
        except BaseException as exc:
            # Leave first, so a waiter that reads again starts a new read rather than this one.
            with self._lock:
                self._inflight.pop(read_key, None)
            if not future.done():
                future.set_exception(exc)
            raise

    async def clear(self) -> None:
        """Discard retained snapshots."""
        with self._lock:
            self._kept.clear()
            self._payload_bytes = 0


CHANGED_FILES = ChangedFilesCache()
