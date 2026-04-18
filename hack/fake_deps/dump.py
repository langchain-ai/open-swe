"""Dump fake-deps SQLite state as a readable markdown report.

Run via ``make dump-fake-data`` or directly with ``python -m hack.fake_deps.dump``.
Prints to stdout — redirect if you want a file. Reads the same DB the running
fake-deps uses (``hack/fake_deps/fake_deps.sqlite3``, overridable via
``FAKE_DEPS_DB_PATH``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(
    os.environ.get(
        "FAKE_DEPS_DB_PATH",
        str(Path(__file__).resolve().parent / "fake_deps.sqlite3"),
    )
)


def _ts(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, limit: int = 200) -> str:
    text = (text or "").replace("\n", "↵ ")
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(sql))


def _section(title: str) -> None:
    print(f"\n## {title}\n")


def _table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        print("_empty_")
        return
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = lambda cells: "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    print(line(headers))
    print(sep)
    for r in rows:
        print(line(r))


def _slack(conn: sqlite3.Connection) -> None:
    _section("Slack users")
    _table(
        ["id", "name", "real_name", "display_name", "email", "is_bot"],
        [
            [r["id"], r["name"] or "-", r["real_name"] or "-", r["display_name"] or "-",
             r["email"] or "-", "yes" if r["is_bot"] else "no"]
            for r in _rows(conn, "SELECT * FROM slack_users ORDER BY id")
        ],
    )

    _section("Slack messages")
    _table(
        ["ts", "channel", "user", "thread_ts", "text"],
        [
            [r["ts"], r["channel_id"], r["user_id"], r["thread_ts"] or "-",
             _truncate(r["text"], 140)]
            for r in _rows(conn, "SELECT * FROM slack_messages ORDER BY created_at")
        ],
    )


def _github(conn: sqlite3.Connection) -> None:
    _section("GitHub users")
    _table(
        ["login", "name", "email", "type"],
        [
            [r["login"], r["name"] or "-", r["email"] or "-", r["type"]]
            for r in _rows(conn, "SELECT * FROM gh_users ORDER BY login")
        ],
    )

    _section("GitHub repos")
    _table(
        ["owner/name", "default_branch", "private", "path_on_disk", "description"],
        [
            [f"{r['owner']}/{r['name']}", r["default_branch"],
             "yes" if r["private"] else "no", r["path_on_disk"] or "-",
             _truncate(r["description"] or "-", 80)]
            for r in _rows(conn, "SELECT * FROM gh_repos ORDER BY owner, name")
        ],
    )

    _section("GitHub issues")
    _table(
        ["repo", "#", "state", "user", "is_pull", "title"],
        [
            [f"{r['owner']}/{r['repo']}", str(r["number"]), r["state"],
             r["user_login"], "pull" if r["is_pull"] else "issue",
             _truncate(r["title"], 100)]
            for r in _rows(conn, "SELECT * FROM gh_issues ORDER BY owner, repo, number")
        ],
    )

    _section("GitHub pulls")
    _table(
        ["repo", "#", "state", "draft", "head → base", "user", "title"],
        [
            [f"{r['owner']}/{r['repo']}", str(r["number"]), r["state"],
             "yes" if r["draft"] else "no", f"{r['head_ref']} → {r['base_ref']}",
             r["user_login"], _truncate(r["title"], 80)]
            for r in _rows(conn, "SELECT * FROM gh_pulls ORDER BY owner, repo, number")
        ],
    )

    _section("GitHub comments")
    _table(
        ["repo", "issue#", "user", "created", "body"],
        [
            [f"{r['owner']}/{r['repo']}", str(r["issue_number"]),
             r["user_login"], _ts(r["created_at"]), _truncate(r["body"], 120)]
            for r in _rows(conn, "SELECT * FROM gh_comments ORDER BY created_at")
        ],
    )

    _section("GitHub reactions")
    _table(
        ["target_type", "target_id", "user", "content", "created"],
        [
            [r["target_type"], str(r["target_id"]), r["user_login"],
             r["content"], _ts(r["created_at"])]
            for r in _rows(conn, "SELECT * FROM gh_reactions ORDER BY created_at")
        ],
    )


def _webhooks(conn: sqlite3.Connection) -> None:
    _section("Webhook log (last 40)")
    _table(
        ["time", "target", "event_type", "status", "response"],
        [
            [_ts(r["created_at"]), r["target"], r["event_type"] or "-",
             str(r["status"] if r["status"] is not None else "-"),
             _truncate(r["response"] or "-", 80)]
            for r in _rows(
                conn,
                "SELECT * FROM webhook_log ORDER BY created_at DESC LIMIT 40",
            )
        ],
    )


def _llm(conn: sqlite3.Connection) -> None:
    _section("LLM scripted responses")
    rows = _rows(
        conn,
        """
        SELECT test_id, seq, response_status, consumed,
               length(response_json) AS resp_bytes,
               created_at
          FROM llm_scripts
         ORDER BY created_at DESC, seq ASC
        """,
    )
    print(f"_{len(rows)} script entries total._\n")
    _table(
        ["test_id", "seq", "status", "consumed", "resp", "created"],
        [
            [r["test_id"], str(r["seq"]), str(r["response_status"]),
             "yes" if r["consumed"] else "no",
             f"{r['resp_bytes']}B", _ts(r["created_at"])]
            for r in rows
        ],
    )


def main() -> int:
    if not DB_PATH.exists():
        print(f"no fake-deps DB found at {DB_PATH}", file=sys.stderr)
        return 1

    print(f"# fake-deps data dump")
    print(f"\n_db: `{DB_PATH}`  |  generated: {_ts(dt.datetime.now().timestamp())}_")

    with sqlite3.connect(DB_PATH) as conn:
        _slack(conn)
        _github(conn)
        _webhooks(conn)
        _llm(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
