"""Custom FastAPI routes for LangGraph server."""

import hashlib
import hmac
import json
import logging
import os
import re
import uuid
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from langchain_core.messages.content import create_text_block
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from .utils.comments import get_recent_comments
from .utils.github_pr_webhook import (
    collect_comments_since_last_tag,
    extract_thread_id_from_branch,
    fetch_issue_comments,
    fetch_pr_review_comments,
    fetch_pr_reviews,
    format_review_comment_for_prompt,
    react_to_github_comment,
    verify_github_signature,
)
from .utils.multimodal import dedupe_urls, extract_image_urls, fetch_image_block
from .utils.slack import (
    add_slack_reaction,
    fetch_slack_thread_messages,
    format_slack_messages_for_prompt,
    get_slack_user_info,
    get_slack_user_names,
    post_slack_thread_reply,
    select_slack_context_messages,
    strip_bot_mention,
    verify_slack_signature,
)

logger = logging.getLogger(__name__)

app = FastAPI()

LINEAR_WEBHOOK_SECRET = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "")
SLACK_BOT_USERNAME = os.environ.get("SLACK_BOT_USERNAME", "")
SLACK_REPO_OWNER = os.environ.get("SLACK_REPO_OWNER", "langchain-ai")
SLACK_REPO_NAME = os.environ.get("SLACK_REPO_NAME", "open-swe")

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")


LINEAR_TEAM_TO_REPO: dict[str, dict[str, Any] | dict[str, str]] = {
    "Brace's test workspace": {"owner": "langchain-ai", "name": "open-swe"},
    "Yogesh-dev": {
        "projects": {
            "open-swe-v3-test": {"owner": "aran-yogesh", "name": "nimedge"},
            "open-swe-dev-test": {"owner": "aran-yogesh", "name": "TalkBack"},
        },
        "default": {
            "owner": "aran-yogesh",
            "name": "TalkBack",
        },  # Fallback for issues without project
    },
    "LangChain OSS": {
        "projects": {
            "deepagents": {"owner": "langchain-ai", "name": "deepagents"},
            "langchain": {"owner": "langchain-ai", "name": "langchain"},
        }
    },
    "Applied AI": {
        "projects": {
            "GTM Engineering": {"owner": "langchain-ai", "name": "ai-sdr"},
        },
        "default": {"owner": "langchain-ai", "name": "ai-sdr"},
    },
    "Docs": {"default": {"owner": "langchain-ai", "name": "docs"}},
    "Open SWE": {"default": {"owner": "langchain-ai", "name": "open-swe"}},
}


def get_repo_config_from_team_mapping(
    team_identifier: str, project_name: str = ""
) -> dict[str, str]:
    """
    Look up repository configuration from LINEAR_TEAM_TO_REPO mapping.

    Supports both legacy flat mapping (team -> repo) and new nested mapping (team -> project -> repo).

    Args:
        team_identifier: Team name or ID to look up (e.g., "LangChain OSS")
        project_name: Name of the project (e.g., "deepagents")

    Returns:
        Repository config dict with 'owner' and 'name' keys. Defaults to langchainplus if not found.
    """
    if not team_identifier or team_identifier not in LINEAR_TEAM_TO_REPO:
        return {"owner": "langchain-ai", "name": "langchainplus"}

    config = LINEAR_TEAM_TO_REPO[team_identifier]

    if "owner" in config and "name" in config:
        return config

    if "projects" in config and project_name:
        project_config = config["projects"].get(project_name)
        if project_config:
            return project_config

    if "default" in config:
        return config["default"]

    return {"owner": "langchain-ai", "name": "langchainplus"}


async def react_to_linear_comment(comment_id: str, emoji: str = "👀") -> bool:
    """Add an emoji reaction to a Linear comment.

    Args:
        comment_id: The Linear comment ID
        emoji: The emoji to react with (default: eyes 👀)

    Returns:
        True if successful, False otherwise
    """
    if not LINEAR_API_KEY:
        return False

    url = "https://api.linear.app/graphql"

    mutation = """
    mutation ReactionCreate($commentId: String!, $emoji: String!) {
        reactionCreate(input: { commentId: $commentId, emoji: $emoji }) {
            success
        }
    }
    """

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": mutation,
                    "variables": {"commentId": comment_id, "emoji": emoji},
                },
            )
            response.raise_for_status()
            result = response.json()
            return bool(result.get("data", {}).get("reactionCreate", {}).get("success"))
        except Exception:  # noqa: BLE001
            return False


async def fetch_linear_issue_details(issue_id: str) -> dict[str, Any] | None:
    """Fetch full issue details from Linear API including description and comments.

    Args:
        issue_id: The Linear issue ID

    Returns:
        Full issue data dict, or None if fetch failed
    """
    if not LINEAR_API_KEY:
        return None

    url = "https://api.linear.app/graphql"

    query = """
    query GetIssue($issueId: String!) {
        issue(id: $issueId) {
            id
            identifier
            title
            description
            url
            project {
                id
                name
            }
            team {
                id
                name
                key
            }
            comments {
                nodes {
                    id
                    body
                    createdAt
                    user {
                        id
                        name
                        email
                    }
                }
            }
        }
    }
    """

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "variables": {"issueId": issue_id},
                },
            )
            response.raise_for_status()
            result = response.json()

            return result.get("data", {}).get("issue")
        except httpx.HTTPError:
            return None


def generate_thread_id_from_issue(issue_id: str) -> str:
    """Generate a deterministic thread ID from a Linear issue ID.

    Args:
        issue_id: The Linear issue ID

    Returns:
        A UUID-formatted thread ID derived from the issue ID
    """
    hash_bytes = hashlib.sha256(f"linear-issue:{issue_id}".encode()).hexdigest()
    return (
        f"{hash_bytes[:8]}-{hash_bytes[8:12]}-{hash_bytes[12:16]}-"
        f"{hash_bytes[16:20]}-{hash_bytes[20:32]}"
    )


def generate_thread_id_from_slack_thread(channel_id: str, thread_id: str) -> str:
    """Generate a deterministic thread ID from a Slack thread identifier."""
    composite = f"{channel_id}:{thread_id}"
    md5_hex = hashlib.md5(composite.encode("utf-8")).hexdigest()
    return str(uuid.UUID(hex=md5_hex))


def _extract_repo_config_from_thread(thread: dict[str, Any]) -> dict[str, str] | None:
    """Extract repo config from persisted thread data."""
    metadata = thread.get("metadata")
    if not isinstance(metadata, dict):
        return None

    repo = metadata.get("repo")
    if isinstance(repo, dict):
        owner = repo.get("owner")
        name = repo.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            return {"owner": owner, "name": name}

    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return {"owner": owner, "name": name}

    return None


def _is_not_found_error(exc: Exception) -> bool:
    """Best-effort check for LangGraph 404 errors."""
    return getattr(exc, "status_code", None) == 404


async def _upsert_slack_thread_repo_metadata(
    thread_id: str, repo_config: dict[str, str], langgraph_client: LangGraphClient
) -> None:
    """Persist the selected repo config on the thread metadata."""
    try:
        await langgraph_client.threads.update(thread_id=thread_id, metadata={"repo": repo_config})
    except Exception as exc:  # noqa: BLE001
        if _is_not_found_error(exc):
            try:
                await langgraph_client.threads.create(
                    thread_id=thread_id,
                    if_exists="do_nothing",
                    metadata={"repo": repo_config},
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to create Slack thread %s while persisting repo metadata",
                    thread_id,
                )
            return
        logger.exception(
            "Failed to persist Slack thread repo metadata for thread %s",
            thread_id,
        )


async def check_if_using_repo_msg_sent(
    channel_id: str, thread_ts: str, using_repo_str: str
) -> bool:
    thread_messages = await fetch_slack_thread_messages(channel_id, thread_ts)
    for message in thread_messages:
        if using_repo_str in message.get("text", ""):
            return True
    return False


async def get_slack_repo_config(message: str, channel_id: str, thread_ts: str) -> dict[str, str]:
    """Resolve repository configuration for Slack-triggered runs."""
    default_owner = SLACK_REPO_OWNER.strip() or "langchain-ai"
    default_name = SLACK_REPO_NAME.strip() or "langchainplus"
    thread_id = generate_thread_id_from_slack_thread(channel_id, thread_ts)
    langgraph_client = get_client(url=LANGGRAPH_URL)

    owner: str | None = None
    name: str | None = None

    if "repo:" in message:
        match = re.search(r"repo:([^ ]+)", message)
        if match:
            repo = match.group(1).strip()
            if "/" in repo:
                owner, name = repo.split("/", 1)

    if not owner or not name:
        try:
            thread = await langgraph_client.threads.get(thread_id)
            thread_repo_config = _extract_repo_config_from_thread(thread)
            if thread_repo_config:
                owner = thread_repo_config["owner"]
                name = thread_repo_config["name"]
        except Exception as exc:  # noqa: BLE001
            if not _is_not_found_error(exc):
                logger.exception(
                    "Failed to fetch Slack thread %s for repo resolution",
                    thread_id,
                )

    if not owner or not name:
        owner = default_owner
        name = default_name

    using_repo_str = f"Using repository: `{owner}/{name}`"
    if not await check_if_using_repo_msg_sent(channel_id, thread_ts, using_repo_str):
        await post_slack_thread_reply(channel_id, thread_ts, using_repo_str)

    return {"owner": owner, "name": name}


async def is_thread_active(thread_id: str) -> bool:
    """Check if a thread is currently active (has a running run).

    Args:
        thread_id: The LangGraph thread ID

    Returns:
        True if the thread status is "busy", False otherwise
    """
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        logger.debug("Fetching thread status for %s from %s", thread_id, LANGGRAPH_URL)
        thread = await langgraph_client.threads.get(thread_id)
        status = thread.get("status", "idle")
        logger.info(
            "Thread %s status check: status=%s, is_busy=%s",
            thread_id,
            status,
            status == "busy",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to get thread status for %s: %s (type: %s) - assuming not active",
            thread_id,
            e,
            type(e).__name__,
        )
        status = "idle"
    return status == "busy"


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a message for a thread that is currently active.

    Stores the message in the langgraph store, namespaced to the thread.
    Supports multiple queued messages by storing them as a list (FIFO order).
    The before_model middleware will pick them up and inject them into state.

    Args:
        thread_id: The LangGraph thread ID
        message_content: The message content to queue (text or content blocks)

    Returns:
        True if successfully queued, False otherwise
    """
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"

        new_message = {"content": message_content}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await langgraph_client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        value = {"messages": existing_messages}

        logger.info(
            "Attempting to queue message for thread %s (total queued: %d)",
            thread_id,
            len(existing_messages),
        )
        await langgraph_client.store.put_item(namespace, key, value)
        logger.info("Successfully queued message for thread %s", thread_id)
        return True  # noqa: TRY300
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False


async def process_linear_issue(  # noqa: PLR0912, PLR0915
    issue_data: dict[str, Any], repo_config: dict[str, str]
) -> None:
    """Process a Linear issue by creating a new LangGraph thread and run.

    Args:
        issue_data: The Linear issue data from webhook (basic info only).
        repo_config: The repo configuration with owner and name.
    """
    issue_id = issue_data.get("id", "")
    logger.info(
        "Processing Linear issue %s for repo %s/%s",
        issue_id,
        repo_config.get("owner"),
        repo_config.get("name"),
    )

    triggering_comment_id = issue_data.get("triggering_comment_id", "")
    if triggering_comment_id:
        await react_to_linear_comment(triggering_comment_id, "👀")

    thread_id = generate_thread_id_from_issue(issue_id)

    full_issue = await fetch_linear_issue_details(issue_id)
    if not full_issue:
        full_issue = issue_data

    user_email = None
    user_name = None
    comment_author = issue_data.get("comment_author", {})
    if comment_author:
        user_email = comment_author.get("email")
        user_name = comment_author.get("name")
    if not user_email:
        creator = full_issue.get("creator", {})
        if creator:
            user_email = creator.get("email")
            user_name = user_name or creator.get("name")
    if not user_email:
        assignee = full_issue.get("assignee", {})
        if assignee:
            user_email = assignee.get("email")
            user_name = user_name or assignee.get("name")

    logger.info("User email for issue %s: %s", issue_id, user_email)

    title = full_issue.get("title", "No title")
    description = full_issue.get("description") or "No description"
    image_urls: list[str] = []
    description_image_urls = extract_image_urls(description)
    if description_image_urls:
        image_urls.extend(description_image_urls)
        logger.debug(
            "Found %d image URL(s) in issue description",
            len(description_image_urls),
        )

    comments = full_issue.get("comments", {}).get("nodes", [])
    comments_text = ""
    triggering_comment = issue_data.get("triggering_comment", "")
    triggering_comment_id = issue_data.get("triggering_comment_id", "")

    bot_message_prefixes = (
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    )

    comment_ids: set[str] = set()
    comment_id_to_index: dict[str, int] = {}
    if comments:
        for i, comment in enumerate(comments):
            comment_id = comment.get("id", "")
            if comment_id:
                comment_ids.add(comment_id)
                comment_id_to_index[comment_id] = i

        relevant_comments = []
        trigger_index = None
        if triggering_comment_id:
            trigger_index = comment_id_to_index.get(triggering_comment_id)
        if trigger_index is not None:
            relevant_comments = comments[trigger_index:]
            logger.debug(
                "Using triggering comment index %d to build relevant comments",
                trigger_index,
            )
        else:
            relevant_comments = get_recent_comments(comments, bot_message_prefixes)

        if relevant_comments:
            comments_text = "\n\n## Comments:\n"
            for comment in relevant_comments:
                user = comment.get("user") or {}
                author = user.get("name", "User")
                body = comment.get("body", "")
                body_image_urls = extract_image_urls(body)
                if body_image_urls:
                    image_urls.extend(body_image_urls)
                    logger.debug(
                        "Found %d image URL(s) in comment by %s",
                        len(body_image_urls),
                        author,
                    )
                if any(body.startswith(prefix) for prefix in bot_message_prefixes):
                    continue
                comments_text += f"\n**{author}:** {body}\n"

    if triggering_comment and triggering_comment_id not in comment_ids:
        if not comments_text:
            comments_text = "\n\n## Comments:\n"
        trigger_author = comment_author.get("name", "Unknown")
        trigger_body = triggering_comment
        trigger_image_urls = extract_image_urls(trigger_body)
        if trigger_image_urls:
            image_urls.extend(trigger_image_urls)
            logger.debug(
                "Found %d image URL(s) in triggering comment by %s",
                len(trigger_image_urls),
                trigger_author,
            )
        comments_text += f"\n**{trigger_author}:** {trigger_body}\n"
        logger.debug(
            "Appended triggering comment %s not present in issue comments list",
            triggering_comment_id or "<missing-id>",
        )

    identifier = full_issue.get("identifier", "") or issue_data.get("identifier", "")

    triggered_by_line = f"## Triggered by: {user_name}\n\n" if user_name else ""
    tag_instruction = (
        f"When calling linear_comment, tag @{user_name} if you are asking them a question, need their input, or are notifying them of something important (e.g. a completed PR). For simple answers, tagging is not required."
        if user_name
        else ""
    )
    prompt = (
        f"Please work on the following issue:\n\n"
        f"## Title: {title}\n\n"
        f"{triggered_by_line}"
        f"## Linear Ticket: {identifier} - Ticket ID: {issue_id}\n\n"
        f"## Description:\n{description}\n"
        f"{comments_text}\n\n"
        f"Please analyze this issue and implement the necessary changes. "
        f"When you're done, commit and push your changes. {tag_instruction}"
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]
    if image_urls:
        image_urls = dedupe_urls(image_urls)
        logger.info("Preparing %d image(s) for multimodal content", len(image_urls))
        logger.debug("Image URLs: %s", image_urls)

        async with httpx.AsyncClient() as client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, client)
                if image_block:
                    content_blocks.append(image_block)
        logger.info("Built %d content block(s) for prompt", len(content_blocks))

    linear_project_id = ""
    linear_issue_number = ""
    if identifier and "-" in identifier:
        parts = identifier.split("-", 1)
        linear_project_id = parts[0]
        linear_issue_number = parts[1]

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "linear_issue": {
            "id": issue_id,
            "title": title,
            "url": full_issue.get("url", "") or issue_data.get("url", ""),
            "identifier": identifier,
            "linear_project_id": linear_project_id,
            "linear_issue_number": linear_issue_number,
            "triggering_user_name": user_name or "",
        },
        "user_email": user_email,
        "source": "linear",
    }

    logger.info("Checking if thread %s is active before creating run", thread_id)
    thread_active = await is_thread_active(thread_id)
    logger.info("Thread %s active status: %s", thread_id, thread_active)

    if thread_active:
        logger.info(
            "Thread %s is active (busy), will queue message instead of creating run",
            thread_id,
        )

        queued_payload = {"text": prompt, "image_urls": image_urls}
        queued = await queue_message_for_thread(
            thread_id=thread_id,
            message_content=queued_payload,
        )

        if queued:
            logger.info("Message queued for thread %s, will be processed by middleware", thread_id)
        else:
            logger.error("Failed to queue message for thread %s", thread_id)
    else:
        logger.info("Creating LangGraph run for thread %s", thread_id)
        langgraph_client = get_client(url=LANGGRAPH_URL)
        await langgraph_client.runs.create(
            thread_id,
            "agent",
            input={"messages": [{"role": "user", "content": content_blocks}]},
            config={"configurable": configurable},
            if_not_exists="create",
        )
        logger.info("LangGraph run created successfully for thread %s", thread_id)


async def process_slack_mention(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
    """Process a Slack app mention by creating or interrupting a thread run."""
    channel_id = event_data.get("channel_id", "")
    thread_ts = event_data.get("thread_ts", "")
    event_ts = event_data.get("event_ts", "")
    user_id = event_data.get("user_id", "")
    text = event_data.get("text", "")
    bot_user_id = event_data.get("bot_user_id", "")

    if not channel_id or not thread_ts or not event_ts:
        logger.warning(
            "Missing Slack event fields (channel_id=%s, thread_ts=%s, event_ts=%s)",
            channel_id,
            thread_ts,
            event_ts,
        )
        return

    reacted = await add_slack_reaction(channel_id, event_ts, "eyes")
    if not reacted:
        logger.debug(
            "Unable to add eyes reaction for Slack message ts=%s in channel=%s",
            event_ts,
            channel_id,
        )

    thread_id = generate_thread_id_from_slack_thread(channel_id, thread_ts)

    user_email = None
    user_name = ""
    if user_id:
        slack_user = await get_slack_user_info(user_id)
        if slack_user:
            profile = slack_user.get("profile", {})
            if isinstance(profile, dict):
                user_email = profile.get("email")
                user_name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or slack_user.get("real_name")
                    or slack_user.get("name")
                    or ""
                )

    thread_messages = await fetch_slack_thread_messages(channel_id, thread_ts)
    if not any(str(message.get("ts")) == str(event_ts) for message in thread_messages):
        thread_messages.append({"ts": event_ts, "text": text, "user": user_id})

    context_messages, context_mode = select_slack_context_messages(
        thread_messages, event_ts, bot_user_id, SLACK_BOT_USERNAME
    )
    context_user_ids = [
        value
        for value in (message.get("user") for message in context_messages)
        if isinstance(value, str) and value
    ]
    user_names_by_id = await get_slack_user_names(context_user_ids)
    if user_id and user_name and user_id not in user_names_by_id:
        user_names_by_id[user_id] = user_name
    context_text = format_slack_messages_for_prompt(
        context_messages,
        user_names_by_id,
        bot_user_id=bot_user_id,
        bot_username=SLACK_BOT_USERNAME,
    )
    context_source = (
        "the previous message where I was tagged"
        if context_mode == "last_mention"
        else "the beginning of the thread"
    )
    clean_text = (
        strip_bot_mention(text, bot_user_id, bot_username=SLACK_BOT_USERNAME)
        or "(no text in mention)"
    )
    trigger_user = user_name or (f"<@{user_id}>" if user_id else "Unknown user")

    prompt = (
        "You were mentioned in Slack.\n\n"
        f"## Repository\n{repo_config.get('owner')}/{repo_config.get('name')}\n\n"
        f"## Triggered by\n{trigger_user}\n\n"
        f"## Slack Thread\n- Channel: {channel_id}\n- Thread TS: {thread_ts}\n"
        f"- Context starts at: {context_source}\n\n"
        f"## Conversation Context\n{context_text}\n\n"
        f"## Latest Mention Request\n{clean_text}\n\n"
        "Use `slack_thread_reply` to communicate in this Slack thread for clarifications, "
        "status updates, and final summaries."
    )
    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]

    configurable: dict[str, Any] = {
        "repo": repo_config,
        "slack_thread": {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "triggering_user_id": user_id,
            "triggering_user_name": user_name,
            "triggering_user_email": user_email,
            "triggering_event_ts": event_ts,
        },
        "user_email": user_email,
        "source": "slack",
    }

    langgraph_client = get_client(url=LANGGRAPH_URL)
    await _upsert_slack_thread_repo_metadata(thread_id, repo_config, langgraph_client)
    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": content_blocks}]},
        config={"configurable": configurable},
        if_not_exists="create",
        multitask_strategy="interrupt",
    )


def verify_linear_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the Linear webhook signature.

    Args:
        body: Raw request body bytes
        signature: The Linear-Signature header value
        secret: The webhook signing secret

    Returns:
        True if signature is valid, False otherwise
    """
    if not secret:
        return True

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/linear")
async def linear_webhook(  # noqa: PLR0911, PLR0912, PLR0915
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Handle Linear webhooks.

    Triggers a new LangGraph run when an issue gets the 'open-swe' label added.
    """
    logger.info("Received Linear webhook")
    body = await request.body()

    signature = request.headers.get("Linear-Signature", "")
    if LINEAR_WEBHOOK_SECRET and not verify_linear_signature(
        body, signature, LINEAR_WEBHOOK_SECRET
    ):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") != "Comment":
        logger.debug("Ignoring webhook: not a Comment event")
        return {"status": "ignored", "reason": "Not a Comment event"}

    action = payload.get("action")
    if action != "create":
        logger.debug("Ignoring webhook: action is %s, not create", action)
        return {
            "status": "ignored",
            "reason": f"Comment action is '{action}', only processing 'create'",
        }

    data = payload.get("data", {})

    if data.get("botActor"):
        logger.debug("Ignoring webhook: comment is from a bot")
        return {"status": "ignored", "reason": "Comment is from a bot"}

    comment_body = data.get("body", "")
    bot_message_prefixes = [
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    ]
    for prefix in bot_message_prefixes:
        if comment_body.startswith(prefix):
            logger.debug("Ignoring webhook: comment is our own bot message")
            return {"status": "ignored", "reason": "Comment is our own bot message"}
    if "@openswe" not in comment_body.lower():
        logger.debug("Ignoring webhook: comment doesn't mention @openswe")
        return {"status": "ignored", "reason": "Comment doesn't mention @openswe"}

    issue = data.get("issue", {})
    if not issue:
        logger.debug("Ignoring webhook: no issue data in comment")
        return {"status": "ignored", "reason": "No issue data in comment"}

    # Fetch full issue details to get project info (webhook doesn't include it)
    issue_id = issue.get("id", "")
    full_issue = await fetch_linear_issue_details(issue_id)
    if not full_issue:
        logger.warning("Failed to fetch full issue details, using webhook data")
        full_issue = issue

    team = full_issue.get("team", {})
    team_name = team.get("name", "") if team else ""
    project = full_issue.get("project")
    project_name = project.get("name", "") if project else ""

    team_identifier = team_name.strip() if team_name else ""
    project_key = project_name.strip() if project_name else ""

    repo_config = get_repo_config_from_team_mapping(team_identifier, project_key)

    logger.debug(
        "Team/project lookup result",
        extra={
            "team_name": team_identifier,
            "project_name": project_key,
            "repo_config": repo_config,
        },
    )

    repo_owner = repo_config["owner"]
    repo_name = repo_config["name"]

    issue["triggering_comment"] = comment_body
    issue["triggering_comment_id"] = data.get("id", "")
    comment_user = data.get("user", {})
    if comment_user:
        issue["comment_author"] = comment_user

    logger.info(
        "Accepted webhook for issue '%s' (%s), scheduling background task",
        issue.get("title"),
        issue.get("id"),
    )
    background_tasks.add_task(process_linear_issue, issue, repo_config)

    return {
        "status": "accepted",
        "message": f"Processing issue '{issue.get('title')}' for repo {repo_owner}/{repo_name}",
    }


@app.get("/webhooks/linear")
async def linear_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Linear webhook setup."""
    return {"status": "ok", "message": "Linear webhook endpoint is active"}


@app.post("/webhooks/slack")
async def slack_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle Slack Event API webhooks for app mentions."""
    body = await request.body()

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if SLACK_SIGNING_SECRET and not verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=SLACK_SIGNING_SECRET,
    ):
        logger.warning("Invalid Slack signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse Slack webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        return {"challenge": challenge}

    if payload.get("type") != "event_callback":
        return {"status": "ignored", "reason": "Not an event callback"}

    event = payload.get("event", {})
    if event.get("type") != "app_mention":
        message_text = event.get("text", "")
        has_username_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USERNAME
            and f"@{SLACK_BOT_USERNAME}" in message_text
        )
        has_id_mention = bool(
            event.get("type") == "message"
            and SLACK_BOT_USER_ID
            and f"<@{SLACK_BOT_USER_ID}>" in message_text
        )
        if not (has_username_mention or has_id_mention):
            return {"status": "ignored", "reason": "Not an app_mention event"}

    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return {"status": "ignored", "reason": "Event from a bot"}

    channel_id = event.get("channel", "")
    event_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or event_ts
    user_id = event.get("user", "")
    text = event.get("text", "")
    if not channel_id or not event_ts or not thread_ts:
        return {"status": "ignored", "reason": "Missing channel/thread timestamp"}

    bot_user_id = SLACK_BOT_USER_ID
    if not bot_user_id:
        authorizations = payload.get("authorizations", [])
        if isinstance(authorizations, list) and authorizations:
            auth_user_id = authorizations[0].get("user_id")
            if isinstance(auth_user_id, str):
                bot_user_id = auth_user_id
    if not bot_user_id:
        authed_users = payload.get("authed_users", [])
        if isinstance(authed_users, list) and authed_users:
            first_user = authed_users[0]
            if isinstance(first_user, str):
                bot_user_id = first_user

    if bot_user_id and user_id == bot_user_id:
        return {"status": "ignored", "reason": "Event from this bot user"}

    event_data = {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "event_ts": event_ts,
        "user_id": user_id,
        "text": text,
        "bot_user_id": bot_user_id,
    }
    repo_config = await get_slack_repo_config(text, channel_id, thread_ts)

    background_tasks.add_task(process_slack_mention, event_data, repo_config)

    return {"status": "accepted", "message": "Slack mention queued"}


@app.get("/webhooks/slack")
async def slack_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Slack webhook setup."""
    return {"status": "ok", "message": "Slack webhook endpoint is active"}


async def _get_github_token_for_pr(owner: str, repo: str, pr_number: int) -> str | None:
    """Resolve a GitHub token for reacting to comments on a PR.

    Finds the thread ID from the PR branch name, then reads the encrypted
    token from thread metadata.
    """
    from .encryption import decrypt_token

    langgraph_client = get_client(url=LANGGRAPH_URL)

    try:
        pr_data = None
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code == 200:  # noqa: PLR2004
                pr_data = response.json()

        if not pr_data:
            return None

        head_branch = pr_data.get("head", {}).get("ref", "")
        thread_id = extract_thread_id_from_branch(head_branch)
        if not thread_id:
            return None

        thread = await langgraph_client.threads.get(thread_id)
        metadata = thread.get("metadata", {})
        encrypted = metadata.get("github_token_encrypted")
        if encrypted:
            return decrypt_token(encrypted)
    except Exception:  # noqa: BLE001
        logger.debug("Could not resolve GitHub token for PR #%d", pr_number)
    return None


async def process_github_pr_comment(  # noqa: PLR0912, PLR0915
    event_data: dict[str, Any],
) -> None:
    """Process a GitHub PR comment by creating or queuing a LangGraph run."""
    owner = event_data["owner"]
    repo = event_data["repo"]
    pr_number = event_data["pr_number"]
    head_branch = event_data["head_branch"]
    triggering_comment_id = event_data.get("triggering_comment_id")
    triggering_user = event_data.get("triggering_user", "")
    triggering_user_email = event_data.get("triggering_user_email")
    comment_type = event_data.get("comment_type", "issue_comment")
    is_review_comment = comment_type == "pr_review_comment"

    github_token = event_data.get("github_token")
    if not github_token:
        github_token = await _get_github_token_for_pr(owner, repo, pr_number)

    if github_token and triggering_comment_id:
        await react_to_github_comment(
            owner,
            repo,
            triggering_comment_id,
            github_token,
            "eyes",
            is_pr_review_comment=is_review_comment,
        )

    thread_id = extract_thread_id_from_branch(head_branch)
    if not thread_id:
        logger.warning("Could not extract thread ID from branch %s", head_branch)
        return

    all_comments: list[dict[str, Any]] = []
    review_comments: list[dict[str, Any]] = []

    if github_token:
        issue_comments = await fetch_issue_comments(owner, repo, pr_number, github_token)
        for c in issue_comments:
            c["_comment_type"] = "issue_comment"
        all_comments.extend(issue_comments)

        review_comments = await fetch_pr_review_comments(owner, repo, pr_number, github_token)
        for c in review_comments:
            c["_comment_type"] = "pr_review_comment"
        all_comments.extend(review_comments)

        reviews = await fetch_pr_reviews(owner, repo, pr_number, github_token)
        for r in reviews:
            if r.get("body"):
                r["_comment_type"] = "pr_review"
                r["created_at"] = r.get("submitted_at", r.get("created_at", ""))
                all_comments.append(r)

    relevant_comments = collect_comments_since_last_tag(all_comments, triggering_comment_id)

    comments_text = ""
    if relevant_comments:
        comments_parts: list[str] = []
        for comment in relevant_comments:
            ctype = comment.get("_comment_type", "issue_comment")
            author = comment.get("user", {}).get("login", "Unknown")
            body = comment.get("body", "")

            if ctype == "pr_review_comment":
                comments_parts.append(format_review_comment_for_prompt(comment))
            elif ctype == "pr_review":
                state = comment.get("state", "")
                state_label = f" [{state}]" if state else ""
                comments_parts.append(f"**@{author}** (review{state_label}):\n{body}")
            else:
                comment_id = comment.get("id", "")
                comments_parts.append(f"**@{author}** (comment_id: {comment_id}):\n{body}")

        comments_text = "\n\n## PR Comments:\n\n" + "\n\n---\n\n".join(comments_parts)

    pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    prompt = (
        "You've been tagged in GitHub PR comments. Please resolve them.\n\n"
        f"## Repository\n{owner}/{repo}\n\n"
        f"## Pull Request\n#{pr_number}: {pr_url}\n\n"
        f"## Triggered by\n@{triggering_user}\n\n"
        f"{comments_text}\n\n"
        "Use `github_comment_on_pr` to communicate in this PR for clarifications, "
        "status updates, and final summaries. Use `commit_and_open_pr` to push any code changes."
    )

    content_blocks: list[dict[str, Any]] = [create_text_block(prompt)]

    image_urls = extract_image_urls(comments_text)
    if image_urls:
        image_urls = dedupe_urls(image_urls)
        async with httpx.AsyncClient() as client:
            for image_url in image_urls:
                image_block = await fetch_image_block(image_url, client)
                if image_block:
                    content_blocks.append(image_block)

    configurable: dict[str, Any] = {
        "repo": {"owner": owner, "name": repo},
        "github_pr": {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "head_branch": head_branch,
            "triggering_user": triggering_user,
            "triggering_user_email": triggering_user_email,
            "github_token": github_token,
        },
        "user_email": triggering_user_email,
        "source": "github",
    }

    logger.info("Checking if thread %s is active before creating run", thread_id)
    thread_active = await is_thread_active(thread_id)

    if thread_active:
        logger.info("Thread %s is active, queuing message", thread_id)
        queued = await queue_message_for_thread(thread_id=thread_id, message_content=prompt)
        if queued:
            logger.info("Message queued for thread %s", thread_id)
        else:
            logger.error("Failed to queue message for thread %s", thread_id)
    else:
        logger.info("Creating LangGraph run for thread %s", thread_id)
        langgraph_client = get_client(url=LANGGRAPH_URL)
        await langgraph_client.runs.create(
            thread_id,
            "agent",
            input={"messages": [{"role": "user", "content": content_blocks}]},
            config={"configurable": configurable},
            if_not_exists="create",
        )
        logger.info("LangGraph run created for thread %s", thread_id)


def _is_pr_comment(payload: dict[str, Any]) -> bool:
    """Check if an issue_comment event is actually on a PR (not a plain issue)."""
    issue = payload.get("issue", {})
    return "pull_request" in issue


@app.post("/webhooks/github")
async def github_webhook(  # noqa: PLR0911, PLR0912, PLR0915
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Handle GitHub webhooks for PR comments, PR review comments, and PR reviews."""
    logger.info("Received GitHub webhook")
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if GITHUB_WEBHOOK_SECRET and not verify_github_signature(
        body, signature, GITHUB_WEBHOOK_SECRET
    ):
        logger.warning("Invalid GitHub webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse GitHub webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    event_type = request.headers.get("X-GitHub-Event", "")
    action = payload.get("action", "")

    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login", "")
    repo = repo_data.get("name", "")

    if event_type == "issue_comment" and action == "created":
        if not _is_pr_comment(payload):
            return {"status": "ignored", "reason": "Comment is on an issue, not a PR"}

        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")

        if not re.search(r"@open-swe\b", comment_body, re.IGNORECASE):
            return {"status": "ignored", "reason": "Comment doesn't mention @open-swe"}

        if comment.get("performed_via_github_app"):
            return {"status": "ignored", "reason": "Comment is from a GitHub App"}

        issue = payload.get("issue", {})
        pr_number = issue.get("number")
        pr_url = issue.get("pull_request", {}).get("url", "")

        pr_data = None
        if pr_url:
            async with httpx.AsyncClient() as client:
                try:
                    resp = await client.get(
                        pr_url,
                        headers={
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                    )
                    if resp.status_code == 200:  # noqa: PLR2004
                        pr_data = resp.json()
                except httpx.HTTPError:
                    logger.exception("Failed to fetch PR details from %s", pr_url)

        head_branch = ""
        if pr_data:
            head_branch = pr_data.get("head", {}).get("ref", "")

        if not head_branch or not extract_thread_id_from_branch(head_branch):
            return {
                "status": "ignored",
                "reason": "PR branch is not an open-swe branch",
            }

        user = comment.get("user", {})
        event_data = {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "head_branch": head_branch,
            "triggering_comment_id": comment.get("id"),
            "triggering_user": user.get("login", ""),
            "triggering_user_email": user.get("email"),
            "comment_type": "issue_comment",
        }

        background_tasks.add_task(process_github_pr_comment, event_data)
        return {"status": "accepted", "message": f"Processing PR comment on #{pr_number}"}

    if event_type == "pull_request_review_comment" and action == "created":
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")

        if not re.search(r"@open-swe\b", comment_body, re.IGNORECASE):
            return {"status": "ignored", "reason": "Review comment doesn't mention @open-swe"}

        if comment.get("performed_via_github_app"):
            return {"status": "ignored", "reason": "Comment is from a GitHub App"}

        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        head_branch = pr.get("head", {}).get("ref", "")

        if not head_branch or not extract_thread_id_from_branch(head_branch):
            return {
                "status": "ignored",
                "reason": "PR branch is not an open-swe branch",
            }

        user = comment.get("user", {})
        event_data = {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "head_branch": head_branch,
            "triggering_comment_id": comment.get("id"),
            "triggering_user": user.get("login", ""),
            "triggering_user_email": user.get("email"),
            "comment_type": "pr_review_comment",
        }

        background_tasks.add_task(process_github_pr_comment, event_data)
        return {
            "status": "accepted",
            "message": f"Processing PR review comment on #{pr_number}",
        }

    if event_type == "pull_request_review" and action == "submitted":
        review = payload.get("review", {})
        review_body = review.get("body", "") or ""

        if not re.search(r"@open-swe\b", review_body, re.IGNORECASE):
            return {"status": "ignored", "reason": "Review doesn't mention @open-swe"}

        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        head_branch = pr.get("head", {}).get("ref", "")

        if not head_branch or not extract_thread_id_from_branch(head_branch):
            return {
                "status": "ignored",
                "reason": "PR branch is not an open-swe branch",
            }

        user = review.get("user", {})
        event_data = {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "head_branch": head_branch,
            "triggering_comment_id": review.get("id"),
            "triggering_user": user.get("login", ""),
            "triggering_user_email": user.get("email"),
            "comment_type": "pr_review",
        }

        background_tasks.add_task(process_github_pr_comment, event_data)
        return {
            "status": "accepted",
            "message": f"Processing PR review on #{pr_number}",
        }

    return {"status": "ignored", "reason": f"Unhandled event: {event_type}/{action}"}


@app.get("/webhooks/github")
async def github_webhook_verify() -> dict[str, str]:
    """Verify endpoint for GitHub webhook setup."""
    return {"status": "ok", "message": "GitHub webhook endpoint is active"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
