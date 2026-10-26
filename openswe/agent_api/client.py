"""The Managed Deep Agents deployment that hosts every Open SWE graph.

``mda`` serves them as one graph; a run's context names the graph it
plays. Dispatch works out the run's catalog once as the run starts, keeps it
(``openswe.agent_api.catalogs``), and stamps a run token that names it into that
context; the deployment calls back with the token for the catalog, the graph's tools
and run hooks.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, NotRequired, TypedDict, get_args
from urllib.parse import urlsplit

from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import Catalog

from openswe.agent_api import catalogs
from openswe.agent_api.tokens import RemoteRun, runtime_tokens_configured, sign_runtime_token
from openswe.config import ENV
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

# The graph id `mda build` derives from the project's agent name.
MDA_GRAPH_ID: Final = "open-swe"
# The graph `mda build` adds for the agent's async general-purpose subagent, which
# async task workers run on, assignment and follow-ups alike.
MDA_WORKER_GRAPH_ID: Final = f"{MDA_GRAPH_ID}--general-purpose"
# Thread metadata naming the MDA graph a thread's runs go to, when not MDA_GRAPH_ID.
MDA_GRAPH_METADATA_KEY: Final = "mda_graph_id"
_MDA_GRAPHS: Final = frozenset({MDA_GRAPH_ID, MDA_WORKER_GRAPH_ID})
HostedGraph = Literal["agent", "reviewer", "review-scout"]
HOSTED_GRAPHS: Final[frozenset[str]] = frozenset(get_args(HostedGraph))
# Thread metadata naming where a thread's runs and checkpoints live.
RUNTIME_METADATA_KEY: Final = "runtime"
MDA_RUNTIME: Final = "mda"

_json_object = TypeAdapter(dict[str, JsonValue])
_hosted_graph = TypeAdapter(HostedGraph)


# The run context the MDA deployment builds its graph from. The token's key is a
# dunder so it stays out of the trace metadata the Agent Server mirrors context into.
# The Agent Server also copies the context into the run's config and its thread's,
# returned on every read of either, so the catalog stays out of it.
RemoteRunContext = TypedDict(
    "RemoteRunContext",
    {
        "graph": HostedGraph,
        "__run_token": str,
        "__catalog": None,
        "invocation_id": NotRequired[str],
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


def mda_graph_for(metadata: Mapping[str, JsonValue]) -> str:
    """The MDA graph a thread's runs go to."""
    graph = metadata.get(MDA_GRAPH_METADATA_KEY)
    return graph if isinstance(graph, str) and graph in _MDA_GRAPHS else MDA_GRAPH_ID


def mda_runtime_api_key() -> str | None:
    return ENV.MDA_RUNTIME_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()


@dataclass(frozen=True)
class StartedRun:
    """An MDA run as the backend starts it: who it is, and what its graph is built from."""

    graph: HostedGraph
    token: str
    # Granted to the run; None when working it out failed here, and the run then asks
    # for its catalog itself.
    catalog: Catalog | None
    invocation_id: str | None = None

    def context(self) -> RemoteRunContext:
        context: RemoteRunContext = {
            "graph": self.graph,
            "__run_token": self.token,
            # Runs that carried their catalog here left it in their thread's config, which
            # the Agent Server merges into every later run's; this replaces it there.
            "__catalog": None,
        }
        if self.invocation_id:
            context["invocation_id"] = self.invocation_id
        return context


async def start_remote_run(
    configurable: dict[str, JsonValue],
    *,
    metadata: dict[str, JsonValue],
    thread_id: str,
    assistant_id: str,
) -> StartedRun:
    """Work out the run's catalog and settings, once, keep the catalog, and sign a token
    that names it, before MDA starts the run.

    The configurable and metadata ride inside the signed token; the deployment calls
    back with the token rather than reading it.
    """
    if assistant_id not in HOSTED_GRAPHS:
        raise RemoteRuntimeConfigurationError(f"Managed Deep Agents does not host {assistant_id}")
    graph = _hosted_graph.validate_python(assistant_id)
    run = RemoteRun(
        thread_id=thread_id,
        assistant_id=assistant_id,
        configurable=_json_object.validate_python(configurable),
        metadata=_json_object.validate_python(metadata),
    ).granting()
    # The webapp must not import the agent stack at startup.
    from openswe.agent_api.hosted import hosted_graphs

    catalog: Catalog | None = None
    try:
        started = await hosted_graphs()[graph].start_run(run)
        digest = await catalogs.keep(started.catalog)
    except Exception:
        # The run still starts, and fails on its own catalog call if this keeps failing.
        logger.exception(
            "Starting an MDA run failed", extra={"thread_id": thread_id, "graph": graph}
        )
    else:
        # Signed, so every later call reads the settings and catalog the run started with.
        run = run.model_copy(update={"settings": started.settings, "catalog_digest": digest})
        catalog = run.grant_operations(started.catalog)
    return StartedRun(
        graph=graph,
        token=sign_runtime_token(run),
        catalog=catalog,
        invocation_id=RunConfig.parse(configurable).invocation_id,
    )


async def remote_run_context(
    configurable: dict[str, JsonValue],
    *,
    metadata: dict[str, JsonValue],
    thread_id: str,
    assistant_id: str,
) -> RemoteRunContext:
    """Everything an MDA run needs, since a run takes either context or configurable."""
    started = await start_remote_run(
        configurable, metadata=metadata, thread_id=thread_id, assistant_id=assistant_id
    )
    return started.context()
