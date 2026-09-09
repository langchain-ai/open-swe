"""Once-only claims for inbound webhook deliveries, shared across providers."""

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import Sequence

from langgraph_sdk import get_client

from agent.config import ENV

logger = logging.getLogger(__name__)

_LOCAL_CLAIM_LIMIT = 2048


class DeliveryClaims:
    """Claims keyed by delivery id, held as short-lived LangGraph threads plus a local LRU."""

    def __init__(self, namespace: Sequence[str], *, limit: int = _LOCAL_CLAIM_LIMIT) -> None:
        self._namespace = tuple(namespace)
        self._seed = ":".join(("open-swe", *self._namespace))
        self._limit = limit
        self._claimed: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()

    def thread_id(self, key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self._seed}:{key}"))

    async def _claim_remotely(self, key: str) -> bool | None:
        client = get_client(url=ENV.LANGGRAPH_URL.get())
        try:
            await client.threads.create(thread_id=self.thread_id(key), if_exists="raise", ttl=10)
        except Exception:  # noqa: BLE001
            try:
                await client.threads.get(self.thread_id(key))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Delivery claim failed",
                    extra={"claim_namespace": self._seed, "claim_key": key},
                )
                return None
            return False
        return True

    def _claim_locally(self, key: str) -> None:
        self._claimed[key] = None
        self._claimed.move_to_end(key)
        while len(self._claimed) > self._limit:
            self._claimed.popitem(last=False)

    def seen(self, key: str) -> bool:
        return bool(key and key in self._claimed)

    def reset(self) -> None:
        self._claimed.clear()

    async def claim(self, *keys: str) -> bool:
        """Claim every key at once; fail open when the platform is unavailable."""
        wanted = tuple(dict.fromkeys(key for key in keys if key))
        if not wanted:
            return True

        async with self._lock:
            if any(key in self._claimed for key in wanted):
                return False

            claimed = False
            for key in wanted:
                result = await self._claim_remotely(key)
                if result is False:
                    for key_to_mark in wanted:
                        self._claim_locally(key_to_mark)
                    return False
                claimed = claimed or bool(result)

            if claimed:
                for key in wanted:
                    self._claim_locally(key)
            return True
