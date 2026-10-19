"""Where this deployment's channels ask the Open SWE backend what each event starts.

MDA's channels verify each delivery and own their conversations: they start the
runs on their threads. Each channel's ``on_event`` names the backend's endpoint for
it, which decides the rest, as for any Open SWE run, and answers with the run to
start, or nothing. Every call presents Open SWE's own credential between the two,
the backend's ``MDA_SERVICE_SECRET``.
"""

import os
from typing import Final

import httpx

from rt.backend import BACKEND_URL_ENV

SERVICE_SECRET_ENV: Final = "MDA_SERVICE_SECRET"
_SERVICE_SECRET_HEADER: Final = "x-mda-service-secret"
# MDA reads the channels to build the deployment, without its environment.
_UNCONFIGURED: Final = "https://open-swe-backend.invalid"


def backend_endpoint(path: str) -> httpx.Request:
    """The backend's endpoint at ``path``, as a channel's ``on_event`` takes it.

    Without ``OPEN_SWE_BACKEND_URL``, as when MDA builds the deployment, it names
    a host that never resolves: a call fails, and the channel says so in the
    conversation.
    """
    base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/") or _UNCONFIGURED
    secret = os.environ.get(SERVICE_SECRET_ENV, "").strip()
    return httpx.Request("POST", f"{base}{path}", headers={_SERVICE_SECRET_HEADER: secret})
