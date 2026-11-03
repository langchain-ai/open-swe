"""Hand @openswe issue mentions to the MDA deployment's GitHub channel.

Open SWE's GitHub App keeps its one webhook URL here, at ``/webhooks/github``:
reviews, pull request comments, CI and automations still start from it. The issue
events that would start an agent run go to MDA's GitHub channel instead, exactly
as GitHub sent them, so the channel verifies GitHub's signature with the same
webhook secret (``mda deploy`` keeps the brought App's webhook here;
MDA_GITHUB_WEBHOOK_ELSEWHERE). The forward is a job (``FORWARD_JOB``), so a restart
doesn't lose it. A delivery MDA refuses, or that never gets through, leaves a 😕
on the comment that mentioned Open SWE, so its author knows.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, Self

import httpx2
from pydantic import BaseModel

from openswe.agent_api.client import mda_runtime_url
from openswe.github.app import get_github_app_installation_token
from openswe.github.comments import react_to_github_comment
from openswe.jobs import queue
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)

FORWARD_JOB: Final = "github_channel_forward"

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
# The longest Retry-After the forward waits out before trying again.
_MAX_RETRY_AFTER_SECONDS: Final = 60.0


def forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of GitHub's request the channel reads, by their canonical names."""
    return {name: value for name in FORWARDED_HEADERS if (value := headers.get(name))}


class ForwardJob(BaseModel):
    """A queued forward: GitHub's body, byte for byte, and the headers that sign it."""

    body: str
    headers: dict[str, str]

    @classmethod
    def of(cls, body: bytes, headers: Mapping[str, str]) -> Self:
        return cls(body=base64.b64encode(body).decode(), headers=forwarded_headers(headers))

    @property
    def raw_body(self) -> bytes:
        return base64.b64decode(self.body)

    async def run(self) -> None:
        await forward_to_mda_channel(self.raw_body, self.headers)


async def queue_forward(body: bytes, headers: Mapping[str, str]) -> None:
    """Queue the forward of a GitHub delivery to MDA's GitHub channel, once per delivery."""
    job = ForwardJob.of(body, headers)
    delivery = job.headers.get("X-GitHub-Delivery")
    await queue.schedule(
        FORWARD_JOB,
        job.model_dump(mode="json"),
        key=f"{FORWARD_JOB}:{delivery}" if delivery else None,
        # A redelivery while the first still waits is the same forward.
        replace=False,
    )


def _retry_after(response: httpx2.Response) -> float | None:
    """The wait a refusal asks for, in seconds and bounded; ``None`` when it names none."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except TypeError, ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


async def forward_to_mda_channel(body: bytes, headers: Mapping[str, str]) -> None:
    """Deliver a GitHub webhook to MDA's GitHub channel, retrying while MDA can't take it.

    MDA deduplicates deliveries by ``X-GitHub-Delivery``, so a retry after an answer
    that got lost starts nothing twice. A 429 or a 5xx is tried again, after the
    ``Retry-After`` the answer names, if any; any other 4xx would fail the same way
    again. A delivery that doesn't get through leaves a 😕 on its comment
    (:func:`react_lost`).
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
            wait: float | None = None
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
                if not _retryable(status):
                    break
                wait = _retry_after(response)
                logger.warning(
                    "MDA's GitHub channel could not take a forwarded delivery yet",
                    extra={"github_delivery": delivery, "status": status, "attempt": attempt},
                )
            if attempt < _ATTEMPTS:
                await asyncio.sleep(backoff if wait is None else wait)
                backoff *= 2
    # An unfollowed redirect took nothing either.
    delivered = status is not None and 200 <= status < 300
    outcome = (
        "delivered"
        if delivered
        else "refused"
        if status is not None and not _retryable(status)
        else "gave_up"
    )
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
        payload: object = json.loads(body)
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
