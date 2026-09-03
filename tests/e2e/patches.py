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
    from agent.github import token as auth
    from agent.slack import client as slack_utils
    from agent.slack import code_channels as slack_code_channels
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
    from agent.dashboard import profiles, thread_api
    from agent.github import pull_request_context, pull_request_status

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
    from agent.sandboxes.providers import langsmith as langsmith_integration

    langsmith_integration.get_async_sandbox_client = _FakeSandboxClient
    # The capture path refuses to run off the langsmith provider; with that
    # provider's snapshot API faked above, the E2E's local sandbox is capturable.
    environments_store.require_capture_support = lambda: None

    # A refresh boots its own builder to run the scripts in. There is no platform
    # to boot one from here, so the local provider stands in and nothing is
    # reclaimed afterwards; the scripts, the capture and the record all run for real.
    from agent.dashboard import environment_refresh

    environment_refresh.require_capture_support = lambda: None
    environment_refresh._create_builder_sandbox = _fake_builder_sandbox
    environment_refresh._release_builder_sandbox = _release_nothing

    _applied = True


async def _fake_builder_sandbox(_record: object) -> object:
    from agent.sandboxes.providers.registry import create_sandbox

    return await create_sandbox()


async def _release_nothing(_sandbox_id: str) -> None:
    return None


class _FakeSnapshot:
    def __init__(self, snapshot_id: str, name: str) -> None:
        self.id = snapshot_id
        self.name = name
        self.status = "ready"


class _FakeSandboxHttp:
    """The SDK's HTTP client, which the capture path wraps to add the tag."""

    def __init__(self) -> None:
        self.body: dict[str, object] = {}

    async def post(self, url: str, **kwargs: object) -> object:  # noqa: ARG002
        payload = kwargs.get("json")
        self.body = dict(payload) if isinstance(payload, dict) else {}
        return None


class _FakeSandboxClient:
    """Stands in for ``AsyncSandboxClient`` for snapshot calls only."""

    def __init__(self) -> None:
        self._http = _FakeSandboxHttp()

    async def __aenter__(self) -> _FakeSandboxClient:
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

        await self._http.post(f"/v2/sandboxes/boxes/{sandbox_name}/snapshot", json={"name": name})
        tag = self._http.body.get("tag")
        return _FakeSnapshot(
            fakes.record_snapshot_capture(
                sandbox_name, name, tag if isinstance(tag, str) else None
            ),
            name,
        )

    async def delete_snapshot(self, snapshot_id: str) -> None:
        import fakes

        fakes.record_snapshot_delete(snapshot_id)
