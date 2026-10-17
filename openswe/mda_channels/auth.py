"""Only Open SWE's MDA deployment may call the routes its channels hand events to.

The deployment and the backend share ``MDA_SERVICE_SECRET``, Open SWE's own
credential between the two: the deployment presents it on every call to a
channel route. MDA already verified the provider's signature on the delivery.
"""

import hmac
from typing import Final

from fastapi import HTTPException, Request

from openswe.config import ENV

MDA_CHANNEL_SECRET_HEADER: Final = "x-mda-service-secret"  # noqa: S105 - a header name


def require_mda_channel(request: Request) -> None:
    """Refuse a caller without the shared secret; with none configured, refuse every caller."""
    expected = ENV.MDA_SERVICE_SECRET.optional()
    provided = request.headers.get(MDA_CHANNEL_SECRET_HEADER, "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid service credential")
