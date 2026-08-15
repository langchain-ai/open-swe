"""Real dashboard login against a fake GitHub, for testing desktop sign-in locally.

Runs the actual `/auth/login`, `/auth/callback`, handoff and exchange code —
only GitHub itself is faked, so the loopback flow is exercised end to end.

    uv run python tests/e2e/local_desktop_login.py

Then point the desktop app at http://127.0.0.1:8765 (Open SWE -> Backend URL...)
and sign in. Endpoints other than /auth/* and /me need a real backend and will
error; login is what this harness covers.
"""

from __future__ import annotations

import os
from html import escape
from typing import Any
from urllib.parse import quote

PORT = int(os.environ.get("PORT", "8765"))
BASE_URL = f"http://127.0.0.1:{PORT}"
os.environ.setdefault("DASHBOARD_JWT_SECRET", "local-desktop-login-secret")
os.environ.setdefault("GITHUB_APP_CLIENT_ID", "local-client-id")
os.environ.setdefault("GITHUB_APP_CLIENT_SECRET", "local-client-secret")
os.environ.setdefault("DASHBOARD_BASE_URL", BASE_URL)
os.environ.setdefault("DASHBOARD_API_BASE_URL", BASE_URL)
os.environ.setdefault("DASHBOARD_ALLOWED_ORIGINS", BASE_URL)
os.environ.setdefault("ALLOWED_GITHUB_ORGS", "")

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402

from agent.dashboard import routes  # noqa: E402

LOGIN = os.environ.get("FAKE_GITHUB_LOGIN", "local-tester")
EMAIL = os.environ.get("FAKE_GITHUB_EMAIL", "local-tester@example.com")

routes.GITHUB_AUTHORIZE_URL = f"{BASE_URL}/fake-gh/login/oauth/authorize"


async def fake_exchange_code(code: str) -> dict[str, Any]:
    return {"access_token": "gho_local", "token_type": "bearer"}


async def fake_fetch_github_user(access_token: str) -> tuple[dict[str, Any], str | None]:
    return {"login": LOGIN, "avatar_url": None}, EMAIL


async def fake_enforce_org_login_gate(login: str) -> None:
    return None


async def fake_upsert(login: str, email: str, data: dict[str, Any]) -> None:
    return None


routes.exchange_code = fake_exchange_code
routes.fetch_github_user = fake_fetch_github_user
routes.enforce_org_login_gate = fake_enforce_org_login_gate
routes.upsert_access_token_from_github_response = fake_upsert

app = FastAPI()
app.include_router(routes.router)


@app.get("/fake-gh/login/oauth/authorize")
async def fake_github_authorize(redirect_uri: str, state: str, client_id: str = "") -> HTMLResponse:
    """Stand-in for GitHub's consent screen."""
    # Both values arrive in the query string, so pin the target to this
    # harness's own callback and escape what reaches the page.
    callback = f"{BASE_URL}/dashboard/api/auth/callback"
    if redirect_uri != callback:
        raise HTTPException(400, "unexpected redirect_uri")
    target = escape(f"{callback}?code=fake-oauth-code&state={quote(state, safe='')}", quote=True)
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8><title>Fake GitHub</title>
        <body style="font:16px system-ui;max-width:30rem;margin:4rem auto;text-align:center">
        <h1 style="font-size:1.1rem">Authorize Open SWE</h1>
        <p style="opacity:.7">Signing in as <b>{escape(LOGIN)}</b> — a local fake, not GitHub.</p>
        <p><a href="{target}"
              style="display:inline-block;padding:.6rem 1rem;border:1px solid;border-radius:.4rem">
           Authorize</a></p>
        </body>"""
    )


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/dashboard/api/me")


if __name__ == "__main__":
    print(f"\n  Fake-GitHub login harness on {BASE_URL}")
    print(f"  Point the desktop app at {BASE_URL} and sign in.\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
