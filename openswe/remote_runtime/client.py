"""The Managed Deep Agents deployment that hosts the agent and reviewer graphs.

``mda`` serves both as one graph; a run's context names the graph it
plays. Dispatch stamps a signed run token into that context, and the deployment
calls back with it for the graph's tools and run hooks.
"""

from typing import Final, Literal, NotRequired, TypedDict
from urllib.parse import urlsplit

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from pydantic import JsonValue, TypeAdapter

from openswe.config import ENV
from openswe.remote_runtime.tokens import RemoteRun, runtime_tokens_configured, sign_runtime_token
from openswe.run_config import RunConfig

# The graph id `mda build` derives from the project's agent name.
MDA_GRAPH_ID: Final = "open-swe"
HostedGraph = Literal["agent", "reviewer"]
HOSTED_GRAPHS: Final[frozenset[str]] = frozenset({"agent", "reviewer"})
# Thread metadata naming where a thread's runs and checkpoints live.
RUNTIME_METADATA_KEY: Final = "runtime"
MDA_RUNTIME: Final = "mda"

_configurable = TypeAdapter(dict[str, JsonValue])


# The run context the MDA deployment builds its graph from. The token's key is a
# dunder so it stays out of the trace metadata the Agent Server mirrors context into.
RemoteRunContext = TypedDict(
    "RemoteRunContext",
    {
        "graph": HostedGraph,
        "__run_token": str,
        "invocation_id": NotRequired[str],
        "models": NotRequired[dict[str, JsonValue]],
    },
)


class RemoteRuntimeConfigurationError(RuntimeError):
    """A remote runtime is configured in a way this backend cannot safely call."""


def mda_runtime_url() -> str | None:
    url = ENV.MDA_RUNTIME_URL.optional()
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteRuntimeConfigurationError("MDA_RUNTIME_URL is not absolute")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RemoteRuntimeConfigurationError("MDA_RUNTIME_URL must use HTTPS")
    if not runtime_tokens_configured():
        raise RemoteRuntimeConfigurationError(
            "REMOTE_RUNTIME_TOKEN_SECRET is required to run graphs on Managed Deep Agents"
        )
    return url.rstrip("/")


def remote_runtime_client() -> LangGraphClient | None:
    """The client for the MDA deployment, or None when graphs run on this one."""
    url = mda_runtime_url()
    if url is None:
        return None
    return get_client(url=url, api_key=mda_runtime_api_key())


def mda_runtime_api_key() -> str | None:
    return ENV.MDA_RUNTIME_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()


def hosted_on_mda(assistant_id: str) -> bool:
    return assistant_id in HOSTED_GRAPHS and mda_runtime_url() is not None


async def remote_run_context(
    configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
) -> RemoteRunContext:
    """Everything an MDA run needs, since a run takes either context or configurable.

    The configurable rides inside the signed token; the deployment calls back with
    the token rather than reading it.
    """
    if assistant_id not in HOSTED_GRAPHS:
        raise RemoteRuntimeConfigurationError(f"Managed Deep Agents does not host {assistant_id}")
    graph: HostedGraph = "agent" if assistant_id == "agent" else "reviewer"
    token = sign_runtime_token(
        RemoteRun(
            thread_id=thread_id,
            assistant_id=assistant_id,
            configurable=_configurable.validate_python(configurable),
        )
    )
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime import agent, reviewer

    cfg = RunConfig.parse(configurable)
    models = await (agent.run_models(cfg) if graph == "agent" else reviewer.run_models(cfg))
    context: RemoteRunContext = {
        "graph": graph,
        "__run_token": token,
        "models": models.model_dump(mode="json"),
    }
    if cfg.invocation_id:
        context["invocation_id"] = cfg.invocation_id
    return context
