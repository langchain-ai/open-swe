"""Run a compiled Python Managed Deep Agent eval artifact inside Harbor."""

# ruff: noqa: INP001

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager, nullcontext, suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

_SCHEMA_VERSION = "mda.eval-agent-result/v1"
_SERVER_START_TIMEOUT_SECONDS = 120
_IDLE_POLL_SECONDS = 0.5


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
    manifest = _manifest(project_dir)
    # mda names each async subagent's graph `<lead graph>--<subagent>`.
    has_async_subagents = any(name.startswith(f"{graph_name}--") for name in manifest["graphs"])
    if has_async_subagents:
        result = asyncio.run(
            _run_on_agent_server(
                project_dir,
                manifest,
                graph_name,
                instruction,
                identity=identity,
                log_path=summary_path.parent / "agent-server.log",
            )
        )
    else:
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


def _manifest(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "langgraph.json").read_text(encoding="utf-8"))


async def _run_on_agent_server(
    project_dir: Path,
    manifest: dict[str, Any],
    graph_name: str,
    instruction: str,
    *,
    identity: dict[str, Any] | None,
    log_path: Path,
) -> Any:
    from langgraph_sdk import get_client  # noqa: PLC0415

    server_dir = Path(tempfile.mkdtemp(prefix="mda-eval-server-"))
    config_path = _write_server_config(server_dir, project_dir, manifest, identity)
    port = _free_port()
    server = _start_server(config_path, port, server_dir, log_path)
    try:
        url = f"http://127.0.0.1:{port}"
        await _wait_for_server(url, server, log_path)
        return await _run_lead(
            get_client(url=url), graph_name, instruction, identity=identity, log_path=log_path
        )
    finally:
        _stop(server)
        shutil.rmtree(server_dir, ignore_errors=True)


async def _run_lead(
    client: Any,
    graph_name: str,
    instruction: str,
    *,
    identity: dict[str, Any] | None,
    log_path: Path,
) -> Any:
    thread_id = (await client.threads.create())["thread_id"]
    run = await client.runs.create(
        thread_id,
        graph_name,
        input={"messages": [{"role": "user", "content": instruction}]},
        config=_server_run_config(identity),
        headers=_harbor_trace_headers(nest_under_parent=True),
    )
    await client.runs.join(thread_id, run["run_id"])
    run = await client.runs.get(thread_id, run["run_id"])
    if run["status"] in ("error", "timeout"):
        msg = f"the agent's run ended with status {run['status']!r}"
        raise RuntimeError(f"{msg}:\n{_tail(log_path)}")
    await _wait_until_idle(client)
    thread = await client.threads.get(thread_id)
    if thread["status"] == "error":
        msg = "the agent's last run failed"
        raise RuntimeError(f"{msg}:\n{_tail(log_path)}")
    state = await client.threads.get_state(thread_id)
    return state["values"]


def _write_server_config(
    server_dir: Path,
    project_dir: Path,
    manifest: dict[str, Any],
    identity: dict[str, Any] | None,
) -> Path:
    # The build's config, outside the graded workspace. The runner env already
    # holds `.env`, and a trial serves no HTTP routes or the project's own auth.
    config = {key: value for key, value in manifest.items() if key not in ("env", "http", "auth")}
    config["dependencies"] = [str(project_dir)]
    config["graphs"] = {
        name: str(project_dir / ref.removeprefix("./")) for name, ref in manifest["graphs"].items()
    }
    user = _identity_user(identity)
    if user is not None:
        auth_path = server_dir / "eval_auth.py"
        auth_path.write_text(
            "import json\n"
            "from langgraph_sdk import Auth\n\n"
            "auth = Auth()\n"
            f"_USER = json.loads({json.dumps(json.dumps(user))})\n\n\n"
            "@auth.authenticate\n"
            "async def authenticate():\n"
            "    return dict(_USER)\n",
            encoding="utf-8",
        )
        # deepagents launches async subagents as Studio.
        config["auth"] = {"path": f"{auth_path}:auth", "disable_studio_auth": True}
    config_path = server_dir / "langgraph.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


def _start_server(
    config_path: Path, port: int, server_dir: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(  # noqa: S603
            [
                _langgraph_cli(),
                "dev",
                "--config",
                str(config_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-browser",
                "--no-reload",
                # The Python dev server runs one job at a time unless told otherwise,
                # and async subagents run alongside the lead.
                "--n-jobs-per-worker",
                "10",
            ],
            cwd=server_dir,
            env=_server_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _server_env() -> dict[str, str]:
    # Harbor sends unset vars as "", and the server rejects LANGSMITH_TRACING="".
    return {key: value for key, value in os.environ.items() if value}


def _langgraph_cli() -> str:
    beside = Path(sys.executable).parent / "langgraph"
    found = str(beside) if beside.is_file() else shutil.which("langgraph")
    if found is None:
        msg = "the langgraph CLI is not installed in the trial; it runs async subagents"
        raise RuntimeError(msg)
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_server(url: str, server: subprocess.Popen[bytes], log_path: Path) -> None:
    import httpx  # noqa: PLC0415

    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            if server.poll() is not None:
                msg = f"the local Agent Server exited with status {server.returncode}"
                raise RuntimeError(f"{msg}:\n{_tail(log_path)}")
            try:
                if (await client.get(f"{url}/ok")).status_code == 200:  # noqa: PLR2004
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.25)
    msg = f"the local Agent Server did not start in {_SERVER_START_TIMEOUT_SECONDS} s"
    raise RuntimeError(f"{msg}:\n{_tail(log_path)}")


def _server_run_config(identity: dict[str, Any] | None) -> dict[str, Any]:
    configurable: dict[str, Any] = {}
    provider = _identity_source_provider(identity)
    if provider is not None:
        configurable["mda_source_provider"] = provider
    return {"configurable": configurable}


def _harbor_trace_headers(*, nest_under_parent: bool = False) -> dict[str, str] | None:
    """The Harbor trial's trace headers, flagged for MDA to nest a run under when asked."""
    parent = os.environ.get("HARBOR_LANGSMITH_PARENT")
    if not parent:
        return None
    harbor_baggage = os.environ.get("HARBOR_LANGSMITH_BAGGAGE")
    if not nest_under_parent:
        return {
            "langsmith-trace": parent,
            **({"baggage": harbor_baggage} if harbor_baggage else {}),
        }
    metadata: dict[str, Any] = {}
    baggage = []
    for item in (harbor_baggage or "").split(","):
        key, _, value = item.partition("=")
        if key == "langsmith-metadata":
            with suppress(ValueError):
                decoded = json.loads(unquote(value))
                metadata = decoded if isinstance(decoded, dict) else {}
        elif item:
            baggage.append(item)
    metadata["ls_nest_under_parent"] = True
    baggage.append(f"langsmith-metadata={quote(json.dumps(metadata))}")
    return {"langsmith-trace": parent, "baggage": ",".join(baggage)}


async def _wait_until_idle(client: Any) -> None:
    idle_checks = 0
    while idle_checks < 2:  # noqa: PLR2004
        await asyncio.sleep(_IDLE_POLL_SECONDS)
        idle_checks = idle_checks + 1 if not await _has_active_runs(client) else 0


async def _has_active_runs(client: Any) -> bool:
    # A thread stays busy from run creation until its last active run ends.
    return bool(await client.threads.search(status="busy", limit=1))


def _stop(server: subprocess.Popen[bytes]) -> None:
    if server.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(server.pid, signal.SIGTERM)
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(server.pid, signal.SIGKILL)
        server.wait()


def _tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


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
    headers = _harbor_trace_headers()
    if headers is None:
        return nullcontext()

    from langsmith.run_helpers import tracing_context  # noqa: PLC0415

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
    user = _identity_user(identity)
    if user is None:
        return
    configurable["langgraph_auth_user"] = user
    provider = _identity_source_provider(identity)
    if provider is not None:
        configurable["mda_source_provider"] = provider


def _identity_user(identity: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(identity, dict):
        return None
    caller = identity.get("user")
    caller_id = caller.get("id") if isinstance(caller, dict) else None
    if not isinstance(caller_id, str) or not caller_id.strip():
        return None
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
    return user


def _identity_source_provider(identity: dict[str, Any] | None) -> str | None:
    source = identity.get("source") if isinstance(identity, dict) else None
    provider = source.get("provider") if isinstance(source, dict) else None
    return provider if isinstance(provider, str) else None


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
    content = value.get("content") if isinstance(value, dict) else getattr(value, "content", None)
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
