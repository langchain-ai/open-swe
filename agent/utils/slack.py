"""Slack API utilities."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from langgraph_sdk.client import LangGraphClient

from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
GITHUB_PR_URL_RE = re.compile(r"https?://(?:www\.)?github\.com/[^\s<>|]+/[^\s<>|]+/pull/\d+")
URL_RE = re.compile(r"https?://[^\s<>|]+")


DEFAULT_ASSISTANT_STATUS = "is thinking…"

# Curated rotating loading strings shown by Slack while the indicator is active.
# Capped at 10 by Slack's API.
DEFAULT_LOADING_MESSAGES: tuple[str, ...] = (
    "Pondering…",
    "Cogitating…",
    "Ruminating…",
    "Noodling…",
    "Percolating…",
    "Marinating…",
    "Simmering…",
    "Conjuring…",
    "Tinkering…",
    "Schlepping…",
)


@dataclass(frozen=True)
class GitHubPrRef:
    owner: str
    repo: str
    number: int
    url: str


def _slack_headers() -> dict[str, str]:
    if not SLACK_BOT_TOKEN:
        return {}
    return {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _parse_ts(ts: str | None) -> float:
    try:
        return float(ts or "0")
    except (TypeError, ValueError):
        return 0.0


def _extract_slack_user_name(user: dict[str, Any]) -> str:
    profile = user.get("profile", {})
    if isinstance(profile, dict):
        display_name = profile.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
        real_name = profile.get("real_name")
        if isinstance(real_name, str) and real_name.strip():
            return real_name.strip()

    real_name = user.get("real_name")
    if isinstance(real_name, str) and real_name.strip():
        return real_name.strip()

    name = user.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()

    return "unknown"


def replace_bot_mention_with_username(text: str, bot_user_id: str, bot_username: str) -> str:
    """Replace Slack bot ID mention token with @username."""
    if not text:
        return ""
    if bot_user_id and bot_username:
        return text.replace(f"<@{bot_user_id}>", f"@{bot_username}")
    return text


def convert_mentions_to_slack_format(text: str) -> str:
    """Convert @Name(USER_ID) patterns to Slack's <@USER_ID> mention format."""
    return re.sub(r"@[^()]+\(([A-Z0-9]+)\)", r"<@\1>", text)


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify Slack request signature."""
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET is not configured — rejecting webhook request")
        return False
    if not timestamp or not signature:
        return False
    try:
        request_timestamp = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - request_timestamp) > max_age_seconds:
        return False

    base_string = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    expected = (
        "v0="
        + hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def strip_bot_mention(text: str, bot_user_id: str, bot_username: str = "") -> str:
    """Remove bot mention token from Slack text."""
    if not text:
        return ""
    stripped = text
    if bot_user_id:
        stripped = stripped.replace(f"<@{bot_user_id}>", "")
    if bot_username:
        stripped = stripped.replace(f"@{bot_username}", "")
    return stripped.strip()


def parse_github_pr_url(url: str) -> GitHubPrRef | None:
    cleaned_url = url.strip().strip("<>")
    if "|" in cleaned_url:
        cleaned_url = cleaned_url.split("|", 1)[0]

    parsed = urlparse(cleaned_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[2] != "pull":
        return None

    try:
        number = int(path_parts[3])
    except ValueError:
        return None

    owner = path_parts[0]
    repo = path_parts[1]
    return GitHubPrRef(
        owner=owner,
        repo=repo,
        number=number,
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
    )


def parse_slack_review_command(text: str) -> GitHubPrRef | None:
    stripped = text.strip()
    command_match = re.fullmatch(r"(?is)review\s+(.+)", stripped)
    if not command_match:
        return None

    rest = command_match.group(1).strip()
    url_match = GITHUB_PR_URL_RE.search(rest)
    if not url_match:
        return None

    trailing_text = rest[url_match.end() :].strip()
    if trailing_text and trailing_text != ">" and not trailing_text.startswith("|"):
        return None

    return parse_github_pr_url(url_match.group(0))


def looks_like_slack_pr_review_command(text: str) -> bool:
    stripped = text.strip()
    if not re.match(r"(?is)^review\b", stripped):
        return False
    for match in URL_RE.finditer(stripped):
        parsed = urlparse(match.group(0).strip("<>"))
        host = (parsed.hostname or "").lower()
        if parsed.scheme in {"http", "https"} and host in {"github.com", "www.github.com"}:
            return True
    return False


def select_slack_context_messages(
    messages: list[dict[str, Any]],
    current_message_ts: str,
    bot_user_id: str,
    bot_username: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Select context from thread start or previous bot mention."""
    if not messages:
        return [], "thread_start"

    current_ts = _parse_ts(current_message_ts)
    ordered = sorted(messages, key=lambda item: _parse_ts(item.get("ts")))
    up_to_current = [item for item in ordered if _parse_ts(item.get("ts")) <= current_ts]
    if not up_to_current:
        up_to_current = ordered

    mention_tokens = []
    if bot_user_id:
        mention_tokens.append(f"<@{bot_user_id}>")
    if bot_username:
        mention_tokens.append(f"@{bot_username}")
    if not mention_tokens:
        return up_to_current, "thread_start"

    last_mention_index = -1
    for index, message in enumerate(up_to_current[:-1]):
        text = message.get("text", "")
        if isinstance(text, str) and any(token in text for token in mention_tokens):
            last_mention_index = index

    if last_mention_index >= 0:
        return up_to_current[last_mention_index:], "last_mention"
    return up_to_current, "thread_start"


def format_slack_messages_for_prompt(
    messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str] | None = None,
    bot_user_id: str = "",
    bot_username: str = "",
) -> str:
    """Format Slack messages into readable prompt text."""
    if not messages:
        return "(no thread messages available)"

    lines: list[str] = []
    for message in messages:
        text = (
            replace_bot_mention_with_username(
                str(message.get("text", "")),
                bot_user_id=bot_user_id,
                bot_username=bot_username,
            ).strip()
            or "[non-text message]"
        )
        user_id = message.get("user")
        if isinstance(user_id, str) and user_id:
            author_name = (user_names_by_id or {}).get(user_id) or user_id
            author = f"@{author_name}({user_id})"
        else:
            bot_profile = message.get("bot_profile", {})
            if isinstance(bot_profile, dict):
                bot_name = bot_profile.get("name") or message.get("username") or "Bot"
            else:
                bot_name = message.get("username") or "Bot"
            author = f"@{bot_name}(bot)"
        lines.append(f"{author}: {text}")
    return "\n".join(lines)


async def set_slack_assistant_status(
    channel_id: str,
    thread_ts: str,
    status: str = DEFAULT_ASSISTANT_STATUS,
    loading_messages: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Set the assistant typing/status indicator on a Slack thread.

    Wraps Slack's `assistant.threads.setStatus` API. The `chat:write` scope
    on the bot token is sufficient. Status auto-clears when the bot posts to
    the thread, and Slack itself expires it after ~2 minutes — callers that
    want it visible across longer runs must refresh it periodically.

    `loading_messages` is an optional list (max 10) of strings Slack rotates
    through while the indicator is visible.

    No-op (returning False) when the bot token is missing or the
    channel/thread is not provided. Failures are logged but never raised —
    the indicator is a UX nicety, not a correctness requirement.
    """
    if not SLACK_BOT_TOKEN or not channel_id or not thread_ts:
        return False

    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "status": status,
    }
    if loading_messages:
        payload["loading_messages"] = list(loading_messages)[:10]

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/assistant.threads.setStatus",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack assistant.threads.setStatus failed: %s", data.get("error"))
                return False
            return True
        except httpx.HTTPError:
            logger.exception("Slack assistant.threads.setStatus request failed")
            return False


async def post_slack_thread_reply_with_ts(
    channel_id: str,
    thread_ts: str,
    text: str,
    *,
    unfurl_links: bool = True,
    unfurl_media: bool = True,
) -> tuple[str | None, str | None]:
    """Post a reply in a Slack thread and return its Slack timestamp and error."""
    if not SLACK_BOT_TOKEN:
        return None, "missing_slack_bot_token"

    payload: dict[str, Any] = {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "text": text,
        "unfurl_links": unfurl_links,
        "unfurl_media": unfurl_media,
    }

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postMessage",
                headers=_slack_headers(),
                json=payload,
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning("Slack chat.postMessage rate limited (retry-after=%s)", retry_after)
                if retry_after:
                    return None, f"rate_limited: {retry_after}"
                return None, "rate_limited"
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                error = data.get("error")
                logger.warning("Slack chat.postMessage failed: %s", error)
                if error == "ratelimited":
                    return None, "rate_limited"
                return None, error
            message_ts = data.get("ts")
            if isinstance(message_ts, str) and message_ts:
                return message_ts, None
            return None, None
        except httpx.HTTPError as exc:
            logger.exception("Slack chat.postMessage request failed")
            return None, f"http_error: {type(exc).__name__}"


async def post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
    """Post a reply in a Slack thread."""
    message_ts, _ = await post_slack_thread_reply_with_ts(channel_id, thread_ts, text)
    return message_ts is not None


async def post_slack_ephemeral_message(
    channel_id: str, user_id: str, text: str, thread_ts: str | None = None
) -> bool:
    """Post an ephemeral message visible only to one user."""
    if not SLACK_BOT_TOKEN:
        return False

    payload: dict[str, str] = {
        "channel": channel_id,
        "user": user_id,
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postEphemeral",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack chat.postEphemeral failed: %s", data.get("error"))
                return False
            return True
        except httpx.HTTPError:
            logger.exception("Slack chat.postEphemeral request failed")
            return False


async def add_slack_reaction(channel_id: str, message_ts: str, emoji: str = "eyes") -> bool:
    """Add a reaction to a Slack message."""
    if not SLACK_BOT_TOKEN:
        return False

    payload = {
        "channel": channel_id,
        "timestamp": message_ts,
        "name": emoji,
    }

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/reactions.add",
                headers=_slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                return True
            if data.get("error") == "already_reacted":
                return True
            logger.warning("Slack reactions.add failed: %s", data.get("error"))
            return False
        except httpx.HTTPError:
            logger.exception("Slack reactions.add request failed")
            return False


async def get_slack_user_info(user_id: str) -> dict[str, Any] | None:
    """Get Slack user details by user ID."""
    if not SLACK_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/users.info",
                headers=_slack_headers(),
                params={"user": user_id},
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack users.info failed: %s", data.get("error"))
                return None
            user = data.get("user")
            if isinstance(user, dict):
                return user
        except httpx.HTTPError:
            logger.exception("Slack users.info request failed")
    return None


async def get_slack_user_names(user_ids: list[str]) -> dict[str, str]:
    """Get display names for a set of Slack user IDs."""
    unique_ids = sorted({user_id for user_id in user_ids if isinstance(user_id, str) and user_id})
    if not unique_ids:
        return {}

    user_infos = await asyncio.gather(
        *(get_slack_user_info(user_id) for user_id in unique_ids),
        return_exceptions=True,
    )

    user_names: dict[str, str] = {}
    for user_id, user_info in zip(unique_ids, user_infos, strict=True):
        if isinstance(user_info, dict):
            user_names[user_id] = _extract_slack_user_name(user_info)
        else:
            user_names[user_id] = user_id
    return user_names


async def fetch_slack_thread_messages(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
    """Fetch all messages for a Slack thread."""
    if not SLACK_BOT_TOKEN:
        return []

    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    async with httpx.AsyncClient() as http_client:
        while True:
            params: dict[str, str | int] = {"channel": channel_id, "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor

            try:
                response = await http_client.get(
                    f"{SLACK_API_BASE_URL}/conversations.replies",
                    headers=_slack_headers(),
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError:
                logger.exception("Slack conversations.replies request failed")
                break

            if not payload.get("ok"):
                logger.warning("Slack conversations.replies failed: %s", payload.get("error"))
                break

            batch = payload.get("messages", [])
            if isinstance(batch, list):
                messages.extend(item for item in batch if isinstance(item, dict))

            response_metadata = payload.get("response_metadata", {})
            cursor = (
                response_metadata.get("next_cursor") if isinstance(response_metadata, dict) else ""
            )
            if not cursor:
                break

    messages.sort(key=lambda item: _parse_ts(item.get("ts")))
    return messages


SLACK_MESSAGE_URL_RE = re.compile(
    r"https?://[a-zA-Z0-9\-]+\.slack\.com/archives/([A-Za-z0-9]+)/p(\d{16})(?:\?[^\s>]*)?"
)


def parse_slack_message_url(url: str) -> tuple[str, str] | None:
    """Parse a Slack message URL into (channel_id, message_ts).

    URL format: https://{workspace}.slack.com/archives/{channel_id}/p{ts_without_dot}
    The 16-digit timestamp becomes {first_10}.{last_6} (e.g. p1776281321762829 -> 1776281321.762829).
    """
    match = SLACK_MESSAGE_URL_RE.search(url)
    if not match:
        return None
    channel_id = match.group(1)
    raw_ts = match.group(2)
    message_ts = f"{raw_ts[:10]}.{raw_ts[10:]}"
    return channel_id, message_ts


def extract_slack_message_urls(text: str) -> list[tuple[str, str, str]]:
    """Extract all Slack message URLs from text.

    Returns list of (full_url, channel_id, message_ts) tuples.
    """
    results: list[tuple[str, str, str]] = []
    for match in SLACK_MESSAGE_URL_RE.finditer(text):
        full_url = match.group(0)
        parsed = parse_slack_message_url(full_url)
        if parsed:
            results.append((full_url, parsed[0], parsed[1]))
    return results


async def fetch_slack_message_by_ts(channel_id: str, message_ts: str) -> dict[str, Any] | None:
    """Fetch a single Slack message by channel and timestamp."""
    if not SLACK_BOT_TOKEN:
        return None

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(
                f"{SLACK_API_BASE_URL}/conversations.history",
                headers=_slack_headers(),
                params={
                    "channel": channel_id,
                    "latest": message_ts,
                    "oldest": message_ts,
                    "inclusive": "true",
                    "limit": 1,
                },
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning(
                    "Slack conversations.history failed for channel=%s ts=%s: %s",
                    channel_id,
                    message_ts,
                    data.get("error"),
                )
                return None
            messages = data.get("messages", [])
            if messages and isinstance(messages[0], dict):
                return messages[0]
        except httpx.HTTPError:
            logger.exception(
                "Slack conversations.history request failed for channel=%s ts=%s",
                channel_id,
                message_ts,
            )
    return None


# Slack file mimetypes/filetypes that should be inlined as text snippets.
TEXT_LIKE_MIMETYPE_PREFIXES: tuple[str, ...] = ("text/",)
TEXT_LIKE_MIMETYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/x-toml",
        "application/toml",
        "application/x-sh",
        "application/x-shellscript",
        "application/javascript",
        "application/x-javascript",
        "application/typescript",
        "application/x-typescript",
        "application/x-python",
        "application/x-httpd-php",
        "application/sql",
        "application/x-sql",
        "application/graphql",
    }
)
TEXT_LIKE_FILETYPES: frozenset[str] = frozenset(
    {
        "text",
        "post",
        "snippet",
        "diff",
        "patch",
        "csv",
        "tsv",
        "json",
        "xml",
        "yaml",
        "toml",
        "ini",
        "conf",
        "config",
        "log",
        "markdown",
        "md",
        "rst",
        "tex",
        "html",
        "css",
        "scss",
        "less",
        "javascript",
        "typescript",
        "jsx",
        "tsx",
        "python",
        "ruby",
        "go",
        "rust",
        "java",
        "kotlin",
        "scala",
        "swift",
        "objc",
        "csharp",
        "fsharp",
        "vbnet",
        "perl",
        "php",
        "r",
        "bash",
        "shell",
        "powershell",
        "dockerfile",
        "makefile",
        "cmake",
        "sql",
        "graphql",
        "lua",
        "groovy",
        "dart",
        "elixir",
        "erlang",
        "haskell",
        "ocaml",
        "clojure",
        "lisp",
        "vbscript",
        "batchfile",
        "applescript",
        "cpp",
        "c",
        "h",
        "hpp",
    }
)

# Per-file and aggregate caps for inlined Slack text attachments.
SLACK_TEXT_FILE_MAX_BYTES = 256 * 1024
SLACK_TEXT_FILES_TOTAL_MAX_BYTES = 1024 * 1024


def is_text_like_slack_file(file_info: dict[str, Any]) -> bool:
    """Whether a Slack file dict is a text snippet we should inline in the prompt."""
    if not isinstance(file_info, dict):
        return False
    if not file_info.get("url_private"):
        return False
    mimetype = (file_info.get("mimetype") or "").lower()
    if any(mimetype.startswith(prefix) for prefix in TEXT_LIKE_MIMETYPE_PREFIXES):
        return True
    if mimetype in TEXT_LIKE_MIMETYPES:
        return True
    filetype = (file_info.get("filetype") or "").lower()
    if filetype in TEXT_LIKE_FILETYPES:
        return True
    mode = (file_info.get("mode") or "").lower()
    if mode in {"snippet", "post"}:
        return True
    return False


def _slack_file_display_name(file_info: dict[str, Any]) -> str:
    name = file_info.get("name") or file_info.get("title") or file_info.get("id") or "snippet"
    return str(name)


def _slack_file_code_fence_lang(file_info: dict[str, Any]) -> str:
    """Pick a markdown code-fence language hint for a Slack file."""
    filetype = (file_info.get("filetype") or "").lower()
    mapping = {
        "javascript": "javascript",
        "typescript": "typescript",
        "jsx": "jsx",
        "tsx": "tsx",
        "python": "python",
        "ruby": "ruby",
        "go": "go",
        "rust": "rust",
        "java": "java",
        "kotlin": "kotlin",
        "scala": "scala",
        "swift": "swift",
        "csharp": "csharp",
        "cpp": "cpp",
        "c": "c",
        "shell": "bash",
        "bash": "bash",
        "powershell": "powershell",
        "sql": "sql",
        "html": "html",
        "css": "css",
        "scss": "scss",
        "less": "less",
        "json": "json",
        "xml": "xml",
        "yaml": "yaml",
        "toml": "toml",
        "ini": "ini",
        "markdown": "markdown",
        "md": "markdown",
        "diff": "diff",
        "patch": "diff",
        "dockerfile": "dockerfile",
        "makefile": "makefile",
        "graphql": "graphql",
        "lua": "lua",
        "perl": "perl",
        "php": "php",
        "r": "r",
        "dart": "dart",
        "elixir": "elixir",
        "erlang": "erlang",
        "haskell": "haskell",
        "clojure": "clojure",
    }
    if filetype in mapping:
        return mapping[filetype]
    mimetype = (file_info.get("mimetype") or "").lower()
    if mimetype == "application/json":
        return "json"
    if mimetype in {"application/x-yaml", "application/yaml"}:
        return "yaml"
    if mimetype in {"application/xml", "text/xml"}:
        return "xml"
    if mimetype in {"application/x-toml", "application/toml"}:
        return "toml"
    if mimetype.startswith("text/x-"):
        return mimetype[len("text/x-") :]
    return ""


async def fetch_slack_file_text(
    file_info: dict[str, Any],
    http_client: httpx.AsyncClient,
    *,
    max_bytes: int = SLACK_TEXT_FILE_MAX_BYTES,
) -> str | None:
    """Download a Slack text-like file and return its decoded contents.

    Returns None if the file is missing, the bot token is not configured, the
    download fails, or the contents cannot be decoded as text.
    """
    url = file_info.get("url_private")
    if not isinstance(url, str) or not url:
        return None
    if not SLACK_BOT_TOKEN:
        logger.debug("SLACK_BOT_TOKEN not set; skipping Slack file fetch for %s", url)
        return None
    try:
        response = await http_client.get(
            url,
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.content
        truncated = False
        if len(content) > max_bytes:
            content = content[:max_bytes]
            truncated = True
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        if truncated:
            text += "\n\n…[truncated]"
        return text
    except httpx.HTTPError:
        logger.exception("Failed to fetch Slack file %s", url)
        return None


def format_slack_file_snippet(file_info: dict[str, Any], content: str) -> str:
    """Format a downloaded Slack text file as a markdown snippet for the prompt."""
    name = _slack_file_display_name(file_info)
    lang = _slack_file_code_fence_lang(file_info)
    pretty_type = file_info.get("pretty_type") or file_info.get("filetype") or ""
    header_bits = [f"`{name}`"]
    if pretty_type:
        header_bits.append(f"({pretty_type})")
    header = " ".join(header_bits)
    fence_open = f"```{lang}" if lang else "```"
    return f"**{header}**\n{fence_open}\n{content}\n```"


async def extract_slack_text_files_from_messages(
    messages: list[dict[str, Any]],
    http_client: httpx.AsyncClient,
    *,
    total_max_bytes: int = SLACK_TEXT_FILES_TOTAL_MAX_BYTES,
) -> str:
    """Download text-like Slack file attachments and format them as a prompt section.

    Files are deduplicated by id (or url_private as a fallback). Returns an empty
    string when there are no text attachments or none could be downloaded.
    """
    if not messages:
        return ""

    seen: set[str] = set()
    queue: list[dict[str, Any]] = []
    for message in messages:
        files = message.get("files", [])
        if not isinstance(files, list):
            continue
        for file_info in files:
            if not is_text_like_slack_file(file_info):
                continue
            key = str(file_info.get("id") or file_info.get("url_private") or id(file_info))
            if key in seen:
                continue
            seen.add(key)
            queue.append(file_info)

    if not queue:
        return ""

    snippets: list[str] = []
    used_bytes = 0
    for file_info in queue:
        if used_bytes >= total_max_bytes:
            snippets.append(
                f"_Skipped `{_slack_file_display_name(file_info)}` — total snippet size cap reached._"
            )
            continue
        remaining = total_max_bytes - used_bytes
        per_file_cap = min(SLACK_TEXT_FILE_MAX_BYTES, remaining)
        content = await fetch_slack_file_text(file_info, http_client, max_bytes=per_file_cap)
        if content is None:
            snippets.append(f"_Could not fetch `{_slack_file_display_name(file_info)}`._")
            continue
        used_bytes += len(content.encode("utf-8", errors="replace"))
        snippets.append(format_slack_file_snippet(file_info, content))

    if not snippets:
        return ""
    return "\n\n## Attached Snippets\n" + "\n\n".join(snippets)


async def resolve_slack_message_url(url: str) -> dict[str, Any] | None:
    """Resolve a Slack message URL to its message content.

    Returns a dict with keys: text, user, ts, channel_id, files, thread_ts (if threaded).
    """
    parsed = parse_slack_message_url(url)
    if not parsed:
        return None

    channel_id, message_ts = parsed
    message = await fetch_slack_message_by_ts(channel_id, message_ts)
    if not message:
        return None

    result: dict[str, Any] = {
        "channel_id": channel_id,
        "ts": message.get("ts", message_ts),
        "text": message.get("text", ""),
        "user": message.get("user", ""),
        "files": message.get("files", []),
    }
    if message.get("thread_ts"):
        result["thread_ts"] = message["thread_ts"]
    return result


async def resolve_slack_links_in_context(
    context_messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str],
) -> tuple[str, list[str]]:
    """Resolve cross-posted Slack message links found in context messages.

    Returns (resolved_links_section, image_urls) where resolved_links_section
    is a formatted markdown string for the prompt, and image_urls is a list
    of image URLs from resolved message attachments.
    """
    all_context_text = " ".join(msg.get("text", "") for msg in context_messages)
    slack_links = extract_slack_message_urls(all_context_text)
    if not slack_links:
        return "", []

    resolved_parts: list[str] = []
    image_urls: list[str] = []
    resolved_messages: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for link_url, _cid, _ts in slack_links:
        if link_url in seen_urls:
            continue
        seen_urls.add(link_url)
        try:
            resolved = await resolve_slack_message_url(link_url)
            if resolved:
                author_id = resolved.get("user", "")
                author = user_names_by_id.get(author_id, author_id)
                if author_id and author == author_id:
                    extra_names = await get_slack_user_names([author_id])
                    author = extra_names.get(author_id, author_id)
                resolved_text = resolved.get("text", "(empty message)")
                resolved_parts.append(
                    f"**{link_url}**\n  Author: {author}\n  Message: {resolved_text}"
                )
                resolved_messages.append(resolved)
                for file_info in resolved.get("files", []):
                    if (
                        isinstance(file_info, dict)
                        and file_info.get("mimetype", "").startswith("image/")
                        and file_info.get("url_private")
                    ):
                        image_urls.append(file_info["url_private"])
            else:
                resolved_parts.append(
                    f"**{link_url}**\n  (Could not fetch — bot may not have access)"
                )
        except Exception:
            logger.exception("Failed to resolve Slack link %s", link_url)
            resolved_parts.append(f"**{link_url}**\n  (Error resolving link)")

    resolved_links_section = ""
    if resolved_parts:
        resolved_links_section = "\n\n## Cross-posted Slack Messages\n" + "\n\n".join(
            resolved_parts
        )

    if resolved_messages:
        try:
            async with httpx.AsyncClient() as http_client:
                snippet_section = await extract_slack_text_files_from_messages(
                    resolved_messages, http_client
                )
        except Exception:
            logger.exception("Failed to extract text files from cross-posted Slack messages")
            snippet_section = ""
        if snippet_section:
            resolved_links_section += snippet_section

    return resolved_links_section, image_urls


TRACE_REPLY_TIPS: tuple[str, ...] = (
    "You can message me in this thread while I'm running — I'll pick up your follow-up before my next step.",
    "Kick off another task in parallel — each one runs in its own isolated sandbox, no queuing.",
    "Add `repo:owner/name` to your message to point me at a different repo for this task.",
    "Drop an `AGENTS.md` at your repo root and I'll read it on every run — it's the easiest way to teach me your conventions.",
    "For code-change tasks, I'll open a draft PR when it's necessary or requested and link it back here.",
    "Tag me on a PR comment of an open-swe PR to have me address review feedback on the same branch.",
    "I can spawn subagents for independent subtasks — useful for parallel research or fan-out work.",
    "Click `View trace` above to watch every tool call and model response live in LangSmith.",
    "React to my final reply with :+1: or :-1: to share feedback — it helps me get better.",
    "Ask me to review a GitHub PR in Slack and I'll spin up the reviewer agent to leave inline comments.",
    "Pasting a GitHub URL into your message also works to point me at a repo — no `repo:` prefix needed.",
    "Attach screenshots or images directly in Slack or Linear — I'll read them as part of the task context.",
    "Tag `@openswe` on a Linear issue and I'll pull in the full title, description, and comment thread before starting.",
    "I also pick up `@openswe` mentions in GitHub issue bodies and comments — not just on PRs.",
    "On a GitHub PR, ask me to review it and I'll hand it off to the reviewer agent for inline comments.",
    "Each thread keeps a persistent sandbox — follow-up runs reuse the same workspace, so my context sticks around.",
    "Paste a Slack message link from another thread and I'll fetch its content (and any images) as extra context.",
    "Ask me to search the web — I have a `web_search` tool for finding docs, examples, and GitHub repos mid-task.",
    "I can read, update, and create Linear issues directly — useful for filing follow-up tickets or linking work back to a project.",
)


def _format_trace_reply(trace_url: str | None) -> str:
    """Format the initial trace reply with a randomly selected tip."""
    tip = random.choice(TRACE_REPLY_TIPS)
    head = f"<{trace_url}|View trace>\n" if trace_url else ""
    return f"{head}_Tip: {tip}_"


async def post_slack_trace_reply(channel_id: str, thread_ts: str, thread_id: str) -> str | None:
    """Post a trace URL reply in a Slack thread and return its Slack timestamp."""
    trace_url = get_langsmith_trace_url(thread_id)
    message_ts, _ = await post_slack_thread_reply_with_ts(
        channel_id,
        thread_ts,
        _format_trace_reply(trace_url),
        unfurl_links=False,
        unfurl_media=False,
    )
    return message_ts


_SLACK_RUN_MAP_NAMESPACE = "slack_run_map"
_THREAD_RUN_KEY_PREFIX = "thread:"
_MESSAGE_RUN_KEY_PREFIX = "message:"


def _extract_run_id_from_store_item(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    value = item.get("value")
    if not isinstance(value, dict):
        return None
    run_id = value.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


async def store_slack_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    run_id: str,
    *,
    message_ts: str | None = None,
    triggering_user_id: str | None = None,
) -> None:
    """Persist Slack thread/message to LangGraph run mapping."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    value: dict[str, Any] = {"run_id": run_id, "thread_ts": thread_ts}
    if triggering_user_id:
        value["triggering_user_id"] = triggering_user_id
    try:
        await langgraph_client.store.put_item(
            namespace, f"{_THREAD_RUN_KEY_PREFIX}{thread_ts}", value
        )
        if message_ts:
            await langgraph_client.store.put_item(
                namespace,
                f"{_MESSAGE_RUN_KEY_PREFIX}{message_ts}",
                {**value, "message_ts": message_ts},
            )
    except Exception:
        logger.exception(
            "Failed to store Slack run mapping for channel=%s thread=%s run=%s",
            channel_id,
            thread_ts,
            run_id,
        )


async def store_slack_message_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
) -> None:
    """Persist a Slack message mapping using the current thread's run mapping."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        item = await langgraph_client.store.get_item(
            namespace, f"{_THREAD_RUN_KEY_PREFIX}{thread_ts}"
        )
        run_id = _extract_run_id_from_store_item(item)
        if not run_id:
            logger.debug(
                "No Slack thread run mapping found for channel=%s thread=%s",
                channel_id,
                thread_ts,
            )
            return
        triggering_user_id: str | None = None
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, dict):
                candidate = value.get("triggering_user_id")
                if isinstance(candidate, str) and candidate:
                    triggering_user_id = candidate
        await store_slack_run_mapping(
            langgraph_client,
            channel_id,
            thread_ts,
            run_id,
            message_ts=message_ts,
            triggering_user_id=triggering_user_id,
        )
    except Exception:
        logger.exception(
            "Failed to store Slack message run mapping for channel=%s message=%s",
            channel_id,
            message_ts,
        )


async def lookup_slack_run_mapping(
    langgraph_client: LangGraphClient,
    channel_id: str,
    message_ts: str,
) -> dict[str, Any] | None:
    """Return the stored mapping value for a Slack bot message, or None."""
    namespace = (_SLACK_RUN_MAP_NAMESPACE, channel_id)
    try:
        item = await langgraph_client.store.get_item(
            namespace, f"{_MESSAGE_RUN_KEY_PREFIX}{message_ts}"
        )
    except Exception:
        logger.exception(
            "Failed to look up Slack message run mapping for channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return None
    if not item:
        return None
    value = item.get("value")
    return value if isinstance(value, dict) else None


async def lookup_run_id_for_slack_message(
    langgraph_client: LangGraphClient,
    channel_id: str,
    message_ts: str,
) -> str | None:
    """Look up the LangGraph run mapped to a specific Slack bot message."""
    value = await lookup_slack_run_mapping(langgraph_client, channel_id, message_ts)
    if not value:
        return None
    run_id = value.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None
