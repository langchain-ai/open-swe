"""Fake dependencies server for local open-swe development.

One FastAPI app on port 13765. Mounts three routers — Slack under
``/slack``, GitHub under ``/github`` + git smart-HTTP under ``/git``, and an
Anthropic record/replay proxy under ``/anthropic``. State is persisted to a
SQLite file (``hack/fake_deps/fake_deps.sqlite3``); repo working trees live
on disk under ``sample_repos/``.

Run::

    make up           # background both this + langgraph dev on :2025
    make fake-deps    # just this one, foreground

All env comes from ``.env`` at the repo root.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import db, github, llm, slack

STATIC_DIR = Path(__file__).resolve().parent
INDEX_HTML = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await slack.seed()
    await github.seed()
    yield


app = FastAPI(title="fake-deps", lifespan=lifespan)
app.include_router(slack.router)
app.include_router(github.router)
app.include_router(github.git_router)
app.include_router(llm.router)


@app.get("/")
async def ui_index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"ok": "true"}
