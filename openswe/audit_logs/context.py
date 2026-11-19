"""The audit entry attached to the current request or tool call."""

from contextvars import ContextVar

from openswe.audit_logs.models import AuditLog

current_audit_log: ContextVar[AuditLog | None] = ContextVar("current_audit_log", default=None)
