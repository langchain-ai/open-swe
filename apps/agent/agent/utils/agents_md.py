"""Helpers for reading agent instructions from AGENTS.md."""

from __future__ import annotations

import asyncio
import logging
import shlex

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


async def read_agents_md_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str | None,
) -> str | None:
    """Read AGENTS.md from the repo root if it exists."""
    if not repo_dir:
        return None

    safe_agents_path = shlex.quote(f"{repo_dir}/AGENTS.md")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        sandbox_backend.execute,
        f"test -f {safe_agents_path} && cat {safe_agents_path}",
    )
    if result.exit_code != 0:
        logger.debug("AGENTS.md not found at %s", safe_agents_path)
        return None
    content = result.output or ""
    content = content.strip()
    return content or None
