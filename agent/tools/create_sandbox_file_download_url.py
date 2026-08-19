import posixpath
from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.config import get_config

from ..integrations.langsmith import get_async_sandbox_client
from ..utils.sandbox_paths import aresolve_sandbox_work_dir
from ..utils.sandbox_state import get_sandbox_backend, unwrap_sandbox_backend

_DEFAULT_EXPIRY_SECONDS = 24 * 60 * 60


async def _create_sandbox_file_download_url(
    file_path: str,
    expires_in_seconds: int | None = _DEFAULT_EXPIRY_SECONDS,
    content_type: str | None = None,
    content_disposition: Literal["attachment", "inline"] = "attachment",
) -> dict[str, Any]:
    """Create a bearer download URL for one file in the active sandbox."""
    if not isinstance(file_path, str) or not file_path.strip() or "\x00" in file_path:
        raise ValueError("file_path must be a non-empty sandbox path")
    if expires_in_seconds is not None and expires_in_seconds < 1:
        raise ValueError("expires_in_seconds must be positive or null")
    if content_type is not None:
        content_type = content_type.strip()
        if not content_type or "\r" in content_type or "\n" in content_type:
            raise ValueError("content_type must be a valid non-empty media type")

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    backend_proxy = await get_sandbox_backend(thread_id)
    work_dir = await aresolve_sandbox_work_dir(backend_proxy)
    path = posixpath.normpath(
        file_path.strip()
        if file_path.strip().startswith("/")
        else posixpath.join(work_dir, file_path.strip())
    )
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


create_sandbox_file_download_url = tool(
    "create_sandbox_file_download_url",
    description="""Create a bearer download URL for one file in the active LangSmith sandbox.

Use this to share large binary artifacts such as videos, images, archives, or PDFs instead of
pasting their contents into a response. Anyone with the URL can download the file, so never use it
for secrets or credentials. Links expire after 24 hours by default; pass null only when a
non-expiring link is deliberately required. Set `content_disposition` to `inline` and provide an
appropriate `content_type` when the browser should preview an image, video, or PDF.""",
)(_create_sandbox_file_download_url)
