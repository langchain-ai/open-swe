"""Declarative access policies for agent tools.

A tool declares where it may run with ``@access(Policy(...))``. The factory binds
only the tools the run's access permits, and every call is rechecked so a thread
that gains an unauthorized writer mid-run loses its shared-write permissions.
"""

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, ParamSpec, TypeVar, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from openswe.config import ENV
from openswe.credential_scope import private_owner_login
from openswe.memories.store import CURATOR_SOURCE
from openswe.run_config import RunConfig
from openswe.slack.client import SLACK_THREAD_MAX_MESSAGES, fetch_slack_thread_messages
from openswe.source_context import SourceContext
from openswe.tools.admin_gate import (
    actor_is_admin,
    configurable,
    is_private_admin_surface,
    participant_is_admin,
)
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_logins,
)
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R", bound=Mapping[str, object])

Place = Literal[
    "anywhere",
    "private",
    "admin_thread",
    "admin_surface",
    "channel_history",
    "thread_history",
    "curator",
]
Actor = Literal["anyone", "owner", "admin"]
Mode = Literal["full", "sole"]
Projection = Callable[[Mapping[str, object]], Mapping[str, object]]

_ACK_KEYS = ("ok", "success", "status", "error", "created", "deleted")
_IGNORED_SLACK_SUBTYPES = frozenset(
    {"channel_join", "channel_leave", "group_join", "group_leave", "message_deleted"}
)
_REFUSED = (
    "This tool is not available in this thread. Use a private dashboard thread or an "
    "authenticated Slack DM, where only you can write and read the results."
)
_WITHHELD = "The result was withheld because other people can read this thread."


def unchanged(result: Mapping[str, object]) -> Mapping[str, object]:
    """Projection for results that only echo what the caller supplied."""
    return result


def _pick(value: Mapping[str, object], keys: list[str]) -> dict[str, object]:
    head, rest = keys[0], keys[1:]
    if head not in value:
        return {}
    child = value[head]
    if not rest:
        return {head: child}
    picked = _pick(child, rest) if isinstance(child, Mapping) else {}
    return {head: picked} if picked else {}


def _merge(into: dict[str, object], picked: Mapping[str, object]) -> None:
    for key, value in picked.items():
        existing = into.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _merge(existing, value)
        else:
            into[key] = dict(value) if isinstance(value, Mapping) else value


def ack(*paths: str) -> Projection:
    """Projection keeping status keys plus the given dotted paths, e.g. ``"automation.id"``."""

    def project(result: Mapping[str, object]) -> Mapping[str, object]:
        kept: dict[str, object] = {key: result[key] for key in _ACK_KEYS if key in result}
        if not kept:
            kept["ok"] = True
        for path in paths:
            _merge(kept, _pick(result, path.split(".")))
        return kept

    return project


@dataclass(frozen=True)
class Policy:
    """Where a tool runs with full results, and what it returns to a sole writer.

    ``sole`` is ``None`` when the tool stays unavailable in shared threads even if
    only the actor has written there; otherwise it projects the result, because
    everyone who can read the thread reads it. Admin writes also allow verified
    admin-only participants, without granting access to personal tools.
    """

    trusted: Place
    actor: Actor = "anyone"
    sole: Projection | None = None
    direct: bool = False


@dataclass(frozen=True)
class Access:
    """What the current run's actor may do in the current thread."""

    private: bool = False
    owner: bool = False
    admin: bool = False
    admin_thread: bool = False
    admin_surface: bool = False
    sole: bool = False
    admin_participants: bool = False
    direct: bool = False
    slack_bot_channel: str | None = None
    # The run was started from a Slack thread; the bot's own reach applies there.
    slack_context: bool = False
    # A workspace curator's own thread (``openswe.memories.curator``).
    curator: bool = False

    @property
    def channel_history(self) -> bool:
        """Whether public channels the bot is in may be read on the asker's behalf."""
        return self.private or bool(self.slack_bot_channel)

    def mode(self, policy: Policy) -> Mode | None:
        if policy.direct and not self.direct:
            return None
        trusted = {
            "anywhere": True,
            "private": self.private,
            "admin_thread": self.admin_thread,
            "admin_surface": self.admin_surface,
            "channel_history": self.channel_history,
            "thread_history": self.slack_context or self.channel_history,
            "curator": self.curator,
        }[policy.trusted]
        if trusted and self._actor(policy.actor, sole=False):
            return "full"
        shared_write = self.sole or (policy.actor == "admin" and self.admin_participants)
        if policy.sole is not None and shared_write and self._actor(policy.actor, sole=True):
            return "sole"
        return None

    def _actor(self, actor: Actor, *, sole: bool) -> bool:
        if actor == "admin":
            return self.admin
        if actor == "owner":
            return self.sole if sole else self.owner
        return True


def direct_user_run(cfg: RunConfig) -> bool:
    """Whether a person started this run directly, rather than an automatic entry point."""
    return (
        cfg.source in ("dashboard", "slack")
        and not cfg.background_task_completion
        and not cfg.schedule_id
        and not cfg.watch_key
    )


async def _metadata(thread_id: str | None) -> dict[str, object]:
    if not thread_id:
        return {}
    try:
        return thread_metadata(await read_thread(thread_id))
    except Exception:
        logger.warning("Could not read thread metadata for access", exc_info=True)
        return {}


@dataclass(frozen=True)
class _SharedWriters:
    cfg: RunConfig
    metadata: Mapping[str, object]
    login: str | None

    async def logins(self) -> set[str] | None:
        if not self.login or not direct_user_run(self.cfg):
            return None
        if (
            self.metadata.get("visibility", "public") != "public"
            or self.metadata.get("owner_type") != "user"
        ):
            return None
        context = SourceContext.from_metadata(self.metadata)
        if self.cfg.slack_thread is not None:
            context.slack_thread = self.cfg.slack_thread
        if context.linear_issue is not None or context.github_issue is not None:
            return None
        logins = set(participant_logins(self.metadata.get(PARTICIPANT_LOGINS_KEY)))
        if self.login.lower() not in logins:
            return None
        for email in participant_logins(self.metadata.get(PARTICIPANT_EMAILS_KEY)):
            if email == (self.cfg.user_email or "").strip().lower():
                continue
            mapped = await User.login_for_email(email)
            if not mapped:
                return None
            logins.add(mapped.lower())
        slack_logins = await self.slack_logins(context)
        return logins | slack_logins if slack_logins is not None else None

    async def slack_logins(self, context: SourceContext) -> set[str] | None:
        slack = context.slack_thread
        if slack is None:
            return set()
        if not slack.channel_id or not slack.thread_ts:
            return None
        bot_user_id = ENV.SLACK_BOT_USER_ID.get()
        try:
            messages = await fetch_slack_thread_messages(
                slack.channel_id, slack.thread_ts, complete=True
            )
        except Exception:
            logger.warning("Could not read the whole Slack thread for access", exc_info=True)
            return None
        if not bot_user_id or not messages or len(messages) >= SLACK_THREAD_MAX_MESSAGES:
            return None
        user_ids: set[str] = set()
        for message in messages:
            user_id = message.get("user")
            if user_id == bot_user_id:
                continue
            if message.get("subtype") in _IGNORED_SLACK_SUBTYPES:
                continue
            if message.get("bot_id") or message.get("bot_profile") or not isinstance(user_id, str):
                return None
            user_ids.add(user_id)
        logins: set[str] = set()
        for user_id in user_ids:
            mapped = await User.login_for_slack(user_id)
            if not mapped:
                return None
            logins.add(mapped.lower())
        return logins

    async def admins(self, logins: set[str] | None) -> bool:
        if not logins:
            return False
        for login in logins:
            if not await participant_is_admin(login):
                return False
        return True


class _ThreadScope(BaseModel):
    """Validated metadata fields that determine tool access."""

    model_config = ConfigDict(strict=True)

    visibility: Literal["public", "private"] = "public"
    owner_type: Literal["user", "system"] | None = None
    admin_thread: bool | None = None
    curator: str | None = None


async def resolve_access(cfg: RunConfig | None = None, *, login: str | None = None) -> Access:
    """Resolve the current run's access from its config and saved thread metadata."""
    cfg = cfg or configurable()
    login = login or cfg.github_login
    if cfg.mcp_caller:
        admin = bool(login) and await actor_is_admin(cfg, login=login)
        return Access(
            private=True,
            owner=bool(login),
            admin=admin,
            admin_thread=admin and cfg.admin_thread is True,
            admin_surface=admin and is_private_admin_surface(cfg),
            sole=bool(login),
            direct=bool(login),
        )
    metadata = await _metadata(cfg.thread_id)
    try:
        scope = _ThreadScope.model_validate(metadata)
    except ValidationError:
        logger.warning(
            "Unrecognized thread scope for tool access", extra={"thread_id": cfg.thread_id}
        )
        return Access()
    if scope.owner_type == "system" and scope.visibility == "private":
        return Access()
    private = scope.visibility == "private"
    admin = await actor_is_admin(cfg, login=login)
    slack = SourceContext.from_metadata(metadata).slack_thread
    current_slack = cfg.slack_thread
    slack_bot_channel = (
        slack.channel_id
        if (
            scope.owner_type == "system"
            and scope.visibility == "public"
            and cfg.source == "slack"
            and slack is not None
            and slack.location is not None
            and slack.triggering_bot_id
            and current_slack is not None
            and current_slack.location == slack.location
            and current_slack.triggering_bot_id == slack.triggering_bot_id
            and current_slack.team_id == slack.team_id
        )
        else None
    )
    writers = _SharedWriters(cfg, metadata, login)
    logins = await writers.logins()
    return Access(
        private=private,
        owner=private_owner_login(cfg, metadata) is not None,
        admin=admin,
        admin_thread=admin and cfg.admin_thread is True and (private or scope.admin_thread is True),
        admin_surface=admin and private and is_private_admin_surface(cfg),
        sole=(
            bool(login)
            and logins == {login.lower()}
            and not (
                set(participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY)))
                - {(cfg.user_email or "").strip().lower()}
            )
        ),
        admin_participants=admin and await writers.admins(logins),
        direct=direct_user_run(cfg),
        slack_bot_channel=slack_bot_channel,
        slack_context=cfg.slack_context,
        # Only the run ``WorkspaceCurator.wake`` started: a person's message in the
        # public thread starts a run that must not steer what every thread reads.
        curator=(
            scope.owner_type == "system"
            and scope.curator is not None
            and scope.curator == cfg.workspace_slug
            and cfg.source == CURATOR_SOURCE
            and not cfg.github_login
            and not cfg.started_by_id
        ),
    )


def policy_of(tool: object) -> Policy | None:
    policy = getattr(tool, "__access__", None)
    return policy if isinstance(policy, Policy) else None


def permitted[T](tools: list[T], access: Access) -> list[T]:
    """The tools this run may bind; tools without a policy are unrestricted."""
    return [tool for tool in tools if (policy := policy_of(tool)) is None or access.mode(policy)]


def access(
    policy: Policy,
    *,
    per_call: Callable[[Mapping[str, object]], Policy | None] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Gate a tool by ``policy``; ``per_call`` may pick a different policy, or none, per call."""

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
            applied = policy
            if per_call is not None:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                applied = per_call(bound.arguments)
                if applied is None:
                    return await fn(*args, **kwargs)
            mode = (await resolve_access()).mode(applied)
            if mode is None:
                return cast(R, {"ok": False, "error": _REFUSED})
            result = await fn(*args, **kwargs)
            if mode == "full" or applied.sole is None:
                return result
            if not isinstance(result, Mapping):
                return cast(R, {"ok": False, "error": _WITHHELD})
            return cast(R, dict(applied.sole(result)))

        setattr(guarded, "__access__", policy)  # noqa: B010
        return guarded

    return decorate
