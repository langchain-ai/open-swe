"""Route the Agent Server API of MDA-hosted threads to the Managed Deep Agents deployment.

Threads stay on this deployment as the index everything else reads: metadata,
listing, Slack and GitHub bindings. Their runs, checkpoints and streams live on
the MDA deployment. This middleware sits in front of the core ``/threads`` and
``/runs`` routes, so the dashboard's proxies, the CLI and every backend client
(in-process or over ``LANGGRAPH_URL``) reach the right place without knowing
where a thread's runs execute.

- Starting an ``agent`` or ``reviewer`` run on a thread with no local history
  claims the thread for MDA (``runtime: mda`` in its metadata) when its person
  turned on ``run_on_mda`` (the thread's owner, else whoever started the run),
  or, for threads no person starts, when its workspace did. The run is created on the ``open-swe`` graph, with the run's
  configurable moved into a signed run token in its context. Every other thread
  runs here, so the choice is fixed for each thread's lifetime.
- Every run, state, history and stream request for a claimed thread is forwarded.
- ``GET /threads/{id}`` and ``POST /threads/search`` answer from this deployment,
  with each claimed thread's run-derived fields (status, values, interrupts,
  error) taken from MDA.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Final, cast, get_args

import httpx
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import ThreadStatus
from langsmith.utils import get_tracer_project
from pydantic import JsonValue, TypeAdapter
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.api.agent_server_auth import backend_call
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.remote_runtime.asgi import (
    configurable_of,
    forward,
    json_body,
    read_body,
    replay,
    send_json,
)
from openswe.remote_runtime.client import (
    HOSTED_GRAPHS,
    MDA_GRAPH_ID,
    MDA_RUNTIME,
    RUNTIME_METADATA_KEY,
    mda_runtime_api_key,
    mda_runtime_url,
    remote_run_context,
)
from openswe.run_config import RunConfig
from openswe.threads.oswe_thread import OsweThreadMetadata
from openswe.users import User

logger = logging.getLogger(__name__)

# Marks this middleware's own reads of the local thread, so they skip it.
LOCAL_HEADER: Final = "x-openswe-local-runtime"
_V3_STREAMING_KEY: Final = "__event_streaming_v2"
# The protocol v2 stream modes `run.start` uses, minus protocol-only channels the
# REST run-create schema rejects.
_V3_RUN_STREAM_MODES: Final = ["values", "updates", "messages", "custom", "tasks", "checkpoints"]
_RUN_DERIVED_FIELDS: Final = ("status", "values", "interrupts", "error")
_UNCLAIMED_TTL_SECONDS: Final = 5.0
_REMOTE_PAGE: Final = 500

_THREAD_PATH: Final = re.compile(
    r"^(?:/noauth)?/threads/(?P<thread_id>[0-9a-fA-F-]{36})(?P<rest>/.*)?$"
)
_CREATE_RUN_PATH: Final = re.compile(r"^/runs(?:/stream|/wait|/crons)?$")
_SEARCH_PATH: Final = re.compile(r"^(?:/noauth)?/threads/search$")
_CANCEL_MANY_PATH: Final = re.compile(r"^(?:/noauth)?/runs/cancel$")

_object = TypeAdapter(dict[str, JsonValue])
_THREAD_STATUSES: Final = frozenset(get_args(ThreadStatus))


class _Unclaimed:
    """Threads recently seen running here, so busy local threads skip the lookup."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def fresh(self, thread_id: str) -> bool:
        seen = self._seen.get(thread_id)
        return seen is not None and time.monotonic() - seen < _UNCLAIMED_TTL_SECONDS

    def remember(self, thread_id: str) -> None:
        if len(self._seen) > 4096:
            self._seen.clear()
        self._seen[thread_id] = time.monotonic()

    def forget(self, thread_id: str) -> None:
        self._seen.pop(thread_id, None)


class MdaRuntime:
    """The MDA deployment, and which of this deployment's threads it hosts."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._claimed: set[str] = set()
        self._unclaimed = _Unclaimed()
        self._mda_threads: set[str] = set()
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            headers = {}
            if api_key := mda_runtime_api_key():
                headers["x-api-key"] = api_key
            self._http = httpx.AsyncClient(
                base_url=self.url, headers=headers, timeout=httpx.Timeout(30.0, read=None)
            )
        return self._http

    def sdk(self) -> LangGraphClient:
        return get_client(url=self.url, api_key=mda_runtime_api_key())

    @staticmethod
    def local() -> LangGraphClient:
        return get_client(headers={LOCAL_HEADER: "1"})

    async def local_thread(self, thread_id: str) -> dict[str, JsonValue] | None:
        try:
            thread = await self.local().threads.get(thread_id)
        except NotFoundError:
            return None
        return _object.validate_python(dict(thread))

    def claimed_in(self, thread: Mapping[str, JsonValue]) -> bool:
        metadata = thread.get("metadata")
        return isinstance(metadata, dict) and metadata.get(RUNTIME_METADATA_KEY) == MDA_RUNTIME

    async def hosts(self, thread_id: str) -> bool:
        if thread_id in self._claimed:
            return True
        if self._unclaimed.fresh(thread_id):
            return False
        thread = await self.local_thread(thread_id)
        if thread is not None and self.claimed_in(thread):
            self._claimed.add(thread_id)
            return True
        self._unclaimed.remember(thread_id)
        return False

    async def claim(self, thread_id: str, configurable: Mapping[str, JsonValue]) -> bool:
        """Claim a fresh ``thread_id`` for MDA if the person it runs for chose MDA."""
        if await self.hosts(thread_id):
            return True
        thread = await self.local_thread(thread_id)
        if thread is not None and (thread.get("values") or thread.get("status") != "idle"):
            # Runs already here keep running where their checkpoints are.
            return False
        if not await runs_on_mda(thread, configurable):
            return False
        if thread is None:
            # Run creation with `if_not_exists=create`: the index thread is made here first.
            await self.local().threads.create(
                thread_id=thread_id,
                if_exists="do_nothing",
                metadata={RUNTIME_METADATA_KEY: MDA_RUNTIME},
            )
        else:
            await self.local().threads.update(
                thread_id, metadata={RUNTIME_METADATA_KEY: MDA_RUNTIME}
            )
        self._claimed.add(thread_id)
        self._unclaimed.forget(thread_id)
        logger.info("Claimed thread for Managed Deep Agents", extra={"thread_id": thread_id})
        return True

    async def ensure_remote_thread(self, thread_id: str) -> None:
        if thread_id in self._mda_threads:
            return
        await self.sdk().threads.create(thread_id=thread_id, if_exists="do_nothing")
        self._mda_threads.add(thread_id)

    async def run_payload(
        self, thread_id: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Rewrite a run-create body for the ``open-swe`` graph: configurable becomes context."""
        config = payload.get("config")
        config = dict(config) if isinstance(config, dict) else {}
        raw_configurable = config.pop("configurable", None)
        configurable = dict(raw_configurable) if isinstance(raw_configurable, dict) else {}
        streaming = configurable.pop(_V3_STREAMING_KEY, None)
        assistant_id = payload.get("assistant_id")
        if not isinstance(assistant_id, str):
            raise ValueError("A run needs an assistant_id")
        context = dict(
            await remote_run_context(configurable, thread_id=thread_id, assistant_id=assistant_id)
        )
        if streaming:
            # Context replaces configurable on the server, so the stream marker rides in it.
            context[_V3_STREAMING_KEY] = True
        rewritten = {**payload, "assistant_id": MDA_GRAPH_ID, "context": context}
        if not payload.get("langsmith_tracer"):
            # Traced into this deployment's project, beside its in-process runs.
            rewritten["langsmith_tracer"] = {"project_name": get_tracer_project()}
        if config:
            rewritten["config"] = config
        else:
            rewritten.pop("config", None)
        return _object.validate_python(rewritten)

    async def start_command_run(
        self, thread_id: str, command: Mapping[str, JsonValue], params: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Answer a protocol v2 ``run.start`` by creating the run here with its context.

        MDA's own ``run.start`` would drop the context, which is what carries the run token.
        """
        config = params.get("config")
        configurable = config.get("configurable") if isinstance(config, dict) else None
        run_config: dict[str, JsonValue] = {
            **(config if isinstance(config, dict) else {}),
            "configurable": {
                **(configurable if isinstance(configurable, dict) else {}),
                _V3_STREAMING_KEY: True,
                "thread_id": thread_id,
            },
        }
        strategy = params.get("multitaskStrategy") or params.get("multitask_strategy")
        payload: dict[str, JsonValue] = {
            "assistant_id": params["assistant_id"],
            "input": params.get("input"),
            "config": run_config,
            "metadata": params.get("metadata"),
            "stream_mode": list(_V3_RUN_STREAM_MODES),
            "stream_subgraphs": True,
            "stream_resumable": True,
            "on_completion": "keep",
            "if_not_exists": "create",
            "multitask_strategy": strategy if isinstance(strategy, str) else "reject",
        }
        await self.ensure_remote_thread(thread_id)
        rewritten = await self.run_payload(thread_id, payload)
        response = await self.http.post(f"/threads/{thread_id}/runs", json=rewritten)
        if response.is_error:
            logger.warning(
                "Starting an MDA run failed",
                extra={"thread_id": thread_id, "status_code": response.status_code},
            )
            return {
                "type": "error",
                "id": command.get("id", 0),
                "error": "unknown_error",
                "message": response.text[:500],
            }
        run = _object.validate_python(response.json())
        return {
            "type": "success",
            "id": command.get("id", 0),
            "result": {"run_id": str(run.get("run_id") or "")},
        }

    async def merged_thread(self, thread: dict[str, JsonValue]) -> dict[str, JsonValue]:
        thread_id = thread.get("thread_id")
        if not isinstance(thread_id, str):
            return thread
        try:
            remote = await self.sdk().threads.get(thread_id)
        except NotFoundError:
            # Claimed but never run: the index thread is all there is.
            return {**thread, "status": "idle"}
        return {**thread, **{key: remote.get(key) for key in _RUN_DERIVED_FIELDS}}

    async def merged_threads(
        self, threads: list[dict[str, JsonValue]]
    ) -> list[dict[str, JsonValue]]:
        """``threads`` with each claimed one's run-derived fields taken from MDA, concurrently."""

        async def merged(thread: dict[str, JsonValue]) -> dict[str, JsonValue]:
            return await self.merged_thread(thread) if self.claimed_in(thread) else thread

        return list(await asyncio.gather(*(merged(thread) for thread in threads)))

    async def claimed_with_status(self, status: ThreadStatus) -> set[str]:
        """Every MDA thread in ``status``; MDA hosts only threads this deployment claimed."""
        ids: set[str] = set()
        offset = 0
        while True:
            page = await self.sdk().threads.search(status=status, limit=_REMOTE_PAGE, offset=offset)
            ids.update(thread["thread_id"] for thread in page)
            if len(page) < _REMOTE_PAGE:
                return ids
            offset += _REMOTE_PAGE

    async def claimed_not_idle(self) -> set[str]:
        statuses = [s for s in _THREAD_STATUSES if s != "idle"]
        found = await asyncio.gather(
            *(self.claimed_with_status(cast("ThreadStatus", s)) for s in statuses)
        )
        return set().union(*found)


async def runs_on_mda(
    thread: Mapping[str, JsonValue] | None, configurable: Mapping[str, JsonValue]
) -> bool:
    """Whether a fresh thread runs on MDA: its person's ``run_on_mda``, else its workspace's.

    The person is the thread's owner, else whoever started the run. Threads no
    person starts (API keys, workflows, system reviews) follow their workspace.
    """
    raw_metadata = thread.get("metadata") if thread is not None else None
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    owner = OsweThreadMetadata.model_validate(metadata)
    run = RunConfig.parse(dict(configurable))
    login = (owner.owner_login or run.github_login or "").strip()
    try:
        if login:
            return (await User.preferences_for_login(login)).run_on_mda
        workspace = run.workspace_slug or metadata.get("workspace")
        settings = await cached_workspace_settings(
            workspace if isinstance(workspace, str) else None
        )
        return settings.get("run_on_mda") is True
    except Exception:
        logger.warning(
            "Could not load the Managed Deep Agents setting; running here",
            exc_info=True,
            extra={"owner_login": login},
        )
        return False


def _may_route(scope: Scope) -> bool:
    """Whether a request is one this proxy may send to MDA."""
    path: str = scope["path"]
    if _THREAD_PATH.match(path):
        return True
    return scope["method"] == "POST" and bool(
        _SEARCH_PATH.match(path) or _CANCEL_MANY_PATH.match(path)
    )


class MdaThreadProxy:
    """ASGI middleware sending MDA-hosted threads' Agent Server calls to MDA."""

    def __init__(self, app: ASGIApp) -> None:
        from langgraph_api.auth.middleware import auth_middleware

        self.app = app
        self._runtime: MdaRuntime | None = None
        # The Agent Server authenticates at its own routes, behind app middleware
        # like this one, so requests handled here pass the same check first.
        self._authenticated: ASGIApp = auth_middleware.cls(
            self._route, *auth_middleware.args, **auth_middleware.kwargs
        )

    def runtime(self) -> MdaRuntime | None:
        url = mda_runtime_url()
        if url is None:
            return None
        if self._runtime is None or self._runtime.url != url:
            self._runtime = MdaRuntime(url)
        return self._runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or Headers(scope=scope).get(LOCAL_HEADER)
            or not _may_route(scope)
            or self.runtime() is None
        ):
            await self.app(scope, receive, send)
            return
        await self._authenticated(scope, receive, send)

    async def _route(self, scope: Scope, receive: Receive, send: Send) -> None:
        runtime = self.runtime()
        if runtime is None or not backend_call(scope):
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        thread_match = _THREAD_PATH.match(path)
        is_search = bool(_SEARCH_PATH.match(path)) and scope["method"] == "POST"
        is_cancel_many = bool(_CANCEL_MANY_PATH.match(path)) and scope["method"] == "POST"
        body = await read_body(receive)
        replayed = replay(body, receive)
        if is_search:
            await self._search(runtime, scope, replayed, send, body)
        elif is_cancel_many:
            await self._cancel_many(runtime, scope, replayed, send, body)
        elif thread_match is not None:
            await self._thread(
                runtime,
                scope,
                replayed,
                send,
                body,
                thread_match["thread_id"],
                thread_match["rest"],
            )

    async def _thread(
        self,
        runtime: MdaRuntime,
        scope: Scope,
        receive: Receive,
        send: Send,
        body: bytes,
        thread_id: str,
        rest: str | None,
    ) -> None:
        method: str = scope["method"]
        if rest is None or rest == "":
            await self._thread_itself(runtime, scope, receive, send, thread_id)
            return
        if method == "POST" and rest == "/commands":
            await self._command(runtime, scope, receive, send, body, thread_id)
            return
        if method == "POST" and _CREATE_RUN_PATH.match(rest):
            payload = json_body(body)
            assistant_id = payload.get("assistant_id") if payload else None
            if (
                payload is not None
                and isinstance(assistant_id, str)
                and assistant_id in HOSTED_GRAPHS
                and await runtime.claim(thread_id, configurable_of(payload))
            ):
                await runtime.ensure_remote_thread(thread_id)
                rewritten = await runtime.run_payload(thread_id, payload)
                await forward(runtime.http, scope, send, json.dumps(rewritten).encode())
                return
        if await runtime.hosts(thread_id):
            await runtime.ensure_remote_thread(thread_id)
            await forward(runtime.http, scope, send, body)
            return
        await self.app(scope, receive, send)

    async def _thread_itself(
        self, runtime: MdaRuntime, scope: Scope, receive: Receive, send: Send, thread_id: str
    ) -> None:
        method: str = scope["method"]
        if method == "GET" and await runtime.hosts(thread_id):
            thread = await runtime.local_thread(thread_id)
            if thread is None:
                await send_json(send, 404, {"detail": f"Thread {thread_id} not found"})
                return
            await send_json(send, 200, await runtime.merged_thread(thread))
            return
        if method == "DELETE" and await runtime.hosts(thread_id):
            try:
                await runtime.sdk().threads.delete(thread_id)
            except NotFoundError:
                pass
            except Exception:
                logger.warning(
                    "Deleting the MDA thread failed", extra={"thread_id": thread_id}, exc_info=True
                )
        await self.app(scope, receive, send)

    async def _command(
        self,
        runtime: MdaRuntime,
        scope: Scope,
        receive: Receive,
        send: Send,
        body: bytes,
        thread_id: str,
    ) -> None:
        command = json_body(body)
        params = command.get("params") if command else None
        is_hosted_start = (
            command is not None
            and command.get("method") == "run.start"
            and isinstance(params, dict)
            and params.get("assistant_id") in HOSTED_GRAPHS
        )
        if is_hosted_start and command is not None and isinstance(params, dict):
            if await runtime.claim(thread_id, configurable_of(params)):
                await self._start_run(runtime, send, thread_id, command, params)
                return
        elif await runtime.hosts(thread_id):
            await runtime.ensure_remote_thread(thread_id)
            await forward(runtime.http, scope, send, body)
            return
        await self.app(scope, receive, send)

    async def _start_run(
        self,
        runtime: MdaRuntime,
        send: Send,
        thread_id: str,
        command: Mapping[str, JsonValue],
        params: Mapping[str, JsonValue],
    ) -> None:
        await send_json(send, 200, await runtime.start_command_run(thread_id, command, params))

    async def _search(
        self, runtime: MdaRuntime, scope: Scope, receive: Receive, send: Send, body: bytes
    ) -> None:
        """Search here, with claimed threads' status (and status filter) answered by MDA."""
        query = json_body(body)
        if query is None:
            await self.app(scope, receive, send)
            return
        status = query.get("status")
        if status in _THREAD_STATUSES:
            threads = await self._search_by_status(runtime, query, cast("ThreadStatus", status))
        else:
            threads = _object_list(await runtime.local().http.post("/threads/search", json=query))
        await send_json(send, 200, await runtime.merged_threads(threads))

    async def _search_by_status(
        self, runtime: MdaRuntime, query: dict[str, JsonValue], status: ThreadStatus
    ) -> list[dict[str, JsonValue]]:
        """One page of a status search, where claimed threads count by their MDA status.

        Claimed threads are always idle here, and only the few not idle on MDA need
        asking about: an idle search drops those, and any other search adds the ones
        in that status. The page is cut after merging, so offsets stay consistent.
        """
        local = runtime.local()
        limit = _int_param(query.get("limit"), 10)
        offset = _int_param(query.get("offset"), 0)
        unfiltered = {k: v for k, v in query.items() if k not in {"status", "limit", "offset"}}
        if status == "idle":
            busy_elsewhere = await runtime.claimed_not_idle()
            window = {**query, "offset": 0, "limit": offset + limit + len(busy_elsewhere)}
            hits = [
                thread
                for thread in _object_list(await local.http.post("/threads/search", json=window))
                if thread.get("thread_id") not in busy_elsewhere
            ]
            return hits[offset : offset + limit]
        window = {**query, "offset": 0, "limit": offset + limit}
        hits = [
            thread
            for thread in _object_list(await local.http.post("/threads/search", json=window))
            if not runtime.claimed_in(thread)
        ]
        claimed_ids = sorted(await runtime.claimed_with_status(status))
        wanted = query.get("ids")
        if isinstance(wanted, list):
            claimed_ids = [thread_id for thread_id in claimed_ids if thread_id in wanted]
        if claimed_ids:
            claimed = await local.http.post(
                "/threads/search",
                json={**unfiltered, "ids": claimed_ids, "limit": len(claimed_ids), "offset": 0},
            )
            hits.extend(thread for thread in _object_list(claimed) if runtime.claimed_in(thread))
            hits.sort(key=_sort_key(query), reverse=_descending(query))
        return hits[offset : offset + limit]

    async def _cancel_many(
        self, runtime: MdaRuntime, scope: Scope, receive: Receive, send: Send, body: bytes
    ) -> None:
        payload = json_body(body)
        thread_id = payload.get("thread_id") if payload else None
        if isinstance(thread_id, str) and await runtime.hosts(thread_id):
            await forward(runtime.http, scope, send, body)
            return
        await self.app(scope, receive, send)


def _int_param(value: JsonValue, default: int) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def _descending(query: Mapping[str, JsonValue]) -> bool:
    order = query.get("sort_order")
    return not (isinstance(order, str) and order.lower() == "asc")


def _sort_key(query: Mapping[str, JsonValue]) -> Callable[[Mapping[str, JsonValue]], str]:
    """The Agent Server's thread search order, which defaults to newest created first."""
    sort_by = query.get("sort_by")
    field = sort_by if isinstance(sort_by, str) and sort_by else "created_at"

    def key(thread: Mapping[str, JsonValue]) -> str:
        value = thread.get(field)
        return "" if value is None else str(value)

    return key


def _object_list(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [_object.validate_python(item) for item in value if isinstance(item, dict)]
