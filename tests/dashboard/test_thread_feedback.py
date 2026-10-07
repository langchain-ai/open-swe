from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe import thread_feedback
from openswe.dashboard.oauth import require_session
from openswe.threads import feedback
from openswe.threads.routes import router as threads_router


@pytest.fixture
async def api(monkeypatch, fake_store):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    metadata = {"source": "dashboard", "feedback_initiator_login": "owner"}
    session = {"sub": "owner"}
    monkeypatch.setattr(
        feedback, "fetch_thread_metadata", AsyncMock(side_effect=lambda _: metadata)
    )
    quiet = AsyncMock(return_value=0)
    monkeypatch.setattr(thread_feedback, "_quiet_until", quiet)

    @asynccontextmanager
    async def unlocked(*args):
        yield

    monkeypatch.setattr(feedback, "agent_thread_pr_state_lock", unlocked)
    monkeypatch.setattr(feedback, "langgraph_client", lambda: None)
    analytics = AsyncMock()
    monkeypatch.setattr(feedback, "record_feedback_submission", analytics)
    await thread_feedback.feedback_store().put("t1", thread_feedback.Feedback(status="ready"))
    app = FastAPI()
    app.include_router(threads_router, prefix="/dashboard/api")
    app.dependency_overrides[require_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"origin": "http://testserver"},
    ) as client:
        yield SimpleNamespace(
            client=client, metadata=metadata, session=session, quiet=quiet, analytics=analytics
        )


@pytest.mark.parametrize("rating", ["bad", "good"])
async def test_submit_saves_rating_and_comment_and_keeps_first_response(api, rating):
    response = await api.client.post(
        "/dashboard/api/threads/t1/feedback",
        json={"rating": rating, "comment": "  Helpful detail  "},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "completed", "rating": rating, "comment": "Helpful detail"}
    duplicate = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    reloaded = await api.client.get("/dashboard/api/threads/t1/feedback")
    assert response.json() == duplicate.json() == reloaded.json()
    assert await thread_feedback.feedback_prompt_status("t1") == "completed"
    api.analytics.assert_awaited_once_with(
        feedback_key="thread:t1",
        rating=5 if rating == "good" else 1,
        source="dashboard",
        run_key=None,
        github_login="owner",
        user_email=None,
    )


@pytest.mark.parametrize("payload", [{"rating": "good"}, {"action": "dismiss"}])
@pytest.mark.parametrize("identity", ["other_member", "unknown_initiator", "slack_initiator"])
async def test_only_verified_initiator_gets_feedback(api, monkeypatch, identity, payload):
    if identity == "other_member":
        api.session["sub"] = "other"
    else:
        api.metadata.pop("feedback_initiator_login")
        api.metadata["participant_logins"] = {"owner": True}
        if identity == "slack_initiator":
            api.metadata["source_context"] = {"slack_thread": {"triggering_user_id": "U1"}}
            monkeypatch.setattr(feedback.User, "login_for_slack", AsyncMock(return_value="owner"))
    visible = await api.client.get("/dashboard/api/threads/t1/feedback")
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json=payload)
    assert visible.json() == {
        "status": "ready" if identity == "slack_initiator" else "unavailable",
        "rating": None,
        "comment": "",
    }
    assert submitted.status_code == (200 if identity == "slack_initiator" else 403)


@pytest.mark.parametrize("state", ["ready", "good", "dismissed"])
async def test_comment_requires_completed_bad_rating(api, state):
    if state != "ready":
        await api.client.post(
            "/dashboard/api/threads/t1/feedback",
            json={"rating": "good"} if state == "good" else {"action": "dismiss"},
        )
    before = (await api.client.get("/dashboard/api/threads/t1/feedback")).json()
    response = await api.client.post(
        "/dashboard/api/threads/t1/feedback", json={"action": "comment", "comment": "Details"}
    )
    assert response.status_code == 409
    assert (await api.client.get("/dashboard/api/threads/t1/feedback")).json() == before


@pytest.mark.parametrize("payload", [{"rating": "good"}, {"action": "dismiss"}])
@pytest.mark.parametrize("blocked", ["activity", "cross_origin"])
async def test_submission_rechecks_eligibility_and_origin(api, blocked, payload):
    headers = {}
    if blocked == "activity":
        api.quiet.return_value = None
    else:
        headers["origin"] = "https://untrusted.example"
    response = await api.client.post(
        "/dashboard/api/threads/t1/feedback", json=payload, headers=headers
    )
    assert response.status_code == (409 if blocked == "activity" else 403)
    assert (await thread_feedback.feedback_store().get("t1")).status == "ready"
