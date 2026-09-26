"""Declarative access policies for agent tools.

A tool declares where it may run with ``@access(Policy(...))``. The factory binds
only the tools the run's access permits, and every call is rechecked so a thread
that gains a second writer mid-run loses the tools that relied on it having one.
"""

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, ParamSpec, TypeVar, cast

import langgraph_sdk

from agent.config import ENV
from agent.credential_scope import private_owner_login
from agent.run_config import RunConfig
from agent.slack.client import SLACK_THREAD_MAX_MESSAGES, fetch_slack_thread_messages
from agent.source_context import SourceContext
from agent.tools.admin_gate import actor_is_admin, configurable, is_private_admin_surface
from agent.users import User
from agent.utils.json_types import thread_metadata
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_logins,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R", bound=Mapping[str, object])

Place = Literal["anywhere", "private", "admin_thread", "admin_surface"]
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
    everyone who can read the thread reads it.
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
    direct: bool = False

    def mode(self, policy: Policy) -> Mode | None:
        if policy.direct and not self.direct:
            return None
        trusted = {
            "anywhere": True,
            "private": self.private,
            "admin_thread": self.admin_thread,
            "admin_surface": self.admin_surface,
        }[policy.trusted]
        if trusted and self._actor(policy.actor, sole=False):
            return "full"
        if policy.sole is not None and self.sole and self._actor(policy.actor, sole=True):
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
        return thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
    except Exception:
        logger.warning("Could not read thread metadata for access", exc_info=True)
        return {}


async def _slack_writers_are(context: SourceContext, login: str) -> bool:
    slack = context.slack_thread
    if slack is None:
        return True
    if not slack.channel_id or not slack.thread_ts:
        return False
    bot_user_id = ENV.SLACK_BOT_USER_ID.get()
    messages = await fetch_slack_thread_messages(slack.channel_id, slack.thread_ts)
    if not bot_user_id or not messages or len(messages) >= SLACK_THREAD_MAX_MESSAGES:
        return False
    user_ids: set[str] = set()
    for message in messages:
        user_id = message.get("user")
        if user_id == bot_user_id:
            continue
        if message.get("subtype") in _IGNORED_SLACK_SUBTYPES:
            continue
        if message.get("bot_id") or message.get("bot_profile") or not isinstance(user_id, str):
            return False
        user_ids.add(user_id)
    for user_id in user_ids:
        mapped = await User.login_for_slack(user_id)
        if not mapped or mapped.lower() != login.lower():
            return False
    return True


async def sole_writer(cfg: RunConfig, metadata: Mapping[str, object], login: str | None) -> bool:
    """Whether ``login`` is the only person or bot that has written in this shared thread."""
    if not login or not direct_user_run(cfg):
        return False
    if metadata.get("visibility", "public") != "public" or metadata.get("owner_type") != "user":
        return False
    context = SourceContext.from_metadata(metadata)
    if cfg.slack_thread is not None:
        context.slack_thread = cfg.slack_thread
    if context.linear_issue is not None or context.github_issue is not None:
        return False
    if participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY)) != [login.lower()]:
        return False
    emails = set(participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY)))
    if emails - {(cfg.user_email or "").strip().lower()}:
        return False
    return await _slack_writers_are(context, login)


def _recognized(metadata: Mapping[str, object]) -> bool:
    visibility = metadata.get("visibility", "public")
    owner_type = metadata.get("owner_type")
    return (
        visibility in ("public", "private")
        and owner_type in (None, "user", "system")
        and not (owner_type == "system" and visibility == "private")
    )


async def resolve_access(cfg: RunConfig | None = None, *, login: str | None = None) -> Access:
    """Resolve the current run's access from its config and saved thread metadata."""
    cfg = cfg or configurable()
    login = login or cfg.github_login
    metadata = await _metadata(cfg.thread_id)
    if not _recognized(metadata):
        metadata = {}
    admin = await actor_is_admin(cfg, login=login)
    return Access(
        private=metadata.get("visibility") == "private",
        owner=private_owner_login(cfg, metadata) is not None,
        admin=admin,
        admin_thread=admin and cfg.admin_thread is True,
        admin_surface=admin and is_private_admin_surface(cfg),
        sole=await sole_writer(cfg, metadata, login),
        direct=direct_user_run(cfg),
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
