"""Managed LangSmith sandbox declaration.

MDA owns the run-scoped name, reuse across turns, and lifecycle. Delete this
directory to run without a sandbox.

To provision a recipe snapshot, add ``sandbox/setup.sh``. ``mda deploy`` and
``mda dev`` bake it once; new threads clone it without re-running the script.
"""

from managed_deepagents import define_sandbox

sandbox = define_sandbox(
    # Reclaim idle sandboxes after 10 minutes.
    idle_ttl_seconds=600,
    # Default per-command timeout, in seconds.
    default_timeout=600,
)
