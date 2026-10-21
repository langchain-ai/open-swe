"""Distill the conversation behind a pull request into context a public repository can show.

People outside the company cannot follow a Slack, Linear or dashboard link from a
public pull request, so the description carries a short, scrubbed summary of what
was asked instead. Only conversations everyone in the workspace could already read
are summarized: DMs, private channels, externally shared channels and private
threads never are.
"""

import asyncio
import logging
import re
from collections.abc import Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from openswe.dashboard.options import gate_fable_model
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.prompts import prompt
from openswe.review.author_guidance import SteeringHistory
from openswe.run_config import RunConfig
from openswe.slack.channels import SlackChannel
from openswe.threads.summary import thread_is_private
from openswe.utils.model import make_model, provider_model_kwargs
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

CONTEXT_SUMMARY = "<summary>Context</summary>"
MAX_CONTEXT_CHARS = 1_500
CONTEXT_MAX_TOKENS = 1_024
CONTEXT_TIMEOUT_SECONDS = 30
_MENTION_RE = re.compile(r"@(?=[\w-])")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")


class _PublicContext(BaseModel):
    summary: str = Field(description="Scrubbed Markdown context, or empty when nothing is safe")


async def _conversation_is_shareable(cfg: RunConfig) -> bool:
    if cfg.source == "linear":
        return cfg.linear_issue is not None
    if cfg.source == "slack":
        slack = cfg.slack_thread
        if slack is None or not slack.channel_id:
            return False
        channel = await SlackChannel.load(slack.channel_id)
        return channel is not None and channel.public
    if cfg.source == "dashboard" and cfg.thread_id:
        thread = await langgraph_client().threads.get(cfg.thread_id)
        metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
        return isinstance(metadata, Mapping) and not thread_is_private(metadata)
    return False


async def _summarize(cfg: RunConfig, history: SteeringHistory) -> str:
    settings = await cached_workspace_settings(cfg.workspace_slug)
    model_id, effort = settings.default_thread_title_model
    model_id, effort = gate_fable_model(model_id, effort, fable_enabled=settings.fable_enabled)
    model = make_model(
        model_id,
        use_gateway=settings.effective_gateway_enabled,
        **provider_model_kwargs(model_id, effort, max_tokens=CONTEXT_MAX_TOKENS),
    )
    async with asyncio.timeout(CONTEXT_TIMEOUT_SECONDS):
        result = await model.with_structured_output(_PublicContext).ainvoke(
            [
                SystemMessage(content=prompt("pull-request/public-context")),
                HumanMessage(
                    content=f"<author_messages>\n{history.messages_block(with_authors=False)}\n</author_messages>"
                ),
            ],
            # Empty callbacks, so this call cannot stream its tokens into the user's thread.
            config={"callbacks": [], "run_name": "public-pr-context"},
        )
    return result.summary.strip() if isinstance(result, _PublicContext) else ""


def _render(summary: str) -> str:
    text = _HTML_TAG_RE.sub("", summary[:MAX_CONTEXT_CHARS])
    # A zero-width space keeps GitHub from turning a stray handle into a notification.
    text = _MENTION_RE.sub("@\u200b", text)
    return f"<details>\n{CONTEXT_SUMMARY}\n\n{text}\n\n</details>"


async def public_context_section(cfg: RunConfig) -> str | None:
    """A collapsed Context block for a public repository's PR body, or ``None``."""
    if not cfg.thread_id or not await _conversation_is_shareable(cfg):
        return None
    history = await SteeringHistory.for_thread(cfg.thread_id)
    if history is None:
        return None
    summary = await _summarize(cfg, history)
    return _render(summary) if summary else None
