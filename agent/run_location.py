"""Where a thread's commands run.

Deliberately free of dependencies: the dashboard reads these on the request
path, and ``agent.webapp`` must not pull in the agent stack (see
``tests/agent/test_import_hygiene.py``).

``run_location`` is not called ``environment``: that name already belongs to
the named sandbox snapshots in ``agent.dashboard.environments``.
"""

from collections.abc import Mapping
from typing import Any

LOCAL_RUN_LOCATION = "local"
CLOUD_RUN_LOCATION = "cloud"


def run_location(metadata: Mapping[str, Any]) -> str:
    return (
        LOCAL_RUN_LOCATION
        if metadata.get("run_location") == LOCAL_RUN_LOCATION
        else CLOUD_RUN_LOCATION
    )


def is_local_run(metadata: Mapping[str, Any]) -> bool:
    return run_location(metadata) == LOCAL_RUN_LOCATION
