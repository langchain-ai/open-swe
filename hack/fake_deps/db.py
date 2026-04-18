"""SQLite persistence for fake_deps.

One schema, two logical domains: Slack (users, messages) and GitHub
(users, repos, issues, comments, pulls, reactions, webhooks). State survives
process restarts; drop the .sqlite3 file to reset.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = Path(
    os.environ.get(
        "FAKE_DEPS_DB_PATH",
        str(Path(__file__).resolve().parent / "fake_deps.sqlite3"),
    )
)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS slack_users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    real_name    TEXT,
    display_name TEXT,
    email        TEXT,
    is_bot       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS slack_reactions (
    message_ts  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (message_ts, user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_slack_reactions_msg ON slack_reactions(message_ts);

CREATE TABLE IF NOT EXISTS slack_thread_status (
    channel_id TEXT NOT NULL,
    thread_ts  TEXT NOT NULL,
    status     TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (channel_id, thread_ts)
);

CREATE TABLE IF NOT EXISTS slack_messages (
    ts             TEXT PRIMARY KEY,
    channel_id     TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    text           TEXT NOT NULL,
    thread_ts      TEXT,
    parent_user_id TEXT,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slack_messages_channel ON slack_messages(channel_id, ts);
CREATE INDEX IF NOT EXISTS idx_slack_messages_thread  ON slack_messages(thread_ts);

CREATE TABLE IF NOT EXISTS gh_users (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT UNIQUE NOT NULL,
    name  TEXT,
    email TEXT,
    type  TEXT NOT NULL DEFAULT 'User'  -- 'User' | 'Bot' | 'Organization'
);

CREATE TABLE IF NOT EXISTS gh_repos (
    owner        TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    default_branch TEXT NOT NULL DEFAULT 'main',
    private      INTEGER NOT NULL DEFAULT 0,
    path_on_disk TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (owner, name)
);

CREATE TABLE IF NOT EXISTS gh_issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner      TEXT NOT NULL,
    repo       TEXT NOT NULL,
    number     INTEGER NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'open',
    user_login TEXT NOT NULL,
    is_pull    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE (owner, repo, number)
);

CREATE TABLE IF NOT EXISTS gh_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner        TEXT NOT NULL,
    repo         TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    body         TEXT NOT NULL,
    user_login   TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gh_comments_issue
    ON gh_comments(owner, repo, issue_number, id);

CREATE TABLE IF NOT EXISTS gh_pulls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner      TEXT NOT NULL,
    repo       TEXT NOT NULL,
    number     INTEGER NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT 'open',
    draft      INTEGER NOT NULL DEFAULT 0,
    head_ref   TEXT NOT NULL,
    base_ref   TEXT NOT NULL,
    user_login TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (owner, repo, number)
);

CREATE TABLE IF NOT EXISTS gh_reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,   -- 'issue_comment' | 'pr_review_comment' | 'pr_review'
    target_id   INTEGER NOT NULL,
    user_login  TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target     TEXT NOT NULL,   -- 'slack' | 'github'
    event_type TEXT,
    status     INTEGER,
    response   TEXT,
    created_at REAL NOT NULL
);

-- Scripted LLM responses. Tests push an ordered list of canned assistant
-- messages keyed by a test_id; the proxy pops them FIFO when the agent's
-- request body contains that test_id. There is no recording.
CREATE TABLE IF NOT EXISTS llm_scripts (
    test_id         TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    response_json   TEXT NOT NULL,
    response_status INTEGER NOT NULL DEFAULT 200,
    consumed        INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    PRIMARY KEY (test_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_llm_scripts_pending
    ON llm_scripts(test_id, consumed, seq);
"""


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def fetchall(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def fetchone(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def execute(sql: str, params: tuple = ()) -> int:
    """Execute a statement. Returns lastrowid for INSERTs, else rowcount."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, params) as cur:
            await db.commit()
            return cur.lastrowid or cur.rowcount
