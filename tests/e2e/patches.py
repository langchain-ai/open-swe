"""Boundary monkeypatches: fake the LLM and the external SaaS endpoints.

Everything patched here is an *external boundary*, not agent logic:
  - the LLM (model factory) -> scripted fake
  - GitHub App token mint + GitHub REST base URL -> dummy token + fake GitHub
  - Slack API base URL -> fake Slack
  - the api.github.com/user identity lookup -> offline (falls back to config)

Applied at import of both the graph entrypoint and the HTTP harness (same dev
process), so it runs before the first run regardless of import order. Idempotent.
"""

import logging
import os

import e2e_env  # noqa: F401  (sets env before any agent import)

logger = logging.getLogger(__name__)

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return

    import importlib

    from agent import server
    from agent.auth import resolve as auth
    from agent.platforms.slack import client as slack_utils
    from agent.platforms.slack import code_channels as slack_code_channels
    from agent.utils import authorship

    # NB: ``from agent.tools import open_pull_request`` returns the re-exported
    # *function* (the tools package __init__ shadows the submodule), so patch the
    # actual module object by name instead.
    opr = importlib.import_module("agent.tools.open_pull_request")

    from e2e_env import FAKE_GITHUB_API, FAKE_SLACK_API

    # The LLM is the only agent-internal piece we fake, and only by default.
    # Set E2E_REAL_LLM=1 to drive the harness (mock Slack/GitHub, real agent)
    # with a real model — useful for manually exercising plan review etc. The
    # provider key (e.g. ANTHROPIC_API_KEY) must be in the environment.
    if os.environ.get("E2E_REAL_LLM"):
        logger.warning("E2E_REAL_LLM set — using the real model factory, not the scripted fake")
    else:
        from fake_llm import FakeScriptedChatModel, build_script

        def _fake_make_model(model_id: str, **kwargs: object):  # noqa: ARG001
            return FakeScriptedChatModel(script=build_script())

        server.make_model = _fake_make_model

    async def _dummy_install_token_with_expiry() -> tuple[str, str | None]:
        return "dummy-installation-token", None

    async def _dummy_install_token() -> str:
        return "dummy-installation-token"

    auth.get_github_app_installation_token_with_expiry = _dummy_install_token_with_expiry
    opr.__dict__["get_github_app_installation_token"] = _dummy_install_token

    # Point the real PR/Slack code at the in-process fakes.
    opr.__dict__["GITHUB_API"] = FAKE_GITHUB_API
    slack_utils.SLACK_API_BASE_URL = FAKE_SLACK_API
    slack_code_channels.SLACK_API_BASE_URL = FAKE_SLACK_API

    # Keep the triggering-user identity lookup offline; the real fallback to
    # config-derived identity (Slack name/email) still runs.
    authorship._identity_from_github_token = lambda _token: None  # noqa: SLF001

    # OAuth-token store is an external credential boundary. Stub it so a web
    # follow-up (dashboard run.start) and PR-as-user resolution have a token;
    # the real ownership/authorization checks still run.
    from agent.dashboard import profiles, pull_request_context, pull_request_status, thread_api

    async def _dummy_user_token(login: str, **_kwargs: object) -> str:  # noqa: ARG001
        return "dummy-user-oauth-token"

    profiles.get_valid_access_token = _dummy_user_token
    thread_api.get_valid_access_token = _dummy_user_token
    pull_request_status.GITHUB_API_BASE = FAKE_GITHUB_API
    pull_request_status.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"
    pull_request_context.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"

    # Snapshot service: another external boundary. The E2E runs the local sandbox
    # provider, so there is nothing to capture from — record the request in the
    # fake store instead. The environment tools, store writes, name/tag scheme
    # and status transitions all still run for real.
    from agent.dashboard import environments as environments_store
    from agent.integrations import langsmith as langsmith_integration

    langsmith_integration.get_async_sandbox_client = _FakeSandboxClient
    # The capture path refuses to run off the langsmith provider; with that
    # provider's snapshot API faked above, the E2E's local sandbox is capturable.
    environments_store._require_capture_support = lambda: None

    _applied = True


class _FakeSnapshot:
    def __init__(self, snapshot_id: str, name: str) -> None:
        self.id = snapshot_id
        self.name = name
        self.status = "ready"


class _FakeSandboxClient:
    """Stands in for ``AsyncSandboxClient`` for snapshot calls only."""

    async def __aenter__(self) -> "_FakeSandboxClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def capture_snapshot(
        self,
        sandbox_name: str,
        name: str,
        *,
        timeout: int = 60,  # noqa: ARG002
        **_kwargs: object,
    ) -> _FakeSnapshot:
        import fakes

        return _FakeSnapshot(fakes.record_snapshot_capture(sandbox_name, name), name)

    async def delete_snapshot(self, snapshot_id: str) -> None:
        import fakes

        fakes.record_snapshot_delete(snapshot_id)
