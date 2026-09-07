"""The local demo exercises production behavior without any live integrations."""

import httpx
import pytest

from agent.utils import dashboard_ui
from tests.investigations.preview import BANNER_TEXT, BASE_URL, create_preview_app


@pytest.fixture
async def preview():
    app = create_preview_app(mount_ui=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
            base_url=BASE_URL,
            headers={"Origin": BASE_URL},
        ) as client:
            yield client, app.state.preview


async def test_preview_enrolls_through_receipts_and_real_worker(preview):
    client, runtime = preview
    me = await client.get("/dashboard/api/me")
    assert me.json()["login"] == "demo-user"
    assert me.json()["is_admin"] is True
    listing = await client.get("/dashboard/api/investigate/investigations")
    record = listing.json()["items"][0]
    assert record["channel_name"] == "inc-checkout-errors"
    assert record["status"] == "watching"
    detail = await client.get(f"/dashboard/api/investigate/investigations/{record['id']}")
    assert detail.json()["report"]["outcome"] == "findings"
    assert len(detail.json()["report"]["evidence"]) >= 3
    assert "conversations.join" in runtime.slack_calls
    assert len(runtime.engine_calls) == 1
    assert not runtime.outbound_attempts


async def test_pause_question_resume_run_real_lifecycle(preview):
    client, runtime = preview
    listing = await client.get("/dashboard/api/investigate/investigations")
    id = listing.json()["items"][0]["id"]
    path = f"/dashboard/api/investigate/investigations/{id}"
    for request_id, action, text, expected in [
        ("pause-1", "pause", None, "paused"),
        ("ask-1", "ask", "What changed before the errors?", "paused"),
        ("resume-1", "resume", None, "watching"),
    ]:
        response = await client.post(
            path + "/commands",
            json={
                "request_id": request_id,
                "action": action,
                "text": text,
            },
        )
        assert response.status_code == 202, response.text
        detail = (await client.get(path)).json()
        assert detail["investigation"]["status"] == expected
    assert len(runtime.engine_calls) == 2
    assert runtime.engine_calls[-1] == "What changed before the errors?"
    assert any(item["type"] == "question" for item in detail["activity"])
    assert not runtime.outbound_attempts


async def test_settings_use_real_versioning_with_fake_auth_and_recovery(preview):
    client, runtime = preview
    path = "/dashboard/api/investigate/settings"
    settings = (await client.get(path)).json()
    policy = settings["policy"] | {"channel_prefix": "inc-demo-"}
    response = await client.patch(
        path,
        json={
            "expected_version": policy["version"],
            "policy": policy,
        },
    )
    assert response.status_code == 202, response.text
    changed = (await client.get(path)).json()
    assert changed["policy"]["channel_prefix"] == "inc-demo-"
    assert changed["policy"]["version"] == policy["version"] + 1
    assert changed["last_operation"]["status"] == "applied"
    assert "auth.test" in runtime.slack_calls
    assert runtime.crons.created
    conflict = await client.patch(path, json={"expected_version": 0, "policy": policy})
    assert conflict.status_code == 409
    assert not runtime.outbound_attempts


async def test_cross_origin_mutation_and_nonlocal_network_are_rejected(preview):
    client, runtime = preview
    response = await client.post("/preview/reset", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    with pytest.raises(RuntimeError, match="Local preview blocks"):
        async with httpx.AsyncClient() as external:
            await external.get("https://slack.com/api/auth.test")
    assert runtime.outbound_attempts == ["https://slack.com/api/auth.test"]
    status = await client.get("/preview/status")
    assert status.json()["banner"] == BANNER_TEXT


async def test_proxy_requests_client_shell_and_injects_persistent_banner():
    app = create_preview_app()
    proxy = next(
        route for route in app.routes if isinstance(route, dashboard_ui.DashboardDevProxyRoute)
    )
    await proxy.client.aclose()

    class ShellStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html><head></head><body>Dashboard shell</body></html>"

    def vite(request):
        assert request.headers["x-tss_shell"] == "true"
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            stream=ShellStream(),
            headers={"Content-Type": "text/html"},
        )

    proxy.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:3000", transport=httpx.MockTransport(vite)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)), base_url=BASE_URL
        ) as client:
            page = await client.get(
                "/investigate",
                headers={"Accept": "text/html", "Cookie": "should-not-forward=anything"},
            )
            assert page.status_code == 200
            assert BANNER_TEXT in page.text
            assert "calc(100svh - 32px)" in page.text
            assert "connect-src 'self'" in page.headers["content-security-policy"]
