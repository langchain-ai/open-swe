"""Run a compiled Python Managed Deep Agent eval artifact inside Harbor."""

# ruff: noqa: INP001

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = "mda.eval-agent-result/v1"


def main() -> int:
    instruction_path = Path(sys.argv[1])
    project_dir = Path(os.environ["MDA_EVAL_PROJECT_DIR"])
    model_name = os.environ.get("MDA_EVAL_MODEL_NAME") or None
    model_override = os.environ.get("MDA_EVAL_MODEL_OVERRIDE") or None
    graph_name = os.environ.get("MDA_EVAL_GRAPH", "agent")
    summary_path = Path(os.environ["MDA_EVAL_SUMMARY_PATH"])
    result_path = Path(os.environ["MDA_EVAL_RESULT_PATH"])
    app_dir = os.environ.get("MDA_EVAL_APP_DIR", "/app")
    session_id = os.environ.get("HARBOR_SESSION_ID") or "mda-eval"

    instruction = instruction_path.read_text(encoding="utf-8")
    os.chdir(project_dir)
    identity = _load_identity_fixture()
    # Pass model only at graph compile time. Including it in invoke
    # `configurable` can make LangChain treat `openai:…` as a raw model id.
    graph = _load_graph(
        project_dir,
        graph_name,
        model=model_override,
        app_dir=app_dir,
        session_id=session_id,
        identity=identity,
    )
    with _langsmith_tracing_context():
        result = _invoke_graph(
            graph,
            instruction,
            app_dir=app_dir,
            session_id=session_id,
            identity=identity,
        )
    answer = _extract_answer(result)

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(_json_safe(result), indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema_version": _SCHEMA_VERSION,
        "runtime": "python",
        "answer_written": answer,
        "model": model_name,
        "usage": _extract_usage(result),
    }
    if isinstance(identity, dict):
        caller = identity.get("user")
        if isinstance(caller, dict) and isinstance(caller.get("id"), str):
            # Observability for identity injection (not under /app).
            summary["identity_user_id"] = caller["id"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


def _load_graph(
    project_dir: Path,
    graph_name: str,
    *,
    model: str | None,
    app_dir: str,
    session_id: str,
    identity: dict[str, Any] | None,
) -> Any:
    manifest = json.loads((project_dir / "langgraph.json").read_text(encoding="utf-8"))
    graph_ref = manifest["graphs"][graph_name]
    module_ref, export_name = graph_ref.split(":", 1)
    module_path = project_dir / module_ref.removeprefix("./")
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        msg = f"unable to import graph module {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_path.stem] = module
    spec.loader.exec_module(module)
    factory = getattr(module, export_name)
    return _resolve(
        factory(_config(model=model, app_dir=app_dir, session_id=session_id, identity=identity))
    )


def _invoke_graph(
    graph: Any,
    instruction: str,
    *,
    app_dir: str,
    session_id: str,
    identity: dict[str, Any] | None,
) -> Any:
    payload = {"messages": [{"role": "user", "content": instruction}]}
    config = _config(app_dir=app_dir, session_id=session_id, identity=identity)
    # Invoke asynchronously like `mda dev` and the hosted runtime do. MCP
    # connector tools are async-only, so a sync `invoke` fails the first tool
    # call with `StructuredTool does not support sync invocation`.
    if hasattr(graph, "ainvoke"):
        return asyncio.run(graph.ainvoke(payload, config=config))
    if hasattr(graph, "invoke"):
        return graph.invoke(payload, config=config)
    if callable(graph):
        return _resolve(graph(payload, config=config))
    msg = "compiled graph is not invokable"
    raise TypeError(msg)


def _langsmith_tracing_context() -> AbstractContextManager[Any]:
    parent = os.environ.get("HARBOR_LANGSMITH_PARENT")
    if not parent:
        return nullcontext()

    from langsmith.run_helpers import tracing_context  # noqa: PLC0415

    headers = {"langsmith-trace": parent}
    baggage = os.environ.get("HARBOR_LANGSMITH_BAGGAGE")
    if baggage:
        headers["baggage"] = baggage
    return tracing_context(parent=headers, enabled=True)


def _resolve(result: Any) -> Any:
    """Await `result` when a bare callable graph returns a coroutine."""
    if inspect.isawaitable(result):
        return asyncio.run(_await(result))
    return result


async def _await(awaitable: Any) -> Any:
    return await awaitable


def _config(
    *,
    model: str | None = None,
    app_dir: str,
    session_id: str,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configurable: dict[str, Any] = {
        "cwd": app_dir,
        "thread_id": session_id,
    }
    if model:
        configurable["model"] = model
    _apply_identity_fixture(configurable, identity)
    return {"configurable": configurable}


def _apply_identity_fixture(configurable: dict[str, Any], identity: dict[str, Any] | None) -> None:
    """Map checked-in eval ``identity.json`` onto ``langgraph_auth_user``."""
    if not isinstance(identity, dict):
        return
    caller = identity.get("user")
    caller_id = caller.get("id") if isinstance(caller, dict) else None
    if not isinstance(caller_id, str) or not caller_id.strip():
        return
    user: dict[str, Any] = {
        "identity": caller_id,
        "permissions": [],
        "mda_user_id": caller_id,
        "mda_user_kind": "service" if caller.get("kind") == "service" else "person",
    }
    if isinstance(caller.get("email"), str):
        user["mda_user_email"] = caller["email"]
    declared_groups = identity.get("groups")
    groups = (
        [group for group in declared_groups if isinstance(group, str)]
        if isinstance(declared_groups, list)
        else []
    )
    if groups:
        user["mda_groups"] = groups
    claims = identity.get("claims")
    if isinstance(claims, dict):
        user["mda_claims"] = claims
    configurable["langgraph_auth_user"] = user
    source = identity.get("source")
    if isinstance(source, dict) and isinstance(source.get("provider"), str):
        configurable["mda_source_provider"] = source["provider"]


def _load_identity_fixture() -> dict[str, Any] | None:
    identity_path = os.environ.get("MDA_EVAL_IDENTITY_PATH")
    if not identity_path:
        return None
    path = Path(identity_path)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _extract_answer(value: Any) -> str:
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list) and messages:
            return _message_text(messages[-1])
        for key in ("answer", "output", "content"):
            item = value.get(key)
            if isinstance(item, str):
                return item
    return _message_text(value)


def _message_text(value: Any) -> str:
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    if isinstance(value, str):
        return value
    return json.dumps(_json_safe(value), sort_keys=True)


def _extract_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    usage = value.get("usage")
    return usage if isinstance(usage, dict) else None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    for method_name in ("model_dump", "dict"):
        dump = getattr(value, method_name, None)
        if callable(dump):
            return _json_safe(dump())
    if hasattr(value, "content"):
        return {"content": _json_safe(value.content)}
    return repr(value)


if __name__ == "__main__":
    raise SystemExit(main())
