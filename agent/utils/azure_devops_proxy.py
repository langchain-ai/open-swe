"""Track and refresh Azure DevOps proxy auth on LangSmith sandboxes."""

from __future__ import annotations

import logging
import os

from langgraph.config import get_config

from .sandbox_state import SANDBOX_BACKENDS, unwrap_sandbox_backend

logger = logging.getLogger(__name__)

_ADO_PROXY_THREADS: set[str] = set()


def mark_ado_proxy_thread(thread_id: str | None) -> None:
    if thread_id:
        _ADO_PROXY_THREADS.add(thread_id)


def clear_ado_proxy_thread(thread_id: str | None) -> None:
    if thread_id:
        _ADO_PROXY_THREADS.discard(thread_id)


def ado_proxy_active(thread_id: str | None) -> bool:
    return bool(thread_id and thread_id in _ADO_PROXY_THREADS)


async def resolve_ado_proxy_pat(thread_id: str) -> str | None:
    from .auth import resolve_scm_credential

    try:
        config = get_config()
    except Exception:
        logger.debug("ADO proxy: no run config for thread %s", thread_id, exc_info=True)
        return None
    pat, _, provider = await resolve_scm_credential(config, thread_id)
    if provider != "azure_devops" or not pat:
        return None
    return pat


async def refresh_ado_proxy_if_active(thread_id: str | None) -> bool:
    """Re-resolve ADO credentials and refresh merged proxy rules (Entra rotation)."""
    if not thread_id or not ado_proxy_active(thread_id):
        return False
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return False

    from .github_proxy import _PROXY_TOKEN_EXPIRY, refresh_proxy_token

    if thread_id in _PROXY_TOKEN_EXPIRY:
        return await refresh_proxy_token(thread_id)

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        return False

    ado_pat = await resolve_ado_proxy_pat(thread_id)
    if not ado_pat:
        logger.warning("ADO proxy refresh for thread %s failed: no credential", thread_id)
        return False

    from ..integrations.langsmith import _configure_sandbox_proxy

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    await _configure_sandbox_proxy(current_backend.id, ado_pat=ado_pat)
    logger.info("Refreshed Azure DevOps proxy for thread %s", thread_id)
    return True
