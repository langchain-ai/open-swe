"""Tag this process's Datadog spans with the environment it serves."""

import logging
import os

from openswe.config import ENV

logger = logging.getLogger(__name__)


def configure_datadog_environment() -> None:
    if environment := ENV.OPENSWE_ENV.optional():
        os.environ["DD_ENV"] = environment
        try:
            from ddtrace import config
        except ImportError:
            logger.debug("Datadog tracing is unavailable", exc_info=True)
        else:
            config.env = environment
