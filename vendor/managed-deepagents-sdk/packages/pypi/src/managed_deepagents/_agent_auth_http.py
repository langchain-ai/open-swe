"""Shared Agent Auth HTTP transport and credential-list paging.

Used by ``credentials.get`` and the credential gate so both talk to the same
API with the same headers, redirect policy, and page walker.
"""

from __future__ import annotations

import atexit
import json
import re
import ssl
import urllib.error
import urllib.parse
from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Final

import httpx

AGENT_AUTH_TIMEOUT_SECONDS = 10.0
AGENT_AUTH_PROBLEM_HINT_MAX_CHARS = 200
_PROBLEM_HINT_FIELDS: Final = ("detail", "remedy")
_CLIENT = httpx.Client(
    verify=ssl.create_default_context(),
    cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    follow_redirects=False,
)
atexit.register(_CLIENT.close)


def agent_auth_problem_hint(body: Mapping[str, object] | None) -> str | None:
    """Return the caller-facing part of an Agent Auth problem+json body.

    ``detail`` names the failure and ``remedy`` names the fix. Nothing else
    from a response body is read, because the rest can quote secret material.
    Both are collapsed to one line and truncated so a long or multi-line body
    cannot reshape an error message.
    """
    seen: dict[str, None] = {}
    for field in _PROBLEM_HINT_FIELDS:
        value = None if body is None else body.get(field)
        if not isinstance(value, str):
            continue
        text = " ".join(value.split())
        if text:
            seen[text] = None
    if not seen:
        return None
    hint = " ".join(seen)
    if len(hint) > AGENT_AUTH_PROBLEM_HINT_MAX_CHARS:
        return hint[:AGENT_AUTH_PROBLEM_HINT_MAX_CHARS] + "..."
    return hint


class CredentialStoreError(RuntimeError):
    """Agent Auth could not answer.

    Callers pass a fixed message; the only part of a response body that reaches
    this error, the logs, or the traceback chain is the allow-listed
    problem+json hint, since an arbitrary body can quote secret material.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        problem: Mapping[str, object] | None = None,
    ) -> None:
        self.status = status
        with_status = message if status is None else f"{message} (status {status})"
        hint = agent_auth_problem_hint(problem)
        super().__init__(with_status if hint is None else f"{with_status}: {hint}")


class ConnectionsNotConfiguredError(CredentialStoreError):
    """The runtime lacks a setting every Agent Auth call needs.

    The message only names the missing setting, never a value, so it is safe to
    log.
    """

    def __init__(self, setting: str) -> None:
        super().__init__(f"connections are not configured: {setting} is not set")


# Each runtime quotes the slug its own way: repr() here, JSON.stringify in npm.
_MISSING_WORKSPACE_CONNECTION = re.compile(
    r"""workspace connection ['"]([A-Za-z0-9][A-Za-z0-9._-]*)['"] is not configured"""
)


def missing_workspace_connection_error(slug: str) -> CredentialStoreError:
    """Return the error for a declared slug that has no workspace connection row."""
    return CredentialStoreError(f"workspace connection {slug!r} is not configured")


def missing_workspace_connection_slug(text: str) -> str | None:
    """Read the slug back out of a missing-connection message.

    A run that fails in the gate reaches a channel as a plain string, so this is
    the only way back to the slug. It must move with the message it parses.
    """
    match = _MISSING_WORKSPACE_CONNECTION.search(text)
    return None if match is None else match.group(1)


__all__ = [
    "AGENT_AUTH_PROBLEM_HINT_MAX_CHARS",
    "AGENT_AUTH_TIMEOUT_SECONDS",
    "CredentialStoreError",
    "agent_auth_credential_query",
    "agent_auth_headers",
    "agent_auth_problem_hint",
    "missing_workspace_connection_error",
    "missing_workspace_connection_slug",
    "request_agent_auth_json",
]


def agent_auth_credential_query(
    *,
    owner_type: str,
    owner_id: str,
    kind: str,
    page_size: int | None = None,
    cursor: str | None = None,
) -> str:
    """Query string for listing credentials filtered by owner and kind.

    The shipped list endpoint does not filter by slug; callers match ``slug``
    client-side across pages.
    """
    params: dict[str, str] = {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "kind": kind,
    }
    if page_size is not None:
        params["page_size"] = str(page_size)
    if cursor:
        params["cursor"] = cursor
    return urllib.parse.urlencode(params)


def agent_auth_headers(
    *,
    api_key: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-langsmith-product": "mda",
    }
    resolved_api_key = (api_key or "").strip()
    if resolved_api_key:
        headers["x-api-key"] = resolved_api_key
    resolved_workspace = (workspace_id or "").strip()
    if resolved_workspace:
        headers["x-tenant-id"] = resolved_workspace
    return headers


def _decode_json_object(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def request_agent_auth_json(
    method: str,
    url: str,
    *,
    body: Mapping[str, object] | None = None,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, dict[str, object]]:
    """Issue one Agent Auth HTTP call: no redirects, bounded timeout, JSON body."""
    data = None if body is None else json.dumps(dict(body)).encode("utf-8")
    try:
        response = _CLIENT.request(method, url, content=data, headers=headers, timeout=timeout)
    except httpx.RequestError as exc:
        raise urllib.error.URLError(exc) from exc
    return response.status_code, _decode_json_object(response.content)
