from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import httpx2
import pytest
from fastapi import FastAPI, HTTPException

from openswe.analytics import gateway, routes
from openswe.dashboard import oauth
from openswe.dashboard.langsmith_login import LangSmithLogin
from openswe.users import User


@pytest.mark.asyncio
async def test_gateway_spend_requires_session():
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/analytics/gateway-spend")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "langsmith", "langsmith_only"])
async def test_gateway_spend_uses_caller_and_matches_provider_counters(
    monkeypatch, langsmith_users, provider
):
    user = langsmith_users.add("alice")
    if provider == "langsmith_only":
        user.identities = [
            identity for identity in user.identities if identity.provider != "github"
        ]
    old_user = User()
    langsmith_users.people[old_user.id] = old_user
    monkeypatch.setattr(
        gateway,
        "datetime",
        Mock(wraps=datetime, now=Mock(return_value=datetime(2026, 12, 31, 23, 30, tzinfo=UTC))),
    )
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "gateway-spend-test-signing-secret")
    monkeypatch.setattr(gateway, "langsmith_issuer", lambda: "https://smith.example")
    base = {
        "id": "personal",
        "name": "Personal budget",
        "enabled": True,
        "subject_matchers": [{"key": "user_id", "value": "ls-alice"}],
        "config": {
            "version": 3,
            "limits": [
                {"window": "daily", "limit_usd": 10},
                {"window": "daily", "limit_usd": 5, "providers": ["openai"]},
                {"window": "monthly", "limit_usd": 100},
            ],
        },
        "current_spend": [
            {"window": "daily", "spend_usd": 3.25},
            {
                "window": "daily",
                "providers": ["openai"],
                "scope": "provider:openai",
                "spend_usd": 1.5,
            },
        ],
    }

    def respond(request):
        assert request.headers["authorization"] == "Bearer langsmith-test-token"
        assert dict(request.url.params) == {
            "policy_type": "spend_cap",
            "subject_matcher_key": "user_id",
            "subject_matcher_value": "ls-alice",
        }
        return httpx2.Response(
            200,
            json=[
                base,
                {**base, "enabled": False},
                {**base, "subject_matchers": [{"key": "user_id", "value": "other"}]},
                {
                    **base,
                    "subject_matchers": [
                        {"key": "user_id", "value": "ls-alice"},
                        {"key": "workspace_id", "value": "workspace"},
                    ],
                },
            ],
        )

    monkeypatch.setattr(gateway, "pooled_transport", lambda: httpx2.MockTransport(respond))
    app = FastAPI()
    app.include_router(routes.router, prefix="/dashboard/api")
    session_user = old_user if provider == "langsmith" else user
    token = oauth.issue_session(
        login="alice" if provider == "github" else f"user:{session_user.id}",
        email=None,
        avatar_url=None,
        user_id=str(session_user.id),
        provider="github" if provider == "github" else "langsmith",
        langsmith_subject="ls-alice",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(oauth.COOKIE_NAME, token)
        response = await client.get("/dashboard/api/analytics/gateway-spend?user_id=other")
        assert response.status_code == 200
        langsmith_users.revoked.add(user.id)
        revoked = await client.get("/dashboard/api/analytics/gateway-spend")
    assert revoked.status_code == 401
    result = response.json()
    assert result["status"] == "ready"
    assert [limit["spend_usd"] for limit in result["limits"]] == ["3.25", "1.5", None]
    assert [limit["limit_usd"] for limit in result["limits"]] == ["10", "5", "100"]
    assert [limit["resets_at"] for limit in result["limits"]] == ["2027-01-01T00:00:00Z"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "sign_in_required"), (403, "forbidden"), (500, "unavailable")],
)
async def test_gateway_spend_distinguishes_upstream_failures(
    monkeypatch, langsmith_users, status, expected
):
    user = langsmith_users.add("alice")
    monkeypatch.setattr(
        gateway,
        "pooled_transport",
        lambda: httpx2.MockTransport(lambda request: httpx2.Response(status)),
    )
    principal = gateway.DashboardPrincipal("github", "alice", user.id)
    assert (await gateway.GatewaySpend.for_person(principal)).status == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window", "now", "expected"),
    [
        ("hourly", "2026-12-31T23:30:00Z", "2027-01-01T00:00:00Z"),
        ("daily", "2028-02-28T12:00:00Z", "2028-02-29T00:00:00Z"),
        ("weekly", "2026-11-15T23:59:59Z", "2026-11-16T00:00:00Z"),
        ("weekly", "2026-11-16T00:00:00Z", "2026-11-23T00:00:00Z"),
        ("monthly", "2028-01-31T12:00:00Z", "2028-02-01T00:00:00Z"),
        ("monthly", "2026-12-31T23:30:00Z", "2027-01-01T00:00:00Z"),
        ("daily", "2026-11-15T20:00:00-08:00", "2026-11-17T00:00:00Z"),
        ("unknown", "2026-11-15T12:00:00Z", None),
    ],
)
async def test_gateway_cap_resets_at_next_utc_calendar_boundary(window, now, expected):
    limit = gateway.SpendLimit(window=window, limit_usd=10)
    reset = await limit.next_reset(datetime.fromisoformat(now))
    assert reset == (datetime.fromisoformat(expected) if expected else None)


@pytest.mark.asyncio
async def test_legacy_spend_and_missing_counter_are_distinct():
    policy = gateway.SpendPolicy.model_validate(
        {
            "id": "legacy",
            "name": "Monthly budget",
            "enabled": True,
            "subject_matchers": [],
            "config": {"window": "monthly", "limit_usd": 100},
        }
    )
    assert (await policy.usage())[0].spend_usd is None
    policy.current_spend_usd = gateway.Decimal("0")
    assert (await policy.usage())[0].spend_usd == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "sign_in_required"), (403, "sign_in_required"), (503, "unavailable")],
)
async def test_missing_grant_never_uses_a_service_key(
    monkeypatch, langsmith_users, status, expected
):
    user = langsmith_users.add("alice")
    monkeypatch.setattr(
        LangSmithLogin,
        "authorize_user",
        AsyncMock(side_effect=HTTPException(status, "Cannot authorize LangSmith login")),
    )
    transport = Mock()
    monkeypatch.setattr(gateway, "pooled_transport", transport)
    principal = gateway.DashboardPrincipal("github", "alice", user.id)
    result = await gateway.GatewaySpend.for_person(principal)
    assert result.status == expected
    assert result.limits == []
    transport.assert_not_called()
