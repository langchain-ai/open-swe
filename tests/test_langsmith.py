```python
import os
import base64
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from pytest import MonkeyPatch

# Import the module under test
from langsmith import (
    _get_langsmith_api_key,
    _parse_optional_int,
    _get_sandbox_snapshot_config,
    _github_proxy_rules,
    _configure_github_proxy,
    create_langsmith_sandbox,
    _update_thread_sandbox_metadata,
    LangSmithSandbox,  # Adjust if class name differs
)


# ---------------------------------------------------------------
# Tests for helper functions
# ---------------------------------------------------------------

class TestGetLangsmithApiKey:
    def test_env_var_set(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-123")
        assert _get_langsmith_api_key() == "test-key-123"

    def test_env_var_empty(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        assert _get_langsmith_api_key() is None

    def test_env_var_missing(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        assert _get_langsmith_api_key() is None

    def test_env_var_whitespace(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "  ")
        # Assuming the function strips whitespace; if not, adjust assertion
        assert _get_langsmith_api_key() is None


class TestParseOptionalInt:
    def test_env_var_valid_int(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "42")
        result = _parse_optional_int("TEST_INT", default=10)
        assert result == 42

    def test_env_var_missing_returns_default(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT", raising=False)
        result = _parse_optional_int("TEST_INT", default=10)
        assert result == 10

    def test_env_var_empty_returns_default(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "")
        result = _parse_optional_int("TEST_INT", default=10)
        assert result == 10

    def test_env_var_invalid_string_returns_default(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_INT", "not_a_number")
        # Assuming invalid string returns default; adjust if raises
        result = _parse_optional_int("TEST_INT", default=10)
        assert result == 10

    def test_env_var_negative_int(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "-5")
        result = _parse_optional_int("TEST_INT", default=0)
        assert result == -5


class TestGetSandboxSnapshotConfig:
    def test_all_vars_set(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_SNAPSHOT_ID", "snap-1")
        monkeypatch.setenv("SANDBOX_TIMEOUT", "60")
        monkeypatch.setenv("SANDBOX_MAX_RETRIES", "3")
        monkeypatch.setenv("SANDBOX_RETRY_DELAY", "1")
        monkeypatch.setenv("SANDBOX_MAX_CONCURRENCY", "5")
        monkeypatch.setenv("SANDBOX_POLL_INTERVAL", "2")
        result = _get_sandbox_snapshot_config()
        assert result == ("snap-1", 60, 3, 1, 5, 2)

    def test_missing_snapshot_id(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("SANDBOX_SNAPSHOT_ID", raising=False)
        monkeypatch.setenv("SANDBOX_TIMEOUT", "60")
        monkeypatch.setenv("SANDBOX_MAX_RETRIES", "3")
        monkeypatch.setenv("SANDBOX_RETRY_DELAY", "1")
        monkeypatch.setenv("SANDBOX_MAX_CONCURRENCY", "5")
        monkeypatch.setenv("SANDBOX_POLL_INTERVAL", "2")
        result = _get_sandbox_snapshot_config()
        assert result == (None, 60, 3, 1, 5, 2)

    def test_all_missing_returns_defaults(self, monkeypatch: MonkeyPatch) -> None:
        for var in [
            "SANDBOX_SNAPSHOT_ID",
            "SANDBOX_TIMEOUT",
            "SANDBOX_MAX_RETRIES",
            "SANDBOX_RETRY_DELAY",
            "SANDBOX_MAX_CONCURRENCY",
            "SANDBOX_POLL_INTERVAL",
        ]:
            monkeypatch.delenv(var, raising=False)
        result = _get_sandbox_snapshot_config()
        # Assuming defaults: None, 300, 3, 1, 10, 5 (adjust as needed)
        assert result == (None, 300, 3, 1, 10, 5)


class TestGithubProxyRules:
    def test_basic_auth_header(self) -> None:
        token = "ghp_test_token"
        rules = _github_proxy_rules(token)
        expected_auth = base64.b64encode(
            f"x-access-token:{token}".encode()
        ).decode()
        # Check that at least one rule contains the Authorization header
        auth_found = any(
            rule.get("headers", {}).get("Authorization") == f"Basic {expected_auth}"
            for rule in rules
        )
        assert auth_found, "Expected Authorization header in proxy rules"

    def test_rules_are_list_of_dicts(self) -> None:
        rules = _github_proxy_rules("dummy")
        assert isinstance(rules, list)
        assert all(isinstance(r, dict) for r in rules)

    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="token cannot be empty"):
            _github_proxy_rules("")


class TestConfigureGithubProxy:
    def test_successful_config(self, mocker) -> None:
        mock_client = mocker.patch("langsmith.ProxyClient")  # Adjust module path
        mock_instance = mock_client.return_value
        _configure_github_proxy("sandbox-1", "token123")
        mock_instance.set_rules.assert_called_once()

    def test_missing_sandbox_name(self, mocker) -> None:
        with pytest.raises(ValueError, match="sandbox_name is required"):
            _configure_github_proxy("", "token")


class TestCreateLangsmithSandbox:
    def test_with_sandbox_id(self, mocker) -> None:
        mock_create = mocker.patch("langsmith.SandboxAPI.create")
        mock_create.return_value = {"id": "sandbox-001", "status": "running"}
        result = create_langsmith_sandbox(sandbox_id="sandbox-001")
        assert result["id"] == "sandbox-001"
        mock_create.assert_called_once_with(sandbox_id="sandbox-001")

    def test_without_sandbox_id(self, mocker) -> None:
        mock_create = mocker.patch("langsmith.SandboxAPI.create")
        mock_create.return_value = {"id": "auto-generated", "status": "running"}
        result = create_langsmith_sandbox()
        assert result["status"] == "running"
        mock_create.assert_called_once()

    def test_creation_failure(self, mocker) -> None:
        mock_create = mocker.patch(
            "langsmith.SandboxAPI.create",
            side_effect=RuntimeError("API failure"),
        )
        with pytest.raises(RuntimeError, match="API failure"):
            create_langsmith_sandbox(sandbox_id="fail")


class TestUpdateThreadSandboxMetadata:
    def test_successful_update(self, mocker) -> None:
        mock_update = mocker.patch("langsmith.ThreadAPI.update_sandbox")
        _update_thread_sandbox_metadata("thread-1")
        mock_update.assert_called_once_with(thread_id="thread-1")

    def test_failure_does_not_raise(self, mocker) -> None:
        mock_update = mocker.patch(
            "langsmith.ThreadAPI.update_sandbox",
            side_effect=Exception("Network error"),
        )
        # Should silently catch the exception
        _update_thread_sandbox_metadata("thread-1")  # No raised error

    def test_none_sandbox_id(self, mocker) -> None:
        mock_update = mocker.patch("langsmith.ThreadAPI.update_sandbox")
        _update_thread_sandbox_metadata(None)  # Should handle None gracefully
        mock_update.assert_not_called()


# ---------------------------------------------------------------
# Tests for the LangSmithSandbox class
# ---------------------------------------------------------------

class TestLangSmithSandboxInit:
    def test_with_provided_api_key(self) -> None:
        sandbox = LangSmithSandbox(api_key="custom-key")
        assert sandbox.api_key == "custom-key"

    def test_api_key_from_env(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("LANGSMITH_API_KEY", "env-key")
        sandbox = LangSmithSandbox()
        assert sandbox.api_key == "env-key"

    def test_no_api_key_raises(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key is required"):
            LangSmithSandbox()


class TestValidateStartupConfig:
    def test_config_present(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("DEFAULT_SANDBOX_SNAPSHOT_ID", "snap-default")
        # Should not raise
        LangSmithSandbox.validate_startup_config()

    def test_config_missing(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("DEFAULT_SANDBOX_SNAPSHOT_ID", raising=False)
        with pytest.raises(RuntimeError, match="DEFAULT_SANDBOX_SNAPSHOT_ID not set"):
            LangSmithSandbox.validate_startup_config()

    def test_config_empty(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("DEFAULT_SANDBOX_SNAPSHOT_ID", "")
        with pytest.raises(RuntimeError, match="DEFAULT_SANDBOX_SNAPSHOT_ID is empty"):
            LangSmithSandbox.validate_startup_config()


class TestGetOrCreate:
    def test_get_existing_sandbox(self, mocker) -> None:
        sandbox = LangSmithSandbox(api_key="key")
        mock_api = mocker.patch.object(sandbox, "_api")
        mock_api.get_sandbox.return_value = {"id": "existing", "status": "running"}
        result = sandbox.get_or_create(sandbox_id="existing")
        assert result == {"id": "existing"}
        mock_api.create_sandbox.assert_not_called()

    def test_create_new_sandbox(self, mocker) -> None:
        sandbox = LangSmithSandbox(api_key="key")
        mock_api = mocker.patch.object(sandbox, "_api")
        mock_api.get_sandbox.side_effect = Exception("Not found")
        mock_api.create_sandbox.return_value = {"id": "new", "status": "created"}
        result = sandbox.get_or_create(sandbox_id="new")
        assert result["id"] == "new"
        mock_api.create_sandbox.assert_called_once_with(sandbox_id="new")

    def test_get_or_create_without_id(self, mocker) -> None:
        sandbox = LangSmithSandbox(api_key="key")
        mock_api = mocker.patch.object(sandbox, "_api")
        mock_api.create_sandbox.return_value = {"id": "auto", "status": "created"}
        result = sandbox.get_or_create()
        assert result["id"] == "auto"
        mock_api.create_sandbox.assert_called_once_with()

    def test_get_or_create_module_level(self, mocker) -> None:
        # Test the standalone function
        mocker.patch("langsmith.LangSmithSandbox")  # Mock the class
        mock_instance = MagicMock()
        mocker.patch("langsmith.LangSmithSandbox", return_value=mock_instance)
        mock_instance.get_or_create.return_value = {"id": "test"}
        result = get_or_create(api_key="key", sandbox_id="test")
        assert result["id"] == "test"
        mock_instance.get_or_create.assert_called_once_with(sandbox_id="test")


class TestDelete:
    def test_delete_existing_sandbox(self, mocker