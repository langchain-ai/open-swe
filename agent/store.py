"""The one way to read and write the LangGraph Store.

Error policy, applied everywhere: **a missing item reads as ``None``; every
other failure raises.** A store outage is not the same thing as an empty
record, and collapsing the two hides data loss behind an empty dashboard.

Call sites that genuinely must survive an outage — the ones on the agent's
critical path, where failing a run is worse than falling back to a default —
wrap their call in their own ``try``/``except`` and say in a comment why.
That keeps the swallow visible at the point where the choice is made.

``TypedStore`` binds a namespace to a Pydantic model so reads come back
validated instead of as ``dict[str, Any]``.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

Namespace = Sequence[str]

_DEFAULT_PAGE_SIZE = 100


def store_client() -> LangGraphClient:
    """The LangGraph client every store access goes through (one patch point)."""
    return get_client()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _is_not_found(exc: httpx.HTTPStatusError) -> bool:
    return getattr(exc.response, "status_code", None) == 404


def _unwrap(item: Any) -> dict[str, Any] | None:
    """The item's ``value`` — the SDK returns dicts or objects depending on transport."""
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def get_value(namespace: Namespace, key: str) -> dict[str, Any] | None:
    try:
        item = await store_client().store.get_item(list(namespace), key)
    except httpx.HTTPStatusError as exc:
        if _is_not_found(exc):
            return None
        raise
    return _unwrap(item)


async def put_value(namespace: Namespace, key: str, value: Mapping[str, Any]) -> None:
    await store_client().store.put_item(list(namespace), key, value)


async def delete_value(namespace: Namespace, key: str) -> None:
    """Delete an item. Deleting one that is already gone is not an error."""
    try:
        await store_client().store.delete_item(list(namespace), key)
    except httpx.HTTPStatusError as exc:
        if not _is_not_found(exc):
            raise


async def _search_items(
    namespace: Namespace,
    filter: dict[str, Any] | None,
    limit: int,
    offset: int,
) -> list[Any]:
    result = await store_client().store.search_items(
        list(namespace), filter=filter, limit=limit, offset=offset
    )
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    return list(items or [])


async def search_values(
    namespace: Namespace,
    *,
    filter: dict[str, Any] | None = None,
    limit: int = _DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """One page of values in ``namespace``, in store order."""
    items = await _search_items(namespace, filter, limit, offset)
    return [value for item in items if (value := _unwrap(item)) is not None]


async def search_all_values(
    namespace: Namespace,
    *,
    filter: dict[str, Any] | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Every value in ``namespace``, paging until the store runs out."""
    values: list[dict[str, Any]] = []
    offset = 0
    while True:
        items = await _search_items(namespace, filter, page_size, offset)
        if not items:
            return values
        values.extend(value for item in items if (value := _unwrap(item)) is not None)
        if len(items) < page_size:
            return values
        offset += len(items)


RecordT = TypeVar("RecordT", bound=BaseModel)


class TypedStore[RecordT: BaseModel]:
    """A namespace whose values are validated against ``model`` on the way out.

    Records outlive the code that wrote them, so ``model`` should default every
    field it can and ignore extras. When a record still fails to validate,
    ``get`` raises — the caller asked for that one record — while the search
    methods skip it and log, so one unreadable record cannot take a whole
    listing down with it.
    """

    def __init__(self, namespace: Namespace, model: type[RecordT]) -> None:
        self.namespace = list(namespace)
        self.model = model

    async def get(self, key: str) -> RecordT | None:
        value = await get_value(self.namespace, key)
        return None if value is None else self.model.model_validate(value)

    async def put(self, key: str, record: RecordT) -> RecordT:
        await put_value(self.namespace, key, record.model_dump(mode="json"))
        return record

    async def delete(self, key: str) -> None:
        await delete_value(self.namespace, key)

    async def search(
        self,
        *,
        filter: dict[str, Any] | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[RecordT]:
        return self._parse_all(
            await search_values(self.namespace, filter=filter, limit=limit, offset=offset)
        )

    async def search_all(
        self,
        *,
        filter: dict[str, Any] | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> list[RecordT]:
        return self._parse_all(
            await search_all_values(self.namespace, filter=filter, page_size=page_size)
        )

    def _parse_all(self, values: list[dict[str, Any]]) -> list[RecordT]:
        records: list[RecordT] = []
        for value in values:
            try:
                records.append(self.model.model_validate(value))
            except ValidationError:
                logger.warning(
                    "Skipping unreadable %s record in %s",
                    self.model.__name__,
                    self.namespace,
                    exc_info=True,
                )
        return records
