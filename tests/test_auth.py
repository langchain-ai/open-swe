from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.utils import auth


# -- is_bot_token_only_mode -----------------------------------------------


def test_bot_token_only_mode_true(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "some-key")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")
    assert auth.is_bot_token_only_mode() is True


def test_bot_token_only_mode_false_when_jwt_secret_set(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "some-key")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "jwt-secret")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")
    assert auth.is_bot_token_only_mode() is False


def test_bot_token_only_mode_false_when_api_key_map_set(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "some-key")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "user1:key1")
    assert auth.is_bot_token_only_mode() is False


def test_bot_token_only_mode_false_when_no_api_key(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")
    assert auth.is_bot_token_only_mode() is False


# -- helper functions -----------------------------------------------------


def test_retry_instruction_slack():
    assert "Slack thread" in auth._retry_instruction("slack")


def test_retry_instruction_other():
    result = auth._retry_instruction("linear")
    assert "@openswe" in result


def test_source_account_label_slack():
    assert auth._source_account_label("slack") == "Slack"


def test_source_account_label_linear():
    assert auth._source_account_label("linear") == "Linear"


def test_auth_link_text_slack():
    url = "https://auth.example.com/oauth"
    assert auth._auth_link_text("slack", url) == url


def test_auth_link_text_linear():
    url = "https://auth.example.com/oauth"
    result = auth._auth_link_text("linear", url)
    assert "[Authenticate with GitHub]" in result
    assert url in result


def test_work_item_label_slack():
    assert auth._work_item_label("slack") == "thread"


def test_work_item_label_linear():
    assert auth._work_item_label("linear") == "issue"


# -- get_secret_key_for_user ----------------------------------------------


def test_get_secret_key_for_user_returns_jwt(monkeypatch):
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "test-jwt-secret")
    token, key_type = auth.get_secret_key_for_user("user-123", "tenant-456")
    assert isinstance(token, str)
    assert len(token) > 0
    assert key_type == "service"


def test_get_secret_key_for_user_raises_without_secret(monkeypatch):
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    with pytest.raises(ValueError, match="X_SERVICE_AUTH_JWT_SECRET"):
        auth.get_secret_key_for_user("user-123", "tenant-456")


def test_get_secret_key_for_user_jwt_contains_user_id(monkeypatch):
    import jwt

    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "test-jwt-secret")
    token, _ = auth.get_secret_key_for_user("user-123", "tenant-456")
    decoded = jwt.decode(token, "test-jwt-secret", algorithms=["HS256"])
    assert decoded["user_id"] == "user-123"
    assert decoded["tenant_id"] == "tenant-456"
    assert decoded["sub"] == "unspecified"
    assert "exp" in decoded


# -- get_ls_user_id_from_email (async) ------------------------------------


@pytest.mark.asyncio
async def test_get_ls_user_id_from_email_no_api_key(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "")
    result = await auth.get_ls_user_id_from_email("user@example.com")
    assert result == {"ls_user_id": None, "tenant_id": None}


@pytest.mark.asyncio
async def test_get_ls_user_id_from_email_success(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"ls_user_id": "ls-user-1", "tenant_id": "tenant-1"}
    ]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.get_ls_user_id_from_email("user@example.com")
        assert result["ls_user_id"] == "ls-user-1"
        assert result["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_get_ls_user_id_from_email_empty_members(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.get_ls_user_id_from_email("unknown@example.com")
        assert result == {"ls_user_id": None, "tenant_id": None}


@pytest.mark.asyncio
async def test_get_ls_user_id_from_email_http_error(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "test-key")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.HTTPError("connection failed"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.get_ls_user_id_from_email("user@example.com")
        assert result == {"ls_user_id": None, "tenant_id": None}


# -- get_github_token_for_user (async) ------------------------------------


@pytest.mark.asyncio
async def test_get_github_token_for_user_no_provider_id(monkeypatch):
    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "")
    result = await auth.get_github_token_for_user("user-1", "tenant-1")
    assert "error" in result


@pytest.mark.asyncio
async def test_get_github_token_for_user_returns_token(monkeypatch):
    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "github-provider")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "jwt-secret")

    mock_response = MagicMock()
    mock_response.json.return_value = {"token": "ghp_abc123"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.get_github_token_for_user("user-1", "tenant-1")
        assert result == {"token": "ghp_abc123"}


@pytest.mark.asyncio
async def test_get_github_token_for_user_returns_auth_url(monkeypatch):
    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "github-provider")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "jwt-secret")

    mock_response = MagicMock()
    mock_response.json.return_value = {"url": "https://github.com/login/oauth/authorize?..."}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.get_github_token_for_user("user-1", "tenant-1")
        assert "auth_url" in result


# -- resolve_github_token_from_email (async) ------------------------------


@pytest.mark.asyncio
async def test_resolve_github_token_from_email_no_ls_user(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.utils.auth.httpx.AsyncClient", return_value=mock_client):
        result = await auth.resolve_github_token_from_email("unknown@test.com")
        assert result["error"] == "no_ls_user"


# -- leave_failure_comment ------------------------------------------------
# (Existing tests in test_auth_sources.py cover slack path;
#  adding linear and github paths here.)


def test_leave_failure_comment_linear(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_comment(issue_id, body):
        captured["issue_id"] = issue_id
        captured["body"] = body
        return True

    monkeypatch.setattr(auth, "comment_on_linear_issue", fake_comment)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {
            "configurable": {
                "linear_issue": {"id": "issue-uuid-123"},
            }
        },
    )

    asyncio.run(auth.leave_failure_comment("linear", "Auth failed"))
    assert captured["issue_id"] == "issue-uuid-123"
    assert captured["body"] == "Auth failed"


def test_leave_failure_comment_github_logs_warning(monkeypatch):
    """GitHub source should just log, not raise."""
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {"configurable": {}},
    )
    # Should not raise
    asyncio.run(auth.leave_failure_comment("github", "Auth failed"))


def test_leave_failure_comment_unknown_source_raises(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {"configurable": {}},
    )
    with pytest.raises(ValueError, match="Unknown source"):
        asyncio.run(auth.leave_failure_comment("telegram", "Auth failed"))


# -- persist_encrypted_github_token ---------------------------------------


@pytest.mark.asyncio
async def test_persist_encrypted_github_token(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)

    captured: dict[str, object] = {}

    class _FakeThreads:
        async def update(self, *, thread_id, metadata):
            captured["thread_id"] = thread_id
            captured["metadata"] = metadata

    monkeypatch.setattr(auth, "client", MagicMock(threads=_FakeThreads()))

    encrypted = await auth.persist_encrypted_github_token("thread-1", "ghp_secret")
    assert encrypted != ""
    assert encrypted != "ghp_secret"
    assert captured["thread_id"] == "thread-1"
    assert captured["metadata"]["github_token_encrypted"] == encrypted


# -- _resolve_bot_installation_token --------------------------------------


@pytest.mark.asyncio
async def test_resolve_bot_installation_token_success(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)

    class _FakeThreads:
        async def update(self, *, thread_id, metadata):
            pass

    monkeypatch.setattr(auth, "client", MagicMock(threads=_FakeThreads()))
    monkeypatch.setattr(
        auth, "get_github_app_installation_token", AsyncMock(return_value="bot-token-123")
    )

    token, encrypted = await auth._resolve_bot_installation_token("thread-1")
    assert token == "bot-token-123"
    assert encrypted != ""


@pytest.mark.asyncio
async def test_resolve_bot_installation_token_no_bot_token(monkeypatch):
    monkeypatch.setattr(
        auth, "get_github_app_installation_token", AsyncMock(return_value=None)
    )

    with pytest.raises(RuntimeError, match="Bot-token-only mode"):
        await auth._resolve_bot_installation_token("thread-1")


# -- resolve_github_token -------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_github_token_bot_mode(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)

    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "some-key")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")

    class _FakeThreads:
        async def update(self, *, thread_id, metadata):
            pass

    monkeypatch.setattr(auth, "client", MagicMock(threads=_FakeThreads()))
    monkeypatch.setattr(
        auth, "get_github_app_installation_token", AsyncMock(return_value="bot-token")
    )

    config = {"configurable": {"source": "slack"}}
    token, encrypted = await auth.resolve_github_token(config, "thread-1")
    assert token == "bot-token"


@pytest.mark.asyncio
async def test_resolve_github_token_missing_source(monkeypatch):
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")

    config = {"configurable": {}}
    with pytest.raises(RuntimeError, match="missing source"):
        await auth.resolve_github_token(config, "thread-1")


# -- save_encrypted_token_from_email --------------------------------------


@pytest.mark.asyncio
async def test_save_encrypted_token_from_email_missing_thread_id(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {"configurable": {}},
    )

    with pytest.raises(ValueError, match="missing thread_id"):
        await auth.save_encrypted_token_from_email("user@test.com", "slack")


@pytest.mark.asyncio
async def test_save_encrypted_token_from_email_missing_email(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {"configurable": {"thread_id": "t-1", "slack_thread": {}}},
    )
    monkeypatch.setattr(
        auth, "leave_failure_comment", AsyncMock()
    )

    with pytest.raises(ValueError, match="missing user_email"):
        await auth.save_encrypted_token_from_email(None, "slack")
