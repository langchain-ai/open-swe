"""Temporary: a single Tool Gateway server answers ``tools/list`` with 428 until
the person connects, so MDA shows the connect card before the run. Delete this
once Tool Gateway lists a single server's tools like a kit's.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING

from managed_deepagents._connection_authorization import (
    CredentialAuthorizationDeferredError,
    CredentialAuthorizationRequiredError,
    PendingCredential,
    ToolCredentialRequiredError,
    raise_credential_authorization_required,
)
from managed_deepagents._log import logger
from managed_deepagents._mcp_problem import McpProblemError, cause_of

if TYPE_CHECKING:
    import httpx2
    from langchain_core.tools import BaseTool


def connect_request(server_name: str, response: httpx2.Response) -> ToolCredentialRequiredError:
    """The connection a single server's 428 names, as a connect card entry."""
    try:
        body = response.json()
    except ValueError:
        body = None
    fields = body if isinstance(body, Mapping) else {}
    url, auth_id, slug, name = (
        fields.get(key) for key in ("verification_url", "auth_id", "slug", "display_name")
    )
    label = str(name or slug or server_name)
    if not (url and auth_id and slug):
        return ToolCredentialRequiredError(label, [])
    credential: PendingCredential = {
        "slug": str(slug),
        "kind": "secret" if fields.get("kind") == "secret" else "oauth2",
        "connect_url": str(url),
        "auth_id": str(auth_id),
        "provider": label,
    }
    return ToolCredentialRequiredError(label, [credential])


def needs_a_connection(name: str, error: BaseException, pending: list[PendingCredential]) -> bool:
    """Whether a server refused to list tools for want of a connection: the person's
    (428, added to ``pending``), or one only a workspace admin can connect (409)."""
    refusal = cause_of(error, ToolCredentialRequiredError)
    if refusal is not None and refusal.credentials:
        pending.extend(refusal.credentials)
        return True
    conflict = cause_of(error, McpProblemError)
    if conflict is None or conflict.status != HTTPStatus.CONFLICT:
        return False
    logger.warning(
        'LangSmith MCP server "%s" skipped: a workspace admin has to connect it.',
        logger.log_identifier(name),
    )
    return True


def ask_before_the_run(pending: list[PendingCredential], tools: list[BaseTool]) -> None:
    """Show the connect card before the run, merged with other connect cards by the
    credential gate. Without the connection, the run continues without the server."""
    try:
        raise_credential_authorization_required(pending)
    except CredentialAuthorizationDeferredError:
        raise CredentialAuthorizationDeferredError(pending, tools) from None
    except CredentialAuthorizationRequiredError:
        log_still_not_connected(pending)


def log_still_not_connected(credentials: list[PendingCredential]) -> None:
    names = ", ".join(dict.fromkeys(str(c.get("provider") or c["slug"]) for c in credentials))
    logger.warning("Tool Gateway servers skipped, still not connected: %s", names)
