"""Pure functions over Slack payloads: parse, normalize, render.

Nothing here talks to Slack or to the store, so the transport layer
(:mod:`agent.slack.api`) and the thread-mapping store
(:mod:`agent.slack.threads`) can both build on it.
"""

import copy
import re
from typing import Any

from ..langsmith.run_usage import RunUsageSummary

SLACK_WEB_LINK_FOOTER_LABEL = "Open in Web"
SLACK_SECTION_TEXT_MAX_CHARS = 3000
SLACK_FORWARDED_ATTACHMENT_MAX_COUNT = 10
SLACK_FORWARDED_ATTACHMENT_MAX_DEPTH = 4
SLACK_FORWARDED_ATTACHMENT_MAX_NODES = 50
SLACK_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS = 8000

TRACE_REPLY_WEB_HANDOFF_NOTICE = (
    "Conversation moved to Web — use the `Open in Web` link above for follow-ups."
)

SlackChannelContext = dict[str, str]

_SLACK_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_SLACK_MESSAGE_TS_RE = re.compile(r"^[0-9]{1,20}(?:\.[0-9]{1,12})?$")


def is_slack_channel_id(value: str) -> bool:
    return bool(_SLACK_CHANNEL_ID_RE.fullmatch(value))


def is_slack_message_ts(value: str) -> bool:
    return bool(_SLACK_MESSAGE_TS_RE.fullmatch(value))


def parse_slack_ts(ts: str | None) -> float:
    """A Slack timestamp as seconds; unparseable values sort to the beginning."""
    try:
        return float(ts or "0")
    except (TypeError, ValueError):
        return 0.0


def slack_message_bot_id(message: dict[str, Any]) -> str:
    """The bot identifier on a Slack message, or "" when a person authored it."""
    bot_id = message.get("bot_id")
    if isinstance(bot_id, str) and bot_id.strip():
        return bot_id.strip()
    if message.get("subtype") == "bot_message":
        username = message.get("username")
        return username.strip() if isinstance(username, str) and username.strip() else "unknown"
    user_id = message.get("user")
    return "" if isinstance(user_id, str) and user_id.strip() else "unknown"


def slack_message_bot_name(message: dict[str, Any]) -> str:
    """The display name for a bot-authored Slack message."""
    profile = message.get("bot_profile")
    if isinstance(profile, dict):
        name = profile.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    username = message.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return "Bot"


def is_own_slack_message(message: dict[str, Any], bot_user_id: str) -> bool:
    """Whether Open SWE itself posted this Slack message.

    Only the authoring user id proves it. A display name cannot: any app may post
    under our configured username, and treating that as proof would hide a third
    party's message from the transcript and attribute it to us.
    """
    return bool(bot_user_id) and message.get("user") == bot_user_id


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


def select_slack_context_messages(
    messages: list[dict[str, Any]],
    current_message_ts: str,
    bot_user_id: str,
    bot_username: str = "",
    *,
    treat_all_messages_as_mentions: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Select context from thread start or previous bot mention."""
    if not messages:
        return [], "thread_start"

    current_ts = parse_slack_ts(current_message_ts)
    ordered = sorted(messages, key=lambda item: parse_slack_ts(item.get("ts")))
    up_to_current = [item for item in ordered if parse_slack_ts(item.get("ts")) <= current_ts]
    if not up_to_current:
        up_to_current = ordered

    mention_tokens = []
    if bot_user_id:
        mention_tokens.append(f"<@{bot_user_id}>")
    if bot_username:
        mention_tokens.append(f"@{bot_username}")
    if not mention_tokens and not treat_all_messages_as_mentions:
        return up_to_current, "thread_start"

    last_mention_index = -1
    for index, message in enumerate(up_to_current[:-1]):
        text = message.get("text", "")
        is_explicit_mention = isinstance(text, str) and any(
            token in text for token in mention_tokens
        )
        user_id = message.get("user")
        is_user_message = (
            isinstance(user_id, str)
            and bool(user_id)
            and user_id != bot_user_id
            and not message.get("bot_id")
            and not message.get("bot_profile")
        )
        if is_explicit_mention or (treat_all_messages_as_mentions and is_user_message):
            last_mention_index = index

    if last_mention_index >= 0:
        return up_to_current[last_mention_index:], "last_mention"
    return up_to_current, "thread_start"


def _format_forwarded_slack_attachments(attachments: Any) -> str:
    forwarded: list[str] = []
    rendered_count = 0
    visited_count = 0

    def visit(values: Any, depth: int) -> None:
        nonlocal rendered_count, visited_count
        if depth > SLACK_FORWARDED_ATTACHMENT_MAX_DEPTH or not isinstance(values, list):
            return

        for attachment in values:
            if (
                rendered_count >= SLACK_FORWARDED_ATTACHMENT_MAX_COUNT
                or visited_count >= SLACK_FORWARDED_ATTACHMENT_MAX_NODES
            ):
                return
            visited_count += 1
            if not isinstance(attachment, dict):
                continue

            is_forwarded = any(
                attachment.get(flag) is True
                for flag in ("is_share", "is_msg_unfurl", "is_reply_unfurl")
            )
            if is_forwarded:
                author = attachment.get("author_name")
                author = author.strip() if isinstance(author, str) else ""
                content = attachment.get("text")
                if not isinstance(content, str) or not content.strip():
                    content = attachment.get("fallback")
                content = content.strip() if isinstance(content, str) else ""
                if len(content) > SLACK_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS:
                    content = (
                        content[:SLACK_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS].rstrip()
                        + "… [truncated]"
                    )
                source = attachment.get("from_url")
                source = source.strip() if isinstance(source, str) else ""

                label = "[Forwarded Slack message"
                if author:
                    label += f" from {author}"
                label += "]"
                parts = [label]
                if content:
                    parts.append(content)
                if source:
                    parts.append(f"Source: {source}")
                if len(parts) > 1:
                    indentation = "  " * depth
                    forwarded.append("\n".join(f"{indentation}{part}" for part in parts))
                    rendered_count += 1

            visit(attachment.get("attachments"), depth + 1)

    visit(attachments, 0)
    return "\n".join(forwarded)


def format_slack_messages_for_prompt(
    messages: list[dict[str, Any]],
    user_names_by_id: dict[str, str] | None = None,
    bot_user_id: str = "",
    bot_username: str = "",
) -> str:
    """Format Slack messages, including forwarded context, as readable prompt text."""
    if not messages:
        return "(no thread messages available)"

    lines: list[str] = []
    for message in messages:
        forwarded = _format_forwarded_slack_attachments(message.get("attachments"))
        text = replace_bot_mention_with_username(
            str(message.get("text", "")),
            bot_user_id=bot_user_id,
            bot_username=bot_username,
        ).strip() or ("[forwarded message]" if forwarded else "[non-text message]")
        user_id = message.get("user")
        if is_own_slack_message(message, bot_user_id):
            author = f"@{bot_username or 'Open SWE'}(self)"
        elif slack_message_bot_id(message):
            author = f"@{slack_message_bot_name(message)}(bot)"
        else:
            author_name = (user_names_by_id or {}).get(str(user_id)) or str(user_id)
            author = f"@{author_name}({user_id})"
        raw_message_ts = message.get("ts")
        message_ts = raw_message_ts.strip() if isinstance(raw_message_ts, str) else ""
        identifier = f" [message_ts={message_ts}]" if is_slack_message_ts(message_ts) else ""
        line = f"{author}{identifier}: {text}"
        if forwarded:
            line += f"\n{forwarded}"
        lines.append(line)
    return "\n".join(lines)


def _channel_section_value(channel: dict[str, Any] | None, key: str) -> str:
    if not isinstance(channel, dict):
        return ""
    section = channel.get(key)
    if isinstance(section, dict):
        value = section.get("value")
        if isinstance(value, str):
            return value.strip()
    value = channel.get(key)
    return value.strip() if isinstance(value, str) else ""


def extract_channel_description_text(channel: dict[str, Any] | None) -> str:
    """Combine a Slack channel's topic and purpose text into one string."""
    parts = [
        value for key in ("topic", "purpose") if (value := _channel_section_value(channel, key))
    ]
    return "\n".join(parts)


def normalize_slack_channel_context(
    channel_id: str, channel: dict[str, Any] | None
) -> SlackChannelContext:
    """Normalize Slack channel info for prompts and metadata."""
    name = ""
    name_normalized = ""
    if isinstance(channel, dict):
        raw_name = channel.get("name")
        raw_normalized = channel.get("name_normalized")
        if isinstance(raw_name, str):
            name = raw_name.strip()
        if isinstance(raw_normalized, str):
            name_normalized = raw_normalized.strip()
    topic = _channel_section_value(channel, "topic")
    purpose = _channel_section_value(channel, "purpose")
    description = "\n".join(value for value in (topic, purpose) if value)
    return {
        "id": channel_id,
        "name": name,
        "name_normalized": name_normalized,
        "topic": topic,
        "purpose": purpose,
        "description": description,
    }


def get_slack_channel_context_description(channel_context: dict[str, Any] | None) -> str:
    """Extract prompt-safe description text from normalized channel context."""
    if not isinstance(channel_context, dict):
        return ""
    description = channel_context.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    parts: list[str] = []
    for key in ("topic", "purpose"):
        value = channel_context.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def is_slack_channel_named(channel_context: dict[str, Any] | None, expected_name: str) -> bool:
    """Check normalized channel context against a Slack channel name."""
    if not isinstance(channel_context, dict):
        return False
    expected = expected_name.strip().lower()
    return any(
        isinstance(value, str) and value.strip().lower() == expected
        for value in (channel_context.get("name"), channel_context.get("name_normalized"))
    )


def _format_token_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _safe_model_label(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:/+\-]", "-", model)[:48].strip("-")


def format_slack_run_usage(usage: RunUsageSummary | None) -> str:
    if usage is None:
        return ""
    labels = sorted({label for model in usage.models if (label := _safe_model_label(model))})
    model_text = " + ".join(labels[:3])
    if len(labels) > 3:
        model_text = f"{model_text} +{len(labels) - 3}"
    parts = [model_text] if model_text else []
    if usage.session_cost_usd is not None:
        parts.append(format_slack_session_cost(usage.session_cost_usd))
    elif usage.main_agent_tokens is not None:
        parts.append(f"{_format_token_count(usage.main_agent_tokens)} main-agent tokens")
    return " • ".join(parts)


_SESSION_COST_LABEL_RE = re.compile(
    r"(?: • )?(?:<\$0\.01|\$[0-9]+(?:\.[0-9]+)?)(?: session cost)?$"
)
_MAIN_AGENT_TOKEN_LABEL_RE = re.compile(r"(?: • )?[0-9]+(?:\.[0-9]+)?[KM]? main-agent tokens$")


def format_slack_session_cost(cost: float) -> str:
    if 0 < cost < 0.01:
        return "<$0.01"
    return f"${cost:.2f}"


def _replace_slack_session_cost(text: str, cost: float, *, require_web_link: bool) -> str:
    if require_web_link and SLACK_WEB_LINK_FOOTER_LABEL not in text:
        return text
    cleaned = _SESSION_COST_LABEL_RE.sub("", text).rstrip()
    cleaned = _MAIN_AGENT_TOKEN_LABEL_RE.sub("", cleaned).rstrip()
    return f"{cleaned} • {format_slack_session_cost(cost)}"


def with_slack_session_cost(
    text: str,
    blocks: list[dict[str, Any]] | None,
    cost: float,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Replace the cumulative cost in a live Slack footer without changing its blocks."""
    updated_text = _replace_slack_session_cost(text, cost, require_web_link=True)
    if blocks is None:
        return updated_text, None

    updated_blocks = copy.deepcopy(blocks)
    candidates: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for block in updated_blocks:
        if block.get("type") != "context":
            continue
        values: list[dict[str, Any]] = []
        block_text = block.get("text")
        if isinstance(block_text, dict):
            values.append(block_text)
        elements = block.get("elements")
        if isinstance(elements, list):
            values.extend(item for item in elements if isinstance(item, dict))
        for value in values:
            value_text = value.get("text")
            if not isinstance(value_text, str):
                continue
            if "main-agent tokens" in value_text:
                candidates.append(value)
            elif SLACK_WEB_LINK_FOOTER_LABEL in value_text:
                fallback_candidates.append(value)

    target = next(iter(candidates or fallback_candidates), None)
    if target is not None:
        target["text"] = _replace_slack_session_cost(
            str(target.get("text") or ""), cost, require_web_link=False
        )
    elif (
        updated_text != text
        and updated_blocks
        and all(block.get("type") == "rich_text" for block in updated_blocks)
    ):
        updated_blocks = None
    return updated_text, updated_blocks


def format_slack_web_link_footer(
    dashboard_url: str | None, usage: RunUsageSummary | None = None
) -> str:
    """Format the compact Slack Web footer link."""
    if not dashboard_url:
        return ""
    footer = f"<{dashboard_url}|{SLACK_WEB_LINK_FOOTER_LABEL}>"
    usage_text = format_slack_run_usage(usage)
    return f"{footer} • {usage_text}" if usage_text else footer


def append_slack_web_link_footer(
    text: str, dashboard_url: str | None, usage: RunUsageSummary | None = None
) -> str:
    """Append the compact Slack Web footer link to fallback text."""
    footer = format_slack_web_link_footer(dashboard_url, usage)
    if not footer or footer in text:
        return text
    stripped = text.rstrip()
    if not stripped:
        return footer
    return f"{stripped} {footer}"


def _slack_web_link_context_block(
    dashboard_url: str | None, usage: RunUsageSummary | None = None
) -> dict[str, Any] | None:
    footer = format_slack_web_link_footer(dashboard_url, usage)
    if not footer:
        return None
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]}


def _block_contains_text(block: dict[str, Any], needle: str) -> bool:
    text = block.get("text")
    if isinstance(text, dict) and needle in str(text.get("text") or ""):
        return True
    elements = block.get("elements")
    if isinstance(elements, list):
        return any(
            isinstance(item, dict) and needle in str(item.get("text") or "") for item in elements
        )
    return False


def with_slack_web_link_context_block(
    text: str,
    blocks: list[dict[str, Any]] | None,
    dashboard_url: str | None,
    usage: RunUsageSummary | None = None,
) -> list[dict[str, Any]] | None:
    """Add the Web footer as a context block, without duplicating one already there."""
    context_block = _slack_web_link_context_block(dashboard_url, usage)
    if context_block is None:
        return blocks
    if not blocks:
        if len(text) > SLACK_SECTION_TEXT_MAX_CHARS:
            return None
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            context_block,
        ]
    updated_blocks = copy.deepcopy(blocks)
    if dashboard_url and any(
        _block_contains_text(block, dashboard_url) for block in updated_blocks
    ):
        usage_text = format_slack_run_usage(usage)
        if not usage_text or any(
            _block_contains_text(block, usage_text) for block in updated_blocks
        ):
            return updated_blocks
        context_block["elements"][0]["text"] = usage_text
    updated_blocks.append(context_block)
    return updated_blocks


def format_trace_reply(
    trace_url: str | None, dashboard_url: str | None, *, moved_to_web: bool = False
) -> str:
    """Format the trace reply with status text."""
    links = []
    if trace_url:
        links.append(f"<{trace_url}|View trace>")
    if dashboard_url:
        links.append(f"<{dashboard_url}|Open in Web>")
    head = f"{' • '.join(links)}\n" if links else ""
    if moved_to_web:
        return f"{head}_{TRACE_REPLY_WEB_HANDOFF_NOTICE}_"
    return head.rstrip()
