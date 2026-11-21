"""The route LangSmith's sandbox proxy calls back for a request's credentials.

Kept apart from ``proxy_callback`` so the web app serves it without importing the agent
stack that answering pulls in.
"""

from datetime import datetime

from fastapi import APIRouter, Request
from langsmith.sandbox import CALLBACK_SIGNATURE_HEADER
from pydantic import BaseModel

from openswe.sandboxes.tool_access import PROXY_CALLBACK_PATH

router = APIRouter(prefix=PROXY_CALLBACK_PATH, tags=["sandbox-proxy-callback"])


class ProxyCallbackAnswer(BaseModel):
    """What LangSmith's proxy adds to the request, as its callback client reads it."""

    headers: dict[str, str] = {}
    # When the credential in ``headers`` expires; None where it carries none.
    expires_at: datetime | None = None


@router.post("", include_in_schema=False)
async def sandbox_proxy_callback(request: Request) -> ProxyCallbackAnswer:
    """LangSmith's proxy asking for the headers of a request a sandbox makes."""
    from openswe.sandboxes.proxy_callback import SandboxProxyCallback

    return await SandboxProxyCallback.answer(
        await request.body(), request.headers.get(CALLBACK_SIGNATURE_HEADER)
    )
