import logging
from typing import cast

import httpx2
from langchain_typesafe import Question, State
from pydantic import JsonValue

from openswe.utils.http_pool import pooled_transport
from openswe.utils.jev import SystemOneGateway

logger = logging.getLogger(__name__)

CLASSIFY_TIMEOUT_SECONDS = 10.0


async def classify(
    state: str | dict[str, JsonValue] | list[JsonValue],
    questions: dict[str, Question],
) -> dict[str, object]:
    """Implement the `classify` tool."""
    gateway = SystemOneGateway.configured()
    if gateway is None:
        return {"success": False, "error": "No LangSmith gateway key is configured."}
    try:
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=CLASSIFY_TIMEOUT_SECONDS
        ) as client:
            # JSON is a subset of State, which dict invariance hides from the checker.
            response = await gateway.classifier(client).ainvoke(
                {"state": cast(State, state), "questions": questions}, config={"tags": ["nostream"]}
            )
    except Exception as e:
        logger.warning("classify_failed", exc_info=True)
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
    return {"success": True, **response.model_dump(mode="json", exclude={"model", "request_id"})}
