"""Managed runtime seam for compiled Managed Deep Agents.

This module is consumed only by the entry module that ``mda deploy`` generates —
agent authors never import it directly. It is the point where MDA injects the
platform-owned backend (the configured sandbox), Context Hub-backed deployed
context, StateBackend scratch fallback, and managed memory/skills.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import math
import os
import posixpath
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict, TypeVar, cast
from urllib.parse import quote, urlsplit

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    ContextHubBackend,
    FilesystemBackend,
    LangSmithSandbox,
    StateBackend,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.middleware.async_subagents import _NEST_UNDER_PARENT
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph as CompiledAgent
from langgraph_sdk import get_client
from langsmith import tracing_context
from langsmith.run_trees import RunTree
from langsmith.utils import tracing_is_enabled

from managed_deepagents._async_subagents import (
    GENERAL_PURPOSE,
    SUBAGENT_SKILLS_KEY,
    async_subagent_definition,
    reject_async_subagents,
    route_async_subagents,
    with_async_general_purpose,
)
from managed_deepagents._channel import (
    Channel,
    collect_channels,
    requirements_from_channels,
)
from managed_deepagents._channels.binding import resolve_channel_provider
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._connector import (
    Connector,
    SandboxMode,
    collect_connectors,
    has_sandbox_connectors,
    load_connector_tools,
    requirements_from_connectors,
    run_connector_sandbox_hooks,
)
from managed_deepagents._eval_filesystem import create_eval_trial_filesystem_backend
from managed_deepagents._identity_auth import build_managed_auth
from managed_deepagents._identity_http import build_managed_http_app
from managed_deepagents._identity_runtime import (
    _field,
    build_runtime_identity,
    resolve_runtime_configurable,
)
from managed_deepagents._log import logger
from managed_deepagents._managed_composition import (
    assert_dynamic_memory_selection,
    snapshot_browser_selection,
    snapshot_managed_composition,
    snapshot_mcp_selection,
    snapshot_memory_selection,
    snapshot_sandbox_selection,
)
from managed_deepagents._managed_middleware import wrap_middleware_with_managed_runtime
from managed_deepagents._managed_tools import (
    ManagedServerRuntime,
    _ManagedExecutionRuntime,
    _ManagedReadRuntime,
    adapt_server_runtime,
    wrap_tools_with_managed_runtime,
)
from managed_deepagents._memory import (
    ManagedCompositeBackend as _ManagedCompositeBackend,
)
from managed_deepagents._memory import (
    managed_context_backend as _managed_context_backend,
)
from managed_deepagents._memory import (
    managed_context_permissions as _managed_context_permissions,
)
from managed_deepagents._memory import (
    managed_selected_skills_backend as _managed_selected_skills_backend,
)
from managed_deepagents._memory.async_util import await_if_needed as _await_if_needed
from managed_deepagents._memory.context_io import (
    download_context_file as _download_context_file,
)
from managed_deepagents._memory.deps import (
    optional_runtime_symbols as _optional_runtime_symbols,
)
from managed_deepagents._memory.layers import (
    MemoryLayers,
    has_agent_memory_layer,
    user_memory_layer,
)
from managed_deepagents._memory.policy import (
    UserMemoryAccess,
    resolve_agent_memory_access,
    resolve_user_memory_access,
)
from managed_deepagents._memory.selected_skills import (
    SelectedSkillsBackend,
    resolve_selected_skills,
)
from managed_deepagents._memory.user_memory import UserMemoryBackend
from managed_deepagents._telemetry.metadata import detect_run_identity
from managed_deepagents._telemetry.run import (
    MemoryObservations,
    RunChannel,
    RunConfiguration,
    start_run,
)
from managed_deepagents._telemetry.run_wrapper import observe_run
from managed_deepagents.agent_entry import SharedAgentFactory
from managed_deepagents.define_deep_agent import DeepAgentDefinition
from managed_deepagents.run_context import ManagedChannelContext, ManagedRunContext


class _Omitted:
    __slots__ = ()


_OMITTED = _Omitted()

_ResultT = TypeVar("_ResultT")

if TYPE_CHECKING:
    from deepagents import SubAgent
    from langchain.agents.middleware import AgentMiddleware
    from langgraph_sdk.runtime import ServerRuntime
    from langsmith.sandbox import Sandbox
    from langsmith.sandbox import SandboxClient as LangSmithSandboxClient
    from pydantic import BaseModel

    from managed_deepagents._managed_tools import RuntimeBackendFactory
    from managed_deepagents._sandbox_proxy import SandboxProxyConfigSync
    from managed_deepagents.agent_entry import AgentEntry
    from managed_deepagents.define_browser import BrowserDefinition
    from managed_deepagents.define_deep_agent import DeepAgentSubAgent, DeepAgentTool
    from managed_deepagents.define_memory import MemoryDefinition, MemoryDefinitionLike
    from managed_deepagents.identity import (
        IdentityDefinitionLike,
    )
    from managed_deepagents.sandbox import SandboxDefinition, SandboxReplacement


class ManagedRunConfig(RunnableConfig, total=False):
    """LangGraph runnable config used to scope the managed sandbox.

    Mirrors TypeScript ``ManagedRunConfig``: the native ``RunnableConfig`` plus
    the Agent Server ``context`` read by the managed runtime at invocation time.
    """

    context: object


__all__ = [
    "MANAGED_SKILLS_PATH",
    "RECIPE_SNAPSHOT_ENV",
    "build_managed_auth",
    "build_managed_http_app",
    "collect_channels",
    "collect_connectors",
    "compile_agent_factory_for_eval_async",
    "compile_managed_agent",
    "compile_managed_agent_async",
    "compile_managed_agent_for_eval",
    "has_sandbox_connectors",
    "load_connector_tools",
    "parent_run",
    "requirements_from_channels",
    "requirements_from_connectors",
    "run_connector_sandbox_hooks",
]


def _snapshot_factory_definition(selected: DeepAgentDefinition) -> DeepAgentDefinition:
    """Validate and copy a definition returned across the callback boundary."""
    try:
        config = dict(selected.config)
    except Exception:  # noqa: BLE001 - returned config is an untrusted callback value
        raise TypeError("agent factory returned an invalid definition") from None
    name = config.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TypeError("agent definition must have a non-empty name")
    forbidden = {"backend", "store", "checkpointer", "system_prompt", "disable_memory"}
    if forbidden.intersection(config):
        raise ValueError("agent factory returned a managed runtime field")
    try:
        snapshot = snapshot_managed_composition(config)
    except Exception:  # noqa: BLE001 - returned config is an untrusted callback value
        raise TypeError("agent factory returned an invalid definition") from None
    selected_memory = snapshot.get("memory")
    if selected_memory is not None:
        assert_dynamic_memory_selection(cast("MemoryDefinition", selected_memory))
    return DeepAgentDefinition(snapshot)


async def resolve_agent_definition(
    entry: AgentEntry, runtime: ManagedServerRuntime
) -> DeepAgentDefinition:
    """Resolve one managed definition without exposing callback data in errors."""
    if isinstance(entry, DeepAgentDefinition):
        return entry
    if not callable(entry):
        raise TypeError("agent export must be a managed agent definition or factory entrypoint")

    try:
        selected = entry(runtime)
        if inspect.isawaitable(selected):
            selected = await selected
    except Exception:  # noqa: BLE001 - callback failures cross an untrusted boundary
        raise RuntimeError("agent factory failed") from None

    if not isinstance(selected, DeepAgentDefinition):
        raise TypeError("agent factory must return define_deep_agent(...)")
    return _snapshot_factory_definition(selected)


# Core framework packages are required for native authoring types. Optional
# provider integrations (for example, MCP and sandbox clients) remain lazy and
# are required only when a runtime path uses them.

#: Backend path the runtime mounts the project's skills at.
#:
#: Local/deploy route this to the Context Hub mock or repo; an eval trial resolves
#: it against the trial workspace, where the Harbor adapter has copied the
#: compiled project (so it reads the project's own ``skills/`` tree).
MANAGED_SKILLS_PATH = "/skills/"

# Keep in sync with MANAGED_DEEP_AGENT_INSTRUCTIONS in the TypeScript runtime.
MANAGED_DEEP_AGENT_INSTRUCTIONS = (
    "<managed_deep_agent>\n"
    "You are a Managed Deep Agent (MDA), built on LangChain's Deep Agents harness.\n"
    "</managed_deep_agent>"
)

#: Deployment secret naming the recipe snapshot baked at ``mda deploy``.
#: Mirrors TypeScript ``RECIPE_SNAPSHOT_ENV``.
RECIPE_SNAPSHOT_ENV = "MDA_SANDBOX_RECIPE_SNAPSHOT"

#: Full sandbox API endpoint resolved and owned by ``mda deploy``.
#: Mirrors TypeScript ``SANDBOX_API_ENDPOINT_ENV``.
SANDBOX_API_ENDPOINT_ENV = "MDA_SANDBOX_API_ENDPOINT"

#: Env var naming the workspace the CLI baked the recipe snapshot into.
#: ``mda deploy`` sets it on the deployment and ``mda dev`` stages it into the
#: local ``.env``. Sandboxes and snapshots are workspace-scoped, so the runtime
#: must send it too — an API key whose default workspace differs from this one
#: resolves ``snapshot_name`` in the wrong workspace and 404s on the recipe.
#: Mirrors TypeScript ``WORKSPACE_ID_ENV``.
WORKSPACE_ID_ENV = "LANGSMITH_WORKSPACE_ID"

#: Header the LangSmith APIs use to scope a request to one workspace.
_TENANT_HEADER = "X-Tenant-Id"

#: Author bake-base keys — deploy-time only; stripped from runtime create.
_BAKE_BASE_OPTION_KEYS = frozenset({"snapshot_name", "snapshot_id", "docker_image", "registry"})

#: Separator between the deployment name and the identity digest in a managed
#: sandbox name. Doubled so one deployment's prefix can never match another's
#: name: ``agent--<digest>`` is not matched by the ``agent-staging--`` prefix.
#: Mirrors the ``--u--`` / ``--t--`` convention used for Context Hub child repos,
#: and must stay in sync with ``SANDBOX_NAME_SEPARATOR`` in the CLI.
_SANDBOX_NAME_SEPARATOR = "--"

#: Hex characters of the identity digest kept in a sandbox name.
_SANDBOX_DIGEST_LENGTH = 12

#: Host sandbox-name limit.
_MAX_SANDBOX_NAME_LENGTH = 63
_SANDBOX_FALLBACK_SUFFIX_LENGTH = 9
_MAX_PRIMARY_DEPLOYMENT_COMPONENT_LENGTH = (
    _MAX_SANDBOX_NAME_LENGTH - len(_SANDBOX_NAME_SEPARATOR) - _SANDBOX_DIGEST_LENGTH
)
_MAX_FALLBACK_DEPLOYMENT_COMPONENT_LENGTH = (
    _MAX_PRIMARY_DEPLOYMENT_COMPONENT_LENGTH - _SANDBOX_FALLBACK_SUFFIX_LENGTH
)
_SANDBOX_DEPLOYMENT_DIGEST_LENGTH = 8

#: Sandbox statuses that can be adopted as-is.
_ADOPTABLE_SANDBOX_STATUS = frozenset({"ready", "running"})

#: Sandbox status for one that is stopped but restartable.
_STOPPED_SANDBOX_STATUS = "stopped"

#: Create failures that mean the sandbox name is already taken.
_SANDBOX_NAME_CONFLICT_RE = re.compile(
    r"409|conflict|already exists|already in use|duplicate", re.IGNORECASE
)


class _ManagedSandboxEntry:
    """Cached sandbox plus per-thread connector sandbox sync state."""

    __slots__ = ("backend", "host_name", "sandbox_id", "synced_threads")

    def __init__(
        self,
        backend: SandboxBackendProtocol,
        host_name: str | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        self.backend = backend
        self.synced_threads: set[str] = set()
        #: LangSmith box name for this entry (deterministic or unique-suffixed).
        self.host_name = host_name
        self.sandbox_id = sandbox_id


#: Created sandboxes, keyed by thread and any explicit dynamic selection,
#: reused for the lifetime of this runtime process. Storing the entry dedupes
#: concurrent runs. Provisioning (``setup.sh``) happens at deploy bake, not on
#: thread create.
_managed_sandboxes: dict[str, _ManagedSandboxEntry] = {}
#: Host box names that must not be re-adopted after invalidate until a fresh
#: create claims them (or a unique suffix replaces them). Covers delete lag.
_invalidated_sandbox_names: set[str] = set()
#: Guards the cache / per-key lock maps only — never held across cold create.
_managed_sandbox_lock = threading.Lock()
#: One lock per scope key so concurrent resolves for the *same* sandbox share a
#: single provision, while different keys stay parallel (Host workers must not
#: block each other on unrelated cold boxes).
_managed_sandbox_key_locks: dict[str, threading.Lock] = {}


def _sandbox_key_lock(key: str) -> threading.Lock:
    """Return the per-scope lock, creating it on first use."""
    with _managed_sandbox_lock:
        lock = _managed_sandbox_key_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _managed_sandbox_key_locks[key] = lock
        return lock


def _peek_managed_host_name(key: str) -> str | None:
    """Host box name currently cached for ``key``, if any."""
    with _managed_sandbox_lock:
        entry = _managed_sandboxes.get(key)
        return entry.host_name if entry is not None else None


def _bake_base(sandbox: SandboxDefinition | None) -> tuple[str, str] | None:
    """The ``(option, value)`` bake base a sandbox sets, if any."""
    if sandbox is None:
        return None
    for key in ("snapshot_id", "snapshot_name", "docker_image"):
        value = _string_option(sandbox.options.get(key))
        if value:
            return key, value
    return None


def _inherit_setup_recipe(
    selected: SandboxDefinition,
    declared: SandboxDefinition | None,
) -> SandboxDefinition:
    """Keep a factory's sandbox selection on the ``setup.sh`` recipe snapshot.

    The recipe is baked on the base declared in ``sandbox/__init__.py``, or on
    Host's default image when none is declared. A selection without a base, or
    with that same base, clones the recipe. Any other base was never baked, so
    the run fails instead of starting a sandbox without ``setup.sh``.
    """
    if not _string_option(os.environ.get(RECIPE_SNAPSHOT_ENV)):
        return selected
    base = _bake_base(selected)
    if base is None:
        return selected
    declared_base = _bake_base(declared)
    if base == declared_base:
        from managed_deepagents.sandbox import SandboxDefinition  # noqa: PLC0415

        return SandboxDefinition(
            {
                key: value
                for key, value in selected.options.items()
                if key not in _BAKE_BASE_OPTION_KEYS
            }
        )
    baked_on = (
        f"{declared_base[0]} {json.dumps(declared_base[1])}"
        if declared_base
        else "the default sandbox image"
    )
    raise TypeError(
        f"sandbox/setup.sh is baked on {baked_on}, but this run selected "
        f"{base[0]} {json.dumps(base[1])}. Remove {base[0]} from the selected sandbox, "
        "or declare the same base in sandbox/__init__.py."
    )


def _sandbox_create_kwargs(
    sandbox: SandboxDefinition,
    selection_fingerprint: str | None = None,
) -> dict[str, object]:
    """Options for ``create_sandbox``."""
    from managed_deepagents.sandbox import SandboxDefinition  # noqa: PLC0415

    create_kwargs = {
        key: value
        for key, value in sandbox.options.items()
        if key
        not in {
            "scope",
            "api_key",
            "default_timeout",
            "template_name",
            *_BAKE_BASE_OPTION_KEYS,
            *SandboxDefinition.RESOLUTION_OPTION_KEYS,
        }
    }
    from managed_deepagents._sandbox_proxy import static_sandbox_proxy_config  # noqa: PLC0415

    if "proxy_config" in create_kwargs:
        create_kwargs["proxy_config"] = static_sandbox_proxy_config(create_kwargs["proxy_config"])
    recipe = _string_option(os.environ.get(RECIPE_SNAPSHOT_ENV))
    snapshot_id = sandbox.options.get("snapshot_id")
    snapshot_name = sandbox.options.get("snapshot_name")
    if selection_fingerprint and snapshot_id:
        create_kwargs["snapshot_id"] = snapshot_id
    elif selection_fingerprint and snapshot_name:
        create_kwargs["snapshot_name"] = snapshot_name
    elif recipe:
        create_kwargs["snapshot_name"] = recipe
    return create_kwargs


class _SandboxClientKwargs(TypedDict, total=False):
    """Subset of ``SandboxClient`` constructor keywords the runtime pins."""

    api_endpoint: str
    api_key: str
    headers: dict[str, str]


def _sandbox_client_kwargs(sandbox: SandboxDefinition) -> _SandboxClientKwargs:
    """Client kwargs pinning the API key and the workspace holding the recipe."""
    kwargs: _SandboxClientKwargs = {}
    api_endpoint = _configured_sandbox_api_endpoint()
    if api_endpoint:
        kwargs["api_endpoint"] = api_endpoint
    api_key = _string_option(sandbox.options.get("api_key")) or _string_option(
        os.environ.get("LANGSMITH_API_KEY")
    )
    if api_key:
        kwargs["api_key"] = api_key
    workspace_id = _string_option(os.environ.get(WORKSPACE_ID_ENV))
    if workspace_id:
        kwargs["headers"] = {_TENANT_HEADER: workspace_id}
    return kwargs


def _configured_sandbox_api_endpoint() -> str | None:
    """Return the validated deploy-owned sandbox endpoint, when configured."""
    raw = _string_option(os.environ.get(SANDBOX_API_ENDPOINT_ENV))
    if raw is None:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
    except ValueError:
        parsed = None
        hostname = None
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"{SANDBOX_API_ENDPOINT_ENV} must be an HTTP(S) URL without credentials, "
            "query, or fragment"
        )
    return raw.rstrip("/")


def _sandbox_client_for(sandbox: SandboxDefinition) -> LangSmithSandboxClient:
    """Build a LangSmith sandbox client using the definition's API key."""
    # Loaded lazily through the optional-provider boundary below.
    sandbox_client_cls = cast("type[LangSmithSandboxClient] | None", SandboxClient)
    if sandbox_client_cls is None:  # pragma: no cover - depends on runtime deps
        raise RuntimeError("MDA requires `langsmith` with sandbox support to create sandboxes.")
    return sandbox_client_cls(**_sandbox_client_kwargs(sandbox))


def _sandbox_name_blocked(name: str) -> bool:
    with _managed_sandbox_lock:
        return name in _invalidated_sandbox_names


def _unblock_sandbox_name(name: str) -> None:
    with _managed_sandbox_lock:
        _invalidated_sandbox_names.discard(name)


def _sandbox_handle_name(handle: object) -> str | None:
    """Read ``name`` from an SDK sandbox handle (object or mapping)."""
    name = _string_option(getattr(handle, "name", None))
    if name is not None:
        return name
    if isinstance(handle, Mapping):
        return _string_option(handle.get("name"))
    return None


def _sandbox_handle_id(handle: object) -> str | None:
    """Read ``id`` from an SDK sandbox handle (object or mapping)."""
    value = getattr(handle, "id", None)
    if value is None and isinstance(handle, Mapping):
        value = handle.get("id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


(SandboxClient,) = _optional_runtime_symbols("langsmith.sandbox", "SandboxClient")


class _ManagedCompileOptions(TypedDict):
    context_root: str | None
    context_repo: str | None
    system_prompt: str | None
    has_skills: bool
    sandbox: SandboxDefinition | None
    sandbox_selection_fingerprint: str | None
    connectors: list[Connector] | None
    channels: list[Channel] | None
    identity: IdentityDefinitionLike | None
    memory: MemoryDefinitionLike | None
    browser: BrowserDefinition | None
    trial_root_dir: str | None


def _internal_server_runtime(
    config: ManagedRunConfig | None,
    context: object,
    is_run: bool,
) -> ManagedServerRuntime:
    """Create an SDK-compatible runtime for direct internal/eval callers."""
    # The SDK exposes no public constructor for internal/eval calls.
    from langgraph.store.base import BaseStore  # noqa: PLC0415 - runtime dependency
    from langgraph.store.memory import InMemoryStore  # noqa: PLC0415 - runtime dependency
    from langgraph_sdk.auth.types import BaseUser  # noqa: PLC0415

    configurable = resolve_runtime_configurable(config)
    candidate = _field(configurable, "langgraph_auth_user")
    user = candidate if isinstance(candidate, BaseUser) else None
    candidate_store = _field(configurable, "__pregel_store")
    store = candidate_store if isinstance(candidate_store, BaseStore) else InMemoryStore()
    if is_run:
        return _ManagedExecutionRuntime(
            access_context="threads.create_run",
            user=user,
            store=store,
            context=cast("dict[str, object]", context if context is not None else {}),
        )
    return _ManagedReadRuntime(access_context="assistants.read", user=user, store=store)


def compile_managed_agent(
    entry: AgentEntry,
    config: ManagedRunConfig | None = None,
    *,
    context_root: str | None = None,
    context_repo: str | None = None,
    system_prompt: str | None = None,
    has_skills: bool = False,
    sandbox: SandboxDefinition | None = None,
    sandbox_setup: bool = False,
    connectors: list[Connector] | None = None,
    channels: list[Channel] | None = None,
    identity: IdentityDefinitionLike | None = None,
    memory: MemoryDefinitionLike | None = None,
    browser: BrowserDefinition | None = None,
    trial_root_dir: str | None = None,
    runtime: ServerRuntime[ManagedRunContext] | None = None,
    run_context: object = None,
    is_run: bool = True,
    report_telemetry: bool = True,
) -> CompiledAgent:
    """Compile one run; direct callers create a fresh graph for each execution."""
    return _await_if_needed(
        compile_managed_agent_async(
            entry,
            config,
            context_root=context_root,
            context_repo=context_repo,
            system_prompt=system_prompt,
            has_skills=has_skills,
            sandbox=sandbox,
            sandbox_setup=sandbox_setup,
            connectors=connectors,
            channels=channels,
            identity=identity,
            memory=memory,
            browser=browser,
            trial_root_dir=trial_root_dir,
            runtime=runtime,
            run_context=run_context,
            is_run=is_run,
            report_telemetry=report_telemetry,
        )
    )


@asynccontextmanager
async def parent_run(config: Mapping[str, object]) -> AsyncIterator[None]:
    """Set up a run for async subagents: verify its lead, then nest its trace.

    The generated entry holds this open for the whole run. For a run that
    deepagents launched from another run, the Agent Server is asked once whether
    this run's caller may access the launching (lead) thread, under MDA's usual
    thread ownership. Only then may the run share the lead's sandbox, and read
    the lead's thread record as its own ``runtime.thread``.
    """
    from managed_deepagents._managed_thread import _verified_lead_metadata  # noqa: PLC0415

    lead = await _accessible_lead_thread(config)
    token = _VERIFIED_LEAD_THREAD.set(lead[0] if lead is not None else None)
    metadata_token = _verified_lead_metadata.set(lead)
    try:
        with _parent_trace(config):
            yield
    finally:
        # The Agent Server enters and exits the entry in one task; tolerate otherwise.
        with suppress(ValueError):
            _verified_lead_metadata.reset(metadata_token)
        with suppress(ValueError):
            _VERIFIED_LEAD_THREAD.reset(token)


@contextmanager
def _parent_trace(config: Mapping[str, object]) -> Iterator[None]:
    """Nest this run's trace under the async subagent call that launched it.

    Only acts on runs deepagents marked as launched by an async subagent tool,
    and only when tracing is on.
    """
    configurable = config.get("configurable")
    parent = _launching_parent(configurable) if isinstance(configurable, Mapping) else None
    if parent is None or not tracing_is_enabled():
        yield
        return
    with tracing_context(parent=parent):
        yield


def _launching_parent(configurable: Mapping[str, object]) -> RunTree | None:
    """Rebuild the launching span from the trace headers the Agent Server kept, if marked."""
    trace = configurable.get("langsmith-trace")
    metadata = configurable.get("langsmith-metadata")
    if not isinstance(trace, str) or not trace or not isinstance(metadata, Mapping):
        return None
    if metadata.get(_NEST_UNDER_PARENT) is not True:
        return None
    project = configurable.get("langsmith-project")
    baggage = f"langsmith-project={quote(project)}" if isinstance(project, str) else ""
    try:
        return RunTree.from_headers({"langsmith-trace": trace, "baggage": baggage})
    except ValueError:
        logger.warning("Ignoring a malformed langsmith-trace; the run keeps its own trace")
        return None


async def _accessible_lead_thread(
    config: Mapping[str, object],
) -> tuple[str, Mapping[str, object]] | None:
    """Return the lead's thread and its metadata when this run's caller owns it, else ``None``.

    Reads the thread in process under the caller's identity, then requires its
    ``owner`` to be the caller's owner key, MDA's rule for changing a thread.
    Being able to read it isn't enough: Studio callers can read every thread.
    A thread without an owner (no managed identity) only needs to be readable.
    """
    lead_thread = _lead_thread(config)
    if lead_thread is None:
        return None
    try:
        thread = await get_client().threads.get(lead_thread)
    except Exception:  # noqa: BLE001 — missing or not allowed: keep this run's own sandbox
        logger.warning("lead thread not accessible to this run; using its own sandbox")
        return None
    metadata = thread.get("metadata") or {}
    owner = metadata.get("owner")
    if owner is not None and owner != _caller_owner_key(config):
        logger.warning("lead thread belongs to another caller; using this run's own sandbox")
        return None
    return lead_thread, metadata


def _caller_owner_key(config: Mapping[str, object]) -> str | None:
    """The thread owner key MDA stamps for this run's caller, if it's authenticated."""
    from managed_deepagents._identity_auth import _thread_owner_key  # noqa: PLC0415

    configurable = resolve_runtime_configurable(config)
    user = configurable.get("langgraph_auth_user") if configurable is not None else None
    return _thread_owner_key(user) if user is not None else None


async def compile_managed_agent_async(
    entry: AgentEntry,
    config: ManagedRunConfig | None = None,
    *,
    context_root: str | None = None,
    context_repo: str | None = None,
    system_prompt: str | None = None,
    has_skills: bool = False,
    sandbox: SandboxDefinition | None = None,
    sandbox_setup: bool = False,
    connectors: list[Connector] | None = None,
    channels: list[Channel] | None = None,
    identity: IdentityDefinitionLike | None = None,
    memory: MemoryDefinitionLike | None = None,
    browser: BrowserDefinition | None = None,
    trial_root_dir: str | None = None,
    runtime: ServerRuntime[ManagedRunContext] | None = None,
    run_context: object = None,
    is_run: bool = True,
    report_telemetry: bool = True,
    async_subagent_graphs: Mapping[str, str] | None = None,
    async_subagent: str | None = None,
) -> CompiledAgent:
    """Resolve one entry and compile it with the current managed run context.

    Generated entries pass `async_subagent_graphs` to the lead, and
    `async_subagent` to build that async subagent's own graph instead.
    """
    if runtime is not None:
        execution = runtime.execution_runtime
        is_run = execution is not None
        run_context = execution.context if execution is not None else None
    factory_runtime = (
        runtime if runtime is not None else _internal_server_runtime(config, run_context, is_run)
    )
    from managed_deepagents._run_input import execution_context, run_thread  # noqa: PLC0415

    register_channel_messaging(channels or [])
    binding, authored_context, _ = (
        execution_context(run_context, config, None) if is_run else (None, run_context, None)
    )
    factory_runtime = adapt_server_runtime(
        factory_runtime,
        authored_context,
        run_thread(binding, config, None).thread if is_run else None,
    )
    factory_entry = callable(entry)
    factory_inspection = factory_entry and not is_run
    shared_inputs: str | None = None
    if isinstance(entry, SharedAgentFactory):
        definition, shared_inputs = await _SharedDefinitions.resolve(entry, factory_runtime)
    else:
        definition = await resolve_agent_definition(entry, factory_runtime)
    if GENERAL_PURPOSE in (async_subagent_graphs or {}) or async_subagent == GENERAL_PURPOSE:
        definition = with_async_general_purpose(definition)
    if async_subagent is not None:
        definition = async_subagent_definition(definition, async_subagent)
    else:
        definition = route_async_subagents(definition, async_subagent_graphs or {})
    return await _compile_resolved_managed_agent_async(
        definition,
        config,
        context_root=context_root,
        context_repo=context_repo,
        system_prompt=system_prompt,
        has_skills=has_skills,
        sandbox=sandbox,
        sandbox_setup=sandbox_setup,
        connectors=connectors,
        channels=channels,
        identity=identity,
        memory=memory,
        browser=browser,
        trial_root_dir=trial_root_dir,
        run_context=run_context,
        is_run=is_run,
        report_telemetry=report_telemetry,
        skip_factory_inspection_tools=factory_inspection,
        factory_entry=factory_entry,
        shared_graph=async_subagent or "lead",
        shared_inputs=shared_inputs,
    )


async def _compile_resolved_managed_agent_async(
    definition: DeepAgentDefinition,
    config: ManagedRunConfig | None = None,
    *,
    context_root: str | None = None,
    context_repo: str | None = None,
    system_prompt: str | None = None,
    has_skills: bool = False,
    sandbox: SandboxDefinition | None = None,
    sandbox_setup: bool = False,
    connectors: list[Connector] | None = None,
    channels: list[Channel] | None = None,
    identity: IdentityDefinitionLike | None = None,
    memory: MemoryDefinitionLike | None = None,
    browser: BrowserDefinition | None = None,
    trial_root_dir: str | None = None,
    run_context: object = None,
    is_run: bool = True,
    report_telemetry: bool = True,
    skip_factory_inspection_tools: bool = False,
    factory_entry: bool = False,
    shared_graph: str | None = None,
    shared_inputs: str | None = None,
) -> CompiledAgent:
    """Select native context schema and memory access for a resolved definition.

    A definition a shared factory built (``shared_inputs``, the digest of its inputs)
    is compiled once per process for those inputs and shared by every run and read of
    ``shared_graph`` (the lead's graph, or an async subagent's), whichever thread or
    caller it serves.
    """
    started_monotonic_ms = time.monotonic() * 1000

    run_connectors = _select_run_connectors(definition.config, connectors)
    run_sandbox = (
        None if trial_root_dir is not None else _select_run_sandbox(definition.config, sandbox)
    )
    sandbox_selection_fingerprint = (
        _sandbox_selection_fingerprint(run_sandbox)
        if factory_entry and run_sandbox is not None and "sandbox" in definition.config
        else None
    )
    if run_sandbox is not None and sandbox_selection_fingerprint and is_run and sandbox_setup:
        run_sandbox = _inherit_setup_recipe(run_sandbox, sandbox)
    # Eval builds ship no browser, like no nested sandbox.
    run_browser = (
        None if trial_root_dir is not None else _select_run_browser(definition.config, browser)
    )
    register_channel_messaging(channels or [])
    memory_selected_by_dynamic_resolver = "memory" in definition.config
    run_memory = (
        (
            None
            if definition.config["memory"] is None
            else snapshot_memory_selection(definition.config["memory"])
        )
        if memory_selected_by_dynamic_resolver
        else memory
    )
    options: _ManagedCompileOptions = {
        "context_root": context_root,
        "context_repo": context_repo,
        "system_prompt": system_prompt,
        "has_skills": has_skills,
        "sandbox": run_sandbox,
        "sandbox_selection_fingerprint": sandbox_selection_fingerprint,
        "connectors": run_connectors,
        "channels": channels,
        "identity": identity,
        "memory": run_memory,
        "browser": run_browser,
        "trial_root_dir": trial_root_dir,
    }
    context_schema = definition.config.get("context_schema")
    declared_memory_layers = _declared_memory_layers(run_memory)
    from managed_deepagents._run_input import (  # noqa: PLC0415
        ManagedCompiledAgent,
        SingleProjectManagedAgent,
        is_channel_execution,
    )

    policy_schema = (
        ManagedChannelContext
        if is_run and is_channel_execution(resolve_runtime_configurable(config))
        else context_schema
    )
    allow_agent = False
    user_access = UserMemoryAccess(denied_reason="no_context")
    if is_run:
        agent_layer = next(
            (layer for layer in declared_memory_layers if layer["type"] == "agent"), None
        )
        # Memory policies keep their public channel field after routing normalization.
        policy_context = _policy_run_context(run_context, config)
        if policy_schema is ManagedChannelContext:
            policy_context = _with_thread_origin(policy_context, config)
        allow_agent = await resolve_agent_memory_access(agent_layer, policy_context, policy_schema)
        user_access = await resolve_user_memory_access(
            user_memory_layer(declared_memory_layers),
            config,
            policy_context,
            policy_schema,
            selected_by_dynamic_resolver=memory_selected_by_dynamic_resolver,
        )
    key = (
        (shared_graph, shared_inputs)
        if shared_graph is not None and shared_inputs is not None
        else None
    )
    policy = (allow_agent, user_access.user_id, user_access.denied_reason) if is_run else None
    built = _SharedGraphs.built(key, policy) if key is not None else None
    if built is None:
        agent, configuration = await asyncio.to_thread(
            _compile_managed_agent,
            definition,
            # A shared graph closes over no run's config: what a call needs of its
            # run, such as the thread its box belongs to, it reads from the run.
            None if key is not None else config,
            **options,
            allow_agent=allow_agent,
            user_access=user_access,
            declared_memory_layers=declared_memory_layers,
            inspection=not is_run,
            skip_factory_inspection_tools=skip_factory_inspection_tools,
            context_schema=context_schema,
        )
        if isinstance(agent, CompiledAgent):
            agent.__class__ = (
                SingleProjectManagedAgent
                if definition.config.get("replicate_traces") is False
                else ManagedCompiledAgent
            )
        built = _BuiltGraph(policy=policy, agent=agent, configuration=configuration)
        if key is not None:
            _SharedGraphs.keep(key, built)
    return _for_call(
        built,
        config,
        run_context=run_context,
        is_run=is_run,
        report_telemetry=report_telemetry,
        started_monotonic_ms=started_monotonic_ms,
    )


def _for_call(
    built: _BuiltGraph,
    config: ManagedRunConfig | None,
    *,
    run_context: object,
    is_run: bool,
    report_telemetry: bool,
    started_monotonic_ms: float,
) -> CompiledAgent:
    """``built`` as this call runs it: its telemetry and this run's config defaults.

    Applied per call, never to a shared graph.
    """
    from managed_deepagents._run_input import (  # noqa: PLC0415
        execution_context,
        graph_default_config,
    )

    agent = built.agent
    if not isinstance(agent, CompiledAgent):
        return agent
    if report_telemetry:
        agent = observe_run(
            agent,
            start_run(
                detect_run_identity(
                    caller=build_runtime_identity(resolve_runtime_configurable(config)),
                    is_run=is_run,
                ),
                replace(built.configuration, channel=_detect_run_channel(config)),
                run_id=_platform_run_id(config),
                thread_id=_thread_id_of(config),
                started_monotonic_ms=started_monotonic_ms,
            ),
        )
    defaults = graph_default_config(config)
    if is_run:
        _, _, routing = execution_context(run_context, config, None)
        if routing is not None:
            # Agent Server filters context against the authored schema after
            # this factory returns. Retain managed routing for graph execution.
            defaults["configurable"] = {**defaults.get("configurable", {}), **routing}
    return agent.with_config(defaults)


@dataclass(frozen=True)
class _BuiltGraph:
    """A compiled graph and what its runs report, before any one call's config."""

    # Who a run's graph was built for (its memory access); None for a read's.
    policy: tuple[bool, str | None, str | None] | None
    agent: CompiledAgent
    configuration: RunConfiguration


#: Graphs a process keeps compiled, one per graph and declared inputs.
_SHARED_GRAPHS_KEPT = 32


class _SharedDefinitions:
    """Each shared factory's definitions, by the digest of their inputs: one build each.

    A shared factory builds from its inputs alone, so the same inputs always build the
    same definition, and an access whose inputs have one kept never builds again.
    """

    _built: ClassVar[
        OrderedDict[tuple[SharedAgentFactory[BaseModel], str], DeepAgentDefinition]
    ] = OrderedDict()

    @classmethod
    async def resolve(
        cls, entry: SharedAgentFactory[BaseModel], runtime: ManagedServerRuntime
    ) -> tuple[DeepAgentDefinition, str]:
        """The definition for this access's inputs, built on the first access with them."""
        try:
            resolved = await entry.resolve(runtime)
        except Exception:  # noqa: BLE001 - callback failures cross an untrusted boundary
            raise RuntimeError("agent factory failed") from None
        key = (entry, resolved.digest)
        definition = cls._built.get(key)
        if definition is None:
            try:
                selected = entry.build(resolved)
            except Exception:  # noqa: BLE001 - callback failures cross an untrusted boundary
                raise RuntimeError("agent factory failed") from None
            if not isinstance(selected, DeepAgentDefinition):
                raise TypeError("agent factory must return define_deep_agent(...)")
            definition = _snapshot_factory_definition(selected)
            cls._built[key] = definition
        cls._built.move_to_end(key)
        while len(cls._built) > _SHARED_GRAPHS_KEPT:
            cls._built.popitem(last=False)
        return definition, resolved.digest


class _SharedGraphs:
    """Compiled graphs shared by every run and read, by graph and shared inputs.

    Only a shared factory's definitions are kept: it builds them from its inputs
    alone, so nothing in them belongs to one run, thread or caller.
    """

    _built: ClassVar[OrderedDict[tuple[str, str], _BuiltGraph]] = OrderedDict()

    @classmethod
    def built(
        cls, key: tuple[str, str], policy: tuple[bool, str | None, str | None] | None
    ) -> _BuiltGraph | None:
        """The graph kept for ``key``: any for a read; for a run, one built for a run like it."""
        built = cls._built.get(key)
        if built is None or (policy is not None and built.policy != policy):
            return None
        cls._built.move_to_end(key)
        return built

    @classmethod
    def keep(cls, key: tuple[str, str], built: _BuiltGraph) -> None:
        kept = cls._built.get(key)
        # A read's build never displaces the graph a run built.
        if built.policy is None and kept is not None:
            return
        cls._built[key] = built
        cls._built.move_to_end(key)
        while len(cls._built) > _SHARED_GRAPHS_KEPT:
            cls._built.popitem(last=False)


def _select_run_connectors(
    config: Mapping[str, object],
    discovered: list[Connector] | None,
) -> list[Connector] | None:
    """Replace discovered MCP definitions while preserving other connectors."""
    if "mcp" not in config:
        return discovered

    selected = () if config["mcp"] is None else snapshot_mcp_selection(config["mcp"])
    return [
        *(connector for connector in discovered or [] if connector.kind != "mcp_servers"),
        *selected,
    ]


def _select_run_sandbox(
    config: Mapping[str, object],
    discovered: SandboxDefinition | None,
) -> SandboxDefinition | None:
    """Replace or disable the discovered sandbox for this run."""
    if "sandbox" not in config:
        return discovered
    return snapshot_sandbox_selection(config["sandbox"])


def _select_run_browser(
    config: Mapping[str, object],
    declared: BrowserDefinition | None,
) -> BrowserDefinition | None:
    """Keep, disable, or replace ``browser.py``'s browser for this run."""
    if "browser" not in config:
        return declared
    selected = snapshot_browser_selection(config["browser"])
    if selected is not None and declared is None:
        msg = (
            "The agent factory selected a browser, but the project has no browser.py. "
            "Declare `browser = define_browser()` there so the deployment installs Stagehand."
        )
        raise ValueError(msg)
    return selected


def _compile_managed_agent(
    definition: DeepAgentDefinition,
    config: ManagedRunConfig | None = None,
    *,
    context_root: str | None = None,
    context_repo: str | None = None,
    system_prompt: str | None = None,
    has_skills: bool = False,
    sandbox: SandboxDefinition | None = None,
    sandbox_selection_fingerprint: str | None = None,
    connectors: list[Connector] | None = None,
    channels: list[Channel] | None = None,
    identity: IdentityDefinitionLike | None = None,
    memory: MemoryDefinitionLike | None = None,
    browser: BrowserDefinition | None = None,
    trial_root_dir: str | None = None,
    user_access: UserMemoryAccess,
    allow_agent: bool,
    declared_memory_layers: MemoryLayers,
    inspection: bool = False,
    skip_factory_inspection_tools: bool = False,
    context_schema: object = None,
) -> tuple[CompiledAgent, RunConfiguration]:
    """Compile the definition; ``_for_call`` adds each call's telemetry and config."""
    allowed_user_id = user_access.user_id
    # Resolution + thread/store scope run in the generated custom-auth handler
    # (``build_managed_auth``). When identity is declared, the seam also surfaces
    # ``runtime.server_info.principal`` to authored + connector tools (below),
    # projected from the trusted ``langgraph_auth_user``.
    merged: dict[str, object] = dict(definition.config)
    authored_instructions = merged.pop("instructions", _OMITTED)
    authored_skills = merged.pop("skills", _OMITTED)
    subagent_skills = merged.pop(SUBAGENT_SKILLS_KEY, None)
    merged.pop("memory", None)
    merged.pop("mcp", None)
    merged.pop("sandbox", None)
    merged.pop("browser", None)
    merged.pop("replicate_traces", None)
    merged["context_schema"] = context_schema

    sandbox_backend = (
        _managed_sandbox_backend(
            sandbox,
            config,
            connectors=connectors,
            identity=identity,
            selection_fingerprint=sandbox_selection_fingerprint,
        )
        if sandbox is not None
        else None
    )
    backend_factory: RuntimeBackendFactory = (
        sandbox_backend.bind_runtime if sandbox_backend is not None else lambda _runtime: None
    )

    context = (
        None
        if inspection
        else _open_context_backend(context_repo=context_repo, context_root=context_root)
    )
    selected_skills_backend = (
        None
        if skip_factory_inspection_tools
        else _resolve_run_skills(
            authored_skills,
            context,
            context_repo=context_repo,
            context_root=context_root,
            trial_root_dir=trial_root_dir,
        )
    )
    mounted_memory: MemoryLayers = []
    user_memory_backend: UserMemoryBackend | None = None
    if context is not None:
        mounted_memory, user_memory_backend = _apply_managed_context(
            merged,
            context_backend=context.backend,
            filesystem_root=context.filesystem_root,
            context_repo=context_repo,
            run_config=config,
            sandbox_backend=sandbox_backend,
            memory_layers=declared_memory_layers,
            selected_skills_backend=selected_skills_backend,
            allow_agent=allow_agent,
            allowed_user_id=allowed_user_id,
        )
    elif sandbox_backend is not None:
        merged["backend"] = sandbox_backend
    if context is None and selected_skills_backend is not None:
        _mount_selected_skills(
            merged,
            selected_skills_backend,
            sandbox_backend=sandbox_backend,
        )

    merged["system_prompt"] = _select_system_prompt(
        authored_instructions,
        context.backend if context is not None else None,
        system_prompt,
    )

    # Mounted the same way in every mode, but resolved against different roots:
    # local/deploy route ``/skills/`` to the Context Hub mock or repo, while an
    # eval trial reads the project's own ``skills/`` tree, which the Harbor
    # adapter copies into the trial workspace the backend is rooted at.
    effective_has_skills = (
        bool(authored_skills)
        if skip_factory_inspection_tools and authored_skills is not _OMITTED
        else selected_skills_backend.file_count > 0
        if selected_skills_backend is not None
        else has_skills
    )
    if sources := _skill_sources(subagent_skills, has_skills=effective_has_skills):
        merged["skills"] = sources
    # Authored tools and managed Slack file tools share the current sandbox backend.
    # Wrap even without identity or a sandbox so runtime.backend is None rather
    # than an absent attribute on LangGraph's runtime.
    # Inspection needs the credential gate, but must not load credentials.
    _prepend_credential_gate_middleware(merged, connectors, sandbox)
    # Save files and checkpoint their paths before credential interrupts;
    # resume deliveries do not include the original file event.
    _prepend_slack_files_middleware(merged, channels)
    # Before the seam, so the browser's tools get the thread's sandbox backend.
    _append_browser_middleware(merged, browser)
    _apply_runtime_seam(merged, backend_factory)

    # Connectors: run each connector's ``tools`` hook and append the results to
    # the authored tools. Connector tools keep identity injection, but do not
    # receive the authored runtime backend API.
    if connectors and not skip_factory_inspection_tools:
        tools_value = merged.get("tools")
        authored_tools = (
            list(cast("Iterable[object]", tools_value)) if tools_value is not None else []
        )
        connector_tools = load_connector_tools(connectors)
        if identity is not None:
            connector_tools = wrap_tools_with_managed_runtime(connector_tools)
        merged["tools"] = authored_tools + connector_tools

    # Managed prompt behavior surrounds authored middleware in the same order as TS.
    _append_channel_response_format_middleware(merged)
    if mounted_memory:
        _append_memory_instructions_middleware(
            merged,
            mounted_memory,
            user_memory_backend,
            agent_memory_backend=context.backend
            if context is not None and has_agent_memory_layer(mounted_memory)
            else None,
        )

    # Author ``metadata`` is not a create_deep_agent kwarg — apply it on the
    # compiled runnable so LangSmith root runs carry prompt provenance, etc.
    metadata = merged.pop("metadata", None)
    # The definition and managed adapters are validated at their own boundaries.
    # This heterogeneous argument map cannot express each native keyword type.
    agent = create_deep_agent(**cast("Any", merged))
    compiled = (
        agent.with_config({"metadata": dict(metadata)})
        if isinstance(metadata, Mapping) and metadata
        else agent
    )
    return compiled, RunConfiguration(
        # Each call's own, set as it runs.
        channel=_detect_run_channel(config),
        sandbox_declared=sandbox is not None,
        identity_declared=identity is not None,
        skills_declared=effective_has_skills,
        backend_kind="sandbox" if sandbox_backend is not None else "state",
        context_source=(
            "none" if context is None else ("local" if context.filesystem_root else "hub")
        ),
        memory=MemoryObservations(
            layers_declared=[layer["type"] for layer in _declared_memory_layers(memory)],
            layers_mounted=[layer["type"] for layer in mounted_memory],
            user_denied_reason=user_access.denied_reason,
        ),
    )


def _platform_run_id(config: Mapping[str, object] | None) -> str | None:
    """LangGraph stamps the run it is executing here; Pregel reads the same key.

    Absent under ``mda dev`` and a direct invoke, where ``start_run`` mints one.
    """
    metadata = config.get("metadata") if config else None
    value = metadata.get("run_id") if isinstance(metadata, Mapping) else None
    return _string_option(value)


def _thread_id_of(config: Mapping[str, object] | None) -> str | None:
    configurable = config.get("configurable") if config else None
    value = configurable.get("thread_id") if isinstance(configurable, Mapping) else None
    return _string_option(value)


def _policy_run_context(run_context: object, config: RunnableConfig | None) -> object:
    from managed_deepagents._run_input import execution_context  # noqa: PLC0415

    _, _, routing = execution_context(run_context, config, None)
    context = run_context if isinstance(run_context, Mapping) else {}
    channel = (routing or {}).get("mda_channel", context.get("mda_channel"))
    if channel is not None:
        return {**context, "mda_channel": channel, "channel": channel}
    return run_context


def _with_thread_origin(policy_context: object, config: RunnableConfig | None) -> object:
    """Give the memory policy of a run with no live channel its thread origin's channel.

    A run no channel message started (a task notification waking the lead, a
    resume) still answers in the thread's channel conversation, so the policy
    sees that channel too. The origin has no provider event, so MDA's default
    policy still denies user memory outside a live Slack DM; only an authored
    policy can allow it. The origin comes from the run's metadata, so this needs
    no extra request.
    """
    from managed_deepagents._run_input import run_thread  # noqa: PLC0415

    if _field(policy_context, "mda_channel") is not None:
        return policy_context
    origin = run_thread(None, config, None).origin
    if origin is None:
        return policy_context
    context = policy_context if isinstance(policy_context, Mapping) else {}
    return {**context, "mda_channel": origin, "channel": origin}


def _detect_run_channel(config: Mapping[str, object] | None) -> RunChannel:
    """The cron principal cannot be forged, so it wins over a channel binding.

    An unrecognized provider reports ``unknown`` rather than widening the
    dimension.
    """
    from managed_deepagents._run_input import is_scheduled_execution  # noqa: PLC0415

    configurable = config.get("configurable") if config else None
    if not isinstance(configurable, Mapping):
        return "unknown"
    if is_scheduled_execution(configurable):
        return "schedule"
    provider = resolve_channel_provider(configurable)
    if provider in {"slack", "http"}:
        return provider
    return "unknown"


class _OpenedContextBackend:
    """Resolved Context Hub or local filesystem mock used by compile."""

    __slots__ = ("backend", "filesystem_root")

    def __init__(self, backend: BackendProtocol, filesystem_root: str | None) -> None:
        self.backend = backend
        self.filesystem_root = filesystem_root


def _open_context_backend(
    *, context_repo: str | None, context_root: str | None
) -> _OpenedContextBackend | None:
    """Open the deploy Hub or local ``.mda/__contexthub__`` mock, if configured."""
    if context_repo:
        return _OpenedContextBackend(ContextHubBackend(context_repo), None)
    if context_root:
        return _OpenedContextBackend(
            FilesystemBackend(root_dir=context_root, virtual_mode=True),
            context_root,
        )
    return None


def _skill_sources(subagent_skills: object, *, has_skills: bool) -> list[str]:
    """Skill sources for the agent: an async subagent's own (`SubAgent.skills`), else the mount."""
    if subagent_skills is None:
        return [MANAGED_SKILLS_PATH] if has_skills else []
    sources = list(cast("Sequence[str]", subagent_skills))
    # Without a mount, `/skills/` would read whatever the sandbox holds there.
    return sources if has_skills else [path for path in sources if path != MANAGED_SKILLS_PATH]


def _resolve_run_skills(
    authored: object,
    context: _OpenedContextBackend | None,
    *,
    context_repo: str | None,
    context_root: str | None,
    trial_root_dir: str | None,
) -> SelectedSkillsBackend | None:
    if authored is _OMITTED:
        return None
    source_context = context
    if source_context is None and trial_root_dir is None:
        source_context = _open_context_backend(
            context_repo=context_repo,
            context_root=context_root,
        )
    return resolve_selected_skills(
        () if authored is None else authored,
        context_backend=source_context.backend if source_context is not None else None,
        local_root=trial_root_dir,
    )


def _mount_selected_skills(
    merged: dict[str, object],
    selected: SelectedSkillsBackend,
    *,
    sandbox_backend: SandboxBackendProtocol | None,
) -> None:
    # ``merged`` is the heterogeneous ``create_deep_agent`` kwargs map; only the
    # runtime itself (sandbox or eval trial backend) has set ``backend`` here.
    merged["backend"] = _managed_selected_skills_backend(
        cast("BackendProtocol | None", merged.get("backend")),
        selected,
        CompositeBackend,
        StateBackend,
    )
    if sandbox_backend is not None:
        merged["permissions"] = []
        return
    merged["permissions"] = _managed_context_permissions(
        merged.get("permissions"),
        FilesystemPermission,
    )


def _apply_managed_context(
    merged: dict[str, object],
    *,
    context_backend: BackendProtocol,
    filesystem_root: str | None,
    context_repo: str | None,
    run_config: Mapping[str, object] | None,
    sandbox_backend: SandboxBackendProtocol | None,
    memory_layers: MemoryLayers,
    selected_skills_backend: SelectedSkillsBackend | None,
    allowed_user_id: str | None,
    allow_agent: bool,
) -> tuple[MemoryLayers, UserMemoryBackend | None]:
    """Wire system prompt, backend routes, permissions, and hot memory.

    Return the mounted layers and the caller backend used to load user hot
    memory once per run without saving it in thread state. Skills are
    mounted by the caller so eval trials mount them too.
    """
    # Memory is opt-in: only the root ``memory`` declaration enables a layer, so a
    # project that declares nothing mounts nothing regardless of identity.
    memory_layers = [
        layer
        for layer in memory_layers
        if (allow_agent if layer["type"] == "agent" else allowed_user_id is not None)
    ]
    declared_user_layer = user_memory_layer(memory_layers)
    user_memory_backend = (
        UserMemoryBackend(
            deploy_handle=context_repo or "local",
            filesystem_root=filesystem_root,
            run_config=run_config,
            # An assertion the backend checks against the authenticated
            # principal; it never selects whose memory is mounted.
            asserted_user_id=declared_user_layer.get("id"),
            allowed_user_id=allowed_user_id,
        )
        if declared_user_layer is not None
        else None
    )
    merged["backend"] = _managed_context_backend(
        context_backend,
        sandbox_backend,
        _ManagedCompositeBackend,
        StateBackend,
        mount_agent_memory=has_agent_memory_layer(memory_layers),
        user_memory_backend=user_memory_backend,
        selected_skills_backend=selected_skills_backend,
    )
    # Sandbox + FilesystemPermission cannot coexist at execute() time: deepagents
    # adapts CompositeBackend and drops route prefixes, so any permission rule
    # disables shell. Rely on read-only Context Hub mounts instead.
    if sandbox_backend is not None:
        merged["permissions"] = []
    else:
        merged["permissions"] = _managed_context_permissions(
            merged.get("permissions"),
            FilesystemPermission,
        )
    return memory_layers, user_memory_backend


def _declared_memory_layers(memory: MemoryDefinitionLike | None) -> MemoryLayers:
    """Layers from the root declaration; empty when the project declares none."""
    if memory is None:
        return []
    config = getattr(memory, "config", None)
    if not isinstance(config, Mapping):
        return []
    scope = config.get("scope")
    # Dynamic boundary: the declaration is imported from the author's module, so
    # its shape is checked here rather than trusted.
    return list(scope) if isinstance(scope, list) else []


def _select_system_prompt(
    authored: object,
    context_backend: BackendProtocol | None,
    fallback: str | None,
) -> str:
    """Choose authored instructions while preserving MDA's platform boundary."""
    if authored is not _OMITTED:
        if authored is None:
            selected = ""
        elif isinstance(authored, str):
            selected = authored
        else:
            raise TypeError("instructions must be a string or None")
    elif context_backend is not None:
        selected = _read_context_instructions(context_backend)
    else:
        selected = None
    if selected is None:
        selected = fallback
    return (
        f"{MANAGED_DEEP_AGENT_INSTRUCTIONS}\n\n{selected}"
        if selected
        else MANAGED_DEEP_AGENT_INSTRUCTIONS
    )


def compile_managed_agent_for_eval(
    definition: DeepAgentDefinition,
    config: ManagedRunConfig | None = None,
    *,
    system_prompt: str | None = None,
    has_skills: bool = False,
    connectors: list[Connector] | None = None,
    channels: list[Channel] | None = None,
    identity: IdentityDefinitionLike | None = None,
    model_override: str | None = None,
) -> CompiledAgent:
    """Compile an authored definition for an MDA eval trial.

    Harbor owns the outer sandbox in eval mode, so authored MDA sandbox wiring is
    deliberately not instantiated here. File tools must still write real disk
    under the trial ``/app`` (or ``configurable["cwd"]``) so Harbor verifiers can
    see outputs like ``/app/answer.txt`` — without this, deepagents defaults to
    StateBackend and writes stay in virtual agent state.

    ``has_skills`` mounts the project's skills the way dev and deploy do. There is
    no Context Hub here: the trial backend is rooted at the workspace the adapter
    copied the compiled project into, so :data:`MANAGED_SKILLS_PATH` resolves to
    the project's own ``skills/`` tree.

    The model override is applied to a copied definition so normal dev/deploy
    behavior remains unchanged.
    """
    reject_async_subagents(definition)
    eval_definition = _prepare_eval_definition(definition, config, model_override)

    return compile_managed_agent(
        eval_definition,
        config,
        run_context=config.get("context") if config else None,
        system_prompt=system_prompt,
        has_skills=has_skills,
        connectors=connectors,
        channels=channels,
        identity=identity,
        trial_root_dir=_eval_app_dir(config),
        # A trial is a scored test rather than someone using an agent, so it
        # reports nothing; ``mda evals`` is counted once by the CLI instead.
        report_telemetry=False,
        # Eval trials never mount durable memory: this path takes no ``memory``
        # keyword, and the generated eval entry does not pass one.
    )


async def compile_agent_factory_for_eval_async(
    entry: AgentEntry,
    config: ManagedRunConfig | None = None,
    *,
    system_prompt: str | None = None,
    has_skills: bool = False,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    model_override: str | None = None,
) -> CompiledAgent:
    """Resolve a dynamic eval entry once before applying its trial model."""
    # Harbor calls this helper only to execute a scored trial. Missing context
    # means the run has empty authored context; it is not graph inspection.
    context = config.get("context") if config is not None else None
    definition = await resolve_agent_definition(
        entry, _internal_server_runtime(config, context, is_run=True)
    )
    reject_async_subagents(definition)
    eval_definition = _prepare_eval_definition(definition, config, model_override)
    return await _compile_resolved_managed_agent_async(
        eval_definition,
        config,
        run_context=context,
        is_run=True,
        system_prompt=system_prompt,
        has_skills=has_skills,
        connectors=connectors,
        identity=identity,
        trial_root_dir=_eval_app_dir(config),
        report_telemetry=False,
        skip_factory_inspection_tools=False,
    )


def _prepare_eval_definition(
    definition: DeepAgentDefinition,
    config: ManagedRunConfig | None,
    model_override: str | None,
) -> DeepAgentDefinition:
    """Copy one resolved definition and add eval-owned model and disk settings."""
    configured_model = _configurable_model(config)
    merged = dict(definition.config)
    selected_model = model_override or configured_model
    if selected_model:
        merged["model"] = selected_model
    # Prefer runner-provided cwd; default matches the adapter's ``/app`` staging.
    # Authoring forbids ``backend`` on definitions; inject via the compiled config.
    merged["backend"] = create_eval_trial_filesystem_backend(
        FilesystemBackend,
        _eval_app_dir(config),
        hide_skills="skills" in merged,
    )
    return DeepAgentDefinition(merged)


def _configurable_model(config: Mapping[str, object] | None) -> str | None:
    configurable = config.get("configurable") if config else None
    if not isinstance(configurable, Mapping):
        return None
    value = configurable.get("model")
    return _string_option(value)


def _eval_app_dir(config: Mapping[str, object] | None) -> str:
    configurable = config.get("configurable") if config else None
    if isinstance(configurable, Mapping):
        cwd = configurable.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return cwd
    return "/app"


def _apply_runtime_seam(merged: dict[str, object], backend_factory: RuntimeBackendFactory) -> None:
    """Wrap authored tools and middleware with managed runtime fields."""
    # ``merged`` is the heterogeneous ``create_deep_agent`` kwargs map; each
    # value was validated against its native type by ``define_deep_agent``.
    tools_value = merged.get("tools")
    if tools_value is not None:
        merged["tools"] = wrap_tools_with_managed_runtime(
            list(cast("Iterable[DeepAgentTool]", tools_value)), backend_factory
        )
    middleware_value = merged.get("middleware")
    if middleware_value is not None:
        merged["middleware"] = wrap_middleware_with_managed_runtime(
            list(cast("Iterable[AgentMiddleware]", middleware_value)), backend_factory
        )
    subagents_value = merged.get("subagents")
    if subagents_value is not None:
        merged["subagents"] = _wrap_declarative_subagents(
            list(cast("Iterable[DeepAgentSubAgent]", subagents_value)), backend_factory
        )


def _wrap_declarative_subagents(
    subagents: Sequence[DeepAgentSubAgent], backend_factory: RuntimeBackendFactory
) -> list[DeepAgentSubAgent]:
    """Wrap authored subagent tools while leaving opaque agents unchanged."""
    wrapped: list[DeepAgentSubAgent] = []
    for subagent in subagents:
        if not isinstance(subagent, Mapping) or "runnable" in subagent or "graph_id" in subagent:
            wrapped.append(subagent)
            continue
        # Compiled (``runnable``) and async (``graph_id``) specs are excluded above.
        spec = cast("SubAgent", dict(subagent))
        tools = spec.get("tools")
        if tools is not None:
            spec["tools"] = wrap_tools_with_managed_runtime(list(tools), backend_factory)
        middleware = spec.get("middleware")
        if middleware is not None:
            spec["middleware"] = wrap_middleware_with_managed_runtime(
                list(middleware), backend_factory
            )
        wrapped.append(spec)
    return wrapped


def _prepend_credential_gate_middleware(
    merged: dict[str, object],
    connectors: list[Connector] | None,
    sandbox: SandboxDefinition | None = None,
) -> None:
    """Gate user credentials required by connectors and sandbox proxy config."""
    from managed_deepagents._connectors.mcp.load import (  # noqa: PLC0415
        mcp_credential_consumers,
    )
    from managed_deepagents._credential_gate import (  # noqa: PLC0415
        create_credential_gate_middleware,
    )
    from managed_deepagents._sandbox_proxy import sandbox_credential_consumers  # noqa: PLC0415

    gate = create_credential_gate_middleware(
        [
            *mcp_credential_consumers(connectors),
            *sandbox_credential_consumers(sandbox),
        ]
    )
    if gate is None:
        return
    middleware_value = merged.get("middleware")
    existing = (
        list(cast("Iterable[object]", middleware_value)) if middleware_value is not None else []
    )
    merged["middleware"] = [gate, *existing]


def _append_memory_instructions_middleware(
    merged: dict[str, object],
    memory: MemoryLayers,
    user_memory_backend: UserMemoryBackend | None = None,
    *,
    agent_memory_backend: BackendProtocol | None = None,
) -> None:
    """Replace native memory in its slot after prompt caching, including in forks."""
    from managed_deepagents._memory_instructions import (  # noqa: PLC0415
        create_memory_instructions_middleware,
    )

    middleware_value = merged.get("middleware")
    existing = (
        list(cast("Iterable[object]", middleware_value)) if middleware_value is not None else []
    )
    # Reserve the native slot; replacement by name removes its loader and state
    # schema before graph compilation. Only MDA's permitted per-run loader runs.
    merged["memory"] = [f"/memories/{layer['type']}/AGENTS.md" for layer in memory]
    merged["middleware"] = [
        *existing,
        create_memory_instructions_middleware(
            memory,
            (lambda: _download_context_file(user_memory_backend, "/AGENTS.md"))
            if user_memory_backend is not None
            else None,
            load_agent_memory=(
                lambda: _download_context_file(agent_memory_backend, "/memories/agent/AGENTS.md")
            )
            if agent_memory_backend is not None
            else None,
        ),
    ]


def _prepend_slack_files_middleware(
    merged: dict[str, object], channels: list[Channel] | None
) -> None:
    """Enable file transfers, and status choosers, when the agent declares Slack."""
    if any(getattr(channel, "kind", None) == "slack" for channel in channels or []):
        from managed_deepagents._channels.slack.middleware import (  # noqa: PLC0415
            SlackFileTransferMiddleware,
        )
        from managed_deepagents._channels.slack.status import (  # noqa: PLC0415
            SlackStatusMiddleware,
            slack_status_choosers,
        )

        choosers = slack_status_choosers(channels)
        authored_middleware = merged.get("middleware")
        merged["middleware"] = [
            SlackFileTransferMiddleware(),
            # Show each Slack channel's status chooser while the run works.
            *([SlackStatusMiddleware(choosers)] if choosers else []),
            *(cast("Iterable[object]", authored_middleware) if authored_middleware else []),
        ]


def _append_browser_middleware(
    merged: dict[str, object], browser: BrowserDefinition | None
) -> None:
    """Give the agent a browser when the project declares one in ``browser.py``."""
    if browser is None:
        return
    from managed_deepagents.browser import BrowserMiddleware  # noqa: PLC0415

    authored_middleware = merged.get("middleware")
    merged["middleware"] = [
        *(cast("Iterable[object]", authored_middleware) if authored_middleware else []),
        BrowserMiddleware(scope=browser.scope, idle_ttl_seconds=browser.idle_ttl_seconds),
    ]


def _append_channel_response_format_middleware(merged: dict[str, object]) -> None:
    """Install managed channel response guidance after authored middleware.

    The run-output recorder goes first, so a stateless run's delivery reads
    what the agent ended with.
    """
    from managed_deepagents._channels.response_format import (  # noqa: PLC0415
        create_channel_response_format_middleware,
    )
    from managed_deepagents._channels.run_output import RunOutputMiddleware  # noqa: PLC0415

    middleware_value = merged.get("middleware")
    existing = (
        list(cast("Iterable[object]", middleware_value)) if middleware_value is not None else []
    )
    merged["middleware"] = [
        RunOutputMiddleware(),
        *existing,
        create_channel_response_format_middleware(),
    ]


#: Refusal returned instead of globbing the sandbox root.
#:
#: deepagents implements sandbox glob as an unfiltered ``find -L <path>`` whose
#: entire stdout is buffered in this process and then split per line, so a root
#: search walks the whole container image (including ``/proc``, and back through
#: ``/`` via its symlinks) and can exhaust the server heap before any truncation
#: applies. Must stay identical to the npm runtime's ``ROOT_GLOB_ERROR``.
_ROOT_GLOB_ERROR = (
    "cannot glob the sandbox root: '/' walks the entire container filesystem, "
    "which can exhaust memory. Retry with a concrete directory, for example /workspace."
)


def _is_filesystem_root(path: str | None) -> bool:
    """Whether ``path`` addresses the filesystem root rather than a directory in it.

    ``None`` counts: deepagents defaults an omitted path to ``/``, and
    ``CompositeBackend`` forwards a root search to the default backend as-is.
    """
    raw = ("/" if path is None else path).strip()
    if not raw:
        return True
    if not raw.startswith("/"):
        return False
    return posixpath.normpath(f"/{raw.lstrip('/')}") == "/"


def _root_glob_refusal() -> GlobResult:
    """Refuse a root glob as a real ``GlobResult``.

    Must be the deepagents dataclass, not a structural stand-in:
    ``CompositeBackend.glob`` gates on ``isinstance(result, GlobResult)`` before
    reading ``error``, and treats anything else as a raw list of matches.
    """
    return GlobResult(error=_ROOT_GLOB_ERROR, matches=[])


class _LazySandboxSource:
    """Factory-closed inputs shared by every bound lazy sandbox backend."""

    __slots__ = (
        "connectors",
        "identity",
        "proxy_config_sync",
        "run_config",
        "sandbox",
        "selection_fingerprint",
    )

    def __init__(
        self,
        sandbox: SandboxDefinition,
        run_config: ManagedRunConfig | None,
        *,
        connectors: list[Connector] | None,
        identity: IdentityDefinitionLike | None,
        selection_fingerprint: str | None,
        proxy_config_sync: SandboxProxyConfigSync,
    ) -> None:
        self.sandbox = sandbox
        self.run_config = run_config
        self.connectors = connectors
        self.identity = identity
        self.selection_fingerprint = selection_fingerprint
        self.proxy_config_sync = proxy_config_sync


def _ambient_runtime() -> object:
    """The LangGraph runtime of the run in progress, if any."""
    from langgraph.config import get_config  # noqa: PLC0415

    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return None
    return configurable.get("__pregel_runtime")


class _LazyManagedSandboxBackend(SandboxBackendProtocol):
    """Lazy sandbox backend with frozen per-instance ``configurable``.

    Construct unbound (``None``) for static mounts, or call ``bind_runtime`` so
    concurrent requests each keep their own thread/actor without shared
    mutable identity state. Process-level sandbox reuse lives in
    ``_resolve_managed_sandbox``.
    """

    def __init__(
        self,
        source: _LazySandboxSource,
        bound_configurable: Mapping[str, object] | None,
        runtime: object = None,
    ) -> None:
        self._source = source
        self._bound_configurable = bound_configurable
        # The bound run's runtime, which picks its box (``_openswe_select``).
        self._runtime = runtime
        self._selected: SandboxDefinition | None = None
        self._resolved_backend: SandboxBackendProtocol | None = None
        # A graph shared by every run closes over no run's config: unbound, this backend
        # finds the run in progress per call and keeps no box of its own.
        self._shared = bound_configurable is None and source.run_config is None

    def bind_runtime(self, runtime: object) -> _LazyManagedSandboxBackend:
        """Capture this run's configuration without resolving the sandbox."""
        return _LazyManagedSandboxBackend(
            self._source, resolve_runtime_configurable(runtime), runtime
        )

    def _selection(self) -> SandboxDefinition | None:
        """The box of the run this backend acts for, as ``_openswe_select`` picks it, if any."""
        from managed_deepagents.sandbox import selected_for  # noqa: PLC0415

        if self._selected is not None:
            return self._selected
        runtime = self._runtime if self._runtime is not None else _ambient_runtime()
        selected = selected_for(self._source.sandbox, runtime)
        if selected is not None and not self._shared:
            self._selected = selected
        return selected

    def _sandbox(self) -> SandboxDefinition:
        selected = self._selection()
        if selected is None:
            raise RuntimeError("This run has no sandbox")
        return selected

    def _without_box(self) -> SandboxBackendProtocol | None:
        """Where this run acts instead (``_openswe_without_box``) when it has no box."""
        from managed_deepagents.sandbox import without_box  # noqa: PLC0415

        if self._resolved_backend is not None:
            return None
        instead = without_box(self._source.sandbox)
        return instead if instead is not None and self._selection() is None else None

    def _keep(self, backend: SandboxBackendProtocol) -> None:
        if not self._shared:
            self._resolved_backend = backend

    def opened(self) -> SandboxBackendProtocol | None:
        """This thread's sandbox if this process opened it, by any handle; never opens one."""
        if self._resolved_backend is not None:
            return self._resolved_backend
        try:
            key = _thread_scope_key(self._config(), self._source.selection_fingerprint)
        except Exception:  # noqa: BLE001 — no thread, so no sandbox, as in ``id``
            return None
        with _managed_sandbox_lock:
            entry = _managed_sandboxes.get(key)
        return entry.backend if entry is not None else None

    @property
    def id(self) -> str:
        # deepagents' sandbox protocol guards require a non-empty ``id``
        # (and snapshot it at adapt time). Return the resolved sandbox id
        # when available; otherwise a stable pending placeholder so execute
        # is not stripped before the first resolve. Never raise — a missing
        # thread_id must not crash CompositeBackend construction.
        resolved = getattr(self._resolved_backend, "id", None)
        if resolved:
            return str(resolved)
        try:
            scope = _sandbox_scope_key(
                self._sandbox(), self._config(), self._source.selection_fingerprint
            )
            return f"pending:{scope}"
        except Exception:  # noqa: BLE001 — match TS pending:sandbox fallback
            return "pending:sandbox"

    def ls(self, path: str = "/") -> LsResult:
        return self._call(lambda backend: backend.ls(path))

    async def als(self, path: str = "/") -> LsResult:
        return await self._acall(lambda backend: backend.als(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._call(lambda backend: backend.read(file_path, offset=offset, limit=limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._acall(lambda backend: backend.aread(file_path, offset, limit))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if max_count is not None:
            return self._call(
                lambda backend: backend.grep(pattern, path, glob, max_count=max_count)
            )
        return self._call(lambda backend: backend.grep(pattern, path, glob))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if max_count is not None:
            return await self._acall(
                lambda backend: backend.agrep(pattern, path, glob, max_count=max_count)
            )
        return await self._acall(lambda backend: backend.agrep(pattern, path, glob))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        # Refuse before resolving: a root glob is never worth creating a sandbox for.
        if _is_filesystem_root(path):
            return _root_glob_refusal()
        return self._call(lambda backend: backend.glob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        if _is_filesystem_root(path):
            return _root_glob_refusal()
        return await self._acall(lambda backend: backend.aglob(pattern, path))

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._call(lambda backend: backend.write(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._acall(lambda backend: backend.awrite(file_path, content))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._call(
            lambda backend: backend.edit(file_path, old_string, new_string, replace_all=replace_all)
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._acall(
            lambda backend: backend.aedit(
                file_path, old_string, new_string, replace_all=replace_all
            )
        )

    def delete(self, file_path: str) -> DeleteResult:
        return self._call(lambda backend: backend.delete(file_path))

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._acall(lambda backend: backend.adelete(file_path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._call(lambda backend: backend.upload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._acall(lambda backend: backend.aupload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._call(lambda backend: backend.download_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._acall(lambda backend: backend.adownload_files(paths))

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if timeout is not None:
            try:
                return self._call(
                    lambda backend: backend.execute(command, timeout=timeout), execute=True
                )
            except TypeError:
                return self._call(lambda backend: backend.execute(command), execute=True)
        return self._call(lambda backend: backend.execute(command), execute=True)

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - native backend timeout contract
    ) -> ExecuteResponse:
        try:
            if timeout is not None:
                return await self._acall(
                    lambda backend: backend.aexecute(command, timeout=timeout), execute=True
                )
            return await self._acall(lambda backend: backend.aexecute(command), execute=True)
        except TypeError:
            return await self._acall(lambda backend: backend.aexecute(command), execute=True)

    async def aopen(self) -> str:
        """Adopt or create this run's box now, as its first use would, and return its name."""
        return (await self._abackend()).id

    async def areplace(self, sandbox: SandboxDefinition | None = None) -> SandboxReplacement:
        """Replace this run's box with a fresh one under its name, from ``sandbox``'s options."""
        source = self._source
        await self._abackend()
        current = self._sandbox()
        backend, replacement = await asyncio.to_thread(
            _replace_managed_sandbox,
            current,
            sandbox if sandbox is not None else current,
            self._config(),
            source.connectors,
            source.identity,
            source.selection_fingerprint,
        )
        source.proxy_config_sync.invalidate(replacement.new_name)
        self._keep(backend)
        return replacement

    def _config(self) -> ManagedRunConfig | None:
        return _effective_sandbox_run_config(
            self._source.run_config, bound_configurable=self._bound_configurable
        )

    async def _sync_sandbox_proxy_config(self) -> None:
        from managed_deepagents.sandbox import manages_proxy  # noqa: PLC0415

        sandbox = self._sandbox()
        if not manages_proxy(sandbox.options):
            return
        config = self._config()
        key = _sandbox_scope_key(sandbox, config, self._source.selection_fingerprint)
        await self._source.proxy_config_sync.sync(
            sandbox,
            _peek_managed_host_name(key),
            config,
        )

    def _backend(self) -> SandboxBackendProtocol:
        if self._resolved_backend is not None:
            return self._resolved_backend
        source = self._source
        backend = _resolve_managed_sandbox(
            self._sandbox(),
            self._config(),
            connectors=source.connectors,
            identity=source.identity,
            selection_fingerprint=source.selection_fingerprint,
        )
        self._keep(backend)
        return backend

    async def _abackend(self) -> SandboxBackendProtocol:
        if self._resolved_backend is not None:
            return self._resolved_backend
        source = self._source
        backend = await asyncio.to_thread(
            _resolve_managed_sandbox,
            self._sandbox(),
            self._config(),
            source.connectors,
            source.identity,
            source.selection_fingerprint,
        )
        self._keep(backend)
        return backend

    def _recover(self, backend: SandboxBackendProtocol, error: Exception) -> None:
        if not _is_sandbox_not_found_error(error):
            return
        source = self._source
        config = self._config()
        if _parent_sandbox_scope(config, source.selection_fingerprint) is not None:
            # The lead recovers its own sandbox; helpers must not recreate it.
            return
        sandbox = self._sandbox()
        previous_name = _peek_managed_host_name(
            _sandbox_scope_key(sandbox, config, source.selection_fingerprint)
        )
        try:
            recovered = _recover_managed_sandbox(
                sandbox,
                config,
                backend,
                source.connectors,
                source.identity,
                source.selection_fingerprint,
            )
        except Exception:  # noqa: BLE001
            return
        if recovered is not backend:
            source.proxy_config_sync.invalidate(previous_name)
        if self._resolved_backend is backend:
            self._keep(recovered)

    def _call(
        self,
        operation: Callable[[SandboxBackendProtocol], _ResultT],
        *,
        execute: bool = False,
    ) -> _ResultT:
        from managed_deepagents._sandbox_proxy import run_sync  # noqa: PLC0415

        instead = self._without_box()
        if instead is not None:
            return operation(instead)
        backend = self._backend()
        try:
            if execute:
                run_sync(self._sync_sandbox_proxy_config())
            return operation(backend)
        except Exception as error:
            self._recover(backend, error)
            raise

    async def _acall(
        self,
        operation: Callable[[SandboxBackendProtocol], Awaitable[_ResultT]],
        *,
        execute: bool = False,
    ) -> _ResultT:
        instead = self._without_box()
        if instead is not None:
            return await _settled(operation(instead))
        backend = await self._abackend()
        try:
            if execute:
                await self._sync_sandbox_proxy_config()
            return await _settled(operation(backend))
        except Exception as error:
            await asyncio.to_thread(self._recover, backend, error)
            raise


async def _settled(result: Awaitable[_ResultT]) -> _ResultT:
    # Provider stand-ins may implement an ``a*`` method synchronously.
    if inspect.isawaitable(result):
        return await result
    return cast("_ResultT", result)


def _managed_sandbox_backend(
    sandbox: SandboxDefinition,
    run_config: ManagedRunConfig | None,
    *,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    selection_fingerprint: str | None = None,
) -> _LazyManagedSandboxBackend:
    from managed_deepagents._sandbox_proxy import SandboxProxyConfigSync  # noqa: PLC0415

    return _LazyManagedSandboxBackend(
        _LazySandboxSource(
            sandbox,
            run_config,
            connectors=connectors,
            identity=identity,
            selection_fingerprint=selection_fingerprint,
            proxy_config_sync=SandboxProxyConfigSync(),
        ),
        None,
    )


def _effective_sandbox_run_config(
    run_config: ManagedRunConfig | None,
    *,
    bound_configurable: Mapping[str, object] | None = None,
) -> ManagedRunConfig | None:
    """Merge bound/ambient LangGraph ``configurable`` into the factory-closed run config.

    Sandbox connector hooks (e.g. GitHub credential injection) need
    the live ``langgraph_auth_user``; the compile-time bag often lacks it.
    """
    # The Agent Server hands over an untyped mapping; check before reading.
    closed = run_config if isinstance(run_config, Mapping) else None
    closed_configurable = closed.get("configurable") if closed is not None else None
    seed: dict[str, Any] = {}
    if isinstance(closed_configurable, Mapping):
        seed.update(closed_configurable)
    if bound_configurable is not None:
        seed.update(bound_configurable)
    configurable = resolve_runtime_configurable({"configurable": seed} if seed else closed)
    if configurable is None:
        return run_config
    merged: ManagedRunConfig = closed.copy() if closed is not None else {}
    existing = merged.get("configurable")
    base: dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
    base.update(configurable)
    merged["configurable"] = base
    return merged


def _read_context_instructions(context_backend: BackendProtocol) -> str | None:
    """Load deploy-owned ``instructions.md`` from Context Hub as the system prompt.

    Prefer ``download_files`` (full raw bytes, no line-limit truncation). Fall
    back to ``read`` and use the ``ReadResult.file_data`` content.
    """
    downloaded = _download_context_file(context_backend, "/instructions.md")
    if downloaded is not None:
        return downloaded

    # Large limit: ``read`` defaults to a few hundred lines, which can truncate prompts.
    result = context_backend.read("/instructions.md", offset=0, limit=10_000_000)
    if result.error or result.file_data is None:
        return None
    return result.file_data["content"]


def _resolve_managed_sandbox(
    sandbox: SandboxDefinition,
    config: ManagedRunConfig | None,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    selection_fingerprint: str | None = None,
) -> SandboxBackendProtocol:
    """Resolve (and cache) the per-thread sandbox backend.

    Connectors that implement ``sandbox`` run on create, when a new thread
    starts against a reused sandbox (mode ``"reuse"``), and again on later
    messages in an already-synced thread (mode ``"credentials"``) so token /
    CLI wiring can refresh without re-cloning. Provisioning (``setup.sh``)
    happens at deploy bake, not here.
    """
    key = _sandbox_scope_key(sandbox, config, selection_fingerprint)
    thread_key = _thread_sync_key(config)
    has_sandbox = has_sandbox_connectors(connectors)

    # Serialize same-key resolve/provision; different keys use different locks
    # so unrelated cold creates stay parallel.
    with _sandbox_key_lock(key):
        with _managed_sandbox_lock:
            entry = _managed_sandboxes.get(key)

        if entry is not None:
            # A helper sharing its lead's sandbox only refreshes credentials: the
            # lead's workspace is already set up.
            helper = _parent_sandbox_scope(config, selection_fingerprint) is not None
            if has_sandbox and (thread_key in entry.synced_threads or helper):
                # Same thread: refresh credentials / CLI wiring without re-cloning.
                run_connector_sandbox_hooks(
                    connectors,
                    backend=entry.backend,
                    mode="credentials",
                    config=config,
                    identity=identity,
                )
            elif has_sandbox:
                run_connector_sandbox_hooks(
                    connectors,
                    backend=entry.backend,
                    mode="reuse",
                    config=config,
                    identity=identity,
                )
                entry.synced_threads.add(thread_key)
            return entry.backend

        # Cold create/provision while holding only the per-key lock. Do not hold
        # ``_managed_sandbox_lock`` here — websocket retries can take tens of
        # seconds and would wedge every other Host worker. A helper adopting its
        # lead's sandbox only refreshes credentials; one whose lead hasn't
        # created it yet creates it under the lead's scope for the lead to adopt.
        helper = _parent_sandbox_scope(config, selection_fingerprint) is not None
        backend, host_name, sandbox_id = _create_langsmith_sandbox(
            sandbox,
            config=config,
            connectors=connectors,
            identity=identity,
            sandbox_mode="create",
            adopted_mode="credentials" if helper else "reuse",
            scope_key=key,
            selection_fingerprint=selection_fingerprint,
        )
        entry = _ManagedSandboxEntry(backend, host_name=host_name, sandbox_id=sandbox_id)
        entry.synced_threads.add(thread_key)
        with _managed_sandbox_lock:
            _managed_sandboxes[key] = entry
        return entry.backend


def _is_sandbox_not_found_error(error: Exception) -> bool:
    from langsmith.sandbox import ResourceNotFoundError, SandboxConnectionError  # noqa: PLC0415

    if isinstance(error, ResourceNotFoundError):
        return True
    if isinstance(error, SandboxConnectionError):
        response = getattr(error.__cause__, "response", None)
        return getattr(response, "status_code", None) == HTTPStatus.NOT_FOUND
    return False


def _recover_managed_sandbox(
    sandbox: SandboxDefinition,
    config: ManagedRunConfig | None,
    failed_backend: SandboxBackendProtocol,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    selection_fingerprint: str | None = None,
) -> SandboxBackendProtocol:
    from langsmith.sandbox import ResourceNotFoundError  # noqa: PLC0415

    key = _sandbox_scope_key(sandbox, config, selection_fingerprint)
    with _sandbox_key_lock(key):
        with _managed_sandbox_lock:
            entry = _managed_sandboxes.get(key)
        if entry is None:
            return failed_backend
        current = entry.backend
        if current is not failed_backend or entry.host_name is None:
            return current
        client = _sandbox_client_for(sandbox)
        try:
            existing = _await_if_needed(client.get_sandbox(entry.host_name))
        except ResourceNotFoundError:
            existing = None
        if existing is not None and _sandbox_handle_id(existing) == entry.sandbox_id:
            return current
        backend, host_name, sandbox_id = _create_langsmith_sandbox(
            sandbox,
            config=config,
            connectors=connectors,
            identity=identity,
            scope_key=key,
            selection_fingerprint=selection_fingerprint,
            recovery_name=entry.host_name,
            existing_sandbox=existing,
        )
        replacement = _ManagedSandboxEntry(backend, host_name=host_name, sandbox_id=sandbox_id)
        replacement.synced_threads.add(_thread_sync_key(config))
        with _managed_sandbox_lock:
            _managed_sandboxes[key] = replacement
        return backend


def _replace_managed_sandbox(
    sandbox: SandboxDefinition,
    replacement: SandboxDefinition,
    config: ManagedRunConfig | None,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    selection_fingerprint: str | None = None,
) -> tuple[SandboxBackendProtocol, SandboxReplacement]:
    """Create a fresh box under the thread's box name; keep the old one, stopped and renamed.

    Renaming frees the name for the fresh box, while the old box stays until its
    lifecycle deletes it, under a name that keeps the deployment prefix.
    """
    from managed_deepagents.sandbox import SandboxReplacement  # noqa: PLC0415

    key = _sandbox_scope_key(sandbox, config, selection_fingerprint)
    with _sandbox_key_lock(key):
        with _managed_sandbox_lock:
            entry = _managed_sandboxes.get(key)
        if entry is None or entry.host_name is None:
            raise RuntimeError("This run's sandbox has no name to replace it under")
        name = entry.host_name
        client = _sandbox_client_for(replacement)
        suffix = f"-old-{secrets.token_hex(2)}"
        retired = f"{name[: _MAX_SANDBOX_NAME_LENGTH - len(suffix)]}{suffix}"
        _await_if_needed(client.update_sandbox(name, new_name=retired))
        stop_error: str | None = None
        try:
            _await_if_needed(client.stop_sandbox(retired))
        except Exception as error:  # noqa: BLE001 - the fresh box stands either way; report it
            stop_error = str(error) or type(error).__name__
        backend, host_name, sandbox_id = _create_langsmith_sandbox(
            replacement,
            config=config,
            connectors=connectors,
            identity=identity,
            scope_key=key,
            selection_fingerprint=_sandbox_selection_fingerprint(replacement),
            recovery_name=name,
        )
        fresh = _ManagedSandboxEntry(backend, host_name=host_name, sandbox_id=sandbox_id)
        fresh.synced_threads.add(_thread_sync_key(config))
        with _managed_sandbox_lock:
            _managed_sandboxes[key] = fresh
    return backend, SandboxReplacement(
        old_name=retired,
        new_name=host_name or name,
        old_stopped=stop_error is None,
        stop_error=stop_error,
    )


def _managed_sandbox_of(backend: object) -> _LazyManagedSandboxBackend:
    """The run's managed sandbox behind ``runtime.backend``."""
    if isinstance(backend, CompositeBackend):
        backend = backend.default
    if not isinstance(backend, _LazyManagedSandboxBackend):
        raise TypeError("This run has no managed sandbox")
    return backend


def _thread_sync_key(
    config: ManagedRunConfig | None,
) -> str:
    """Per-thread sync key (always the thread scope key)."""
    return _thread_scope_key(config)


def _sandbox_selection_fingerprint(sandbox: SandboxDefinition) -> str:
    """Return a stable digest of only the sandbox identity options, without exposing them."""
    options = sandbox.options
    registry = options.get("registry")
    mount_config = options.get("mount_config")
    identity = {
        "snapshot_id": options.get("snapshot_id"),
        "snapshot_name": options.get("snapshot_name"),
        "docker_image": options.get("docker_image"),
        "registry_url": registry.get("url") if isinstance(registry, Mapping) else None,
        "vcpus": options.get("vcpus"),
        "mem_bytes": options.get("mem_bytes"),
        "fs_capacity_bytes": options.get("fs_capacity_bytes"),
        "mounts": mount_config.get("mounts") if isinstance(mount_config, Mapping) else None,
    }
    identity = {key: value for key, value in identity.items() if value is not None}
    canonical = _canonical_sandbox_value(identity, set())
    encoded = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _legacy_sandbox_selection_fingerprint(sandbox: SandboxDefinition) -> str:
    """Reconstruct the 0.9.0 selection fingerprint, which hashed every option.

    Used only to reconnect a live 0.9.0 box — new boxes are created under
    :func:`_sandbox_selection_fingerprint`. Remove in 0.10 once 0.9.0 sandboxes
    have idled out.
    """
    canonical = _canonical_sandbox_value(sandbox.options, set())
    encoded = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _canonical_sandbox_value(  # noqa: C901, PLR0911, PLR0912 - exhaustive type boundary
    value: object, ancestors: set[int]
) -> object:
    """Canonicalize supported sandbox option values or reject ambiguity."""
    from managed_deepagents._connections import (  # noqa: PLC0415
        is_connection_reference,
    )
    from managed_deepagents.sandbox import ConnectionHeader  # noqa: PLC0415

    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("sandbox options must contain finite numbers")
        rendered = "-0" if value == 0 and math.copysign(1, value) < 0 else repr(value)
        return ["number", rendered]
    if is_connection_reference(value):
        owner = value.owner
        return ["connection", value.slug, owner["type"], owner.get("id")]
    if isinstance(value, ConnectionHeader):
        return [
            "connection_header",
            value.format,
            value.username,
            _canonical_sandbox_value(value.connection, ancestors),
        ]

    identity = id(value)
    if identity in ancestors:
        raise TypeError("sandbox options must not contain cycles")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("sandbox option mapping keys must be strings")
        ancestors.add(identity)
        try:
            mapping = cast("Mapping[str, object]", value)
            return [
                "object",
                [
                    [key, _canonical_sandbox_value(mapping[key], ancestors)]
                    for key in sorted(mapping)
                ],
            ]
        finally:
            ancestors.remove(identity)
    if isinstance(value, (list, tuple)):
        ancestors.add(identity)
        try:
            return [
                "array",
                [_canonical_sandbox_value(item, ancestors) for item in value],
            ]
        finally:
            ancestors.remove(identity)
    raise TypeError("sandbox options contain an unsupported value")


def _sandbox_scope_key(
    sandbox: SandboxDefinition,
    config: ManagedRunConfig | None,
    selection_fingerprint: str | None = None,
) -> str:
    """The process-level reuse key: the owning thread's, and the selection's unless per thread."""
    from managed_deepagents.sandbox import is_per_thread, owning_thread  # noqa: PLC0415

    thread_id = owning_thread(sandbox.options)
    scope_config: ManagedRunConfig | None = (
        {"configurable": {"thread_id": thread_id}} if thread_id else config
    )
    fingerprint = None if is_per_thread(sandbox.options) else selection_fingerprint
    return _thread_scope_key(scope_config, fingerprint)


def _thread_scope_key(
    config: ManagedRunConfig | None,
    selection_fingerprint: str | None = None,
) -> str:
    """Derive the process-level reuse key for a thread and optional selection.

    An async subagent sharing its lead's sandbox uses the lead's sandbox scope,
    so both resolve, sync, and never invalidate the same sandbox.
    """
    if (shared := _parent_sandbox_scope(config, selection_fingerprint)) is not None:
        return shared
    thread_key = f"thread:{_thread_id_from_config(config)}"
    return f"{thread_key}:sandbox:{selection_fingerprint}" if selection_fingerprint else thread_key


#: ``configurable`` key where deepagents puts an async subagent run's parent reference.
_PARENT_REFERENCE_KEY = "deepagents_parent"
#: Run metadata key (copied from the task thread) naming an async subagent's lead thread.
_PARENT_THREAD_METADATA_KEY = "parent_thread_id"
#: The lead thread this run's caller may access, set once per run by ``parent_run``.
_VERIFIED_LEAD_THREAD: ContextVar[str | None] = ContextVar("mda_verified_lead_thread", default=None)


def _lead_thread(config: ManagedRunConfig | Mapping[str, object] | None) -> str | None:
    """The thread of the agent that launched this async subagent run, if deepagents sent it.

    Read from deepagents' parent reference, or else from the task thread's
    ``parent_thread_id`` metadata, which the Agent Server copies into every run
    on the thread, including runs deepagents didn't start (such as a resume).
    """
    configurable = config.get("configurable") if isinstance(config, Mapping) else None
    reference = (
        configurable.get(_PARENT_REFERENCE_KEY) if isinstance(configurable, Mapping) else None
    )
    thread_id = reference.get("thread_id") if isinstance(reference, Mapping) else None
    if thread_id is None:
        metadata = config.get("metadata") if isinstance(config, Mapping) else None
        thread_id = (
            metadata.get(_PARENT_THREAD_METADATA_KEY) if isinstance(metadata, Mapping) else None
        )
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _parent_sandbox_scope(
    config: ManagedRunConfig | Mapping[str, object] | None,
    selection_fingerprint: str | None = None,
) -> str | None:
    """Return the lead's sandbox scope when this run may share the lead's sandbox.

    MDA keys and names sandboxes by thread scope, so the lead's thread is enough
    to find its sandbox: the same process's cache, or the same deterministic
    name in LangSmith. Requires ``parent_run`` to have verified that this run's
    caller owns the lead's thread; otherwise the run keeps its own sandbox.
    Called on every sandbox operation, so it doesn't log.
    """
    lead_thread = _lead_thread(config)
    if lead_thread is None or _VERIFIED_LEAD_THREAD.get() != lead_thread:
        return None
    scope = f"thread:{lead_thread}"
    return f"{scope}:sandbox:{selection_fingerprint}" if selection_fingerprint else scope


def _thread_id_from_config(config: ManagedRunConfig | None) -> str:
    thread_id = _config_value(config, "thread_id")
    if thread_id is None:
        raise RuntimeError(
            "managed sandboxes require configurable.thread_id (one sandbox per thread)"
        )
    return thread_id


def _config_value(config: Mapping[str, object] | None, key: str) -> str | None:
    if config is None:
        return None
    for container_key in ("metadata", "configurable"):
        container = config.get(container_key)
        if isinstance(container, Mapping):
            value = container.get(key)
            if value is not None:
                return str(value)
    return None


def _managed_deployment_name(env: Mapping[str, str] | None = None) -> str | None:
    """Deployment this runtime belongs to, used to name the sandboxes it creates.

    ``MDA_DEPLOYMENT_NAME`` is injected by ``mda deploy``;
    ``LANGSMITH_DEPLOYMENT_NAME`` is the platform's own value and is preferred
    when present. ``None`` under local ``mda dev``, where sandboxes stay unnamed
    and LangSmith generates a name.
    """
    values = os.environ if env is None else env
    return (
        _string_option(values.get("LANGSMITH_DEPLOYMENT_NAME"))
        or _string_option(values.get("MDA_DEPLOYMENT_NAME"))
        # Sandboxes only: a link built from a given env (Studio's) names the deployment.
        or (_local_dev_deployment_name() if env is None else None)
    )


def _local_dev_deployment_name() -> str | None:
    """A stable name for ``mda dev`` boxes, so a restarted dev server finds its threads' boxes.

    Unique per machine and project directory, so two projects never share a box.
    """
    if os.environ.get("MDA_LOCAL_DEV") != "1":
        return None
    machine = f"{socket.gethostname()}\0{Path.cwd()}"
    return f"mda-dev-{hashlib.sha256(machine.encode()).hexdigest()[:8]}"


def _managed_sandbox_name(deployment: str, scope_key: str) -> str:
    """Deterministic sandbox name for a deployment and thread.

    The thread key is digested rather than embedded so names stay short and
    stable across container restarts. Recipe (``setup.sh`` / bake base) changes
    do **not** rename live boxes — new threads clone the new recipe; live threads
    keep ``/workspace`` until idle reclaim.
    """
    return _managed_sandbox_name_with_deployment_limit(
        deployment,
        scope_key,
        _MAX_PRIMARY_DEPLOYMENT_COMPONENT_LENGTH,
    )


def _legacy_provisioning_fingerprint(
    create_options: Mapping[str, object] | None,
) -> str:
    """Stable string 0.5.x hashed into the sandbox name.

    0.5.x serialized ``[sorted entries, setup_script]``. ``setup.sh`` was never
    a documented name input, so reconnect always uses the empty-script form.
    """
    options = sorted((create_options or {}).items())
    return json.dumps([options, ""], sort_keys=True, default=str)


def _legacy_sandbox_create_kwargs(sandbox: SandboxDefinition) -> dict[str, object]:
    """Create options 0.5.x included in the sandbox-name fingerprint.

    Mirrors the 0.5.x ``_sandbox_create_kwargs`` rest-spread: ``template_name``
    becomes ``snapshot_name``, and managed keys are dropped.
    """
    create_kwargs = {
        key: value
        for key, value in sandbox.options.items()
        if key not in {"scope", "api_key", "default_timeout", "template_name"}
    }
    from managed_deepagents._sandbox_proxy import static_sandbox_proxy_config  # noqa: PLC0415

    if "proxy_config" in create_kwargs:
        create_kwargs["proxy_config"] = static_sandbox_proxy_config(create_kwargs["proxy_config"])
    template_name = _string_option(sandbox.options.get("template_name"))
    if template_name:
        create_kwargs["snapshot_name"] = template_name
    return create_kwargs


def _legacy_managed_sandbox_name(
    deployment: str,
    scope_key: str,
    create_options: Mapping[str, object] | None = None,
) -> str:
    """Reconstruct the 0.5.x Host box name for one thread.

    0.5.x hashed the thread scope plus the options it sent to Host. 0.6.0
    hashes only the thread id, so a live 0.5.x box is invisible under the new
    name. Used only to reconnect that existing box — new threads are created
    under :func:`_managed_sandbox_name`.
    """
    fingerprint = _legacy_provisioning_fingerprint(create_options)
    digest = hashlib.sha256(f"{scope_key}\0{fingerprint}".encode()).hexdigest()[
        :_SANDBOX_DIGEST_LENGTH
    ]
    return f"{deployment}{_SANDBOX_NAME_SEPARATOR}{digest}"


def _managed_sandbox_lookup_names(
    deployment: str,
    scope_key: str,
    sandbox: SandboxDefinition,
    config: ManagedRunConfig | None = None,
    selection_fingerprint: str | None = None,
) -> list[str]:
    """Create name, then 0.9.0 and 0.5.x names that only reconnect a live box."""
    names = [_managed_sandbox_name(deployment, scope_key)]
    if selection_fingerprint:
        with contextlib.suppress(TypeError):
            legacy_fingerprint = _legacy_sandbox_selection_fingerprint(sandbox)
            names.append(
                _managed_sandbox_name(deployment, _thread_scope_key(config, legacy_fingerprint))
            )
    names.append(
        _legacy_managed_sandbox_name(
            deployment,
            scope_key,
            _legacy_sandbox_create_kwargs(sandbox),
        )
    )
    return list(dict.fromkeys(names))


def _send_create_fields(client: LangSmithSandboxClient, fields: Mapping[str, object]) -> None:
    """Add ``fields`` to every create request ``client`` sends.

    The SDK's ``create_sandbox`` takes only the fields it models, so the rest are
    merged into the request body on its transport.
    """
    if not fields:
        return
    http = getattr(client, "_http", None)
    post = getattr(http, "post", None)
    if http is None or not callable(post):
        raise RuntimeError("This langsmith SDK cannot send sandbox _openswe_create_fields")

    def post_with_fields(url: object, *args: object, **kwargs: object) -> object:
        payload = kwargs.get("json")
        if str(url).endswith("/boxes") and isinstance(payload, dict):
            kwargs["json"] = {**fields, **payload}
        return post(url, *args, **kwargs)

    http.post = post_with_fields


def _managed_sandbox_fallback_name(deployment: str, scope_key: str) -> str:
    base = _managed_sandbox_name_with_deployment_limit(
        deployment,
        scope_key,
        _MAX_FALLBACK_DEPLOYMENT_COMPONENT_LENGTH,
    )
    return f"{base}-{secrets.token_hex(4)}"


def _managed_sandbox_name_with_deployment_limit(
    deployment: str,
    scope_key: str,
    deployment_limit: int,
) -> str:
    digest = hashlib.sha256(scope_key.encode()).hexdigest()[:_SANDBOX_DIGEST_LENGTH]
    component = _sandbox_deployment_component(deployment, deployment_limit)
    return f"{component}{_SANDBOX_NAME_SEPARATOR}{digest}"


def _sandbox_deployment_component(deployment: str, max_length: int) -> str:
    if len(deployment) <= max_length:
        return deployment
    digest = hashlib.sha256(deployment.encode()).hexdigest()[:_SANDBOX_DEPLOYMENT_DIGEST_LENGTH]
    prefix_length = max_length - _SANDBOX_DEPLOYMENT_DIGEST_LENGTH - 1
    return f"{deployment[:prefix_length]}-{digest}"


def _fetch_sandbox(client: LangSmithSandboxClient, name: str) -> Sandbox | None:
    """Look up a sandbox by name, treating any failure as "not there"."""
    get_sandbox = getattr(client, "get_sandbox", None)
    if not callable(get_sandbox):
        return None
    try:
        return _await_if_needed(get_sandbox(name))
    except Exception:  # noqa: BLE001 - absent or unreachable; create instead
        return None


def _restart_sandbox(client: LangSmithSandboxClient, name: str, stopped: Sandbox) -> Sandbox | None:
    """Start a stopped sandbox so it can be adopted; ``None`` when that fails."""
    start_sandbox = getattr(client, "start_sandbox", None)
    if not callable(start_sandbox):
        return None
    try:
        # Some SDK versions return nothing on success; it is the same sandbox.
        return _await_if_needed(start_sandbox(name)) or stopped
    except Exception:  # noqa: BLE001 - fall back to creating
        return None


def _adopt_sandbox(client: LangSmithSandboxClient, name: str) -> Sandbox | None:
    """Return the existing sandbox for ``name`` when usable, else ``None``.

    Best-effort by design: this runs on the sandbox hot path, so a lookup failure
    falls back to creating rather than failing the run.
    """
    existing = _fetch_sandbox(client, name)
    if existing is None:
        return None

    status = _string_option(getattr(existing, "status", None))
    normalized = status.lower() if status else None
    # No status reported: assume the SDK handed back a usable sandbox.
    if normalized is None or normalized in _ADOPTABLE_SANDBOX_STATUS:
        return existing
    if normalized == _STOPPED_SANDBOX_STATUS:
        return _restart_sandbox(client, name, existing)
    return None


def _is_sandbox_name_conflict(error: Exception) -> bool:
    """True when a create failed because the sandbox name is already taken."""
    return bool(_SANDBOX_NAME_CONFLICT_RE.search(f"{type(error).__name__}: {error}"))


def _resolve_sandbox_instance(
    client: LangSmithSandboxClient,
    create_kwargs: dict[str, object],
    name: str | None,
    fallback_name: Callable[[], str] | None = None,
    adopt_aliases: Iterable[str] = (),
) -> tuple[Sandbox, bool]:
    """Adopt this deployment's existing sandbox for ``name``, or create it.

    Names are deterministic per scope, so a redeployed or restarted container
    finds the sandbox its predecessor created instead of stranding it until the
    idle TTL fires. When ``name`` is ``None`` (local dev) this is a plain create.

    ``adopt_aliases`` are 0.9.0 and 0.5.x names for this thread: reconnect a box that
    already exists, never create under the old formula.

    Returns the sandbox and whether it was adopted rather than created.
    """
    # Options are a validated bag of provider kwargs, not individually typed.
    create_sandbox = cast("Callable[..., Sandbox]", client.create_sandbox)
    if name is None:
        return _await_if_needed(create_sandbox(**create_kwargs)), False

    # After invalidate, refuse to adopt the same Host name even if delete lags —
    # otherwise recreate loops on the broken box that just failed provision.
    candidates = [name, *[alias for alias in adopt_aliases if alias != name]]
    for candidate in candidates:
        existing = _adopt_sandbox(client, candidate)
        if existing is not None and not _sandbox_name_blocked(candidate):
            return existing, True

    try:
        created = _await_if_needed(create_sandbox(name=name, **create_kwargs))
        _unblock_sandbox_name(name)
        return created, False
    except Exception as error:
        if not _is_sandbox_name_conflict(error):
            raise
        # Either a concurrent container won the create race, or an unusable
        # sandbox (failed, mid-delete) still holds the name. Never adopt a name
        # we just invalidated — fall through to a unique suffix instead.
        if not _sandbox_name_blocked(name):
            winner = _adopt_sandbox(client, name)
            if winner is not None:
                return winner, True
        # Keep the deployment prefix so teardown still finds this sandbox.
        unique = fallback_name() if fallback_name is not None else f"{name}-{secrets.token_hex(4)}"
        created = _await_if_needed(create_sandbox(name=unique, **create_kwargs))
        _unblock_sandbox_name(name)
        return created, False


def _create_replacement_sandbox(
    client: LangSmithSandboxClient, create_kwargs: dict[str, object], name: str
) -> tuple[Sandbox, bool]:
    from langsmith.sandbox import (  # noqa: PLC0415
        ResourceAlreadyExistsError,
        ResourceNameConflictError,
    )

    # Options are a validated bag of provider kwargs, not individually typed.
    create_sandbox = cast("Callable[..., Sandbox]", client.create_sandbox)
    try:
        return _await_if_needed(create_sandbox(name=name, **create_kwargs)), False
    except (ResourceAlreadyExistsError, ResourceNameConflictError):
        return _await_if_needed(client.get_sandbox(name)), True


def _create_langsmith_sandbox(
    sandbox: SandboxDefinition,
    *,
    config: ManagedRunConfig | None = None,
    connectors: list[Connector] | None = None,
    identity: IdentityDefinitionLike | None = None,
    sandbox_mode: Literal["create", "reuse"] = "create",
    adopted_mode: SandboxMode = "reuse",
    scope_key: str,
    selection_fingerprint: str | None = None,
    recovery_name: str | None = None,
    existing_sandbox: Sandbox | None = None,
) -> tuple[SandboxBackendProtocol, str | None, str | None]:
    """Build a LangSmith sandbox, optionally from the baked recipe, and wrap it."""
    from managed_deepagents.sandbox import (  # noqa: PLC0415
        declared_create_fields,
        is_per_thread,
        owning_thread,
    )

    create_kwargs = _sandbox_create_kwargs(sandbox, selection_fingerprint)
    client = _sandbox_client_for(sandbox)
    _send_create_fields(client, declared_create_fields(sandbox.options))
    deployment = _managed_deployment_name()
    if not deployment:
        lookup_names = []
    elif is_per_thread(sandbox.options) or owning_thread(sandbox.options):
        # Names from before these options never named a box kept this way.
        lookup_names = [_managed_sandbox_name(deployment, scope_key)]
    else:
        lookup_names = _managed_sandbox_lookup_names(
            deployment, scope_key, sandbox, config, selection_fingerprint
        )
    sandbox_name = recovery_name or (lookup_names[0] if lookup_names else None)
    fallback_name: Callable[[], str] | None = None
    if deployment:
        fallback_name = partial(_managed_sandbox_fallback_name, deployment, scope_key)
    if existing_sandbox is not None:
        langsmith_sandbox, adopted = existing_sandbox, True
    elif recovery_name:
        langsmith_sandbox, adopted = _create_replacement_sandbox(
            client, create_kwargs, recovery_name
        )
    else:
        langsmith_sandbox, adopted = _resolve_sandbox_instance(
            client, create_kwargs, sandbox_name, fallback_name, lookup_names[1:]
        )
    host_name = _sandbox_handle_name(langsmith_sandbox) or sandbox_name
    logger.info(
        "sandbox resolve: %s name=%r id=%r scope=%r",
        "adopted" if adopted else "created",
        host_name,
        _sandbox_handle_id(langsmith_sandbox),
        scope_key,
    )
    # The optional SDK client returns its provider handle across a dynamic boundary.
    backend = LangSmithSandbox(sandbox=langsmith_sandbox)

    configured_timeout = cast("int | None", sandbox.options.get("default_timeout"))
    resolved = backend
    if configured_timeout is not None and hasattr(resolved, "_default_timeout"):
        resolved._default_timeout = configured_timeout  # noqa: SLF001

    run_connector_sandbox_hooks(
        connectors,
        backend=resolved,
        # An adopted sandbox already ran create-time connector hooks in an
        # earlier process, so treat it as a reuse for connector purposes.
        mode=adopted_mode if adopted else sandbox_mode,
        config=config,
        identity=identity,
    )

    return resolved, host_name, _sandbox_handle_id(langsmith_sandbox)


def _string_option(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
