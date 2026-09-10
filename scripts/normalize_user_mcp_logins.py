"""Normalize pre-release personal MCP namespaces while MCP writes/runs are paused."""

import argparse
import asyncio

from httpx import HTTPStatusError
from langgraph_sdk import get_client


async def normalize(url: str, apply: bool) -> None:
    store = get_client(url=url).store
    namespaces = []
    offset = 0
    while True:
        page = await store.list_namespaces(prefix=["user_mcps"], max_depth=2, offset=offset)
        namespaces.extend(page["namespaces"])
        if len(page["namespaces"]) < 100:
            break
        offset += 100
    moves = {}
    for namespace in namespaces:
        if len(namespace) != 2:
            continue
        target = ["user_mcps", namespace[1].strip().lower()]
        if target == namespace:
            continue
        offset = 0
        while True:
            page = await store.search_items(namespace, limit=100, offset=offset)
            for item in page["items"]:
                identity = (*target, item["key"])
                try:
                    existing = await store.get_item(target, item["key"])
                except HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
                    existing = None
                if identity in moves or existing:
                    raise ValueError(f"Resolve duplicate personal MCP before migrating: {identity}")
                moves[identity] = (namespace, target, item)
            if len(page["items"]) < 100:
                break
            offset += 100
    print(f"{'Applying' if apply else 'Dry run:'} {len(moves)} personal MCP moves")
    if apply:
        for namespace, target, item in moves.values():
            await store.put_item(target, item["key"], item["value"])
            await store.delete_item(namespace, item["key"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(normalize(args.url, args.apply))
