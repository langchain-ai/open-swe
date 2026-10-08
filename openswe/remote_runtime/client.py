"""Clients for graphs hosted in another deployment, and the run token dispatch stamps for them."""

from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from pydantic import JsonValue, TypeAdapter

from openswe.config import ENV
from openswe.remote_runtime.tokens import RemoteRun, runtime_tokens_configured, sign_runtime_token
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import SandboxCreateConfig

_configurable = TypeAdapter(dict[str, JsonValue])


class RemoteRunContext(TypedDict):
    """The run context a remote deployment builds its graph from."""

    run_token: str
    invocation_id: NotRequired[str]
    snapshot_id: NotRequired[str]


class RemoteRuntimeConfigurationError(RuntimeError):
    """A remote runtime is configured in a way this backend cannot safely call."""


def _remote_url(assistant_id: str) -> str | None:
    if assistant_id == "reviewer":
        return ENV.REVIEWER_RUNTIME_URL.optional()
    return None


def remote_runtime_client(assistant_id: str) -> LangGraphClient | None:
    """The client for the deployment hosting ``assistant_id``, or None to run it locally."""
    url = _remote_url(assistant_id)
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteRuntimeConfigurationError(f"The {assistant_id} runtime URL is not absolute")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RemoteRuntimeConfigurationError(f"The {assistant_id} runtime URL must use HTTPS")
    if not runtime_tokens_configured():
        raise RemoteRuntimeConfigurationError(
            "REMOTE_RUNTIME_TOKEN_SECRET is required to run graphs in another deployment"
        )
    return get_client(url=url.rstrip("/"), api_key=ENV.REVIEWER_RUNTIME_API_KEY.optional())


async def remote_run_context(
    configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
) -> RemoteRunContext:
    """Everything a remote run needs, since a run takes either context or configurable.

    The configurable rides inside the signed token; the remote deployment calls back
    with the token rather than reading it.
    """
    token = sign_runtime_token(
        RemoteRun(
            thread_id=thread_id,
            assistant_id=assistant_id,
            configurable=_configurable.validate_python(configurable),
        )
    )
    cfg = RunConfig.parse(configurable)
    sandbox = await SandboxCreateConfig.resolve(cfg.workspace_slug)
    context: RemoteRunContext = {"run_token": token}
    if cfg.invocation_id:
        context["invocation_id"] = cfg.invocation_id
    if sandbox.snapshot_id:
        context["snapshot_id"] = sandbox.snapshot_id
    return context
