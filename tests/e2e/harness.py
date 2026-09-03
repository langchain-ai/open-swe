"""HTTP app for the full-flow E2E (served as langgraph dev's http.app).

Mounts, on top of the REAL ``agent.webapp`` app:
  - fake GitHub REST API  (/fake-gh/...)   the real open_pull_request hits this
  - fake Slack API         (/fake-slack/...) the real slack code hits this
  - mock UIs               (/mock/slack, /mock/github) what the user/Playwright sees
  - control + compose      (/control/*, /mock/slack/send) the test driver

Nothing here touches agent logic — it only stands in for the SaaS boundaries
and renders their state back as a user-facing UI.
"""

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e2e_env  # noqa: E402
import patches  # noqa: E402

patches.apply()

import fakes  # noqa: E402
import httpx  # noqa: E402
import reviewer_apis  # noqa: E402
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
    PlainTextResponse,
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

from langgraph_sdk import get_client  # noqa: E402

from agent.api.app import app  # noqa: E402
from agent.dashboard.oauth import COOKIE_NAME, issue_session  # noqa: E402
from agent.slack.client import lookup_slack_thread_id  # noqa: E402

GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
STATIC_DIR = Path(__file__).parent / "static"

CURRENT_THREAD: dict[str, str | None] = {"channel": DEMO_CHANNEL, "thread_ts": None}
LAST_SLACK_EVENT: dict[str, Any] = {"payload": None}

# Message timestamps restart from a fixed base on every boot, but the store the
# webhook dedupes against is persisted — so event ids need a per-process salt or
# a rerun's mentions look like redeliveries of the previous run's.
EVENT_ID_SALT = uuid.uuid4().hex[:8]

fakes.seed_bare_remotes()

if os.environ.get("E2E_EXIT_WHEN_ORPHANED"):
    # Playwright closes the webServer stdin pipe when its runner exits.
    def _exit_when_orphaned() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:
            return
        os._exit(0)

    threading.Thread(target=_exit_when_orphaned, daemon=True).start()


# --- control + Slack compose (the test driver) -----------------------------
@app.post("/control/reset")
async def control_reset() -> JSONResponse:
    fakes.reset()
    CURRENT_THREAD["channel"] = DEMO_CHANNEL
    CURRENT_THREAD["thread_ts"] = None
    LAST_SLACK_EVENT["payload"] = None
    return JSONResponse({"ok": True})


@app.post("/control/prepare-sandbox-repo")
async def control_prepare_sandbox_repo() -> JSONResponse:
    fakes.seed_sandbox_repo()
    return JSONResponse({"ok": True})


@app.get("/control/state")
async def control_state() -> JSONResponse:
    return JSONResponse(
        {"channel": CURRENT_THREAD["channel"], "thread_ts": CURRENT_THREAD["thread_ts"]}
    )


@app.get("/control/snapshots")
async def control_snapshots() -> JSONResponse:
    """Snapshot captures/deletes the environment tools asked the platform for."""
    return JSONResponse({"captured": fakes.SNAPSHOTS, "deleted": fakes.DELETED_SNAPSHOTS})


@app.get("/control/last-system-prompt")
async def control_last_system_prompt() -> JSONResponse:
    """The system prompt of the most recent model call (what the agent was told)."""
    from fake_llm import LAST_SYSTEM_PROMPT

    return JSONResponse({"text": LAST_SYSTEM_PROMPT["text"]})


@app.post("/control/repo-private")
async def control_repo_private(request: Request) -> JSONResponse:
    body = await request.json()
    value = bool(body.get("private", False))
    fakes.set_repo_private(value)
    return JSONResponse({"ok": True, "private": value})


@app.post("/control/pull-request-health")
async def control_pull_request_health(request: Request) -> JSONResponse:
    body = await request.json()
    number = body.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise HTTPException(400, "A pull request number is required")
    pull = fakes.update_pull_health(number, body)
    if pull is None:
        raise HTTPException(404, "Pull request not found")
    return JSONResponse({"ok": True, "pull_request": fakes.pull_health_json(pull)})


@app.get("/control/queued")
async def control_queued(thread_id: str = "") -> JSONResponse:
    """Count the follow-ups parked on a busy thread's message queue.

    While the agent is busy, debounced follow-ups accumulate here (namespace
    ``("queue", thread_id)``) until the active run drains them together at its
    next model call. Lets the E2E assert coalescing instead of per-message runs."""
    from langgraph_sdk import get_client

    value: Any = None
    try:
        client = get_client(url=os.environ["LANGGRAPH_URL"])
        item = await client.store.get_item(("queue", thread_id), key="pending_messages")
        value = item.get("value") if item else None
    except Exception:  # noqa: BLE001
        value = None
    messages = value.get("messages") if isinstance(value, dict) else None
    return JSONResponse({"queued_count": len(messages) if isinstance(messages, list) else 0})


async def _deliver_slack_event(payload: dict[str, Any], retry_num: str = "") -> httpx.Response:
    """POST a signed Events-API delivery to the real /webhooks/slack route."""
    raw = json.dumps(payload).encode()
    req_ts = str(int(time.time()))
    base = f"v0:{req_ts}:{raw.decode()}".encode()
    sig = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Signature": sig,
        "X-Slack-Request-Timestamp": req_ts,
        "Content-Type": "application/json",
    }
    if retry_num:
        headers["X-Slack-Retry-Num"] = retry_num
        headers["X-Slack-Retry-Reason"] = "http_timeout"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://harness") as client:
        return await client.post("/webhooks/slack", content=raw, headers=headers)


async def _deliver_slack_interaction(payload: dict[str, Any]) -> httpx.Response:
    raw = urlencode({"payload": json.dumps(payload)}).encode()
    req_ts = str(int(time.time()))
    base = f"v0:{req_ts}:{raw.decode()}".encode()
    sig = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Signature": sig,
        "X-Slack-Request-Timestamp": req_ts,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://harness") as client:
        return await client.post("/webhooks/slack/interactivity", content=raw, headers=headers)


async def _slack_send_result(payload: dict[str, Any], resp: httpx.Response) -> JSONResponse:
    event = payload["event"]
    channel = str(event["channel"])
    thread_ts = "0" if channel in fakes.CODE_CHANNELS else str(event["thread_ts"])
    thread_id = await lookup_slack_thread_id(
        get_client(url=os.environ["LANGGRAPH_URL"]), channel, thread_ts
    )
    return JSONResponse(
        {
            "thread_ts": thread_ts,
            "thread_id": thread_id,
            "event_id": payload.get("event_id"),
            "webhook_status": resp.status_code,
            "webhook": resp.json(),
        }
    )


@app.post("/control/forget-slack-events")
async def control_forget_slack_events() -> JSONResponse:
    """Drop the in-process record of handled Slack events.

    A redelivery normally lands on a different instance than the original, which
    only has the LangGraph store to dedupe on. Clearing the local cache lets the
    E2E exercise that path instead of the same-process fast path."""
    from agent.slack.events import reset_slack_event_claims

    reset_slack_event_claims()
    return JSONResponse({"ok": True})


@app.post("/mock/slack/send")
async def slack_send(request: Request) -> JSONResponse:
    """Simulate a user posting in Slack: store the message, then deliver the
    signed Events-API webhook to the real /webhooks/slack route.

    ``redeliver`` replays the previous delivery verbatim — same ``event_id``, no
    new Slack message — which is what Slack does when it doesn't get a 2xx in
    three seconds."""
    form = await request.json()
    if form.get("redeliver"):
        payload = LAST_SLACK_EVENT.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="No Slack event to redeliver")
        resp = await _deliver_slack_event(payload, str(form.get("retry_num") or "1"))
        return await _slack_send_result(payload, resp)

    text = str(form.get("text", ""))
    mention_bot = bool(form.get("mention_bot", True))
    channel_type = str(form.get("channel_type") or "")
    # Sender defaults to the first test user (Alice) — the canonical owner the
    # automated tests log in as; the mock UI passes the chosen test user.
    user_id = str(form.get("user") or TEST_USERS[0]["slack_id"])
    channel = str(form.get("channel") or ("D_DEMO" if channel_type == "im" else DEMO_CHANNEL))

    # ``thread_ts`` replies into an existing thread (a distinct message ts under
    # the same thread); omitting it opens a fresh thread, as the mock UI does.
    reply_thread_ts = str(form.get("thread_ts") or "")
    if reply_thread_ts:
        thread_ts = reply_thread_ts
        event_ts = fakes.add_slack_message(
            channel, thread_ts, user=user_id, text=text, is_bot=False
        )
    else:
        thread_ts = fakes.new_thread_ts()
        fakes.add_slack_message(channel, thread_ts, user=user_id, text=text, is_bot=False)
        event_ts = thread_ts
    CURRENT_THREAD["channel"] = channel
    CURRENT_THREAD["thread_ts"] = thread_ts

    event = {
        "type": "app_mention" if mention_bot else "message",
        "channel": channel,
        "user": user_id,
        "text": text,
        "ts": event_ts,
        "thread_ts": thread_ts,
    }
    if channel_type:
        event["channel_type"] = channel_type
    payload = {
        "type": "event_callback",
        "event_id": f"Ev{EVENT_ID_SALT}{event_ts}",
        "authorizations": [{"user_id": BOT_USER_ID}],
        "event": event,
    }
    LAST_SLACK_EVENT["payload"] = payload
    return await _slack_send_result(payload, await _deliver_slack_event(payload))


@app.post("/mock/slack/action")
async def slack_action(request: Request) -> JSONResponse:
    body = await request.json()
    action = body.get("action")
    channel_id = str(CURRENT_THREAD.get("channel") or "")
    thread_ts = str(body.get("thread_ts") or CURRENT_THREAD.get("thread_ts") or "")
    message_ts = str(body.get("message_ts") or "")
    user_id = str(body.get("user") or TEST_USERS[0]["slack_id"])
    if not isinstance(action, dict) or not channel_id or not thread_ts or not message_ts:
        raise HTTPException(status_code=400, detail="Missing Slack action context")
    source_message = fakes.slack_message(channel_id, thread_ts, message_ts)
    if source_message is None:
        raise HTTPException(status_code=404, detail="Slack message not found")

    payload = {
        "type": "block_actions",
        "user": {"id": user_id},
        "channel": {"id": channel_id},
        "container": {
            "channel_id": channel_id,
            "message_ts": message_ts,
            "thread_ts": thread_ts,
        },
        "message": {
            "ts": message_ts,
            "thread_ts": thread_ts,
            "text": source_message["text"],
            "blocks": source_message["blocks"],
        },
        "actions": [{**action, "action_ts": fakes.next_slack_ts()}],
    }
    response = await _deliver_slack_interaction(payload)
    return JSONResponse(response.json(), status_code=response.status_code)


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
async def mock_github_login(redirect_to: str = "") -> Response:
    """E2E stand-in for the dashboard OAuth start route.

    The real route would redirect to github.com. Keep the dashboard-facing URL
    intact, then hand off to the fake GitHub simulator so Playwright exercises a
    browser login flow instead of test code pre-minting a session cookie.
    """
    ui = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    dest = redirect_to or (f"{ui}/agents" if ui else "/agents")
    return RedirectResponse(f"/fake-gh/login/oauth/authorize?redirect_to={quote(dest)}", 302)


@app.get("/fake-gh/login/oauth/authorize")
async def fake_github_authorize(redirect_to: str = "", login: str = "") -> Response:
    """Fake GitHub OAuth consent/login page for dashboard e2e tests."""
    ui = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    dest = redirect_to or (f"{ui}/agents" if ui else "/agents")
    if not login:
        options = "".join(
            f'<option value="{escape(u["login"], quote=True)}">'
            f"{escape(u['name'])} (@{escape(u['login'])})</option>"
            for u in TEST_USERS
        )
        return HTMLResponse(
            f"""<!doctype html><meta charset=utf-8><title>GitHub · Authorize Open SWE</title>
            <body style="font-family:system-ui;max-width:420px;margin:3rem auto;padding:0 1rem">
            <main data-testid="fake-github-login">
              <h1 style="font-size:1.1rem">Authorize Open SWE</h1>
              <p style="color:#888;font-size:0.9rem">Pick a fake GitHub account to continue.</p>
              <form method=get action=/fake-gh/login/oauth/authorize>
                <input type=hidden name=redirect_to value="{escape(dest, quote=True)}">
                <label>GitHub user
                  <select name=login style="font:inherit;padding:0.4rem">{options}</select>
                </label>
                <button style="font:inherit;padding:0.45rem 0.9rem;cursor:pointer">Authorize Open SWE</button>
              </form>
            </main>
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


# The app is served by its own Nitro server, which the specs address directly and
# which fronts these routes in turn — the shape a deployment has. This serves only
# the one asset the fake Slack payloads point at.
UI_PUBLIC = REPO_ROOT / "ui" / ".output" / "public"


def _ui_file(name: str) -> FileResponse:
    path = UI_PUBLIC / name
    if not path.is_file():
        raise HTTPException(404, f"{name} not built — run `pnpm run build` at the repo root")
    return FileResponse(path)


@app.get("/logo-mark.png")
async def ui_logo_mark() -> FileResponse:
    return _ui_file("logo-mark.png")


@app.get("/mock/users")
async def mock_users() -> JSONResponse:
    """The named test users that drive the Slack sender + login dropdowns."""
    return JSONResponse(TEST_USERS)


@app.get("/mock/slack/messages")
async def slack_messages(channel: str = "", thread_ts: str = "") -> JSONResponse:
    selected_channel = channel or CURRENT_THREAD["channel"]
    assert selected_channel is not None
    msgs = (
        fakes.slack_thread(selected_channel, thread_ts)
        if thread_ts
        else fakes.slack_messages(selected_channel)
    )
    return JSONResponse(
        [
            {
                "channel": selected_channel,
                "user": m["user"],
                "text": m["text"],
                "is_bot": m["is_bot"],
                "ts": m["ts"],
                "thread_ts": m["thread_ts"],
                "blocks": m["blocks"],
            }
            for m in msgs
        ]
    )


@app.get("/mock/slack/state")
async def slack_state(channel: str = "") -> JSONResponse:
    current_channel = CURRENT_THREAD["channel"] or DEMO_CHANNEL
    selected_channel = (
        current_channel if current_channel != DEMO_CHANNEL else channel or DEMO_CHANNEL
    )
    channels = [{"id": DEMO_CHANNEL, "name": "demo", "code_channel": False}]
    channels.extend(
        {
            "id": message_channel,
            "name": "direct-message"
            if message_channel.startswith("D")
            else message_channel.lower(),
            "code_channel": False,
        }
        for message_channel in fakes.slack_channels()
        if message_channel != DEMO_CHANNEL and message_channel not in fakes.CODE_CHANNELS
    )
    channels.extend(
        {**value, "code_channel": True}
        for value in fakes.CODE_CHANNELS.values()
        if not value.get("archived")
    )
    return JSONResponse({"selected_channel": selected_channel, "channels": channels})


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


def _review_section(pr: dict[str, Any]) -> str:
    """Render what a reviewer published on this PR — empty when none did."""
    owner, repo, number = pr["owner"], pr["repo"], pr["number"]
    reviews = fakes.reviews_for(owner, repo, number)
    comments = fakes.review_comments_for(owner, repo, number)
    checks = [
        run
        for run in fakes.CHECK_RUNS
        if run["owner"] == owner and run["repo"] == repo and run["head_sha"] == pr["head_sha"]
    ]
    comment_items = "".join(
        f'<li class="review-comment" data-file="{escape(str(comment["path"]))}" '
        f'data-line="{escape(str(comment["line"]))}" '
        f'data-resolved="{str(comment["resolved"]).lower()}">'
        f"<code>{escape(str(comment['path']))}:{escape(str(comment['line']))}</code> "
        f'<span class="body">{escape(str(comment["body"]))}</span></li>'
        for comment in comments
    )
    review_items = "".join(
        f'<li class="review" data-review="{review["id"]}">'
        f'<span class="state">{escape(str(review["state"]))}</span> '
        f'<span class="body">{escape(str(review["body"]))}</span></li>'
        for review in reviews
    )
    check_items = "".join(
        f'<li class="check-run" data-name="{escape(str(run["name"]))}" '
        f'data-conclusion="{escape(str(run["conclusion"]))}">{escape(str(run["name"]))}: '
        f"{escape(str(run['conclusion'] or run['status']))}</li>"
        for run in checks
    )
    return f"""<h3>Reviews (<span id="review-count">{len(reviews)}</span>)</h3>
        <ul id="pr-reviews">{review_items}</ul>
        <h3>Review comments (<span id="review-comment-count">{len(comments)}</span>)</h3>
        <ul id="pr-review-comments">{comment_items}</ul>
        <h3>Checks</h3>
        <ul id="pr-checks">{check_items}</ul>"""


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
        {_review_section(pr)}
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
        "mergeable": pr["mergeable"],
        "mergeable_state": pr["mergeable_state"],
        "title": pr["title"],
        "body": pr["body"],
        "user": {
            "login": pr["author"],
            "avatar_url": f"{BASE_URL}/logo-mark.png",
        },
        "head": {"ref": pr["head"], "sha": pr["head_sha"]},
        "base": {
            "ref": pr["base"],
            "sha": pr.get("base_sha", ""),
            "repo": {"id": 1, "private": fakes.repo_private()},
        },
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": len(pr["files"]),
        "created_at": pr["created_at"],
    }


reviewer_apis.register(app, webhook_secret=GITHUB_WEBHOOK_SECRET, pr_json=_gh_pr_json)


@app.get("/fake-gh/repos/{owner}/{repo}")
async def gh_get_repo(owner: str, repo: str) -> JSONResponse:
    return JSONResponse({"full_name": f"{owner}/{repo}", "private": fakes.repo_private()})


@app.get("/fake-gh/repos/{owner}/{repo}/branches/{branch:path}")
async def gh_get_branch(owner: str, repo: str, branch: str) -> JSONResponse:  # noqa: ARG001
    if not fakes.branch_exists(owner, repo, branch):
        return JSONResponse({"message": "Branch not found"}, status_code=404)
    return JSONResponse({"name": branch, "commit": {"sha": "deadbeef"}})


@app.get("/fake-gh/repos/{owner}/{repo}/pulls")
async def gh_list_pulls(owner: str, repo: str, state: str = "open", head: str = "") -> JSONResponse:
    branch = head.split(":", 1)[-1] if head else ""
    return JSONResponse(
        [
            _gh_pr_json(pull)
            for pull in fakes.PULLS
            if pull["owner"] == owner
            and pull["repo"] == repo
            and (not state or state == "all" or pull["state"] == state)
            and (not branch or pull["head"] == branch)
        ]
    )


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
async def gh_get_pull(owner: str, repo: str, number: int, request: Request) -> Response:
    pr = fakes.find_pull(number, owner, repo)
    if pr is None:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    # The reviewer asks for the same URL with a diff media type; that response is
    # the diff GitHub validates inline-comment anchors against.
    if "diff" in request.headers.get("accept", ""):
        return PlainTextResponse(fakes.pull_diff(owner, repo, pr["base"], pr["head"]))
    return JSONResponse(_gh_pr_json(pr))


@app.get("/fake-gh/repos/{owner}/{repo}/commits/{sha}/check-runs")
async def gh_get_check_runs(owner: str, repo: str, sha: str) -> JSONResponse:
    pr = fakes.find_pull_by_sha(owner, repo, sha)
    if pr is None:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    return JSONResponse({"total_count": len(pr["check_runs"]), "check_runs": pr["check_runs"]})


@app.get("/fake-gh/repos/{owner}/{repo}/commits/{sha}/status")
async def gh_get_commit_status(owner: str, repo: str, sha: str) -> JSONResponse:
    pr = fakes.find_pull_by_sha(owner, repo, sha)
    if pr is None:
        return JSONResponse({"message": "Not Found"}, status_code=404)
    return JSONResponse({"state": "pending", "sha": sha, "statuses": pr["statuses"]})


@app.post("/fake-gh/graphql")
async def gh_graphql(request: Request) -> JSONResponse:
    body = await request.json()
    query = body.get("query", "")
    variables = body.get("variables", {})
    if "resolveReviewThread" in query:
        thread_id = variables.get("threadId")
        if not isinstance(thread_id, str) or not fakes.resolve_review_thread(thread_id):
            return JSONResponse({"errors": [{"message": "Thread not found"}]})
        return JSONResponse(
            {"data": {"resolveReviewThread": {"thread": {"id": thread_id, "isResolved": True}}}}
        )
    owner = variables.get("owner")
    repo = variables.get("repo")
    number = variables.get("number") or variables.get("pr")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return JSONResponse({"errors": [{"message": "Invalid variables"}]}, status_code=400)
    pr = fakes.find_pull(number, owner, repo)
    if pr is None:
        return JSONResponse({"errors": [{"message": "Pull request not found"}]})
    # Threads come from two places: fixtures a spec injected via
    # /control/pull-request-health, and the inline comments the reviewer itself
    # published (which is what a re-review has to reconcile against).
    review_threads = {
        "nodes": [
            *[fakes.review_thread_graphql(thread) for thread in pr["review_threads"]],
            *fakes.published_threads_graphql(owner, repo, number),
        ],
        "pageInfo": {"hasNextPage": False, "endCursor": None},
    }
    pull_request: dict[str, Any] = {"reviewThreads": review_threads}
    if "PullRequestFixReviews" in query:
        pull_request.update(
            {
                "reviewDecision": pr["review_decision"],
                "mergeStateStatus": "DIRTY" if not pr["mergeable"] else "CLEAN",
                "latestOpinionatedReviews": {
                    "nodes": [
                        {
                            "author": {"login": review.get("author")},
                            "state": review.get("state"),
                            "body": review.get("body", ""),
                            "url": review.get("url"),
                        }
                        for review in pr["reviews"]
                    ]
                },
            }
        )
    if "PullRequestFixChecks" in query:
        pull_request = {
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "oid": pr["head_sha"],
                            "statusCheckRollup": {
                                "contexts": {
                                    "nodes": [
                                        *[fakes.check_graphql(check) for check in pr["check_runs"]],
                                        *[
                                            fakes.status_graphql(status)
                                            for status in pr["statuses"]
                                        ],
                                    ],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                }
                            },
                        }
                    }
                ]
            }
        }
    return JSONResponse({"data": {"repository": {"pullRequest": pull_request}}})


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


@app.post("/fake-slack/chat.update")
async def slack_update_message(request: Request) -> JSONResponse:
    body = await request.json()
    message = fakes.update_slack_message(
        str(body.get("channel") or ""),
        str(body.get("ts") or ""),
        text=str(body.get("text") or ""),
        blocks=body.get("blocks"),
    )
    if message is None:
        return JSONResponse({"ok": False, "error": "message_not_found"})
    return _ok({"ts": message["ts"], "message": message})


@app.post("/fake-slack/chat.postEphemeral")
async def slack_post_ephemeral(request: Request) -> JSONResponse:
    await request.body()
    return _ok({"message_ts": fakes.next_slack_ts()})


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
    code_channel = fakes.CODE_CHANNELS.get(channel)
    data: dict[str, Any] = {
        "id": channel,
        "name": code_channel["name"] if code_channel else "demo",
        "name_normalized": code_channel["name"] if code_channel else "demo",
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
        "topic": {"value": "Demo channel topic"},
        "purpose": {"value": "Demo channel purpose"},
    }
    if code_channel:
        data["properties"] = {"record_channel": {"record_type": "agent_channel"}}
    return _ok({"channel": data})


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
async def slack_conversations_history(channel: str = "") -> JSONResponse:
    return _ok(
        {
            "messages": [
                {
                    "type": "message",
                    "user": message["user"],
                    "text": message["text"],
                    "ts": message["ts"],
                }
                for message in reversed(fakes.slack_messages(channel))
            ]
        }
    )


@app.post("/fake-slack/agents.conversations.create")
async def slack_create_code_channel(request: Request) -> JSONResponse:
    channel = fakes.create_code_channel(await request.json())
    return _ok({"channel": {"id": channel["id"]}})


@app.post("/fake-slack/agents.sessions.setStatus")
async def slack_set_code_channel_status(request: Request) -> JSONResponse:
    body = await request.json()
    channel = fakes.update_code_channel(
        str(body.get("channel_id") or ""), status=body.get("status")
    )
    return _ok() if channel else JSONResponse({"ok": False, "error": "channel_not_found"})


@app.post("/fake-slack/agents.sessions.rename")
async def slack_rename_code_channel(request: Request) -> JSONResponse:
    body = await request.json()
    channel = fakes.update_code_channel(str(body.get("channel_id") or ""), name=body.get("title"))
    return _ok() if channel else JSONResponse({"ok": False, "error": "channel_not_found"})


@app.post("/fake-slack/agents.conversations.setProperties")
async def slack_set_code_channel_properties(request: Request) -> JSONResponse:
    body = await request.json()
    channel_id = str(body.get("channel_id") or "")
    code_channel = body.get("code_channel") if isinstance(body.get("code_channel"), dict) else {}
    values: dict[str, Any] = {}
    if "context_bar_items" in code_channel:
        values["context_bar_items"] = code_channel["context_bar_items"]
    if "summary_message" in code_channel:
        values["summary_message"] = code_channel["summary_message"]
    if "agent_resource" in body:
        values["agent_resource"] = body["agent_resource"]
    channel = fakes.update_code_channel(channel_id, **values)
    return _ok() if channel else JSONResponse({"ok": False, "error": "channel_not_found"})


@app.post("/fake-slack/agents.conversations.setCommands")
async def slack_set_code_channel_commands(request: Request) -> JSONResponse:
    body = await request.json()
    channel = fakes.update_code_channel(
        str(body.get("channel_id") or ""), commands=body.get("commands", [])
    )
    return _ok() if channel else JSONResponse({"ok": False, "error": "channel_not_found"})


@app.post("/fake-slack/agents.conversations.setView")
async def slack_set_code_channel_view(request: Request) -> JSONResponse:
    body = await request.json()
    channel = fakes.CODE_CHANNELS.get(str(body.get("channel_id") or ""))
    if channel is None:
        return JSONResponse({"ok": False, "error": "channel_not_found"})
    view = {**body, "view_id": f"V{len(channel['views']) + 1}"}
    channel["views"].append(view)
    return _ok(view)


@app.post("/fake-slack/agents.conversations.archive")
async def slack_archive_code_channel(request: Request) -> JSONResponse:
    body = await request.json()
    channel = fakes.update_code_channel(str(body.get("channel_id") or ""), archived=True)
    return _ok() if channel else JSONResponse({"ok": False, "error": "channel_not_found"})


@app.get("/fake-slack/chat.getPermalink")
async def slack_get_permalink(channel: str = "", message_ts: str = "") -> JSONResponse:  # noqa: ARG001
    return _ok({"permalink": f"{BASE_URL}/mock/slack"})


# Quietly reference imports used only for env side effects.
_ = (e2e_env, HUMAN_USER)
