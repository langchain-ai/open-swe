"""Each model reply's input tokens, by whether the prompt cache served them, for Datadog.

Where the deployment sets ``DD_API_KEY``, the Agent Server runs under Datadog's
``datadog-init``, whose agent takes DogStatsD on the container's loopback, as the
server's own Go runtime metrics already reach it. A reply's counts go there as one UDP
datagram each, never waited on: with no agent listening, they are dropped.

``call_position`` keeps a thread's first model call apart from its follow-ups. A first
call can only read a prefix another thread cached, so a cache that misses there hides
behind the follow-ups' hits in an aggregate rate.
"""

import logging
import os
import socket
from collections.abc import Sequence
from typing import ClassVar, Final, Literal
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage, BaseMessage

from openswe.config import ENV

logger = logging.getLogger(__name__)

METRIC: Final = "openswe_model_usage_tokens"

type CallPosition = Literal["first", "followup", "subagent"]


class ModelUsageMetrics:
    """The process's DogStatsD socket, which each reply's token counts go out on."""

    _socket: ClassVar[socket.socket | None] = None

    @classmethod
    def record(cls, replies: Sequence[BaseMessage], *, position: CallPosition) -> None:
        """Count ``replies``' input tokens: uncached, read from the cache, and written to it.

        Never raises into the report it counts.
        """
        try:
            for reply in replies:
                if isinstance(reply, AIMessage) and reply.usage_metadata:
                    cls._send(cls.lines(reply, position=position))
        except Exception:
            logger.debug("Model usage metrics were not sent", exc_info=True)

    @staticmethod
    def lines(reply: AIMessage, *, position: CallPosition) -> list[str]:
        """``reply``'s DogStatsD counter lines, one per token type, zeros included.

        A monitor that divides one token type by the others sees a group only once
        every type has a series, so a group that never hits the cache still reports
        ``cache_read:0``.
        """
        usage = reply.usage_metadata or {}
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read") or 0
        cache_creation = details.get("cache_creation") or 0
        # Every provider's ``input_tokens`` includes what the cache read and wrote.
        counts = {
            "input": max(usage.get("input_tokens", 0) - cache_read - cache_creation, 0),
            "cache_read": cache_read,
            "cache_creation": cache_creation,
        }
        metadata = reply.response_metadata
        tags = {
            # Not ``env``: Datadog's agent may add its own ``env`` tag to the same point.
            "openswe_env": ModelUsageMetrics._environment(),
            "provider": metadata.get("model_provider") or "unknown",
            "model": metadata.get("model_name") or metadata.get("model") or "unknown",
            "call_position": position,
        }
        tagged = ",".join(f"{key}:{ModelUsageMetrics._tag(value)}" for key, value in tags.items())
        return [
            f"{METRIC}:{count}|c|#token_type:{token_type},{tagged}"
            for token_type, count in counts.items()
        ]

    @classmethod
    def _send(cls, lines: list[str]) -> None:
        if not lines:
            return
        if cls._socket is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.connect(cls._destination())
            cls._socket = sock
        try:
            cls._socket.send("\n".join(lines).encode())
        except OSError:
            # Nothing listening (ECONNREFUSED from an earlier datagram) or a full buffer.
            logger.debug("DogStatsD did not take model usage metrics", exc_info=True)

    @staticmethod
    def _destination() -> tuple[str, int]:
        """Where DogStatsD listens: ``DD_DOGSTATSD_URL``, else the agent host, else loopback."""
        if (url := os.environ.get("DD_DOGSTATSD_URL")) and url.startswith("udp://"):
            parts = urlsplit(url)
            return parts.hostname or "127.0.0.1", parts.port or 8125
        host = os.environ.get("DD_AGENT_HOST") or "127.0.0.1"
        return host, int(os.environ.get("DD_DOGSTATSD_PORT") or 8125)

    @staticmethod
    def _environment() -> str:
        """``OPENSWE_ENV``, with ``prod`` read as ``production`` as the prompts read it."""
        environment = (ENV.OPENSWE_ENV.optional() or "local").strip().lower()
        return "production" if environment == "prod" else environment

    @staticmethod
    def _tag(value: object) -> str:
        """``value`` as a DogStatsD tag value, without the separators a datagram reads."""
        return str(value).strip().lower().translate(str.maketrans(",| ", "___"))
