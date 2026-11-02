"""The catalog MDA builds a run from, and the settings its later calls read from its token.

Both are resolved once, as the backend starts the run, from what only the backend holds:
the run's kind resolves its tools, models, subagents and loop rules (``RunKind.catalog``),
and the backend adds the sandbox MDA keeps for the thread and a grant of each operation. The catalog lists every tool but the run's integrations, which
the context names only: every tool is kept by digest (``run_tool_sets``), which
``tool_specs``, ``search_tools`` and ``resolve_tool`` read.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import (
    SEARCH_TOOLS,
    Catalog,
    FilesSpec,
    ModelSpec,
    RunModels,
    SandboxSpec,
    SubagentSpec,
    ToolSpec,
)

from openswe.agent_api.connections import operation_connections
from openswe.agent_api.git_metadata import trace_actor
from openswe.agent_api.operations import Injections
from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.run_sandbox import RunSandbox
from openswe.agent_api.runs import run_config
from openswe.agent_api.tokens import AGENT_TURN_KEY, AgentTurnSettings, RemoteRun, RunSettings
from openswe.agent_api.tool_sets import ToolSet
from openswe.dashboard.options import model_supports_images
from openswe.dispatch import COMPLETION_WEBHOOK_URL
from openswe.incidents.runtime import INCIDENT_SOURCE
from openswe.prompts import common_prompt, prompt
from openswe.run_config import RunConfig
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.sandboxes.tool_runtime import EXCLUDED_TOOLS, ModelChoice, ToolSurface
from openswe.server import (
    DEEP_AGENT_TOOL_NAMES,
    call_seconds,
    code_name,
    code_only,
    direct_and_from_code,
    fallback_model_choice,
)
from openswe.threads.access import agent_version_metadata
from openswe.utils.model import provider_model_kwargs

# MDA's own deep agent runs subagents and code.
_SYNC_SUBAGENT_TOOL: Final = "task"
_MDA_TOOLS: Final = frozenset({_SYNC_SUBAGENT_TOOL, "eval"})
# deepagents' async subagent tools. Open SWE launches a task worker with the first,
# wherever the thread may delegate; its task tools track the worker from then on.
_LAUNCH_WORKER_TOOL: Final = "start_async_task"
_ASYNC_TASK_TOOLS: Final = frozenset(
    {
        _LAUNCH_WORKER_TOOL,
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    }
)
# Tools the model labels each call of, for the dashboard to show in place of a bare name.
_DESCRIBED_TOOLS: Final = ("eval",)
# The file and shell tools MDA's own filesystem middleware gives the run.
_FILE_TOOLS: Final = frozenset(DEEP_AGENT_TOOL_NAMES) - _MDA_TOOLS

_json_object = TypeAdapter(dict[str, JsonValue])


def model_spec(model_id: str, kwargs: Mapping[str, object]) -> ModelSpec:
    """``model_id`` as the runtime calls it: whether it reads images, and its fallback."""
    fallback = fallback_model_choice(model_id)
    return ModelSpec(
        model_id=model_id,
        kwargs=_json_object.validate_python(dict(kwargs)),
        reads_images=model_supports_images(model_id),
        fallback=None
        if fallback is None
        else ModelSpec(
            model_id=fallback.model_id,
            kwargs=_json_object.validate_python(dict(fallback.kwargs)),
            reads_images=model_supports_images(fallback.model_id),
        ),
    )


def chosen_model(model_id: str, effort: object) -> ModelSpec:
    """A model the run calls by id, such as one a person asked for, at ``effort``."""
    kwargs = provider_model_kwargs(
        model_id, effort if isinstance(effort, str) else None, max_tokens=DEFAULT_LLM_MAX_TOKENS
    )
    return model_spec(model_id, kwargs)


@dataclass(frozen=True)
class RunStart:
    """What the backend resolves for a run as it starts it."""

    catalog: Catalog
    settings: RunSettings


class RunCatalog:
    """A run's catalog as its kind resolves it, before MDA starts the run."""

    def __init__(
        self, kind: RunKind, run: RemoteRun, surface: ToolSurface, config: RunnableConfig
    ) -> None:
        self.kind = kind
        self.run = run
        self.surface = surface
        self.config = config

    @classmethod
    async def of(cls, kind: RunKind, run: RemoteRun) -> RunCatalog:
        # Resolved from the run as dispatch stamped it, never from the settings it decides.
        config = run_config(run, settled=False)
        surface = ToolSurface()
        await kind.catalog(config, surface)
        await surface.prepare()
        return cls(kind, run, surface, config)

    async def start(self) -> RunStart:
        """What MDA builds the run from, and the settings its calls read."""
        tools, catalog = await asyncio.gather(self.tool_set().keep(), self.granted())
        settings = self.settings().model_copy(update={"tools": tools})
        return RunStart(catalog=catalog, settings=settings)

    def _backend_tools(self) -> dict[str, BaseTool]:
        return {name: tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS}

    def _unoffered_delegation_tools(self) -> frozenset[str]:
        if self.surface.delegates:
            # A coordinator delegates only through its workers: no synchronous subagents,
            # not even the general-purpose one deepagents adds to any agent without them.
            return (_ASYNC_TASK_TOOLS - {_LAUNCH_WORKER_TOOL}) | {_SYNC_SUBAGENT_TOOL}
        return _ASYNC_TASK_TOOLS

    def _excluded(self) -> frozenset[str]:
        return frozenset(
            ((self.surface.excluded | EXCLUDED_TOOLS) - _MDA_TOOLS)
            | self._unoffered_delegation_tools()
        )

    def _specs(self) -> list[ToolSpec]:
        tools = self._backend_tools()
        return [
            ToolSpec(
                name=described.name,
                description=described.description,
                parameters=described.parameters,
                reads=(injections := Injections.of(tools[described.name])).reads,
                reads_all=injections.reads_all,
                code_name=code_name(described.name),
                seconds=call_seconds(described.name),
                connections=operation_connections(described.name, self.config),
            )
            for described in self.surface.catalog()
            if described.name in tools
        ]

    def tool_set(self) -> ToolSet:
        """Every tool the run may call, which the backend keeps for the run to read."""
        return ToolSet(self._specs(), self._excluded())

    def models(self) -> RunModels:
        models = self.surface.models
        if models is None:
            raise RuntimeError("Starting the run picked no models")

        def spec(choice: ModelChoice) -> ModelSpec:
            return model_spec(choice.model_id, choice.kwargs)

        return RunModels(
            model=spec(models.main),
            subagent_model=spec(models.subagent),
            use_gateway=models.use_gateway,
            routes={route: spec(choice) for route, choice in models.routes.items()},
            vision=None if models.vision is None else spec(models.vision),
        )

    def build(self) -> Catalog:
        surface = self.surface
        tools = self._backend_tools()
        excluded = self._excluded()
        # Called from MDA's code mode, never offered to the model as schemas.
        from_code_only = {
            name
            for name in tools.keys() - excluded
            if code_only(name) and (surface.code_mode or name in surface.integration_names)
        }
        # Integrations stay on the backend, which finds them for the run's search and its
        # code mode: listing hundreds of them would put about a megabyte in every run's
        # context. The run's own tools stay listed, so the model knows them unsearched.
        withheld = from_code_only & set(surface.integration_names)
        searches = surface.code_mode or bool(withheld)
        offered = tools.keys() | _FILE_TOOLS | ({SEARCH_TOOLS} if searches else set())
        # Code mode calls tools past the model's list, so what the run excludes stays out.
        from_code = offered - excluded if surface.code_mode else frozenset()
        return Catalog(
            operations=[spec for spec in self._specs() if spec.name not in withheld],
            mcp_servers=list(surface.mcp_servers),
            subagents=[
                SubagentSpec(
                    name=subagent.name,
                    description=subagent.description,
                    system_prompt=common_prompt(subagent.system_prompt),
                    tools=None
                    if subagent.tools is None
                    else [name for name in subagent.tools if name in offered],
                )
                for subagent in (() if surface.delegates else surface.subagents)
            ],
            models=self.models(),
            excluded=sorted(excluded),
            code_only=sorted(from_code_only - withheld),
            # Offered to the model, and callable from MDA's code mode too.
            also_from_code=sorted(name for name in from_code if direct_and_from_code(name)),
            described={name: prompt("tools/display-description") for name in _DESCRIBED_TOOLS},
            files=FilesSpec(
                backend_paths=list(surface.backend_paths),
                artifacts_root=surface.artifacts_root,
                checks=list(surface.file_checks),
            ),
            incident_evidence=surface.loop.incident,
            worker_webhook=COMPLETION_WEBHOOK_URL if surface.delegates else None,
            skill_sources=list(surface.skill_sources),
            inbox=surface.inbox,
            loop=surface.loop.model_copy(update={"deployment_prompt": common_prompt()}),
            search_tools=searches,
            search_tools_description=prompt(f"tools/{SEARCH_TOOLS}") if searches else None,
            model_call_limit=surface.model_call_limit,
        )

    async def granted(self) -> Catalog:
        """The catalog, with who the run acts for, the sandbox MDA creates for it, and the
        grant of each operation to the run."""
        sandbox = await RunSandbox(self.config, reviews=self.kind.reviews).managed()
        spec = (
            None if sandbox is None else SandboxSpec.model_validate(sandbox.model_dump(mode="json"))
        )
        catalog = self.build()
        return catalog.model_copy(
            update={
                "trace_metadata": {**trace_actor(self.config), **agent_version_metadata()},
                "sandbox": spec,
                "operations": [
                    operation.model_copy(update={"grant": self.run.grant(operation.name)})
                    for operation in catalog.operations
                ],
            }
        )

    def settings(self) -> RunSettings:
        """What starting the run decided, which its later calls read from its token."""
        cfg = RunConfig.from_config(self.config)
        configurable = self.config.get("configurable") or {}
        turn = configurable.get(AGENT_TURN_KEY)
        slack_thread = configurable.get("slack_thread")
        return RunSettings(
            source=cfg.source,
            draft_prs=cfg.draft_prs,
            model_id=cfg.resolved_agent_model_id,
            effort=cfg.resolved_agent_effort,
            agent=None if turn is None else AgentTurnSettings.model_validate(turn),
            slack_thread=_json_object.validate_python(slack_thread)
            if cfg.source == INCIDENT_SOURCE and isinstance(slack_thread, Mapping)
            else None,
        )
