"""Where a thread's runs live: this deployment's Agent Server, or Managed Deep Agents.

Both keep a copy of the thread's metadata for their runs and traces; the index in
PostgreSQL answers every thread read. A thread starts on the local Agent Server,
which also runs the graphs Managed Deep Agents does not host.

MDA scopes threads by owner. The backend reads through a second client that also
says ``x-auth-scheme: langsmith``, as LangSmith Studio does, so it can read the
threads an MDA channel owns too; every write goes through the owner-scoped one. A
thread an MDA channel owns stays its conversation's: the backend starts, answers
and stops runs there through MDA's trusted-backend ingress, naming that conversation.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Final

import httpx
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langsmith.utils import get_tracer_project
from managed_deepagents._agent_auth import AgentAuth, AgentAuthContext
from managed_deepagents._connections import (
    PLATFORM_API_URL_ENV,
    TRUSTED_BACKEND_PROVIDER,
    resolve_agent_auth_base_url,
)
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe import dispatch
from openswe.agent_api.client import (
    MDA_GRAPH_ID,
    MDA_STUDIO_READ_HEADERS,
    mda_runtime_api_key,
    mda_runtime_url,
    remote_run_context,
)
from openswe.config import ENV
from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.index import INDEXED_FIELDS, IndexedThread, Runtime, ThreadStatus
from openswe.utils.langsmith import resolve_tenant_id

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

# MDA's channel threads are owned by ``mda:source-thread:<provider>:<conversation key>``.
_CHANNEL_OWNER_PREFIX: Final = "mda:source-thread:"
# Who the backend's runs on a channel's thread are from, as MDA records them.
BACKEND_USER: Final = "openswe:backend"
# Beside ``x-mda-user-id``, the Agent Auth principal MDA's channels stamp a run's sender with.
_PRINCIPAL_HEADER: Final = "x-mda-agent-auth-principal-id"

_object = TypeAdapter(dict[str, JsonValue])


def now_ms() -> int:
    return int(time.time() * 1000)


class _RunError(BaseModel):
    """The exception LangGraph records on a thread whose latest run failed."""

    error: str = ""
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class _ErroredThread(BaseModel):
    error: _RunError | None = None


class ChannelThreadError(RuntimeError):
    """A thread an MDA channel owns that the backend can't act on: no owner, or no secret."""


class _SdkThreads:
    """A runtime reached through the LangGraph SDK: ``client`` writes, ``reads`` reads."""

    def __init__(self, client: LangGraphClient, reads: LangGraphClient | None = None) -> None:
        self.client = client
        self.reads = reads or client

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        try:
            return _object.validate_python(dict(await self.reads.threads.get(thread_id)))
        except NotFoundError:
            return None

    async def indexed(self, thread_id: str) -> IndexedThread | None:
        """The thread as the index keeps it, read without its values."""
        found = await self.reads.threads.search(ids=[thread_id], limit=1, select=INDEXED_FIELDS)
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
        # Each channel thread's conversation on MDA, (provider, key), which never changes.
        self._channel_conversations: dict[str, tuple[str, str]] = {}
        self._http: httpx.AsyncClient | None = None
        self._read_http: httpx.AsyncClient | None = None

    def _client(self, extra: Mapping[str, str]) -> httpx.AsyncClient:
        headers = dict(extra)
        if api_key := mda_runtime_api_key():
            headers["x-api-key"] = api_key
        return httpx.AsyncClient(
            base_url=self.url, headers=headers, timeout=httpx.Timeout(30.0, read=None)
        )

    @property
    def http(self) -> httpx.AsyncClient:
        """For writes: thread changes, runs, cancels, answers to interrupts."""
        if self._http is None:
            self._http = self._client({})
        return self._http

    @property
    def read_http(self) -> httpx.AsyncClient:
        """For reads: threads, state, history, runs and their streams, channel threads too."""
        if self._read_http is None:
            self._read_http = self._client(MDA_STUDIO_READ_HEADERS)
        return self._read_http

    def sdk(self) -> LangGraphClient:
        """For writes, scoped to the threads the backend owns."""
        return get_client(url=self.url, api_key=mda_runtime_api_key())

    def read_sdk(self) -> LangGraphClient:
        """For reads, of any thread on the deployment."""
        return get_client(
            url=self.url, api_key=mda_runtime_api_key(), headers=dict(MDA_STUDIO_READ_HEADERS)
        )

    async def channel_write_headers(
        self, thread_id: str, *, user_id: str | None = None
    ) -> dict[str, str]:
        """What lets the backend start, answer and stop runs on a thread an MDA channel owns.

        MDA's trusted-backend ingress: its ingress secret, the ``MDA_INGRESS_SECRET``
        the deployment gets with the value of the ``MDA_SERVICE_SECRET`` the two share,
        with the reserved headers naming the conversation that owns the thread, read
        from MDA once. The thread keeps that owner, so the conversation's later
        messages still reach it. Raises ``ChannelThreadError`` without the secret or
        when MDA names no channel conversation as the thread's owner.

        ``user_id`` is the Open SWE user the write is for, so a person's run here has
        the principal their channel runs have (:meth:`user_principal`); without one, or
        when Agent Auth can't name it, the backend acts as itself.
        """
        secret = ENV.MDA_SERVICE_SECRET.optional()
        if not secret:
            raise ChannelThreadError("No MDA_SERVICE_SECRET to act on MDA's channel threads")
        conversation = self._channel_conversations.get(thread_id)
        if conversation is None:
            response = await self.read_http.get(f"/threads/{thread_id}")
            response.raise_for_status()
            metadata = _object.validate_python(response.json()).get("metadata")
            owner = metadata.get("owner") if isinstance(metadata, dict) else None
            if not isinstance(owner, str) or not owner.startswith(_CHANNEL_OWNER_PREFIX):
                raise ChannelThreadError(f"MDA names no conversation owning thread {thread_id}")
            provider, _, key = owner.removeprefix(_CHANNEL_OWNER_PREFIX).partition(":")
            if not provider or not key:
                raise ChannelThreadError(f"MDA names no conversation owning thread {thread_id}")
            conversation = self._channel_conversations[thread_id] = (provider, key)
        provider, key = conversation
        principal = await self.user_principal(user_id) if user_id else None
        return {
            "x-mda-ingress-secret": secret,
            "x-mda-user-id": principal or BACKEND_USER,
            **({_PRINCIPAL_HEADER: principal} if principal else {}),
            "x-mda-source-provider": provider,
            "x-mda-source-thread-id": key,
        }

    async def user_principal(self, user_id: str) -> str | None:
        """The Agent Auth principal an Open SWE user's channel runs have, or ``None``.

        The backend's ``resolve_user`` names the user (``mda/identity.py``), and MDA
        resolves that as the ``trusted_backend`` identity of the user's id in its
        workspace; this resolves the same identity, with the key the backend calls MDA
        with. The SDK caches each answer.
        """
        api_key = mda_runtime_api_key()
        workspace = ENV.MDA_WORKSPACE_ID.optional() or await resolve_tenant_id()
        extra = {"user_id": user_id}
        if not api_key or not workspace:
            logger.warning("No LangSmith key or workspace to resolve a principal", extra=extra)
            return None
        try:
            agent_auth = AgentAuth(
                AgentAuthContext(
                    base_url=resolve_agent_auth_base_url(
                        {PLATFORM_API_URL_ENV: ENV.LANGSMITH_ENDPOINT.get()}
                    ),
                    principal_id="",
                    api_key=api_key,
                    workspace_id=workspace,
                )
            )
            return await asyncio.to_thread(
                agent_auth.resolve_principal,
                {"provider": TRUSTED_BACKEND_PROVIDER, "issuer": workspace, "subject": user_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not resolve a user's principal", extra=extra, exc_info=True)
            return None

    async def ensure_remote_thread(self, thread_id: str) -> None:
        if thread_id in self._mda_threads:
            return
        await self.sdk().threads.create(thread_id=thread_id, if_exists="do_nothing")
        self._mda_threads.add(thread_id)

    async def interrupt_if_stopped(self, thread_id: str, run_id: str, *, starting_ms: int) -> None:
        """Interrupt a run whose thread was stopped while it was starting, at ``starting_ms``.

        Until MDA has the run, which takes as long as working out its catalog, a stop
        finds nothing to cancel.
        """
        thread = await index.get(thread_id)
        if thread is None or not thread.stopped_since(starting_ms):
            return
        extra = {"thread_id": thread_id, "run_id": run_id}
        logger.info("Interrupting a run its thread was stopped while it started", extra=extra)
        try:
            # A channel's thread is stopped as the backend acting in its conversation.
            headers = (
                await self.channel_write_headers(thread_id)
                if dispatch.channel_of(thread.metadata) is not None
                else {}
            )
            response = await self.http.post(
                f"/threads/{thread_id}/runs/{run_id}/cancel",
                params={"action": "interrupt"},
                headers=headers,
            )
        except httpx.HTTPError, ChannelThreadError:
            logger.warning("Could not interrupt a stopped run", extra=extra, exc_info=True)
            return
        if response.is_error:
            # A stop that came once MDA had the run may have cancelled it already.
            logger.warning(
                "Could not interrupt a stopped run",
                extra={**extra, "status_code": response.status_code},
            )

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
        channel: bool = False,
        user_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """Answer a protocol v2 ``run.start`` by creating the run here with its context.

        MDA's own ``run.start`` would drop the context, which is what carries the run token.
        On a ``channel`` thread the run starts in its conversation as ``user_id``, or as the
        backend without one (:meth:`channel_write_headers`), which raises ``ChannelThreadError``.
        """
        starting_ms = now_ms()
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
            # A client that reads the transcript instead of this stream opts out.
            "stream_resumable": params.get("stream_resumable") is not False,
            "on_completion": "keep",
            "if_not_exists": "create",
            "multitask_strategy": strategy if isinstance(strategy, str) else "reject",
        }
        # As on dispatched runs: the run-complete webhook settles the thread in the index.
        if dispatch.COMPLETION_WEBHOOK_URL:
            payload["webhook"] = dispatch.COMPLETION_WEBHOOK_URL
        if channel:
            # The channel created the thread there, as its conversation's own.
            headers = await self.channel_write_headers(thread_id, user_id=user_id)
        else:
            headers = {}
            await self.ensure_remote_thread(thread_id)
        rewritten = await self.run_payload(
            thread_id, payload, thread_metadata=thread_metadata, graph_id=graph_id
        )
        response = await self.http.post(
            f"/threads/{thread_id}/runs", json=rewritten, headers=headers
        )
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
        run_id = str(run.get("run_id") or "")
        await self.interrupt_if_stopped(thread_id, run_id, starting_ms=starting_ms)
        return {
            "type": "success",
            "id": command.get("id", 0),
            "result": {"run_id": run_id},
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
        runtime = self.mda_runtime
        return _SdkThreads(runtime.sdk(), runtime.read_sdk())

    def runs_on(self, runtime: Runtime) -> _SdkThreads:
        return self.mda if runtime == "mda" else self.local

    def copies(self, runtime: Runtime) -> list[_SdkThreads]:
        """Every runtime that keeps a copy of a thread on ``runtime``."""
        return [self.local, self.mda] if runtime == "mda" else [self.local]


RUNTIMES: Final = Runtimes()
