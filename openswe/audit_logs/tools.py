"""Audit selected tool mutations without retaining arguments or returned values."""

import asyncio
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import ParamSpec, TypeVar

from openswe.audit_logs.context import current_audit_log, enrich_workspace
from openswe.audit_logs.models import AuditLog, AuditLogEnrichments
from openswe.audit_logs.store import append_safely
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.tools.access import direct_user_run
from openswe.tools.admin_gate import configurable
from openswe.users import User

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R", bound=Mapping[str, object])


async def _enrich(entry: AuditLog, *, thread_workspace: bool) -> None:
    cfg = configurable()
    entry.enrichments.thread_id = cfg.thread_id
    if thread_workspace:
        entry.enrichments.workspace = cfg.workspace
    backend = SANDBOX_BACKENDS.get(cfg.thread_id) if cfg.thread_id else None
    if backend is not None and backend.has_backend:
        entry.enrichments.delegated_from_sandbox_id = backend.id
    # A remote MCP caller is the person, calling the tool directly.
    if not direct_user_run(cfg) and not cfg.mcp_caller:
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
    *, reads: Collection[str] = (), target_workspace: str | None = None
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Record a tool's outcome, including refusals returned by its access policy.

    Calls whose ``action`` is one of ``reads`` change nothing and are not
    recorded. ``target_workspace`` names the argument holding the workspace the
    call acts on, which the entry is filed under instead of the thread's.
    """

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        signature = inspect.signature(fn)
        name = getattr(fn, "__name__", "tool")
        operation_name = name[:128] if isinstance(name, str) else "tool"

        @functools.wraps(fn)
        async def audited(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if bound.arguments.get("action") in reads:
                return await fn(*args, **kwargs)
            entry = AuditLog(
                operation_name=operation_name,
                operation_succeeded=False,
                enrichments=AuditLogEnrichments(source="tool", actor_kind="agent"),
            )
            try:
                async with asyncio.timeout(2):
                    await _enrich(entry, thread_workspace=target_workspace is None)
            except Exception:
                logger.warning(
                    "Could not resolve tool audit context",
                    extra={"operation_name": operation_name},
                    exc_info=True,
                )
            workspace = bound.arguments.get(target_workspace) if target_workspace else None
            if isinstance(workspace, str):
                await enrich_workspace(entry, workspace)
            token = current_audit_log.set(entry)
            try:
                result = await fn(*args, **kwargs)
                entry.operation_succeeded = not (
                    result.get("ok") is False
                    or result.get("success") is False
                    or bool(result.get("error"))
                )
                return result
            finally:
                current_audit_log.reset(token)
                await append_safely(entry)

        return audited

    return decorate
