"""MDA asks ``/mda/resolve-user`` who a channel's verified sender is in Open SWE."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from standardwebhooks import Webhook

from openswe.mda_channels.resolve_user import router
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")

_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")


async def _ask(body: dict[str, JsonValue], *, signed: bool = True) -> httpx.Response:
    app = FastAPI()
    app.include_router(router)
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if signed:
        now = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1_resolve_user",
            "webhook-timestamp": str(int(now.timestamp())),
            "webhook-signature": Webhook(_SERVICE_SECRET).sign(
                "msg_delivery-1_resolve_user", now, content.decode()
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/mda/resolve-user", content=content, headers=headers)


async def test_only_mda_may_ask() -> None:
    response = await _ask({"channel": {"name": "slack"}}, signed=False)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("actor", "maps"),
    [
        ({"provider": "slack", "id": "U1", "teamId": "T1"}, True),
        # A Slack account linked in another workspace is someone else's.
        ({"provider": "slack", "id": "U1", "teamId": "T2"}, False),
        ({"provider": "slack", "id": "U-unknown", "teamId": "T1"}, False),
        # A link that names no workspace could be anyone's in any of them.
        ({"provider": "slack", "id": "U2", "teamId": "T1"}, False),
        ({"provider": "github", "id": "1001", "name": "ada", "type": "User"}, True),
        ({"provider": "github", "id": "1001", "name": "ada", "type": "Bot"}, False),
        # Only the numeric id is GitHub's word for who it is; a login can change hands.
        ({"provider": "github", "id": "2002", "name": "ada", "type": "User"}, False),
        ({"provider": "linear", "id": "lin-1", "email": "ada@example.com"}, True),
        ({"provider": "linear", "id": "lin-1", "type": "user", "email": "ada@example.com"}, True),
        ({"provider": "linear", "id": "app-1", "type": "app", "email": "ada@example.com"}, False),
        ({"provider": "linear", "id": "lin-1"}, False),
    ],
)
async def test_a_linked_sender_runs_as_their_open_swe_user(
    actor: dict[str, str], *, maps: bool
) -> None:
    user = await User.sign_in("github", "1001", login="ada", email="ada@example.com")
    await user.link("slack", "U1", team_id="T1")
    await user.link("slack", "U2")

    response = await _ask({"actor": actor, "channel": {"name": actor["provider"]}})

    assert response.status_code == 200
    assert response.json() == ({"type": "user", "id": str(user.id)} if maps else None)


async def test_an_unverified_sender_keeps_the_channels_principal() -> None:
    response = await _ask({"channel": {"name": "linear"}})

    assert response.status_code == 200
    assert response.json() is None
