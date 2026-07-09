"""Acquire Azure DevOps access tokens via Microsoft Entra ID (service principal).

Uses ``azure-identity`` token caching and refresh. Tokens are short-lived; async
callers should use :func:`get_azure_devops_access_token_async` (runs sync Azure SDK
calls in a thread pool so LangGraph/blockbuster does not treat socket I/O as
blocking the event loop). Sync code may call :func:`get_azure_devops_access_token_sync`
or resolve via :func:`agent.utils.azure_devops.resolve_azure_devops_pat`.

Git and REST use the same string as with a PAT: Basic auth with an empty username
and the access token as the password (see ``basic_auth_headers``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Default scope for Azure DevOps (Azure AD resource).
_DEFAULT_DEVOPS_SCOPE = "https://app.vssps.visualstudio.com/.default"

_credential: Any = None
_credential_lock = threading.Lock()


def is_entra_mode_requested() -> bool:
    """True if ``AZURE_DEVOPS_USE_ENTRA_IDENTITY`` is set (even if credentials are incomplete)."""
    return (os.environ.get("AZURE_DEVOPS_USE_ENTRA_IDENTITY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _entra_identity_enabled() -> bool:
    if not is_entra_mode_requested():
        return False
    tenant = (os.environ.get("AZURE_TENANT_ID") or "").strip()
    client = (os.environ.get("AZURE_CLIENT_ID") or "").strip()
    if not tenant or not client:
        logger.warning(
            "AZURE_DEVOPS_USE_ENTRA_IDENTITY is set but AZURE_TENANT_ID or "
            "AZURE_CLIENT_ID is missing",
        )
        return False
    cert = (os.environ.get("AZURE_CLIENT_CERTIFICATE_PATH") or "").strip()
    secret = (os.environ.get("AZURE_CLIENT_SECRET") or "").strip()
    if not cert and not secret:
        logger.warning(
            "AZURE_DEVOPS_USE_ENTRA_IDENTITY is set but neither "
            "AZURE_CLIENT_CERTIFICATE_PATH nor AZURE_CLIENT_SECRET is configured",
        )
        return False
    return True


def _azure_devops_scope() -> str:
    return (os.environ.get("AZURE_DEVOPS_AAD_SCOPE") or _DEFAULT_DEVOPS_SCOPE).strip()


def _build_credential() -> Any:
    from azure.identity import CertificateCredential, ClientSecretCredential

    tenant_id = os.environ["AZURE_TENANT_ID"].strip()
    client_id = os.environ["AZURE_CLIENT_ID"].strip()
    cert_path = (os.environ.get("AZURE_CLIENT_CERTIFICATE_PATH") or "").strip()
    client_secret = (os.environ.get("AZURE_CLIENT_SECRET") or "").strip()

    if cert_path:
        password = (os.environ.get("AZURE_CLIENT_CERTIFICATE_PASSWORD") or "").strip()
        kwargs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "client_id": client_id,
            "certificate_path": cert_path,
        }
        if password:
            kwargs["password"] = password
        logger.info(
            "Azure DevOps auth: using CertificateCredential (client_id=%s)",
            client_id,
        )
        return CertificateCredential(**kwargs)
    if client_secret:
        logger.info(
            "Azure DevOps auth: using ClientSecretCredential (client_id=%s)",
            client_id,
        )
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    raise RuntimeError("No certificate path or client secret for Entra identity")


def get_azure_devops_access_token_sync() -> str | None:
    """Return a valid Azure DevOps access token, or ``None`` if Entra mode is off.

    Performs synchronous HTTP via ``azure-identity`` / ``requests``. Do not call
    this from an asyncio event loop thread; use :func:`get_azure_devops_access_token_async`
    instead.

    ``azure-identity`` caches tokens and refreshes before expiry when possible.
    """
    if not _entra_identity_enabled():
        return None

    global _credential
    try:
        with _credential_lock:
            if _credential is None:
                _credential = _build_credential()
        scope = _azure_devops_scope()
        token = _credential.get_token(scope)
        return token.token
    except Exception:
        logger.exception("Failed to acquire Azure DevOps Entra access token")
        return None


async def get_azure_devops_access_token_async() -> str | None:
    """Same as :func:`get_azure_devops_access_token_sync`, safe for async/web servers.

    Runs the sync Azure identity client in ``asyncio.to_thread`` so MSAL/urllib3
    socket usage does not run on the event loop (avoids LangGraph blockbuster
    ``BlockingError`` on ``socket.connect``).
    """
    return await asyncio.to_thread(get_azure_devops_access_token_sync)


def reset_entra_credential_for_tests() -> None:
    """Clear cached credential (tests only)."""
    global _credential
    with _credential_lock:
        _credential = None
