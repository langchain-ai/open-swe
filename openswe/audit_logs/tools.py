"""Audit selected tool mutations without retaining arguments or returned values."""

import asyncio
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import ParamSpec, TypeVar

from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import AuditLog, AuditLogEnrichments
from openswe.audit_logs.store import append_safely

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R", bound=Mapping[str, object])


def audit_tool(
    *, reads: Collection[str] = ()
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Record a tool's outcome, including refusals returned by its access policy.

    Calls whose ``action`` is one of ``reads`` are not recorded.
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
                    await entry.enrich_tool()
            except Exception:
                logger.warning(
                    "Could not resolve tool audit context",
                    extra={"operation_name": operation_name},
                    exc_info=True,
                )
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
