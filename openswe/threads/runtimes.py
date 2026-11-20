"""Where a thread's runs live: this deployment's Agent Server, or Managed Deep Agents.

The index in PostgreSQL answers every thread read. Every new thread is created and
run on MDA; the local Agent Server keeps only the threads made before that, for
reads, and refuses to create any thread or run (``openswe.threads.local_writes``).

MDA scopes threads by owner. The backend reads through a second client that also
says ``x-auth-scheme: langsmith``, as LangSmith Studio does, so it can read the
threads an MDA channel owns too; every write goes through the owner-scoped one. A
thread an MDA channel owns stays its conversation's: the backend starts, answers
and stops runs there through MDA's trusted-backend ingress, naming that conversation.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal
from urllib.request import getproxies, proxy_bypass

import httpx2
import orjson
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from managed_deepagents._agent_auth import AgentAuth, AgentAuthContext
from managed_deepagents._channels.binding import handler_threads_key
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
from openswe.agent_api.connections import ACTING_USER_KEY, SENDER_USER_KEY
from openswe.config import ENV
from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.index import INDEXED_FIELDS, IndexedThread, Runtime, ThreadStatus
from openswe.threads.start_request import RunRefused, StartRequest
from openswe.trace_metadata import (
    BILLING_FIELDS,
    BILLING_USER_FIELDS,
    RunOrigin,
    TraceVisibility,
)
from openswe.utils.http_pool import KEEPALIVE_SECONDS, pooled_transport
from openswe.utils.langsmith import resolve_tenant_id
from openswe.utils.startup_trace import apm_span
from openswe.utils.timing import phase

logger = logging.getLogger(__name__)

# Marks the thread router's own calls to the local Agent Server, so they skip it.
LOCAL_HEADER: Final = "x-openswe-local-runtime"
# The run metadata the backend reads back from the run token, which travels in a
# header on every hook call: what the graph's hooks read, and the person the run acts
# as, which decides whether its catalog names that person's connections, and the person
# whose message started it, whom its model calls bill to.
_TOKEN_METADATA_KEYS: Final = (
    "origin",
    "event_match_ids",
    "ls_attribution_identifier",
    ACTING_USER_KEY,
    SENDER_USER_KEY,
    *BILLING_FIELDS,
)
# Whom a run is for, which ``MdaRuntime.run_payload`` settles before signing its token and
# writes the same into its config metadata, whatever its caller sent.
_ATTRIBUTION_KEYS: Final = BILLING_FIELDS | {SENDER_USER_KEY}

# MDA's channel threads are owned by ``mda:source-thread:<provider>:<conversation key>``.
_CHANNEL_OWNER_PREFIX: Final = "mda:source-thread:"
# Who the backend's writes on a channel's thread that name no person's principal are
# from: MDA's channel service principal, which owns no user connections. Keep it a
# service id: MDA takes any other id for a person, so on Slack threads every such
# write would share that one id's user connections.
CHANNEL_SERVICE_USER: Final = "mda:channel"
# Beside ``x-mda-user-id``, the Agent Auth principal MDA's channels stamp a run's sender with.
_PRINCIPAL_HEADER: Final = "x-mda-agent-auth-principal-id"
# How many channel threads' conversations an ``MdaRuntime`` keeps.
_CHANNEL_CONVERSATIONS_LIMIT: Final = 4096
#: How long a channel thread's conversation is kept before MDA is asked again: a run
#: may hand its thread to another conversation, a Slack thread it moved to.
_CHANNEL_CONVERSATION_SECONDS: Final = 60.0
#: How long MDA's ``/info`` flags are cached in process before refreshing.
_INFO_TTL_SECONDS: Final = 300.0
_INFO_RESPONSE_HEADERS: Final = ("content-type", "cache-control")
# Connections each MDA client holds at once: the short calls, then the streams, which
# stay open as long as someone watches a thread.
_MDA_CONNECTIONS: Final = 100
_MDA_STREAM_CONNECTIONS: Final = 1000

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


type RunStatus = Literal["pending", "running", "success", "error", "timeout", "interrupted"]


# What of a run's kwargs a dashboard client reads: a queued run's message or answer, and
# how the run streams. The rest stays here, the context above all: it carries the run
# token, which the agent API accepts as the run itself.
_CLIENT_RUN_KWARGS: Final = (
    "input",
    "command",
    "stream_mode",
    "subgraphs",
    "__protocol_stream_run",
)


class MdaRun(BaseModel):
    """A run as MDA reports it."""

    run_id: str
    status: RunStatus
    created_at: datetime
    metadata: dict[str, JsonValue] = {}

    @property
    def active(self) -> bool:
        return self.status in ("pending", "running")

    @staticmethod
    def client_view(run: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        """``run`` as a dashboard client may see it: its kwargs cut to what a client reads,
        keeping only the stream marker of its configurable."""
        kwargs = run.get("kwargs")
        if not isinstance(kwargs, dict):
            return dict(run)
        visible: dict[str, JsonValue] = {
            key: kwargs[key] for key in _CLIENT_RUN_KWARGS if key in kwargs
        }
        config = kwargs.get("config")
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict) and dispatch.V3_STREAMING_CONFIG_KEY in configurable:
            marker = configurable[dispatch.V3_STREAMING_CONFIG_KEY]
            visible["config"] = {"configurable": {dispatch.V3_STREAMING_CONFIG_KEY: marker}}
        return {**run, "kwargs": visible}


_runs = TypeAdapter(list[MdaRun])


class ChannelThreadError(RuntimeError):
    """A thread an MDA channel owns that the backend can't act on: no owner, or no secret."""


@dataclass(frozen=True)
class ChannelWrite:
    """Who a backend write on a thread is from, as MDA is told and as its run token says.

    On a channel's thread, ``headers`` are MDA's trusted-backend ingress for its
    conversation (:meth:`MdaRuntime.channel_write`); on the backend's own threads there
    are none, and the write is the backend key's. ``user_id`` is the Open SWE user whose
    principal the headers name, only when they name one.
    """

    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    user_id: str | None = None

    def acting(self, metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        """Run ``metadata`` acting as this write's person or no one, never as it asked.

        So the run's token, whose catalog names a person's connections only for a run
        that acts as them, always agrees with the principal MDA starts it as.
        """
        stamped = {key: value for key, value in metadata.items() if key != ACTING_USER_KEY}
        if self.user_id is not None:
            stamped[ACTING_USER_KEY] = self.user_id
        return stamped


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

    async def state(
        self,
        thread_id: str,
        *,
        select: list[str] | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, JsonValue]:
        """The thread's current state here, only its ``select`` fields when given.

        The SDK parses the state as it reads it, so this read adds no phases to ``timings``.
        """
        state = await self.client.threads.get_state(thread_id, select=select)
        return _object.validate_python(dict(state))

    async def has_active_run(self, thread_id: str) -> bool:
        """Whether a run on the thread here is still pending or running."""
        for status in ("pending", "running"):
            if await self.client.runs.list(thread_id, status=status, limit=1):
                return True
        return False

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.create(
            thread_id=thread_id, if_exists="do_nothing", metadata=dict(metadata)
        )

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        await self.client.threads.update(thread_id, metadata=dict(metadata), return_minimal=True)

    async def delete(self, thread_id: str) -> None:
        try:
            await self.client.threads.delete(thread_id)
        except NotFoundError:
            return

    async def status(self, thread_id: str) -> ThreadStatus | None:
        """The thread's status here; one this runtime does not have has nothing running."""
        thread = await self.indexed(thread_id)
        return "idle" if thread is None else thread.status

    async def latest_run(self, thread_id: str) -> MdaRun | None:
        try:
            runs = await self.client.runs.list(thread_id, limit=1)
            if runs and runs[0]["status"] in {"pending", "interrupted"}:
                runs = await self.client.runs.list(thread_id, status="running", limit=1) or runs
        except NotFoundError:
            return None
        return MdaRun.model_validate(runs[0]) if runs else None

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
        # Each channel thread's conversation on MDA, (provider, key), with when it was read;
        # the most recently used ones.
        self._channel_conversations: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        self._info_cache: tuple[int, bytes, dict[str, str], float] | None = None
        self._info_lock = asyncio.Lock()

    async def info(self) -> tuple[int, bytes, dict[str, str]]:
        """MDA's ``/info``, cached for 5 minutes once a ``200`` read succeeds."""
        cached = self._info_cache
        if cached is not None and time.monotonic() - cached[3] < _INFO_TTL_SECONDS:
            return cached[0], cached[1], dict(cached[2])
        async with self._info_lock:
            cached = self._info_cache
            if cached is not None and time.monotonic() - cached[3] < _INFO_TTL_SECONDS:
                return cached[0], cached[1], dict(cached[2])
            async with self.read_http as client:
                response = await client.get("/info")
            headers = {
                name: value
                for name in _INFO_RESPONSE_HEADERS
                if (value := response.headers.get(name)) is not None
            }
            if response.status_code == 200:
                self._info_cache = (
                    response.status_code,
                    response.content,
                    headers,
                    time.monotonic(),
                )
            return response.status_code, response.content, headers

    def _client(
        self, extra: Mapping[str, str], *, pool: str, max_connections: int = _MDA_CONNECTIONS
    ) -> httpx2.AsyncClient:
        headers = dict(extra)
        if api_key := mda_runtime_api_key():
            headers["x-api-key"] = api_key
        url = httpx2.URL(self.url)
        proxies = getproxies()
        proxy = (
            None
            if proxy_bypass(url.netloc.decode())
            or any(
                url.host.lower().endswith(host.strip()[1:].lower())
                for host in proxies.get("no", "").split(",")
                if host.strip().startswith("*.")
            )
            else proxies.get(url.scheme, proxies.get("all"))
        )
        return httpx2.AsyncClient(
            base_url=self.url,
            headers=headers,
            timeout=httpx2.Timeout(30.0, read=None),
            transport=pooled_transport(
                f"mda-{pool}",
                limits=httpx2.Limits(
                    max_connections=max_connections, keepalive_expiry=KEEPALIVE_SECONDS
                ),
                proxy=proxy,
            ),
        )

    @property
    def http(self) -> httpx2.AsyncClient:
        """For writes: thread changes, runs, cancels, answers to interrupts."""
        return self._client({}, pool="write")

    @property
    def read_http(self) -> httpx2.AsyncClient:
        """For reads: threads, state, history, runs and their streams, channel threads too."""
        return self._client(MDA_STUDIO_READ_HEADERS, pool="read")

    @property
    def stream_http(self) -> httpx2.AsyncClient:
        """For reads held open until a run ends or a tab closes: thread and run streams, joins.

        Their own pool, so open dashboard tabs never leave runs unable to start or stop.
        """
        return self._client(
            MDA_STUDIO_READ_HEADERS, pool="stream", max_connections=_MDA_STREAM_CONNECTIONS
        )

    def sdk(self) -> LangGraphClient:
        """For writes, scoped to the threads the backend owns."""
        return get_client(url=self.url, api_key=mda_runtime_api_key())

    async def channel_write(self, thread_id: str, *, user_id: str | None = None) -> ChannelWrite:
        """What lets the backend start, answer and stop runs on a thread an MDA channel owns.

        MDA's trusted-backend ingress: its ingress secret, the ``MDA_INGRESS_SECRET``
        the deployment gets with the value of the ``MDA_SERVICE_SECRET`` the two share,
        with the reserved headers naming the conversation that owns the thread, read
        from MDA and kept for a minute. The thread keeps that owner, so the
        conversation's later messages still reach it, unless a run hands the thread to
        a Slack thread it moved to (:meth:`forget_channel_conversation`). Raises ``ChannelThreadError`` without the secret or
        when MDA names no channel conversation as the thread's owner.

        ``user_id`` is the Open SWE user the write is for, so a person's run here has
        the principal their channel runs have (:meth:`user_principal`); without one, or
        when Agent Auth can't name it, the write is the channel's own, for no person, and
        acts as no one (:attr:`ChannelWrite.user_id`).
        """
        secret = ENV.MDA_SERVICE_SECRET.optional()
        if not secret:
            raise ChannelThreadError("No MDA_SERVICE_SECRET to act on MDA's channel threads")
        cached = self._channel_conversations.get(thread_id)
        if cached is not None and time.monotonic() - cached[2] < _CHANNEL_CONVERSATION_SECONDS:
            self._channel_conversations.move_to_end(thread_id)
            conversation = cached[:2]
        else:
            async with self.read_http as client:
                response = await client.get(f"/threads/{thread_id}")
            response.raise_for_status()
            metadata = _object.validate_python(response.json()).get("metadata")
            owner = metadata.get("owner") if isinstance(metadata, dict) else None
            if not isinstance(owner, str) or not owner.startswith(_CHANNEL_OWNER_PREFIX):
                raise ChannelThreadError(f"MDA names no conversation owning thread {thread_id}")
            provider, _, key = owner.removeprefix(_CHANNEL_OWNER_PREFIX).partition(":")
            if not provider or not key:
                raise ChannelThreadError(f"MDA names no conversation owning thread {thread_id}")
            conversation = (provider, key)
            self._remember_conversation(thread_id, provider, key)
        provider, key = conversation
        return await self._conversation_write(secret, provider, key, user_id=user_id)

    async def create_handler_thread(self, thread_id: str, *, provider: str, channel: str) -> None:
        """Make ``thread_id`` on MDA one of the threads ``channel``'s handlers choose.

        Such a thread is owned by the channel's handler threads (``handler_threads_key``),
        not by one conversation, so the handler can start later runs on it for whichever
        conversation it joins, as it does on the side threads it opens itself. The
        backend creates it before it starts its first run there (:meth:`channel_write`).
        Raises ``ChannelThreadError`` without the secret.
        """
        secret = ENV.MDA_SERVICE_SECRET.optional()
        if not secret:
            raise ChannelThreadError("No MDA_SERVICE_SECRET to act on MDA's channel threads")
        key = handler_threads_key(channel)
        write = await self._conversation_write(secret, provider, key)
        async with self.http as client:
            response = await client.post(
                "/threads",
                json={"thread_id": thread_id, "if_exists": "do_nothing"},
                headers=write.headers,
            )
        response.raise_for_status()
        self._remember_conversation(thread_id, provider, key)

    def _remember_conversation(self, thread_id: str, provider: str, key: str) -> None:
        self._channel_conversations[thread_id] = (provider, key, time.monotonic())
        self._channel_conversations.move_to_end(thread_id)
        if len(self._channel_conversations) > _CHANNEL_CONVERSATIONS_LIMIT:
            self._channel_conversations.popitem(last=False)

    async def _conversation_write(
        self, secret: str, provider: str, key: str, *, user_id: str | None = None
    ) -> ChannelWrite:
        """MDA's trusted-backend ingress for the conversation ``(provider, key)``."""
        principal = await self.user_principal(user_id) if user_id else None
        headers = {
            "x-mda-ingress-secret": secret,
            "x-mda-user-id": principal or CHANNEL_SERVICE_USER,
            **({_PRINCIPAL_HEADER: principal} if principal else {}),
            "x-mda-source-provider": provider,
            "x-mda-source-thread-id": key,
        }
        return ChannelWrite(MappingProxyType(headers), user_id if principal else None)

    def forget_channel_conversation(self, thread_id: str) -> None:
        """Ask MDA again which conversation owns ``thread_id`` on its next write: a run
        handed the thread to the Slack thread it moved to."""
        self._channel_conversations.pop(thread_id, None)

    async def user_principal(self, user_id: str) -> str | None:
        """The Agent Auth principal an Open SWE user's channel runs have, or ``None``.

        The backend's ``resolve_user`` names the user (``mda/identity.py``), and MDA
        resolves that as the ``trusted_backend`` identity of the user's id in its
        workspace; this resolves the same identity, with the key the backend calls MDA
        with. The SDK caches each answer.
        """
        extra = {"user_id": user_id}
        try:
            agent_auth = await self._agent_auth()
            if agent_auth is None or not (workspace := agent_auth.context.workspace_id):
                logger.warning("No LangSmith key or workspace to resolve a principal", extra=extra)
                return None
            return await asyncio.to_thread(
                agent_auth.resolve_principal,
                {"provider": TRUSTED_BACKEND_PROVIDER, "issuer": workspace, "subject": user_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not resolve a user's principal", extra=extra, exc_info=True)
            return None

    async def deployment_secret(self, deployment_id: str, slug: str) -> str | None:
        """The secret the deployment holds for itself in Agent Auth as connection ``slug``:
        one its channel's ``mda deploy`` keeps there, such as the Slack app's bot token.

        ``None`` when the deployment holds none, or the backend has no key or workspace
        to ask with; raises ``CredentialStoreError`` when Agent Auth does not answer.
        """
        agent_auth = await self._agent_auth(deployment_id=deployment_id)
        if agent_auth is None:
            return None
        credential_id = await asyncio.to_thread(agent_auth.find_workspace_credential_id, slug)
        if credential_id is None:
            return None
        return await asyncio.to_thread(agent_auth.read_secret, credential_id)

    async def _agent_auth(self, *, deployment_id: str | None = None) -> AgentAuth | None:
        """Agent Auth in the deployment's workspace, asked with the key the backend calls
        MDA with; ``None`` without either. ``deployment_id`` to read what it owns."""
        api_key = mda_runtime_api_key()
        workspace = ENV.MDA_WORKSPACE_ID.optional() or await resolve_tenant_id()
        if not api_key or not workspace:
            return None
        return AgentAuth(
            AgentAuthContext(
                base_url=resolve_agent_auth_base_url(
                    {PLATFORM_API_URL_ENV: ENV.LANGSMITH_ENDPOINT.get()}
                ),
                principal_id="",
                agent_id=deployment_id,
                api_key=api_key,
                workspace_id=workspace,
            )
        )

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
        logger.info(
            "Interrupting a run its thread was stopped while it started",
            extra={"thread_id": thread_id, "run_id": run_id},
        )
        # A stop that came once MDA had the run may have cancelled it already.
        await self.cancel(thread_id, run_id)

    async def cancel(self, thread_id: str, run_id: str) -> bool:
        """Interrupt the run; ``False`` when MDA refused or could not be reached."""
        extra = {"thread_id": thread_id, "run_id": run_id}
        try:
            thread = await index.get(thread_id)
            # A channel's thread is stopped as the backend acting in its conversation.
            write = (
                await self.channel_write(thread_id)
                if thread is not None and dispatch.channel_of(thread.metadata) is not None
                else ChannelWrite()
            )
            async with self.http as client:
                response = await client.post(
                    f"/threads/{thread_id}/runs/{run_id}/cancel",
                    params={"action": "interrupt"},
                    headers=write.headers,
                )
        except httpx2.HTTPError, ChannelThreadError:
            logger.warning("Could not interrupt an MDA run", extra=extra, exc_info=True)
            return False
        if response.is_error:
            logger.warning(
                "Could not interrupt an MDA run",
                extra={**extra, "status_code": response.status_code},
            )
            return False
        return True

    async def run(self, thread_id: str, run_id: str) -> MdaRun | None:
        async with self.http as client:
            response = await client.get(f"/threads/{thread_id}/runs/{run_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return MdaRun.model_validate(response.json())

    async def active_runs(self, thread_id: str) -> list[MdaRun]:
        """The thread's unfinished runs in the order they hold it: running, then pending oldest first."""
        active: list[MdaRun] = []
        for status in ("running", "pending"):
            async with self.http as client:
                response = await client.get(
                    f"/threads/{thread_id}/runs", params={"status": status, "limit": 10}
                )
            response.raise_for_status()
            active += sorted(_runs.validate_python(response.json()), key=lambda run: run.created_at)
        return active

    @staticmethod
    async def trace_access(thread_id: str) -> dict[str, JsonValue]:
        """The root trace metadata access policies read: the stored thread's owner and visibility.

        Set before the root exists, since policies read root metadata only as ingested. A
        thread that can't be read is traced as private, so a policy hiding private traces
        hides it too.
        """
        from openswe.tasks.schemas import ThreadMetadata
        from openswe.threads.store import THREADS

        owner_ls_user_id: str | None = None
        visibility = TraceVisibility.PRIVATE
        try:
            thread = await THREADS.get(thread_id)
            if thread is not None:
                visibility = TraceVisibility.of(thread.metadata)
                if thread.metadata.get("owner_type") == "user":
                    metadata = ThreadMetadata.model_validate(thread.metadata)
                    if metadata.owner_user_id is not None:
                        owner = await metadata.owner()
                        identities = [
                            item for item in owner.identities if item.provider == "langsmith"
                        ]
                        if len(identities) == 1:
                            owner_ls_user_id = identities[0].external_id
        except Exception:
            logger.warning(
                "Could not resolve a run's trace access",
                extra={"thread_id": thread_id},
                exc_info=True,
            )
        return {"owner_ls_user_id": owner_ls_user_id, "visibility": visibility.value}

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
        config_metadata = config.get("metadata")
        origin = RunOrigin.resolve(
            run_metadata.get("origin"),
            inherited=configurable.get("origin")
            or (config_metadata.get("origin") if isinstance(config_metadata, dict) else None),
            fallback=configurable.get("source"),
        ).value
        assistant_id = payload.get("assistant_id")
        if not isinstance(assistant_id, str):
            raise ValueError("A run needs an assistant_id")
        thread_kind = thread_metadata.get("kind")
        kind = (
            run_metadata.get("kind")
            or (config_metadata.get("kind") if isinstance(config_metadata, dict) else None)
            or (thread_kind if isinstance(thread_kind, str) else assistant_id)
        )
        run_metadata["kind"] = kind
        configurable["origin"] = origin
        run_metadata["origin"] = origin
        config["metadata"] = {
            **(config_metadata if isinstance(config_metadata, dict) else {}),
            "origin": origin,
            "kind": kind,
        }
        streaming = configurable.pop(dispatch.V3_STREAMING_CONFIG_KEY, None)
        run_metadata.pop("billed_user_id", None)
        run_metadata["created_by"] = None
        run_metadata.setdefault(
            "billing_bucket", "channel" if dispatch.channel_of(thread_metadata) else "system"
        )
        actor = run_metadata.get(ACTING_USER_KEY)
        if (
            assistant_id == "agent"
            and isinstance(actor, str)
            and actor != run_metadata.get("billed_robin_user_id")
        ):
            from uuid import UUID

            from openswe.users import User

            for key in BILLING_USER_FIELDS:
                run_metadata.pop(key, None)
            run_metadata["billing_bucket"] = (
                "channel" if dispatch.channel_of(thread_metadata) else "system"
            )
            try:
                with apm_span("start_run.acting_user", {}):
                    user = await User.get(UUID(actor))
                if user is not None:
                    run_metadata.update(user.billing_trace_metadata())
            except Exception:
                logger.warning("Could not resolve acting user for trace metadata", exc_info=True)
        billed = run_metadata.get("billed_robin_user_id")
        if run_metadata.get("user_id") != billed:
            run_metadata.pop("user_id", None)
        # Its model calls spend the sender's own LangSmith budget, so the token names a
        # sender only when the run is billed to them and, if it acts as anyone, acts as them.
        sender = run_metadata.get(SENDER_USER_KEY)
        if sender is not None and (sender != billed or actor not in (None, sender)):
            run_metadata.pop(SENDER_USER_KEY)
        with apm_span("start_run.trace_access", {}):
            trace_access = await self.trace_access(thread_id)
        run_metadata.update(trace_access)
        raw_config_metadata = config.get("metadata")
        if isinstance(raw_config_metadata, dict):
            config["metadata"] = {
                **{
                    key: value
                    for key, value in raw_config_metadata.items()
                    if key not in _ATTRIBUTION_KEYS and key != "billed_user_id"
                },
                **{key: run_metadata[key] for key in _ATTRIBUTION_KEYS if key in run_metadata},
                **trace_access,
                "created_by": None,
            }
        context = dict(
            await remote_run_context(
                configurable,
                metadata={k: run_metadata[k] for k in _TOKEN_METADATA_KEYS if k in run_metadata},
                thread_id=thread_id,
                assistant_id=assistant_id,
            )
        )
        context.update(trace_access)
        if isinstance(streaming, bool):
            # Context replaces configurable on the server, so the stream marker rides in
            # it; ``False`` too, or the run inherits the ``True`` its thread kept.
            context[dispatch.V3_STREAMING_CONFIG_KEY] = streaming
        rewritten = {
            **payload,
            "assistant_id": graph_id,
            "context": context,
            "metadata": run_metadata,
        }
        if config:
            rewritten["config"] = config
        else:
            rewritten.pop("config", None)
        with apm_span("start_run.validate_payload", {}):
            return _object.validate_python(rewritten)

    @staticmethod
    def run_body(
        thread_id: str, start: StartRequest, *, write: ChannelWrite
    ) -> dict[str, JsonValue]:
        """The runs API body ``start`` asks for, as :meth:`run_payload` takes it.

        MDA's own ``run.start`` would drop the context, which is what carries the run token,
        so the backend builds the body and creates the run itself (:meth:`create_run`).
        """
        payload: dict[str, JsonValue] = {
            "assistant_id": "agent",
            "input": start.input,
            "config": {
                "configurable": {
                    **start.configurable,
                    dispatch.V3_STREAMING_CONFIG_KEY: False,
                    "thread_id": thread_id,
                },
                "metadata": start.metadata,
            },
            "metadata": write.acting(start.metadata),
            **start.run_format.run_fields(),
            "on_completion": "keep",
            "if_not_exists": "create",
            # A run begun since the caller found the thread idle is never stopped or
            # queued behind here: the start is refused, and the caller steers or queues.
            "multitask_strategy": "reject",
        }
        if start.command is not None:
            payload["command"] = start.command
        # As on dispatched runs: the run-complete webhook settles the thread in the index.
        if dispatch.COMPLETION_WEBHOOK_URL:
            payload["webhook"] = dispatch.COMPLETION_WEBHOOK_URL
        return payload

    async def create_run(
        self,
        thread_id: str,
        run: Mapping[str, JsonValue],
        *,
        starting_ms: int,
        write: ChannelWrite,
        trace_headers: Mapping[str, str] | None = None,
    ) -> str:
        """Create ``run``, rewritten by :meth:`run_payload`, and return its id.

        On a channel's thread, ``write`` (:meth:`channel_write`) starts the run in its
        conversation, as the person it names; without one the run is the backend's own.
        ``starting_ms`` is when the start was asked for, which a stop since then interrupts.
        ``trace_headers`` name the LangSmith trace the run nests in. A run MDA refuses
        raises :class:`RunRefused` with MDA's status.
        """
        async with self.http as client:
            response = await client.post(
                f"/threads/{thread_id}/runs",
                json=dict(run),
                headers={**(trace_headers or {}), **write.headers},
            )
        if response.is_error:
            logger.warning(
                "Starting an MDA run failed",
                extra={"thread_id": thread_id, "status_code": response.status_code},
            )
            raise RunRefused(response.status_code, response.text[:500], code="unknown_error")
        created = _object.validate_python(response.json())
        run_id = created.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RunRefused(502, "the runtime started no run", code="unknown_error")
        await self.interrupt_if_stopped(thread_id, run_id, starting_ms=starting_ms)
        return run_id


class _MdaThreads(_SdkThreads):
    """MDA's runtime: SDK writes, as the backend; reads over :attr:`MdaRuntime.read_http`.

    So the backend reads the threads an MDA channel owns too, and writes only its own.
    """

    def __init__(self, runtime: MdaRuntime) -> None:
        super().__init__(runtime.sdk())
        self.runtime = runtime

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        async with self.runtime.read_http as client:
            response = await client.get(f"/threads/{thread_id}")
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return _object.validate_python(response.json())

    async def indexed(self, thread_id: str) -> IndexedThread | None:
        async with self.runtime.read_http as client:
            response = await client.post(
                "/threads/search", json={"ids": [thread_id], "limit": 1, "select": INDEXED_FIELDS}
            )
        response.raise_for_status()
        found = response.json()
        return next(
            (thread for raw in found if (thread := IndexedThread.from_agent_server(raw))), None
        )

    async def latest_run(self, thread_id: str) -> MdaRun | None:
        async with self.runtime.read_http as client:
            response = await client.get(f"/threads/{thread_id}/runs", params={"limit": 1})
        if response.status_code == httpx2.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        runs = _runs.validate_python(response.json())
        if runs and runs[0].status in {"pending", "interrupted"}:
            async with self.runtime.read_http as client:
                response = await client.get(
                    f"/threads/{thread_id}/runs", params={"status": "running", "limit": 1}
                )
            if response.status_code == httpx2.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            runs = _runs.validate_python(response.json()) or runs
        return runs[0] if runs else None

    async def state(
        self,
        thread_id: str,
        *,
        select: list[str] | None = None,
        timings: dict[str, float] | None = None,
    ) -> dict[str, JsonValue]:
        """The thread's state on MDA: ``mda`` in ``timings`` is the read with its body, and
        ``parse`` this backend's parse and validation of it."""
        record = timings if timings is not None else {}
        with phase(record, "mda"):
            async with self.runtime.read_http as client:
                response = await client.get(
                    f"/threads/{thread_id}/state", params={"select": select} if select else None
                )
        response.raise_for_status()
        with phase(record, "parse"):
            return _object.validate_python(orjson.loads(response.content))

    async def has_active_run(self, thread_id: str) -> bool:
        for status in ("pending", "running"):
            async with self.runtime.read_http as client:
                response = await client.get(
                    f"/threads/{thread_id}/runs", params={"status": status, "limit": 1}
                )
            response.raise_for_status()
            if response.json():
                return True
        return False


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
        return _MdaThreads(self.mda_runtime)

    def runs_on(self, runtime: Runtime) -> _SdkThreads:
        return self.mda if runtime == "mda" else self.local


RUNTIMES: Final = Runtimes()
