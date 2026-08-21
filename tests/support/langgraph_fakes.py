"""One in-memory stand-in for the ``langgraph_sdk`` client.

``FakeLangGraphClient`` exposes the four sub-clients the agent actually uses --
``threads``, ``runs``, ``store`` and ``crons`` -- backed by plain dicts. Every
sub-client appends to the owning client's :attr:`FakeLangGraphClient.calls`
log, so a test can assert the *order* of operations across sub-clients without
wiring up its own capture list, and each sub-client also keeps the narrower
per-operation lists (``created``/``updates``/``puts``/...) that most
assertions want.

Seed data through the constructor; reach for the public attributes
(``threads.metadata``, ``runs.cancel_error``, ...) for the one-off knobs a
single test needs.

**Missing reads.** ``threads.get`` raises like the real SDK: an
``httpx.HTTPStatusError`` carrying a 404 response. The store instead reads a
missing item as ``None`` by default, because that is what production code
behind :mod:`agent.store` is written against; pass ``missing="404"`` to get the
SDK's raising behaviour and exercise the not-found branches.
"""

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Literal

import httpx

Thread = dict[str, Any]
Run = dict[str, Any]
Cron = dict[str, Any]
Namespace = tuple[str, ...]
Call = tuple[str, dict[str, Any]]


def not_found(what: str) -> httpx.HTTPStatusError:
    """The error the real SDK raises for a resource that does not exist."""
    request = httpx.Request("GET", f"http://langgraph.test/{what}")
    return httpx.HTTPStatusError(
        f"{what} not found",
        request=request,
        response=httpx.Response(404, request=request),
    )


class _SubClient:
    def __init__(self, calls: list[Call], prefix: str) -> None:
        self._calls = calls
        self._prefix = prefix

    def _record(self, method: str, **kwargs: Any) -> None:
        self._calls.append((f"{self._prefix}.{method}", kwargs))


class FakeThreads(_SubClient):
    """Threads keyed by id.

    ``metadata`` is the metadata every *unseeded* thread id resolves to; it is
    the record's own dict, so a test can mutate it between calls and the next
    ``get`` sees the change. Leave it ``None`` to make unseeded ids 404.

    ``get`` returns a snapshot, not the stored record: a later ``update`` must
    not retroactively change a thread a caller already fetched, exactly as two
    separate calls to the real service behave.

    ``search`` pages over the threads seeded at construction. Records added
    afterwards -- by ``create``, or by assigning into ``threads`` -- are
    reachable by id but not by search, which is how a test gives a caller a
    thread it may fetch but must not list.
    """

    def __init__(
        self,
        calls: list[Call],
        *,
        threads: Sequence[Thread] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "idle",
        messages: Sequence[dict[str, Any]] | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(calls, "threads")
        if state is not None and messages is not None:
            raise ValueError("pass either `state` or `messages`, not both")
        self.threads: dict[str, Thread] = {
            str(thread["thread_id"]): thread for thread in (threads or [])
        }
        self.search_results: list[Thread] = list(threads or [])
        self.metadata = metadata
        self.status = status
        self.state: dict[str, Any] = (
            state if state is not None else {"values": {"messages": list(messages or [])}}
        )
        self.created: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.searches: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.get_error: BaseException | None = None

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.state["values"]["messages"]

    @property
    def updates(self) -> list[dict[str, Any]]:
        """Just the metadata patches, for the common case that ignores the id."""
        return [call["metadata"] for call in self.update_calls]

    def _resolve(self, thread_id: str) -> Thread | None:
        thread = self.threads.get(thread_id)
        if thread is not None:
            return thread
        if self.metadata is None:
            return None
        thread = {"thread_id": thread_id, "metadata": self.metadata, "status": self.status}
        self.threads[thread_id] = thread
        return thread

    async def create(
        self,
        *,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Thread:
        record = {"thread_id": thread_id, "metadata": dict(metadata or {}), **kwargs}
        self.created.append(record)
        self._record("create", **record)
        thread: Thread = {
            "thread_id": thread_id,
            "metadata": dict(metadata or {}),
            "status": self.status,
        }
        if thread_id is not None:
            self.threads[thread_id] = thread
        return thread

    async def get(self, thread_id: str) -> Thread:
        self._record("get", thread_id=thread_id)
        if self.get_error is not None:
            raise self.get_error
        thread = self._resolve(thread_id)
        if thread is None:
            raise not_found(f"thread {thread_id}")
        return {**thread, "metadata": dict(thread.get("metadata") or {})}

    async def update(
        self, *, thread_id: str, metadata: dict[str, Any] | None = None, **kwargs: Any
    ) -> Thread | None:
        self._record("update", thread_id=thread_id, metadata=metadata, **kwargs)
        self.update_calls.append({"thread_id": thread_id, "metadata": dict(metadata or {})})
        thread = self._resolve(thread_id)
        if thread is not None and metadata:
            thread.setdefault("metadata", {}).update(metadata)
        return thread

    async def delete(self, thread_id: str) -> None:
        self._record("delete", thread_id=thread_id)
        self.deleted.append(thread_id)
        self.threads.pop(thread_id, None)

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[Thread]:
        """One page of the seeded threads, in seeding order.

        ``metadata`` is recorded but not applied as a filter: the callers under
        test build the filter themselves and the assertions are about what they
        asked for, not about re-implementing the platform's matching.
        """
        call = {"metadata": metadata, "limit": limit, "offset": offset, **kwargs}
        self.searches.append(call)
        self._record("search", **call)
        return self.search_results[offset : offset + limit]

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        self._record("get_state", thread_id=thread_id)
        return self.state


class FakeRuns(_SubClient):
    """Runs listed per thread.

    ``runs`` is either one list shared by every thread or a mapping from thread
    id to that thread's runs. ``list`` filters on ``status`` when the caller
    passes one, the way the platform does.
    """

    def __init__(
        self,
        calls: list[Call],
        *,
        runs: Sequence[Run] | Mapping[str, Sequence[Run]] | None = None,
        run_id: str = "run-1",
    ) -> None:
        super().__init__(calls, "runs")
        self.runs = runs if runs is not None else []
        self.run_id = run_id
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.create_error: BaseException | None = None
        self.cancel_error: BaseException | None = None

    def _for_thread(self, thread_id: str) -> list[Run]:
        if isinstance(self.runs, Mapping):
            return list(self.runs.get(thread_id, []))
        return list(self.runs)

    async def create(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, str]:
        record = {"thread_id": thread_id, "assistant_id": assistant_id, **kwargs}
        self.created.append(record)
        self._record("create", **record)
        if self.create_error is not None:
            raise self.create_error
        return {"run_id": self.run_id}

    async def list(
        self, thread_id: str, *, limit: int = 10, status: str | None = None, **kwargs: Any
    ) -> list[Run]:
        call = {"thread_id": thread_id, "limit": limit, "status": status, **kwargs}
        self.list_calls.append(call)
        self._record("list", **call)
        runs = self._for_thread(thread_id)
        if status is not None:
            runs = [run for run in runs if run.get("status") == status]
        return runs[:limit]

    async def get(self, thread_id: str, run_id: str) -> Run:
        self._record("get", thread_id=thread_id, run_id=run_id)
        for run in self._for_thread(thread_id):
            if run.get("run_id") == run_id or run.get("id") == run_id:
                return run
        raise not_found(f"run {run_id}")

    async def cancel(self, thread_id: str, run_id: str, **kwargs: Any) -> None:
        record = {"thread_id": thread_id, "run_ids": [run_id], **kwargs}
        self.cancelled.append(record)
        self._record("cancel", **record)
        if self.cancel_error is not None:
            raise self.cancel_error

    async def cancel_many(self, **kwargs: Any) -> None:
        self.cancelled.append(dict(kwargs))
        self._record("cancel_many", **kwargs)
        if self.cancel_error is not None:
            raise self.cancel_error

    async def wait(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        record = {"thread_id": thread_id, "assistant_id": assistant_id, **kwargs}
        self.created.append(record)
        self._record("wait", **record)
        return {"run_id": self.run_id}


class FakeStore(_SubClient):
    """Items keyed by ``(namespace, key)``, holding the unwrapped value.

    ``items`` is the seed and stays readable/writable for assertions. Reads
    return the SDK's ``{"value": ...}`` envelope.
    """

    def __init__(
        self,
        calls: list[Call],
        *,
        items: Mapping[tuple[Namespace, str], dict[str, Any]] | None = None,
        missing: Literal["none", "404"] = "none",
    ) -> None:
        super().__init__(calls, "store")
        self.items: dict[tuple[Namespace, str], dict[str, Any]] = dict(items or {})
        self.missing = missing
        self.puts: list[tuple[Namespace, str, dict[str, Any]]] = []
        self.deleted: list[tuple[Namespace, str]] = []

    async def get_item(self, namespace: Sequence[str], key: str) -> dict[str, Any] | None:
        self._record("get_item", namespace=tuple(namespace), key=key)
        value = self.items.get((tuple(namespace), key))
        if value is None:
            if self.missing == "404":
                raise not_found(f"item {'/'.join(namespace)}/{key}")
            return None
        return {"value": value}

    async def put_item(self, namespace: Sequence[str], key: str, value: dict[str, Any]) -> None:
        self._record("put_item", namespace=tuple(namespace), key=key, value=value)
        self.puts.append((tuple(namespace), key, value))
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: Sequence[str], key: str) -> None:
        self._record("delete_item", namespace=tuple(namespace), key=key)
        self.deleted.append((tuple(namespace), key))
        self.items.pop((tuple(namespace), key), None)

    async def search_items(
        self,
        namespace: Sequence[str],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, list[dict[str, Any]]]:
        self._record(
            "search_items", namespace=tuple(namespace), filter=filter, limit=limit, offset=offset
        )
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}

    async def aget(self, namespace: Sequence[str], key: str) -> SimpleNamespace | None:
        """The ``BaseStore`` spelling used from inside a graph run."""
        self._record("aget", namespace=tuple(namespace), key=key)
        value = self.items.get((tuple(namespace), key))
        return None if value is None else SimpleNamespace(value=value)

    async def adelete(self, namespace: Sequence[str], key: str) -> None:
        self._record("adelete", namespace=tuple(namespace), key=key)
        self.deleted.append((tuple(namespace), key))
        self.items.pop((tuple(namespace), key), None)


class FakeCrons(_SubClient):
    """Crons in a list. ``cron_id`` is a template: ``{n}`` is the 1-based index."""

    def __init__(
        self,
        calls: list[Call],
        *,
        crons: Sequence[Cron] | None = None,
        cron_id: str = "cron-{n}",
    ) -> None:
        super().__init__(calls, "crons")
        self.crons: list[Cron] = list(crons or [])
        self.cron_id = cron_id
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.search_calls: list[dict[str, Any]] = []

    def _next_id(self) -> str:
        return self.cron_id.format(n=len(self.created) + 1)

    def _create(self, record: dict[str, Any]) -> dict[str, str]:
        cron_id = self._next_id()
        self.created.append({"cron_id": cron_id, **record})
        self._record("create", cron_id=cron_id, **record)
        self.crons.append({"cron_id": cron_id, **record})
        return {"cron_id": cron_id}

    async def create(self, assistant_id: str, **kwargs: Any) -> dict[str, str]:
        return self._create({"assistant_id": assistant_id, **kwargs})

    async def create_for_thread(
        self, thread_id: str, assistant_id: str, **kwargs: Any
    ) -> dict[str, str]:
        return self._create({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[Cron]:
        call = {"metadata": metadata, "limit": limit, "offset": offset}
        self.search_calls.append(call)
        self._record("search", **call, **kwargs)
        matches = [
            cron
            for cron in self.crons
            if not metadata
            or all(
                (cron.get("metadata") or {}).get(key) == value for key, value in metadata.items()
            )
        ]
        return matches[offset : offset + limit]

    async def delete(self, cron_id: str) -> None:
        self._record("delete", cron_id=cron_id)
        self.deleted.append(cron_id)
        self.crons = [cron for cron in self.crons if cron.get("cron_id") != cron_id]


class FakeLangGraphClient:
    """Stand-in for ``langgraph_sdk.client.LangGraphClient``."""

    def __init__(
        self,
        *,
        threads: Sequence[Thread] | None = None,
        thread_metadata: dict[str, Any] | None = None,
        thread_status: str = "idle",
        messages: Sequence[dict[str, Any]] | None = None,
        state: dict[str, Any] | None = None,
        runs: Sequence[Run] | Mapping[str, Sequence[Run]] | None = None,
        run_id: str = "run-1",
        items: Mapping[tuple[Namespace, str], dict[str, Any]] | None = None,
        missing: Literal["none", "404"] = "none",
        crons: Sequence[Cron] | None = None,
        cron_id: str = "cron-{n}",
    ) -> None:
        self.calls: list[Call] = []
        self.threads = FakeThreads(
            self.calls,
            threads=threads,
            metadata=thread_metadata,
            status=thread_status,
            messages=messages,
            state=state,
        )
        self.runs = FakeRuns(self.calls, runs=runs, run_id=run_id)
        self.store = FakeStore(self.calls, items=items, missing=missing)
        self.crons = FakeCrons(self.calls, crons=crons, cron_id=cron_id)

    @property
    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]
