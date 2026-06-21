"""Boundary monkeypatches: fake the LLM and the external SaaS endpoints.

Everything patched here is an *external boundary*, not agent logic:
  - the LLM (model factory) -> scripted fake
  - GitHub App token mint + GitHub REST base URL -> dummy token + fake GitHub
  - Slack API base URL -> fake Slack
  - the api.github.com/user identity lookup -> offline (falls back to config)

Applied at import of both the graph entrypoint and the HTTP harness (same dev
process), so it runs before the first run regardless of import order. Idempotent.
"""

from __future__ import annotations

import e2e_env  # noqa: F401  (sets env before any agent import)

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return

    import importlib

    from agent import server
    from agent.utils import auth, authorship
    from agent.utils import slack as slack_utils

    # NB: ``from agent.tools import open_pull_request`` returns the re-exported
    # *function* (the tools package __init__ shadows the submodule), so patch the
    # actual module object by name instead.
    opr = importlib.import_module("agent.tools.open_pull_request")

    from e2e_env import FAKE_GITHUB_API, FAKE_SLACK_API
    from fake_llm import FakeScriptedChatModel, build_script

    def _fake_make_model(model_id: str, **kwargs: object):  # noqa: ARG001
        return FakeScriptedChatModel(script=build_script())

    server.make_model = _fake_make_model

    async def _dummy_install_token_with_expiry() -> tuple[str, str | None]:
        return "dummy-installation-token", None

    async def _dummy_install_token() -> str:
        return "dummy-installation-token"

    auth.get_github_app_installation_token_with_expiry = _dummy_install_token_with_expiry
    opr.get_github_app_installation_token = _dummy_install_token

    # Point the real PR/Slack code at the in-process fakes.
    opr.GITHUB_API = FAKE_GITHUB_API
    slack_utils.SLACK_API_BASE_URL = FAKE_SLACK_API

    # Keep the triggering-user identity lookup offline; the real fallback to
    # config-derived identity (Slack name/email) still runs.
    authorship._identity_from_github_token = lambda _token: None  # noqa: SLF001

    # OAuth-token store is an external credential boundary. Stub it so a web
    # follow-up (dashboard run.start) and PR-as-user resolution have a token;
    # the real ownership/authorization checks still run.
    from agent.dashboard import profiles, thread_api

    async def _dummy_user_token(login: str, **_kwargs: object) -> str:  # noqa: ARG001
        return "dummy-user-oauth-token"

    profiles.get_valid_access_token = _dummy_user_token
    thread_api.get_valid_access_token = _dummy_user_token

    _applied = True
