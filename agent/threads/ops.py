"""Shared LangGraph thread reads and metadata writes.

Everything here talks to the platform over the HTTP client, so it is for
out-of-process callers (webhooks, dashboard jobs) — code running inside a
graph reads thread state through ``agent.review.findings`` instead.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``agent.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The store-queue
below is retained for the dashboard's deliberate "inject a follow-up into a
run that's already in flight" path (``thread_api.send_dashboard_message``).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk.client import LangGraphClient

from ..config import langgraph_client
from ..settings.agent_overrides import resolve_login_from_email_async
from ..slack.api import get_slack_permalink
from ..utils.json_types import as_thread_dict
from .participants import PARTICIPANT_LOGINS_KEY, merge_participant_logins

logger = logging.getLogger(__name__)

MAX_QUEUED_MESSAGES = 100


def is_not_found_error(exc: Exception) -> bool:
    """Whether a LangGraph failure is a 404 rather than an outage.

    The SDK raises its own ``NotFoundError`` (status on the exception) for
    typed endpoints and a bare ``httpx.HTTPStatusError`` (status on the
    response) elsewhere, so both shapes have to be recognised — everything that
    isn't a 404 has to stay distinguishable from "the resource is missing".
    """
    if getattr(exc, "status_code", None) == 404:  # noqa: PLR2004
        return True
    return getattr(getattr(exc, "response", None), "status_code", None) == 404  # noqa: PLR2004


async def thread_exists(thread_id: str) -> bool:
    """Whether a LangGraph thread already exists (assumes yes when unreachable)."""
    client = langgraph_client()
    try:
        await client.threads.get(thread_id)
        return True
    except Exception as exc:  # noqa: BLE001
        if is_not_found_error(exc):
            return False
        logger.warning("Failed to fetch thread %s, assuming it exists", thread_id)
        return True


async def ensure_thread_exists(thread_id: str, client: LangGraphClient) -> bool:
    """Create the thread if it is missing; False when even that failed."""
    try:
        await client.threads.create(thread_id=thread_id, if_exists="do_nothing")
        return True
    except Exception:
        logger.exception("Failed to ensure thread %s exists before metadata update", thread_id)
        return False


async def fetch_thread_metadata(thread_id: str) -> dict[str, Any] | None:
    """A thread's metadata, or ``None`` when the thread doesn't exist.

    Same error policy as ``agent.store``: a missing thread reads as ``None``,
    every other failure raises. Callers route and authorize on these fields and
    read a missing key as a permissive default, so a transport blip must not be
    allowed to masquerade as "the thread has no metadata".
    """
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:
        if is_not_found_error(exc):
            return None
        raise
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return metadata if isinstance(metadata, dict) else {}


async def get_thread_plan_mode(thread_id: str) -> bool | None:
    """The persisted plan-mode flag for a thread, or ``None`` if unset."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        if is_not_found_error(exc):
            return None
        logger.warning("Failed to fetch plan-mode metadata for thread %s", thread_id)
        return None
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("plan_mode")
    return value if isinstance(value, bool) else None


async def set_thread_plan_mode(thread_id: str, enabled: bool) -> None:
    """Persist the plan-mode flag onto thread metadata."""
    client = langgraph_client()
    try:
        await client.threads.update(thread_id=thread_id, metadata={"plan_mode": bool(enabled)})
    except Exception as exc:  # noqa: BLE001
        if is_not_found_error(exc):
            try:
                await client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"plan_mode": bool(enabled)},
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to create thread %s while persisting plan_mode", thread_id)
            return
        logger.exception("Failed to persist plan_mode for thread %s", thread_id)


async def get_thread_environment(thread_id: str) -> str | None:
    """The environment slug persisted for a thread, or ``None`` if unset."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        if not is_not_found_error(exc):
            logger.warning("Failed to fetch environment metadata for thread %s", thread_id)
        return None
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("environment")
    return value.strip() or None if isinstance(value, str) else None


def _existing_slack_permalink(
    existing_metadata: dict[str, Any], channel_id: str, thread_ts: str
) -> str | None:
    source_context = existing_metadata.get("source_context")
    if not isinstance(source_context, dict):
        return None
    slack_thread = source_context.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return None
    if slack_thread.get("channel_id") != channel_id or slack_thread.get("thread_ts") != thread_ts:
        return None
    permalink = slack_thread.get("permalink")
    return permalink.strip() if isinstance(permalink, str) and permalink.strip() else None


async def _source_context_with_slack_permalink(
    source_context: dict[str, Any],
    existing_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(source_context)
    slack_thread = enriched.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return enriched

    enriched_slack_thread = dict(slack_thread)
    permalink = enriched_slack_thread.get("permalink")
    if isinstance(permalink, str) and permalink.strip():
        enriched_slack_thread["permalink"] = permalink.strip()
        enriched["slack_thread"] = enriched_slack_thread
        return enriched

    channel_id = enriched_slack_thread.get("channel_id")
    thread_ts = enriched_slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id.strip():
        return enriched
    if not isinstance(thread_ts, str) or not thread_ts.strip():
        return enriched

    normalized_channel_id = channel_id.strip()
    normalized_thread_ts = thread_ts.strip()
    try:
        permalink = await get_slack_permalink(normalized_channel_id, normalized_thread_ts)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to resolve Slack permalink for thread metadata", exc_info=True)
        permalink = None
    if not permalink and existing_metadata:
        permalink = _existing_slack_permalink(
            existing_metadata, normalized_channel_id, normalized_thread_ts
        )
    if permalink:
        enriched_slack_thread["permalink"] = permalink
        enriched["slack_thread"] = enriched_slack_thread
    return enriched


async def upsert_agent_thread_owner_metadata(
    thread_id: str,
    *,
    source: str,
    repo_config: dict[str, str] | None = None,
    github_login: str = "",
    user_email: str = "",
    title: str = "",
    source_context: dict[str, Any] | None = None,
    environment: str | None = None,
) -> None:
    """Persist owner/source metadata so the dashboard can surface non-dashboard threads.

    Webhook-triggered runs only pass ``source``/``github_login`` through the run
    config; the Agents UI lists and authorizes threads by thread *metadata*, so we
    mirror the owner-identifying fields onto the thread here.
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    category = "interactive"
    if isinstance(source_context, dict):
        if source_context.get("github_issue") or source_context.get("linear_issue"):
            category = "issue"
        elif source_context.get("pr_number"):
            category = "pull_request"
    metadata: dict[str, Any] = {
        "source": source,
        "origin": source,
        "thread_category": category,
        "trigger_kind": "user",
        "updated_at_ms": now_ms,
    }
    if isinstance(repo_config, dict) and repo_config.get("owner") and repo_config.get("name"):
        metadata["repo"] = repo_config
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    if title:
        metadata["title"] = title[:80]
    if environment:
        metadata["environment"] = environment

    client = langgraph_client()
    try:
        existing = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        if not is_not_found_error(exc):
            logger.exception("Failed to read thread %s for owner metadata", thread_id)
        existing = None

    existing_dict = as_thread_dict(existing) if existing is not None else {}
    existing_meta = (
        existing_dict["metadata"] if isinstance(existing_dict.get("metadata"), dict) else {}
    )
    existing_context = existing_meta.get("source_context")
    if github_login:
        metadata[PARTICIPANT_LOGINS_KEY] = merge_participant_logins(
            existing_meta.get(PARTICIPANT_LOGINS_KEY), github_login
        )
    same_slack_owner = bool(
        isinstance(existing_context, dict)
        and isinstance(source_context, dict)
        and isinstance(existing_slack := existing_context.get("slack_thread"), dict)
        and isinstance(incoming_slack := source_context.get("slack_thread"), dict)
        and existing_slack.get("triggering_user_id")
        and existing_slack["triggering_user_id"] == incoming_slack.get("triggering_user_id")
    )
    owner_initialized = any(
        existing_meta.get(key) for key in ("github_login", "triggering_user_email")
    ) or bool(existing_context and not same_slack_owner)
    if not owner_initialized:
        resolved_login = github_login or await resolve_login_from_email_async(user_email) or ""
        if resolved_login:
            metadata["github_login"] = resolved_login
        if user_email:
            metadata["triggering_user_email"] = user_email.strip().lower()
    else:
        source_context = existing_context if isinstance(existing_context, dict) else None
    if source_context:
        metadata["source_context"] = await _source_context_with_slack_permalink(
            source_context, existing_meta
        )
    if existing_meta.get("created_at_ms") is None:
        metadata["created_at_ms"] = now_ms
    if existing_meta.get("title") and "title" in metadata:
        # Preserve a title that was already chosen (first message wins).
        metadata.pop("title")
    elif source == "slack" and "title" in metadata:
        metadata["title_seed"] = metadata["title"]

    try:
        if existing is None:
            await client.threads.create(
                thread_id=thread_id, if_exists="do_nothing", metadata=metadata
            )
        else:
            await client.threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist owner metadata for thread %s", thread_id)


async def get_thread_active_status(thread_id: str) -> bool | None:
    """Return whether the thread is active, or None when status cannot be determined."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
        status = thread.get("status", "idle") if isinstance(thread, dict) else "idle"
        logger.info("Thread %s status check: status=%s", thread_id, status)
        return status == "busy"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get thread status for %s: %s", thread_id, exc)
        return None


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a follow-up message for a busy thread (FIFO store namespace).

    Used by the dashboard to inject a follow-up into a run that's already in
    flight; webhook triggers use ``multitask_strategy="interrupt"`` instead.
    """
    client = langgraph_client()
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"
        new_message = {"content": message_content}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        if len(existing_messages) > MAX_QUEUED_MESSAGES:
            existing_messages = existing_messages[-MAX_QUEUED_MESSAGES:]
            logger.warning(
                "Thread %s queue capped at %d messages (dropped oldest)",
                thread_id,
                MAX_QUEUED_MESSAGES,
            )
        await client.store.put_item(namespace, key, {"messages": existing_messages})
        logger.info(
            "Queued message for thread %s (total queued: %d)",
            thread_id,
            len(existing_messages),
        )
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False
