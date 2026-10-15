import pytest
from langgraph_api.auth.custom import normalize_user
from langgraph_sdk import Auth
from starlette.types import Scope

from openswe.api import agent_server_auth
from openswe.dashboard.oauth import COOKIE_NAME, issue_session

BACKEND_KEY = "lsv2_backend_key"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", BACKEND_KEY)
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-session-signing-key-at-least-32-bytes")


def _scope(*headers: tuple[bytes, bytes]) -> Scope:
    return {"type": "http", "method": "POST", "path": "/threads", "headers": list(headers)}


def _ctx(permissions: list[str], resource: str, action: str) -> Auth.types.AuthContext:
    return Auth.types.AuthContext(
        user=normalize_user({"identity": "ada"}),
        permissions=permissions,
        resource=resource,
        action=action,
    )


async def test_only_the_backend_key_or_a_dashboard_session_authenticates() -> None:
    backend = await agent_server_auth.authenticate(_scope((b"x-api-key", BACKEND_KEY.encode())))
    assert backend["permissions"] == [agent_server_auth.BACKEND_PERMISSION]

    session = issue_session(login="ada", email=None, avatar_url=None, user_id="u1")
    person = await agent_server_auth.authenticate(
        _scope((b"cookie", f"{COOKIE_NAME}={session}".encode()))
    )
    assert person == {"identity": "ada", "permissions": []}

    for headers in ([(b"x-api-key", b"lsv2_someone_elses_key")], [(b"cookie", b"osw_session=x")]):
        with pytest.raises(Auth.exceptions.HTTPException) as exc:
            await agent_server_auth.authenticate(_scope(*headers))
        assert exc.value.status_code == 401


async def test_people_read_only_their_own_threads_and_change_nothing() -> None:
    person = _ctx([], "threads", "read")
    assert await agent_server_auth.read_thread(ctx=person, value={}) == {"owner_login": "ada"}
    assert await agent_server_auth.search_threads(ctx=person, value={}) == {"owner_login": "ada"}
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await agent_server_auth.backend_only(_ctx([], "threads", "update"), {})
    assert exc.value.status_code == 403

    backend = _ctx([agent_server_auth.BACKEND_PERMISSION], "threads", "update")
    assert await agent_server_auth.read_thread(ctx=backend, value={}) is None
    assert await agent_server_auth.backend_only(ctx=backend, value={}) is None
