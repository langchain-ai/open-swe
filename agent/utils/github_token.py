"""GitHub token lookup utilities."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError

from ..encryption import decrypt_token

logger = logging.getLogger(__name__)

_GITHUB_TOKEN_METADATA_KEY = "github_token_encrypted"

client = get_client()


def _read_encrypted_github_token(metadata: dict[str, Any]) -> str | None:
    encrypted_token = metadata.get(_GITHUB_TOKEN_METADATA_KEY)
    return encrypted_token if isinstance(encrypted_token, str) and encrypted_token else None


def _decrypt_github_token(encrypted_token: str | None) -> str | None:
    if not encrypted_token:
        return None

    return decrypt_token(encrypted_token)


def get_github_token() -> str | None:
    """Resolve a GitHub token from run metadata."""
    config = get_config()
    return _decrypt_github_token(_read_encrypted_github_token(config.get("metadata", {})))


async def get_github_token_from_thread(thread_id: str) -> tuple[str | None, str | None]:
    """Resolve a GitHub token from LangGraph thread metadata.

    Returns:
        A `(token, encrypted_token)` tuple. Either value may be `None`.
    """
    try:
        thread = await client.threads.get(thread_id)
    except NotFoundError:
        logger.debug("Thread %s not found while looking up GitHub token", thread_id)
        return None, None
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch thread metadata for %s", thread_id)
        return None, None

    encrypted_token = _read_encrypted_github_token((thread or {}).get("metadata", {}))
    token = _decrypt_github_token(encrypted_token)
    if token:
        logger.info("Found GitHub token in thread metadata for thread %s", thread_id)
    return token, encrypted_token
