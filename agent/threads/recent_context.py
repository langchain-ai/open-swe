"""A compact digest of a sender's recent threads for the system prompt.

The digest answers "what has this person been working on?" with metadata that
already exists: generated titles, repositories, and statuses. It is selected
under audience rules that keep even a title from leaking private work into a
shared conversation, and it is background data — never instructions.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal

from langgraph_sdk.client import LangGraphClient

from agent.prompts import render_prompt
from agent.source_context import SourceContext
from agent.threads.summary import (
    _is_automation_thread,
    _metadata_repo,
    _thread_id,
    _thread_metadata,
    _thread_updated_ms,
    thread_is_owner,
    thread_is_private,
    thread_is_unlisted,
    thread_source,
)
from agent.utils.json_types import ThreadLike
from agent.utils.thread_participants import participant_search_filters

logger = logging.getLogger(__name__)

RECENT_CONTEXT_THREAD_COUNT = 5
RECENT_CONTEXT_TITLE_MAX_CHARS = 160
RECENT_CONTEXT_PAYLOAD_MAX_CHARS = 4_000
RECENT_CONTEXT_TIMEOUT_SECONDS = 1.0
RECENT_CONTEXT_SCAN_CAP = 100
_UNTITLED = "Untitled thread"
type RecentContextAudience = Literal["private", "shared_slack"]
type EligibilityPredicate = Callable[[Mapping[str, object]], bool]


class RecentThreadContext:
    """One entry of the digest, as the prompt section renders it."""

    def __init__(
        self,
        *,
        thread_id: str,
        title: str,
        repo: str | None,
        source: str,
        resolved: bool | None,
        updated_at_ms: int | None,
    ) -> None:
        self.thread_id = thread_id
        self.title = title
        self.repo = repo
        self.source = source
        self.resolved = resolved
        self.updated_at_ms = updated_at_ms


def _clean_title(raw: object, repo: str | None, source: str) -> str:
    """A bounded, single-line title with a non-empty fallback."""
    text = (
        "".join(char if char.isprintable() and char not in "\r\n" else " " for char in raw)
        if isinstance(raw, str)
        else ""
    )
    collapsed = " ".join(text.split())
    if not collapsed:
        if repo:
            return f"{repo} ({source})"[:RECENT_CONTEXT_TITLE_MAX_CHARS]
        return _UNTITLED
    return collapsed[:RECENT_CONTEXT_TITLE_MAX_CHARS]


def _thread_resolved(metadata: Mapping[str, object]) -> bool | None:
    value = metadata.get("resolved")
    return value if isinstance(value, bool) else None


def _eligible_for_private_or_dm(metadata: Mapping[str, object], login: str | None) -> bool:
    """Private destination: the sender's own public and privately-owned threads."""
    if thread_is_private(metadata):
        return thread_is_owner(metadata, login)
    return True


def _eligible_for_shared_slack(
    metadata: Mapping[str, object],
    *,
    team_id: str,
    channel_id: str,
    login: str | None,
) -> bool:
    """Shared Slack channel: only this channel's public threads, same Slack team."""
    if thread_is_private(metadata) or not login:
        return False
    ref = SourceContext.from_metadata(metadata).slack_thread
    if ref is None:
        return False
    if not team_id or ref.team_id != team_id:
        return False
    location = ref.location
    return location is not None and location[0] == channel_id


def _excluded(metadata: Mapping[str, object]) -> bool:
    return (
        thread_is_unlisted(metadata)
        or metadata.get("admin_thread") is True
        or _is_automation_thread(metadata)
        or thread_source(metadata) == "incidents_agent"
    )


class RecentContextSelector:
    """Selects the sender's most recently updated eligible threads."""

    def __init__(
        self,
        client: LangGraphClient,
        *,
        audience: RecentContextAudience,
        login: str | None,
        email: str | None = None,
        exclude_thread_id: str | None = None,
        slack_team_id: str | None = None,
        slack_channel_id: str | None = None,
        scan_cap: int = RECENT_CONTEXT_SCAN_CAP,
    ) -> None:
        self._client = client
        self._audience = audience
        self._login = login
        self._email = email
        self._exclude_thread_id = exclude_thread_id
        self._slack_team_id = slack_team_id or ""
        self._slack_channel_id = slack_channel_id or ""
        self._scan_cap = max(scan_cap, RECENT_CONTEXT_THREAD_COUNT)
        if not login:
            raise ValueError("recent thread context requires a verified sender login")

    async def select(self) -> list[RecentThreadContext]:
        """Newest-first eligible threads, deduplicated by thread ID."""
        if self._audience == "shared_slack":
            return await self._select_shared_slack()
        return await self._select_private()

    async def _select_shared_slack(self) -> list[RecentThreadContext]:
        return await self._collect(self._shared_slack_eligible)

    async def _select_private(self) -> list[RecentThreadContext]:
        return await self._collect(self._private_eligible)

    async def _collect(self, eligible: EligibilityPredicate) -> list[RecentThreadContext]:
        selected: dict[str, RecentThreadContext] = {}
        login = self._login
        if not login:
            return []
        metadata_filters = participant_search_filters(login, self._email)
        filter_cap = max(self._scan_cap // len(metadata_filters), RECENT_CONTEXT_THREAD_COUNT)
        for metadata_filter in metadata_filters:
            offset = 0
            while offset < filter_cap:
                page_size = min(50, filter_cap - offset)
                batch = await self._client.threads.search(
                    metadata=metadata_filter,
                    limit=page_size,
                    offset=offset,
                    sort_by="updated_at",
                    sort_order="desc",
                    select=["thread_id", "status", "metadata", "created_at", "updated_at"],
                )
                threads = [thread for thread in batch or [] if isinstance(thread, Mapping)]
                if not threads:
                    break
                for thread in threads:
                    context = self._context_for(thread, eligible)
                    if context is not None:
                        selected.setdefault(context.thread_id, context)
                if len(threads) < page_size:
                    break
                offset += page_size
        return self._ordered(selected)

    def _context_for(
        self, thread: ThreadLike, eligible: EligibilityPredicate
    ) -> RecentThreadContext | None:
        metadata = _thread_metadata(thread)
        thread_id = _thread_id(thread)
        if (
            not thread_id
            or thread_id == self._exclude_thread_id
            or _excluded(metadata)
            or not eligible(metadata)
        ):
            return None
        _, _, full_name = _metadata_repo(metadata)
        updated_at = _thread_updated_ms(thread)
        return RecentThreadContext(
            thread_id=thread_id,
            title=_clean_title(metadata.get("title"), full_name or None, thread_source(metadata)),
            repo=full_name or None,
            source=thread_source(metadata),
            resolved=_thread_resolved(metadata),
            updated_at_ms=updated_at,
        )

    def _shared_slack_eligible(self, metadata: Mapping[str, object]) -> bool:
        return _eligible_for_shared_slack(
            metadata,
            team_id=self._slack_team_id,
            channel_id=self._slack_channel_id,
            login=self._login,
        )

    def _private_eligible(self, metadata: Mapping[str, object]) -> bool:
        return _eligible_for_private_or_dm(metadata, self._login)

    @staticmethod
    def _ordered(selected: dict[str, RecentThreadContext]) -> list[RecentThreadContext]:
        return sorted(
            selected.values(),
            key=lambda context: (context.updated_at_ms or 0, context.thread_id),
            reverse=True,
        )[:RECENT_CONTEXT_THREAD_COUNT]


def _format_ts(updated_at_ms: int | None) -> str:
    if not updated_at_ms:
        return "unknown"
    moment = datetime.fromtimestamp(updated_at_ms / 1000, tz=UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _render_entries(entries: list[RecentThreadContext]) -> str:
    lines: list[str] = []
    for index, entry in enumerate(entries, 1):
        state = {True: "resolved", False: "unresolved", None: "unknown"}[entry.resolved]
        lines.extend(
            [
                f"{index}. Topic: {entry.title}",
                f"   Repo: {entry.repo or 'unknown'} · Source: {entry.source}",
                f"   State: {state} · Last activity: {_format_ts(entry.updated_at_ms)}",
                f"   Thread: {entry.thread_id}",
            ]
        )
    return render_prompt("system/recent-thread-context.md", entries="\n".join(lines))


def render_recent_thread_context(entries: list[RecentThreadContext]) -> str:
    """The bounded prompt section, or an empty string when there is nothing to show."""
    if not entries:
        return ""
    section = _render_entries(entries)
    if len(section) <= RECENT_CONTEXT_PAYLOAD_MAX_CHARS:
        return section
    # Drop whole entries from the end until the section fits; never emit a
    # half-rendered one. Each entry costs at least its title length, so a linear
    # scan from the front is the clearest bound.
    for keep in range(len(entries) - 1, 0, -1):
        candidate = _render_entries(entries[:keep])
        if len(candidate) <= RECENT_CONTEXT_PAYLOAD_MAX_CHARS:
            return candidate
    return ""


async def recent_thread_context_section(
    *,
    audience: RecentContextAudience,
    login: str | None,
    email: str | None = None,
    exclude_thread_id: str | None = None,
    slack_team_id: str | None = None,
    slack_channel_id: str | None = None,
    client: LangGraphClient | None = None,
) -> str:
    """The prompt section for this sender, or "" when ineligible or unavailable."""
    if not login:
        return ""
    if client is None:
        from agent.utils.thread_ops import langgraph_client

        client = langgraph_client()
    started = time.monotonic()
    try:
        selector = RecentContextSelector(
            client,
            audience=audience,
            login=login,
            email=email,
            exclude_thread_id=exclude_thread_id,
            slack_team_id=slack_team_id,
            slack_channel_id=slack_channel_id,
        )
        entries = await asyncio.wait_for(
            selector.select(),
            timeout=RECENT_CONTEXT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Recent thread context lookup timed out",
            extra={
                "thread_id": exclude_thread_id or "",
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return ""
    except Exception:
        logger.warning("Recent thread context lookup failed", exc_info=True)
        return ""
    section = render_recent_thread_context(entries)
    logger.debug(
        "Recent thread context selected",
        extra={
            "thread_id": exclude_thread_id or "",
            "selected": len(entries),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return section
