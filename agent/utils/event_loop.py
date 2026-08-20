"""Open SWE runs on exactly one event loop per process.

Sandbox state is cached per thread in a process-global registry and reused by
later runs. Isolated queue loops hand each worker its own loop, so anything
loop-affine a run leaves in that registry is unusable from the next run's loop
and strands the thread permanently.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

ISOLATED_LOOPS_ENV = "BG_JOB_ISOLATED_LOOPS"
_TRUTHY = {"1", "true", "yes", "on"}


def pin_single_event_loop() -> None:
    """Force the queue to run every job on the server's loop.

    Raises if the setting cannot be forced: running with isolated loops is worse
    than not booting, because it fails later as a permanently unusable thread.
    """
    requested = os.environ.get(ISOLATED_LOOPS_ENV, "").strip().lower() in _TRUTHY
    # Covers the server reading its config after us; importing it here instead
    # would drag in a module that demands DATABASE_URI just to be loaded.
    os.environ[ISOLATED_LOOPS_ENV] = "false"

    langgraph_config = sys.modules.get("langgraph_api.config")
    if langgraph_config is None:
        return

    if not hasattr(langgraph_config, ISOLATED_LOOPS_ENV):
        msg = (
            f"langgraph_api.config has no {ISOLATED_LOOPS_ENV}. The queue's loop model "
            "changed, so Open SWE can no longer guarantee one event loop per process."
        )
        raise RuntimeError(msg)

    setattr(langgraph_config, ISOLATED_LOOPS_ENV, False)
    if getattr(langgraph_config, ISOLATED_LOOPS_ENV):
        msg = f"Could not force {ISOLATED_LOOPS_ENV} off"
        raise RuntimeError(msg)

    if requested:
        logger.warning(
            "%s was set; forcing it off. Open SWE requires one event loop per process.",
            ISOLATED_LOOPS_ENV,
        )
