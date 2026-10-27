"""What a run last told the person, kept on the thread for run-completion notifications."""

import logging
from collections.abc import Mapping

from langgraph.config import get_config
from pydantic import BaseModel, ValidationError

from openswe.run_config import RunConfig
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

LAST_REPLY_KEY = "last_reply"
_MAX_TEXT_CHARS = 600


class LastReply(BaseModel):
    """The final answer one run gave; no text means it decided nobody needed one."""

    run_id: str
    text: str | None = None
    awaiting_choice: bool = False

    @classmethod
    def for_run(cls, metadata: Mapping[str, object], run_id: str | None) -> LastReply | None:
        raw = metadata.get(LAST_REPLY_KEY)
        if not run_id or not isinstance(raw, Mapping):
            return None
        try:
            reply = cls.model_validate(raw)
        except ValidationError:
            return None
        return reply if reply.run_id == run_id else None

    @classmethod
    async def record(
        cls, text: str | None, *, awaiting_choice: bool = False, fallback: bool = False
    ) -> None:
        """Remember the current run's answer on its thread, best-effort.

        A ``fallback`` answer never replaces one the run's reply tools already recorded.
        """
        try:
            config = get_config()
        except RuntimeError:
            return
        cfg = RunConfig.from_config(config)
        run_id = config.get("run_id") or cfg.run_id
        if not cfg.thread_id or not run_id:
            return
        reply = cls(
            run_id=str(run_id),
            text=text.strip()[:_MAX_TEXT_CHARS] if text and text.strip() else None,
            awaiting_choice=awaiting_choice,
        )
        client = langgraph_client()
        try:
            if fallback and cls.for_run(
                thread_metadata(await client.threads.get(cfg.thread_id)), reply.run_id
            ):
                return
            await client.threads.update(
                thread_id=cfg.thread_id, metadata={LAST_REPLY_KEY: reply.model_dump()}
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not record the run's last reply",
                exc_info=True,
                extra={"agent_thread_id": cfg.thread_id},
            )

    def summary(self) -> dict[str, object]:
        return {"text": self.text, "awaitingChoice": self.awaiting_choice}
