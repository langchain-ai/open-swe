from typing import Any
from urllib.parse import quote

from agent.run_config import RunConfig
from agent.sandboxes.providers.langsmith import get_async_sandbox_client
from agent.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from agent.utils.dashboard_links import dashboard_base_url
from agent.utils.dashboard_ui import serves_static_ui

_DIRECT_EXPIRES_IN_SECONDS = 86400


async def create_sandbox_service_url(port: int) -> dict[str, Any]:
    """Create a browser URL for a service listening in the active LangSmith sandbox.

    The service must listen on `0.0.0.0` at the specified port. The dashboard proxies the URL
    and attaches the sandbox credential itself, so the link is short, never expires, and only
    signed-in users who can read this thread can reach the service.

    The service is served under `base_path`, so anything it serves from a root-absolute URL
    (`/assets/app.js`, `/@vite/client`) is requested from the dashboard root and never reaches
    it. Start dev servers with that base path — `vite --base=<base_path>`, Next.js `basePath`,
    `ng build --base-href` — and their WebSockets and hot reload work through the proxy too.
    Static files and JSON APIs need no configuration.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")

    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    base_url = "" if serves_static_ui() else dashboard_base_url()
    if base_url:
        base_path = f"/sandbox/{quote(thread_id, safe='')}/{port}/"
        return {"url": f"{base_url}{base_path}", "port": port, "base_path": base_path}

    # No dashboard app in front of this backend to attach the token, so hand out
    # LangSmith's own token-bearing URL instead.
    backend_proxy = await get_sandbox_backend(thread_id)
    backend = unwrap_sandbox_backend(backend_proxy)
    async with get_async_sandbox_client() as client:
        service = await client.service(
            backend.id,
            port,
            expires_in_seconds=_DIRECT_EXPIRES_IN_SECONDS,
        )
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while creating the service URL; retry")
    if not service.browser_url:
        raise RuntimeError("LangSmith did not return a service URL")

    return {
        "url": service.browser_url,
        "port": port,
        "expires_at": service.expires_at,
    }
