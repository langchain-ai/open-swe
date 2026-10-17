"""The Managed Deep Agents deployment that hosts every Open SWE graph.

``mda`` serves them as one graph; a run's context names the graph it
plays. Dispatch stamps a signed run token into that context, and the deployment
calls back with it for the graph's tools and run hooks.
"""

from typing import Final, Literal, NotRequired, TypedDict, get_args
from urllib.parse import urlsplit

from pydantic import JsonValue, TypeAdapter

from openswe.config import ENV
from openswe.remote_runtime.tokens import RemoteRun, runtime_tokens_configured, sign_runtime_token
from openswe.run_config import RunConfig

# The graph id `mda build` derives from the project's agent name.
MDA_GRAPH_ID: Final = "open-swe"
HostedGraph = Literal["agent", "reviewer", "review-scout"]
HOSTED_GRAPHS: Final[frozenset[str]] = frozenset(get_args(HostedGraph))
# Thread metadata naming where a thread's runs and checkpoints live.
RUNTIME_METADATA_KEY: Final = "runtime"
MDA_RUNTIME: Final = "mda"

_configurable = TypeAdapter(dict[str, JsonValue])
_hosted_graph = TypeAdapter(HostedGraph)


# The run context the MDA deployment builds its graph from. The token's and the
# configuration's keys are dunders so they stay out of the trace metadata the Agent
# Server mirrors context into.
RemoteRunContext = TypedDict(
    "RemoteRunContext",
    {
        "graph": HostedGraph,
        "__run_token": str,
        # The configuration the token signs a hash of, which every hook call echoes.
        "__run_config": str,
        "invocation_id": NotRequired[str],
        "models": NotRequired[dict[str, JsonValue]],
    },
)


class RemoteRuntimeConfigurationError(RuntimeError):
    """Managed Deep Agents is missing or configured in a way this backend cannot safely call."""


def mda_runtime_url() -> str:
    url = ENV.MDA_RUNTIME_URL.optional()
    if url is None:
        raise RemoteRuntimeConfigurationError(
            "MDA_RUNTIME_URL is required: Open SWE's graphs run on Managed Deep Agents"
        )
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


def validate_mda_startup_config() -> None:
    """Refuse to start without the Managed Deep Agents deployment every agent run needs."""
    mda_runtime_url()


def mda_runtime_api_key() -> str | None:
    return ENV.MDA_RUNTIME_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()


async def remote_run_context(
    configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
) -> RemoteRunContext:
    """Everything an MDA run needs, since a run takes either context or configurable.

    The configurable rides beside the token that signs its hash; the deployment
    calls back with both rather than reading either.
    """
    if assistant_id not in HOSTED_GRAPHS:
        raise RemoteRuntimeConfigurationError(f"Managed Deep Agents does not host {assistant_id}")
    graph = _hosted_graph.validate_python(assistant_id)
    run = RemoteRun(
        thread_id=thread_id,
        assistant_id=assistant_id,
        configurable=_configurable.validate_python(configurable),
    )
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime.hosted import hosted_graphs

    cfg = RunConfig.parse(configurable)
    models = await hosted_graphs()[graph].start_models(cfg)
    context: RemoteRunContext = {
        "graph": graph,
        "__run_token": sign_runtime_token(run),
        "__run_config": run.config_text(),
        "models": models.model_dump(mode="json"),
    }
    if cfg.invocation_id:
        context["invocation_id"] = cfg.invocation_id
    return context
