"""Rate-limited diagnostics, readable through private admin SQL only."""

import asyncio
import json
import logging
from collections import deque
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import text

from openswe.database import configured, transaction
from openswe.webhooks.event_log import EventLog

logger = logging.getLogger(__name__)


class ErrorLog:
    def __init__(self) -> None:
        self._recorded: deque[float] = deque()

    async def record(
        self,
        source: Literal["internal", "frontend"],
        *,
        endpoint: str,
        payload: dict[str, JsonValue],
        error_id: str = "",
    ) -> None:
        """Store at most five diagnostics in any rolling minute per process."""
        if not configured():
            return
        now = monotonic()
        while self._recorded and self._recorded[0] <= now - 60:
            self._recorded.popleft()
        if len(self._recorded) >= 5:
            return
        self._recorded.append(now)
        try:
            async with asyncio.timeout(2):
                await EventLog.ensure_partitions()
                async with transaction() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO event_log "
                            "(source, endpoint, event_type, delivery_id, payload) "
                            "VALUES (:source, :endpoint, 'error', :error_id, CAST(:payload AS jsonb))"
                        ),
                        {
                            "source": source,
                            "endpoint": endpoint,
                            "error_id": error_id or str(uuid4()),
                            "payload": json.dumps(payload),
                        },
                    )
        except Exception:
            logger.warning("Recording a diagnostic in the event log failed", exc_info=True)


ERROR_LOG = ErrorLog()
