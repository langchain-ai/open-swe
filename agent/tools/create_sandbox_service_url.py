from typing import Any

from agent.run_config import RunConfig
from agent.sandboxes.providers.langsmith import (
    create_login_service_url,
    service_identity_jwks_url,
)
from agent.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend


async def create_sandbox_service_url(port: int) -> dict[str, Any]:
    """Implement the `create_sandbox_service_url` tool."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")

    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")

    backend_proxy = await get_sandbox_backend(thread_id)
    backend = unwrap_sandbox_backend(backend_proxy)
    service = await create_login_service_url(backend.id, port)
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while creating the service URL; retry")

    return {
        "url": service.browser_url,
        "port": port,
        "access": service.access,
        "jwks_url": service_identity_jwks_url(),
    }
