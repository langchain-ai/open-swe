"""Only Open SWE's MDA deployment may call the routes its channels hand events to.

The deployment and the backend share ``MDA_SERVICE_SECRET``: MDA signs every call
its channels make to an endpoint with it, as Standard Webhooks does. MDA already
verified the provider's signature on the delivery.
"""

from fastapi import HTTPException, Request
from standardwebhooks import Webhook, WebhookVerificationError

from openswe.config import ENV


async def require_mda_channel(request: Request) -> None:
    """Refuse a call MDA didn't sign with the shared secret; with none configured, every call."""
    secret = ENV.MDA_SERVICE_SECRET.optional()
    try:
        if not secret:
            raise WebhookVerificationError
        Webhook(secret).verify(await request.body(), dict(request.headers))
    except WebhookVerificationError, ValueError:
        raise HTTPException(status_code=401, detail="Invalid service credential") from None
