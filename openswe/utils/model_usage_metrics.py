"""Each model reply's input tokens, by whether the prompt cache served them, for Datadog.

Where the deployment sets ``DD_API_KEY``, the Agent Server runs under Datadog's
``datadog-init``, whose agent takes DogStatsD on the container's loopback, as the
server's own Go runtime metrics already reach it. A reply's counts go there as one UDP
datagram each, never waited on: with no agent listening, they are dropped.

``call_position`` keeps a thread's first model call apart from its follow-ups. A first
call can only read a prefix another thread cached, so a cache that misses there hides
behind the follow-ups' hits in an aggregate rate. A later run's first call
(``run_first``) is kept apart too: its run rebuilds the system prompt, so it can miss
the thread's cached history without anything inside a run breaking the cache.
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

type CallPosition = Literal["first", "run_first", "followup", "subagent"]


class ModelUsageMetrics:
    """The process's DogStatsD socket, which each reply's token counts go out on."""

    _socket: ClassVar[socket.socket | None] = None

    @classmethod
    def record(
        cls,
        replies: Sequence[BaseMessage],
        *,
        position: CallPosition,
        thread_id: str | None = None,
    ) -> None:
        """Log replies and count those with usage without raising into their report."""
        for reply in replies:
            if isinstance(reply, AIMessage):
                try:
                    cls._record_reply(reply, position=position, thread_id=thread_id)
                except Exception:
                    logger.warning(
                        "Model usage metrics were not sent",
                        exc_info=True,
                        extra={"thread_id": thread_id, "reply_id": reply.id},
                    )

    @classmethod
    def _record_reply(
        cls, reply: AIMessage, *, position: CallPosition, thread_id: str | None
    ) -> None:
        """Log normalized usage before sending one datagram, including zero token types."""
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
        logger.info(
            "Model usage observed",
            extra={
                **tags,
                "thread_id": thread_id,
                "reply_id": reply.id,
                "invocation_id": metadata.get("open_swe_invocation_id"),
                "usage_present": bool(reply.usage_metadata),
                "input_tokens": usage.get("input_tokens", 0),
                "uncached_input_tokens": counts["input"],
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
            },
        )
        if not reply.usage_metadata:
            return
        input_tokens = sum(counts.values())
        large_context_miss = (
            position == "followup" and input_tokens >= 100_000 and cache_read * 5 < input_tokens
        )
        cls._send(
            [
                f"{METRIC}:{count}|c|#token_type:{token_type},{tagged}"
                for token_type, count in counts.items()
            ]
            + [
                f"openswe_model_usage_calls:1|c|#{tagged}",
                f"openswe_model_large_context_cache_misses:{int(large_context_miss)}|c|#{tagged}",
            ]
        )

    @classmethod
    def _send(cls, lines: list[str]) -> None:
        if not lines or not any(
            os.environ.get(key)
            for key in ("DD_API_KEY", "DD_DOGSTATSD_URL", "DD_AGENT_HOST", "DD_DOGSTATSD_PORT")
        ):
            return
        if cls._socket is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setblocking(False)
                sock.connect(cls._destination())
            except Exception:
                sock.close()
                raise
            cls._socket = sock
        cls._socket.send("\n".join(lines).encode())

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
