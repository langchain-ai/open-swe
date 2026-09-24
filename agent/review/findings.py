"""Findings storage for the reviewer agent.

Findings live in PostgreSQL under the pull request they were raised on, and the
human replies on their GitHub threads link to registered users by login.
Callers address them by reviewer thread; thread-level reviewer state (PR
identity, head SHA, watch flag) stays in LangGraph thread metadata. This file
owns the Finding schema and the read/write helpers that the reviewer's tools
and webhook handlers go through.

Threads reviewed before findings moved to PostgreSQL still hold them in thread
metadata. The first access to such a thread copies them over once; a
``pull_request_finding_state`` row marks a pull request whose findings live
here and names its reviewer thread.
"""

import asyncio
import copy
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal, Self, TypedDict, cast
from uuid import UUID, uuid7

from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError as LangGraphSDKNotFoundError
from pydantic import BaseModel, ValidationError
from sqlalchemy import BigInteger, ForeignKey, ForeignKeyConstraint, Select, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.run_config import RunConfig
from agent.users.models import UserIdentity

logger = logging.getLogger(__name__)


class ReviewerThreadMissingError(RuntimeError):
    """The reviewer thread backing findings storage does not exist.

    Raised instead of the SDK's ``NotFoundError`` so tool wrappers can return a
    structured do-not-retry result: the thread won't appear on retry (evicted,
    eval-mode, or never created), and blind retries burn the whole run.
    """

    def __init__(self, thread_id: str, original: Exception) -> None:
        super().__init__(f"Reviewer thread {thread_id!r} not found: {original}")
        self.thread_id = thread_id


REVIEWER_THREAD_KIND = "reviewer"
REVIEWER_EVAL_PUBLICATION_KEY = "reviewer_eval_publication"
# Sidebar label for reviewer threads that have no PR identity yet. Real PR
# titles land in ``pr`` metadata from the first webhook that reaches them.
REVIEWER_UNTITLED = "Review: pending"

# Suggestions are only useful when the reader can scan them at a glance and
# accept with one click. Anything longer reads as the reviewer rewriting the
# code for the author and clutters the comment. We cap at 4 lines and drop
# longer suggestions; the description still gets posted on its own.
MAX_SUGGESTION_LINES = 4
MAX_FINDING_TITLE_LENGTH = 120
DEFAULT_FINDING_TITLE = "Code review finding"
REVIEW_FINDING_CAP = 6
FINDING_FINGERPRINT_VERSION = 1


def clip_suggestion(suggestion: str | None) -> tuple[str | None, bool]:
    """Return (suggestion_or_none, was_dropped). Drops if over the line cap."""
    if not suggestion:
        return suggestion, False
    if suggestion.count("\n") + 1 > MAX_SUGGESTION_LINES:
        return None, True
    return suggestion, False


def normalize_finding_title(title: str | None, description: str = "") -> str:
    """Return a compact finding title suitable for a review comment headline."""
    raw = title.strip() if isinstance(title, str) else ""
    if not raw and description:
        raw = description.strip().split("\n", 1)[0].strip()
    compact = " ".join(raw.split())
    if not compact:
        return DEFAULT_FINDING_TITLE
    if len(compact) > MAX_FINDING_TITLE_LENGTH:
        return f"{compact[: MAX_FINDING_TITLE_LENGTH - 3].rstrip()}..."
    return compact


Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
FindingStatus = Literal["open", "resolved", "dismissed"]
DiffSide = Literal["LEFT", "RIGHT"]
SurfaceState = Literal["not_surfaced", "surfaced", "resolve_pending", "resolved"]
InteractionKind = Literal["human_reply", "bot_reply"]

SEVERITY_ORDER: dict[Severity, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Surface states only ever move forward, so normalization of a legacy record can
# reconcile contradictory leftovers by taking the furthest-along state.
SURFACE_STATE_ORDER: dict[SurfaceState, int] = {
    "not_surfaced": 0,
    "surfaced": 1,
    "resolve_pending": 2,
    "resolved": 3,
}

# Confidence is recorded on every finding for post-hoc calibration analysis
# but does not gate publication — the system prompt's defensibility bar is
# the discipline.


class Finding(TypedDict):
    """A single review finding.

    Where a finding surfaced on GitHub is recorded exactly once, by the
    ``github_*_ids`` lists plus ``surface_state``. Records persisted by older
    revisions carry flat singulars and a nested ``surface`` record instead;
    :func:`coerce_finding` folds those into the canonical fields on read, so
    nothing outside this module ever sees the legacy shape.
    """

    id: str
    severity: Severity
    confidence: Confidence
    category: str
    title: str
    file: str
    start_line: int | None
    end_line: int | None
    side: DiffSide
    in_diff: bool
    description: str
    suggestion: str | None
    status: FindingStatus
    first_seen_sha: str
    last_confirmed_sha: str
    github_review_id: int | None
    github_review_run_id: str | None
    github_review_comment_ids: list[int]
    github_review_thread_ids: list[str]
    github_resolved_thread_ids: list[str]
    github_posted_resolution_comment_ids: list[int]
    surface_state: SurfaceState
    last_human_reply_at: str | None
    last_human_reply_author: str | None
    last_human_reply_body: str | None
    last_reconciliation_note: str | None
    resolution_note: str | None
    diff_hunk: str | None
    fingerprint: str
    interactions: list[FindingInteraction]
    rank: int | None


class AppendFindingResult(TypedDict):
    finding: Finding
    created: bool


class FindingInteraction(TypedDict, total=False):
    kind: InteractionKind
    github_comment_id: int | None
    github_parent_comment_id: int | None
    author: str
    body: str
    created_at: str
    needs_reassessment: bool


class ReviewerPRMeta(TypedDict, total=False):
    """PR identity stored on reviewer thread metadata, used by the UI."""

    owner: str
    name: str
    number: int
    url: str
    title: str
    head_ref: str
    base_ref: str
    author: str


class ReviewerSlackThread(TypedDict, total=False):
    """Slack thread that initiated this review — used to post a completion reply."""

    channel_id: str
    thread_ts: str


class ReviewerEvalPublication(TypedDict):
    finding_ids: list[str]
    severity_threshold: Severity
    cap: int


def new_finding_id() -> str:
    """Return a stable, short, URL-friendly finding id (``f_<hex>``)."""
    return f"f_{uuid.uuid4().hex[:10]}"


def new_finding(
    *,
    severity: Severity,
    category: str,
    file: str,
    start_line: int | None,
    end_line: int | None,
    description: str,
    sha: str,
    title: str | None = None,
    confidence: Confidence = "medium",
    side: DiffSide = "RIGHT",
    suggestion: str | None = None,
    diff_hunk: str | None = None,
    finding_id: str | None = None,
    in_diff: bool = True,
) -> Finding:
    """Construct a fully-populated ``Finding`` ready to persist."""
    resolved_id = finding_id or new_finding_id()
    finding: Finding = {
        "id": resolved_id,
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "title": normalize_finding_title(title, description),
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "side": side,
        "in_diff": in_diff,
        "description": description,
        "suggestion": suggestion,
        "status": "open",
        "first_seen_sha": sha,
        "last_confirmed_sha": sha,
        "github_review_id": None,
        "github_review_run_id": None,
        "github_review_comment_ids": [],
        "github_review_thread_ids": [],
        "github_resolved_thread_ids": [],
        "github_posted_resolution_comment_ids": [],
        "surface_state": "not_surfaced",
        "last_human_reply_at": None,
        "last_human_reply_author": None,
        "last_human_reply_body": None,
        "last_reconciliation_note": None,
        "resolution_note": None,
        "diff_hunk": diff_hunk,
        "fingerprint": _finding_fingerprint(file, side, start_line, end_line, description),
        "interactions": [],
        "rank": None,
    }
    return finding


def _finding_fingerprint(
    file: str,
    side: DiffSide,
    start_line: int | None,
    end_line: int | None,
    description: str,
) -> str:
    payload = {
        "version": FINDING_FINGERPRINT_VERSION,
        "file": file,
        "side": side,
        "start_line": start_line,
        "end_line": end_line,
        "description": " ".join(description.casefold().split()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"v{FINDING_FINGERPRINT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


# Read accessors take a plain mapping so callers holding a raw persisted record
# (dashboard serializers, usage rollups) can use them without a cast.
FindingLike = Mapping[str, Any]


def comment_ids_for_finding(finding: FindingLike) -> list[int]:
    """GitHub review comment ids this finding was posted as, oldest first."""
    return _int_list(finding.get("github_review_comment_ids"))


def thread_ids_for_finding(finding: FindingLike) -> list[str]:
    """GitHub review thread node ids this finding lives in, oldest first."""
    return _str_list(finding.get("github_review_thread_ids"))


def resolved_thread_ids_for_finding(finding: FindingLike) -> list[str]:
    return _str_list(finding.get("github_resolved_thread_ids"))


def posted_resolution_comment_ids_for_finding(finding: FindingLike) -> list[int]:
    return _int_list(finding.get("github_posted_resolution_comment_ids"))


def review_id_for_finding(finding: FindingLike) -> int | None:
    review_id = finding.get("github_review_id")
    return review_id if isinstance(review_id, int) and not isinstance(review_id, bool) else None


def surface_state_of(finding: FindingLike) -> SurfaceState:
    state = finding.get("surface_state")
    return state if state in SURFACE_STATE_ORDER else "not_surfaced"


def is_surfaced(finding: FindingLike) -> bool:
    """True once this finding has been posted to the PR."""
    return surface_state_of(finding) != "not_surfaced"


def is_thread_resolved(finding: FindingLike) -> bool:
    """True once every GitHub review thread for this finding is resolved."""
    return surface_state_of(finding) == "resolved"


def mark_surfaced(finding: Finding) -> bool:
    """Record that the finding is now on GitHub. Returns True when it changed."""
    if is_surfaced(finding):
        return False
    finding["surface_state"] = "surfaced"
    return True


def set_surface_state(finding: Finding, state: SurfaceState) -> bool:
    """Set the surface state outright. Returns True when it changed."""
    if surface_state_of(finding) == state:
        return False
    finding["surface_state"] = state
    return True


def _legacy_surface_state(record: dict[str, Any], surface: dict[str, Any]) -> SurfaceState:
    if record.get("github_thread_resolved") is True:
        return "resolved"
    if (
        _int_list(record.get("github_review_comment_ids"))
        or _str_list(record.get("github_review_thread_ids"))
        or isinstance(record.get("github_review_id"), int)
        or isinstance(surface.get("github_review_comment_id"), int)
        or isinstance(surface.get("github_review_thread_id"), str)
    ):
        return "surfaced"
    return "not_surfaced"


def _normalize_publication_identity(record: dict[str, Any]) -> None:
    """Fold legacy GitHub-identity fields into the canonical ones, in place.

    Records written before the identity fields were unified carry flat
    singulars (``github_review_comment_id``, …), a ``github_thread_resolved``
    flag and a nested ``surface`` record. Every read passes through here, so
    the rest of the codebase — and every subsequent write — only ever deals
    with the lists plus ``surface_state``.
    """
    surface = record.pop("surface", None)
    surface = surface if isinstance(surface, dict) else {}
    record.pop("anchor", None)

    comment_ids = _int_list(record.get("github_review_comment_ids"))
    for legacy_comment_id in (
        record.pop("github_review_comment_id", None),
        surface.get("github_review_comment_id"),
    ):
        if isinstance(legacy_comment_id, int) and legacy_comment_id not in comment_ids:
            comment_ids.insert(0, legacy_comment_id)
    record["github_review_comment_ids"] = comment_ids

    thread_ids = _str_list(record.get("github_review_thread_ids"))
    for legacy_thread_id in (
        record.pop("github_review_thread_id", None),
        surface.get("github_review_thread_id"),
    ):
        if isinstance(legacy_thread_id, str) and legacy_thread_id not in ("", *thread_ids):
            thread_ids.insert(0, legacy_thread_id)
    record["github_review_thread_ids"] = thread_ids

    if not isinstance(record.get("github_review_id"), int):
        legacy_review_id = surface.get("github_review_id")
        record["github_review_id"] = legacy_review_id if isinstance(legacy_review_id, int) else None

    states: list[SurfaceState] = [_legacy_surface_state(record, surface)]
    for candidate in (record.get("surface_state"), surface.get("state")):
        if candidate in SURFACE_STATE_ORDER:
            states.append(cast(SurfaceState, candidate))
    record.pop("github_thread_resolved", None)
    record["surface_state"] = max(states, key=lambda state: SURFACE_STATE_ORDER[state])


def coerce_finding(value: Any) -> Finding | None:
    """Normalize one persisted record into a canonical ``Finding``.

    Returns ``None`` when the value isn't a finding record at all.
    """
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("id"), str):
        return None
    _normalize_publication_identity(value)
    return cast(Finding, value)


def coerce_findings(value: Any) -> list[Finding]:
    """Normalize a persisted findings blob into canonical ``Finding`` records."""
    if not isinstance(value, list):
        return []
    out: list[Finding] = []
    for entry in value:
        finding = coerce_finding(entry)
        if finding is not None:
            out.append(finding)
    return out


def get_thread_id_from_runtime() -> str:
    """Return the thread id from the current LangGraph runnable config."""
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        msg = "No thread_id available in runtime config"
        raise RuntimeError(msg)
    return thread_id


async def get_thread_metadata(thread_id: str) -> dict[str, Any]:
    """Fetch the current metadata for a thread.

    Raises :class:`ReviewerThreadMissingError` when the thread does not exist
    (swallowing it as ``{}`` made tools report misleading results like "No
    finding found" instead of the do-not-retry contract). Other transient
    failures still degrade to ``{}``.
    """
    try:
        return await _get_thread_metadata_strict(thread_id)
    except ReviewerThreadMissingError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch thread metadata for %s", thread_id)
        return {}


async def _get_thread_metadata_strict(thread_id: str) -> dict[str, Any]:
    client = get_client()
    try:
        thread = await client.threads.get(thread_id)
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return metadata if isinstance(metadata, dict) else {}


async def resolve_review_head_sha(thread_id: str, cfg: RunConfig) -> str:
    """Return the current PR head SHA for a reviewer run.

    A push that lands while a reviewer run is in flight is delivered as a queued
    message into that run, whose frozen ``configurable`` still names the head the
    run was created for. The dispatching webhook records the current head in
    thread metadata, so prefer that; fall back to the run's config when metadata
    carries no head (first review, eval, tests).
    """
    config_head = cfg.head_sha or ""
    if not thread_id:
        return config_head
    metadata = await get_thread_metadata(thread_id)
    meta_head = metadata.get("head_sha")
    return meta_head if isinstance(meta_head, str) and meta_head else config_head


class FindingState(Base):
    """A pull request whose findings live in PostgreSQL, and the reviewer thread that raised them."""

    __tablename__ = "pull_request_finding_state"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    reviewer_thread_id: Mapped[str]
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class InteractionRow(Base):
    __tablename__ = "pull_request_finding_interaction"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pull_request_id", "finding_id"],
            ["pull_request_finding.pull_request_id", "pull_request_finding.id"],
            ondelete="CASCADE",
        ),
    )

    position: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    pull_request_id: Mapped[UUID] = mapped_column(init=False)
    finding_id: Mapped[str] = mapped_column(init=False)
    kind: Mapped[str | None] = mapped_column(default=None)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    github_parent_comment_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author: Mapped[str | None] = mapped_column(default=None)
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    body: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str | None] = mapped_column(default=None)
    needs_reassessment: Mapped[bool | None] = mapped_column(default=None)

    @classmethod
    def of(cls, position: int, interaction: FindingInteraction) -> Self:
        row = cls(position=position)
        row.assign(interaction)
        return row

    def assign(self, interaction: FindingInteraction) -> None:
        values: Mapping[str, Any] = interaction
        for field in INTERACTION_FIELDS:
            setattr(self, field, values.get(field))
        self.author_user_id = None

    def to_interaction(self) -> FindingInteraction:
        return cast(
            FindingInteraction,
            {
                field: value
                for field in INTERACTION_FIELDS
                if (value := getattr(self, field)) is not None
            },
        )


class FindingRow(Base):
    __tablename__ = "pull_request_finding"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    position: Mapped[int]
    rank: Mapped[int | None]
    severity: Mapped[str]
    confidence: Mapped[str]
    category: Mapped[str]
    title: Mapped[str]
    file: Mapped[str]
    start_line: Mapped[int | None]
    end_line: Mapped[int | None]
    side: Mapped[str]
    in_diff: Mapped[bool]
    description: Mapped[str]
    suggestion: Mapped[str | None]
    status: Mapped[str]
    first_seen_sha: Mapped[str]
    last_confirmed_sha: Mapped[str]
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    github_review_run_id: Mapped[str | None]
    github_review_comment_ids: Mapped[list[int]] = mapped_column(JSONB)
    github_review_thread_ids: Mapped[list[str]] = mapped_column(JSONB)
    github_resolved_thread_ids: Mapped[list[str]] = mapped_column(JSONB)
    github_posted_resolution_comment_ids: Mapped[list[int]] = mapped_column(JSONB)
    surface_state: Mapped[str]
    last_human_reply_at: Mapped[str | None]
    last_human_reply_author: Mapped[str | None]
    last_human_reply_body: Mapped[str | None]
    last_reconciliation_note: Mapped[str | None]
    resolution_note: Mapped[str | None]
    diff_hunk: Mapped[str | None]
    fingerprint: Mapped[str]
    interaction_rows: Mapped[list[InteractionRow]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: InteractionRow.position,
    )

    @classmethod
    def of(cls, pull_request_id: UUID, position: int, finding: Finding) -> Self:
        complete = _complete_finding(finding)
        row = cls(
            pull_request_id=pull_request_id,
            position=position,
            **{field: complete[field] for field in COLUMN_FIELDS},
        )
        row.assign_interactions(complete["interactions"])
        return row

    def assign(self, finding: Finding) -> None:
        complete = _complete_finding(finding)
        for field in COLUMN_FIELDS:
            setattr(self, field, complete[field])
        self.assign_interactions(complete["interactions"])

    def assign_interactions(self, interactions: list[FindingInteraction]) -> None:
        """Update rows in place by position, so no two rows ever share one mid-flush."""
        for position, interaction in enumerate(interactions):
            if position < len(self.interaction_rows):
                row = self.interaction_rows[position]
                if row.to_interaction() != interaction:
                    row.assign(interaction)
            else:
                self.interaction_rows.append(InteractionRow.of(position, interaction))
        del self.interaction_rows[len(interactions) :]

    def to_finding(self) -> Finding:
        values = {field: copy.deepcopy(getattr(self, field)) for field in COLUMN_FIELDS}
        values["interactions"] = [row.to_interaction() for row in self.interaction_rows]
        return cast(Finding, values)


FINDING_FIELDS: tuple[str, ...] = tuple(Finding.__annotations__)
COLUMN_FIELDS: tuple[str, ...] = tuple(field for field in FINDING_FIELDS if field != "interactions")
INTERACTION_FIELDS: tuple[str, ...] = tuple(FindingInteraction.__annotations__)


class ReviewerThreadUnlinkedError(RuntimeError):
    """The reviewer thread's metadata names no pull request to store its findings under."""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"Reviewer thread {thread_id!r} has no pull request in its metadata")
        self.thread_id = thread_id


class _PullRequestRef(BaseModel):
    owner: str
    name: str
    number: int


def _complete_finding(finding: Mapping[str, Any]) -> dict[str, Any]:
    """Fill fields that records persisted by older revisions may lack."""
    base = new_finding(
        severity="low",
        category="",
        file=finding.get("file") or "",
        start_line=finding.get("start_line"),
        end_line=finding.get("end_line"),
        description=finding.get("description") or "",
        sha=finding.get("first_seen_sha") or finding.get("last_confirmed_sha") or "",
        title=finding.get("title"),
        side=finding.get("side") or "RIGHT",
        finding_id=finding["id"],
    )
    overrides = {
        field: finding[field]
        for field in FINDING_FIELDS
        if field in finding and finding[field] is not None
    }
    complete = {**base, **overrides}
    complete["interactions"] = [
        interaction for interaction in complete["interactions"] if isinstance(interaction, dict)
    ]
    return complete


async def _stored_pull_request_id(session: AsyncSession, thread_id: str) -> UUID | None:
    return await session.scalar(
        select(FindingState.pull_request_id).where(FindingState.reviewer_thread_id == thread_id)
    )


async def _ensure_finding_state(thread_id: str, metadata: Mapping[str, Any] | None = None) -> UUID:
    """The pull request a thread's findings are stored under, copying its metadata findings the first time.

    ``metadata`` is the thread's metadata when the caller already holds it.
    """
    from agent.github.pull_requests import PullRequest

    async with postgres.session() as session:
        if (pull_request_id := await _stored_pull_request_id(session, thread_id)) is not None:
            return pull_request_id
    if metadata is None:
        metadata = await _get_thread_metadata_strict(thread_id)
    try:
        ref = _PullRequestRef.model_validate(metadata.get("pr"))
    except ValidationError as exc:
        raise ReviewerThreadUnlinkedError(thread_id) from exc
    pull_request = await PullRequest(owner=ref.owner, repo=ref.name, number=ref.number).ensure()
    legacy = coerce_findings(metadata.get("findings"))
    async with postgres.session() as session:
        created = await session.scalar(
            insert(FindingState)
            .values(pull_request_id=pull_request.id, reviewer_thread_id=thread_id)
            .on_conflict_do_nothing()
            .returning(FindingState.pull_request_id)
        )
        if created is None:
            if (pull_request_id := await _stored_pull_request_id(session, thread_id)) is None:
                raise RuntimeError(
                    f"Pull request {pull_request.url} already stores findings from another reviewer thread"
                )
            return pull_request_id
        rows = [
            FindingRow.of(pull_request.id, position, finding)
            for position, finding in enumerate(legacy)
        ]
        session.add_all(rows)
        await _link_interaction_authors(session, rows)
    if legacy:
        logger.info(
            "Backfilled reviewer findings from thread metadata",
            extra={
                "reviewer_thread_id": thread_id,
                "pr_repo_full_name": pull_request.repo_full_name,
                "pr_number": pull_request.number,
                "finding_count": len(legacy),
            },
        )
    return pull_request.id


async def _link_interaction_authors(session: AsyncSession, rows: list[FindingRow]) -> None:
    """Point each unlinked interaction at the registered user with its author's GitHub login."""
    pending = [
        interaction
        for row in rows
        for interaction in row.interaction_rows
        if interaction.author and interaction.author_user_id is None
    ]
    logins = {interaction.author.lower() for interaction in pending if interaction.author}
    if not logins:
        return
    matches = await session.execute(
        select(func.lower(UserIdentity.login), UserIdentity.user_id)
        .where(UserIdentity.provider == "github", func.lower(UserIdentity.login).in_(logins))
        .order_by(UserIdentity.last_seen_at)
    )
    user_ids = dict(matches.tuples().all())
    for interaction in pending:
        if interaction.author:
            interaction.author_user_id = user_ids.get(interaction.author.lower())


def _rows_query(*pull_request_ids: UUID) -> Select[tuple[FindingRow]]:
    return (
        select(FindingRow)
        .where(FindingRow.pull_request_id.in_(pull_request_ids))
        .order_by(FindingRow.pull_request_id, FindingRow.position)
        .options(selectinload(FindingRow.interaction_rows))
    )


async def list_findings(thread_id: str) -> list[Finding]:
    """Return all findings recorded for the reviewer thread.

    Raises :class:`ReviewerThreadMissingError` when a thread not yet copied to
    PostgreSQL does not exist; other failures degrade to no findings.
    """
    try:
        pull_request_id = await _ensure_finding_state(thread_id)
        async with postgres.session() as session:
            return [row.to_finding() for row in await session.scalars(_rows_query(pull_request_id))]
    except ReviewerThreadMissingError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to load reviewer findings", extra={"reviewer_thread_id": thread_id}
        )
        return []


async def findings_by_thread(
    metadata_by_thread: Mapping[str, dict[str, Any]],
) -> dict[str, list[Finding]]:
    """Findings for many reviewer threads at once.

    Threads not yet copied to PostgreSQL are copied from the metadata passed in.
    A thread whose copy fails is logged and read from that metadata instead.
    """
    thread_ids = list(metadata_by_thread)
    if not thread_ids:
        return {}
    out = await _stored_findings(thread_ids)
    uncopied = [thread_id for thread_id in thread_ids if thread_id not in out]
    if uncopied:
        semaphore = asyncio.Semaphore(4)

        async def copy(thread_id: str) -> str | None:
            async with semaphore:
                try:
                    await _ensure_finding_state(thread_id, metadata_by_thread[thread_id])
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to copy reviewer findings from thread metadata",
                        extra={"reviewer_thread_id": thread_id},
                    )
                    return None
                return thread_id

        copied = [
            thread_id for thread_id in await asyncio.gather(*map(copy, uncopied)) if thread_id
        ]
        if copied:
            out.update(await _stored_findings(copied))
    for thread_id in thread_ids:
        if thread_id not in out:
            out[thread_id] = coerce_findings(metadata_by_thread[thread_id].get("findings"))
    return out


async def _stored_findings(thread_ids: list[str]) -> dict[str, list[Finding]]:
    """Findings of the threads among ``thread_ids`` whose findings live in PostgreSQL."""
    async with postgres.session() as session:
        states = await session.execute(
            select(FindingState.pull_request_id, FindingState.reviewer_thread_id).where(
                FindingState.reviewer_thread_id.in_(thread_ids)
            )
        )
        thread_by_pull_request = dict(states.tuples().all())
        out: dict[str, list[Finding]] = {
            thread_id: [] for thread_id in thread_by_pull_request.values()
        }
        if thread_by_pull_request:
            for row in await session.scalars(_rows_query(*thread_by_pull_request)):
                out[thread_by_pull_request[row.pull_request_id]].append(row.to_finding())
    return out


async def get_finding(thread_id: str, finding_id: str) -> Finding | None:
    """Return one finding by id, or ``None`` if not present."""
    findings = await list_findings(thread_id)
    for finding in findings:
        if finding.get("id") == finding_id:
            return finding
    return None


async def replace_findings(thread_id: str, findings: list[Finding]) -> None:
    """Merge a findings snapshot without dropping concurrently-added records."""

    def _merge(latest: list[Finding]) -> bool:
        incoming_by_id = {finding["id"]: finding for finding in findings}
        changed = False
        for index, finding in enumerate(latest):
            incoming = incoming_by_id.pop(finding["id"], None)
            if incoming is not None and incoming != finding:
                latest[index] = incoming
                changed = True
        latest.extend(incoming_by_id.values())
        return changed or bool(incoming_by_id)

    await mutate_findings(thread_id, _merge)


async def _record_finding_telemetry(thread_id: str, findings: list[Finding]) -> None:
    if not findings:
        return
    from agent.analytics.usage import record_reviewer_finding_state

    metadata = await get_thread_metadata(thread_id)
    pr: dict[str, Any] = metadata["pr"] if isinstance(metadata.get("pr"), dict) else {}
    results = await asyncio.gather(
        *(
            record_reviewer_finding_state(
                thread_id,
                {
                    **finding,
                    "pr": pr,
                    "owner": pr.get("owner"),
                    "repo": pr.get("name"),
                    "pr_number": pr.get("number"),
                },
            )
            for finding in findings
        ),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, Exception)]
    if failures:
        logger.debug("Failed to update reviewer usage telemetry: %s", failures[0])


def thread_missing_tool_result(exc: ReviewerThreadMissingError) -> dict[str, Any]:
    """Structured tool result for a missing reviewer thread.

    Returned (not raised) so the agent sees an explicit do-not-retry contract
    instead of an empty error blob it retries against.
    """
    return {
        "success": False,
        "error": "thread_not_found",
        "thread_id": exc.thread_id,
        "note": (
            "Reviewer findings storage is unavailable. Do not retry; report the "
            "blocker and include intended findings inline in the final message."
        ),
        "detail": str(exc),
    }


async def mutate_findings(
    thread_id: str,
    mutator: Callable[[list[Finding]], bool],
) -> list[Finding]:
    """Read the latest findings, apply ``mutator`` in place, persist iff changed.

    Centralizes the read-modify-write so every mutation operates on the freshest
    persisted list rather than a stale in-memory snapshot. ``mutator`` edits the
    list in place and returns ``True`` when it changed something; we only write
    on change, so a no-op mutation never clobbers a concurrent update.
    """
    pull_request_id = await _ensure_finding_state(thread_id)
    async with postgres.session() as session:
        await session.execute(
            select(FindingState)
            .where(FindingState.pull_request_id == pull_request_id)
            .with_for_update()
        )
        rows = list(await session.scalars(_rows_query(pull_request_id)))
        findings = [row.to_finding() for row in rows]
        before = {finding["id"]: copy.deepcopy(finding) for finding in findings}
        if not mutator(findings):
            return findings
        rows_by_id = {row.id: row for row in rows}
        next_position = max((row.position for row in rows), default=-1) + 1
        changed: list[Finding] = []
        written: list[FindingRow] = []
        for finding in findings:
            row = rows_by_id.pop(finding["id"], None)
            if row is None:
                row = FindingRow.of(pull_request_id, next_position, finding)
                session.add(row)
                next_position += 1
            elif before[finding["id"]] != finding:
                row.assign(finding)
            else:
                continue
            changed.append(finding)
            written.append(row)
        for removed in rows_by_id.values():
            await session.delete(removed)
        await _link_interaction_authors(session, written)
    await _record_finding_telemetry(thread_id, changed)
    return findings


def _current_fingerprint(finding: Finding) -> str:
    return _finding_fingerprint(
        finding["file"],
        finding.get("side", "RIGHT"),
        finding.get("start_line"),
        finding.get("end_line"),
        finding["description"],
    )


async def append_finding(thread_id: str, finding: Finding) -> AppendFindingResult:
    """Persist a finding once and return the canonical stored record."""
    captured: dict[str, Finding] = {}
    fingerprint = _current_fingerprint(finding)

    def _append(findings: list[Finding]) -> bool:
        for existing in findings:
            if existing.get("status", "open") != "open":
                continue
            if _current_fingerprint(existing) == fingerprint:
                captured["finding"] = existing
                return False
        findings.append(finding)
        captured["finding"] = finding
        return True

    await mutate_findings(thread_id, _append)
    persisted = captured["finding"]
    return {"finding": persisted, "created": persisted["id"] == finding["id"]}


async def update_finding_fields(
    thread_id: str,
    finding_id: str,
    updates: dict[str, Any],
) -> Finding | None:
    """Apply field updates to one finding by id and persist."""
    captured: dict[str, Finding] = {}

    def _apply(findings: list[Finding]) -> bool:
        for finding in findings:
            if finding.get("id") == finding_id:
                finding.update(cast(Finding, updates))
                captured["finding"] = finding
                return True
        return False

    await mutate_findings(thread_id, _apply)
    return captured.get("finding")


async def append_finding_interaction(
    thread_id: str,
    finding_id: str,
    interaction: FindingInteraction,
) -> Finding | None:
    """Persist a GitHub review-thread interaction on one finding."""
    captured: dict[str, Finding] = {}

    def _apply(findings: list[Finding]) -> bool:
        for finding in findings:
            if finding.get("id") != finding_id:
                continue
            captured["finding"] = finding
            interactions = finding.get("interactions")
            if not isinstance(interactions, list):
                interactions = []
            github_comment_id = interaction.get("github_comment_id")
            if isinstance(github_comment_id, int) and any(
                isinstance(item, dict) and item.get("github_comment_id") == github_comment_id
                for item in interactions
            ):
                return False
            interactions.append(interaction)
            finding["interactions"] = interactions
            return True
        return False

    await mutate_findings(thread_id, _apply)
    return captured.get("finding")


async def set_reviewer_thread_metadata(
    thread_id: str,
    *,
    pr: ReviewerPRMeta | None = None,
    last_reviewed_sha: str | None = None,
    head_sha: str | None = None,
    watch: bool | None = None,
    slack_thread: ReviewerSlackThread | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist reviewer-thread-level metadata.

    Always sets ``kind=reviewer`` so the future UI can list reviewer threads by
    filtering on metadata, and keeps the sidebar title ``Review: #nn <PR
    title>`` in step with the PR record. Only includes the fields the caller
    passed in (langgraph metadata updates merge rather than overwrite).

    ``head_sha`` records the current PR head the dispatching webhook is acting
    on. A push that lands mid-run is queued into the still-running run, whose
    frozen config can't be updated; persisting the head here lets the reviewer
    tools resolve the live head via ``resolve_review_head_sha``.
    """
    client = get_client()
    metadata: dict[str, Any] = {"kind": REVIEWER_THREAD_KIND}
    if pr is not None:
        metadata["pr"] = pr
        metadata["title"] = reviewer_thread_title(pr)
    if last_reviewed_sha is not None:
        metadata["last_reviewed_sha"] = last_reviewed_sha
    if head_sha is not None:
        metadata["head_sha"] = head_sha
    if watch is not None:
        metadata["watch"] = watch
    if slack_thread is not None:
        metadata["slack_thread"] = slack_thread
    if extra:
        metadata.update(extra)
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata)
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc


def get_thread_watch_flag(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("watch"))


def get_thread_last_reviewed_sha(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("last_reviewed_sha")
    return value if isinstance(value, str) and value else None


def get_thread_pr_meta(metadata: dict[str, Any]) -> ReviewerPRMeta | None:
    pr = metadata.get("pr")
    if not isinstance(pr, dict):
        return None
    return cast(ReviewerPRMeta, pr)


def reviewer_thread_title(pr: ReviewerPRMeta) -> str:
    """Sidebar title for a reviewer thread: ``Review: #nn <PR title>``."""
    number = pr.get("number")
    title = pr.get("title")
    number_part = f"#{number}" if isinstance(number, int) and not isinstance(number, bool) else ""
    title_part = title.strip() if isinstance(title, str) else ""
    label = f"Review: {number_part}".strip()
    if title_part:
        label = f"{label} {title_part}" if number_part or label.endswith(":") else title_part
    return label if label.strip() and label.strip() != "Review:" else REVIEWER_UNTITLED


def get_thread_slack_ref(metadata: dict[str, Any]) -> ReviewerSlackThread | None:
    slack_thread = metadata.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    if not channel_id or not thread_ts:
        return None
    return cast(ReviewerSlackThread, slack_thread)


def filter_findings_for_publish(
    findings: list[Finding],
    *,
    severity_threshold: Severity = "medium",
    cap: int | None = None,
) -> list[Finding]:
    """Return findings to surface to GitHub.

    - status must be ``open``
    - severity must be at or above ``severity_threshold``
    - sorted by the reviewer's ``rank``; unranked findings follow by severity
      descending, then file/start_line for stable ordering
    - optionally capped at ``cap`` for benchmark runs
    """
    severity_rank = SEVERITY_ORDER[severity_threshold]
    eligible = [
        finding
        for finding in findings
        if finding.get("status", "open") == "open"
        and SEVERITY_ORDER.get(finding.get("severity", "low"), 0) >= severity_rank
    ]
    eligible.sort(
        key=lambda f: (
            f.get("rank") is None,
            f.get("rank") or 0,
            -SEVERITY_ORDER.get(f.get("severity", "low"), 0),
            f.get("file", ""),
            f.get("start_line") or 0,
        )
    )
    return eligible[:cap]
