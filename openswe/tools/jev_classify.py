import logging
from typing import Literal

import httpx2
from pydantic import BaseModel, ValidationError

from openswe.config import ENV
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
# Pinned so confidence thresholds tuned against it keep holding.
JEV_MODEL = "jev-1.13.0"
JEV_TIMEOUT_SECONDS = 10.0

Structured = str | dict[str, object] | list[object]


class JevQuestion(BaseModel):
    """One question Jev answers about the state, in Jev's own format."""

    type: Literal["choice", "noul", "score"]
    instructions: Structured
    criteria: Structured | None = None


class JevReply(BaseModel):
    """What Jev answers: each question's answer by name, and the tokens it used."""

    answers: dict[str, dict[str, object]]
    usage: dict[str, int] = {}


async def jev_classify(
    state: Structured,
    questions: dict[str, JevQuestion],
    model: str = JEV_MODEL,
) -> dict[str, object]:
    """Implement the `jev_classify` tool."""
    api_key = ENV.TYPESAFE_API_KEY.optional()
    if not api_key:
        return {"success": False, "error": "TYPESAFE_API_KEY is not configured."}
    body = {
        "model": model,
        "state": state,
        "questions": {
            name: question.model_dump(exclude_none=True) for name, question in questions.items()
        },
    }
    try:
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=JEV_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                JEV_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx2.HTTPError as e:
        logger.warning("jev_request_failed", exc_info=True)
        return {"success": False, "error": f"Jev request failed: {type(e).__name__}: {e}"}
    if response.status_code != 200:
        return {
            "success": False,
            "status_code": response.status_code,
            "error": f"Jev rejected the request: {response.text[:2000]}",
        }
    try:
        data = JevReply.model_validate_json(response.content)
    except ValidationError as e:
        logger.warning("jev_reply_unreadable", exc_info=True)
        return {"success": False, "error": f"Jev reply was unreadable: {e}"}
    return {"success": True, "model": model, "answers": data.answers, "usage": data.usage}
