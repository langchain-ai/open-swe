"""Slack Socket Mode shim for local dev against the real Slack API.

Opens a websocket to Slack using ``SLACK_APP_TOKEN`` (an ``xapp-`` level
token with ``connections:write``), receives Events API payloads, and
forwards each one to the local agent's ``/webhooks/slack`` endpoint with a
freshly-computed Slack signature. The agent behaves exactly as it would
behind an HTTP tunnel — same signature check, same handler, same env.

Requirements:
- The Slack app has Socket Mode enabled.
- ``SLACK_APP_TOKEN`` (xapp-) is set in ``.env``.
- ``SLACK_BOT_TOKEN`` and ``SLACK_SIGNING_SECRET`` are set (same values the
  agent loads).

Run:
    make slack-socket
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from threading import Event

import httpx
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web import WebClient

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("slack-socket")

APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
WEBHOOK_URL = os.environ.get("SLACK_LOCAL_WEBHOOK_URL", "http://localhost:2025/webhooks/slack")


def _forward(payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    base = f"v0:{ts}:{raw.decode('utf-8')}".encode("utf-8")
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256).hexdigest()
    resp = httpx.post(
        WEBHOOK_URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
        timeout=10.0,
    )
    logger.info("forwarded event → %s (HTTP %s)", WEBHOOK_URL, resp.status_code)


def _handle(client: SocketModeClient, req: SocketModeRequest) -> None:
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    if req.type != "events_api":
        logger.debug("skipping envelope type=%s", req.type)
        return
    try:
        _forward(req.payload)
    except Exception:
        logger.exception("failed to forward event")


def main() -> None:
    client = SocketModeClient(app_token=APP_TOKEN, web_client=WebClient(token=BOT_TOKEN))
    client.socket_mode_request_listeners.append(_handle)
    client.connect()
    logger.info("connected — forwarding Slack events to %s", WEBHOOK_URL)
    Event().wait()


if __name__ == "__main__":
    main()
