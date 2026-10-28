"""Browser use: the tools and sessions around Stagehand's facade, without a browser."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Self, cast
from unittest.mock import MagicMock

import httpx
import pytest
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import HumanMessage, SystemMessage, convert_to_messages
from langgraph.runtime import ExecutionInfo
from langgraph.types import Command
from stagehand_middleware.facade import (
    StagehandFacadeExecutionError,
    StagehandFacadeInputError,
    facade_instructions,
    facade_tools,
)

from managed_deepagents import browser as b
from managed_deepagents import define_sandbox
from managed_deepagents import runtime as mda_runtime
from managed_deepagents._channels.assistant_text import is_task_notification
from managed_deepagents._managed_tools import ManagedToolRuntime
from managed_deepagents.define_browser import BrowserDefinition, define_browser
from managed_deepagents.identity import ManagedServerInfo, PrincipalView


class _Backend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self.files.update(files)
        return [FileUploadResponse(path=path, error=None) for path, _ in files]


class _SandboxBackend(_Backend, SandboxBackendProtocol):
    def __init__(self, box_id: str = "box") -> None:  # "pending:…" until MDA makes it
        super().__init__()
        self.box_id = box_id

    @property
    def id(self) -> str:
        return self.box_id

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: ASYNC109 - the protocol's signature
        return ExecuteResponse(output="", exit_code=0)


def _runtime(
    *, thread: str = "t1", user: str | None = None, kind: str = "person", backend: object = None
) -> ManagedToolRuntime:
    principal = cast("PrincipalView", SimpleNamespace(id=user, kind=kind)) if user else None
    return ManagedToolRuntime(
        state={},
        context=None,
        config={},
        execution_info=ExecutionInfo(
            checkpoint_id="c", checkpoint_ns="", task_id="task", thread_id=thread
        ),
        stream_writer=lambda _: None,
        tool_call_id="call-1",
        store=None,
        server_info=ManagedServerInfo(assistant_id="a", graph_id="g", principal=principal),
        backend=cast("Any", backend),
    )


class _Facade:
    """Stands in for ``StagehandFacadeTools``: records calls, returns ``result`` or raises it."""

    def __init__(self, artifacts: str) -> None:
        self.artifacts = artifacts
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result: object = {"ok": True}
        self.saves: dict[str, bytes] = {}

    def _answer(self, name: str, **kwargs: object) -> object:
        self.calls.append((name, kwargs))
        for relative, data in self.saves.items():
            target = Path(self.artifacts, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def run(self, **kwargs: object) -> object:
        return self._answer("run", **kwargs)

    async def snapshot(self, **kwargs: object) -> object:
        return self._answer("snapshot", **kwargs)

    async def screenshot(self, **kwargs: object) -> object:
        return self._answer("screenshot", **kwargs)


class _Stagehand:
    def __init__(self) -> None:
        self.closed = False
        self.browser = object()

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[b._Session]]:
    """Replace the LangSmith side: every open returns a fresh fake session."""
    sessions: list[b._Session] = []

    async def fake_open(key: str, window: str, idle_ttl_seconds: int) -> b._Session:
        artifacts = tmp_path / f"session-{len(sessions)}"
        artifacts.mkdir()
        session = b._Session(
            window,
            cast("Any", _Stagehand()),
            cast("Any", _Facade(str(artifacts))),
            str(artifacts),
            "browser",
        )
        sessions.append(session)
        return session

    monkeypatch.setattr(b, "_open", fake_open)
    yield sessions
    for task in b._RELEASES.values():
        task.cancel()
    b._RELEASES.clear()
    b._SESSIONS.clear()


def _tools(middleware: b.BrowserMiddleware) -> dict[str, Any]:
    return {tool.name: cast("Any", tool).coroutine for tool in middleware.tools}


def _facade(session: b._Session) -> _Facade:
    return cast("_Facade", session.tools)


def test_define_browser_defaults_and_validates() -> None:
    assert define_browser() == BrowserDefinition(scope="thread", idle_ttl_seconds=600)
    assert define_browser(scope="user").scope == "user"
    with pytest.raises(ValueError, match="scope"):
        define_browser(scope="team")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="idle_ttl_seconds"):
        define_browser(idle_ttl_seconds=10)


def _key(runtime: ManagedToolRuntime, scope: b.Scope) -> str:
    return b._browser_key(runtime.server_info, "t1", scope)


def test_browser_key_is_the_thread_or_the_person() -> None:
    assert _key(_runtime(user="u1"), "thread") == "thread:t1"
    assert _key(_runtime(user="u1"), "user") == "user:u1"
    # A service principal stands for many callers, so its runs keep their thread's browser.
    assert _key(_runtime(user="svc", kind="service"), "user") == "thread:t1"
    with pytest.raises(RuntimeError, match="signed-in user"):
        _key(_runtime(), "user")


def test_an_agent_builds_with_the_middleware() -> None:
    # ToolNode resolves the tools' annotations when the agent is built, so the runtime
    # type they name must be importable then, not only while type checking.
    create_agent(GenericFakeChatModel(messages=iter([])), middleware=[b.BrowserMiddleware()])


def test_the_tools_are_the_facades_own_under_browser_names() -> None:
    # Its descriptions and schemas, as its TypeScript hosts advertise them, so the model
    # reads the `fullPage` the descriptions name. Screenshot adds `path`.
    tools = {tool.name: tool for tool in b.BrowserMiddleware().tools}
    assert list(tools) == [
        "browser_run",
        "browser_snapshot",
        "browser_screenshot",
        "browser_handoff",
    ]
    for entry in facade_tools():
        own = tools[f"browser_{entry['name']}"]
        schema = cast("dict[str, Any]", own.args_schema)
        properties = {name: p for name, p in schema["properties"].items() if name != "path"}
        assert own.description == entry["description"]
        assert {**schema, "properties": properties} == entry["inputSchema"]
    assert "path" in cast("dict[str, Any]", tools["browser_screenshot"].args_schema)["properties"]


def test_the_facades_argument_names_reach_its_methods(opened: list[b._Session]) -> None:
    tools = _tools(b.BrowserMiddleware())

    async def go() -> None:
        await tools["browser_snapshot"](_runtime(), includeIframes=False)
        _facade(opened[0]).result = {"data": "", "mimeType": "image/png"}
        await tools["browser_screenshot"](_runtime(), fullPage=True, type="jpeg", quality=40)

    asyncio.run(go())
    assert _facade(opened[0]).calls == [
        ("snapshot", {"include_iframes": False}),
        ("screenshot", {"full_page": True, "type": "jpeg", "quality": 40}),
    ]


def test_names_a_tool_lacks_are_turned_away_before_it_runs() -> None:
    middleware = b.BrowserMiddleware()

    async def ran(_: object) -> str:
        return "ran"

    def call(name: str, **args: object) -> object:
        request = SimpleNamespace(tool_call={"name": name, "args": args, "id": "c1"})
        result = asyncio.run(middleware.awrap_tool_call(cast("Any", request), cast("Any", ran)))
        return result if isinstance(result, str) else cast("Any", result).content

    assert call("browser_screenshot", full_page=True) == "error: unexpected argument 'full_page'."
    assert call("browser_snapshot", a=1, b=2) == "error: unexpected arguments 'a', 'b'."
    assert call("browser_screenshot", fullPage=True, path="/a.png") == "ran"
    assert call("read_file", file_path="/a") == "ran"  # another middleware's tool


class _Request:
    def __init__(self, system_message: SystemMessage) -> None:
        self.system_message = system_message

    def override(self, *, system_message: SystemMessage) -> _Request:
        return _Request(system_message)


def test_the_model_gets_the_facade_instructions_after_the_agents_own() -> None:
    request = cast("Any", _Request(SystemMessage(content="You are helpful.")))

    async def handler(updated: Any) -> Any:
        return updated

    updated = asyncio.run(b.BrowserMiddleware().awrap_model_call(request, handler))
    blocks = cast("Any", updated).system_message.content_blocks
    assert blocks[0]["text"] == "You are helpful."
    text = blocks[1]["text"]
    assert text.startswith(facade_instructions())
    # The facade's text names its tools `run` and so on; the note maps them to ours.
    assert "browser_run, browser_snapshot and browser_screenshot" in text
    assert "/workspace" in text


@pytest.mark.parametrize("scope", ["thread", "user"])
def test_only_a_threads_browser_is_told_about_its_sandboxs_localhost(scope: b.Scope) -> None:
    # A user's browser serves several threads, whose sandboxes cannot all be localhost.
    request = cast("Any", _Request(SystemMessage(content="You are helpful.")))

    async def handler(updated: Any) -> Any:
        return updated

    updated = asyncio.run(b.BrowserMiddleware(scope=scope).awrap_model_call(request, handler))
    ours = cast("Any", updated).system_message.content_blocks[1]["text"]
    assert ("http://localhost:<port>" in ours) is (scope == "thread")


def test_calls_reuse_the_session_until_the_run_ends(opened: list[b._Session]) -> None:
    middleware = b.BrowserMiddleware()
    tools = _tools(middleware)

    async def go() -> None:
        assert await tools["browser_snapshot"](_runtime()) == {"ok": True}
        assert await tools["browser_run"](_runtime(), code="return 1") == '{"ok": true}'
        assert len(opened) == 1
        assert [name for name, _ in _facade(opened[0]).calls] == ["snapshot", "run"]
        await middleware.aafter_agent(cast("Any", {}), cast("Any", _runtime()))
        assert cast("Any", opened[0].stagehand).closed
        await tools["browser_snapshot"](_runtime())
        assert len(opened) == 2

    asyncio.run(go())


def test_a_users_threads_share_the_browser_but_each_gets_its_own_window(
    opened: list[b._Session],
) -> None:
    tools = _tools(b.BrowserMiddleware(scope="user"))

    async def go() -> None:
        await tools["browser_snapshot"](_runtime(thread="t1", user="u1"))
        await tools["browser_snapshot"](_runtime(thread="t1", user="u1"))
        await tools["browser_snapshot"](_runtime(thread="t2", user="u1"))
        first, second = (session.window for session in opened)
        assert first != second
        # Moving to t2's window released t1's client: the extension serves one at a time.
        assert cast("Any", opened[0].stagehand).closed
        assert list(b._SESSIONS) == ["user:u1"]

    asyncio.run(go())


def test_a_threads_own_browser_drives_the_window_named_mda(opened: list[b._Session]) -> None:
    # The window a host's dashboard frames, as Open SWE's does by name.
    asyncio.run(_tools(b.BrowserMiddleware())["browser_snapshot"](_runtime()))
    assert [session.window for session in opened] == ["mda"]


def test_a_subclass_hears_each_call_with_the_browsers_name(opened: list[b._Session]) -> None:
    heard: list[tuple[b._Session, str, str]] = []

    class Reporting(b.BrowserMiddleware):
        async def _share(
            self, session: b._Session, name: str, key: str, runtime: ManagedToolRuntime
        ) -> None:
            heard.append((session, name, key))

    tools = _tools(Reporting())
    asyncio.run(tools["browser_snapshot"](_runtime()))
    asyncio.run(tools["browser_snapshot"](_runtime()))
    assert heard == [(opened[0], opened[0].browser, "thread:t1")] * 2


def test_errors_go_back_to_the_model(opened: list[b._Session]) -> None:
    tools = _tools(b.BrowserMiddleware())

    async def go() -> None:
        await tools["browser_snapshot"](_runtime())
        facade = _facade(opened[0])
        facade.result = StagehandFacadeInputError("call snapshot first")
        assert await tools["browser_run"](_runtime(), actions=[]) == "error: call snapshot first"
        facade.result = StagehandFacadeExecutionError("TypeError", "x is undefined")
        assert (
            await tools["browser_run"](_runtime(), code="x.y") == "error: TypeError: x is undefined"
        )
        assert len(b._SESSIONS) == 1  # the page answered: the session is fine

        # Anything else may mean the connection died: report it, and reconnect next call.
        facade.result = ConnectionError("socket closed")
        assert (
            await tools["browser_snapshot"](_runtime()) == "error: ConnectionError: socket closed"
        )
        assert not b._SESSIONS
        assert len(facade.calls) == 4  # the failed snapshot was not retried

    asyncio.run(go())


def test_a_typo_in_run_code_keeps_the_session(
    opened: list[b._Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The facade checks run code before it reaches the browser, with a plain SyntaxError.
    del opened
    session = _connect_to(_Window("tab0"), monkeypatch)

    async def fake_open(key: str, window: str, idle_ttl_seconds: int) -> b._Session:
        return session

    monkeypatch.setattr(b, "_open", fake_open)
    typo = asyncio.run(
        _tools(b.BrowserMiddleware())["browser_run"](_runtime(), code="await page.goto(")
    )
    assert typo.startswith("error: SyntaxError: ")
    assert {"thread:t1": session} == b._SESSIONS
    shutil.rmtree(session.artifacts)


def test_run_moves_saved_screenshots_into_the_sandbox(opened: list[b._Session]) -> None:
    tools = _tools(b.BrowserMiddleware())
    backend = _Backend()

    async def go() -> str:
        await tools["browser_snapshot"](_runtime(backend=backend))
        _facade(opened[0]).saves = {"shots/home.png": b"png"}
        return await tools["browser_run"](_runtime(backend=backend), code="...")

    assert asyncio.run(go()) == '{"ok": true}\nSaved /workspace/shots/home.png.'
    assert backend.files == {"/workspace/shots/home.png": b"png"}
    assert not any(Path(opened[0].artifacts).rglob("*.png"))


def test_screenshots_run_code_saved_survive_its_browser_close(
    opened: list[b._Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # `await page.screenshot({ path }); await browser.close();`: the facade asks to close
    # once it has saved the screenshot, before the run tool moves it into the sandbox.
    del opened
    session = _connect_to(_Window("tab0"), monkeypatch)
    saved = {"path": "shots/a.png", "base64": base64.b64encode(b"png").decode()}

    async def batch(source: str, page: object) -> dict[str, object]:
        return {
            "__stagehandPlaywrightCompat": True,
            "batchRuntimeMs": 1,
            "telemetry": {},
            "artifacts": [saved],
            "closeRequested": True,
            "value": "closed",
        }

    async def fake_open(key: str, window: str, idle_ttl_seconds: int) -> b._Session:
        return session

    monkeypatch.setattr(session.tools, "_run_batch", batch)
    monkeypatch.setattr(b, "_open", fake_open)
    backend = _Backend()
    code = "await page.screenshot({ path: 'shots/a.png' }); await browser.close();"
    result = asyncio.run(
        _tools(b.BrowserMiddleware())["browser_run"](_runtime(backend=backend), code=code)
    )
    assert result == '"closed"\nSaved /workspace/shots/a.png.'
    assert backend.files == {"/workspace/shots/a.png": b"png"}
    assert "thread:t1" not in b._SESSIONS  # the client is still released


def test_screenshot_returns_the_image_and_saves_it_to_path(opened: list[b._Session]) -> None:
    del opened
    tools = _tools(b.BrowserMiddleware())
    backend = _Backend()
    data = base64.b64encode(b"jpeg").decode()

    async def go() -> object:
        await tools["browser_snapshot"](_runtime(backend=backend))
        _facade(next(iter(b._SESSIONS.values()))).result = {"data": data, "mimeType": "image/jpeg"}
        return await tools["browser_screenshot"](
            _runtime(backend=backend), type="jpeg", quality=40, path="/workspace/a/shot.jpg"
        )

    assert asyncio.run(go()) == [
        {"type": "text", "text": "Screenshot of the current page. Saved /workspace/a/shot.jpg."},
        {"type": "image", "url": f"data:image/jpeg;base64,{data}"},
    ]
    assert backend.files == {"/workspace/a/shot.jpg": b"jpeg"}


class _Tab:
    def __init__(self, page_id: str) -> None:
        self.page_id = page_id
        self.closed = False

    async def url(self) -> str:
        return f"https://example.com/{self.page_id}"

    async def snapshot(self, *, include_iframes: bool, timeout: float) -> object:  # noqa: ASYNC109 - Stagehand's signature
        return SimpleNamespace(formatted_tree=f"tree of {self.page_id}", xpath_map={})

    async def close(self) -> None:
        self.closed = True


class _Window:
    """A window's tabs, with Chrome focused on another window's tab, which this client
    cannot see: as once localhost is shared, or after the client left another window."""

    def __init__(self, *tabs: str) -> None:
        self.tabs = [_Tab(tab) for tab in tabs]

    async def pages(self) -> list[_Tab]:
        return [tab for tab in self.tabs if not tab.closed]

    async def new_page(self, url: str | None = None) -> _Tab:
        self.tabs.append(_Tab(f"tab{len(self.tabs)}"))
        return self.tabs[-1]

    async def active_page(self) -> _Tab:
        msg = "activePage: active target not registered (OTHER) timed out after 3000ms"
        raise RuntimeError(msg)

    async def set_active_page(self, page: _Tab) -> None:  # does not move headless focus
        return None


def _connect_to(window: _Window, monkeypatch: pytest.MonkeyPatch) -> b._Session:
    browser = SimpleNamespace(context=window)
    stagehand = SimpleNamespace(browser=browser)

    async def connect(**_: object) -> object:
        return browser

    async def create(**_: object) -> object:
        return stagehand

    monkeypatch.setattr(b.local_browser, "connect", connect)
    monkeypatch.setattr(b.Stagehand, "create", create)
    return asyncio.run(
        b._connect("thread:t1", "mda", "browser", "ws://api/cdp", "ws://127.0.0.1/w", {})
    )


def test_the_tools_act_on_the_windows_tab_wherever_chrome_is_focused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _Window("tab0")
    session = _connect_to(window, monkeypatch)
    assert asyncio.run(session.tools.snapshot()) == "tree of tab0"
    # Attaching keeps the window's tab as it was: nothing reloads it.
    assert [(tab.page_id, tab.closed) for tab in window.tabs] == [("tab0", False)]
    shutil.rmtree(session.artifacts)


def test_a_window_without_a_tab_gets_one(monkeypatch: pytest.MonkeyPatch) -> None:
    window = _Window()
    session = _connect_to(window, monkeypatch)
    assert asyncio.run(session.tools.snapshot()) == "tree of tab0"
    shutil.rmtree(session.artifacts)


class _Box:
    """The browser's sandbox, without the extension yet."""

    def __init__(self) -> None:
        self.written: list[str] = []

    async def run(self, command: str, **_: object) -> SimpleNamespace:
        return SimpleNamespace(exit_code=1 if command.startswith("test -f") else 0, stderr="")

    async def write(self, path: str, data: bytes, **_: object) -> None:
        self.written.append(path)


def test_the_sessions_file_work_stays_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `langgraph dev`, so `mda dev`, turns away blocking file calls on the event loop.
    for name in ("scandir", "mkdir"):
        real = getattr(os, name)

        def guarded(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop()
                msg = f"blocking os.{_name} on the event loop"
                raise AssertionError(msg)
            return _real(*args, **kwargs)

        monkeypatch.setattr(os, name, guarded)
    session = _connect_to(_Window("tab0"), monkeypatch)
    box = _Box()
    asyncio.run(b._install_extension(cast("Any", box)))
    asyncio.run(session.close())
    assert box.written == [b._EXTENSION_ZIP]
    assert not Path(session.artifacts).exists()


def test_screenshot_path_must_be_absolute() -> None:
    note = asyncio.run(b._save(_runtime(backend=_Backend()), "shot.png", b""))
    assert note == "error: shot.png not saved: give an absolute path."


class _Sandboxes:
    """Stands in for the sandbox client: each browser's status, and the ones started."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses
        self.started: list[str] = []

    async def get_sandbox(self, name: str) -> Any:
        return SimpleNamespace(name=name, status=self.statuses.get(name, "ready"))

    async def start_sandbox(self, name: str, *, timeout: int) -> Any:  # noqa: ASYNC109
        self.started.append(name)
        return SimpleNamespace(name=name, status="ready")


class _CreateAPI:
    """Stands in for the browsers API: creating a browser that exists is a conflict."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.created: list[dict[str, object]] = []

    async def post(self, url: str, *, json: dict[str, object]) -> None:
        if json["name"] in self.existing:
            request = httpx.Request("POST", url)
            response = httpx.Response(httpx.codes.CONFLICT, request=request)
            raise httpx.HTTPStatusError("exists", request=request, response=response)
        self.created.append(json)


def test_a_deleted_browser_is_replaced_by_the_keys_next_one() -> None:
    first, second = b._browser_name("user:u1"), b._browser_name("user:u1", 1)
    assert first != second
    sandboxes = _Sandboxes({first: "deleting"})
    api = _CreateAPI(existing={first})

    name, box = asyncio.run(
        b._usable_browser(cast("Any", api), cast("Any", sandboxes), "https://api/b", "user:u1", 600)
    )

    assert (name, box.status) == (second, "ready")
    assert api.created == [{"name": second, "idle_ttl_seconds": 600, "hidpi": True}]


def test_an_existing_browser_is_reused_and_a_stopped_one_started() -> None:
    first = b._browser_name("user:u1")
    sandboxes = _Sandboxes({first: "stopped"})
    api = _CreateAPI(existing={first})

    name, _ = asyncio.run(
        b._usable_browser(cast("Any", api), cast("Any", sandboxes), "https://api/b", "user:u1", 600)
    )

    assert name == first
    assert sandboxes.started == [first]
    assert api.created == []


# The sandbox "box" has this id; the browser names its localhost by id.
_BOX_ID = "00000000-0000-0000-0000-000000000000"
_BOX_ALIAS = "sb-aaaaaaaaaaaaaaaaaaaaaaaaaa.localhost"


def test_thread_sandbox_is_the_runs_sandbox_once_it_exists() -> None:
    box = _SandboxBackend()
    assert b._thread_sandbox(_runtime(backend=box)) is box
    assert b._thread_sandbox(_runtime(backend=_SandboxBackend("pending:t1"))) is None
    assert b._thread_sandbox(_runtime(backend=_Backend())) is None
    assert b._thread_sandbox(_runtime()) is None


def test_thread_sandbox_finds_the_box_mda_opened_for_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A run's tools see its lazy sandbox behind a CompositeBackend, a fresh handle each
    # run: the box is open once MDA has opened it in this process, by any handle.
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "browser-test")
    monkeypatch.setattr(mda_runtime, "_managed_sandboxes", {})
    client = MagicMock()
    client.get_sandbox.return_value = SimpleNamespace(name="box", id=_BOX_ID, status="running")
    monkeypatch.setattr(mda_runtime, "_sandbox_client_for", lambda _: client)
    monkeypatch.setattr(mda_runtime, "LangSmithSandbox", lambda **_: _SandboxBackend())
    definition = define_sandbox()
    config: mda_runtime.ManagedRunConfig = {"configurable": {"thread_id": "t1"}}
    lazy = mda_runtime._managed_sandbox_backend(definition, config)
    runtime = _runtime(backend=CompositeBackend(default=lazy, routes={}))

    assert b._thread_sandbox(runtime) is None
    assert not client.mock_calls  # looking never opens one
    asyncio.run(mda_runtime._managed_sandbox_backend(definition, config)._abackend())
    assert getattr(b._thread_sandbox(runtime), "id", None) == "box"


_MAIN = {"id": "w1", "name": "mda"}


@pytest.mark.parametrize(
    ("windows", "writes"),
    [
        ([_MAIN], ["POST attachments"]),
        ([_MAIN, {"id": "w2", "name": "mda-localhost", "localhost_alias": _BOX_ALIAS}], []),
        # MDA replaced the sandbox under the same name: the window names the old id.
        (
            [_MAIN, {"id": "w2", "name": "mda-localhost", "localhost_alias": "sb-old.localhost"}],
            ["DELETE attachments/w2", "POST attachments"],
        ),
    ],
)
def test_share_localhost_attaches_the_sandbox_once(
    windows: list[dict[str, str]], writes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v2/sandboxes/browsers/mda-browser-1/")
        seen.append((request.method, path, json.loads(request.content or b"null")))
        if request.url.path.endswith("/boxes/box"):
            return httpx.Response(200, json={"id": _BOX_ID, "name": "box"})
        return httpx.Response(200, json={"attachments": windows})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        b.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    monkeypatch.setattr(b, "_api", lambda: ("https://api/v2/sandboxes", {"X-Api-Key": "k"}))
    asyncio.run(b._share_localhost("mda-browser-1", "box"))
    assert [f"{method} {path}" for method, path, _ in seen if method != "GET"] == writes
    if writes:
        body = {"name": "mda-localhost", "sandbox": "box", "share_localhost": True}
        assert seen[-1][2] == body


@pytest.mark.parametrize(
    ("scope", "runtimes", "fail", "shares"),
    [
        # Not while MDA has only named it, then once.
        ("thread", ["pending", "made", "made"], False, ["box"]),
        ("user", ["person"], False, []),  # a user's browser shares no sandbox
        ("user", ["schedule"], False, ["box"]),  # a schedule's thread browser does
        ("thread", ["made", "made"], True, ["box", "box"]),  # a failure goes on, then retries
        # MDA replaced the sandbox mid-run under the same name: share the new one, once.
        ("thread", ["made", "replaced", "replaced"], False, ["box", "box"]),
    ],
)
def test_a_session_shares_its_threads_sandbox_once_it_exists(
    scope: b.Scope,
    runtimes: list[str],
    fail: bool,
    shares: list[str],
    opened: list[b._Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del opened
    made = _SandboxBackend()
    by_name = {
        "pending": _runtime(backend=_SandboxBackend("pending:t1")),
        "made": _runtime(backend=made),
        "replaced": _runtime(backend=_SandboxBackend()),
        "person": _runtime(user="u1", backend=made),
        "schedule": _runtime(user="mda:schedule:a", kind="service", backend=made),
    }
    shared: list[str] = []

    async def share(_name: str, sandbox: str) -> None:
        shared.append(sandbox)
        if fail:
            msg = "could not share"
            raise RuntimeError(msg)

    monkeypatch.setattr(b, "_share_localhost", share)
    snapshot = _tools(b.BrowserMiddleware(scope=scope))["browser_snapshot"]

    async def calls() -> list[object]:
        return [await snapshot(by_name[name]) for name in runtimes]

    assert asyncio.run(calls()) == [{"ok": True}] * len(runtimes)  # browsing goes on regardless
    assert shared == shares


# --- browser_handoff: hand the window to the user, notify the thread when it's back ---

_PENDING: b.PendingBrowserHandoff = {"browser": "browser", "window_id": "w1", "until": 2e9}


def _with_state(runtime: ManagedToolRuntime, state: dict[str, object]) -> ManagedToolRuntime:
    runtime.state = state
    return runtime


def test_browser_handoff_hands_over_and_returns_at_once(
    opened: list[b._Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    handed: list[tuple[str, str]] = []
    watched: list[b.PendingBrowserHandoff] = []

    async def hand_over(key: str, window: str, idle_ttl_seconds: int) -> tuple[str, str, str]:
        handed.append((key, window))
        return "browser", f"w{len(handed)}", f"https://act.example/{len(handed)}"

    async def watch(runtime: ManagedToolRuntime, pending: b.PendingBrowserHandoff) -> None:
        watched.append(pending)

    monkeypatch.setattr(b, "_hand_over", hand_over)
    monkeypatch.setattr(b, "_start_watcher", watch)
    tools = _tools(b.BrowserMiddleware(scope="user"))

    async def go() -> tuple[object, object]:
        await tools["browser_snapshot"](_runtime(user="u1"))  # the agent was browsing
        first = await tools["browser_handoff"](_runtime(user="u1"), reason="Log in to LinkedIn")
        # Asked again while the user has the window: handed over again, never connected.
        pending = cast("dict[str, Any]", cast("Command", first).update)["mda_browser_handoff"]
        again = _with_state(_runtime(user="u1"), {"mda_browser_handoff": pending})
        second = await tools["browser_handoff"](again, reason="Log in again")
        return first, second

    first, second = asyncio.run(go())
    update = cast("dict[str, Any]", cast("Command", first).update)
    [reply] = update["messages"]
    assert reply.artifact == {
        "type": "browser_handoff",
        "message": "Log in to LinkedIn",
        "url": "https://act.example/1",
    }
    assert "act.example" not in reply.content  # the model reads only the text
    assert reply.tool_call_id == "call-1"
    pending = update["mda_browser_handoff"]
    assert (pending["browser"], pending["window_id"]) == ("browser", "w1")
    assert opened[0].stagehand.closed  # the agent's client let go
    assert len(opened) == 1  # handing over opens no client
    [again] = cast("dict[str, Any]", cast("Command", second).update)["messages"]
    assert again.artifact["url"] == "https://act.example/2"
    assert [p["window_id"] for p in watched] == ["w1", "w2"]
    assert handed == [("user:u1", b._window_name("user:u1", "t1"))] * 2


@pytest.mark.parametrize(("status", "refused"), [(None, True), ("handed_back", False)])
def test_the_browser_waits_while_the_user_has_it(
    opened: list[b._Session],
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
    refused: bool,
) -> None:
    async def handoff_status(pending: b.PendingBrowserHandoff) -> str | None:
        return status

    monkeypatch.setattr(b, "_handoff_status", handoff_status)
    tools = _tools(b.BrowserMiddleware())
    runtime = _with_state(_runtime(), {"mda_browser_handoff": _PENDING})

    out = asyncio.run(tools["browser_snapshot"](runtime))

    # Once it ended the tools work, even if no watcher said so (after a restart).
    assert (out == f"error: {b._WITH_USER}") is refused
    assert (opened == []) is refused


class _BrowsersAPI:
    """Stands in for httpx.AsyncClient on the browsers API: the browser's windows."""

    windows: ClassVar[list[dict[str, object]] | None] = []  # None: the browser is gone

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if self.windows is None:
            return httpx.Response(404, json={"detail": "not found"}, request=request)
        return httpx.Response(200, json={"attachments": self.windows}, request=request)


@pytest.mark.parametrize(
    ("windows", "until", "status"),
    [
        ([{"id": "w1", "controller": "user"}], 2e9, None),
        ([{"id": "w1", "controller": "agent"}], 2e9, "handed_back"),
        ([{"id": "w1", "controller": "user"}], 0, "ended"),  # the link ran out
        ([], 2e9, "ended"),  # the window is gone
        (None, 2e9, "ended"),  # the browser is gone: it idled out or was deleted
    ],
)
def test_how_a_handoff_ended(
    monkeypatch: pytest.MonkeyPatch,
    windows: list[dict[str, object]] | None,
    until: float,
    status: str | None,
) -> None:
    monkeypatch.setattr(_BrowsersAPI, "windows", windows)
    monkeypatch.setattr(b.httpx, "AsyncClient", _BrowsersAPI)
    pending: b.PendingBrowserHandoff = {**_PENDING, "until": until}
    assert asyncio.run(b._handoff_status(pending)) == status


def test_a_handoff_survives_a_watcher_that_could_not_start(
    opened: list[b._Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hand_over(key: str, window: str, idle_ttl_seconds: int) -> tuple[str, str, str]:
        return "browser", "w1", "https://act.example/1"

    async def watch(runtime: ManagedToolRuntime, pending: b.PendingBrowserHandoff) -> None:
        raise RuntimeError("no Agent Server")

    monkeypatch.setattr(b, "_hand_over", hand_over)
    monkeypatch.setattr(b, "_start_watcher", watch)
    tools = _tools(b.BrowserMiddleware())

    result = asyncio.run(tools["browser_handoff"](_runtime(), reason="Log in"))

    # The user still gets the window; its browser tools work again once it ended.
    [reply] = cast("dict[str, Any]", cast("Command", result).update)["messages"]
    assert reply.artifact["url"] == "https://act.example/1"


class _AgentServer:
    """Stands in for the Agent Server's client: records what it's asked."""

    def __init__(self, thread_status: str = "idle", saved: object = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.thread = {"status": thread_status, "values": {"mda_browser_handoff": saved}}
        self.threads = SimpleNamespace(create=self._record("threads.create"), get=self._get)
        self.runs = SimpleNamespace(create=self._record("runs.create"))

    def _record(self, name: str) -> Callable[..., Awaitable[dict[str, object]]]:
        async def call(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls.append((name, args, kwargs))
            return {"thread_id": "watch-1"}

        return call

    async def _get(self, thread_id: str) -> dict[str, object]:
        self.calls.append(("threads.get", (thread_id,), {}))
        return self.thread


_LEAD_CONFIG = {"configurable": {"thread_id": "t1"}, "metadata": {"assistant_id": "a"}}


def test_a_handoff_starts_a_watcher_run_linked_to_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _AgentServer()
    monkeypatch.setattr(b, "get_client", lambda: server)
    trace = {"langsmith-trace": "20261026T000000000000Zabc", "baggage": "langsmith-project=p"}
    monkeypatch.setattr(b, "_trace_headers", lambda spec: trace)
    runtime = _runtime()
    runtime.config = cast("Any", _LEAD_CONFIG)
    runtime.context = {"user_tz": "UTC", "deepagents_parent": {"thread_id": "p"}}

    asyncio.run(b._start_watcher(runtime, _PENDING))

    # As deepagents starts an async subagent's task, on a thread of its own, with
    # this run's context (without deepagents' parent reference), under its trace.
    assert server.calls == [
        (
            "threads.create",
            (),
            {"metadata": {"parent_thread_id": "t1", "parent_assistant_id": "a"}},
        ),
        (
            "runs.create",
            ("watch-1", b.HANDOFF_WATCHER_GRAPH),
            {"input": {"handoff": _PENDING}, "context": {"user_tz": "UTC"}, "headers": trace},
        ),
    ]


@pytest.mark.parametrize(
    ("thread_status", "saved", "notified"),
    [
        ("busy", _PENDING, ["handed_back"]),  # enqueued after the run going on
        ("idle", {**_PENDING, "window_id": "w2"}, []),  # a newer handoff replaced it
        ("idle", None, []),  # a run already ended it
        # A new run would drop the approval the thread waits on; tools work again anyway.
        ("interrupted", _PENDING, []),
    ],
)
def test_the_watcher_notifies_the_agent_once_the_user_hands_back(
    monkeypatch: pytest.MonkeyPatch,
    thread_status: str,
    saved: b.PendingBrowserHandoff | None,
    notified: list[str],
) -> None:
    statuses = iter([None, "handed_back"])
    server = _AgentServer(thread_status, saved)

    async def handoff_status(pending: b.PendingBrowserHandoff) -> str | None:
        return next(statuses)

    monkeypatch.setattr(b, "_handoff_status", handoff_status)
    monkeypatch.setattr(b, "_HAND_BACK_POLL_S", 0)
    monkeypatch.setattr(b, "get_client", lambda: server)
    # The agent's reference, as the thread's metadata gives it to every run on it.
    config = {"metadata": {"parent_thread_id": "t1", "parent_assistant_id": "a"}}
    runtime = SimpleNamespace(context={"user_tz": "UTC", "deepagents_parent": {"thread_id": "p"}})

    monkeypatch.setattr(b, "get_config", lambda: config)

    asyncio.run(b._watch(b._WatcherState(handoff=_PENDING), cast("Any", runtime)))

    woke = [kwargs for name, _, kwargs in server.calls if name == "runs.create"]
    assert [w["input"]["messages"][0]["mda_notification"]["status"] for w in woke] == notified
    for args in (args for name, args, _ in server.calls if name == "runs.create"):
        assert args == ("t1", "a")
    # The run gets the agent's context back, as deepagents' parent notifications do.
    assert all(w["multitask_strategy"] == "enqueue" for w in woke)
    assert all(w["context"] == {"user_tz": "UTC"} for w in woke)


def test_the_watcher_graph_runs_the_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _AgentServer("idle", _PENDING)

    async def handoff_status(pending: b.PendingBrowserHandoff) -> str:
        return "ended"

    monkeypatch.setattr(b, "_handoff_status", handoff_status)
    monkeypatch.setattr(b, "_HAND_BACK_POLL_S", 0)
    monkeypatch.setattr(b, "get_client", lambda: server)
    # The Agent Server copies the watcher thread's metadata into its run.
    config = {"metadata": {"parent_thread_id": "t1", "parent_assistant_id": "a"}}

    asyncio.run(b.handoff_watcher.ainvoke({"handoff": _PENDING}, cast("Any", config)))

    [(_, args, kwargs)] = [call for call in server.calls if call[0] == "runs.create"]
    assert args == ("t1", "a")
    assert kwargs["input"]["messages"][0]["mda_notification"]["status"] == "ended"


def test_the_notification_is_shaped_like_a_task_notification() -> None:
    message = b._wake_message("handed_back")
    assert message["role"] == "user"
    assert message["content"].startswith("[SYSTEM NOTIFICATION - NOT USER INPUT]\n")
    assert message["mda_notification"] == {"type": "browser_handoff", "status": "handed_back"}
    [converted] = convert_to_messages([message])
    assert is_task_notification(converted)  # channels don't take it for the user


def test_the_notifications_run_ends_the_handoff() -> None:
    middleware = b.BrowserMiddleware()
    [notification] = convert_to_messages([b._wake_message("ended")])
    user = HumanMessage("hi")

    def before(messages: list[object], pending: object) -> object:
        state = {"messages": messages, "mda_browser_handoff": pending}
        return asyncio.run(middleware.abefore_agent(cast("Any", state), cast("Any", None)))

    assert before([notification], _PENDING) == {"mda_browser_handoff": None}
    assert before([user], _PENDING) is None  # the user's own message: still waiting
    assert before([notification], None) is None


class _WorkspaceAPI:
    """Stands in for the API client: the workspace's info."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.urls.append(url)
        info = {"id": "ws", "dashboard_url": "https://eu.smith.langchain.com/", "config": {}}
        return httpx.Response(200, json=info, request=httpx.Request("GET", url))


def test_studio_may_embed_the_handoff_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # The workspace's dashboard_url names the UI's origin, self-hosted ones included.
    monkeypatch.delenv("MDA_BROWSER_EMBED_ORIGINS", raising=False)
    monkeypatch.setattr(b, "_endpoint", lambda: "https://eu.api.smith.langchain.com")
    api = _WorkspaceAPI()
    assert asyncio.run(b._embed_origins(cast("Any", api))) == ["https://eu.smith.langchain.com"]
    assert api.urls == ["https://eu.api.smith.langchain.com/v1/platform/workspaces/current/info"]


def test_more_origins_may_embed_the_handoff_window(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ui(api: object) -> str:
        return "https://smith.langchain.com"

    monkeypatch.setattr(b, "_langsmith_ui_origin", ui)
    monkeypatch.setenv(
        "MDA_BROWSER_EMBED_ORIGINS", " https://preview.example ,,https://smith.langchain.com"
    )
    origins = asyncio.run(b._embed_origins(cast("Any", None)))
    assert origins == ["https://smith.langchain.com", "https://preview.example"]
