"""Pass-through endpoints between the dashboard and the LangGraph HTTP API."""

import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import nullcontext

import httpx
import httpx2
from fastapi import HTTPException
from pydantic import JsonValue

from openswe.config import ENV
from openswe.dashboard.ttft import DashboardClient, DashboardRunTimeline, DashboardSendTrace
from openswe.dispatch import channel_of, prepare_durable_run
from openswe.plugins.records import PluginChat
from openswe.threads.access import (
    _authorized_thread_metadata,
    _ensure_dashboard_github_token,
    _readable_thread,
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
    _SlackWebHandoff,
    offload_requested,
    queue_follow_up_run,
    steer_running_thread,
)
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.start_request import RunRefused, SentRun, StartRequest
from openswe.threads.store import POSTING_COMMANDS, THREADS, CommandReply
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
from openswe.utils.thread_ops import langgraph_client, langgraph_url
from openswe.utils.timing import phase

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
    """Answer one command, enriched for whoever sent it.

    ``run.start`` and ``input.respond`` start an old-format run through
    :class:`ThreadSend`; the rest are forwarded to the thread's runtime.

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
    raw_params = parsed.get("params")
    params: dict[str, JsonValue] = raw_params if isinstance(raw_params, dict) else {}
    if method != "run.start":
        # Input commands on admin threads require an admin; other threads keep
        # unattributed commands owner-only. Checked before anything is read off them.
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
        if principal.machine:
            raise HTTPException(403, "a machine principal may only start runs")

    if method in {"run.start", "input.respond"}:
        command_id = parsed.get("id", 0)
        try:
            start = (
                StartRequest.of_command(params)
                if method == "run.start"
                else StartRequest.answering(params)
            )
            sent = await ThreadSend(
                thread_id,
                principal,
                start,
                relayed_from=relayed_from,
                relayed_origin=relayed_origin,
                client=client,
                received_at_ms=received_at_ms,
                submitted_at=submitted_at,
            ).send()
        except RunRefused as refused:
            if refused.code is None:
                return CommandReply.json(refused.status_code, {"detail": refused.detail})
            return CommandReply.json(
                200,
                {
                    "type": "error",
                    "id": command_id,
                    "error": refused.code,
                    "message": refused.detail,
                },
            )
        return CommandReply.json(
            200,
            {
                "id": command_id,
                "type": "success",
                "result": sent.reply() if method == "run.start" else {},
            },
        )

    # Only a ``run.start`` takes these.
    params.pop(RELAYED_FROM_KEY, None)
    params.pop("multitask_strategy", None)
    try:
        return await THREADS.command(thread_id, json.dumps(parsed).encode())
    except Exception as exc:
        logger.exception(
            "Delivering a thread command failed",
            extra={
                "thread_id": thread_id,
                "command_method": method,
                "error_type": type(exc).__name__,
            },
        )
        return CommandReply.json(500, {"detail": "the command could not be delivered"})


class ThreadSend:
    """One send to a thread: a message that starts a run, steers into the live one or
    waits behind it as a queued run, or an answer to the thread's interrupt.

    ``/commands``, the runs routes and the backend's own senders start every run here, so
    who may post, enrichment and attribution, steering and queueing, the transcript turn
    and the run's bookkeeping can't drift apart between them. Each run is old-format, in
    the stream ``request`` asks for (:class:`StartRequest`). A thread the client minted the id
    of is created by its first message, unless ``creates`` is off, which leaves a missing
    thread a 404.

    ``principal`` is who sent it: a person, or a machine. ``relayed_from`` names the
    agent thread that sent the message on a person's behalf. ``client``,
    ``received_at_ms`` and ``submitted_at``, the client's own send time, only time the run.
    """

    def __init__(
        self,
        thread_id: str,
        principal: Principal,
        request: StartRequest,
        *,
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
        self.request = request
        self.creates = creates
        self.relayed_from = relayed_from
        self.relayed_origin = relayed_origin
        self.client = client
        self.received_at_ms = _now_ms() if received_at_ms is None else received_at_ms
        self.submitted_at_ms = DashboardRunTimeline.client_submitted_at(
            submitted_at, self.received_at_ms
        )

    async def send(self) -> SentRun:
        """Send it, and say which run it ended up in. A run that can't start raises
        :class:`RunRefused`."""
        if self.request.command is not None:
            return await self._answer(self.request.command)
        return await self._start()

    async def _start(self) -> SentRun:
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

        message = self.request.as_command(relayed_from=self.relayed_from)
        params = message["params"]
        assert isinstance(params, dict)
        mentions = ThreadMentions(
            self.thread_id,
            self.login,
            _command_message_id(params) or str(uuid.uuid7()),
            _command_prompt_text(_command_message_content(params))
            if not self.principal.machine and not self.relayed_from
            else "",
        )
        offloading = offload_requested(params)
        if thread_busy:
            sent = await self._follow_up(
                message,
                metadata,
                enqueue=self.request.multitask_strategy == "enqueue",
                offloading=offloading,
            )
            mentions.dispatch()
            return sent
        # A start that loses the thread to another run is queued as it was sent.
        requeueable = not creating and not self.principal.machine and not offloading

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
        built = await self._build(message, send_trace, metadata, creating=creating)
        try:
            run_id = await self._deliver(built, send_trace, sender=await self._sender(metadata))
        except RunRefused as refused:
            if send_trace is not None:
                send_trace.fail(f"The run did not start (HTTP {refused.status_code})")
            if requeueable:
                requeued = await self._queue_if_thread_turned_busy()
                if requeued is not None:
                    logger.info(
                        "Queued a start refused for a run begun since the busy check",
                        extra={"thread_id": self.thread_id, "status_code": refused.status_code},
                    )
                    mentions.dispatch()
                    return requeued
            await self._fail_requested_turn(built, refused.status_code)
            raise
        if send_trace is not None:
            send_trace.end_commands()

        if not self.principal.machine and chats_beside_plugin(metadata):
            user = await User.for_person(ThreadIdentity(self.login).person)
            if user:
                await PluginChat.deliver_input(self.thread_id, user.id, built.input)
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
        return SentRun(run_id=run_id)

    async def _answer(self, command: dict[str, JsonValue]) -> SentRun:
        """Answer the thread's interrupt with a run's ``command``.

        The same people may answer as may post, and no message joins the transcript. The
        run that resumes the thread is its sender's, made for them as a message's would be.
        """
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
        )
        run_id = await THREADS.start_run(
            self.thread_id,
            StartRequest(
                configurable=config["configurable"],
                metadata=config["metadata"],
                command=command,
                run_format=self.request.run_format,
            ),
            user_id=await self._sender(metadata),
        )
        return SentRun(run_id=run_id)

    async def _sender(self, metadata: Mapping[str, object]) -> str | None:
        """The Open SWE user the send acts as on a channel's thread: none when relayed."""
        if self.relayed_from:
            return None
        return await _sender_user_id(self.principal, self.login, metadata)

    async def _follow_up(
        self,
        message: dict[str, JsonValue],
        metadata: dict[str, JsonValue],
        *,
        enqueue: bool,
        offloading: bool,
    ) -> SentRun:
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
                message,
                metadata=metadata,
                turn=turn,
                email=self.email,
                relayed_origin=self.relayed_origin,
                run_format=self.request.run_format,
            )
        return await queue_follow_up_run(
            self.thread_id,
            self.login,
            message,
            metadata=metadata,
            email=self.email,
            relayed_origin=self.relayed_origin,
            run_format=self.request.run_format,
        )

    async def _build(
        self,
        message: dict[str, JsonValue],
        send_trace: DashboardSendTrace | None,
        metadata: dict[str, JsonValue],
        *,
        creating: bool,
    ) -> StartRequest:
        """The run built for whoever sent the message, with what times the send."""
        try:
            with send_trace.stage("build_run") if send_trace is not None else nullcontext():
                if self.principal.machine:
                    enriched = await _enrich_system_run_start_command(
                        self.thread_id,
                        self.principal,
                        message,
                        metadata=metadata,
                        creating=creating,
                    )
                else:
                    enriched = await _enrich_run_start_command(
                        self.thread_id,
                        self.login,
                        message,
                        metadata=metadata,
                        creating=creating,
                        email=self.email,
                        relayed_origin=self.relayed_origin,
                    )
        except BaseException as exc:
            if send_trace is not None:
                send_trace.fail(f"The run could not be built ({type(exc).__name__})")
            raise
        built = self.request.built_from(enriched)
        # The run's completion times the send from these (``DashboardRunTimeline``).
        built.metadata[DashboardRunTimeline.RECEIVED_AT_KEY] = self.received_at_ms
        built.metadata[DashboardRunTimeline.CLIENT_KEY] = self.client.value
        if self.submitted_at_ms is not None:
            built.metadata[DashboardRunTimeline.SUBMITTED_AT_KEY] = self.submitted_at_ms
        if send_trace is not None:
            invocation_id = built.metadata.get("invocation_id")
            await send_trace.adopt(
                thread_id=self.thread_id,
                invocation_id=invocation_id if isinstance(invocation_id, str) else None,
            )
            built.metadata.update(send_trace.run_metadata())
        return built

    async def _deliver(
        self, built: StartRequest, send_trace: DashboardSendTrace | None, *, sender: str | None
    ) -> str:
        try:
            with send_trace.stage("start_run") if send_trace is not None else nullcontext():
                return await THREADS.start_run(
                    self.thread_id,
                    built,
                    user_id=sender,
                    trace_headers=send_trace.headers() if send_trace is not None else None,
                )
        except RunRefused:
            raise
        except Exception as exc:
            logger.exception(
                "Delivering a thread command failed",
                extra={
                    "thread_id": self.thread_id,
                    "command_method": "run.start",
                    "error_type": type(exc).__name__,
                },
            )
            raise RunRefused(
                500,
                "No connection was available to start the message. Try sending it again."
                if isinstance(exc, (httpx.PoolTimeout, httpx2.PoolTimeout))
                else "the command could not be delivered",
            ) from exc

    async def _queue_if_thread_turned_busy(self) -> SentRun | None:
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
            self.request.as_command(relayed_from=self.relayed_from),
            metadata=dict(thread.metadata),
            email=self.email,
            relayed_origin=self.relayed_origin,
            run_format=self.request.run_format,
        )

    async def _fail_requested_turn(self, built: StartRequest, status: int) -> None:
        """Record a failed start and close its turn so later follow-ups cannot join it."""
        turn_id = built.configurable.get("transcript_turn_id")
        logger.warning(
            "Dashboard run start failed",
            extra={
                "thread_id": self.thread_id,
                "turn_id": turn_id,
                "status_code": status,
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
    timings: dict[str, float] | None = None,
) -> tuple[int, bytes, str | None]:
    record = timings if timings is not None else {}
    require_json_content_type(content_type)
    with phase(record, "thread_get"):
        thread = await _readable_thread(thread_id, login=login, email=email)
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
    with phase(record, "history"):
        if thread.runtime == "mda":
            mda = RUNTIMES.mda_runtime
            if channel_of(thread.metadata) is None:
                # The channel created its threads there; the backend's own may need it.
                await mda.ensure_remote_thread(thread_id)
            async with mda.read_http as client:
                # MDA's read pool never times a read out, so this one gives up as the loopback did.
                response = await client.post(
                    f"/threads/{thread_id}/history", json=payload, timeout=_PROXY_REQUEST_TIMEOUT
                )
        else:
            url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/history"
            headers = langgraph_proxy_headers(content_type=content_type)
            async with httpx2.AsyncClient(
                transport=pooled_transport(), timeout=_PROXY_REQUEST_TIMEOUT
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


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
