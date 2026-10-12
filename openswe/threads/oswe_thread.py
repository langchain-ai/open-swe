"""Open SWE's typed view of a LangGraph thread."""

import logging
from typing import Self

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ConfigDict

from openswe.utils import ttl_cache
from openswe.utils.json_types import ThreadLike, thread_metadata

logger = logging.getLogger(__name__)

PREFER_TOOLS_IN_SANDBOX_KEY = "prefer_tools_in_sandbox"
# Fixed at creation, so a cached read never goes stale.
_TOOLS_IN_SANDBOX_CACHE_TTL_SECONDS = 3600


class OsweThreadMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_login: str | None = None
    # Stamped from the owner's preference at creation; never changes afterwards.
    prefer_tools_in_sandbox: bool = False


class OsweThread(BaseModel):
    metadata: OsweThreadMetadata

    @classmethod
    def from_sdk(cls, thread: ThreadLike) -> Self:
        return cls(metadata=OsweThreadMetadata.model_validate(thread_metadata(thread)))

    @classmethod
    async def get(cls, client: LangGraphClient, thread_id: str) -> Self:
        return cls.from_sdk(await client.threads.get(thread_id=thread_id))

    @classmethod
    async def prefers_tools_in_sandbox(cls, client: LangGraphClient, thread_id: str) -> bool:
        async def _load() -> bool:
            return (await cls.get(client, thread_id)).metadata.prefer_tools_in_sandbox

        try:
            return await ttl_cache.cached(
                f"prefer-tools-in-sandbox:{thread_id}", _TOOLS_IN_SANDBOX_CACHE_TTL_SECONDS, _load
            )
        except Exception:
            logger.warning(
                "Could not read tools-in-sandbox mode for thread",
                exc_info=True,
                extra={"thread_id": thread_id},
            )
            return False
