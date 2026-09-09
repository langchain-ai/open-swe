import asyncio
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
def feedback_api(monkeypatch, fake_store):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    metadata = {"source": "dashboard", "feedback_initiator_login": "owner"}
    session = {"sub": "owner"}
    thread_metadata = AsyncMock(side_effect=lambda *args, **kwargs: metadata.copy())
    monkeypatch.setattr(feedback, "fetch_thread_metadata", thread_metadata)
    eligibility = AsyncMock(return_value=("ready", 1000))
    monkeypatch.setattr(feedback, "feedback_prompt_status", eligibility)
    lock = asyncio.Lock()

    @asynccontextmanager
    async def thread_lock(*args):
        async with lock:
            yield

    monkeypatch.setattr(feedback, "agent_thread_pr_state_lock", thread_lock)
    monkeypatch.setattr(feedback, "langgraph_client", lambda: None)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: session
    return SimpleNamespace(
        app=app,
        metadata=metadata,
        session=session,
        eligibility=eligibility,
    )


def client(api):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="http://testserver",
        headers={"origin": "http://testserver"},
    )


async def test_submit_is_durable_and_duplicate_cannot_overwrite(feedback_api):
    async with client(feedback_api) as http:
        saved = await http.post(
            "/dashboard/api/threads/t1/feedback",
            json={"rating": "good", "comment": "  Fixed it.  "},
        )
        duplicate = await http.post(
            "/dashboard/api/threads/t1/feedback", json={"rating": "bad", "comment": "Changed"}
        )
        current = await http.get("/dashboard/api/threads/t1/feedback")
    assert saved.status_code == 200
    assert saved.json() == duplicate.json() == current.json()
    assert current.json()["status"] == "completed"
    assert current.json()["rating"] == "good"
    assert current.json()["comment"] == "Fixed it."
    assert await thread_feedback.feedback_prompt_status("t1") == ("completed", None)


async def test_dismiss_survives_reload_and_prevents_submission(feedback_api):
    async with client(feedback_api) as http:
        dismissed = await http.post("/dashboard/api/threads/t1/feedback/dismiss")
        submitted = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "bad"})
        current = await http.get("/dashboard/api/threads/t1/feedback")
    assert dismissed.status_code == 200
    assert current.json()["status"] == submitted.json()["status"] == "dismissed"
    assert await thread_feedback.feedback_prompt_status("t1") == ("dismissed", None)


async def test_only_initiator_can_see_or_submit_feedback(feedback_api):
    feedback_api.session["sub"] = "other-member"
    async with client(feedback_api) as http:
        response = await http.get("/dashboard/api/threads/t1/feedback")
        submitted = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
        dismissed = await http.post("/dashboard/api/threads/t1/feedback/dismiss")
    assert response.json()["status"] == "unavailable"
    assert submitted.status_code == dismissed.status_code == 403


async def test_legacy_thread_with_only_participants_fails_closed(feedback_api):
    feedback_api.metadata.pop("feedback_initiator_login")
    feedback_api.metadata["participant_logins"] = {"owner": True}
    async with client(feedback_api) as http:
        response = await http.get("/dashboard/api/threads/t1/feedback")
    assert response.json()["status"] == "unavailable"


async def test_slack_initiator_uses_verified_mapping(feedback_api, monkeypatch):
    feedback_api.metadata.clear()
    feedback_api.metadata.update(
        {
            "source": "slack",
            "github_login": "other-member",
            "source_context": {"slack_thread": {"triggering_user_id": "U1"}},
        }
    )
    monkeypatch.setattr(feedback, "login_for_slack_id", AsyncMock(return_value="owner"))
    async with client(feedback_api) as http:
        response = await http.get("/dashboard/api/threads/t1/feedback")
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize(
    "rating,comment", [("other", ""), ("other", "  "), ("great", "Great"), ("good", "x" * 3001)]
)
async def test_invalid_submission_is_not_saved(feedback_api, rating, comment):
    async with client(feedback_api) as http:
        response = await http.post(
            "/dashboard/api/threads/t1/feedback", json={"rating": rating, "comment": comment}
        )
        current = await http.get("/dashboard/api/threads/t1/feedback")
    assert response.status_code == 422
    assert current.json()["status"] == "ready"


async def test_other_saves_comment_without_positive_or_negative_rating(feedback_api):
    async with client(feedback_api) as http:
        response = await http.post(
            "/dashboard/api/threads/t1/feedback",
            json={"rating": "other", "comment": "More detail please."},
        )
    assert response.json()["status"] == "completed"
    assert response.json()["rating"] == "other"
    assert response.json()["comment"] == "More detail please."


@pytest.mark.parametrize("status", ["waiting", "unavailable"])
async def test_submission_rechecks_eligibility(feedback_api, status):
    feedback_api.eligibility.return_value = (status, 2000 if status == "waiting" else None)
    async with client(feedback_api) as http:
        response = await http.get("/dashboard/api/threads/t1/feedback")
        submitted = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    assert response.json()["status"] == status
    assert submitted.status_code == 409


async def test_completed_feedback_survives_new_activity(feedback_api):
    async with client(feedback_api) as http:
        response = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "bad"})
        feedback_api.eligibility.return_value = ("unavailable", None)
        current = await http.get("/dashboard/api/threads/t1/feedback")
    assert response.json()["status"] == current.json()["status"] == "completed"


async def test_concurrent_submissions_keep_the_first_response(feedback_api):
    async with client(feedback_api) as http:
        responses = await asyncio.gather(
            *(
                http.post("/dashboard/api/threads/t1/feedback", json={"rating": rating})
                for rating in ("good", "bad")
            )
        )
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["status"] == "completed"


async def test_failed_store_write_does_not_report_completion(feedback_api, fake_store, monkeypatch):
    write = fake_store.put_item
    monkeypatch.setattr(
        fake_store, "put_item", AsyncMock(side_effect=RuntimeError("Store unavailable"))
    )
    async with client(feedback_api) as http:
        with pytest.raises(RuntimeError, match="Store unavailable"):
            await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
        current = await http.get("/dashboard/api/threads/t1/feedback")
        assert current.json()["status"] == "ready"
        monkeypatch.setattr(fake_store, "put_item", write)
        retried = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    assert retried.json()["status"] == "completed"


async def test_cross_origin_submission_is_rejected(feedback_api):
    async with client(feedback_api) as http:
        response = await http.post(
            "/dashboard/api/threads/t1/feedback",
            json={"rating": "good"},
            headers={"origin": "https://untrusted.example"},
        )
        current = await http.get("/dashboard/api/threads/t1/feedback")
    assert response.status_code == 403
    assert current.json()["status"] == "ready"


@pytest.mark.parametrize("status", ["completed", "dismissed"])
async def test_feedback_completed_in_slack_hides_web_controls(feedback_api, status):
    await thread_feedback.complete_feedback_prompt("t1", status)
    feedback_api.eligibility.side_effect = thread_feedback.feedback_prompt_status
    async with client(feedback_api) as http:
        current = await http.get("/dashboard/api/threads/t1/feedback")
        submitted = await http.post("/dashboard/api/threads/t1/feedback", json={"rating": "good"})
    assert current.json() == {"status": status, "promptAt": None, "rating": None, "comment": ""}
    assert submitted.json() == current.json()
