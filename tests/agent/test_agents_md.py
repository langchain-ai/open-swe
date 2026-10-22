import asyncio
import logging
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse

from openswe.utils import agents_md


class _LocalBackend:
    def __init__(self) -> None:
        self.uploaded_paths: list[Path] = []
        self.commands: list[str] = []

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses = []
        for path, content in files:
            self.uploaded_paths.append(Path(path))
            await asyncio.to_thread(Path(path).write_bytes, content)
            responses.append(FileUploadResponse(path=path))
        return responses

    async def aexecute(self, command: str) -> ExecuteResponse:
        self.commands.append(command)
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await process.communicate()
        return ExecuteResponse(exit_code=process.returncode, output=output.decode())


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
async def test_scoped_docs_read_base_tree_not_head_for_large_changes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tmp_path = tmp_path / "repo with 'quotes'"
    tmp_path.mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    changed_files = [
        f"packages/component-{directory:04d}-with-a-long-name/src/file-{file}.py"
        for directory in range(4000)
        for file in range(5)
    ]
    last_directory = tmp_path / Path(changed_files[-1]).parent
    last_directory.mkdir(parents=True)
    (last_directory / "CLAUDE.md").write_text("late scoped fallback")
    (tmp_path / "packages" / "AGENTS.md").write_text("shared conventions")
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

    backend = _LocalBackend()
    result = await agents_md.fetch_scoped_agents_md(
        backend, str(tmp_path), base, [*changed_files, "ui/app.py"]
    )
    assert result == {
        "ui/AGENTS.md": "trusted",
        "packages/AGENTS.md": "shared conventions",
        f"{Path(changed_files[-1]).parent}/CLAUDE.md": "late scoped fallback",
    }
    assert max(map(len, backend.commands)) < 8192
    assert backend.uploaded_paths
    assert all(not path.exists() for path in backend.uploaded_paths)
    assert not caplog.records

    with caplog.at_level(logging.WARNING, logger=agents_md.__name__):
        result = await agents_md.fetch_scoped_agents_md(
            backend, str(tmp_path), "missing-base-ref", changed_files
        )
    assert result == {}
    assert "Failed to read scoped agent docs" in caplog.text
    assert len(set(backend.uploaded_paths)) == 2
    assert all(not path.exists() for path in backend.uploaded_paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("upload", TimeoutError("upload timed out")),
        ("upload", "permission_denied"),
        ("upload", None),
        ("read", TimeoutError("sandbox timed out")),
        ("read", "git warning\n{}"),
    ],
)
async def test_scoped_docs_failures_are_best_effort(
    stage: str, failure: Exception | str | None, caplog: pytest.LogCaptureFixture
) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[FileUploadResponse(path="request")])
    cleanup = ExecuteResponse(exit_code=0, output="")
    backend.aexecute = AsyncMock(return_value=cleanup)
    if stage == "upload":
        if isinstance(failure, Exception):
            backend.aupload_files.side_effect = failure
        else:
            backend.aupload_files.return_value = (
                [FileUploadResponse(path="request", error=failure)] if failure else []
            )
    else:
        read = (
            failure
            if isinstance(failure, Exception)
            else ExecuteResponse(exit_code=0, output=failure or "")
        )
        backend.aexecute.side_effect = [read, cleanup]
    with caplog.at_level(logging.WARNING, logger=agents_md.__name__):
        result = await agents_md.fetch_scoped_agents_md(backend, "/repo", "base", ["ui/app.py"])
    assert result == {}
    assert "Failed to load scoped agent docs" in caplog.text
    request_path = backend.aupload_files.call_args.args[0][0][0]
    assert backend.aexecute.call_args.args[0] == f"rm -f -- {request_path}"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("cleanup timed out"), 1])
async def test_cleanup_failure_preserves_scoped_docs(
    failure: Exception | int, caplog: pytest.LogCaptureFixture
) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[FileUploadResponse(path="request")])
    backend.aexecute = AsyncMock(
        side_effect=[
            ExecuteResponse(exit_code=0, output='{"ui/AGENTS.md": "trusted"}'),
            failure
            if isinstance(failure, Exception)
            else ExecuteResponse(exit_code=failure, output="denied"),
        ]
    )
    with caplog.at_level(logging.WARNING, logger=agents_md.__name__):
        result = await agents_md.fetch_scoped_agents_md(backend, "/repo", "base", ["ui/app.py"])
    assert result == {"ui/AGENTS.md": "trusted"}
    assert "Failed to clean up scoped agent docs request" in caplog.text
