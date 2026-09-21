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
import re
from urllib.parse import urlparse

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
    from agent.slack import http as slack_http
    from agent.utils import authorship

    # NB: ``from agent.tools import open_pull_request`` returns the re-exported
    # *function* (the tools package __init__ shadows the submodule), so patch the
    # actual module object by name instead.
    opr = importlib.import_module("agent.tools.open_pull_request")

    from e2e_env import (
        BASE_URL,
        FAKE_GITHUB_API,
        FAKE_SLACK_API,
        OWNER,
        REPO,
        SECOND_OWNER,
        SECOND_REPO,
    )

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

    # Callers pass installation ids, repository scopes and permission maps; the
    # fake GitHub does not care, so accept whatever the real signatures take.
    async def _dummy_install_token_with_expiry(**_kwargs: object) -> tuple[str, str | None]:
        return "dummy-installation-token", None

    async def _dummy_install_token(**_kwargs: object) -> str:
        return "dummy-installation-token"

    async def _dummy_install_id(owner: str, repo: str) -> int:  # noqa: ARG001
        return 42

    auth.get_github_app_installation_token_with_expiry = _dummy_install_token_with_expiry
    auth.get_github_app_installation_token = _dummy_install_token
    auth.get_github_app_installation_id_for_repo = _dummy_install_id
    opr.__dict__["get_github_app_installation_token"] = _dummy_install_token

    # The App-token boundary again, for the modules that bound these names at
    # import time. Resolving an installation would reach api.github.com, and
    # without it a durable watch has no token and silently does nothing.
    from agent import baby_sit
    from agent.expedited_review import voting, watch

    # Same shadowing caveat as ``opr`` above: the tools package re-exports the
    # functions, so reach the modules by name.
    manage_baby_sit = importlib.import_module("agent.tools.manage_baby_sit")
    expedite_tool = importlib.import_module("agent.tools.expedite_pr_approval")
    thread_tools = importlib.import_module("agent.tools.threads")

    for module in (watch, voting, manage_baby_sit, baby_sit):
        for name, stub in (
            ("get_github_app_installation_id_for_repo", _dummy_install_id),
            ("get_github_app_installation_token", _dummy_install_token),
        ):
            if hasattr(module, name):
                module.__dict__[name] = stub

    # Point the real PR/Slack code at the in-process fakes.
    opr.__dict__["GITHUB_API"] = FAKE_GITHUB_API
    slack_http.SLACK_API_BASE_URL = FAKE_SLACK_API

    from agent.github import repos as github_repos

    async def _fake_user_repos(
        _login: str,
    ) -> tuple[list[github_repos.InstallationSummary], list[github_repos.RepositorySummary]]:
        return (
            [{"id": 42, "account": {"login": OWNER, "type": "Organization"}}],
            [
                {"full_name": f"{OWNER}/{REPO}", "private": False},
                {"full_name": f"{SECOND_OWNER}/{SECOND_REPO}", "private": False},
            ],
        )

    github_repos.fetch_user_installations_and_repos = _fake_user_repos

    # A PR URL identifies the repository a tool is allowed to act on, so the real
    # parser only accepts github.com. The fake GitHub serves its pull requests
    # from the harness origin instead, so teach the parser that one extra shape
    # rather than weakening the host check that production relies on.
    from agent.slack import client as slack_client
    from agent.slack.client import GitHubPrRef
    from agent.slack.tools import request_pr_review

    _real_parse = slack_client.parse_github_pr_url
    _mock_pr_path = re.compile(r"^/mock/github/([^/]+)/([^/]+)/pull/(\d+)/?$")

    def _parse_pr_url(url: str) -> GitHubPrRef | None:
        parsed = _real_parse(url)
        if parsed is not None:
            return parsed
        cleaned = url.strip().strip("<>").split("|", 1)[0]
        target = urlparse(cleaned)
        if f"{target.scheme}://{target.netloc}" != BASE_URL:
            return None
        match = _mock_pr_path.match(target.path)
        if match is None:
            return None
        owner, repo, number = match.group(1), match.group(2), int(match.group(3))
        return GitHubPrRef(
            owner=owner,
            repo=repo,
            number=number,
            url=f"{BASE_URL}/mock/github/{owner}/{repo}/pull/{number}",
        )

    slack_client.parse_github_pr_url = _parse_pr_url
    for module in (manage_baby_sit, expedite_tool, thread_tools, opr, request_pr_review):
        if "parse_github_pr_url" in module.__dict__:
            module.__dict__["parse_github_pr_url"] = _parse_pr_url

    # Keep the triggering-user identity lookup offline; the real fallback to
    # config-derived identity (Slack name/email) still runs.
    async def _no_github_identity(_token: str | None) -> None:
        return None

    authorship._identity_from_github_token = _no_github_identity  # noqa: SLF001

    # OAuth-token store is an external credential boundary. Stub it so a web
    # follow-up (dashboard run.start) and PR-as-user resolution have a token;
    # the real ownership/authorization checks still run.
    from agent.dashboard import profiles, repo_access
    from agent.github import (
        pull_request_actions,
        pull_request_context,
        pull_request_status,
        repo_merge_methods,
    )
    from agent.threads import access as thread_access

    async def _dummy_user_token(login: str, **_kwargs: object) -> str:
        # Carries the login so the fake GitHub can attribute a write (a submitted
        # review) to the person whose token made it, as the real API does.
        return f"dummy-user-oauth-token:{login}" if login else "dummy-user-oauth-token"

    # Every module that bound the name at import time needs its own rebind, or
    # it keeps calling the real store and reports "no GitHub token for @user".
    from agent.github import repos as github_repos
    from agent.review import routes as review_routes
    from agent.webhooks import common as webhook_common

    for module in (
        profiles,
        thread_access,
        webhook_common,
        voting,
        repo_access,
        github_repos,
        review_routes,
    ):
        module.__dict__["get_valid_access_token"] = _dummy_user_token
    # Each of these imported GITHUB_API_BASE by name, so the module attribute is
    # the one their calls read.
    pull_request_status.GITHUB_API_BASE = FAKE_GITHUB_API
    pull_request_actions.GITHUB_API_BASE = FAKE_GITHUB_API
    repo_merge_methods.GITHUB_API_BASE = FAKE_GITHUB_API
    pull_request_status.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"
    pull_request_context.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"
    pull_request_actions.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"

    # The repo-access check builds its api.github.com URL inline, so there is no
    # base to repoint; swap the one call for the same request against the fake.
    # require_repo_access_for_user's own token fetch, 401 refresh and status
    # mapping still run.
    repo_access.assert_repo_access = _fake_assert_repo_access

    # Every other module that captured the REST base at import time: PR and
    # check reads (``ci``), the check-run writes, and the expedited-review
    # eligibility, readiness and voting calls.
    from agent.expedited_review import eligibility, readiness
    from agent.github import checks as github_checks
    from agent.github import ci as github_ci

    github_ci.__dict__["_GITHUB_API_BASE"] = FAKE_GITHUB_API
    github_checks.__dict__["_GITHUB_API_BASE"] = FAKE_GITHUB_API
    for module in (eligibility, readiness, voting):
        module.__dict__["GITHUB_API_BASE"] = FAKE_GITHUB_API

    # Snapshot service: another external boundary. The E2E runs the local sandbox
    # provider, so there is nothing to capture from — record the request in the
    # fake store instead. The workspace tools, store writes, name/tag scheme
    # and status transitions all still run for real.
    from agent.sandboxes.providers import langsmith as langsmith_integration
    from agent.workspaces import store as workspaces_store

    langsmith_integration.get_async_sandbox_client = _FakeSandboxClient
    # The capture path refuses to run off the langsmith provider; with that
    # provider's snapshot API faked above, the E2E's local sandbox is capturable.
    workspaces_store.require_capture_support = lambda: None

    # A refresh boots its own builder to run the scripts in. There is no platform
    # to boot one from here, so the local provider stands in and nothing is
    # reclaimed afterwards; the scripts, the capture and the record all run for real.
    from agent.workspaces import refresh as workspace_refresh

    workspace_refresh.require_capture_support = lambda: None
    workspace_refresh._create_builder_sandbox = _fake_builder_sandbox
    workspace_refresh._release_builder_sandbox = _release_nothing

    _applied = True


async def _fake_assert_repo_access(full_name: str, token: str) -> str:
    import httpx2
    from e2e_env import FAKE_GITHUB_API

    from agent.dashboard import repo_access

    normalized = repo_access.normalize_repo_full_name(full_name)
    owner, name = normalized.split("/", 1)
    async with httpx2.AsyncClient(timeout=repo_access.DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.get(
            f"{FAKE_GITHUB_API}/repos/{owner}/{name}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    repo_access._raise_for_github_repo_status(response.status_code)  # noqa: SLF001
    return normalized


async def _fake_builder_sandbox(_record: object, _snapshot_id: object = None) -> object:
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
