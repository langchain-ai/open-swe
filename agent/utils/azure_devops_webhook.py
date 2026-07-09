"""Azure DevOps Service Hook webhook validation."""

from __future__ import annotations

import logging
import secrets

from fastapi import Request

from .azure_devops_payload import azure_devops_service_hook_should_process

logger = logging.getLogger(__name__)

DEFAULT_SECRET_HEADER = "X-Azure-DevOps-Webhook-Secret"


def verify_azure_devops_webhook_secret(
    request: Request,
    secret: str,
    *,
    header_name: str = DEFAULT_SECRET_HEADER,
) -> bool:
    """Constant-time compare of configured secret to incoming header value."""
    if not secret:
        logger.warning("AZURE_DEVOPS_WEBHOOK_SECRET is not configured — rejecting webhook")
        return False
    received = request.headers.get(header_name, "")
    if not received:
        logger.warning("Azure DevOps webhook missing header %s", header_name)
        return False
    try:
        return secrets.compare_digest(received.strip(), secret.strip())
    except (TypeError, ValueError):
        return False


__all__ = [
    "DEFAULT_SECRET_HEADER",
    "azure_devops_service_hook_should_process",
    "verify_azure_devops_webhook_secret",
]
