from typing import cast

from langchain_typesafe import Question, State
from pydantic import JsonValue

from openswe.utils.http_pool import pooled_transport
from openswe.utils.jev import Jev, JevUnavailable

CLASSIFY_TIMEOUT_SECONDS = 10.0


async def classify(
    state: str | dict[str, JsonValue] | list[JsonValue],
    questions: dict[str, Question],
) -> dict[str, object]:
    """Implement the `classify` tool."""
    jev = Jev(transport=pooled_transport, timeout=CLASSIFY_TIMEOUT_SECONDS)
    try:
        # JSON is a subset of State, which dict invariance hides from the checker.
        response = await jev.ask(cast(State, state), questions=questions)
    except JevUnavailable as exc:
        if exc.reason == "missing_credentials":
            return {"success": False, "error": "No LangSmith gateway key is configured."}
        cause = exc.__cause__
        return {
            "success": False,
            "error": f"{type(cause).__name__}: {cause}" if cause else exc.reason,
        }
    return {"success": True, **response.model_dump(mode="json", exclude={"model", "request_id"})}
