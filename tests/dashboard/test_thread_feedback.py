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


async def test_dismiss_prevents_later_submission(api):
    dismissed = await api.client.post(
        "/dashboard/api/threads/t1/feedback", json={"action": "dismiss"}
    )
    assert dismissed.status_code == 200
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "bad"})
    assert (
        submitted.json()
        == dismissed.json()
        == {"status": "dismissed", "rating": None, "comment": ""}
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
            monkeypatch.setattr(feedback, "login_for_slack_id", AsyncMock(return_value="owner"))
    visible = await api.client.get("/dashboard/api/threads/t1/feedback")
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json=payload)
    assert visible.json() == {
        "status": "ready" if identity == "slack_initiator" else "unavailable",
        "rating": None,
        "comment": "",
    }
    assert submitted.status_code == (200 if identity == "slack_initiator" else 403)


async def test_private_thread_owner_can_give_feedback(api):
    api.metadata.update({"visibility": "private", "owner_login": "owner"})
    visible = await api.client.get("/dashboard/api/threads/t1/feedback")
    assert visible.json()["status"] == "ready"
    submitted = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    assert submitted.status_code == 200

    api.session["sub"] = "other"
    assert (await api.client.get("/dashboard/api/threads/t1/feedback")).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"rating": "other", "comment": "  "},
        {"rating": "other", "comment": "Details"},
        {"action": "comment", "comment": "  "},
        {"rating": "great"},
        {},
        {"action": "submit"},
        {"action": "unknown", "rating": "good"},
        {"rating": "good", "comment": "x" * 3001},
    ],
)
async def test_invalid_feedback_is_not_saved(api, payload):
    response = await api.client.post("/dashboard/api/threads/t1/feedback", json=payload)
    assert response.status_code == 422
    assert (await api.client.get("/dashboard/api/threads/t1/feedback")).json()["status"] == "ready"


async def test_bad_rating_is_saved_before_optional_comment(api):
    rated = await api.client.post("/dashboard/api/threads/t1/feedback", json={"rating": "bad"})
    assert rated.json() == {"status": "completed", "rating": "bad", "comment": ""}
    commented = await api.client.post(
        "/dashboard/api/threads/t1/feedback",
        json={"action": "comment", "comment": "  The fix did not work.  "},
    )
    assert commented.status_code == 200
    assert commented.json() == {
        "status": "completed",
        "rating": "bad",
        "comment": "The fix did not work.",
    }
    duplicate = await api.client.post(
        "/dashboard/api/threads/t1/feedback",
        json={"action": "comment", "comment": "Overwrite"},
    )
    assert duplicate.json() == commented.json()


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


@pytest.mark.parametrize("status", ["completed", "dismissed"])
async def test_slack_completion_hides_web_controls(api, status):
    await thread_feedback.complete_feedback_prompt("t1", status)
    response = await api.client.get("/dashboard/api/threads/t1/feedback")
    assert response.json() == {"status": status, "rating": None, "comment": ""}


def test_openapi_exposes_one_typed_feedback_response():
    app = FastAPI()
    app.include_router(feedback.feedback_router)
    schema = app.openapi()
    responses = [
        schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        for path, method in [
            ("/threads/{thread_id}/feedback", "get"),
            ("/threads/{thread_id}/feedback", "post"),
        ]
    ]
    assert responses[0] == responses[1]
    model = schema["components"]["schemas"][responses[0]["$ref"].rsplit("/", 1)[1]]
    assert set(model["properties"]) == {"status", "rating", "comment"}
    assert set(model["properties"]["status"]["enum"]) == {
        "unavailable",
        "ready",
        "completed",
        "dismissed",
    }
