import pytest

from agent.dashboard.admin import is_admin


def test_is_admin_accepts_email_or_github_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "Alice, bob@langchain.dev")

    assert is_admin("bob@langchain.dev", login="not-bob") is True
    assert is_admin("other@langchain.dev", login="alice") is True
    assert is_admin("other@langchain.dev", login="ALICE") is True
    assert is_admin("other@langchain.dev", login="mallory") is False


def test_is_admin_rejects_blank_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")

    assert is_admin(None, login=None) is False
    assert is_admin("", login=" ") is False


async def test_usage_leaderboard_privacy_get_is_session_scoped_and_put_is_admin_only(
    monkeypatch: pytest.MonkeyPatch, fake_store
) -> None:
    import httpx
    from fastapi import FastAPI

    from agent.dashboard import oauth, routes

    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/dashboard/api/settings/usage-leaderboard-privacy")
        assert anonymous.status_code == 401

        app.dependency_overrides[oauth.require_session] = lambda: {"sub": "mallory"}
        member = await client.get("/dashboard/api/settings/usage-leaderboard-privacy")
        assert member.status_code == 200
        assert member.json() == {"usage_leaderboard_privacy_enabled": True}
        forbidden = await client.put(
            "/dashboard/api/settings/usage-leaderboard-privacy",
            json={"usage_leaderboard_privacy_enabled": False},
        )
        assert forbidden.status_code == 403

        app.dependency_overrides[oauth.require_session] = lambda: {"sub": "alice"}
        saved = await client.put(
            "/dashboard/api/settings/usage-leaderboard-privacy",
            json={"usage_leaderboard_privacy_enabled": False},
        )
        assert saved.status_code == 200
        assert saved.json()["usage_leaderboard_privacy_enabled"] is False
        assert (await client.get("/dashboard/api/settings/usage-leaderboard-privacy")).json() == {
            "usage_leaderboard_privacy_enabled": False
        }
