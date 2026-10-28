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
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from urllib.parse import urlparse

import e2e_env  # noqa: F401  (sets env before any agent import)

logger = logging.getLogger(__name__)

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return

    # E2E_LOG_FILE captures the server's own logging, which langgraph dev does
    # not surface: without it a swallowed warning is invisible to the suite.
    log_file = os.environ.get("E2E_LOG_FILE")
    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    import importlib

    from openswe import server
    from openswe.github import token as auth
    from openswe.slack import http as slack_http
    from openswe.utils import authorship

    # NB: ``from openswe.tools import open_pull_request`` returns the re-exported
    # *function* (the tools package __init__ shadows the submodule), so patch the
    # actual module object by name instead.
    opr = importlib.import_module("openswe.tools.open_pull_request")

    from e2e_env import BASE_URL, FAKE_GITHUB_API, FAKE_SLACK_API

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
        # The review scout graph binds its own factory by name.
        from openswe.review_scout import graph as review_scout_graph

        review_scout_graph.__dict__["make_model"] = _fake_make_model

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
    # GitHubClient.as_app reads these from their defining module at call time.
    from openswe.github import app as github_app

    github_app.get_github_app_installation_token = _dummy_install_token
    github_app.get_github_app_installation_id_for_repo = _dummy_install_id
    opr.__dict__["get_github_app_installation_token"] = _dummy_install_token

    # The App-token boundary again, for the modules that bound these names at
    # import time. Resolving an installation would reach api.github.com, and
    # without it a durable watch has no token and silently does nothing.
    from openswe import baby_sit
    from openswe.expedited_review import merge, reviews, voting
    from openswe.human_review import merging, people

    # Same shadowing caveat as ``opr`` above: the tools package re-exports the
    # functions, so reach the modules by name.
    manage_baby_sit = importlib.import_module("openswe.tools.manage_baby_sit")
    expedite_tool = importlib.import_module("openswe.tools.expedite_pr_approval")
    thread_tools = importlib.import_module("openswe.tools.threads")

    for module in (people, merging, merge, voting, manage_baby_sit, baby_sit):
        for name, stub in (
            ("get_github_app_installation_id_for_repo", _dummy_install_id),
            ("get_github_app_installation_token", _dummy_install_token),
        ):
            if hasattr(module, name):
                module.__dict__[name] = stub

    # Point the real PR/Slack code at the in-process fakes.
    opr.__dict__["GITHUB_API"] = FAKE_GITHUB_API
    slack_http.SLACK_API_BASE_URL = FAKE_SLACK_API

    # Production resolves no completion webhook for a loopback deployment, since
    # the platform rejects those; langgraph.e2e.json opts this server back in, so
    # the platform delivers every run's completion to the real route.
    from openswe import dispatch

    schedule_thread_wakeup = importlib.import_module("openswe.tools.schedule_thread_wakeup")
    completion_webhook = (
        f"{BASE_URL}/webhooks/run-complete?token={os.environ['RUN_COMPLETE_WEBHOOK_SECRET']}"
    )
    for module in (dispatch, schedule_thread_wakeup):
        module.__dict__["COMPLETION_WEBHOOK_URL"] = completion_webhook

    # A PR URL identifies the repository a tool is allowed to act on, so the real
    # parser only accepts github.com. The fake GitHub serves its pull requests
    # from the harness origin instead, so teach the parser that one extra shape
    # rather than weakening the host check that production relies on.
    from openswe.slack import client as slack_client
    from openswe.slack.client import GitHubPrRef
    from openswe.slack.tools import request_pr_review

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

    # Uploading the expedited-review diff image crosses two production guards
    # that a loopback harness cannot satisfy: the ticket URL must be on
    # files.slack.com, and the byte transfer must survive the SSRF check, which
    # rejects loopback. Widen both to this one harness path — skipping them
    # would stop exercising them at all, and without the upload the card
    # silently degrades to its text fallback and the suite tests a rendering
    # nobody sees.
    from openswe.utils import url_safety

    _harness_upload = f"{BASE_URL}/fake-slack/upload/"
    _real_validate_upload = slack_client._validate_slack_upload_url
    _real_resolve = url_safety.resolve_and_validate

    def _validate_upload_url(url: str) -> None:
        if url.startswith(_harness_upload):
            return
        return _real_validate_upload(url)

    def _resolve_and_validate(url: str) -> tuple[str, list[str]]:
        if url.startswith(_harness_upload):
            host = urlparse(url).hostname or "127.0.0.1"
            return host, list(
                dict.fromkeys(
                    info[4][0] for info in socket.getaddrinfo(host, urlparse(url).port or 80)
                )
            )
        return _real_resolve(url)

    slack_client._validate_slack_upload_url = _validate_upload_url
    url_safety.resolve_and_validate = _resolve_and_validate

    slack_client.parse_github_pr_url = _parse_pr_url
    merge_tool = importlib.import_module("openswe.tools.merge_expedited_pr")
    human_review_tool = importlib.import_module("openswe.tools.request_human_review")
    for module in (
        manage_baby_sit,
        expedite_tool,
        merge_tool,
        human_review_tool,
        thread_tools,
        opr,
        request_pr_review,
    ):
        if "parse_github_pr_url" in module.__dict__:
            module.__dict__["parse_github_pr_url"] = _parse_pr_url

    # Keep the triggering-user identity lookup offline; the real fallback to
    # the config's GitHub login still runs.
    async def _no_github_identity(_token: str | None) -> None:
        return None

    authorship._identity_from_github_token = _no_github_identity  # noqa: SLF001

    # OAuth-token store is an external credential boundary. Stub it so a web
    # follow-up (dashboard run.start) and PR-as-user resolution have a token;
    # the real ownership/authorization checks still run.
    from openswe.dashboard import profiles, repo_access
    from openswe.github import http as github_http
    from openswe.github import pull_request_actions, pull_request_context, pull_request_status
    from openswe.threads import access as thread_access

    async def _dummy_user_token(login: str, **_kwargs: object) -> str:
        # Carries the login so the fake GitHub can attribute a write (a submitted
        # review) to the person whose token made it, as the real API does.
        return f"dummy-user-oauth-token:{login}" if login else "dummy-user-oauth-token"

    # Every module that bound the name at import time needs its own rebind, or
    # it keeps calling the real store and reports "no GitHub token for @user".
    import httpx
    from githubkit import GitHub
    from githubkit.auth import BaseAuthStrategy

    from openswe.github import repos as github_repos

    @asynccontextmanager
    async def _fake_github_sdk[A: BaseAuthStrategy](
        auth: A, *, timeout: float = 30.0, connect_timeout: float = 10.0
    ) -> AsyncIterator[GitHub[A]]:
        async with GitHub(
            auth,
            base_url=FAKE_GITHUB_API,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            http_cache=False,
            auto_retry=False,
        ) as client:
            yield client

    from openswe.github import sdk as github_sdk_module

    real_github_sdk = github_sdk_module.github_sdk
    github_sdk_module.github_sdk = _fake_github_sdk
    for module in list(sys.modules.values()):
        if getattr(module, "github_sdk", None) is real_github_sdk:
            module.github_sdk = _fake_github_sdk
    from openswe.review import routes as review_routes
    from openswe.schedules import store as schedules_store
    from openswe.webhooks import common as webhook_common

    for module in (
        profiles,
        thread_access,
        webhook_common,
        voting,
        reviews,
        repo_access,
        github_repos,
        review_routes,
        schedules_store,
    ):
        module.__dict__["get_valid_access_token"] = _dummy_user_token
    github_http.GITHUB_API_BASE = FAKE_GITHUB_API
    github_http.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"
    # Each of these imported GITHUB_API_BASE by name, so the module attribute is
    # the one their calls read.
    pull_request_status.GITHUB_API_BASE = FAKE_GITHUB_API
    pull_request_actions.GITHUB_API_BASE = FAKE_GITHUB_API
    pull_request_context.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"
    pull_request_actions.GITHUB_GRAPHQL = f"{FAKE_GITHUB_API}/graphql"

    # The repo-access check builds its api.github.com URL inline, so there is no
    # base to repoint; swap the one call for the same request against the fake.
    # require_repo_access_for_user's own token fetch, 401 refresh and status
    # mapping still run.
    repo_access.assert_repo_access = _fake_assert_repo_access

    # The check-run writes captured the REST base at import time.
    from openswe.github import checks as github_checks

    github_checks.__dict__["_GITHUB_API_BASE"] = FAKE_GITHUB_API

    # LangSmith's sandbox service: another external boundary. MDA creates every
    # thread's box (mda_entrypoint.py) and the backend reconnects to it by id, both
    # through the stand-in, which has no proxy to configure. Snapshots are recorded
    # in the fake store; the workspace tools, store writes, name/tag scheme and
    # status transitions all still run for real.
    import sandbox_service

    from openswe.sandboxes import lifecycle as sandbox_lifecycle
    from openswe.sandboxes.providers import langsmith as langsmith_integration

    langsmith_integration.get_async_sandbox_client = _FakeSandboxClient
    if not sandbox_service.REAL:
        from openswe.github import proxy as github_proxy

        langsmith_integration.create_langsmith_sandbox = _stand_in_sandbox
        langsmith_integration.configure_sandbox_proxy = _configure_no_proxy
        sandbox_lifecycle.configure_sandbox_proxy = _configure_no_proxy
        # The token a sandbox's GitHub proxy carries is minted from the App, like the
        # other App tokens stubbed above; the stand-in has no proxy to carry it.
        for module in (sandbox_lifecycle, github_proxy):
            module.__dict__["workspace_token"] = _sandbox_github_access

    # A refresh boots its own builder to run the scripts in: a stand-in box, of
    # which nothing is reclaimed afterwards; the scripts, the capture and the
    # record all run for real.
    from openswe.workspaces import refresh as workspace_refresh

    workspace_refresh._create_builder_sandbox = _fake_builder_sandbox
    workspace_refresh._release_builder_sandbox = _release_nothing

    # Every MDA run names its thread's browser, so sharing a thread deletes it.
    # No browser exists without LangSmith, so deleting one finds nothing.
    from openswe.sandboxes.browsers import LangSmithBrowser

    LangSmithBrowser.delete = _delete_nothing

    from openswe.review import chat as review_chat
    from openswe.review import reviews as review_reviews
    from openswe.review_scout import graph as review_scout_graph

    for module in (review_reviews, review_chat):
        module.__dict__["get_github_app_installation_token"] = _dummy_install_token
    review_chat.__dict__["fetch_pr_diff"] = _fake_fetch_pr_diff

    # A scout sandbox clones the PR through the GitHub proxy, which the stand-in
    # sandboxes do not have, so provisioning fails the way an unavailable
    # sandbox provider does. The delay keeps the run visibly in flight first,
    # which is the state the review page polls through.
    review_scout_graph.__dict__["_ensure_scout_sandbox"] = _unavailable_scout_sandbox

    _answer_every_call_on_a_fresh_replica()

    _applied = True


SCOUT_SANDBOX_ERROR = "E2E has no sandbox provider for the review scout"


_calling: ContextVar[str] = ContextVar("e2e_calling", default="")


def _answer_every_call_on_a_fresh_replica() -> None:
    """Answer each MDA call as a backend replica that never served the run would.

    Production runs three replicas, and a run's calls land on any of them; here one
    process answers them all, so it forgets the plans it cached between calls. Each
    time it plans a run is recorded, by the call that needed the plan, which
    ``/control/plans`` reports.
    """
    import fakes
    from pydantic import JsonValue

    from openswe.agent_api.graph import RemoteGraph, RunSession, RunStart
    from openswe.agent_api.tokens import RemoteRun

    answer = RemoteGraph.api
    plan = RemoteGraph._planned
    session = RemoteGraph.session
    start = RemoteGraph.start_run

    async def api(self: RemoteGraph, run: RemoteRun, endpoint: str, raw: JsonValue) -> JsonValue:
        calling = _calling.set(endpoint)
        try:
            return await answer(self, run, endpoint, raw)
        finally:
            _calling.reset(calling)

    async def start_run(self: RemoteGraph, run: RemoteRun) -> RunStart:
        # A dispatch can run in the context of a call it was made from, such as an operation.
        calling = _calling.set("start")
        try:
            return await start(self, run)
        finally:
            _calling.reset(calling)

    async def planned(
        self: RemoteGraph, run: RemoteRun, run_id: str | None, *, fresh: bool
    ) -> RunSession:
        fakes.PLANS.append({"thread_id": run.thread_id, "call": _calling.get()})
        return await plan(self, run, run_id, fresh=fresh)

    async def forgetful_session(
        self: RemoteGraph, run: RemoteRun, run_id: str | None, *, fresh: bool = False
    ) -> RunSession:
        self._sessions.clear()
        return await session(self, run, run_id, fresh=fresh)

    RemoteGraph.api = api
    RemoteGraph.start_run = start_run
    RemoteGraph._planned = planned
    RemoteGraph.session = forgetful_session


async def _unavailable_scout_sandbox(_thread_id: str, _cfg: object) -> object:
    import asyncio

    await asyncio.sleep(float(os.environ.get("E2E_SCOUT_FAIL_AFTER_SECONDS", "4")))
    raise RuntimeError(SCOUT_SANDBOX_ERROR)


async def _fake_fetch_pr_diff(
    *, owner: str, repo: str, pr_number: int, token: str, timeout: float = 30.0
) -> str | None:
    import httpx2
    from e2e_env import FAKE_GITHUB_API

    async with httpx2.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{FAKE_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        )
    if response.status_code != 200:
        logger.warning(
            "Fake PR diff fetch failed",
            extra={"pr_number": pr_number, "status_code": response.status_code},
        )
        return None
    return response.text


async def _fake_assert_repo_access(full_name: str, token: str) -> str:
    import httpx2
    from e2e_env import FAKE_GITHUB_API

    from openswe.dashboard import repo_access

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
    import sandbox_service

    return sandbox_service.open_box()


async def _stand_in_sandbox(
    sandbox_id: str | None = None, *_args: object, **_kwargs: object
) -> object:
    import sandbox_service

    return sandbox_service.open_box(sandbox_id)


async def _configure_no_proxy(*_args: object, **_kwargs: object) -> None:
    return None


async def _sandbox_github_access(*_args: object, **_kwargs: object) -> object:
    from openswe.github.sandbox_access import SandboxGitHubAccess

    return SandboxGitHubAccess("dummy-installation-token")


async def _release_nothing(_sandbox_id: str) -> None:
    return None


async def _delete_nothing(_browser: object) -> None:
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
