"""Structured product usage events for server-side features."""

import logging

logger = logging.getLogger(__name__)


def log_product_event(
    event: str,
    *,
    user_id: str | None = None,
    source: str,
    **dimensions: str | bool | int | None,
) -> None:
    """Emit a searchable INFO event without recording user content."""
    logger.info(
        "Product analytics event",
        extra={
            "product_analytics": True,
            "product_event": event,
            "product_source": source,
            "product_user_id": user_id,
            "product_dimensions": {
                key: value for key, value in dimensions.items() if value is not None
            },
        },
    )
