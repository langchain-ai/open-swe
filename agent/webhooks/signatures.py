"""Signature verification for every inbound webhook.

One function per channel, side by side, so the three schemes (GitHub's
``sha256=`` HMAC, Linear's bare HMAC, Slack's timestamped ``v0=`` HMAC) can be
compared at a glance. All three reject the request when no secret is
configured — an unverified webhook is never trusted.
"""

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)


def verify_github_signature(body: bytes, signature: str, *, secret: str) -> bool:
    """Verify the ``X-Hub-Signature-256`` header of a GitHub webhook."""
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_linear_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the ``Linear-Signature`` header of a Linear webhook."""
    if not secret:
        logger.warning("LINEAR_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify the ``X-Slack-Signature`` header, rejecting stale timestamps."""
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET is not configured — rejecting webhook request")
        return False
    if not timestamp or not signature:
        return False
    try:
        request_timestamp = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - request_timestamp) > max_age_seconds:
        return False

    base_string = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
    expected = (
        "v0="
        + hmac.new(secret.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)
