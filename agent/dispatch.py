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
import time
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

logger = logging.getLogger(__name__)

ContentBlocks = str | list[dict[str, Any]]

# Cold-start preamble (repo clone, git config, AGENTS.md read, `gh pr checks`,
# `gh run view`) repeats verbatim across back-to-back dispatches on the same PR
# when each dispatch lands on a fresh thread (e.g. CI-autofix retries that lose
# their parent thread). We cache a small fingerprint of recent PR dispatches in
# this process and surface it to the next dispatch as `_cached_pr_context`, so
# the agent's prompt can short-circuit the preamble when the context is fresh.
_PR_CONTEXT_TTL_SECONDS = int(os.environ.get("DISPATCH_PR_CONTEXT_TTL_SECONDS", "1800"))
_PR_CONTEXT_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}


def _pr_context_key(configurable: dict[str, Any]) -> tuple[str, str, int] | None:
    """Return the (owner, name, pr_number) cache key for a dispatch, or None."""
    pr_number = configurable.get("pr_number")
    repo = configurable.get("repo") or {}
    if not isinstance(pr_number, int) or pr_number <= 0:
        return None
    if not isinstance(repo, dict):
        return None
    owner = str(repo.get("owner") or "")
    name = str(repo.get("name") or "")
    if not owner or not name:
        return None
    return (owner, name, pr_number)


def _lookup_cached_pr_context(key: tuple[str, str, int], *, now: float) -> dict[str, Any] | None:
    """Return a fresh cached entry for `key`, or None when missing/stale."""
    entry = _PR_CONTEXT_CACHE.get(key)
    if not entry:
        return None
    age = now - float(entry.get("recorded_at", 0.0))
    if age > _PR_CONTEXT_TTL_SECONDS:
        _PR_CONTEXT_CACHE.pop(key, None)
        return None
    return {**entry, "age_seconds": int(age)}


def _record_pr_context(
    key: tuple[str, str, int],
    *,
    thread_id: str,
    configurable: dict[str, Any],
    now: float,
) -> None:
    """Record that a dispatch just happened for this PR so the next one can reuse it."""
    owner, name, pr_number = key
    _PR_CONTEXT_CACHE[key] = {
        "recorded_at": now,
        "thread_id": thread_id,
        "repo": {"owner": owner, "name": name},
        "pr_number": pr_number,
        "branch": configurable.get("branch") or configurable.get("head_ref") or "",
    }


# Same-server FastAPI route the platform POSTs run completion/failure to. A
# relative URL loopback-posts into this app (no SSRF/loopback config needed);
# override with an absolute URL via env for split deployments. The route is
# fail-closed on RUN_COMPLETE_WEBHOOK_SECRET, so only register the webhook when
# the secret is set, appending it as ?token= so the route can verify the call
# came from us (completion.verify_run_complete_token). Unset → no webhook.
_COMPLETION_WEBHOOK_BASE = os.environ.get("COMPLETION_WEBHOOK_URL") or "/webhooks/run-complete"
_RUN_COMPLETE_SECRET = os.environ.get("RUN_COMPLETE_WEBHOOK_SECRET")
COMPLETION_WEBHOOK_URL: str | None
if not _RUN_COMPLETE_SECRET:
    COMPLETION_WEBHOOK_URL = None
elif "?" in _COMPLETION_WEBHOOK_BASE:
    COMPLETION_WEBHOOK_URL = _COMPLETION_WEBHOOK_BASE
else:
    COMPLETION_WEBHOOK_URL = f"{_COMPLETION_WEBHOOK_BASE}?token={_RUN_COMPLETE_SECRET}"


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
    cache_key = _pr_context_key(configurable)
    if cache_key is not None:
        now = time.time()
        cached = _lookup_cached_pr_context(cache_key, now=now)
        if cached is not None and cached.get("thread_id") != thread_id:
            configurable = {**configurable, "_cached_pr_context": cached}
            logger.info(
                "Reusing cached PR context for %s/%s#%s (age=%ss, prior_thread=%s)",
                cache_key[0],
                cache_key[1],
                cache_key[2],
                cached.get("age_seconds"),
                cached.get("thread_id"),
            )
        _record_pr_context(cache_key, thread_id=thread_id, configurable=configurable, now=now)
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
