import os
import posixpath

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langsmith.sandbox import SandboxClientError
from tenki_sandbox import AsyncClient, AsyncSandbox
from tenki_sandbox.errors import (
    CommandTimeoutError,
    PermissionDeniedError,
    SandboxError,
)
from tenki_sandbox.errors import (
    FileNotFoundError as TenkiFileNotFoundError,
)

DEFAULT_COMMAND_TIMEOUT = 30 * 60
DEFAULT_START_TIMEOUT = 180


def validate_tenki_startup_config() -> None:
    if not (os.getenv("TENKI_API_KEY", "").strip() or os.getenv("TENKI_AUTH_TOKEN", "").strip()):
        raise ValueError("TENKI_API_KEY or TENKI_AUTH_TOKEN is required when SANDBOX_TYPE=tenki")
    if not os.getenv("TENKI_SANDBOX_PROJECT_ID", "").strip():
        raise ValueError("TENKI_SANDBOX_PROJECT_ID is required when SANDBOX_TYPE=tenki")


def _positive_timeout(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _combine_output(stdout: str, stderr: str) -> str:
    if not stderr:
        return stdout
    return f"{stdout}\n{stderr}" if stdout else stderr


class TenkiSandbox(BaseSandbox):
    def __init__(
        self,
        *,
        client: AsyncClient,
        sandbox: AsyncSandbox,
        command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> None:
        self._client = client
        self._sandbox = sandbox
        self._command_timeout = command_timeout
        self._github_token: str | None = None

    @property
    def id(self) -> str:
        return self._sandbox.id

    def set_github_token(self, token: str) -> None:
        self._github_token = token

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("TenkiSandbox is async-only; use aexecute")

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else self._command_timeout
        if effective_timeout < 0:
            raise ValueError(f"timeout must be non-negative, got {effective_timeout}")

        env = None
        if self._github_token:
            env = {"GH_TOKEN": self._github_token, "GIT_TOKEN": self._github_token}
            command = command.replace("GH_TOKEN=dummy", 'GH_TOKEN="${GH_TOKEN:-dummy}"')

        try:
            result = await self._sandbox.shell(
                command,
                env=env,
                timeout=effective_timeout,
            )
        except CommandTimeoutError:
            return ExecuteResponse(
                output=f"Command timed out after {effective_timeout} seconds",
                exit_code=124,
                truncated=False,
            )
        except SandboxError as exc:
            raise SandboxClientError(f"Tenki sandbox {self.id}: {exc}") from exc

        return ExecuteResponse(
            output=_combine_output(result.stdout_text, result.stderr_text),
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError("TenkiSandbox is async-only; use aupload_files")

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                await self._sandbox.fs.mkdir(posixpath.dirname(path), recursive=True)
                await self._sandbox.fs.write_bytes(path, content)
                responses.append(FileUploadResponse(path=path))
            except PermissionDeniedError:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            except TenkiFileNotFoundError:
                responses.append(FileUploadResponse(path=path, error="file_not_found"))
            except SandboxError as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError("TenkiSandbox is async-only; use adownload_files")

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not path.startswith("/"):
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            try:
                info = await self._sandbox.fs.stat(path)
                if info.is_dir:
                    responses.append(FileDownloadResponse(path=path, error="is_directory"))
                    continue
                content = await self._sandbox.fs.read_bytes(path)
                responses.append(FileDownloadResponse(path=path, content=content))
            except TenkiFileNotFoundError:
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
            except PermissionDeniedError:
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
            except SandboxError as exc:
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses

    async def aclose(self) -> None:
        try:
            await self._sandbox.close_if_open()
        finally:
            await self._client.close()


async def create_tenki_sandbox(sandbox_id: str | None = None) -> TenkiSandbox:
    validate_tenki_startup_config()
    start_timeout = _positive_timeout(
        "TENKI_SANDBOX_START_TIMEOUT_SECONDS",
        DEFAULT_START_TIMEOUT,
    )
    command_timeout = _positive_timeout(
        "TENKI_SANDBOX_COMMAND_TIMEOUT_SECONDS",
        DEFAULT_COMMAND_TIMEOUT,
    )
    client = AsyncClient()

    try:
        if sandbox_id:
            sandbox = await client.get(sandbox_id)
            if sandbox.state == "PAUSED":
                await sandbox.resume()
            if sandbox.state != "RUNNING":
                await sandbox.wait_ready(start_timeout)
        else:
            project_id = os.environ["TENKI_SANDBOX_PROJECT_ID"].strip()
            image = os.getenv("TENKI_SANDBOX_IMAGE", "").strip()
            sandbox = await client.create(
                timeout=start_timeout,
                project_id=project_id,
                image=image or None,
            )
    except Exception:
        await client.close()
        raise

    return TenkiSandbox(
        client=client,
        sandbox=sandbox,
        command_timeout=command_timeout,
    )
