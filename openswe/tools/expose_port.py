from openswe.run_config import RunConfig
from openswe.sandboxes.providers.langsmith import create_workspace_service_url
from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from openswe.utils.dashboard_links import dashboard_app_preview_url, dashboard_embed_origins


def validate_port(port: object) -> int:
    """Return ``port`` if it is a TCP port number, else raise."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port


async def _share_port(port: int) -> tuple[str, str]:
    """Share ``port`` of this run's sandbox with the workspace; return the thread id and URL."""
    port = validate_port(port)
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    backend_proxy = await get_sandbox_backend(thread_id)
    backend = unwrap_sandbox_backend(backend_proxy)
    url = await create_workspace_service_url(
        backend.id, port, embed_origins=dashboard_embed_origins()
    )
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while creating the service URL; retry")
    return thread_id, url


async def expose_port(port: int) -> dict[str, str | int]:
    """Implement the `expose_port` tool."""
    _, url = await _share_port(port)
    return {"url": url, "port": port}


async def preview_port(port: int) -> dict[str, str | int]:
    """Implement the `preview_port` tool."""
    thread_id, url = await _share_port(port)
    preview_url = dashboard_app_preview_url(thread_id, port)
    if preview_url is None:
        raise RuntimeError("no dashboard is configured for previews; use expose_port instead")
    return {"url": preview_url, "app_url": url, "port": port}
