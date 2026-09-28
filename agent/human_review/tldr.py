"""What a pull request does, short enough for a review card."""

import asyncio
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.prompts import prompt

logger = logging.getLogger(__name__)

TLDR_MAX_CHARS = 280
_INPUT_MAX_CHARS = 8_000
_MAX_TOKENS = 512
_TIMEOUT_SECONDS = 15
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class _Tldr(BaseModel):
    tldr: str = Field(description="One or two sentences on what the change does and why")


def _clean(body: str) -> str:
    return _HTML_COMMENT.sub("", body).strip()


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= TLDR_MAX_CHARS:
        return flat
    return flat[: TLDR_MAX_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


async def _summarize(title: str, body: str) -> str:
    # Provider SDKs stay out of agent.webapp's import closure (tests/agent/test_import_hygiene.py).
    from agent.utils.model import make_model, provider_model_kwargs

    settings = await get_workspace_settings()
    model_id, effort = settings.default_thread_title_model
    model = make_model(
        model_id,
        use_gateway=settings.effective_gateway_enabled,
        **provider_model_kwargs(model_id, effort, max_tokens=_MAX_TOKENS),
    )
    async with asyncio.timeout(_TIMEOUT_SECONDS):
        result = await model.with_structured_output(_Tldr).ainvoke(
            [
                SystemMessage(content=prompt("human-review/tldr")),
                HumanMessage(content=f"Title: {title}\n\nDescription:\n{body[:_INPUT_MAX_CHARS]}"),
            ],
            config={"callbacks": [], "run_name": "human-review-tldr"},
        )
    return result.tldr if isinstance(result, _Tldr) else ""


async def pull_request_tldr(title: str, body: str | None) -> str:
    """The description itself when it is short; a generated summary when it is long."""
    cleaned = _clean(body or "")
    if len(" ".join(cleaned.split())) <= TLDR_MAX_CHARS:
        return _clip(cleaned)
    try:
        summary = await _summarize(title, cleaned)
    except Exception:
        logger.warning("Pull request summary failed; clipping the description", exc_info=True)
        summary = ""
    return _clip(summary or cleaned)
