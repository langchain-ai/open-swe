"""GitHub mock — routes under /github.

Covers the slice of the GitHub REST + GraphQL + git smart-HTTP surface the
open-swe agent actually uses. State persisted in SQLite; repo working-trees
live on disk under ``sample_repos/``.

Smart-HTTP paths (real ``git clone/fetch/push``):

    GET  /git/{owner}/{repo}.git/info/refs
    POST /git/{owner}/{repo}.git/git-upload-pack
    POST /git/{owner}/{repo}.git/git-receive-pack

REST / GraphQL paths (proxied from ``GITHUB_API_BASE_URL``):

    POST /github/api/app/installations/{id}/access_tokens
    GET  /github/api/user
    GET  /github/api/orgs/{org}/repos
    GET  /github/api/users/{user}/repos
    GET  /github/api/repos/{owner}/{repo}
    GET/POST /github/api/repos/{owner}/{repo}/issues
    GET  /github/api/repos/{owner}/{repo}/issues/{n}
    GET/POST /github/api/repos/{owner}/{repo}/issues/{n}/comments
    POST /github/api/repos/{owner}/{repo}/issues/comments/{id}/reactions
    GET  /github/api/repos/{owner}/{repo}/pulls
    GET  /github/api/repos/{owner}/{repo}/pulls/{n}
    POST /github/api/repos/{owner}/{repo}/pulls
    POST /github/api/graphql    (addReaction mutation only)

UI paths:

    GET  /github/ui/state
    POST /github/ui/trigger_webhook   (signs + POSTs an issue_comment event)
    POST /github/ui/seed_sample       (re-seed the sample repo)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import db, events

logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "dev-secret")
FORWARD_URL = os.environ.get(
    "FAKE_GITHUB_FORWARD_URL", "http://localhost:2025/webhooks/github"
)

REPOS_DIR = Path(__file__).resolve().parent / "sample_repos"
SAMPLE_OWNER = "demo-org"
SAMPLE_REPO = "hello"
SAMPLE_BOT_LOGIN = "openswe-bot"
SAMPLE_USER_LOGIN_FALLBACK = "local-user"


def _resolve_current_gh_user() -> tuple[str, str, str]:
    """Resolve (login, name, email) from the local ``gh`` CLI session.

    Falls back to a generic local-user identity if ``gh`` is not installed
    or not authenticated — fake-deps still runs without network access.
    """
    try:
        out = subprocess.run(
            ["gh", "api", "user"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        data = json.loads(out)
        login = data.get("login") or SAMPLE_USER_LOGIN_FALLBACK
        name = data.get("name") or login
        email = data.get("email") or f"{login}@local"
        return login, name, email
    except Exception:
        return SAMPLE_USER_LOGIN_FALLBACK, "Local User", "local@local"


SAMPLE_USER_LOGIN, _SAMPLE_USER_NAME, _SAMPLE_USER_EMAIL = _resolve_current_gh_user()

router = APIRouter(prefix="/github")
git_router = APIRouter(prefix="/git")


# --- seed -------------------------------------------------------------------


async def seed() -> None:
    """Ensure default users, one sample repo on disk, one sample issue."""
    for login, type_, name, email in (
        (SAMPLE_USER_LOGIN, "User", _SAMPLE_USER_NAME, _SAMPLE_USER_EMAIL),
        (SAMPLE_BOT_LOGIN, "Bot", "Open SWE", "bot@example.com"),
    ):
        await db.execute(
            "INSERT OR IGNORE INTO gh_users (login, name, email, type) VALUES (?,?,?,?)",
            (login, name, email, type_),
        )

    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = REPOS_DIR / f"{SAMPLE_OWNER}__{SAMPLE_REPO}.git"
    if not repo_path.exists():
        _init_bare_sample_repo(repo_path)
    await db.execute(
        "INSERT OR IGNORE INTO gh_repos "
        "(owner, name, description, default_branch, private, path_on_disk, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            SAMPLE_OWNER,
            SAMPLE_REPO,
            "Tiny sample repo for fake-deps demos",
            "main",
            0,
            str(repo_path),
            time.time(),
        ),
    )

    existing = await db.fetchone(
        "SELECT number FROM gh_issues WHERE owner=? AND repo=? ORDER BY number DESC LIMIT 1",
        (SAMPLE_OWNER, SAMPLE_REPO),
    )
    if not existing:
        await db.execute(
            "INSERT INTO gh_issues "
            "(owner, repo, number, title, body, state, user_login, is_pull, created_at) "
            "VALUES (?, ?, 1, ?, ?, 'open', ?, 0, ?)",
            (
                SAMPLE_OWNER,
                SAMPLE_REPO,
                "Add a greeting in French",
                "The app says hello in English. Please also greet the user in French.",
                SAMPLE_USER_LOGIN,
                time.time(),
            ),
        )


def _init_bare_sample_repo(bare_path: Path) -> None:
    """Create a bare repo with an initial commit containing README + hello.py."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        (work / "README.md").write_text(
            "# hello\n\nTiny sample repo used by fake-deps to exercise the agent.\n"
            "Edit `hello.py` to change the greeting.\n"
        )
        (work / "hello.py").write_text(
            'def greet(name: str) -> str:\n'
            '    return f"Hello, {name}!"\n\n\n'
            'if __name__ == "__main__":\n'
            '    print(greet("world"))\n'
        )
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "fake-deps")
        env.setdefault("GIT_AUTHOR_EMAIL", "fake-deps@example.invalid")
        env.setdefault("GIT_COMMITTER_NAME", "fake-deps")
        env.setdefault("GIT_COMMITTER_EMAIL", "fake-deps@example.invalid")
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
        subprocess.run(
            ["git", "-C", str(work), "commit", "-q", "-m", "initial commit"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(work), str(bare_path)], check=True, env=env
        )
        # Allow receive-pack so demos can push if desired.
        subprocess.run(
            [
                "git",
                "-C",
                str(bare_path),
                "config",
                "http.receivepack",
                "true",
            ],
            check=True,
        )
        shutil.rmtree(work, ignore_errors=True)


# --- git smart-HTTP ---------------------------------------------------------


def _git_http_backend_path() -> str:
    exec_path = subprocess.check_output(["git", "--exec-path"]).decode().strip()
    return os.path.join(exec_path, "git-http-backend")


async def _git_http_backend(request: Request, subpath: str, owner: str, repo: str) -> Response:
    row = await db.fetchone(
        "SELECT path_on_disk FROM gh_repos WHERE owner = ? AND name = ?", (owner, repo)
    )
    if not row:
        return Response(status_code=404, content=b"repo not found")
    repo_path = row["path_on_disk"]

    body = await request.body()
    env = os.environ.copy()
    env.update(
        {
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_PROJECT_ROOT": str(Path(repo_path).parent),
            "PATH_INFO": f"/{Path(repo_path).name}/{subpath}",
            "REQUEST_METHOD": request.method,
            "QUERY_STRING": request.url.query or "",
            "CONTENT_TYPE": request.headers.get("content-type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": "fake-deps",
            "REMOTE_ADDR": request.client.host if request.client else "127.0.0.1",
        }
    )
    proc = await asyncio.create_subprocess_exec(
        _git_http_backend_path(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(input=body)
    if proc.returncode != 0:
        logger.warning("git-http-backend failed: %s", stderr.decode(errors="replace"))

    # Parse CGI response: headers + blank line + body.
    sep = stdout.find(b"\r\n\r\n")
    if sep == -1:
        sep = stdout.find(b"\n\n")
        header_end = sep
        body_start = sep + 2
    else:
        header_end = sep
        body_start = sep + 4
    if sep == -1:
        return Response(content=stdout, media_type="application/octet-stream")

    header_block = stdout[:header_end].decode(errors="replace")
    resp_body = stdout[body_start:]
    status = 200
    headers: dict[str, str] = {}
    for line in header_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k.lower() == "status":
                try:
                    status = int(v.split()[0])
                except ValueError:
                    pass
                continue
            headers[k] = v
    return Response(
        content=resp_body,
        status_code=status,
        headers=headers,
        media_type=headers.get("Content-Type", "application/octet-stream"),
    )


@git_router.get("/{owner}/{repo_name:path}")
async def git_get(owner: str, repo_name: str, request: Request) -> Response:
    """Catch-all GET for smart-HTTP info/refs and file-dumb requests."""
    repo, _, sub = repo_name.partition("/")
    if not repo.endswith(".git"):
        return Response(status_code=404)
    return await _git_http_backend(request, sub, owner, repo[:-4])


@git_router.post("/{owner}/{repo_name:path}")
async def git_post(owner: str, repo_name: str, request: Request) -> Response:
    """Catch-all POST for upload-pack / receive-pack."""
    repo, _, sub = repo_name.partition("/")
    if not repo.endswith(".git"):
        return Response(status_code=404)
    return await _git_http_backend(request, sub, owner, repo[:-4])


# --- REST: auth + repo listing ---------------------------------------------


@router.post("/api/app/installations/{installation_id}/access_tokens")
async def create_installation_token(installation_id: str) -> dict[str, Any]:
    return {
        "token": "ghs_" + uuid.uuid4().hex,
        "expires_at": "2099-12-31T23:59:59Z",
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "metadata": "read",
        },
        "repository_selection": "all",
    }


@router.get("/api/user")
async def current_user() -> dict[str, Any]:
    return {
        "login": SAMPLE_BOT_LOGIN,
        "id": 1,
        "type": "Bot",
        "name": "Open SWE",
        "email": "bot@example.com",
    }


def _repo_row_to_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": abs(hash((r["owner"], r["name"]))) % (10**9),
        "name": r["name"],
        "full_name": f"{r['owner']}/{r['name']}",
        "owner": {"login": r["owner"], "type": "Organization"},
        "description": r["description"] or "",
        "default_branch": r["default_branch"],
        "private": bool(r["private"]),
        "clone_url": f"http://localhost:13765/git/{r['owner']}/{r['name']}.git",
        "html_url": f"http://localhost:13765/github/ui/#repo/{r['owner']}/{r['name']}",
    }


@router.get("/api/orgs/{org}/repos")
async def list_org_repos(org: str) -> list[dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM gh_repos WHERE owner = ? ORDER BY name", (org,))
    return [_repo_row_to_api(r) for r in rows]


@router.get("/api/users/{user}/repos")
async def list_user_repos(user: str) -> list[dict[str, Any]]:
    return await list_org_repos(user)


@router.get("/api/repos/{owner}/{repo}")
async def get_repo(owner: str, repo: str) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT * FROM gh_repos WHERE owner = ? AND name = ?", (owner, repo)
    )
    if not row:
        return {"message": "Not Found"}
    return _repo_row_to_api(row)


# --- REST: issues + comments + reactions -----------------------------------


def _issue_to_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "number": r["number"],
        "title": r["title"],
        "body": r["body"],
        "state": r["state"],
        "user": {"login": r["user_login"]},
        "pull_request": {"url": ""} if r["is_pull"] else None,
        "created_at": r["created_at"],
        "html_url": f"http://localhost:13765/github/ui/#issue/{r['owner']}/{r['repo']}/{r['number']}",
        "node_id": f"I_{r['id']:08d}",
    }


def _comment_to_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "body": r["body"],
        "user": {"login": r["user_login"]},
        "node_id": r["node_id"],
        "created_at": r["created_at"],
        "html_url": (
            f"http://localhost:13765/github/ui/#issue/{r['owner']}/{r['repo']}/"
            f"{r['issue_number']}#comment-{r['id']}"
        ),
    }


@router.get("/api/repos/{owner}/{repo}/issues")
async def list_issues(owner: str, repo: str) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM gh_issues WHERE owner=? AND repo=? ORDER BY number DESC",
        (owner, repo),
    )
    return [_issue_to_api(r) for r in rows]


@router.get("/api/repos/{owner}/{repo}/issues/{number}")
async def get_issue(owner: str, repo: str, number: int) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT * FROM gh_issues WHERE owner=? AND repo=? AND number=?",
        (owner, repo, number),
    )
    if not row:
        return {"message": "Not Found"}
    return _issue_to_api(row)


@router.get("/api/repos/{owner}/{repo}/issues/{number}/comments")
async def list_issue_comments(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM gh_comments WHERE owner=? AND repo=? AND issue_number=? ORDER BY id",
        (owner, repo, number),
    )
    return [_comment_to_api(r) for r in rows]


@router.post("/api/repos/{owner}/{repo}/issues/{number}/comments")
async def create_issue_comment(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    body = payload.get("body", "")
    # Real GitHub attributes the comment to the token's owner — you cannot
    # forge it via payload.user.login. Match that behavior.
    user = _resolve_login_from_auth(request)
    node_id = f"IC_{uuid.uuid4().hex[:12]}"
    comment_id = await db.execute(
        "INSERT INTO gh_comments "
        "(owner, repo, issue_number, body, user_login, node_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner, repo, number, body, user, node_id, time.time()),
    )
    row = await db.fetchone("SELECT * FROM gh_comments WHERE id = ?", (comment_id,))
    serialized = _comment_to_api(row)
    await events.broadcast(
        {
            "type": "gh_comment_added",
            "owner": owner,
            "repo": repo,
            "issue_number": number,
            "comment": serialized,
        }
    )
    return serialized


def _resolve_login_from_auth(request: Request) -> str:
    # Fake: any presented token is accepted and mapped to the bot.
    auth = request.headers.get("authorization", "")
    if auth:
        return SAMPLE_BOT_LOGIN
    return SAMPLE_USER_LOGIN


@router.post("/api/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions")
async def react_issue_comment(owner: str, repo: str, comment_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    content = payload.get("content", "eyes")
    user = _resolve_login_from_auth(request)
    await db.execute(
        "INSERT INTO gh_reactions "
        "(target_type, target_id, user_login, content, created_at) "
        "VALUES ('issue_comment', ?, ?, ?, ?)",
        (comment_id, user, content, time.time()),
    )
    await events.broadcast(
        {
            "type": "gh_reaction_added",
            "target_type": "issue_comment",
            "target_id": comment_id,
            "content": content,
        }
    )
    return {"id": 1, "content": content, "user": {"login": user}}


@router.post("/api/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions")
async def react_pr_review_comment(owner: str, repo: str, comment_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    content = payload.get("content", "eyes")
    await db.execute(
        "INSERT INTO gh_reactions "
        "(target_type, target_id, user_login, content, created_at) "
        "VALUES ('pr_review_comment', ?, ?, ?, ?)",
        (comment_id, _resolve_login_from_auth(request), content, time.time()),
    )
    return {"id": 1, "content": content}


@router.post("/api/repos/{owner}/{repo}/pulls/{pr_number}/reviews/{comment_id}/reactions")
async def react_pr_review(owner: str, repo: str, pr_number: int, comment_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    content = payload.get("content", "eyes")
    await db.execute(
        "INSERT INTO gh_reactions "
        "(target_type, target_id, user_login, content, created_at) "
        "VALUES ('pr_review', ?, ?, ?, ?)",
        (comment_id, _resolve_login_from_auth(request), content, time.time()),
    )
    return {"id": 1, "content": content}


# --- REST: pulls -----------------------------------------------------------


def _pull_to_api(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "number": r["number"],
        "title": r["title"],
        "body": r["body"],
        "state": r["state"],
        "draft": bool(r["draft"]),
        "head": {"ref": r["head_ref"]},
        "base": {"ref": r["base_ref"]},
        "user": {"login": r["user_login"]},
        "created_at": r["created_at"],
        "html_url": (
            f"http://localhost:13765/github/ui/#pr/{r['owner']}/{r['repo']}/{r['number']}"
        ),
        "node_id": f"PR_{r['id']:08d}",
    }


@router.get("/api/repos/{owner}/{repo}/pulls")
async def list_pulls(owner: str, repo: str) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT * FROM gh_pulls WHERE owner=? AND repo=? ORDER BY number DESC",
        (owner, repo),
    )
    return [_pull_to_api(r) for r in rows]


@router.get("/api/repos/{owner}/{repo}/pulls/{number}")
async def get_pull(owner: str, repo: str, number: int) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT * FROM gh_pulls WHERE owner=? AND repo=? AND number=?",
        (owner, repo, number),
    )
    if not row:
        return {"message": "Not Found"}
    return _pull_to_api(row)


@router.post("/api/repos/{owner}/{repo}/pulls")
async def create_pull(owner: str, repo: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    title = payload.get("title", "")
    body = payload.get("body", "")
    head = payload.get("head", "")
    base = payload.get("base", "main")
    draft = 1 if payload.get("draft") else 0
    user = _resolve_login_from_auth(request)
    existing = await db.fetchone(
        "SELECT number FROM gh_pulls WHERE owner=? AND repo=? ORDER BY number DESC LIMIT 1",
        (owner, repo),
    )
    number = (existing["number"] if existing else 0) + 1
    await db.execute(
        "INSERT INTO gh_pulls "
        "(owner, repo, number, title, body, state, draft, head_ref, base_ref, user_login, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
        (owner, repo, number, title, body, draft, head, base, user, time.time()),
    )
    row = await db.fetchone(
        "SELECT * FROM gh_pulls WHERE owner=? AND repo=? AND number=?",
        (owner, repo, number),
    )
    serialized = _pull_to_api(row)
    await events.broadcast(
        {"type": "gh_pr_created", "owner": owner, "repo": repo, "pr": serialized}
    )
    return serialized


# --- GraphQL (addReaction only) --------------------------------------------


@router.post("/api/graphql")
async def graphql(request: Request) -> dict[str, Any]:
    payload = await request.json()
    query = payload.get("query", "")
    variables = payload.get("variables", {})
    if "addReaction" not in query:
        return {"errors": [{"message": "unsupported query"}]}
    subject_id = variables.get("subjectId", "")
    await db.execute(
        "INSERT INTO gh_reactions "
        "(target_type, target_id, user_login, content, created_at) "
        "VALUES ('graphql', ?, ?, ?, ?)",
        (abs(hash(subject_id)) % (10**9), SAMPLE_BOT_LOGIN, "EYES", time.time()),
    )
    return {"data": {"addReaction": {"reaction": {"content": "EYES"}}}}


# --- UI ---------------------------------------------------------------------


@router.get("/ui/state")
async def ui_state() -> dict[str, Any]:
    repos = await db.fetchall("SELECT * FROM gh_repos ORDER BY owner, name")
    issues = await db.fetchall("SELECT * FROM gh_issues ORDER BY created_at DESC")
    pulls = await db.fetchall("SELECT * FROM gh_pulls ORDER BY created_at DESC")
    comments = await db.fetchall("SELECT * FROM gh_comments ORDER BY id DESC LIMIT 100")
    reactions = await db.fetchall("SELECT * FROM gh_reactions ORDER BY id DESC LIMIT 100")
    return {
        "default_user": SAMPLE_USER_LOGIN,
        "bot_user": SAMPLE_BOT_LOGIN,
        "repos": [_repo_row_to_api(r) for r in repos],
        "issues": [_issue_to_api(r) for r in issues],
        "pulls": [_pull_to_api(r) for r in pulls],
        "comments": [_comment_to_api(r) for r in comments],
        "reactions": reactions,
    }


@router.post("/ui/create_issue")
async def ui_create_issue(request: Request) -> dict[str, Any]:
    payload = await request.json()
    owner = payload["owner"]
    repo = payload["repo"]
    existing = await db.fetchone(
        "SELECT number FROM gh_issues WHERE owner=? AND repo=? ORDER BY number DESC LIMIT 1",
        (owner, repo),
    )
    number = (existing["number"] if existing else 0) + 1
    await db.execute(
        "INSERT INTO gh_issues (owner, repo, number, title, body, state, user_login, is_pull, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?, 0, ?)",
        (
            owner,
            repo,
            number,
            payload.get("title", "Untitled"),
            payload.get("body", ""),
            SAMPLE_USER_LOGIN,
            time.time(),
        ),
    )
    row = await db.fetchone(
        "SELECT * FROM gh_issues WHERE owner=? AND repo=? AND number=?", (owner, repo, number)
    )
    serialized = _issue_to_api(row)
    await events.broadcast({"type": "gh_issue_created", "issue": serialized})
    return serialized


@router.post("/ui/post_comment_and_trigger")
async def ui_post_comment_and_trigger(request: Request) -> dict[str, Any]:
    """Post an issue comment as the default human user AND fire /webhooks/github."""
    payload = await request.json()
    owner = payload["owner"]
    repo = payload["repo"]
    number = int(payload["number"])
    body = payload.get("body", "")

    node_id = f"IC_{uuid.uuid4().hex[:12]}"
    comment_id = await db.execute(
        "INSERT INTO gh_comments "
        "(owner, repo, issue_number, body, user_login, node_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner, repo, number, body, SAMPLE_USER_LOGIN, node_id, time.time()),
    )
    comment_row = await db.fetchone("SELECT * FROM gh_comments WHERE id = ?", (comment_id,))
    comment_serialized = _comment_to_api(comment_row)
    await events.broadcast(
        {
            "type": "gh_comment_added",
            "owner": owner,
            "repo": repo,
            "issue_number": number,
            "comment": comment_serialized,
        }
    )

    issue_row = await db.fetchone(
        "SELECT * FROM gh_issues WHERE owner=? AND repo=? AND number=?",
        (owner, repo, number),
    )
    if not issue_row:
        return {"ok": False, "error": "issue not found"}

    repo_row = await db.fetchone(
        "SELECT * FROM gh_repos WHERE owner=? AND name=?", (owner, repo)
    )
    event_payload = {
        "action": "created",
        "issue": _issue_to_api(issue_row),
        "comment": comment_serialized,
        "repository": _repo_row_to_api(repo_row) if repo_row else {},
        "sender": {"login": SAMPLE_USER_LOGIN, "type": "User"},
    }
    raw_body = json.dumps(event_payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()

    forwarded_status: int | None = None
    forwarded_response: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                FORWARD_URL,
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": signature,
                    "X-GitHub-Event": "issue_comment",
                    "X-GitHub-Delivery": uuid.uuid4().hex,
                },
            )
        forwarded_status = resp.status_code
        try:
            forwarded_response = resp.json()
        except Exception:  # noqa: BLE001
            forwarded_response = {"body": resp.text}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Forward to %s failed", FORWARD_URL)
        forwarded_response = {"error": str(exc)}

    await db.execute(
        "INSERT INTO webhook_log (target, event_type, status, response, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("github", "issue_comment", forwarded_status or 0, json.dumps(forwarded_response), time.time()),
    )
    await events.broadcast(
        {
            "type": "webhook_result",
            "target": "github",
            "status": forwarded_status,
            "response": forwarded_response,
        }
    )
    return {
        "ok": True,
        "comment": comment_serialized,
        "forwarded_status": forwarded_status,
        "forwarded_response": forwarded_response,
    }


@router.get("/ui/repo_file")
async def ui_repo_file(owner: str, repo: str, path: str = "", ref: str = "HEAD") -> dict[str, Any]:
    """Return a single file's contents or a directory listing from disk."""
    row = await db.fetchone(
        "SELECT path_on_disk FROM gh_repos WHERE owner=? AND name=?", (owner, repo)
    )
    if not row:
        return {"error": "repo not found"}
    repo_path = row["path_on_disk"]
    try:
        if not path or path.endswith("/"):
            listing = subprocess.check_output(
                ["git", "-C", repo_path, "ls-tree", "--name-only", ref, path or ""],
                stderr=subprocess.DEVNULL,
            ).decode().splitlines()
            return {"kind": "dir", "path": path, "entries": listing}
        raw = subprocess.check_output(
            ["git", "-C", repo_path, "show", f"{ref}:{path}"], stderr=subprocess.DEVNULL
        )
        return {
            "kind": "file",
            "path": path,
            "content_b64": base64.b64encode(raw).decode(),
        }
    except subprocess.CalledProcessError as exc:
        return {"error": f"git returned {exc.returncode}"}


@router.post("/ui/reset")
async def ui_reset() -> dict[str, Any]:
    for table in ("gh_comments", "gh_reactions", "gh_pulls"):
        await db.execute(f"DELETE FROM {table}")
    await db.execute("DELETE FROM gh_issues WHERE number > 1")
    await events.broadcast({"type": "gh_reset"})
    return {"ok": True}
