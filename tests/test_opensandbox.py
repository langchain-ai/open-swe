"""Unit tests for the OpenSandbox integration."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from deepagents.backends.protocol import ExecuteResponse, WriteResult


# ---------------------------------------------------------------------------
# Helpers – fake OpenSandbox SDK objects
# ---------------------------------------------------------------------------

class _FakeOutputMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeLogs:
    def __init__(
        self,
        stdout: list[_FakeOutputMessage] | None = None,
        stderr: list[_FakeOutputMessage] | None = None,
    ) -> None:
        self.stdout = stdout or []
        self.stderr = stderr or []


class _FakeExecution:
    def __init__(
        self,
        exit_code: int = 0,
        logs: _FakeLogs | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.logs = logs or _FakeLogs()


class _FakeCommands:
    def __init__(self, execution: _FakeExecution | None = None) -> None:
        self._execution = execution or _FakeExecution()
        self.last_command: str | None = None
        self.last_opts = None

    def run(self, command: str, *, opts=None, handlers=None):
        self.last_command = command
        self.last_opts = opts
        return self._execution


class _FakeFiles:
    def __init__(self) -> None:
        self.written: dict[str, str | bytes] = {}
        self.files: dict[str, str] = {}

    def write_file(self, path: str, data, **kwargs) -> None:
        self.written[path] = data

    def read_file(self, path: str, **kwargs) -> str:
        if path in self.files:
            return self.files[path]
        raise FileNotFoundError(path)


class _FakeSandboxSync:
    def __init__(
        self,
        sandbox_id: str = "test-sandbox-123",
        commands: _FakeCommands | None = None,
        files: _FakeFiles | None = None,
    ) -> None:
        self.id = sandbox_id
        self.commands = commands or _FakeCommands()
        self.files = files or _FakeFiles()


# ---------------------------------------------------------------------------
# Tests – OpenSandboxBackend
# ---------------------------------------------------------------------------

class TestOpenSandboxBackend:
    def _make_backend(self, **kwargs):
        from agent.integrations.opensandbox import OpenSandboxBackend

        sandbox = _FakeSandboxSync(**kwargs)
        return OpenSandboxBackend(sandbox), sandbox

    def test_id_property(self):
        backend, _ = self._make_backend(sandbox_id="abc-123")
        assert backend.id == "abc-123"

    def test_execute_stdout_only(self):
        execution = _FakeExecution(
            exit_code=0,
            logs=_FakeLogs(stdout=[_FakeOutputMessage("hello world")]),
        )
        backend, sandbox = self._make_backend(commands=_FakeCommands(execution))

        result = backend.execute("echo hello world")

        assert isinstance(result, ExecuteResponse)
        assert result.output == "hello world"
        assert result.exit_code == 0
        assert result.truncated is False
        assert sandbox.commands.last_command == "echo hello world"

    def test_execute_stderr_only(self):
        execution = _FakeExecution(
            exit_code=1,
            logs=_FakeLogs(stderr=[_FakeOutputMessage("error occurred")]),
        )
        backend, _ = self._make_backend(commands=_FakeCommands(execution))

        result = backend.execute("bad-cmd")

        assert result.output == "error occurred"
        assert result.exit_code == 1

    def test_execute_combined_stdout_stderr(self):
        execution = _FakeExecution(
            exit_code=0,
            logs=_FakeLogs(
                stdout=[_FakeOutputMessage("out1"), _FakeOutputMessage("out2")],
                stderr=[_FakeOutputMessage("warn")],
            ),
        )
        backend, _ = self._make_backend(commands=_FakeCommands(execution))

        result = backend.execute("some-cmd")

        assert result.output == "out1out2\nwarn"

    def test_execute_empty_output(self):
        execution = _FakeExecution(exit_code=0, logs=_FakeLogs())
        backend, _ = self._make_backend(commands=_FakeCommands(execution))

        result = backend.execute("true")

        assert result.output == ""
        assert result.exit_code == 0

    def test_execute_timeout_forwarded(self):
        backend, sandbox = self._make_backend()

        backend.execute("sleep 10", timeout=30)

        assert sandbox.commands.last_opts is not None
        assert sandbox.commands.last_opts.timeout == timedelta(seconds=30)

    def test_execute_default_timeout(self):
        backend, sandbox = self._make_backend()

        backend.execute("ls")

        assert sandbox.commands.last_opts is not None
        assert sandbox.commands.last_opts.timeout == timedelta(seconds=300)  # 5 min default

    def test_write_success(self):
        backend, sandbox = self._make_backend()

        result = backend.write("/tmp/test.txt", "hello")

        assert isinstance(result, WriteResult)
        assert result.error is None
        assert result.path == "/tmp/test.txt"
        assert sandbox.files.written["/tmp/test.txt"] == "hello"

    def test_write_failure(self):
        files = _FakeFiles()
        files.write_file = MagicMock(side_effect=PermissionError("read-only"))
        backend, _ = self._make_backend(files=files)

        result = backend.write("/readonly/file.txt", "data")

        assert result.error is not None
        assert "read-only" in result.error

    def test_download_files_success(self):
        files = _FakeFiles()
        files.files = {"/app/a.txt": "content-a", "/app/b.txt": "content-b"}
        backend, _ = self._make_backend(files=files)

        responses = backend.download_files(["/app/a.txt", "/app/b.txt"])

        assert len(responses) == 2
        assert responses[0].content == b"content-a"
        assert responses[0].error is None
        assert responses[1].content == b"content-b"

    def test_download_files_not_found(self):
        backend, _ = self._make_backend()

        responses = backend.download_files(["/no/such/file.txt"])

        assert len(responses) == 1
        assert responses[0].content is None
        assert responses[0].error == "file_not_found"

    def test_upload_files_success(self):
        backend, sandbox = self._make_backend()

        responses = backend.upload_files([("/tmp/f.bin", b"binary-data")])

        assert len(responses) == 1
        assert responses[0].error is None
        assert sandbox.files.written["/tmp/f.bin"] == b"binary-data"

    def test_upload_files_failure(self):
        files = _FakeFiles()
        files.write_file = MagicMock(side_effect=OSError("disk full"))
        backend, _ = self._make_backend(files=files)

        responses = backend.upload_files([("/tmp/f.bin", b"data")])

        assert len(responses) == 1
        assert responses[0].error == "permission_denied"


# ---------------------------------------------------------------------------
# Tests – factory function
# ---------------------------------------------------------------------------

class TestCreateOpenSandboxSandbox:
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises(self):
        from agent.integrations.opensandbox import create_opensandbox_sandbox

        with pytest.raises(ValueError, match="OPEN_SANDBOX_API_KEY"):
            create_opensandbox_sandbox()

    @patch("agent.integrations.opensandbox.SandboxSync")
    @patch.dict(
        "os.environ",
        {
            "OPEN_SANDBOX_API_KEY": "test-key",
            "OPEN_SANDBOX_DOMAIN": "sandbox.example.com",
        },
    )
    def test_create_new_sandbox(self, mock_sandbox_cls):
        from agent.integrations.opensandbox import create_opensandbox_sandbox

        fake = _FakeSandboxSync()
        mock_sandbox_cls.create.return_value = fake

        backend = create_opensandbox_sandbox(sandbox_id=None)

        mock_sandbox_cls.create.assert_called_once()
        assert backend.id == fake.id

    @patch("agent.integrations.opensandbox.SandboxSync")
    @patch.dict(
        "os.environ",
        {
            "OPEN_SANDBOX_API_KEY": "test-key",
            "OPEN_SANDBOX_DOMAIN": "sandbox.example.com",
        },
    )
    def test_reconnect_existing_sandbox(self, mock_sandbox_cls):
        from agent.integrations.opensandbox import create_opensandbox_sandbox

        fake = _FakeSandboxSync(sandbox_id="existing-456")
        mock_sandbox_cls.connect.return_value = fake

        backend = create_opensandbox_sandbox(sandbox_id="existing-456")

        mock_sandbox_cls.connect.assert_called_once()
        assert backend.id == "existing-456"
        mock_sandbox_cls.create.assert_not_called()
