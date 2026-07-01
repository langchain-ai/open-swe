"""Single durable dispatch contract behind every agent/reviewer run trigger.

Replaces the per-site ``runs.create`` calls (plus the ``is_thread_active``
busy-check and the custom store-queue) with one function that always uses:

- ``multitask_strategy="interrupt"`` — a follow-up halts the active run
  (progress preserved by the sync checkpoint) and resumes the agent with full
  history + the new message; on an idle thread it just starts. This is the
  platform-native, cross-process replacement for the racy busy-check + queue.
- ``durability="sync"`` — checkpoint before each step so a crash/recycle
  resumes from the last checkpoint instead of losing all work.
- ``webhook=COMPLETION_WEBHOOK_URL`` — the platform calls us on completion or
  failure so every run ends with a signal even if the agent died.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

logger = logging.getLogger(__name__)

ContentBlocks = str | list[dict[str, Any]]

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


async def dispatch_agent_run(
    thread_id: str,
    content: ContentBlocks,
    configurable: dict[str, Any],
    *,
    source: str,
    assistant_id: str = "agent",
    metadata: dict[str, Any] | None = None,
    client: LangGraphClient | None = None,
) -> dict[str, Any]:
    """Create (or interrupt-and-resume) a run for ``thread_id``.

    Routes every Slack / Linear / GitHub / dashboard trigger through one
    contract. ``source`` is for logging/metadata only; ``assistant_id`` selects
    the graph (``"agent"`` or ``"reviewer"``).
    """
    client = client or dispatch_client()
    run = await client.runs.create(
        thread_id,
        assistant_id,
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": configurable, "metadata": metadata or {}},
        multitask_strategy="interrupt",
        durability="sync",
        webhook=COMPLETION_WEBHOOK_URL,
        if_not_exists="create",
    )
    logger.info(
        "Dispatched %s run on thread %s (source=%s, run=%s)",
        assistant_id,
        thread_id,
        source,
        run.get("run_id") if isinstance(run, dict) else None,
    )
    return run
