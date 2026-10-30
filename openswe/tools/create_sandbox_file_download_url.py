import logging
import posixpath
import shlex
from datetime import datetime
from typing import Any, Literal

from langgraph.config import get_config

from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.run_config import RunConfig
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import get_async_sandbox_client
from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from openswe.short_links.models import ShortLink
from openswe.tools.access import direct_user_run
from openswe.users import User
from openswe.utils.dashboard_links import public_dashboard_api_base_url
from openswe.utils.json_types import as_json_object

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
    content_disposition: Literal["attachment", "inline"] = "attachment",
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
    url = download.download_url
    # Models copy a short link reliably, unlike hundreds of signed characters; only a public
    # deployment can serve one to the people and image proxies that follow it.
    if public_dashboard_api_base_url() is not None:
        cfg = RunConfig.from_runtime()
        # Only a person's own run is theirs; scheduled and other automatic runs write as the system.
        login = (
            await resolve_github_login(as_json_object(get_config()))
            if direct_user_run(cfg)
            else None
        )
        person = await User.for_login("github", login) if login else None
        link = await ShortLink.create(
            url,
            expires_at=(
                None if download.expires_at is None else datetime.fromisoformat(download.expires_at)
            ),
            created_by=person.id if person else None,
        )
        url = link.url
    return {"url": url, "file_path": path, "expires_at": download.expires_at}
