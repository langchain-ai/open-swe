import posixpath
import shlex
from typing import Any, Literal

from agent.run_config import RunConfig
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.providers.langsmith import get_async_sandbox_client
from agent.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend


async def _resolve_sandbox_file(file_path: str) -> tuple[Any, str, str]:
    if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
        raise ValueError("file_path must be a non-empty sandbox path")

    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    backend_proxy = await get_sandbox_backend(thread_id)
    work_dir = posixpath.normpath(await resolve_sandbox_work_dir(backend_proxy))
    path = posixpath.normpath(
        file_path.strip()
        if file_path.strip().startswith("/")
        else posixpath.join(work_dir, file_path.strip())
    )
    if posixpath.commonpath((work_dir, path)) != work_dir:
        raise ValueError(f"file_path must resolve within the sandbox work directory ({work_dir})")
    resolved = await backend_proxy.aexecute(f"realpath -- {shlex.quote(path)}")
    if resolved.exit_code != 0:
        raise ValueError("file_path must identify an existing sandbox file")
    path = posixpath.normpath(resolved.output.strip())
    if posixpath.commonpath((work_dir, path)) != work_dir:
        raise ValueError(f"file_path must resolve within the sandbox work directory ({work_dir})")
    return backend_proxy, path, work_dir


async def _resolve_download_artifact(file_path: str) -> tuple[Any, str]:
    if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
        raise ValueError("file_path must be a non-empty absolute path under /artifacts")

    path = posixpath.normpath(file_path.strip())
    artifacts_dir = "/artifacts"
    if (
        not file_path.strip().startswith("/")
        or posixpath.commonpath((artifacts_dir, path)) != artifacts_dir
    ):
        raise ValueError("file_path must resolve within /artifacts")

    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")
    backend_proxy = await get_sandbox_backend(thread_id)
    resolved = await backend_proxy.aexecute(f"realpath -- {shlex.quote(path)}")
    if resolved.exit_code != 0:
        raise ValueError("file_path must identify an existing sandbox file")
    path = posixpath.normpath(resolved.output.strip())
    if posixpath.commonpath((artifacts_dir, path)) != artifacts_dir:
        raise ValueError("file_path must resolve within /artifacts")
    return backend_proxy, path


async def create_sandbox_file_download_url(
    file_path: str,
    expires_in_seconds: int | None = None,
    content_type: str | None = None,
    content_disposition: Literal["attachment", "inline"] = "attachment",
) -> dict[str, Any]:
    """Create a bearer download URL for one file in the active LangSmith sandbox.

    Use this to share large binary artifacts such as videos, images, archives, or PDFs instead of
    pasting their contents into a response. The file must be placed under `/artifacts/` outside the
    sandbox work directory before calling this tool. Anyone with the URL can download the file, so
    never use it for secrets or credentials. Links do not expire by default; pass
    `expires_in_seconds` only when a link should stop working after a set time. Set
    `content_disposition` to `inline` and provide an appropriate `content_type` when the browser
    should preview an image, video, or PDF.
    """
    if expires_in_seconds is not None and expires_in_seconds < 1:
        raise ValueError("expires_in_seconds must be positive or null")
    if content_type is not None:
        content_type = content_type.strip()
        if not content_type or "\r" in content_type or "\n" in content_type:
            raise ValueError("content_type must be a valid non-empty media type")

    backend_proxy, path = await _resolve_download_artifact(file_path)
    backend = unwrap_sandbox_backend(backend_proxy)
    async with get_async_sandbox_client() as client:
        download = await client.generate_download_url(
            backend.id,
            path,
            expires_in_seconds=expires_in_seconds,
            content_type=content_type,
            content_disposition=content_disposition,
        )
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while creating the download URL; retry")

    if not download.download_url:
        raise RuntimeError("LangSmith did not return a download URL")
    return {
        "url": download.download_url,
        "file_path": path,
        "expires_at": download.expires_at,
    }
