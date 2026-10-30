import logging
import posixpath
import shlex
from typing import Any

from openswe.run_config import RunConfig
from openswe.sandboxes.file_links import LINKS_PATH, ContentDisposition, create_link
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import get_async_sandbox_client
from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from openswe.utils.dashboard_links import public_dashboard_api_base_url

logger = logging.getLogger(__name__)


async def resolve_sandbox_file(file_path: str) -> tuple[Any, str, str]:
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


async def create_sandbox_file_download_url(
    file_path: str,
    expires_in_seconds: int | None = None,
    content_type: str | None = None,
    content_disposition: ContentDisposition = "attachment",
) -> dict[str, Any]:
    """Implement the `create_sandbox_file_download_url` tool."""
    try:
        if expires_in_seconds is not None and expires_in_seconds < 1:
            raise ValueError("expires_in_seconds must be positive or null")
        if content_type is not None:
            content_type = content_type.strip()
            if not content_type or "\r" in content_type or "\n" in content_type:
                raise ValueError("content_type must be a valid non-empty media type")

        backend_proxy, path, _ = await resolve_sandbox_file(file_path)
    except ValueError as exc:
        logger.warning("Sandbox download request rejected", extra={"error": str(exc)})
        return {"error": str(exc)}
    backend = unwrap_sandbox_backend(backend_proxy)
    target = (backend.id, path, expires_in_seconds, content_type, content_disposition)
    url, expires_at = await _short_link(*target) or await _signed_url(*target)
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while creating the download URL; retry")
    return {"url": url, "file_path": path, "expires_at": expires_at}


async def _signed_url(
    sandbox_id: str,
    path: str,
    expires_in_seconds: int | None,
    content_type: str | None,
    content_disposition: ContentDisposition,
) -> tuple[str, str | None]:
    async with get_async_sandbox_client() as client:
        download = await client.generate_download_url(
            sandbox_id,
            path,
            expires_in_seconds=expires_in_seconds,
            content_type=content_type,
            content_disposition=content_disposition,
        )
    if not download.download_url:
        raise RuntimeError("LangSmith did not return a download URL")
    return download.download_url, download.expires_at


async def _short_link(
    sandbox_id: str,
    path: str,
    expires_in_seconds: int | None,
    content_type: str | None,
    content_disposition: ContentDisposition,
) -> tuple[str, str | None] | None:
    """A short Open SWE URL and its expiry, or None where only a signed URL can work.

    Models copy a short link reliably; a signed URL is hundreds of random characters.
    """
    base = public_dashboard_api_base_url()
    thread_id = RunConfig.from_runtime().thread_id
    if base is None or thread_id is None:
        return None
    link = await create_link(
        thread_id=thread_id,
        sandbox_id=sandbox_id,
        path=path,
        content_type=content_type,
        content_disposition=content_disposition,
        expires_in_seconds=expires_in_seconds,
    )
    if link is None:
        return None
    expires_at = None if link.expires_at is None else link.expires_at.isoformat()
    return f"{base}{LINKS_PATH}/{link.id}", expires_at
