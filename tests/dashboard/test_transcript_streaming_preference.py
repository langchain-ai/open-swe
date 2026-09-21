"""The per-user opt-in that points the dashboard at the transcript event log."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent.dashboard import user_preferences
from agent.dashboard.oauth import require_session
from agent.dashboard.routes import router

PREFERENCES_URL = "/dashboard/api/me/preferences"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(router)

    async def session() -> dict[str, str]:
        return {"sub": "alice", "email": "alice@example.com"}

    app.dependency_overrides[require_session] = session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The preferences record, as the user preference store holds it."""
    records: dict[str, dict[str, Any]] = {}

    async def get_value(namespace: list[str], login: str) -> dict[str, Any] | None:
        assert namespace == user_preferences.USER_PREFERENCES_NAMESPACE
        return records.get(login)

    async def put_value(namespace: list[str], login: str, value: dict[str, Any]) -> None:
        records[login] = value

    monkeypatch.setattr(user_preferences, "get_value", get_value)
    monkeypatch.setattr(user_preferences, "put_value", put_value)
    return records


async def test_transcript_streaming_is_off_until_the_user_turns_it_on(
    client: httpx.AsyncClient, store: dict[str, dict[str, Any]]
) -> None:
    # A record written before the preference existed reads as opted out.
    store["alice"] = {"default_visibility": "private"}
    assert (await client.get(PREFERENCES_URL)).json()["transcript_streaming"] is False

    saved = await client.put(
        PREFERENCES_URL, json={"default_visibility": "private", "transcript_streaming": True}
    )

    assert saved.json()["transcript_streaming"] is True
    assert (await client.get(PREFERENCES_URL)).json()["transcript_streaming"] is True

    # A client that predates the field (an older desktop build) saves the other
    # preferences without it; that must not switch the reader back.
    saved = await client.put(PREFERENCES_URL, json={"default_visibility": "public"})

    assert saved.json()["default_visibility"] == "public"
    assert saved.json()["transcript_streaming"] is True


async def test_the_session_payload_carries_the_opt_in(
    client: httpx.AsyncClient, store: dict[str, dict[str, Any]]
) -> None:
    """The thread page picks its source on first render, so `/me` has to say."""
    store["alice"] = {"transcript_streaming": True}

    assert (await client.get("/dashboard/api/me")).json()["transcript_streaming"] is True
