from typing import Literal, TypedDict


class NoSlackResponseSuccess(TypedDict):
    success: Literal[True]
    reason: str


class NoSlackResponseError(TypedDict):
    success: Literal[False]
    error: str


async def no_slack_response_needed(
    reason: str,
) -> NoSlackResponseSuccess | NoSlackResponseError:
    """Record why the current Slack turn intentionally needs no response."""
    if not reason.strip():
        return {"success": False, "error": "Reason cannot be empty"}
    return {"success": True, "reason": reason.strip()}
