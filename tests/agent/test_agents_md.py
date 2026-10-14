import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openswe.utils import agents_md


def _make_response(status: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


@pytest.mark.asyncio
async def test_fetch_agents_md_oversized_agents_md_does_not_fall_back_to_claude_md() -> None:
    big = "x" * (agents_md._MAX_AGENTS_MD_BYTES + 1)
    with patch("httpx2.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[
                _make_response(200, big),
                _make_response(200, "# CLAUDE.md\nrules"),
            ]
        )
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result is None
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_scoped_docs_read_base_tree_not_head(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    directory = tmp_path / "ui"
    directory.mkdir()
    (directory / "AGENTS.md").write_text("trusted")
    (directory / "CLAUDE.md").write_text("fallback")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    base = (
        subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"]).decode().strip()
    )
    (directory / "AGENTS.md").write_text("untrusted")

    async def execute(command: str) -> SimpleNamespace:
        process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        return SimpleNamespace(exit_code=process.returncode, output=output.decode())

    backend = MagicMock()
    backend.aexecute = execute
    result = await agents_md.fetch_scoped_agents_md(backend, str(tmp_path), base, ["ui/app.py"])
    assert result == {"ui/AGENTS.md": "trusted"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("sandbox timed out"), "git warning\n{}"])
async def test_scoped_docs_failures_are_best_effort(failure: Exception | str) -> None:
    backend = MagicMock()
    backend.aexecute = (
        AsyncMock(side_effect=failure)
        if isinstance(failure, Exception)
        else AsyncMock(return_value=SimpleNamespace(exit_code=0, output=failure))
    )
    result = await agents_md.fetch_scoped_agents_md(backend, "/repo", "base", ["ui/app.py"])
    assert result == {}
