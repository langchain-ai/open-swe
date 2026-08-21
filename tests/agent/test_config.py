"""Parsing rules for every accessor in :mod:`agent.config`.

These pin the rules the rest of the codebase now depends on: the defaults, the
list splitting, the precedence between related variables, and the fact that
every accessor reads at call time rather than at import.
"""

import pytest

from agent import config


def test_accessors_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the module: a rotated value is picked up immediately."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "first")
    assert config.github_webhook_secret() == "first"
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "second")
    assert config.github_webhook_secret() == "second"


# --- LangGraph deployment ---------------------------------------------------


def test_langgraph_url_prefers_the_explicit_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_URL", "https://explicit.example")
    monkeypatch.setenv("LANGGRAPH_URL_PROD", "https://prod.example")
    assert config.langgraph_url() == "https://explicit.example"


def test_langgraph_url_falls_back_to_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL_PROD", "https://prod.example")
    assert config.langgraph_url() == "https://prod.example"


def test_langgraph_url_defaults_to_the_local_dev_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_URL", raising=False)
    monkeypatch.delenv("LANGGRAPH_URL_PROD", raising=False)
    assert config.langgraph_url() == "http://localhost:2024"
    assert config.configured_langgraph_url() is None


def test_is_local_dev_follows_the_langgraph_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_URL", raising=False)
    monkeypatch.delenv("LANGGRAPH_URL_PROD", raising=False)
    assert config.is_local_dev() is True
    monkeypatch.setenv("LANGGRAPH_URL", "http://127.0.0.1:2024")
    assert config.is_local_dev() is True
    monkeypatch.setenv("LANGGRAPH_URL", "https://deployment.us.langgraph.app")
    assert config.is_local_dev() is False


def test_agent_version_metadata_is_empty_without_a_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    assert config.agent_version_metadata() == {}
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", "abc123")
    assert config.agent_version_metadata() == {"LANGSMITH_AGENT_VERSION": "abc123"}


# --- GitHub allow-lists -----------------------------------------------------


def test_allowed_github_orgs_splits_strips_and_lowercases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", " LangChain-AI , anthropics ,, ")
    assert config.allowed_github_orgs() == frozenset({"langchain-ai", "anthropics"})


def test_allowed_github_orgs_blank_is_no_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "  ,  ")
    assert config.allowed_github_orgs() == frozenset()


def test_allowed_github_repos_keeps_owner_name_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_REPOS", "LangChain-AI/Open-SWE, acme/tools")
    assert config.allowed_github_repos() == frozenset({"langchain-ai/open-swe", "acme/tools"})


def test_public_repo_org_gate_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_REPO_ORG_GATE", "  langchain-ai  ")
    assert config.public_repo_org_gate() == "langchain-ai"


def test_open_swe_mention_tags_are_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_MENTION_TAGS", "@OpenSWE, @open-swe ")
    assert config.open_swe_mention_tags() == ("@openswe", "@open-swe")


def test_open_swe_mention_tags_blank_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_MENTION_TAGS", " , ")
    assert config.open_swe_mention_tags() == ()


# --- Repositories -----------------------------------------------------------


def test_default_repo_owner_has_no_hardcoded_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFAULT_REPO_OWNER", raising=False)
    assert config.default_repo_owner() == ""


def test_slack_repo_falls_back_to_the_default_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_REPO_OWNER", "acme")
    monkeypatch.setenv("DEFAULT_REPO_NAME", "tools")
    monkeypatch.delenv("SLACK_REPO_OWNER", raising=False)
    monkeypatch.delenv("SLACK_REPO_NAME", raising=False)
    assert (config.slack_repo_owner(), config.slack_repo_name()) == ("acme", "tools")
    monkeypatch.setenv("SLACK_REPO_OWNER", "slack-org")
    assert (config.slack_repo_owner(), config.slack_repo_name()) == ("slack-org", "tools")


# --- Dashboard --------------------------------------------------------------


def test_dashboard_base_url_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    assert config.dashboard_base_url() is None


def test_dashboard_base_url_strips_space_and_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "  https://app.example/  ")
    assert config.dashboard_base_url() == "https://app.example"


def test_dashboard_allowed_origins_are_split_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", " https://a.example , https://b.example/ ")
    assert config.dashboard_allowed_origins() == ("https://a.example", "https://b.example/")


def test_dashboard_api_is_https_only_for_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")
    assert config.dashboard_api_is_https() is True
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")
    assert config.dashboard_api_is_https() is False


def test_admin_oidc_subjects_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_OIDC_SUBJECTS", "repo:acme/images:ref:refs/heads/main, acme/tools")
    assert config.admin_oidc_subjects() == (
        "repo:acme/images:ref:refs/heads/main",
        "acme/tools",
    )


# --- LangSmith credentials --------------------------------------------------


def _clear_langsmith(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGSMITH_API_KEY",
        "LANGSMITH_API_KEY_PROD",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_GATEWAY_API_KEY",
        "SANDBOX_LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_ENDPOINT_PROD",
        "SANDBOX_LANGSMITH_ENDPOINT",
        "LANGSMITH_GATEWAY_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_langsmith_credentials_are_none_without_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_langsmith(monkeypatch)
    for purpose in ("prod", "platform", "gateway", "sandbox"):
        assert config.langsmith_credentials(purpose) is None


def test_prod_credentials_pair_the_prod_key_with_the_prod_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "prod-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT_PROD", "https://prod.smith")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://workspace.smith")
    assert config.langsmith_credentials("prod") == ("prod-key", "https://prod.smith")


def test_prod_credentials_fall_back_to_the_workspace_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "legacy-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT_PROD", "https://prod.smith")
    assert config.langsmith_credentials("prod") == (
        "legacy-key",
        "https://api.smith.langchain.com",
    )


def test_platform_credentials_prefer_the_injected_workspace_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "workspace-key")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "prod-key")
    assert config.langsmith_credentials("platform") == (
        "workspace-key",
        "https://api.smith.langchain.com",
    )


def test_platform_credentials_ignore_the_legacy_langchain_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "legacy-key")
    assert config.langsmith_credentials("platform") is None


def test_gateway_credentials_prefer_the_gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "prod-key")
    assert config.langsmith_credentials("gateway") == (
        "gateway-key",
        "https://gateway.smith.langchain.com",
    )


def test_gateway_credentials_prefer_prod_over_the_workspace_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "workspace-key")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "prod-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://eu.gateway.smith/")
    assert config.langsmith_credentials("gateway") == ("prod-key", "https://eu.gateway.smith")


def test_sandbox_credentials_override_key_and_endpoint_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "workspace-key")
    monkeypatch.setenv("SANDBOX_LANGSMITH_API_KEY", "sandbox-key")
    monkeypatch.setenv("SANDBOX_LANGSMITH_ENDPOINT", "https://sandbox.smith")
    assert config.langsmith_credentials("sandbox") == ("sandbox-key", "https://sandbox.smith")


def test_sandbox_credentials_fall_back_to_the_platform_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langsmith(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "workspace-key")
    assert config.langsmith_credentials("sandbox") == (
        "workspace-key",
        "https://api.smith.langchain.com",
    )


def test_gateway_openai_use_responses_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", raising=False)
    assert config.langsmith_gateway_openai_use_responses() is True
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "false")
    assert config.langsmith_gateway_openai_use_responses() is False
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "")
    assert config.langsmith_gateway_openai_use_responses() is False


def test_gateway_enabled_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_GATEWAY_ENABLED", raising=False)
    assert config.langsmith_gateway_enabled_default() is False
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "on")
    assert config.langsmith_gateway_enabled_default() is True


# --- Models -----------------------------------------------------------------


def test_openai_base_url_prefers_the_modern_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://modern.example")
    monkeypatch.setenv("OPENAI_API_BASE", "https://legacy.example")
    assert config.openai_base_url() == "https://modern.example"
    monkeypatch.delenv("OPENAI_BASE_URL")
    assert config.openai_base_url() == "https://legacy.example"


def test_missing_provider_api_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.missing_provider_api_key("anthropic:claude") == "ANTHROPIC_API_KEY"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert config.missing_provider_api_key("anthropic:claude") is None


def test_missing_provider_api_key_ignores_unrouted_providers() -> None:
    assert config.missing_provider_api_key("google_vertexai:gemini") is None


def test_model_call_timeout_falls_back_on_a_bad_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "not-a-number")
    assert config.model_call_timeout_seconds(900.0) == 900.0
    monkeypatch.setenv("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "-5")
    assert config.model_call_timeout_seconds(900.0) == 900.0
    monkeypatch.setenv("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "30")
    assert config.model_call_timeout_seconds(900.0) == 30.0


# --- Sandboxes --------------------------------------------------------------


def test_sandbox_provider_defaults_to_langsmith(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_TYPE", raising=False)
    assert config.sandbox_provider() == "langsmith"
    assert config.is_langsmith_sandbox() is True
    monkeypatch.setenv("SANDBOX_TYPE", "modal")
    assert config.sandbox_provider() == "modal"
    assert config.is_langsmith_sandbox() is False


def test_sandbox_int_settings_refuse_a_malformed_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_SANDBOX_VCPUS", "many")
    with pytest.raises(ValueError, match="DEFAULT_SANDBOX_VCPUS must be an integer"):
        config.sandbox_vcpus(4)


def test_sandbox_ttls_refuse_a_negative_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_SANDBOX_IDLE_TTL_SECONDS", "-1")
    with pytest.raises(ValueError, match=">= 0"):
        config.sandbox_idle_ttl_seconds(7200)


def test_sandbox_create_extra_fields_requires_a_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_CREATE_EXTRA_JSON", "  ")
    assert config.sandbox_create_extra_fields() == {}
    monkeypatch.setenv("SANDBOX_CREATE_EXTRA_JSON", '{"_internal_runtime": "v2"}')
    assert config.sandbox_create_extra_fields() == {"_internal_runtime": "v2"}
    monkeypatch.setenv("SANDBOX_CREATE_EXTRA_JSON", "[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        config.sandbox_create_extra_fields()


def test_e2b_template_rejects_a_blank_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_TEMPLATE", raising=False)
    assert config.e2b_template() is None
    monkeypatch.setenv("E2B_TEMPLATE", "  ")
    with pytest.raises(ValueError, match="E2B_TEMPLATE must not be empty"):
        config.e2b_template()
    monkeypatch.setenv("E2B_TEMPLATE", " open-swe ")
    assert config.e2b_template() == "open-swe"


def test_repo_snapshot_stale_build_seconds_clamps_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPO_SNAPSHOT_STALE_BUILD_SECONDS", "-30")
    assert config.repo_snapshot_stale_build_seconds(600) == 0
    monkeypatch.setenv("REPO_SNAPSHOT_STALE_BUILD_SECONDS", "not-a-number")
    assert config.repo_snapshot_stale_build_seconds(600) == 600


# --- Integrations -----------------------------------------------------------


def test_corridor_credentials_take_the_first_configured_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CORRIDOR_API_TOKEN", "CORRIDOR_MCP_TOKEN", "CORRIDOR_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert config.corridor_mcp_token() == ""
    monkeypatch.setenv("CORRIDOR_TOKEN", "third")
    assert config.corridor_mcp_token() == "third"
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "first")
    assert config.corridor_mcp_token() == "first"


def test_stagehand_local_mode_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAGEHAND_ENV", raising=False)
    assert config.stagehand_local_mode() is True
    monkeypatch.setenv("STAGEHAND_ENV", " browserbase ")
    assert config.stagehand_local_mode() is False


def test_stagehand_headless_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAGEHAND_HEADLESS", raising=False)
    assert config.stagehand_headless() is True
    monkeypatch.setenv("STAGEHAND_HEADLESS", "false")
    assert config.stagehand_headless() is False


def test_stagehand_model_api_key_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("STAGEHAND_MODEL_API_KEY", "MODEL_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config.stagehand_model_api_key() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic")
    assert config.stagehand_model_api_key() == "anthropic"
    monkeypatch.setenv("MODEL_API_KEY", "generic")
    assert config.stagehand_model_api_key() == "generic"
    monkeypatch.setenv("STAGEHAND_MODEL_API_KEY", "stagehand")
    assert config.stagehand_model_api_key() == "stagehand"


# --- Webhooks ---------------------------------------------------------------


def test_completion_webhook_base_defaults_to_the_route_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMPLETION_WEBHOOK_URL", raising=False)
    assert config.completion_webhook_base_url() == "/webhooks/run-complete"


def test_run_complete_secret_is_none_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "   ")
    assert config.run_complete_webhook_secret() is None
