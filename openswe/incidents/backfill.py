"""Copy legacy incident namespaces before switching incident traffic to PostgreSQL."""

import argparse
import asyncio
import logging
from collections.abc import Mapping

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from openswe.database import postgres
from openswe.incidents.persistence import RecordKind, import_legacy
from openswe.store import store_client

logger = logging.getLogger(__name__)
KINDS: tuple[RecordKind, ...] = (
    "policies",
    "incidents",
    "reports",
    "commands",
    "history",
    "summaries",
)


async def backfill(*, client: LangGraphClient | None = None, page_size: int = 100) -> int:
    client = client or store_client()
    if page_size < 1:
        raise ValueError("page_size must be positive")
    copied = 0
    for kind in KINDS:
        namespace = ["incidents", kind]
        offset = 0
        while True:
            response = await client.store.search_items(namespace, limit=page_size, offset=offset)
            items = response["items"]
            for item in items:
                if item.get("namespace", namespace) != namespace:
                    continue
                key, payload = item.get("key"), item.get("value")
                if not isinstance(key, str) or not isinstance(payload, Mapping):
                    raise ValueError(f"Invalid legacy incident record in {kind}")
                copied += await import_legacy(kind, key, dict(payload))
            if len(items) < page_size:
                break
            offset += len(items)
    logger.info("Legacy incidents copied", extra={"records_copied": copied})
    return copied


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True, help="Legacy LangGraph deployment URL")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    await postgres.migrate()
    await backfill(client=get_client(url=args.source_url))


if __name__ == "__main__":
    asyncio.run(main())
