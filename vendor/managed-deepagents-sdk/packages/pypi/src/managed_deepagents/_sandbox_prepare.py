"""Run a sandbox declaration's ``prepare`` callback against a deploy bake builder.

``mda deploy`` / ``mda dev`` start this module on the deploy machine with
``python -m managed_deepagents._sandbox_prepare`` after creating the builder
sandbox. The callback can read local files and write them into the builder;
the CLI then captures the builder as the recipe snapshot.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys
from pathlib import Path

from deepagents.backends import LangSmithSandbox
from langsmith.sandbox import SandboxClient

from managed_deepagents.sandbox import SandboxDefinition

PROJECT_ROOT_ENV = "MDA_PREPARE_PROJECT_ROOT"
MODULE_ENV = "MDA_PREPARE_MODULE"
BUILDER_ENV = "MDA_PREPARE_BUILDER"
SANDBOX_API_ENDPOINT_ENV = "MDA_SANDBOX_API_ENDPOINT"
WORKSPACE_ID_ENV = "LANGSMITH_WORKSPACE_ID"
_TENANT_HEADER = "X-Tenant-Id"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required to run sandbox prepare")
    return value


def load_sandbox_definition(project_root: Path, module: str) -> SandboxDefinition:
    """Import the project's ``sandbox`` export with the project root as working directory."""
    os.chdir(project_root)
    sys.path.insert(0, str(project_root))
    definition = getattr(importlib.import_module(module), "sandbox", None)
    if not isinstance(definition, SandboxDefinition):
        raise TypeError(f"{module}.sandbox must be assigned from define_sandbox(...)")
    if definition.prepare is None:
        raise TypeError(f"{module}.sandbox does not declare prepare")
    return definition


async def run_prepare(definition: SandboxDefinition, client: SandboxClient, builder: str) -> None:
    """Hand the builder to ``prepare`` as the same backend agents use at runtime."""
    if definition.prepare is None:
        raise TypeError("sandbox does not declare prepare")
    backend = LangSmithSandbox(sandbox=client.get_sandbox(builder))
    result = definition.prepare(backend)
    if inspect.isawaitable(result):
        await result


def main() -> None:
    workspace_id = os.environ.get(WORKSPACE_ID_ENV, "").strip()
    client = SandboxClient(
        api_endpoint=_required_env(SANDBOX_API_ENDPOINT_ENV),
        headers={_TENANT_HEADER: workspace_id} if workspace_id else None,
    )
    definition = load_sandbox_definition(
        Path(_required_env(PROJECT_ROOT_ENV)), _required_env(MODULE_ENV)
    )
    asyncio.run(run_prepare(definition, client, _required_env(BUILDER_ENV)))


if __name__ == "__main__":
    main()
