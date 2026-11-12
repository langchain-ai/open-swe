import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openswe.dashboard import oauth
from openswe.sandboxes import paths as sandbox_paths
from openswe.threads import file_preview, files
from openswe.threads.routes import router
from openswe.users import User
from tests.conftest import LangSmithUsers

THREAD = "thread-1"


@dataclass
class Access:
    """What the faked thread lookup answers: the thread's sandbox and who may not prompt it."""

    user: User
    sandbox_id: str = "sbx-1"
    denied: set[str] = field(default_factory=set)


class LocalSandbox:
    """Runs the real workspace script against a local folder, as the sandbox would."""

    async def aexecute(self, command: str, *, timeout: int | None = None) -> SimpleNamespace:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await process.communicate()
        return SimpleNamespace(output=output.decode(), exit_code=process.returncode)

    async def adownload_files(self, paths: list[str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(content=Path(path).read_bytes()) for path in paths]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "site" / "css").mkdir(parents=True)
    (root / "site" / ".hidden").mkdir()
    (root / ".config").mkdir()
    (root / "site" / "index.html").write_text("<h1>deck</h1>")
    (root / "site" / "css" / "deck.css").write_text("h1{}")
    (root / "site" / ".hidden" / "x.css").write_text("x{}")
    (root / "secret.js").write_text("secret()")
    (root / "site" / "data.json").write_text('{"rows": []}')
    (root / ".config" / "token.css").write_text("token{}")
    (root / "site" / "leak.js").symlink_to(root / "secret.js")
    (root / "site" / "alias").symlink_to(root / ".config")
    (root / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / "archive.zip").write_bytes(b"PK\x03\x04")
    (root / "clip.mp4").write_bytes(bytes(range(256)) * 4)
    return root


@pytest.fixture
def state(monkeypatch, workspace: Path, langsmith_users: LangSmithUsers) -> Access:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-32-bytes")
    user = langsmith_users.add("octocat")
    current = Access(user)

    async def readable(thread_id: str, *, login: str | None = None, email: str | None = None):
        if thread_id != THREAD:
            raise HTTPException(404, "thread not found")
        return {"sandbox_id": current.sandbox_id}

    def promptable(metadata: object, login: str | None) -> None:
        if login in current.denied:
            raise HTTPException(404, "thread not found")

    async def connect(sandbox_id: str, *, thread_id: str) -> LocalSandbox:
        return LocalSandbox()

    async def work_dir(sandbox: object) -> str:
        return os.fspath(workspace)

    monkeypatch.setattr(files, "_readable_thread_metadata", readable)
    monkeypatch.setattr(files, "_assert_thread_promptable", promptable)
    monkeypatch.setattr(files, "connect_sandbox", connect)
    monkeypatch.setattr(sandbox_paths, "resolve_sandbox_work_dir", work_dir)
    return current


@pytest.fixture
def client(state: Access) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/dashboard/api")
    session = oauth.issue_session(
        login="octocat", email=None, avatar_url=None, user_id=str(state.user.id)
    )
    app.state.session = session
    return TestClient(app)


def _link(client: TestClient, path: str) -> str:
    client.cookies.set(oauth.COOKIE_NAME, client.app.state.session)
    response = client.post(
        f"/dashboard/api/threads/{THREAD}/files/preview-url", json={"path": path}
    )
    assert response.status_code == 200, response.text
    return "/dashboard/api" + response.json()["path"]


def _raw(client: TestClient, url: str, **headers: str):
    client.cookies.clear()
    return client.get(url, headers=headers)


def test_html_page_reaches_its_folder_and_nothing_beside_it(client: TestClient) -> None:
    page = _link(client, "site/index.html")
    folder = page.rsplit("/", 1)[0]

    response = _raw(client, page)
    assert response.status_code == 200
    assert response.text == "<h1>deck</h1>"
    csp = response.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts allow-downloads;")
    assert "allow-same-origin" not in csp and "connect-src 'none'" in csp
    assert _raw(client, f"{folder}/css/deck.css").headers["content-type"].startswith("text/css")

    # data.json is in the folder, but a page could send its own link out: data needs its own link.
    for escape in ("data.json", "leak.js", "alias/token.css", ".hidden/x.css", "missing.css"):
        refused = _raw(client, f"{folder}/{escape}")
        assert refused.status_code == 404, escape
        assert refused.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert refused.headers["x-content-type-options"] == "nosniff"


def test_exact_grant_serves_only_its_own_file(client: TestClient) -> None:
    logo = _link(client, "logo.svg")
    assert _raw(client, logo).headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    )
    assert _raw(client, logo.rsplit("/", 1)[0] + "/archive.zip").status_code == 404

    archive = _raw(client, _link(client, "archive.zip"))
    assert archive.headers["content-type"] == "application/octet-stream"
    assert archive.headers["content-disposition"] == 'attachment; filename="archive.zip"'


def test_video_seeks_by_range(client: TestClient) -> None:
    clip = _link(client, "clip.mp4")

    partial = _raw(client, clip, Range="bytes=10-19")
    assert partial.status_code == 206
    assert partial.content == bytes(range(10, 20))
    assert partial.headers["content-range"] == "bytes 10-19/1024"
    assert _raw(client, clip, Range="bytes=2048-").status_code == 416


def test_a_folder_or_root_swapped_for_a_symlink_takes_no_grant_with_it(
    client: TestClient, workspace: Path
) -> None:
    page = _link(client, "site/index.html")
    folder = page.rsplit("/", 1)[0]
    (workspace / "private").mkdir()
    (workspace / "private" / "secret.js").write_text("secret()")
    (workspace / "site").rename(workspace / "site-moved")
    (workspace / "site").symlink_to(workspace / "private")

    assert _raw(client, f"{folder}/secret.js").status_code == 404

    clip = _link(client, "clip.mp4")
    elsewhere = workspace.with_name("elsewhere")
    elsewhere.mkdir()
    (elsewhere / "clip.mp4").write_bytes(b"elsewhere")
    workspace.rename(workspace.with_name("repo-moved"))
    workspace.symlink_to(elsewhere)
    assert _raw(client, clip).status_code == 404


@pytest.mark.parametrize(
    ("change", "status"), [("account", 401), ("thread", 404), ("sandbox", 404)]
)
def test_link_stops_working_when_access_does(
    client: TestClient, state: Access, langsmith_users: LangSmithUsers, change: str, status: int
) -> None:
    page = _link(client, "site/index.html")
    if change == "account":
        langsmith_users.revoked.add(state.user.id)
    elif change == "thread":
        state.denied.add("octocat")
    else:
        state.sandbox_id = "sbx-2"

    assert _raw(client, page).status_code == status


def test_link_is_bound_to_its_thread_and_is_not_a_session(client: TestClient) -> None:
    page = _link(client, "site/index.html")
    token = page.split("/files/raw/")[1].split("/")[0]

    assert _raw(client, page.replace(THREAD, "thread-2")).status_code == 401
    with pytest.raises(HTTPException):
        oauth.decode_session(token)
    # A refresh in the same second must still get a new URL, or the panel shows cached bytes.
    assert _link(client, "site/index.html") != _link(client, "site/index.html")


def _ts_strings(source: str, start: str, end: str) -> list[str]:
    block = source[source.index(start) : source.index(end, source.index(start))]
    return re.findall(r'"([^"]*)"', block)


def test_desktop_and_dashboard_serve_files_as_the_backend_does() -> None:
    """The desktop and the dashboard keep their own copies of these rules; a drift breaks or opens a viewer."""
    app = Path(__file__).resolve().parents[2]
    desktop = (app / "desktop" / "src" / "preview-files.cts").read_text()
    types = _ts_strings(desktop, "const INLINE_TYPES", "};")
    assert dict(zip(types[::2], types[1::2], strict=True)) == file_preview._INLINE_TYPES
    assert "; ".join(_ts_strings(desktop, "const HTML_CSP", "].join")) == file_preview._HTML_CSP
    assert set(_ts_strings(desktop, "const FOLDER_EXTENSIONS", ";")) == set(
        file_preview._FOLDER_EXTENSIONS
    )
    assert set(_ts_strings(desktop, "const FOLDER_ASSETS", ";")) == file_preview._FOLDER_ASSETS
    headers = _ts_strings(desktop, "const ALWAYS", "};")
    assert dict(zip(headers[::2], headers[1::2], strict=True)) == file_preview._ALWAYS
    for constant in ("SVG_CSP", "DEFAULT_CSP"):
        line = next(row for row in desktop.splitlines() if row.startswith(f"const {constant} ="))
        assert re.findall(r'"([^"]*)"', line) == [getattr(file_preview, f"_{constant}")]

    # A kind the panel loads straight from its link must be served inline, not as a download.
    ui = (app / "ui" / "src" / "features" / "agents" / "lib" / "filePreview.ts").read_text()
    block = ui[ui.index("const KINDS") : ui.index("}", ui.index("const KINDS"))]
    linked = {"markdown", "html", "image", "svg", "video", "audio", "pdf"}
    kinds = re.findall(r"^  (\w+): \"(\w+)\",", block, re.MULTILINE)
    assert kinds
    for extension, kind in kinds:
        if kind in linked:
            assert f".{extension}" in file_preview._INLINE_TYPES, extension
