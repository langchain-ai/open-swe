"""The reviewer's tools.

Most are calls to the Open SWE backend as the current run. The ones that act on
the sandbox run here, because this deployment owns it. Names, descriptions and
argument schemas come from ``spec.json``, which the backend exports from its
tools (``scripts/export_remote_reviewer_spec.py``), so the model sees exactly
what the in-process reviewer shows it.
"""

import json
import re
from importlib.resources import files
from typing import Final, TypedDict

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from managed_deepagents import ManagedToolRuntime
from pydantic import JsonValue

from open_swe_reviewer.backend import BackendCallError, call_backend
from open_swe_reviewer.middleware import RunState, prepared_run

# Long enough for publish_review, which posts the review and settles the check run.
_TOOL_TIMEOUT_SECONDS: Final = 600.0
_MAX_CHANGED_FILES: Final = 200
_DIFF_FILE_HEADER_RE: Final = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")


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
    sandbox_tools: list[ToolSpec]
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


async def fetch_review_diff(runtime: ToolRuntime[None, RunState]) -> dict[str, JsonValue]:
    """Write this run's review diff to the sandbox and describe it."""
    prepared = prepared_run(runtime.state)
    checkout = prepared.checkout if prepared is not None else None
    if checkout is None:
        return {"success": False, "error": "review repository unavailable"}
    if not isinstance(runtime, ManagedToolRuntime) or runtime.backend is None:
        return {"success": False, "error": "review sandbox unavailable"}
    uploads = await runtime.backend.aupload_files(
        [(checkout.diff_path, checkout.diff_text.encode())]
    )
    if uploads and uploads[0].error:
        return {"success": False, "error": f"failed to materialize review diff: {uploads[0].error}"}
    paths = (
        match.group("b")
        for line in checkout.diff_text.splitlines()
        if (match := _DIFF_FILE_HEADER_RE.match(line))
    )
    all_files = list(dict.fromkeys(paths))
    shown = all_files[:_MAX_CHANGED_FILES]
    return {
        "success": True,
        "path": checkout.diff_path,
        "bytes": len(checkout.diff_text.encode()),
        "files": shown,
        "file_count": len(all_files),
        "files_truncated": len(all_files) > len(shown),
        "base_sha": checkout.diff_base_ref,
        "head_sha": checkout.head_sha,
        "cached": False,
    }


_SANDBOX_TOOLS: Final = {"fetch_review_diff": fetch_review_diff}


def _sandbox_tool(spec: ToolSpec) -> BaseTool:
    return StructuredTool.from_function(
        coroutine=_SANDBOX_TOOLS[spec["name"]],
        name=spec["name"],
        description=spec["description"],
    )


def reviewer_tools() -> list[BaseTool]:
    spec = runtime_spec()
    return [
        *(_backend_tool(tool) for tool in spec["tools"]),
        *(_sandbox_tool(tool) for tool in spec["sandbox_tools"]),
    ]
