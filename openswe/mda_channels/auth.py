"""Only Open SWE's MDA deployment may call the routes its channels hand events to.

The deployment and the backend share ``MDA_SERVICE_SECRET``. MDA signs every call
its channels make to an endpoint with it, as Standard Webhooks does, and the
backend verifies the signature, which also refuses a replay older than five
minutes. MDA already verified the provider's signature on the delivery.
"""

import logging
from typing import ClassVar

from fastapi import HTTPException, Request
from standardwebhooks import Webhook, WebhookVerificationError

from openswe.api.body_limit import BodyLimitedRoute
from openswe.config import ENV

logger = logging.getLogger(__name__)


class MdaChannelRoute(BodyLimitedRoute):
    """A channel route: the body is capped while it streams in, before it is verified."""

    max_bytes: ClassVar[int] = 2 * 1024 * 1024


async def require_mda_channel(request: Request) -> None:
    """Refuse a call MDA didn't sign with the shared secret; with none configured, every call."""
    secret = ENV.MDA_SERVICE_SECRET.optional()
    if not secret:
        raise HTTPException(status_code=401, detail="Invalid service credential")
    try:
        Webhook(secret).verify(await request.body(), dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(status_code=401, detail="Invalid service credential") from None
    except ValueError:
        # A secret that isn't base64 can't verify anything.
        logger.error("MDA_SERVICE_SECRET is not a Standard Webhooks secret")
        raise HTTPException(status_code=401, detail="Invalid service credential") from None
