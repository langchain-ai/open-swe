"""Pass-through endpoints between the dashboard and the LangGraph HTTP API."""

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import nullcontext
from typing import Any

import httpx
import httpx2
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError
from pydantic import JsonValue

from openswe.config import ENV
from openswe.dashboard.ttft import DashboardClient, DashboardRunTimeline, DashboardSendTrace
from openswe.dispatch import PROTOCOL_V2, RunFormat, channel_of, prepare_durable_run
from openswe.plugins.records import PluginChat
from openswe.threads.access import (
    _authorized_thread_metadata,
    _ensure_dashboard_github_token,
    _readable_thread_metadata,
    agent_version_metadata,
)
from openswe.threads.identity import ThreadIdentity
from openswe.threads.machine_reads import machine_thread
from openswe.threads.mentions import ThreadMentions
from openswe.threads.principals import Principal
from openswe.threads.runs import (
    _ASSISTANT_ID,
    QUEUED_BY_KEY,
    _build_dashboard_configurable,
    _command_message_content,
    _command_message_id,
    _command_prompt_text,
    _enrich_run_start_command,
    _enrich_system_run_start_command,
    _extract_run_id_from_command_response,
    _SlackWebHandoff,
    offload_requested,
    queue_follow_up_run,
    steer_running_thread,
)
from openswe.threads.runtimes import MdaRun
from openswe.threads.store import ACTING_COMMANDS, POSTING_COMMANDS, THREADS, CommandReply
from openswe.threads.summary import (
    DASHBOARD_SOURCE,
    TRANSCRIPT_VERSION,
    _assert_thread_postable,
    _now_ms,
    chats_beside_plugin,
)
from openswe.trace_metadata import RunOrigin
from openswe.transcript.turns import fail_unstarted_turn, steer_target
from openswe.users import User
from openswe.utils.dashboard_handoff import RELAYED_FROM_KEY
from openswe.utils.http_pool import pooled_transport
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client, langgraph_url

logger = logging.getLogger(__name__)

_PROXY_REQUEST_TIMEOUT = httpx2.Timeout(30.0, connect=5.0)
_PROXY_STREAM_TIMEOUT = httpx2.Timeout(None)
_DISCOVERY_HISTORY_LIMIT = 5


def require_json_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def langgraph_proxy_headers(
    *, content_type: str = "application/json", accept: str | None = None
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if accept:
        headers["Accept"] = accept
    api_key = ENV.LANGSMITH_API_KEY.optional()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


async def proxy_dashboard_thread_stream_events(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
    principal: Principal | None = None,
) -> AsyncIterator[bytes]:
    # Preflight here (not in the generator) so auth/content-type failures
    # surface as real HTTP errors before the SSE response starts streaming.
    require_json_content_type(content_type)
    if principal is not None and principal.machine:
        await machine_thread(thread_id, principal)
    else:
        await _readable_thread_metadata(thread_id, login=login, email=email)
    return stream_thread_events(thread_id, body, content_type)


async def stream_thread_events(
    thread_id: str,
    body: bytes,
    content_type: str,
) -> AsyncIterator[bytes]:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/stream/events"
    headers = langgraph_proxy_headers(content_type=content_type, accept="text/event-stream")

    try:
        # Its own client, not the shared pool: the stream stays open for as long as
        # a tab watches the thread, and open tabs must not starve other requests.
        async with httpx2.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    payload = {
                        "status": response.status_code,
                        "detail": error_body.decode(errors="replace") or response.reason_phrase,
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n".encode()
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception:
        logger.warning("LangGraph stream/events proxy closed for %s", thread_id, exc_info=True)


async def proxy_dashboard_thread_commands(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
    principal: Principal | None = None,
    relayed_from: str | None = None,
    relayed_origin: RunOrigin | None = None,
    client: DashboardClient = DashboardClient.OTHER,
    submitted_at: str | None = None,
) -> tuple[int, bytes, str | None]:
    """Forward one command, enriched for whoever sent it.

    ``principal`` is how a machine gets in. Without one the sender is the person
    named by ``login``, which is what the dashboard and the agent's own tools pass.
    ``relayed_from`` names the agent thread that sent it on that person's behalf.
    ``client`` and ``submitted_at``, the client's own send time, only time the run.
    """
    received_at_ms = _now_ms()
    principal = principal or Principal.of_login(login, email)
    require_json_content_type(content_type)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")

    method = parsed.get("method")
    if method == "run.start":
        return await ThreadSend(
            thread_id,
            principal,
            parsed,
            relayed_from=relayed_from,
            relayed_origin=relayed_origin,
            client=client,
            received_at_ms=received_at_ms,
            submitted_at=submitted_at,
        ).start()

    # Input commands on admin threads require an admin; other threads keep
    # unattributed commands such as ``input.respond`` owner-only.
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise HTTPException(404, "thread not found")
    metadata = dict(thread.metadata)
    post_command = method in POSTING_COMMANDS
    if post_command:
        await principal.assert_can_post(metadata)
    else:
        principal.assert_can_read(metadata)
    if not (post_command and metadata.get("admin_thread") is True):
        principal.assert_can_read(metadata)
    params = parsed.get("params")
    if isinstance(params, dict):
        # Only a ``run.start`` takes these.
        params.pop(RELAYED_FROM_KEY, None)
        params.pop("multitask_strategy", None)
    if principal.machine:
        raise HTTPException(403, "a machine principal may only start runs")

    sender = (
        await _sender_user_id(principal, login, metadata)
        if method in ACTING_COMMANDS and not relayed_from
        else None
    )
    try:
        return await THREADS.command(thread_id, json.dumps(parsed).encode(), user_id=sender)
    except Exception as exc:
        logger.exception(
            "Delivering a thread command failed",
            extra={
                "thread_id": thread_id,
                "command_method": method,
                "error_type": type(exc).__name__,
            },
        )
        return CommandReply(
            500,
            json.dumps({"detail": "the command could not be delivered"}).encode(),
            "application/json",
        )


class ThreadSend:
    """One send to a thread: a message that starts a run, steers into the live one or
    waits behind it as a queued run, or an answer to the thread's interrupt.

    ``/commands`` and the runs routes start every message here, so who may post,
    enrichment and attribution, steering and queueing, the transcript turn and the
    run's bookkeeping can't drift apart between them; only the format the run streams
    in (``run_format``) differs. A thread the client minted the id of is created by its
    first message, unless ``creates`` is off, which leaves a missing thread a 404.

    ``principal`` is who sent it: a person, or a machine. ``relayed_from`` names the
    agent thread that sent the message on a person's behalf. ``client``,
    ``received_at_ms`` and ``submitted_at``, the client's own send time, only time the run.
    """

    def __init__(
        self,
        thread_id: str,
        principal: Principal,
        command: dict[str, JsonValue],
        *,
        run_format: RunFormat = PROTOCOL_V2,
        creates: bool = True,
        relayed_from: str | None = None,
        relayed_origin: RunOrigin | None = None,
        client: DashboardClient = DashboardClient.OTHER,
        received_at_ms: int | None = None,
        submitted_at: str | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.principal = principal
        # A machine has no login; the enrichment that would need one is the person's.
        self.login = principal.login or ""
        self.email = principal.email
        self.command = command
        self.run_format = run_format
        self.creates = creates
        self.relayed_from = relayed_from
        self.relayed_origin = relayed_origin
        self.client = client
        self.received_at_ms = _now_ms() if received_at_ms is None else received_at_ms
        self.submitted_at_ms = DashboardRunTimeline.client_submitted_at(
            submitted_at, self.received_at_ms
        )

    async def start(self) -> CommandReply:
        """Start the message, answered as protocol v2 answers a ``run.start``."""
        # The dashboard mints the thread id client-side and submits straight away,
        # so the very first message may target a thread that doesn't exist yet. It
        # lazily creates + stamps + owns the thread (in ``_enrich_run_start_command``).
        # On an existing thread, posting is open to any allowed user and attributed
        # in ``_enrich_run_start_command``.
        thread = await THREADS.get(self.thread_id)
        creating = thread is None
        if thread is None:
            if not self.creates:
                raise HTTPException(404, "thread not found")
            metadata: dict[str, JsonValue] = {}
            thread_busy = False
        else:
            metadata = dict(thread.metadata)
            await self.principal.assert_can_post(metadata)
            metadata_run_status = metadata.get("latest_run_status")
            thread_busy = thread.status == "busy" or metadata_run_status in {"pending", "running"}

        params = self._params()
        # The client's queue-or-steer choice rides the run's multitask strategy.
        # LangGraph's commands endpoint does not take it, so it is consumed here.
        enqueue = params.pop("multitask_strategy", None) == "enqueue"
        mentions = ThreadMentions(
            self.thread_id,
            self.login,
            _command_message_id(params) or str(self.command.get("id") or uuid.uuid7()),
            _command_prompt_text(_command_message_content(params))
            if not self.principal.machine and not self.relayed_from
            else "",
        )
        offloading = offload_requested(params)
        if thread_busy:
            handled = await self._follow_up(metadata, enqueue=enqueue, offloading=offloading)
            mentions.dispatch()
            return CommandReply(200, json.dumps(handled).encode(), "application/json")
        # Enrichment rewrites the command in place; a start that loses the thread
        # to another run is queued from the command as sent.
        requeueable = (
            copy.deepcopy(self.command)
            if not creating and not self.principal.machine and not offloading
            else None
        )

        run_input = params.get("input")
        send_trace = await DashboardSendTrace.open(
            thread_id=self.thread_id,
            client=self.client,
            started_at_ms=self.submitted_at_ms or self.received_at_ms,
            received_at_ms=self.received_at_ms,
            inputs=run_input if isinstance(run_input, dict) else {},
            origin=(
                RunOrigin.API
                if self.principal.machine
                else RunOrigin.resolve(self.relayed_origin)
                if self.relayed_from
                else RunOrigin.DASHBOARD
            ),
        )
        enriched = await self._build(send_trace, metadata, creating=creating)
        sender = (
            await _sender_user_id(self.principal, self.login, metadata)
            if not self.relayed_from
            else None
        )
        reply = await self._deliver(enriched, send_trace, sender=sender)

        try:
            response_payload = json.loads(reply.content) if reply.content else None
        except json.JSONDecodeError:
            response_payload = None
        run_id = _extract_run_id_from_command_response(response_payload)
        started = (
            reply.status in {200, 202, 204}
            and isinstance(response_payload, dict)
            and response_payload.get("type") == "success"
            and run_id is not None
        )
        # The store answers a start the runtime refused with a 200 carrying an error envelope.
        refused = reply.status >= 400 or (
            isinstance(response_payload, dict) and response_payload.get("type") == "error"
        )
        if send_trace is not None:
            if started:
                send_trace.end_commands()
            else:
                send_trace.fail(f"The run did not start (HTTP {reply.status})")
        if requeueable is not None and not started:
            requeued = await self._queue_if_thread_turned_busy(requeueable)
            if requeued is not None:
                mentions.dispatch()
                return CommandReply(200, json.dumps(requeued).encode(), "application/json")
        if refused:
            await self._fail_requested_turn(enriched, reply)
        if not started:
            return reply

        if not self.principal.machine and chats_beside_plugin(metadata):
            user = await User.for_person(ThreadIdentity(self.login).person)
            if user:
                params = enriched.get("params")
                await PluginChat.deliver_input(
                    self.thread_id,
                    user.id,
                    params.get("input") if isinstance(params, dict) else None,
                )
        if not creating and not self.relayed_from:
            await _SlackWebHandoff(self.thread_id, metadata).notify()
        mentions.dispatch()
        try:
            await THREADS.update(
                self.thread_id,
                {
                    "latest_run_id": run_id,
                    # Later runs inherit the send's run metadata; its timing is this run's.
                    DashboardRunTimeline.STARTED_RUNS_KEY: DashboardRunTimeline.started(
                        metadata, received_at_ms=self.received_at_ms, run_id=run_id
                    ),
                    "latest_run_status": "pending",
                    "updated_at_ms": _now_ms(),
                },
            )
        except Exception:
            logger.warning(
                "Failed to persist a started dashboard run",
                exc_info=True,
                extra={"thread_id": self.thread_id, "run_id": run_id},
            )
        return reply

    async def answer(self, command: Mapping[str, JsonValue]) -> CommandReply:
        """Answer the thread's interrupt with a run's ``command``, as an ``input.respond`` on
        ``/commands`` does, the run in a runs API format.

        The same people may answer as there, and no message joins the transcript. The run
        that resumes the thread is its sender's, made for them as a message's would be.
        """
        run_format = RunFormat.runs_api({**self.run_format.fields, "command": dict(command)})
        thread = await THREADS.get(self.thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        metadata = dict(thread.metadata)
        await self.principal.assert_can_post(metadata)
        if metadata.get("admin_thread") is not True:
            self.principal.assert_can_read(metadata)
        if self.principal.machine:
            raise HTTPException(403, "a machine principal may only start runs")
        await _ensure_dashboard_github_token(self.login)
        configurable = await _build_dashboard_configurable(self.thread_id, self.login, metadata)
        config = await prepare_durable_run(
            _ASSISTANT_ID,
            {"configurable": configurable},
            agent_version_metadata(),
            source=DASHBOARD_SOURCE,
            as_sender=True,
            run_format=run_format,
        )
        start = {
            "method": "run.start",
            "params": {
                "assistant_id": _ASSISTANT_ID,
                "input": None,
                "config": config,
                "metadata": config["metadata"],
            },
        }
        sender = await _sender_user_id(self.principal, self.login, metadata)
        return await THREADS.start_run(
            self.thread_id, json.dumps(start).encode(), run_format, user_id=sender
        )

    def _params(self) -> dict[str, JsonValue]:
        """The command's params, naming the relaying thread only when the caller did."""
        raw = self.command.get("params")
        params = raw if isinstance(raw, dict) else {}
        params.pop(RELAYED_FROM_KEY, None)
        if self.relayed_from:
            params[RELAYED_FROM_KEY] = self.relayed_from
            self.command["params"] = params
        return params

    async def _follow_up(
        self, metadata: dict[str, JsonValue], *, enqueue: bool, offloading: bool
    ) -> dict[str, JsonValue]:
        """Steer a message sent while a run is live into it, or queue it behind it."""
        if offloading:
            raise HTTPException(409, "offloading requires an idle conversation")
        # Queueing and steering both attribute the message to a person, which a
        # machine has none of; it retries instead.
        if self.principal.machine:
            raise HTTPException(409, "thread is already running")
        # A follow-up while a run is live either waits for that run as a queued
        # run of its own, or joins the open turn. With no open turn the run has
        # closed it and is about to end, so the follow-up waits for it. Either
        # reply keeps the protocol's shape so the client cannot tell them from a
        # plain start. See docs/tla/SteerRace.tla.
        transcribed = metadata.get("transcript") == TRANSCRIPT_VERSION
        turn = await steer_target(self.thread_id) if transcribed and not enqueue else None
        if not enqueue and (turn is not None or not transcribed):
            return await steer_running_thread(
                self.thread_id,
                self.login,
                self.command,
                metadata=metadata,
                turn=turn,
                email=self.email,
                relayed_origin=self.relayed_origin,
                run_format=self.run_format,
            )
        return await queue_follow_up_run(
            self.thread_id,
            self.login,
            self.command,
            metadata=metadata,
            email=self.email,
            relayed_origin=self.relayed_origin,
            run_format=self.run_format,
        )

    async def _build(
        self,
        send_trace: DashboardSendTrace | None,
        metadata: dict[str, JsonValue],
        *,
        creating: bool,
    ) -> dict[str, JsonValue]:
        """The command enriched for whoever sent it, with what times the send."""
        try:
            with send_trace.stage("build_run") if send_trace is not None else nullcontext():
                if self.principal.machine:
                    enriched = await _enrich_system_run_start_command(
                        self.thread_id,
                        self.principal,
                        self.command,
                        metadata=metadata,
                        creating=creating,
                    )
                else:
                    enriched = await _enrich_run_start_command(
                        self.thread_id,
                        self.login,
                        self.command,
                        metadata=metadata,
                        creating=creating,
                        email=self.email,
                        relayed_origin=self.relayed_origin,
                    )
        except BaseException as exc:
            if send_trace is not None:
                send_trace.fail(f"The run could not be built ({type(exc).__name__})")
            raise
        params = enriched.get("params")
        if isinstance(params, dict):
            run_metadata = params.get("metadata")
            if not isinstance(run_metadata, dict):
                run_metadata = {}
                params["metadata"] = run_metadata
            # The run's completion times the send from these (``DashboardRunTimeline``).
            run_metadata[DashboardRunTimeline.RECEIVED_AT_KEY] = self.received_at_ms
            run_metadata[DashboardRunTimeline.CLIENT_KEY] = self.client.value
            if self.submitted_at_ms is not None:
                run_metadata[DashboardRunTimeline.SUBMITTED_AT_KEY] = self.submitted_at_ms
            if send_trace is not None:
                invocation_id = run_metadata.get("invocation_id")
                await send_trace.adopt(
                    thread_id=self.thread_id,
                    invocation_id=invocation_id if isinstance(invocation_id, str) else None,
                )
                run_metadata.update(send_trace.run_metadata())
        return enriched

    async def _deliver(
        self,
        enriched: dict[str, JsonValue],
        send_trace: DashboardSendTrace | None,
        *,
        sender: str | None,
    ) -> CommandReply:
        try:
            with send_trace.stage("start_run") if send_trace is not None else nullcontext():
                return await THREADS.start_run(
                    self.thread_id,
                    json.dumps(enriched).encode(),
                    self.run_format,
                    user_id=sender,
                    trace_headers=send_trace.headers() if send_trace is not None else None,
                )
        except Exception as exc:
            logger.exception(
                "Delivering a thread command failed",
                extra={
                    "thread_id": self.thread_id,
                    "command_method": "run.start",
                    "error_type": type(exc).__name__,
                },
            )
            return CommandReply(
                500,
                json.dumps(
                    {
                        "detail": (
                            "No connection was available to start the message. Try sending it again."
                            if isinstance(exc, (httpx.PoolTimeout, httpx2.PoolTimeout))
                            else "the command could not be delivered"
                        )
                    }
                ).encode(),
                "application/json",
            )

    async def _queue_if_thread_turned_busy(
        self, command: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        """Queue a start that a run begun after the busy check made LangGraph refuse.

        The completion hook starts a run for follow-ups steered into a run that was
        ending, so a start sent right after can find the thread taken.
        """
        try:
            thread = await THREADS.get(self.thread_id)
        except Exception:
            logger.warning(
                "Could not re-read a thread after a refused start",
                exc_info=True,
                extra={"start": {"thread_id": self.thread_id}},
            )
            return None
        if thread is None or thread.status != "busy":
            return None
        return await queue_follow_up_run(
            self.thread_id,
            self.login,
            command,
            metadata=dict(thread.metadata),
            email=self.email,
            relayed_origin=self.relayed_origin,
            run_format=self.run_format,
        )

    async def _fail_requested_turn(
        self, enriched: dict[str, JsonValue], reply: CommandReply
    ) -> None:
        """Record a failed start and close its turn so later follow-ups cannot join it."""
        params = enriched.get("params")
        config = params.get("config") if isinstance(params, dict) else None
        configurable = config.get("configurable") if isinstance(config, dict) else None
        turn_id = configurable.get("transcript_turn_id") if isinstance(configurable, dict) else None
        logger.warning(
            "Dashboard run start failed",
            extra={
                "thread_id": self.thread_id,
                "turn_id": turn_id,
                "status_code": reply.status,
            },
        )
        if not isinstance(turn_id, str):
            return
        try:
            await fail_unstarted_turn(
                self.thread_id,
                uuid.UUID(turn_id),
                error=(
                    "The message could not be started. Try sending it again. "
                    "If it fails again, share the debug details with the Robin team."
                ),
            )
        except Exception:
            logger.warning(
                "Could not close the turn of a refused start",
                exc_info=True,
                extra={"start": {"thread_id": self.thread_id, "turn_id": turn_id}},
            )


async def _sender_user_id(
    principal: Principal, login: str, metadata: Mapping[str, object]
) -> str | None:
    """The Open SWE user a person's own command on a channel's thread acts as there.

    The one ``_run_user_metadata`` attributes the person's dashboard runs to, so a
    follow-up has the principal their channel runs have. ``None`` for a machine,
    which is no person, and on any other thread. An agent's relayed message is not the
    person's own, so its caller asks for none.
    """
    if principal.machine or channel_of(metadata) is None:
        return None
    user = await ThreadIdentity(login).user()
    return None if user is None else str(user.id)


async def proxy_dashboard_thread_history(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "history body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "history body must be a JSON object")
    limit = payload.get("limit", _DISCOVERY_HISTORY_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise HTTPException(400, "history limit must be a positive integer")
    if not any(payload.get(key) for key in ("before", "checkpoint", "metadata")):
        payload["limit"] = min(limit, _DISCOVERY_HISTORY_LIMIT)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/history"
    headers = langgraph_proxy_headers(content_type=content_type)
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=_PROXY_REQUEST_TIMEOUT
    ) as client:
        response = await client.post(url, json=payload, headers=headers)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def _get_thread_tolerating_create_race(client: Any, thread_id: str) -> dict[str, Any] | None:
    """Fetch a thread, tolerating the brief window where a concurrent
    ``run.start`` on this same thread is still lazily creating it.

    The SDK's queue adapter only enqueues once the client believes a run is
    already active on ``thread_id`` — meaning a ``run.start`` dispatch for
    that very thread just went out. On a brand-new thread, that dispatch is
    what creates the thread row; a fast enough follow-up can reach here
    before it lands. Retry briefly rather than 404 what should resolve
    within one HTTP round trip.

    Only retries a genuine ``NotFoundError`` (404). Anything else — an
    outage, a timeout, an auth failure — is a real error, not "not found
    yet", and must propagate instead of being silently retried and then
    reported as a 404.
    """
    for delay in (0.0, 0.15, 0.3, 0.6):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await client.threads.get(thread_id)
        except NotFoundError:
            continue
    return None


async def proxy_dashboard_thread_run_enqueue(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    """Create a durable, attributed ``multitask_strategy="enqueue"`` run.

    Backs the "stream"-kind `ThreadSource`'s `AgentServerQueueAdapter.enqueue()`,
    which calls the raw ``client.runs.create()`` REST endpoint directly
    instead of the ``commands`` protocol. Reshapes that call into a
    ``run.start`` command and hands it to ``queue_follow_up_run`` — the same
    function the ``commands`` proxy's queue branch uses — so a queued
    follow-up here gets identical attribution, dedup, and transcript
    bookkeeping. Always enqueues: the raw runs API has no "steer" concept,
    and the client's own ``multitask_strategy`` (if any) is ignored — this
    endpoint must not become a backdoor around ``commands``'s busy-conflict
    check.
    """
    require_json_content_type(content_type)
    try:
        body_dict = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "run body must be a JSON object") from exc
    if not isinstance(body_dict, dict):
        raise HTTPException(400, "run body must be a JSON object")

    client = langgraph_client()
    thread = await _get_thread_tolerating_create_race(client, thread_id)
    if thread is None:
        raise HTTPException(404, "thread not found")
    metadata = thread_metadata(thread)
    await _assert_thread_postable(metadata, login, email)

    command = {
        "method": "run.start",
        "params": {
            "input": body_dict.get("input"),
            "config": body_dict.get("config"),
            "metadata": body_dict.get("metadata"),
        },
    }
    mentions = ThreadMentions(
        thread_id,
        login,
        _command_message_id(command["params"]) or str(uuid.uuid7()),
        _command_prompt_text(_command_message_content(command["params"])),
    )
    queued = await queue_follow_up_run(thread_id, login, command, metadata=metadata, email=email)
    run_id = queued.get("result", {}).get("run_id") if isinstance(queued, dict) else None
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(502, "LangGraph did not return a run id for the queued follow-up")
    mentions.dispatch()
    # The raw runs REST caller expects a `Run` object back, not the
    # `commands` protocol's `{id, type, result}` envelope `queue_follow_up_run`
    # returns — re-fetch the run it just created to answer in that shape.
    return MdaRun.client_view(await client.runs.get(thread_id, run_id))


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    reason: str | None = None,
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    metadata = await _authorized_thread_metadata(thread_id, login, email=email)
    await _assert_thread_postable(metadata, login, email)
    try:
        run = await langgraph_client().runs.get(thread_id, run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "run not found") from exc
    queued_by = (run.get("metadata") or {}).get(QUEUED_BY_KEY)
    if isinstance(queued_by, str) and login not in {queued_by, metadata.get("owner_login")}:
        raise HTTPException(403, "only its sender can withdraw a queued follow-up")
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/cancel"
    headers = langgraph_proxy_headers()
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=_PROXY_REQUEST_TIMEOUT
    ) as client:
        response = await client.post(
            url,
            headers=headers,
            params={
                "wait": wait,
                "action": action,
                **({"reason": reason} if reason is not None else {}),
            },
        )
    if response.status_code in {200, 202, 204}:
        # Cancelling a queued run leaves the live one untouched, so the thread
        # only reads as interrupted when nothing else is still running.
        try:
            still_running = [
                run
                for run in await langgraph_client().runs.list(thread_id, status="running", limit=5)
                if run.get("run_id") != run_id
            ]
            if not still_running:
                await langgraph_client().threads.update(
                    thread_id=thread_id,
                    metadata={
                        "latest_run_status": "interrupted",
                        "updated_at_ms": _now_ms(),
                    },
                )
        except Exception:
            logger.debug(
                "Could not update thread metadata after run cancel for %s",
                thread_id,
                exc_info=True,
            )
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type
