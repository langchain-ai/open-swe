"""HTTP app for the full-flow E2E (served as langgraph dev's http.app).

Mounts, on top of the REAL ``agent.webapp`` app:
  - fake GitHub REST API  (/fake-gh/...)   the real open_pull_request hits this
  - fake Slack API         (/fake-slack/...) the real slack code hits this
  - mock UIs               (/mock/slack, /mock/github) what the user/Playwright sees
  - control + compose      (/control/*, /mock/slack/send) the test driver

Nothing here touches agent logic — it only stands in for the SaaS boundaries
and renders their state back as a user-facing UI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e2e_env  # noqa: E402
import patches  # noqa: E402

patches.apply()

import fakes  # noqa: E402
import httpx  # noqa: E402
from e2e_env import (  # noqa: E402
    BASE_URL,
    BOT_USER_ID,
    DEMO_CHANNEL,
    HUMAN_USER,
    REPO_ROOT,
    TEST_USERS,
)
from fastapi import HTTPException, Request  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

# Slack-user directory the fake ``users.info`` resolves: the default sender used
# by the automated tests plus the named manual-test users.
_SLACK_USERS: dict[str, dict[str, str]] = {
    HUMAN_USER: {"name": "devuser", "real_name": "Dev User", "email": "dev@example.com"},
    **{
        u["slack_id"]: {"name": u["login"], "real_name": u["name"], "email": u["email"]}
        for u in TEST_USERS
    },
}

from agent.dashboard.oauth import COOKIE_NAME, issue_session  # noqa: E402
from agent.webapp import app, generate_thread_id_from_slack_thread  # noqa: E402

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
STATIC_DIR = Path(__file__).parent / "static"

CURRENT_THREAD: dict[str, str | None] = {"channel": DEMO_CHANNEL, "thread_ts": None}

fakes.seed_bare_remote()


# --- control + Slack compose (the test driver) -----------------------------
@app.post("/control/reset")
async def control_reset() -> JSONResponse:
    fakes.reset()
    CURRENT_THREAD["thread_ts"] = None
    return JSONResponse({"ok": True})


@app.get("/control/state")
async def control_state() -> JSONResponse:
    return JSONResponse(
        {"channel": CURRENT_THREAD["channel"], "thread_ts": CURRENT_THREAD["thread_ts"]}
    )


@app.post("/mock/slack/send")
async def slack_send(request: Request) -> JSONResponse:
    """Simulate a user posting in Slack: store the message, then deliver the
    signed Events-API webhook to the real /webhooks/slack route."""
    form = await request.json()
    text = str(form.get("text", ""))
    mention_bot = bool(form.get("mention_bot", True))
    # Sender defaults to the first test user (Alice) — the canonical owner the
    # automated tests log in as; the mock UI passes the chosen test user.
    user_id = str(form.get("user") or TEST_USERS[0]["slack_id"])
    channel = DEMO_CHANNEL

    ts = fakes.new_thread_ts()
    CURRENT_THREAD["thread_ts"] = ts
    fakes.add_slack_message(channel, ts, user=user_id, text=text, is_bot=False)

    payload = {
        "type": "event_callback",
        "event_id": f"Ev{ts}",
        "authorizations": [{"user_id": BOT_USER_ID}],
        "event": {
            "type": "app_mention" if mention_bot else "message",
            "channel": channel,
            "user": user_id,
            "text": text,
            "ts": ts,
            "thread_ts": ts,
        },
    }
    raw = json.dumps(payload).encode()
    req_ts = str(int(time.time()))
    base = f"v0:{req_ts}:{raw.decode()}".encode()
    sig = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://harness") as client:
        resp = await client.post(
            "/webhooks/slack",
            content=raw,
            headers={
                "X-Slack-Signature": sig,
                "X-Slack-Request-Timestamp": req_ts,
                "Content-Type": "application/json",
            },
        )
    return JSONResponse(
        {
            "thread_ts": ts,
            "thread_id": generate_thread_id_from_slack_thread(channel, ts),
            "webhook_status": resp.status_code,
            "webhook": resp.json(),
        }
    )


@app.post("/control/login")
async def control_login(request: Request) -> JSONResponse:
    """Simulate a signed-in dashboard user by minting the real session cookie."""
    form = await request.json()
    login = str(form.get("login", "dev-user"))
    email = str(form.get("email", "dev@example.com"))
    token = issue_session(login=login, email=email, avatar_url=None)
    resp = JSONResponse({"ok": True, "login": login, "email": email})
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=False, path="/")
    return resp


@app.get("/control/login")
async def control_login_get(login: str = "", email: str = "", next_url: str = "") -> Response:
    """Browser login. With no ``login``, render a dropdown of the test users;
    with ``?login=<u>`` (email resolved from the registry, or pass ``&email=``),
    mint the session cookie and redirect into the dashboard. Use a separate
    browser/profile per user — each has its own cookie jar."""
    # Land on the dashboard origin (DASHBOARD_BASE_URL — the Vite HMR server in
    # dev:mock), not this harness, so the cookie + the hot-reloading UI line up.
    ui = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    dest = next_url or (f"{ui}/agents" if ui else "/agents")
    if not login:
        options = "".join(f'<option value="{u["login"]}">{u["name"]}</option>' for u in TEST_USERS)
        return HTMLResponse(
            f"""<!doctype html><meta charset=utf-8><title>Mock login</title>
            <body style="font-family:system-ui;max-width:420px;margin:3rem auto;padding:0 1rem">
            <h1 style="font-size:1.1rem">Sign in (mock)</h1>
            <form method=get action=/control/login>
              <select name=login style="font:inherit;padding:0.4rem">{options}</select>
              <button style="font:inherit;padding:0.45rem 0.9rem;cursor:pointer">Sign in</button>
            </form>
            <p style="color:#888;font-size:0.85rem">Tip: use a separate browser or profile per
            user so their sessions don't overwrite each other.</p>
            </body>"""
        )
    if not email:
        match = next((u for u in TEST_USERS if u["login"] == login), None)
        email = match["email"] if match else f"{login}@example.com"
    token = issue_session(login=login, email=email, avatar_url=None)
    resp = RedirectResponse(url=dest, status_code=303)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=False, path="/")
    return resp


@app.get("/dashboard/api/auth/login")
async def mock_github_login(redirect_to: str = "", login: str = "") -> Response:
    """Mock stand-in for GitHub OAuth: the dashboard's "Continue with GitHub"
    button lands here. With no ``login``, render a picker of the fake GitHub
    test users; once one is chosen, mint the real session cookie and redirect
    back into the dashboard (``redirect_to``)."""
    ui = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    dest = redirect_to or (f"{ui}/agents" if ui else "/agents")
    if not login:
        options = "".join(
            f'<option value="{u["login"]}">{u["name"]} (@{u["login"]})</option>' for u in TEST_USERS
        )
        return HTMLResponse(
            f"""<!doctype html><meta charset=utf-8><title>Continue with GitHub (mock)</title>
            <body style="font-family:system-ui;max-width:420px;margin:3rem auto;padding:0 1rem">
            <h1 style="font-size:1.1rem">Continue with GitHub (mock)</h1>
            <p style="color:#888;font-size:0.9rem">Pick a fake GitHub account to sign in as.</p>
            <form method=get action=/dashboard/api/auth/login>
              <input type=hidden name=redirect_to value="{dest}">
              <select name=login style="font:inherit;padding:0.4rem">{options}</select>
              <button style="font:inherit;padding:0.45rem 0.9rem;cursor:pointer">Continue</button>
            </form>
            <p style="color:#888;font-size:0.85rem">Tip: use a separate browser or profile per
            user so their sessions don't overwrite each other.</p>
            </body>"""
        )
    match = next((u for u in TEST_USERS if u["login"] == login), None)
    email = match["email"] if match else f"{login}@example.com"
    token = issue_session(login=login, email=email, avatar_url=None)
    resp = RedirectResponse(url=dest, status_code=303)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=False, path="/")
    return resp


# The real dashboard registered /dashboard/api/auth/login first (via
# include_router), so Starlette would match it before ours. Move ours to the
# front of the table so the mock picker shadows the real OAuth redirect.
for _i, _route in enumerate(app.router.routes):
    if getattr(_route, "endpoint", None) is mock_github_login:
        app.router.routes.insert(0, app.router.routes.pop(_i))
        break


@app.post("/control/logout")
async def control_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# --- serve the REAL built ui/ SPA, same-origin so the session cookie works ----
# The "Open in Web" link (DASHBOARD_BASE_URL/agents/{id}) lands on the real app;
# it calls /dashboard/api/* (same origin) and streams via the dashboard proxy.
UI_PUBLIC = REPO_ROOT / "ui" / ".output" / "public"
_ASSETS_ROOT = (UI_PUBLIC / "assets").resolve()


def _ui_file(name: str) -> FileResponse:
    path = UI_PUBLIC / name
    if not path.is_file():
        raise HTTPException(404, f"{name} not built — run `bun run build` in ui/")
    return FileResponse(path)


@app.get("/assets/{asset_path:path}")
async def ui_asset(asset_path: str) -> FileResponse:
    # Explicit route, not app.mount(StaticFiles): LangGraph's custom-app loader
    # serves APIRoutes but drops sub-app Mounts, so a mount 404s under it.
    target = (_ASSETS_ROOT / asset_path).resolve()
    if not str(target).startswith(str(_ASSETS_ROOT)) or not target.is_file():
        raise HTTPException(404, "asset not found")
    return FileResponse(target)


@app.get("/_shell.html", response_class=HTMLResponse)
async def ui_shell() -> FileResponse:
    return _ui_file("_shell.html")


@app.get("/manifest.webmanifest")
async def ui_manifest() -> FileResponse:
    return _ui_file("manifest.webmanifest")


@app.get("/favicon.png")
async def ui_favicon() -> FileResponse:
    return _ui_file("favicon.png")


@app.get("/apple-touch-icon.png")
async def ui_apple_icon() -> FileResponse:
    return _ui_file("apple-touch-icon.png")


@app.get("/logo-mark.png")
async def ui_logo_mark() -> FileResponse:
    return _ui_file("logo-mark.png")


# Client routes used by the handoff tests: serve the SPA shell; the client
# router boots at the current URL. Kept explicit (no catch-all) so LangGraph's
# own root routes — which the dashboard proxy calls server-side — are untouched.
@app.get("/agents/{thread_id}", response_class=HTMLResponse)
async def ui_agents_thread(thread_id: str) -> FileResponse:  # noqa: ARG001
    return _ui_file("_shell.html")


@app.get("/agents/{thread_id}/plan", response_class=HTMLResponse)
async def ui_agents_plan(thread_id: str) -> FileResponse:  # noqa: ARG001
    return _ui_file("_shell.html")


@app.get("/login", response_class=HTMLResponse)
async def ui_login() -> FileResponse:
    return _ui_file("_shell.html")


@app.get("/mock/users")
async def mock_users() -> JSONResponse:
    """The named test users that drive the Slack sender + login dropdowns."""
    return JSONResponse(TEST_USERS)


@app.get("/mock/slack/messages")
async def slack_messages() -> JSONResponse:
    thread = CURRENT_THREAD["thread_ts"]
    msgs = fakes.slack_thread(CURRENT_THREAD["channel"], thread) if thread else []
    return JSONResponse(
        [{"user": m["user"], "text": m["text"], "is_bot": m["is_bot"], "ts": m["ts"]} for m in msgs]
    )


# --- mock UIs --------------------------------------------------------------
@app.get("/mock/slack", response_class=HTMLResponse)
async def mock_slack_page() -> str:
    return (STATIC_DIR / "slack.html").read_text()


@app.get("/mock/github", response_class=HTMLResponse)
async def mock_github_page() -> str:
    return (STATIC_DIR / "github.html").read_text()


def _pr_html_url(pr: dict[str, Any]) -> str:
    return f"{BASE_URL}/mock/github/{pr['owner']}/{pr['repo']}/pull/{pr['number']}"


@app.get("/mock/github/data")
async def mock_github_data() -> JSONResponse:
    return JSONResponse(
        [
            {
                "number": p["number"],
                "title": p["title"],
                "head": p["head"],
                "base": p["base"],
                "state": p["state"],
                "draft": p["draft"],
                "author": p["author"],
                "body": p["body"],
                "files": p["files"],
                "url": _pr_html_url(p),
            }
            for p in fakes.PULLS
        ]
    )


@app.get("/mock/github/{owner}/{repo}/pull/{number}", response_class=HTMLResponse)
async def mock_github_pr(owner: str, repo: str, number: int) -> HTMLResponse:  # noqa: ARG001
    pr = fakes.find_pull(number)
    if pr is None:
        return HTMLResponse(f"<h1>PR #{number} not found</h1>", status_code=404)
    files = "".join(
        f'<li data-file="{f["filename"]}">{f["filename"]} '
        f"<span class='stat'>+{f['additions']} −{f['deletions']}</span></li>"
        for f in pr["files"]
    )
    draft = " (draft)" if pr["draft"] else ""
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8>
        <title>PR #{pr["number"]} — {pr["owner"]}/{pr["repo"]}</title>
        <body style="font-family:system-ui;max-width:720px;margin:2rem auto">
        <p><a href="/mock/github">← all pull requests</a></p>
        <h1 id="pr-title">{pr["title"]}{draft}</h1>
        <p>#{pr["number"]} · <span id="pr-state">{pr["state"]}</span> ·
           <code id="pr-head">{pr["head"]}</code> → <code>{pr["base"]}</code> ·
           by <span id="pr-author">{pr["author"]}</span></p>
        <h3>Description</h3><pre id="pr-body">{pr["body"]}</pre>
        <h3>Files changed ({len(pr["files"])})</h3>
        <ul id="pr-files">{files}</ul>
        </body>"""
    )


# --- fake GitHub REST API (open_pull_request hits this) --------------------
def _gh_pr_json(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pr["number"],
        "html_url": _pr_html_url(pr),
        "state": pr["state"],
        "draft": pr["draft"],
        "merged": pr["merged"],
        "title": pr["title"],
        "body": pr["body"],
        "user": {"login": pr["author"]},
        "head": {"ref": pr["head"]},
        "base": {"ref": pr["base"]},
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": len(pr["files"]),
    }


@app.get("/fake-gh/repos/{owner}/{repo}")
async def gh_get_repo(owner: str, repo: str) -> JSONResponse:
    return JSONResponse({"full_name": f"{owner}/{repo}", "private": False})


@app.get("/fake-gh/repos/{owner}/{repo}/pulls")
async def gh_list_pulls(owner: str, repo: str) -> JSONResponse:  # noqa: ARG001
    return JSONResponse([])


@app.post("/fake-gh/repos/{owner}/{repo}/pulls")
async def gh_create_pull(owner: str, repo: str, request: Request) -> JSONResponse:
    body = await request.json()
    pr = fakes.create_pull(
        owner,
        repo,
        head=body.get("head", ""),
        base=body.get("base", "main"),
        title=body.get("title", ""),
        body=body.get("body", ""),
        draft=bool(body.get("draft", True)),
    )
    return JSONResponse(_gh_pr_json(pr), status_code=201)


@app.get("/fake-gh/repos/{owner}/{repo}/pulls/{number}")
async def gh_get_pull(owner: str, repo: str, number: int) -> JSONResponse:  # noqa: ARG001
    pr = fakes.find_pull(number)
    if pr is None:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    return JSONResponse(_gh_pr_json(pr))


# --- fake Slack API (real slack code hits this) ----------------------------
def _ok(extra: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse({"ok": True, **(extra or {})})


@app.post("/fake-slack/chat.postMessage")
async def slack_post_message(request: Request) -> JSONResponse:
    body = await request.json()
    ts = fakes.add_slack_message(
        body.get("channel", ""),
        body.get("thread_ts", ""),
        user=BOT_USER_ID,
        text=body.get("text", ""),
        blocks=body.get("blocks"),
        is_bot=True,
    )
    return _ok({"ts": ts, "message": {"ts": ts}})


@app.post("/fake-slack/chat.postEphemeral")
async def slack_post_ephemeral(request: Request) -> JSONResponse:
    await request.body()
    return _ok({"message_ts": fakes.next_slack_ts()})


@app.post("/fake-slack/assistant.threads.setStatus")
async def slack_set_status(request: Request) -> JSONResponse:
    await request.body()
    return _ok()


@app.post("/fake-slack/reactions.add")
async def slack_reactions_add(request: Request) -> JSONResponse:
    await request.body()
    return _ok()


@app.get("/fake-slack/users.info")
async def slack_users_info(user: str = "") -> JSONResponse:
    info = _SLACK_USERS.get(
        user, {"name": "devuser", "real_name": "Dev User", "email": "dev@example.com"}
    )
    return _ok(
        {
            "user": {
                "id": user,
                "name": info["name"],
                "real_name": info["real_name"],
                "profile": {
                    "email": info["email"],
                    "display_name": info["real_name"],
                    "real_name": info["real_name"],
                },
            }
        }
    )


@app.get("/fake-slack/conversations.info")
async def slack_conversations_info(channel: str = "") -> JSONResponse:
    return _ok(
        {
            "channel": {
                "id": channel,
                "name": "demo",
                "name_normalized": "demo",
                "topic": {"value": "Demo channel topic"},
                "purpose": {"value": "Demo channel purpose"},
            }
        }
    )


@app.get("/fake-slack/conversations.replies")
async def slack_conversations_replies(channel: str = "", ts: str = "") -> JSONResponse:
    msgs = fakes.slack_thread(channel, ts)
    return _ok(
        {
            "messages": [
                {
                    "type": "message",
                    "user": m["user"],
                    "text": m["text"],
                    "ts": m["ts"],
                    "thread_ts": m["thread_ts"],
                }
                for m in msgs
            ]
        }
    )


@app.get("/fake-slack/conversations.history")
async def slack_conversations_history(channel: str = "") -> JSONResponse:  # noqa: ARG001
    return _ok({"messages": []})


@app.get("/fake-slack/chat.getPermalink")
async def slack_get_permalink(channel: str = "", message_ts: str = "") -> JSONResponse:  # noqa: ARG001
    return _ok({"permalink": f"{BASE_URL}/mock/slack"})


# Quietly reference imports used only for env side effects.
_ = (e2e_env, HUMAN_USER)
