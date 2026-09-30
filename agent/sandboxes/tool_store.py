"""LangGraph Store access for tools executed outside a graph worker."""

from collections.abc import Iterable
from datetime import datetime

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)
from langgraph_sdk import get_client


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class ToolStore(BaseStore):
    def batch(self, ops: Iterable[Op]) -> list[Result]:
        raise NotImplementedError("Use abatch")

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        client = get_client().store
        results: list[Result] = []
        for op in ops:
            if isinstance(op, GetOp):
                try:
                    item = await client.get_item(op.namespace, op.key)
                except Exception as exc:
                    if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                        results.append(None)
                        continue
                    raise
                results.append(
                    Item(
                        namespace=tuple(item["namespace"]),
                        key=item["key"],
                        value=item["value"],
                        created_at=_timestamp(item["created_at"]),
                        updated_at=_timestamp(item["updated_at"]),
                    )
                )
            elif isinstance(op, PutOp):
                if op.value is None:
                    await client.delete_item(op.namespace, op.key)
                else:
                    await client.put_item(
                        op.namespace,
                        op.key,
                        op.value,
                        index=op.index,
                        ttl=int(op.ttl) if op.ttl is not None else None,
                    )
                results.append(None)
            elif isinstance(op, SearchOp):
                response = await client.search_items(
                    op.namespace_prefix,
                    filter=op.filter,
                    limit=op.limit,
                    offset=op.offset,
                    query=op.query,
                )
                results.append(
                    [
                        SearchItem(
                            namespace=tuple(item["namespace"]),
                            key=item["key"],
                            value=item["value"],
                            created_at=_timestamp(item["created_at"]),
                            updated_at=_timestamp(item["updated_at"]),
                            score=item.get("score"),
                        )
                        for item in response["items"]
                    ]
                )
            elif isinstance(op, ListNamespacesOp):
                matches = {
                    match.match_type: list(match.path) for match in op.match_conditions or ()
                }
                response = await client.list_namespaces(
                    prefix=matches.get("prefix"),
                    suffix=matches.get("suffix"),
                    max_depth=op.max_depth,
                    limit=op.limit,
                    offset=op.offset,
                )
                results.append([tuple(namespace) for namespace in response["namespaces"]])
        return results
