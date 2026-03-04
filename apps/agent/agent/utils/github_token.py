"""GitHub token lookup utilities."""

from __future__ import annotations

import asyncio
import logging

from langgraph.config import get_config
from langgraph_sdk import get_client

from ..encryption import decrypt_token

logger = logging.getLogger(__name__)

client = get_client()


def get_github_token() -> str | None:
    """Resolve a GitHub token from config metadata or thread metadata."""
    config = get_config()
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")

    encrypted_token = None
    metadata = config.get("metadata", {})
    if isinstance(metadata, dict):
        encrypted_token = metadata.get("github_token_encrypted")
    if not encrypted_token and thread_id:
        try:
            thread = asyncio.run(client.threads.get(thread_id))
            thread_metadata = (thread or {}).get("metadata", {})
            if isinstance(thread_metadata, dict):
                encrypted_token = thread_metadata.get("github_token_encrypted")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch thread metadata for %s", thread_id)
    return decrypt_token(encrypted_token) if encrypted_token else None
