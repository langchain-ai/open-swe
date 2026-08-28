"""Single durable dispatch contract behind every agent/reviewer run trigger.

Replaces the per-site ``runs.create`` calls (plus the ``is_thread_active``
busy-check and the custom store-queue) with one function that uses:

- ``multitask_strategy="interrupt"`` by default — a follow-up halts the active run
  (progress preserved by the sync checkpoint) and resumes the agent with full
  history + the new message; on an idle thread it just starts. Background
  follow-ups such as `/baby-sit` can opt into ``enqueue`` instead.
- ``durability="sync"`` — checkpoint before each step so a crash/recycle
  resumes from the last checkpoint instead of losing all work.
- ``webhook=COMPLETION_WEBHOOK_URL`` — the platform calls us on completion or
  failure so every run ends with a signal even if the agent died.
- ``stream_resumable=True`` — the run's event stream is retained so a client that
  attaches later can replay it. Without this the dashboard cannot observe a run
  it did not start: the v2 protocol only synthesizes the ``lifecycle: running``
  event that drives ``stream.isLoading`` when it can replay the run's events, so
  a Slack/Linear/GitHub-triggered run looked idle in the web UI (no stop button)
  until it happened to emit its next event.
- the Protocol v2 run shape — the same ``stream_mode`` set, ``stream_subgraphs``
  and ``configurable`` marker that ``langgraph_api``'s ``run.start`` command
  applies when the dashboard submits a run. The server fixes a run's streaming
  protocol at creation: without the marker a run streams ``values`` only, so
  the dashboard saw no ``tools`` events and no subagent namespaces for runs
  triggered outside it (subagent cards never showed nested activity).
"""

import logging
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Run

from agent.run_config import RunConfig

from .input_messages import (
    ChannelIdentity,
    InputMessageContext,
    PersonIdentity,
    RunInput,
    Surface,
    SystemIdentity,
    build_run_input,
)

logger = logging.getLogger(__name__)

ContentBlocks = str | list[dict[str, Any]]
LangGraphRunConfig = dict[str, Any]

# Mirrors ``langgraph_api.event_streaming``'s ``EVENT_STREAMING_V2_CONFIG_KEY``.
# Not imported: ``langgraph-api`` is the serving runtime, not a dependency of
# this package. The marker alone selects the v3 stream path, which emits every
# protocol channel (``tools``, ``lifecycle``, namespaced subagent events)
# regardless of ``stream_mode``.
EVENT_STREAMING_V2_CONFIG_KEY = "__event_streaming_v2"
# The dashboard's ``run.start`` defaults, minus ``tools`` / ``lifecycle``: those
# are protocol channels the REST ``POST /runs`` schema does not accept.
V2_RUN_STREAM_MODES: tuple[str, ...] = (
    "values",
    "updates",
    "messages",
    "custom",
    "tasks",
    "checkpoints",
)


def _dispatch_input(content: ContentBlocks, source: str, configurable: dict[str, Any]) -> RunInput:
    surface: Surface = (
        source
        if source in {"slack", "linear", "github", "web", "desktop", "eval"}
        else "automation"
    )  # type: ignore[assignment]
    people: list[PersonIdentity] = []
    channels: list[ChannelIdentity] = []
    systems: list[SystemIdentity] = []
    cfg = RunConfig.parse(configurable)
    login = cfg.github_login
    email = cfg.user_email
    slack_thread = cfg.slack_thread
    sender_id = ""
    channel_id: str | None = None
    if surface == "slack" and slack_thread is not None:
        if slack_thread.triggering_user_id:
            sender_id = f"slack:{slack_thread.triggering_user_id}"
            person: PersonIdentity = {"id": sender_id, "platform": "slack"}
            if slack_thread.triggering_user_name:
                person["display_name"] = slack_thread.triggering_user_name
            if slack_thread.triggering_user_timezone:
                person["timezone"] = slack_thread.triggering_user_timezone
            if login:
                person["github_login"] = login
            if email:
                person["email"] = email
            people.append(person)
        if slack_thread.channel_id:
            channel_id = f"slack:{slack_thread.channel_id}"
            channel: ChannelIdentity = {"id": channel_id, "platform": "slack"}
            channel_context = slack_thread.channel_context
            if isinstance(channel_context, dict):
                name = channel_context.get("name") or channel_context.get("name_normalized")
                topic = channel_context.get("topic")
                purpose = channel_context.get("purpose")
                if isinstance(name, str) and name:
                    channel["name"] = name
                if isinstance(topic, str) and topic:
                    channel["topic"] = topic
                if isinstance(purpose, str) and purpose:
                    channel["purpose"] = purpose
            if slack_thread.thread_ts:
                channel["thread_id"] = slack_thread.thread_ts
            channels.append(channel)
    if not sender_id and login:
        sender_id = f"github:{login}"
        person = {"id": sender_id, "platform": "github", "github_login": login}
        if email:
            person["email"] = email
        people.append(person)
    if not sender_id and surface == "linear" and email:
        sender_id = f"linear:{email.lower()}"
        people.append({"id": sender_id, "platform": "linear", "email": email})
    kind = "human" if sender_id else "system"
    if not sender_id:
        sender_id = f"system:{source.replace('_', '-')}"
        systems.append(
            {
                "id": sender_id,
                "display_name": source.replace("-", " ").title(),
                "platform": "open-swe",
            }
        )
    context: InputMessageContext = {
        "sender_id": sender_id,
        "surface": surface,
        "kind": kind,
    }
    if channel_id:
        context["channel_id"] = channel_id
    return build_run_input(
        content,
        context,
        people=people,
        channels=channels,
        systems=systems,
    )


# FastAPI route the platform POSTs run completion/failure to. The platform
# rejects loopback webhooks (relative URLs / localhost) — they bypass auth via
# the in-process ASGI transport — so a loopback URL would 422 *every* run at
# create time. COMPLETION_WEBHOOK_URL must therefore be the deployment's
# absolute https URL (…/webhooks/run-complete). The route is fail-closed on
# RUN_COMPLETE_WEBHOOK_SECRET, so we only attach the webhook when the secret is
# set, appending it as ?token= so the route can verify the call came from us
# (completion.verify_run_complete_token). Secret unset, or URL relative/loopback
# → no webhook attached (the completion reply is best-effort; it must never
# break run creation).
_COMPLETION_WEBHOOK_BASE = os.environ.get("COMPLETION_WEBHOOK_URL") or "/webhooks/run-complete"
_RUN_COMPLETE_SECRET = os.environ.get("RUN_COMPLETE_WEBHOOK_SECRET")


def _is_loopback_webhook(url: str) -> bool:
    """Whether a webhook URL is relative or points at localhost (platform-rejected)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return True  # relative / schemeless
    return (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def _resolve_completion_webhook_url(base: str, secret: str | None) -> str | None:
    """Resolve the completion webhook URL, or None to attach no webhook.

    Degrades to None (with a warning) for a relative/loopback URL rather than
    letting a rejected webhook poison every ``runs.create``.
    """
    if not secret:
        return None
    if _is_loopback_webhook(base):
        logger.warning(
            "RUN_COMPLETE_WEBHOOK_SECRET is set but COMPLETION_WEBHOOK_URL (%r) is relative "
            "or loopback; the platform rejects such webhooks, so run-completion replies are "
            "disabled. Set COMPLETION_WEBHOOK_URL to the deployment's absolute https URL "
            "ending in /webhooks/run-complete to enable them.",
            base,
        )
        return None
    if "?" in base:
        return base
    return f"{base}?token={secret}"


COMPLETION_WEBHOOK_URL: str | None = _resolve_completion_webhook_url(
    _COMPLETION_WEBHOOK_BASE, _RUN_COMPLETE_SECRET
)


def _langgraph_url() -> str:
    return os.environ.get("LANGGRAPH_URL") or os.environ.get(
        "LANGGRAPH_URL_PROD", "http://localhost:2024"
    )


def dispatch_client() -> LangGraphClient:
    return get_client(url=_langgraph_url())


def prepare_run_config(
    config: LangGraphRunConfig | None,
    metadata: dict[str, Any] | None,
) -> LangGraphRunConfig:
    run_config = dict(config or {})
    configurable = run_config.get("configurable")
    configurable = dict(configurable) if isinstance(configurable, dict) else {}
    configurable.setdefault("prepare_run_id", str(uuid.uuid4()))
    configurable[EVENT_STREAMING_V2_CONFIG_KEY] = True
    run_config["configurable"] = configurable
    existing_metadata = run_config.get("metadata")
    merged_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    if metadata is not None:
        merged_metadata.update(metadata)
    merged_metadata["prepare_run_id"] = configurable["prepare_run_id"]
    run_config["metadata"] = merged_metadata
    return run_config


async def create_durable_run(
    thread_id: str,
    assistant_id: str,
    *,
    input: RunInput,
    source: str,
    config: LangGraphRunConfig | None = None,
    metadata: dict[str, Any] | None = None,
    client: LangGraphClient | None = None,
    multitask_strategy: str = "interrupt",
    durability: str = "sync",
    if_not_exists: str = "create",
    stream_resumable: bool = True,
    after_seconds: int | float | None = None,
) -> Run:
    """Create a run with Open SWE's durable LangGraph defaults."""
    client = client or dispatch_client()
    run_config = prepare_run_config(config, metadata)
    create_kwargs: dict[str, Any] = {
        "input": input,
        "config": run_config,
        "metadata": run_config["metadata"],
        "multitask_strategy": multitask_strategy,
        "durability": durability,
        "if_not_exists": if_not_exists,
        "stream_mode": list(V2_RUN_STREAM_MODES),
        "stream_subgraphs": True,
        "stream_resumable": stream_resumable,
    }
    if COMPLETION_WEBHOOK_URL:
        create_kwargs["webhook"] = COMPLETION_WEBHOOK_URL
    if after_seconds is not None:
        create_kwargs["after_seconds"] = after_seconds

    run = await client.runs.create(thread_id, assistant_id, **create_kwargs)
    logger.info(
        "Dispatched %s run on thread %s (source=%s, run=%s)",
        assistant_id,
        thread_id,
        source,
        run.get("run_id") if isinstance(run, dict) else None,
    )
    return run


async def dispatch_agent_run(
    thread_id: str,
    content: ContentBlocks | None,
    configurable: dict[str, Any],
    *,
    source: str,
    input: RunInput | None = None,
    context: InputMessageContext | None = None,
    people: list[PersonIdentity] | None = None,
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    assistant_id: str = "agent",
    metadata: dict[str, Any] | None = None,
    client: LangGraphClient | None = None,
    multitask_strategy: str = "interrupt",
) -> Run:
    """Create a durable run for ``thread_id`` using the requested multitask strategy.

    Routes every Slack / Linear / GitHub / dashboard trigger through one
    contract. ``source`` is for logging/metadata only; ``assistant_id`` selects
    the graph (``"agent"`` or ``"reviewer"``).
    """
    if input is not None and any(
        value is not None for value in (content, context, people, channels, systems)
    ):
        raise ValueError("prebuilt input cannot be combined with content or source identities")
    if input is None:
        if content is None:
            raise ValueError("content is required when input is not provided")
        input = (
            build_run_input(
                content,
                context,
                people=people,
                channels=channels,
                systems=systems,
            )
            if context is not None
            else _dispatch_input(content, source, configurable)
        )
    return await create_durable_run(
        thread_id,
        assistant_id,
        input=input,
        config={"configurable": configurable},
        metadata=metadata or {},
        source=source,
        client=client or dispatch_client(),
        multitask_strategy=multitask_strategy,
    )
