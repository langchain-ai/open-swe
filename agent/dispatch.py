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
  it did not start: the v3 protocol only synthesizes the ``lifecycle: running``
  event that drives ``stream.isLoading`` when it can replay the run's events, so
  a Slack/Linear/GitHub-triggered run looked idle in the web UI (no stop button)
  until it happened to emit its next event.
- the v3 run shape — the same ``stream_mode`` set, ``stream_subgraphs`` and
  compatibility marker that ``langgraph_api``'s ``run.start`` command applies
  when the dashboard submits a run. The server fixes a run's streaming protocol
  at creation: without the marker a run streams ``values`` only, so the dashboard
  sees no ``tools`` events or subagent namespaces for externally triggered runs.
"""

import logging
from typing import Any
from urllib.parse import urlparse

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Run

from agent.config import ENV
from agent.input_messages import RunInput
from agent.invocation import new_invocation_id, resolve_invocation_id, with_invocation_id

logger = logging.getLogger(__name__)

LangGraphRunConfig = dict[str, Any]

# The server's legacy-named compatibility marker selects the v3 stream path.
V3_STREAMING_CONFIG_KEY = "__event_streaming_v2"
# The dashboard's ``run.start`` defaults, minus protocol-only channels rejected by
# the REST ``POST /runs`` schema.
V3_RUN_STREAM_MODES: tuple[str, ...] = (
    "values",
    "updates",
    "messages",
    "custom",
    "tasks",
    "checkpoints",
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
_COMPLETION_WEBHOOK_BASE = ENV.COMPLETION_WEBHOOK_URL.optional() or "/webhooks/run-complete"
_RUN_COMPLETE_SECRET = ENV.RUN_COMPLETE_WEBHOOK_SECRET.optional()


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
    return ENV.LANGGRAPH_URL.get()


def dispatch_client() -> LangGraphClient:
    return get_client(url=_langgraph_url())


def prepare_run_config(
    config: LangGraphRunConfig | None,
    metadata: dict[str, Any] | None,
) -> LangGraphRunConfig:
    run_config = dict(config or {})
    configurable = run_config.get("configurable")
    configurable = dict(configurable) if isinstance(configurable, dict) else {}
    existing_metadata = run_config.get("metadata")
    merged_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    if metadata is not None:
        merged_metadata.update(metadata)
    invocation_id = resolve_invocation_id(configurable, merged_metadata) or new_invocation_id()
    configurable = with_invocation_id(configurable, invocation_id)
    configurable[V3_STREAMING_CONFIG_KEY] = True
    run_config["configurable"] = configurable
    run_config["metadata"] = with_invocation_id(merged_metadata, invocation_id)
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
        "stream_mode": list(V3_RUN_STREAM_MODES),
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
    configurable: dict[str, Any],
    *,
    source: str,
    input: RunInput,
    assistant_id: str = "agent",
    metadata: dict[str, Any] | None = None,
    client: LangGraphClient | None = None,
    multitask_strategy: str = "interrupt",
) -> Run:
    """Dispatch a durable run with caller-constructed, provenance-explicit input."""
    client = client or dispatch_client()
    if assistant_id == "agent" and source in {"slack", "web", "desktop", "dashboard"}:
        from agent.thread_feedback import note_feedback_activity

        await note_feedback_activity(thread_id, client=client)
    return await create_durable_run(
        thread_id,
        assistant_id,
        input=input,
        config={"configurable": configurable},
        metadata=metadata or {},
        source=source,
        client=client,
        multitask_strategy=multitask_strategy,
    )
