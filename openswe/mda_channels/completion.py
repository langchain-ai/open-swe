"""Complete an MDA channel's run from its ``after_agent`` hook.

The channel starts the run itself, so no run-complete webhook follows it. The
run's middleware calls ``after_agent`` with the run token once the agent is done,
which is where the backend completes it, as the webhook does for its own runs.
A run that crashed never gets there; the thread status reconcile job completes it.
"""

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import messages_from_dict
from pydantic import JsonValue

from openswe.dispatch import CHANNEL_METADATA_KEY, channel_of
from openswe.invocation import INVOCATION_ID_KEY, LEGACY_INVOCATION_ID_KEY
from openswe.remote_runtime.tokens import RemoteRun
from openswe.threads import index

logger = logging.getLogger(__name__)

# What the run's configurable carries for its usage telemetry.
_INVOCATION_KEYS = (INVOCATION_ID_KEY, LEGACY_INVOCATION_ID_KEY, "invocation_started_at")


async def complete_after_agent(
    run: RemoteRun, run_id: str | None, messages: Sequence[dict[str, JsonValue]]
) -> None:
    """Complete a channel's run that finished; any other run waits for its webhook.

    Never raises: the agent's work is done either way. Completion that failed before
    it settled the thread leaves it busy, for the reconcile job to complete.
    """
    if not run_id:
        return
    try:
        thread = await index.get(run.thread_id)
        channel = channel_of(thread.metadata) if thread else None
        if channel is None:
            return
        metadata: dict[str, Any] = {
            key: run.configurable[key] for key in _INVOCATION_KEYS if key in run.configurable
        }
        metadata[CHANNEL_METADATA_KEY] = channel
        values: dict[str, Any] = {"messages": messages_from_dict(list(messages))}
        # Late: completion pulls in every surface's reply code.
        from openswe.completion import complete_run_once

        await complete_run_once(
            {
                "thread_id": run.thread_id,
                "run_id": run_id,
                "status": "success",
                "metadata": metadata,
                "values": values,
            },
            # The run is about to end; MDA still reports it running.
            thread_status="idle",
        )
    except Exception:
        logger.warning(
            "Completing a channel run failed",
            extra={"thread_id": run.thread_id, "run_id": run_id},
            exc_info=True,
        )
