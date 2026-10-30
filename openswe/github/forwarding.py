"""Hand @openswe issue mentions to the MDA deployment's GitHub channel.

Open SWE's GitHub App keeps its one webhook URL here, at ``/webhooks/github``:
reviews, pull request comments, CI and automations still start from it. The issue
events that would start an agent run go to MDA's GitHub channel instead, exactly
as GitHub sent them, so the channel verifies GitHub's signature with the same
webhook secret (``mda deploy`` keeps the brought App's webhook here;
MDA_GITHUB_WEBHOOK_ELSEWHERE). A delivery MDA refuses, or that never gets through,
leaves a 😕 on the comment that mentioned Open SWE, so its author knows.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, Final

import httpx2

from openswe.agent_api.client import mda_runtime_url
from openswe.github.app import get_github_app_installation_token
from openswe.github.comments import react_to_github_comment
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

#: GitHub's headers the channel reads; the rest of the request stays here.
FORWARDED_HEADERS: Final = (
    "X-Hub-Signature-256",
    "X-GitHub-Event",
    "X-GitHub-Delivery",
    "Content-Type",
)
CHANNEL_EVENTS_PATH: Final = "/channels/github/events"
_ATTEMPTS: Final = 4
_TIMEOUT_SECONDS: Final = 10.0
_FIRST_BACKOFF_SECONDS: Final = 1.0


def forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of GitHub's request the channel reads, by their canonical names."""
    return {name: value for name in FORWARDED_HEADERS if (value := headers.get(name))}


async def forward_to_mda_channel(body: bytes, headers: Mapping[str, str]) -> None:
    """Deliver a GitHub webhook to MDA's GitHub channel, retrying while MDA can't take it.

    MDA deduplicates deliveries by ``X-GitHub-Delivery``, so a retry after an answer
    that got lost starts nothing twice. A 4xx would fail the same way again. A
    delivery that doesn't get through leaves a 😕 on its comment (:func:`react_lost`).
    """
    url = f"{mda_runtime_url()}{CHANNEL_EVENTS_PATH}"
    delivery = headers.get("X-GitHub-Delivery", "")
    backoff = _FIRST_BACKOFF_SECONDS
    status: int | None = None
    attempt = 0
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await client.post(url, content=body, headers=dict(headers))
            except httpx2.TransportError:
                status = None
                logger.warning(
                    "Forwarding a GitHub delivery to MDA's channel failed",
                    extra={"github_delivery": delivery, "attempt": attempt},
                    exc_info=True,
                )
            else:
                status = response.status_code
                if status < 500:
                    break
                logger.warning(
                    "MDA's GitHub channel could not take a forwarded delivery yet",
                    extra={"github_delivery": delivery, "status": status, "attempt": attempt},
                )
            if attempt < _ATTEMPTS:
                await asyncio.sleep(backoff)
                backoff *= 2
    # An unfollowed redirect took nothing either.
    delivered = status is not None and 200 <= status < 300
    outcome = "delivered" if delivered else "refused" if status and status < 500 else "gave_up"
    logger.log(
        logging.INFO if delivered else logging.ERROR,
        "Forwarded a GitHub delivery to MDA's channel"
        if delivered
        else "MDA's GitHub channel never took a forwarded delivery; redeliver it from the "
        "App's settings",
        extra={
            "github_delivery": delivery,
            "github_event": headers.get("X-GitHub-Event", ""),
            "forward_outcome": outcome,
            "status": status,
            "attempts": attempt,
        },
    )
    if not delivered:
        await react_lost(body, headers.get("X-GitHub-Event", ""))


async def react_lost(body: bytes, event_type: str) -> bool:
    """Leave a 😕 on the comment (or issue) a lost delivery came from; whether it landed."""
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    repository = payload.get("repository") or {}
    repo_config = {
        "owner": str((repository.get("owner") or {}).get("login") or ""),
        "name": str(repository.get("name") or ""),
    }
    subject = (
        (payload.get("comment") or {}).get("id")
        if event_type == "issue_comment"
        else (payload.get("issue") or {}).get("number")
        if event_type == "issues"
        else None
    )
    if not (isinstance(subject, int) and repo_config["owner"] and repo_config["name"]):
        return False
    installation_id = (payload.get("installation") or {}).get("id")
    try:
        token = await get_github_app_installation_token(
            installation_id=installation_id if isinstance(installation_id, int) else None
        )
        reacted = bool(token) and await react_to_github_comment(
            repo_config, subject, event_type=event_type, token=token or "", content="confused"
        )
    except Exception:  # noqa: BLE001 - the error log above already names the delivery
        logger.warning("Could not react to a lost GitHub delivery", exc_info=True)
        return False
    return reacted
