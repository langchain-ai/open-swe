from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent import thread_feedback
from agent.dashboard import feedback, routes
from agent.dashboard.oauth import require_session


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
    await thread_feedback.feedback_store().put("t1", thread_feedback.Feedback(status="ready"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"origin": "http://testserver"},
    ) as client:
        yield SimpleNamespace(client=client, metadata=metadata, session=session, quiet=quiet)


@pytest.mark.parametrize("rating", ["bad", "good", "other"])
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


async def test_dismiss_prevents_later_submission(api):
    dismissed = await api.client.post("/dashboard/api/threads/t1/feedback/dismiss")
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "bad"})
    assert (
        submitted.json()
        == dismissed.json()
        == {"status": "dismissed", "rating": None, "comment": ""}
    )


@pytest.mark.parametrize("identity", ["other_member", "unknown_initiator", "slack_initiator"])
async def test_only_verified_initiator_gets_feedback(api, monkeypatch, identity):
    if identity == "other_member":
        api.session["sub"] = "other"
    else:
        api.metadata.pop("feedback_initiator_login")
        api.metadata["participant_logins"] = {"owner": True}
        if identity == "slack_initiator":
            api.metadata["source_context"] = {"slack_thread": {"triggering_user_id": "U1"}}
            monkeypatch.setattr(feedback, "login_for_slack_id", AsyncMock(return_value="owner"))
    visible = await api.client.get("/dashboard/api/threads/t1/feedback")
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    assert visible.json()["status"] == ("ready" if identity == "slack_initiator" else "unavailable")
    assert submitted.status_code == (200 if identity == "slack_initiator" else 403)


@pytest.mark.parametrize(
    "payload",
    [
        {"rating": "other", "comment": "  "},
        {"rating": "great"},
        {"rating": "good", "comment": "x" * 3001},
    ],
)
async def test_invalid_feedback_is_not_saved(api, payload):
    response = await api.client.post("/dashboard/api/threads/t1/feedback", json=payload)
    assert response.status_code == 422
    assert (await api.client.get("/dashboard/api/threads/t1/feedback")).json()["status"] == "ready"


@pytest.mark.parametrize("blocked", ["activity", "cross_origin"])
async def test_submit_rechecks_eligibility_and_origin(api, blocked):
    headers = {}
    if blocked == "activity":
        api.quiet.return_value = None
    else:
        headers["origin"] = "https://untrusted.example"
    response = await api.client.post(
        "/dashboard/api/threads/t1/feedback", json={"rating": "good"}, headers=headers
    )
    assert response.status_code == (409 if blocked == "activity" else 403)
    assert (await thread_feedback.feedback_store().get("t1")).status == "ready"


@pytest.mark.parametrize("status", ["completed", "dismissed"])
async def test_slack_completion_hides_web_controls(api, status):
    await thread_feedback.complete_feedback_prompt("t1", status)
    response = await api.client.get("/dashboard/api/threads/t1/feedback")
    assert response.json() == {"status": status, "rating": None, "comment": ""}
