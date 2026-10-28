"""Deployment metadata resolved once per process; caller identity stays per run."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from http import HTTPStatus
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from typing import TYPE_CHECKING, TypedDict

import httpx

from managed_deepagents._agent_auth_http import agent_auth_headers, request_agent_auth_json
from managed_deepagents._telemetry.gates import is_telemetry_disabled
from managed_deepagents._telemetry.run import RunIdentity

if TYPE_CHECKING:
    from managed_deepagents.identity import RuntimeIdentity


class _AuthMetadata(TypedDict):
    tenant_id: str
    organization_id: str | None


# Cache only verified auth data; a failed lookup stays None until restart.
_auth_metadata: _AuthMetadata | None = None
_auth_resolved = False
_auth_lock = Lock()


def _nonempty(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def detect_run_identity(
    *,
    env: Mapping[str, str] | None = None,
    sdk_version: str | None = None,
    caller: RuntimeIdentity | None = None,
    is_run: bool = True,
) -> RunIdentity:
    from managed_deepagents._connections import (  # noqa: PLC0415
        InvalidAgentAuthEndpointError,
        resolve_agent_auth_base_url,
    )

    global _auth_metadata, _auth_resolved  # noqa: PLW0603 - one lookup per process
    resolved = os.environ if env is None else env
    workspace = _nonempty(resolved.get("LANGSMITH_TENANT_ID")) or _nonempty(
        resolved.get("LANGSMITH_WORKSPACE_ID")
    )
    origin = None
    with suppress(ValueError, InvalidAgentAuthEndpointError, httpx.InvalidURL):
        origin = str(
            httpx.URL(
                resolve_agent_auth_base_url(
                    {
                        **resolved,
                        "LANGSMITH_ENDPOINT": _nonempty(resolved.get("LANGSMITH_AUTH_ENDPOINT"))
                        or resolved.get("LANGSMITH_ENDPOINT", ""),
                    }
                )
            )
        )
    auth = None
    if is_run and not is_telemetry_disabled(resolved):
        # Runtime compilation already runs in asyncio.to_thread.
        with _auth_lock:
            if not _auth_resolved:
                _auth_metadata = _lookup_auth(resolved, origin, workspace)
                _auth_resolved = True
            auth = _auth_metadata
    principal = _nonempty(caller.get("agent_auth_principal_id")) if caller else None
    if (
        principal is None
        and caller
        and resolved.get("MDA_LOCAL_DEV") == "1"
        and caller["user"]["id"] == "mda:local-dev"
    ):
        principal = _nonempty(resolved.get("MDA_AGENT_AUTH_PRINCIPAL_ID"))
    return RunIdentity(
        workspace_id=auth["tenant_id"] if auth else workspace,
        org_id=auth["organization_id"] if auth else None,
        langsmith_host_url=origin,
        principal_id=principal,
        tenant_id=_nonempty(resolved.get("LANGSMITH_TENANT_ID")),
        deployment_id=_nonempty(resolved.get("LANGSMITH_HOST_PROJECT_ID")),
        deployment_name=_nonempty(resolved.get("LANGSMITH_HOST_PROJECT_NAME"))
        or _nonempty(resolved.get("MDA_DEPLOYMENT_NAME")),
        environment="local" if resolved.get("MDA_LOCAL_DEV") == "1" else "deployed",
        sdk_language="python",
        sdk_version=package_version() if sdk_version is None else sdk_version,
    )


def _lookup_auth(
    env: Mapping[str, str], origin: str | None, workspace: str | None
) -> _AuthMetadata | None:
    api_key = (
        _nonempty(env.get("LANGSMITH_CONTROL_PLANE_API_KEY"))
        or _nonempty(env.get("LANGCHAIN_API_KEY"))
        or _nonempty(env.get("LANGSMITH_API_KEY"))
        if _nonempty(env.get("LANGSMITH_AUTH_ENDPOINT"))
        else _nonempty(env.get("LANGSMITH_API_KEY")) or _nonempty(env.get("LANGCHAIN_API_KEY"))
    )
    if not api_key or not origin:
        return None
    try:
        status, body = request_agent_auth_json(
            "GET",
            f"{origin}/auth/public",
            headers=agent_auth_headers(api_key=api_key, workspace_id=workspace),
            timeout=1.0,
        )
        tenant = _nonempty(body.get("tenant_id"))
        if (
            not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES
            or not tenant
            or (workspace and tenant != workspace)
        ):
            return None
        return _AuthMetadata(
            tenant_id=tenant, organization_id=_nonempty(body.get("organization_id"))
        )
    except Exception:  # noqa: BLE001 - lookup failure only makes telemetry less informative
        return None


_cached_version: str | None = None


def package_version() -> str:
    """``unknown`` beats failing a run over a metadata lookup."""
    global _cached_version  # noqa: PLW0603 - process-wide cache, resolved once
    if _cached_version is None:
        try:
            _cached_version = version("managed-deepagents")
        except PackageNotFoundError:
            _cached_version = "unknown"
    return _cached_version
