"""Audit selected tool mutations without retaining arguments or returned values."""

import asyncio
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import ParamSpec, TypeVar

from agent.audit_logs.context import enrich_workspace
from agent.audit_logs.models import AuditLog, AuditLogEnrichments
from agent.audit_logs.store import append_safely
from agent.sandboxes.state import SANDBOX_BACKENDS
from agent.tools.access import direct_user_run
from agent.tools.admin_gate import configurable
from agent.users import User

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R", bound=Mapping[str, object])


async def _enrich(entry: AuditLog, *, feature_flags: bool) -> None:
    cfg = configurable()
    entry.enrichments.thread_id = cfg.thread_id
    if not feature_flags:
        entry.enrichments.workspace = cfg.workspace
    backend = SANDBOX_BACKENDS.get(cfg.thread_id) if cfg.thread_id else None
    if backend is not None and backend.has_backend:
        entry.enrichments.delegated_from_sandbox_id = backend.id
    if not direct_user_run(cfg):
        return
    entry.enrichments.actor_login = cfg.github_login
    if cfg.github_user_id:
        user = await User.for_identity("github", cfg.github_user_id)
    elif cfg.github_login:
        user = await User.for_login("github", cfg.github_login)
    elif cfg.slack_thread and cfg.slack_thread.triggering_user_id:
        user = await User.for_identity("slack", cfg.slack_thread.triggering_user_id)
    else:
        user = None
    if user is not None:
        entry.user_id = user.id
        entry.enrichments.actor_login = user.github_login or None


def audit_tool(
    *, skip_read: bool = False
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Record a tool's outcome, including refusals returned by its access policy."""

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        signature = inspect.signature(fn)
        name = getattr(fn, "__name__", "tool")
        operation_name = name[:128] if isinstance(name, str) else "tool"

        @functools.wraps(fn)
        async def audited(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if skip_read and bound.arguments.get("action") == "read":
                return await fn(*args, **kwargs)
            entry = AuditLog(
                operation_name=operation_name,
                operation_succeeded=False,
                enrichments=AuditLogEnrichments(source="tool", actor_kind="agent"),
            )
            try:
                async with asyncio.timeout(2):
                    await _enrich(entry, feature_flags=operation_name == "manage_feature_flags")
            except Exception:
                logger.warning(
                    "Could not resolve tool audit context",
                    extra={"operation_name": operation_name},
                    exc_info=True,
                )
            workspace = bound.arguments.get("workspace")
            if operation_name == "manage_feature_flags" and isinstance(workspace, str):
                await enrich_workspace(entry, workspace)
            try:
                result = await fn(*args, **kwargs)
                entry.operation_succeeded = not (
                    result.get("ok") is False
                    or result.get("success") is False
                    or bool(result.get("error"))
                )
                return result
            finally:
                await append_safely(entry)

        return audited

    return decorate
