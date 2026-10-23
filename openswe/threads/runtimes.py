"""Where a thread's runs live: this deployment's Agent Server, or Managed Deep Agents.

Both keep a copy of the thread's metadata for their runs and traces; the index in
PostgreSQL answers every thread read. A thread starts on the local Agent Server,
which also runs the graphs Managed Deep Agents does not host.
"""

import logging
from collections.abc import Mapping
from typing import Final

import httpx
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langsmith.utils import get_tracer_project
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe import dispatch
from openswe.remote_runtime.client import (
    MDA_GRAPH_ID,
    mda_runtime_api_key,
    mda_runtime_url,
    remote_run_context,
)
from openswe.source_context import SourceContext
from openswe.threads.index import INDEXED_FIELDS, IndexedThread, Runtime, ThreadStatus

logger = logging.getLogger(__name__)

# Marks the thread router's own calls to the local Agent Server, so they skip it.
LOCAL_HEADER: Final = "x-openswe-local-runtime"
_V3_STREAMING_KEY: Final = "__event_streaming_v2"
# The protocol v2 stream modes `run.start` uses, minus protocol-only channels the
# REST run-create schema rejects.
_V3_RUN_STREAM_MODES: Final = ["values", "updates", "messages", "custom", "tasks", "checkpoints"]
# The run metadata the graph's hooks read. Only these ride in the run token, which
# travels in a header on every hook call.
_HOOK_METADATA_KEYS: Final = ("event_match_ids", "ls_attribution_identifier")

_object = TypeAdapter(dict[str, JsonValue])


class _RunError(BaseModel):
    """The exception LangGraph records on a thread whose latest run failed."""

    error: str = ""
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class _ErroredThread(BaseModel):
    error: _RunError | None = None


class _SdkThreads:
    """A runtime reached through the LangGraph SDK."""

    def __init__(self, client: LangGraphClient) -> None:
        self.client = client

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        try:
            return _object.validate_python(dict(await self.client.threads.get(thread_id)))
        except NotFoundError:
            return None

    async def indexed(self, thread_id: str) -> IndexedThread | None:
        """The thread as the index keeps it, read without its values."""
        found = await self.client.threads.search(ids=[thread_id], limit=1, select=INDEXED_FIELDS)
        return next(
            (thread for raw in found if (thread := IndexedThread.from_agent_server(raw))), None
        )

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.create(
            thread_id=thread_id, if_exists="do_nothing", metadata=dict(metadata)
        )

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.update(thread_id, metadata=dict(metadata))

    async def delete(self, thread_id: str) -> None:
        try:
            await self.client.threads.delete(thread_id)
        except NotFoundError:
            return

    async def status(self, thread_id: str) -> ThreadStatus | None:
        """The thread's status here; one this runtime does not have has nothing running."""
        thread = await self.indexed(thread_id)
        return "idle" if thread is None else thread.status

    async def run_error(self, thread_id: str) -> str | None:
        """Why the thread's latest run here failed, or ``None`` when it recorded no error."""
        thread = await self.get(thread_id)
        failure = _ErroredThread.model_validate(thread or {}).error
        return (failure.describe() or None) if failure else None


class MdaRuntime:
    """The MDA deployment, reached for the runs of threads the index marks ``mda``."""

    def __init__(self, url: str) -> None:
        self.url = url
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

    async def ensure_remote_thread(self, thread_id: str) -> None:
        if thread_id in self._mda_threads:
            return
        await self.sdk().threads.create(thread_id=thread_id, if_exists="do_nothing")
        self._mda_threads.add(thread_id)

    async def run_payload(
        self,
        thread_id: str,
        payload: dict[str, JsonValue],
        *,
        thread_metadata: Mapping[str, JsonValue],
        graph_id: str = MDA_GRAPH_ID,
    ) -> dict[str, JsonValue]:
        """Rewrite a run-create body for the thread's MDA graph: configurable becomes context."""
        raw_metadata = payload.get("metadata")
        run_metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        config = payload.get("config")
        config = dict(config) if isinstance(config, dict) else {}
        slack_thread = SourceContext.from_metadata(thread_metadata).slack_thread
        if slack_thread is not None and (permalink := slack_thread.permalink.strip()):
            run_metadata["slack_thread_url"] = permalink
            raw_config_metadata = config.get("metadata")
            config["metadata"] = {
                **(raw_config_metadata if isinstance(raw_config_metadata, dict) else {}),
                "slack_thread_url": permalink,
            }
        raw_configurable = config.pop("configurable", None)
        configurable = dict(raw_configurable) if isinstance(raw_configurable, dict) else {}
        streaming = configurable.pop(_V3_STREAMING_KEY, None)
        assistant_id = payload.get("assistant_id")
        if not isinstance(assistant_id, str):
            raise ValueError("A run needs an assistant_id")
        context = dict(
            await remote_run_context(
                configurable,
                metadata={k: run_metadata[k] for k in _HOOK_METADATA_KEYS if k in run_metadata},
                thread_id=thread_id,
                assistant_id=assistant_id,
            )
        )
        if streaming:
            # Context replaces configurable on the server, so the stream marker rides in it.
            context[_V3_STREAMING_KEY] = True
        rewritten = {
            **payload,
            "assistant_id": graph_id,
            "context": context,
            "metadata": run_metadata,
        }
        if not payload.get("langsmith_tracer"):
            # Traced into this deployment's project, beside its in-process runs.
            rewritten["langsmith_tracer"] = {"project_name": get_tracer_project()}
        if config:
            rewritten["config"] = config
        else:
            rewritten.pop("config", None)
        return _object.validate_python(rewritten)

    async def start_command_run(
        self,
        thread_id: str,
        command: Mapping[str, JsonValue],
        params: Mapping[str, JsonValue],
        *,
        thread_metadata: Mapping[str, JsonValue],
        graph_id: str = MDA_GRAPH_ID,
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
        # As on dispatched runs: the run-complete webhook settles the thread in the index.
        if dispatch.COMPLETION_WEBHOOK_URL:
            payload["webhook"] = dispatch.COMPLETION_WEBHOOK_URL
        await self.ensure_remote_thread(thread_id)
        rewritten = await self.run_payload(
            thread_id, payload, thread_metadata=thread_metadata, graph_id=graph_id
        )
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


class Runtimes:
    """This deployment's Agent Server, and the MDA deployment."""

    def __init__(self) -> None:
        self._mda: MdaRuntime | None = None

    @property
    def local(self) -> _SdkThreads:
        # Past the thread router, straight to the Agent Server's own routes.
        return _SdkThreads(get_client(headers={LOCAL_HEADER: "1"}))

    @property
    def mda_runtime(self) -> MdaRuntime:
        url = mda_runtime_url()
        if self._mda is None or self._mda.url != url:
            self._mda = MdaRuntime(url)
        return self._mda

    @property
    def mda(self) -> _SdkThreads:
        return _SdkThreads(self.mda_runtime.sdk())

    def runs_on(self, runtime: Runtime) -> _SdkThreads:
        return self.mda if runtime == "mda" else self.local

    def copies(self, runtime: Runtime) -> list[_SdkThreads]:
        """Every runtime that keeps a copy of a thread on ``runtime``."""
        return [self.local, self.mda] if runtime == "mda" else [self.local]


RUNTIMES: Final = Runtimes()
