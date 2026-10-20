"""Resolve user-owned connections or interrupt for authorization."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

from langgraph.types import interrupt as graph_interrupt

from managed_deepagents._agent_auth import (
    AgentAuth,
    CredentialAuthorizationRequiredError,
)
from managed_deepagents._agent_auth_http import CredentialStoreError


class PendingCredential(TypedDict, total=False):
    slug: str
    kind: Literal["oauth2", "secret"]
    connect_url: str
    auth_id: str
    #: Optional display label (e.g. MCP server name). Slack prefers this over slug.
    provider: str


class CredentialAuthorizationRequiredInterrupt(TypedDict):
    """Interrupt payload when the caller has not granted one or more connections."""

    type: str
    message: str
    credentials: list[PendingCredential]


@dataclass(frozen=True)
class _UserConnectionGrant:
    slug: str
    kind: Literal["oauth2", "secret"]
    token: str | None


InterruptFn = Callable[[CredentialAuthorizationRequiredInterrupt], None]


def raise_credential_authorization_required(
    credentials: Sequence[PendingCredential],
    *,
    interrupt: InterruptFn | None = None,
    message: str = "Connect the following integrations to continue.",
) -> None:
    """Interrupt once with the given missing credentials, then raise.

    Shared by the credential gate (ordinary MCP connections) and LMT probes so
    every missing grant becomes one ``credential_authorization_required``.
    """
    if not credentials:
        raise ValueError("credentials must be non-empty")
    payload: CredentialAuthorizationRequiredInterrupt = {
        "type": "credential_authorization_required",
        "message": message,
        "credentials": list(credentials),
    }
    (interrupt or _interrupt)(payload)
    raise CredentialAuthorizationRequiredError(
        [str(credential["slug"]) for credential in credentials]
    )


def collect_pending_user_connection_credentials(
    agent_auth: AgentAuth,
    slugs: Sequence[str],
) -> list[PendingCredential]:
    """Return pending credentials for missing grants without interrupting."""
    grants = [_read_user_connection_grant(agent_auth, slug) for slug in dict.fromkeys(slugs)]
    return _pending_credentials_for_grants(agent_auth, grants)


def resolve_user_connection_grants(
    agent_auth: AgentAuth,
    slugs: Sequence[str],
    *,
    interrupt: InterruptFn | None = None,
) -> dict[str, str]:
    """Return user connection material or interrupt once for every missing grant."""
    grants = [_read_user_connection_grant(agent_auth, slug) for slug in dict.fromkeys(slugs)]
    pending = _pending_credentials_for_grants(agent_auth, grants)
    if pending:
        raise_credential_authorization_required(pending, interrupt=interrupt)
    return {grant.slug: cast("str", grant.token) for grant in grants}


def _pending_credentials_for_grants(
    agent_auth: AgentAuth,
    grants: Sequence[_UserConnectionGrant],
) -> list[PendingCredential]:
    missing_oauth = [grant for grant in grants if grant.token is None and grant.kind == "oauth2"]
    missing_secret = [grant for grant in grants if grant.token is None and grant.kind == "secret"]
    if not missing_oauth and not missing_secret:
        return []
    sessions = [agent_auth.start_authorization_session(grant.slug) for grant in missing_oauth]
    return [
        {
            "slug": session["slug"],
            "kind": "oauth2",
            "connect_url": session["connect_url"],
            "auth_id": session["auth_id"],
        }
        for session in sessions
    ] + [{"slug": grant.slug, "kind": "secret"} for grant in missing_secret]


def _read_user_connection_grant(agent_auth: AgentAuth, slug: str) -> _UserConnectionGrant:
    row = agent_auth.find_workspace_connection(slug)
    if row is None:
        raise CredentialStoreError(f"workspace connection {slug!r} is not configured")
    kind: Literal["oauth2", "secret"] = "secret" if row.kind == "secret" else "oauth2"
    token = (
        agent_auth.read_user_secret_for_connection(row)
        if kind == "secret"
        else agent_auth.read_user_access_token_for_connection(row)
    )
    return _UserConnectionGrant(slug=slug, kind=kind, token=token)


def _interrupt(payload: CredentialAuthorizationRequiredInterrupt) -> None:
    graph_interrupt(payload)


__all__ = [
    "CredentialAuthorizationRequiredInterrupt",
    "PendingCredential",
    "collect_pending_user_connection_credentials",
    "raise_credential_authorization_required",
    "resolve_user_connection_grants",
]
