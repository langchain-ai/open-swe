from typing import Any

from langgraph.config import get_config

from agent.integrations.langsmith import get_async_sandbox_client
from agent.run_config import RunConfig
from agent.utils.sandbox_state import get_sandbox_backend, unwrap_sandbox_backend


async def create_sandbox_service_url(
    port: int,
    expires_in_seconds: int = 600,
) -> dict[str, Any]:
    """Create a browser URL for a service listening in the active LangSmith sandbox.

    The service must listen on `0.0.0.0` at the specified port. Anyone with the URL can access it.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if (
        isinstance(expires_in_seconds, bool)
        or not isinstance(expires_in_seconds, int)
        or not 1 <= expires_in_seconds <= 86400
    ):
        raise ValueError("expires_in_seconds must be an integer between 1 and 86400")

    config = get_config()
    thread_id = RunConfig.from_config(config).thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    backend_proxy = await get_sandbox_backend(thread_id)
    backend = unwrap_sandbox_backend(backend_proxy)
    async with get_async_sandbox_client() as client:
        service = await client.service(
            backend.id,
            port,
            expires_in_seconds=expires_in_seconds,
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
