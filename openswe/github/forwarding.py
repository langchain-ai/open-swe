"""Hand @openswe issue mentions to the MDA deployment's GitHub channel.

Open SWE's GitHub App keeps its one webhook URL here, at ``/webhooks/github``:
reviews, pull request comments, CI and automations still start from it. The issue
events that would start an agent run go to MDA's GitHub channel instead, exactly
as GitHub sent them, so the channel verifies GitHub's signature with the same
webhook secret (``mda deploy`` keeps the brought App's webhook here;
MDA_GITHUB_WEBHOOK_ELSEWHERE).
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Final

import httpx2

from openswe.remote_runtime.client import mda_runtime_url

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
    that got lost starts nothing twice. A 4xx would fail the same way again.
    """
    url = f"{mda_runtime_url()}{CHANNEL_EVENTS_PATH}"
    delivery = headers.get("X-GitHub-Delivery", "")
    backoff = _FIRST_BACKOFF_SECONDS
    async with httpx2.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await client.post(url, content=body, headers=dict(headers))
            except httpx2.TransportError:
                logger.warning(
                    "Forwarding a GitHub delivery to MDA's channel failed",
                    extra={"github_delivery": delivery, "attempt": attempt},
                    exc_info=True,
                )
            else:
                if response.status_code < 500:
                    if response.is_error:
                        logger.error(
                            "MDA's GitHub channel refused a forwarded delivery",
                            extra={"github_delivery": delivery, "status": response.status_code},
                        )
                    return
                logger.warning(
                    "MDA's GitHub channel could not take a forwarded delivery yet",
                    extra={
                        "github_delivery": delivery,
                        "status": response.status_code,
                        "attempt": attempt,
                    },
                )
            if attempt < _ATTEMPTS:
                await asyncio.sleep(backoff)
                backoff *= 2
    logger.error(
        "Gave up forwarding a GitHub delivery to MDA's channel; redeliver it from the App's "
        "settings",
        extra={"github_delivery": delivery},
    )
