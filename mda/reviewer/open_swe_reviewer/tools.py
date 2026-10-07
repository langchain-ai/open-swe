"""The reviewer's tools, each a call to the Open SWE backend as the current run.

Names, descriptions and argument schemas come from ``spec.json``, which the
backend exports from the tools it serves (``scripts/export_remote_reviewer_spec.py``),
so the model sees exactly what the in-process reviewer shows it.
"""

import json
from importlib.resources import files
from typing import Final, TypedDict

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import JsonValue

from open_swe_reviewer.backend import BackendCallError, call_backend

# Long enough for publish_review, which posts the review and settles the check run.
_TOOL_TIMEOUT_SECONDS: Final = 600.0


class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class SubagentSpec(TypedDict):
    name: str
    description: str
    system_prompt: str


class RuntimeSpec(TypedDict):
    tools: list[ToolSpec]
    subagent: SubagentSpec


def runtime_spec() -> RuntimeSpec:
    text = files("open_swe_reviewer").joinpath("spec.json").read_text(encoding="utf-8")
    spec: RuntimeSpec = json.loads(text)
    return spec


def _backend_tool(spec: ToolSpec) -> BaseTool:
    name = spec["name"]

    async def call(**arguments: JsonValue) -> JsonValue:
        try:
            result = await call_backend(name, arguments, timeout_seconds=_TOOL_TIMEOUT_SECONDS)
        except BackendCallError as exc:
            raise ToolException(
                f"The Open SWE backend did not complete {name}: {exc}. "
                "It may already have taken effect, so check before retrying."
            ) from exc
        content = result.get("content")
        if result.get("status") == "error":
            raise ToolException(content if isinstance(content, str) else json.dumps(content))
        return content

    return StructuredTool.from_function(
        coroutine=call,
        name=name,
        description=spec["description"],
        args_schema=spec["parameters"],
        handle_tool_error=True,
    )


def backend_tools() -> list[BaseTool]:
    return [_backend_tool(spec) for spec in runtime_spec()["tools"]]
