"""Managed LangSmith sandbox declaration.

MDA owns the run-scoped name, reuse across turns, and lifecycle. Delete this
directory to run without a sandbox.

To provision a recipe snapshot, add ``sandbox/setup.sh``. ``mda deploy`` and
``mda dev`` bake it once; new threads clone it without re-running the script.
"""

__MDA_SANDBOX_STDLIB__from managed_deepagents import __MDA_SANDBOX_IMPORTS__define_sandbox

sandbox = define_sandbox(
__MDA_SANDBOX_OPTIONS__)
