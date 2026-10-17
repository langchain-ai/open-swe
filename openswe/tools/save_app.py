"""Save a web app running in the thread's sandbox to the triggering user's Apps page."""

from typing import Any

from langgraph.config import get_config
from pydantic import ValidationError

from openswe.apps.models import AppLaunchError, AppSpec, SandboxApp
from openswe.audit_logs.tools import audit_tool
from openswe.bridge.store import Bridge
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.run_config import RunConfig
from openswe.sandboxes.providers.langsmith import create_workspace_service_url
from openswe.sandboxes.state import get_sandbox_backend, unwrap_sandbox_backend
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_base_url
from openswe.utils.json_types import as_json_object


@audit_tool()
async def save_app(
    name: str,
    port: int,
    start_command: str,
    workdir: str,
    description: str = "",
) -> dict[str, Any]:
    """Implement the `save_app` tool."""
    try:
        spec = AppSpec(
            name=name,
            description=description,
            port=port,
            start_command=start_command,
            workdir=workdir,
        )
    except ValidationError as exc:
        return {"ok": False, "error": str(exc)}

    login = await resolve_github_login(as_json_object(get_config()))
    user = await User.for_login("github", login) if login else None
    if user is None:
        return {"ok": False, "error": "The triggering user has no Open SWE account"}

    thread_id = RunConfig.from_runtime().thread_id
    if not thread_id:
        return {"ok": False, "error": "no thread_id in run config"}
    backend_proxy = await get_sandbox_backend(thread_id)
    backend = unwrap_sandbox_backend(backend_proxy)
    if Bridge.bridge_id_of(backend.id) is not None:
        return {"ok": False, "error": "Apps can only be saved from a cloud sandbox"}

    try:
        await spec.start_in(backend)
    except AppLaunchError as exc:
        return {"ok": False, "error": str(exc), "log": exc.log}
    url = await create_workspace_service_url(backend.id, spec.port)
    if unwrap_sandbox_backend(backend_proxy) is not backend:
        raise RuntimeError("sandbox changed while saving the app; retry")

    app = await SandboxApp.save(user.id, spec, thread_id=thread_id, sandbox_id=backend.id, url=url)
    base = dashboard_base_url()
    return {
        "ok": True,
        "app_id": str(app.id),
        "name": app.name,
        "url": url,
        "apps_page": f"{base}/agents/apps" if base else None,
    }
