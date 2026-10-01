"""Binary content deepagents offloads out of thread state into the store."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.store import get_value, put_value

THREAD_BLOBS_NAMESPACE = "thread_blobs"
_BLOB_REF_KEY = "deepagents_blob"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def blob_namespace(thread_id: str) -> tuple[str, str]:
    return (THREAD_BLOBS_NAMESPACE, thread_id)


def referenced_blob_digests(messages: Sequence[Any]) -> list[str]:
    """Digests of the offloaded blobs referenced by serialized ``messages``."""
    digests: dict[str, None] = {}
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        for block in content if isinstance(content, list) else []:
            ref = block.get(_BLOB_REF_KEY) if isinstance(block, Mapping) else None
            if isinstance(ref, str) and _DIGEST_RE.fullmatch(ref):
                digests[ref] = None
    return list(digests)


async def copy_thread_blobs(
    source_thread_id: str, target_thread_id: str, digests: Sequence[str]
) -> None:
    """Copy ``digests`` from one thread's blobs to another's; blobs already gone are skipped."""
    for digest in digests:
        key = f"/{digest}"
        value = await get_value(blob_namespace(source_thread_id), key)
        if value is not None:
            await put_value(blob_namespace(target_thread_id), key, value)
