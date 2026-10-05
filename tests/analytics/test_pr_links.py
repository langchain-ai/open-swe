from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI

from agent.analytics import pr_links
from agent.dashboard.oauth import COOKIE_NAME, issue_session


@pytest.mark.parametrize("visitor", ["signed_in", "anonymous", "invalid_cookie"])
async def test_pr_link_visit_preserves_destination_and_optional_identity(monkeypatch, visitor):
    monkeypatch.setenv("PR_LINK_SIGNING_KEY", "durable-link-key")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "session-key-" * 4)
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://app.example")
    monkeypatch.setenv("SEGMENT_WRITE_KEY", "")
    destination = "https://example.com/path?signature=a%2Fb&value=one#section"
    tracked = pr_links.tracked_pr_link(destination, thread_id="thread", kind="plan")
    visits = []

    async def record(link, user_id, login):
        visits.append((link, user_id, login))

    monkeypatch.setattr(pr_links, "_record_visit", record)
    user_id = str(uuid4())
    cookie = issue_session(login="reader", email=None, avatar_url=None, user_id=user_id)
    cookies = {}
    if visitor == "signed_in":
        cookies[COOKIE_NAME] = cookie
    elif visitor == "invalid_cookie":
        cookies[COOKIE_NAME] = "invalid"
    app = FastAPI()
    app.include_router(pr_links.router, prefix="/dashboard/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example", cookies=cookies
    ) as client:
        response = await client.get(tracked)
        assert response.status_code == 302
        assert response.headers["location"] == destination
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "set-cookie" not in response.headers
        assert visits[0][1:] == ((user_id, "reader") if visitor == "signed_in" else (None, None))
        token = parse_qs(urlsplit(tracked).query)["token"][0]
        claims = jwt.decode(token, options={"verify_signature": False})
        claims["url"] = "https://attacker.example"
        tampered = jwt.encode(claims, "wrong-key-" * 4, algorithm="HS256")
        assert (
            await client.get("/dashboard/api/analytics/pr-link", params={"token": tampered})
        ).status_code == 400
        assert (
            await client.get("/dashboard/api/analytics/pr-link", params={"token": cookie})
        ).status_code == 400
        assert (await client.head(tracked)).status_code == 405
        assert len(visits) == 1
        monkeypatch.setenv("DASHBOARD_JWT_SECRET", "rotated-session-key-" * 4)
        assert (await client.get(tracked)).status_code == 302


async def test_visit_delivery_is_anonymous_without_session_and_failure_is_nonfatal(monkeypatch):
    monkeypatch.setenv("SEGMENT_WRITE_KEY", "segment-key")
    payloads = []

    async def post(self, url, *, json):
        payloads.append(json)
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    link = pr_links.PrLink(
        aud="pr-description-link",
        url="https://example.com/?secret=x",
        thread_id="thread",
        kind="plan",
    )
    await pr_links._record_visit(link, None, None)
    await pr_links._record_visit(link, "user-id", "reader")
    assert "userId" not in payloads[0]
    assert payloads[0]["anonymousId"]
    assert payloads[1]["userId"] == "user-id"
    assert "anonymousId" not in payloads[1]
    assert "secret" not in str(payloads)
