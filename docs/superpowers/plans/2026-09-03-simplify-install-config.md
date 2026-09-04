# Simplify Open SWE Installation and Configuration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitHub + Slack the minimum supported install with seven env vars, auto-discover everything else that can be discovered, and rewrite the install docs around that happy path.

**Architecture:** Discovery lives next to the credential it depends on (`agent/auth/github_app.py` for the GitHub App, `agent/utils/slack.py` + `agent/webhooks/common.py` for Slack, `agent/utils/langsmith.py` for LangSmith). Each resolver applies the same precedence: explicit env override → context-based lookup → single-candidate auto-selection → `None`. Module-level constants that tests already monkeypatch stay as module attributes; discovery fills them in when empty. A new `agent/utils/startup_config.py` reports the effective configuration and deprecations at boot. A new `scripts/setup_env.py` is the guided installer that generates the two local secrets.

**Tech Stack:** Python 3.11+, httpx, PyJWT, cryptography (Fernet), langsmith SDK, FastAPI, pytest (`asyncio_mode = "auto"`), ruff, basedpyright. Dashboard UI is TypeScript (one copy change).

## Global Constraints

- Repo: `/Users/mukil/langchain/open-swe` (public GitHub repo `langchain-ai/open-swe`). **No `Co-Authored-By` trailer on commits.**
- Never log, print, or assert on real secret values. Tests use fake tokens (`"xoxb-test"`, `"key"`).
- `agent/` is a namespace package (no `__init__.py`). Run tests with `uv run pytest` from the repo root; `make install` first if `pytest` is missing.
- Line length 100; `ruff` + `basedpyright` (`typeCheckingMode = "standard"`) must pass: `make lint`, `make typecheck`.
- Minimal code comments; no task narration in comments or docstrings.
- Deprecated env vars remain functional as explicit overrides for one transition period. Never remove a read in this plan; only re-order precedence and add discovery.
- Do not add new configuration. The only new env var is none; the new Make target is `make setup`.

---

## Variable audit (runtime usage, verified 2026-09-03)

| Variable | Read at | Runtime role | Disposition |
|---|---|---|---|
| `LANGSMITH_API_KEY_PROD` | `auth/resolve.py:47`, `utils/langsmith.py:62`, `utils/gateway.py:49`, `utils/reviewer_outcomes.py:67`, `integrations/langsmith.py:59`, `dashboard/thread_api.py:141`, scripts | Sandboxes, trace lookups, gateway auth, bot-token mode | **Required (minimum)** |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … | `utils/model.py:347-374` (validated only when `DASHBOARD_BASE_URL` is localhost) | Model provider | **Required unless** `LANGSMITH_GATEWAY_ENABLED=true` |
| `GITHUB_APP_CLIENT_ID` | `dashboard/oauth.py:73`, `dashboard/routes.py:461` | Dashboard login only today | **Required (minimum)**; becomes JWT `iss` (Task 1) |
| `GITHUB_APP_ID` | `auth/github_app.py:18` (`iss` claim + guards), `auth/resolve.py:463` (message) | JWT issuer | **Deprecated** → fallback issuer only |
| `GITHUB_APP_PRIVATE_KEY` | `auth/github_app.py:19` | JWT signing | **Required (minimum)** |
| `GITHUB_APP_INSTALLATION_ID` | `auth/github_app.py:20,178` | Default installation for every token mint | **Override**; auto-discovered (Task 1) |
| `GITHUB_WEBHOOK_SECRET` | `webhooks/common.py:324` | Webhook HMAC | **Required (minimum)** |
| `SLACK_BOT_TOKEN` | `utils/slack.py:36`, `utils/multimodal.py:77` | Slack API auth | **Required (minimum)** |
| `SLACK_SIGNING_SECRET` | `webhooks/common.py:325` | Webhook HMAC | **Required (minimum)** |
| `SLACK_BOT_USER_ID` / `SLACK_BOT_USERNAME` | `webhooks/common.py:326-327`; read via `common.*` in `slack_routes.py`, `webhooks/slack.py` | Mention detection, self-message filtering | **Override**; auto-discovered via `auth.test` + `users.info` (Task 2) |
| `LANGSMITH_TENANT_ID_PROD` | `utils/langsmith.py:98`, `review/trace_context.py:473` | Trace-link URL `/o/<tenant>` | **Override**; discovered from project metadata (Task 3) |
| `LANGSMITH_TRACING_PROJECT_ID_PROD` | `utils/langsmith.py:103,151` | Fallback when project name lookup fails | **Deprecated** override (redundant: graphs trace by name) |
| `LANGCHAIN_PROJECT` | not read by Open SWE | SDK default project for untagged traces; graphs pin their own | **Docs-only removal** |
| `LANGSMITH_URL_PROD` | `utils/langsmith.py:101`, `review/trace_context.py:475` | Web host for links | **Optional**; derived from API endpoint (Task 3) |
| `DEFAULT_SANDBOX_SNAPSHOT_ID` | `integrations/langsmith.py:118,717`, `dashboard/sandbox_settings.py:53` | Base snapshot | **Optional**; platform default snapshot when unset (Task 5) |
| `DEFAULT_SANDBOX_{FS_CAPACITY_BYTES,VCPUS,MEM_BYTES,IDLE_TTL_SECONDS,DELETE_AFTER_STOP_SECONDS}` | `integrations/langsmith.py:118-131` | Sizing/TTL, code defaults exist | **Docs-only** (move to optional) |
| `SANDBOX_TYPE` | 11 sites, default `"langsmith"` | Provider | **Docs-only** |
| `REVIEWER_OUTCOMES_DATASET` | `utils/reviewer_outcomes.py:27`, default `openswe-reviewer-outcomes` | Dataset name | **Docs-only** |
| `ENVIRONMENT_SNAPSHOT_PREFIX` | `dashboard/environments.py:116`, default `openswe` | Snapshot naming | **Docs-only** |
| `DEFAULT_REPO_OWNER` / `DEFAULT_REPO_NAME` | `webhooks/common.py:328-329,379,818-819`, `utils/repo.py:6`, `dashboard/team_settings.py:257-258` (seeds `default_repo`) | Fallback repo; owner default hardcoded to `langchain-ai` | **Deprecated** seed; team setting is canonical (Task 6) |
| `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` | `webhooks/common.py:330-331,818-819` | Slack-only fallback repo | **Deprecated** duplicate (Task 6) |
| `DASHBOARD_BASE_URL` | `dashboard/oauth.py:92,133,360`, `routes.py:357`, `utils/dashboard_links.py:11`, `utils/model.py:355`, `notion_oauth.py:145` | Frontend origin | Optional (dashboard add-on) |
| `DASHBOARD_ALLOWED_ORIGINS` | `api/app.py:43` (CORS only), `dashboard/oauth.py:95` (CSRF/redirect, already unioned with base URL) | Extra origins | **Only for additional origins** (Task 4) |
| `DASHBOARD_JWT_SECRET` | `dashboard/oauth.py:78` | Session/state HMAC | Dashboard add-on; generated by installer (Task 8) |
| `TOKEN_ENCRYPTION_KEY` | `encryption.py:34` | Fernet for stored OAuth tokens | Dashboard add-on; generated by installer (Task 8) |

## Staging

**A. Documentation-only cleanup** — `LANGCHAIN_PROJECT`, `LANGSMITH_URL_PROD`, `SANDBOX_TYPE`, sandbox sizing/TTL, `REVIEWER_OUTCOMES_DATASET`, `ENVIRONMENT_SNAPSHOT_PREFIX`; happy-path restructure (Task 9).

**B. Safe runtime defaulting or discovery** — Tasks 1 (installation id, client-id issuer), 2 (Slack identity), 3 (tenant/host), 4 (CORS from base URL), 5 (platform default snapshot), 7 (startup report).

**C. Backward-compatible deprecations** — `GITHUB_APP_ID`, `SLACK_REPO_*`, `DEFAULT_REPO_*`, `LANGSMITH_TRACING_PROJECT_ID_PROD` keep working as overrides and warn once at startup (Tasks 1, 6, 7). Hardcoded `langchain-ai` owner default removed (Task 6).

**D. Architectural changes requiring migration (NOT in this plan)** — (1) deleting the deprecated env reads after the transition; (2) persisting the env-seeded `default_repo` into the LangGraph Store at startup so the env vars can be removed without losing the value (needs the Store reachable during lifespan, which is not guaranteed inside `langgraph dev`); (3) persisting the discovered installation id / Slack identity across processes (in-process caches are sufficient today).

---

### Task 1: GitHub App — client ID as JWT issuer, installation auto-discovery

**Files:**
- Modify: `agent/auth/github_app.py`
- Modify: `agent/auth/resolve.py:458-464` (error message)
- Modify: `agent/webhooks/common.py:1177-1188` (`_reviewer_token_for_repo` passes repo context)
- Test: `tests/github/test_github_app.py` (append), Create: `tests/github/test_github_app_discovery.py`

**Interfaces:**
- Produces: `GITHUB_APP_CLIENT_ID: str` (module attr), `_app_jwt_issuer() -> str`, `_app_credentials_configured() -> bool`, `async list_app_installations() -> list[dict[str, Any]]`, `async resolve_default_installation_id(*, owner: str | None = None, repo: str | None = None) -> str | None`, `clear_app_token_cache()` (now also clears discovery caches). `get_github_app_installation_token(_with_expiry)` gain `owner: str | None = None, repo: str | None = None` kwargs.

- [ ] **Step 1: Write failing tests** in `tests/github/test_github_app_discovery.py`:

```python
from typing import Any

import pytest

from agent.auth import github_app


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Any:
    github_app.clear_app_token_cache()
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "")
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setattr(github_app, "GITHUB_APP_PRIVATE_KEY", "key")
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "")
    monkeypatch.setattr(github_app, "_generate_app_jwt", lambda: "jwt")
    yield
    github_app.clear_app_token_cache()


class _Response:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self) -> Any:
        return self._payload


def _client_factory(routes: dict[str, Any]) -> type:
    class Client:
        gets: list[str] = []
        posts: list[str] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            type(self).gets.append(url)
            path = url.split("api.github.com", 1)[1].split("?", 1)[0]
            payload = routes.get(path)
            return _Response(
                payload if payload is not None else {}, 200 if payload is not None else 404
            )

        async def post(self, url: str, **kwargs: Any) -> _Response:
            type(self).posts.append(url)
            return _Response({"token": "tok", "expires_at": "2099-01-01T00:00:00Z"})

    return Client


def test_issuer_prefers_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "12345")
    assert github_app._app_jwt_issuer() == "Iv1.client"


def test_issuer_falls_back_to_app_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "")
    monkeypatch.setattr(github_app, "GITHUB_APP_ID", "12345")
    assert github_app._app_jwt_issuer() == "12345"
    assert github_app._app_credentials_configured()


def test_not_configured_without_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_CLIENT_ID", "")
    assert not github_app._app_credentials_configured()


async def test_env_installation_id_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "77")
    client = _client_factory({})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id() == "77"
    assert client.gets == []


async def test_single_installation_is_used_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory(
        {"/app/installations": [{"id": 42, "account": {"login": "acme", "type": "Organization"}}]}
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id() == "42"
    assert await github_app.resolve_default_installation_id() == "42"
    assert client.gets == ["https://api.github.com/app/installations?per_page=100&page=1"]


async def test_multiple_installations_do_not_auto_select(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory(
        {
            "/app/installations": [
                {"id": 1, "account": {"login": "a"}},
                {"id": 2, "account": {"login": "b"}},
            ]
        }
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id() is None
    token, _ = await github_app.get_github_app_installation_token_with_expiry()
    assert token is None
    assert client.posts == []


async def test_zero_installations_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory({"/app/installations": []})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id() is None


async def test_repo_context_wins_over_single_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_factory(
        {
            "/app/installations": [{"id": 1, "account": {"login": "a"}}],
            "/repos/acme/widgets/installation": {"id": 9},
        }
    )
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id(owner="acme", repo="widgets") == "9"
    token, _ = await github_app.get_github_app_installation_token_with_expiry(
        owner="acme", repo="widgets"
    )
    assert token == "tok"
    assert client.posts == ["https://api.github.com/app/installations/9/access_tokens"]


async def test_env_override_beats_repo_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app, "GITHUB_APP_INSTALLATION_ID", "5")
    client = _client_factory({"/repos/acme/widgets/installation": {"id": 9}})
    monkeypatch.setattr(github_app.httpx, "AsyncClient", client)
    assert await github_app.resolve_default_installation_id(owner="acme", repo="widgets") == "5"
    assert client.gets == []
```

- [ ] **Step 2: Run** `uv run pytest tests/github/test_github_app_discovery.py -q` → FAIL (`AttributeError: GITHUB_APP_CLIENT_ID`).

- [ ] **Step 3: Implement** in `agent/auth/github_app.py`:

```python
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID", "")
GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")

GITHUB_API_BASE_URL = "https://api.github.com"
_INSTALLATION_CACHE_TTL = timedelta(hours=1)
_DISCOVERY_RETRY_INTERVAL = timedelta(minutes=5)
_INSTALLATIONS_PAGE_SIZE = 100
_INSTALLATIONS_MAX_PAGES = 10

# (owner, repo or "") -> (installation id, cached_at)
_INSTALLATION_ID_CACHE: dict[tuple[str, str], tuple[str, datetime]] = {}
# Result of the app-wide single-installation lookup: (id or None, checked_at)
_SINGLE_INSTALLATION: tuple[str | None, datetime] | None = None


def _app_jwt_issuer() -> str:
    """GitHub accepts either the client ID or the numeric app ID as ``iss``; the
    client ID is what GitHub recommends and what the dashboard already needs."""
    return GITHUB_APP_CLIENT_ID or GITHUB_APP_ID


def _app_credentials_configured() -> bool:
    return bool(_app_jwt_issuer() and GITHUB_APP_PRIVATE_KEY)


def _app_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_generate_app_jwt()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
```

`_generate_app_jwt` uses `"iss": _app_jwt_issuer()`. Replace every `not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY` guard with `not _app_credentials_configured()`. Add:

```python
async def list_app_installations() -> list[dict[str, Any]]:
    """Every installation of this GitHub App, across all accounts."""
    if not _app_credentials_configured():
        return []
    installations: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        for page in range(1, _INSTALLATIONS_MAX_PAGES + 1):
            response = await client.get(
                f"{GITHUB_API_BASE_URL}/app/installations"
                f"?per_page={_INSTALLATIONS_PAGE_SIZE}&page={page}",
                headers=_app_headers(),
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                break
            installations.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _INSTALLATIONS_PAGE_SIZE:
                break
    return installations


async def _discover_single_installation() -> str | None:
    global _SINGLE_INSTALLATION
    now = datetime.now(UTC)
    if _SINGLE_INSTALLATION is not None:
        cached_id, checked_at = _SINGLE_INSTALLATION
        if cached_id is not None or now - checked_at < _DISCOVERY_RETRY_INTERVAL:
            return cached_id
    try:
        installations = await list_app_installations()
    except Exception:
        logger.warning("Failed to list GitHub App installations", exc_info=True)
        _SINGLE_INSTALLATION = (None, now)
        return None
    ids = [str(i["id"]) for i in installations if isinstance(i.get("id"), int) and i["id"] > 0]
    if len(ids) == 1:
        logger.info("Using the GitHub App's only installation (id %s)", ids[0])
        _SINGLE_INSTALLATION = (ids[0], now)
        return ids[0]
    if not ids:
        logger.warning("The GitHub App is not installed on any account yet")
    else:
        accounts = ", ".join(str((i.get("account") or {}).get("login", "?")) for i in installations)
        logger.warning(
            "The GitHub App has %d installations (%s); set GITHUB_APP_INSTALLATION_ID or "
            "rely on repository context to choose one",
            len(ids),
            accounts,
        )
    _SINGLE_INSTALLATION = (None, now)
    return None


async def _cached_context_installation(owner: str, repo: str | None) -> str | None:
    key = (owner.strip().lower(), (repo or "").strip().lower())
    now = datetime.now(UTC)
    cached = _INSTALLATION_ID_CACHE.get(key)
    if cached is not None and now - cached[1] < _INSTALLATION_CACHE_TTL:
        return cached[0]
    resolved = (
        await get_github_app_installation_id_for_repo(owner, repo)
        if repo
        else await get_github_app_installation_id_for_org(owner)
    )
    if resolved is None:
        return None
    _INSTALLATION_ID_CACHE[key] = (str(resolved), now)
    return str(resolved)


async def resolve_default_installation_id(
    *, owner: str | None = None, repo: str | None = None
) -> str | None:
    """Installation to mint tokens under when the caller didn't name one.

    Precedence: ``GITHUB_APP_INSTALLATION_ID`` → the installation that owns
    ``owner/repo`` (or ``owner``) → the app's only installation → ``None``.
    """
    env_id = GITHUB_APP_INSTALLATION_ID.strip()
    if env_id:
        return env_id
    if not _app_credentials_configured():
        return None
    if owner and owner.strip():
        contextual = await _cached_context_installation(owner, repo)
        if contextual:
            return contextual
    return await _discover_single_installation()
```

In `clear_app_token_cache()` also `_INSTALLATION_ID_CACHE.clear()` and set `_SINGLE_INSTALLATION = None` (declare `global`). In `get_github_app_installation_token_with_expiry` add kwargs `owner: str | None = None, repo: str | None = None` and replace the `resolved_installation_id` computation with:

```python
    if installation_id is None:
        resolved = await resolve_default_installation_id(owner=owner, repo=repo)
    else:
        resolved = str(installation_id)
    resolved_installation_id = (resolved or "").strip()
    if (
        not _app_credentials_configured()
        or not resolved_installation_id.isdigit()
        or int(resolved_installation_id) <= 0
    ):
        logger.debug("GitHub App not fully configured, skipping app token")
        return None, None
```

Pass the two kwargs through `get_github_app_installation_token`. Use `_app_headers()` in the three existing request sites.

- [ ] **Step 4:** `agent/auth/resolve.py` message → `"Set GITHUB_APP_CLIENT_ID and GITHUB_APP_PRIVATE_KEY (and GITHUB_APP_INSTALLATION_ID when the app has more than one installation)."`. In `agent/webhooks/common.py:_reviewer_token_for_repo`, pass `owner=repo_config.get("owner"), repo=repo_config.get("name")` on all three mint calls.

- [ ] **Step 5: Run** `uv run pytest tests/github -q` → PASS (existing tests set `GITHUB_APP_ID="1"` and still work via the fallback issuer).

- [ ] **Step 6: Commit** `feat(auth): use GitHub App client ID as JWT issuer and auto-discover the installation`.

---

### Task 2: Slack bot identity discovery

**Files:**
- Modify: `agent/utils/slack.py` (add `SlackBotIdentity`, `fetch_slack_bot_identity`)
- Modify: `agent/webhooks/common.py` (add `ensure_slack_bot_identity`, export)
- Modify: `agent/webhooks/slack_routes.py` (call `ensure_slack_bot_identity()` at the top of the events and interactivity handlers)
- Create: `tests/slack/test_slack_bot_identity.py`

**Interfaces:**
- Produces: `SlackBotIdentity(user_id: str, username: str, team_id: str | None)`, `async fetch_slack_bot_identity(token: str | None = None) -> SlackBotIdentity | None`, `async common.ensure_slack_bot_identity() -> None` (fills `common.SLACK_BOT_USER_ID` / `common.SLACK_BOT_USERNAME` when empty; throttles retries to once per 60 s; never raises).

- [ ] **Step 1: Failing tests:**

```python
from typing import Any

import pytest

from agent.utils import slack as slack_utils
from agent.webhooks import common


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


def _client(routes: dict[str, dict[str, Any]]) -> type:
    class Client:
        calls: list[str] = []

        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> _Response:
            method = url.rsplit("/", 1)[1]
            type(self).calls.append(method)
            return _Response(routes.get(method, {"ok": False, "error": "unknown_method"}))

        async def get(self, url: str, **kwargs: Any) -> _Response:
            method = url.rsplit("/", 1)[1].split("?", 1)[0]
            type(self).calls.append(method)
            return _Response(routes.get(method, {"ok": False, "error": "unknown_method"}))

    return Client


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "")
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "")
    monkeypatch.setattr(common, "_SLACK_IDENTITY_ATTEMPTED_AT", None)
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")


async def test_fetch_identity_uses_auth_test_and_users_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        {
            "auth.test": {"ok": True, "user_id": "UBOT", "user": "bot", "team_id": "T1"},
            "users.info": {
                "ok": True,
                "user": {"name": "open-swe", "profile": {"display_name": "Open SWE"}},
            },
        }
    )
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)
    identity = await slack_utils.fetch_slack_bot_identity()
    assert identity == slack_utils.SlackBotIdentity(
        user_id="UBOT", username="open-swe", team_id="T1"
    )


async def test_fetch_identity_falls_back_to_auth_test_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        {"auth.test": {"ok": True, "user_id": "UBOT", "user": "openswe-bot", "team_id": "T1"}}
    )
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)
    identity = await slack_utils.fetch_slack_bot_identity()
    assert identity is not None and identity.username == "openswe-bot"


async def test_fetch_identity_returns_none_on_slack_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client({"auth.test": {"ok": False, "error": "invalid_auth"}})
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)
    assert await slack_utils.fetch_slack_bot_identity() is None


async def test_fetch_identity_without_token_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")
    client = _client({})
    monkeypatch.setattr(slack_utils.httpx, "AsyncClient", client)
    assert await slack_utils.fetch_slack_bot_identity() is None
    assert client.calls == []


async def test_ensure_fills_empty_module_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake() -> slack_utils.SlackBotIdentity:
        return slack_utils.SlackBotIdentity("UBOT", "open-swe", "T1")

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)
    await common.ensure_slack_bot_identity()
    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UBOT", "open-swe")


async def test_ensure_respects_explicit_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "SLACK_BOT_USER_ID", "UENV")
    monkeypatch.setattr(common, "SLACK_BOT_USERNAME", "env-name")
    called = False

    async def fake() -> None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)
    await common.ensure_slack_bot_identity()
    assert not called
    assert (common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME) == ("UENV", "env-name")


async def test_ensure_throttles_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake() -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(common, "fetch_slack_bot_identity", fake)
    await common.ensure_slack_bot_identity()
    await common.ensure_slack_bot_identity()
    assert calls == 1
```

- [ ] **Step 2: Run** → FAIL (`AttributeError: SlackBotIdentity`).

- [ ] **Step 3: Implement** in `agent/utils/slack.py`:

```python
@dataclass(frozen=True)
class SlackBotIdentity:
    user_id: str
    username: str
    team_id: str | None


async def fetch_slack_bot_identity(token: str | None = None) -> SlackBotIdentity | None:
    """Resolve the bot user behind a bot token via ``auth.test`` and ``users.info``."""
    bot_token = token or SLACK_BOT_TOKEN
    if not bot_token:
        return None
    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            auth = await client.post(f"{SLACK_API_BASE_URL}/auth.test", headers=headers)
            auth_payload = auth.json()
            if not isinstance(auth_payload, dict) or not auth_payload.get("ok"):
                logger.warning(
                    "Slack auth.test failed: %s",
                    auth_payload.get("error") if isinstance(auth_payload, dict) else "bad response",
                )
                return None
            user_id = auth_payload.get("user_id")
            if not isinstance(user_id, str) or not user_id:
                return None
            username = auth_payload.get("user") if isinstance(auth_payload.get("user"), str) else ""
            team_id = (
                auth_payload.get("team_id")
                if isinstance(auth_payload.get("team_id"), str)
                else None
            )
            info = await client.get(
                f"{SLACK_API_BASE_URL}/users.info", headers=headers, params={"user": user_id}
            )
            info_payload = info.json()
            if isinstance(info_payload, dict) and info_payload.get("ok"):
                user = info_payload.get("user") or {}
                username = (
                    user.get("name") or (user.get("profile") or {}).get("display_name") or username
                )
    except Exception:
        logger.warning("Could not resolve the Slack bot identity", exc_info=True)
        return None
    return SlackBotIdentity(user_id=user_id, username=str(username or ""), team_id=team_id)
```

In `agent/webhooks/common.py` (import `fetch_slack_bot_identity` from `..utils.slack`; add `"ensure_slack_bot_identity"` to `__all__`):

```python
_SLACK_IDENTITY_RETRY_SECONDS = 60.0
_SLACK_IDENTITY_ATTEMPTED_AT: float | None = None


async def ensure_slack_bot_identity() -> None:
    """Fill ``SLACK_BOT_USER_ID`` / ``SLACK_BOT_USERNAME`` from the bot token when unset."""
    global SLACK_BOT_USER_ID, SLACK_BOT_USERNAME, _SLACK_IDENTITY_ATTEMPTED_AT
    if SLACK_BOT_USER_ID and SLACK_BOT_USERNAME:
        return
    now = time.monotonic()
    if (
        _SLACK_IDENTITY_ATTEMPTED_AT is not None
        and now - _SLACK_IDENTITY_ATTEMPTED_AT < _SLACK_IDENTITY_RETRY_SECONDS
    ):
        return
    _SLACK_IDENTITY_ATTEMPTED_AT = now
    identity = await fetch_slack_bot_identity()
    if identity is None:
        return
    SLACK_BOT_USER_ID = SLACK_BOT_USER_ID or identity.user_id
    SLACK_BOT_USERNAME = SLACK_BOT_USERNAME or identity.username
    logger.info(
        "Resolved Slack bot identity: user_id=%s username=%s", SLACK_BOT_USER_ID, SLACK_BOT_USERNAME
    )
```

In `slack_routes.py`, add `await common.ensure_slack_bot_identity()` as the first statement of the events POST handler and the interactivity POST handler (after signature verification is fine too; before any read of `common.SLACK_BOT_USER_ID`).

- [ ] **Step 4: Run** `uv run pytest tests/slack -q` → PASS. **Step 5: Commit** `feat(slack): discover the bot user id and username from the bot token`.

---

### Task 3: LangSmith tenant and host discovery

**Files:**
- Modify: `agent/utils/langsmith.py`
- Modify: `agent/review/trace_context.py:470-477`
- Test: `tests/agent/test_langsmith_trace_url.py` (append)

**Interfaces:**
- Produces: `async resolve_tenant_id() -> str | None`, `langsmith_host_url() -> str`, `_TENANT_ID_CACHE: str | None`, `_discover_tenant_id() -> str | None` (sync, run in thread).

- [ ] **Step 1: Failing tests** (append):

```python
async def test_tenant_id_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TENANT_ID_PROD", "tenant-env")
    ls_utils._TENANT_ID_CACHE = "tenant-cached"
    assert await ls_utils.resolve_tenant_id() == "tenant-env"


async def test_tenant_id_is_learned_from_project_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)
    ls_utils._TENANT_ID_CACHE = None

    class _Project:
        id = "pid"
        tenant_id = "tenant-from-project"

    class _Client:
        async def read_project(self, *, project_name: str) -> _Project:
            return _Project()

    monkeypatch.setattr(ls_utils, "_build_prod_langsmith_client", lambda: _Client())
    assert await ls_utils._resolve_project_id_by_name("open-swe-agent") == "pid"
    assert await ls_utils.resolve_tenant_id() == "tenant-from-project"


async def test_tenant_id_falls_back_to_listing_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TENANT_ID_PROD", raising=False)
    ls_utils._TENANT_ID_CACHE = None
    monkeypatch.setattr(ls_utils, "_discover_tenant_id", lambda: "tenant-listed")
    assert await ls_utils.resolve_tenant_id() == "tenant-listed"
    assert ls_utils._TENANT_ID_CACHE == "tenant-listed"


async def test_tenant_id_none_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGSMITH_TENANT_ID_PROD",
        "LANGSMITH_API_KEY_PROD",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    ls_utils._TENANT_ID_CACHE = None
    assert await ls_utils.resolve_tenant_id() is None


def test_host_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_URL_PROD", "https://smith.example/")
    assert ls_utils.langsmith_host_url() == "https://smith.example"


def test_host_url_derived_from_self_hosted_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_URL_PROD", raising=False)
    monkeypatch.delenv("LANGSMITH_ENDPOINT_PROD", raising=False)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://langsmith.acme.internal/api")
    assert ls_utils.langsmith_host_url() == "https://langsmith.acme.internal"


def test_host_url_default_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LANGSMITH_URL_PROD", "LANGSMITH_ENDPOINT_PROD", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    assert ls_utils.langsmith_host_url() == "https://smith.langchain.com"
```

Update the existing `_set_env` fixture and `_clear_cache` fixture to also reset `ls_utils._TENANT_ID_CACHE = None`; the existing "none when unset" test must monkeypatch `ls_utils._discover_tenant_id` to `lambda: None` so a developer's shell key cannot make it flaky.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**:

```python
from langsmith.utils import get_host_url

_TENANT_ID_CACHE: str | None = None


def _prod_api_url() -> str:
    return os.environ.get("LANGSMITH_ENDPOINT_PROD") or os.environ.get(
        "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
    )


def langsmith_host_url() -> str:
    """Web host for trace links: ``LANGSMITH_URL_PROD`` or derived from the API endpoint."""
    explicit = os.environ.get("LANGSMITH_URL_PROD", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return str(get_host_url(None, _prod_api_url())).rstrip("/")


def _remember_tenant_id(value: Any) -> None:
    global _TENANT_ID_CACHE
    if value and _TENANT_ID_CACHE is None:
        _TENANT_ID_CACHE = str(value)


def _discover_tenant_id() -> str | None:
    """Any project in the workspace carries the tenant id; read the first one."""
    api_key = (
        os.environ.get("LANGSMITH_API_KEY_PROD")
        or os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
    )
    if not api_key:
        return None
    client = sync_langsmith_client(api_key, _prod_api_url())
    for project in client.list_projects(limit=1):
        tenant_id = getattr(project, "tenant_id", None)
        if tenant_id:
            return str(tenant_id)
    return None


async def resolve_tenant_id() -> str | None:
    """``LANGSMITH_TENANT_ID_PROD`` when set; otherwise discovered once and cached."""
    global _TENANT_ID_CACHE
    explicit = os.environ.get("LANGSMITH_TENANT_ID_PROD", "").strip()
    if explicit:
        return explicit
    if _TENANT_ID_CACHE:
        return _TENANT_ID_CACHE
    try:
        discovered = await asyncio.to_thread(_discover_tenant_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not discover the LangSmith tenant id", exc_info=True)
        return None
    _remember_tenant_id(discovered)
    return _TENANT_ID_CACHE
```

In `_resolve_project_id_by_name`, after a successful `read_project`, call `_remember_tenant_id(getattr(project, "tenant_id", None))`. `_build_prod_langsmith_client` uses `_prod_api_url()`. `_compose_langsmith_project_url` becomes:

```python
    tenant_id = await resolve_tenant_id()
    if not tenant_id:
        return None
    project_id = await _resolve_project_id_by_name(project_name) or os.environ.get(
        "LANGSMITH_TRACING_PROJECT_ID_PROD"
    )
    if not project_id:
        return None
    return f"{langsmith_host_url()}/o/{tenant_id}/projects/p/{project_id}"
```

`agent/review/trace_context.py:_trace_url` → `tenant_id = await resolve_tenant_id()` and `host_url = langsmith_host_url()` (import both from `..utils.langsmith`; drop `os` there if unused).

- [ ] **Step 4: Run** `uv run pytest tests/agent/test_langsmith_trace_url.py tests/reviewer/test_reviewer_trace_context.py -q` → PASS. **Step 5: Commit** `feat(langsmith): discover the tenant id and derive the web host for trace links`.

---

### Task 4: CORS allowlist derived from `DASHBOARD_BASE_URL`

**Files:** Modify `agent/api/app.py`; Create `tests/dashboard/test_app_cors.py`.

- [ ] **Step 1: Failing tests:**

```python
import pytest
from fastapi.middleware.cors import CORSMiddleware

from agent.api import app as app_module


def _cors_origins(app) -> list[str] | None:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return sorted(middleware.kwargs["allow_origins"])
    return None


def test_cors_uses_dashboard_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example/")
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
    assert _cors_origins(app_module.create_app()) == ["https://dashboard.example"]


def test_cors_adds_extra_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv(
        "DASHBOARD_ALLOWED_ORIGINS", "https://preview.example, http://localhost:3000"
    )
    assert _cors_origins(app_module.create_app()) == [
        "http://localhost:3000",
        "https://preview.example",
    ]


def test_cors_disabled_without_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
    assert _cors_origins(app_module.create_app()) is None


def test_cors_rejects_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "*")
    with pytest.raises(RuntimeError):
        app_module.create_app()
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** in `create_app()`:

```python
raw_extra = os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "")
if "*" in (entry.strip() for entry in raw_extra.split(",")):
    raise RuntimeError("DASHBOARD_ALLOWED_ORIGINS must not include '*' when allow_credentials=True")
allowed_origins = sorted(allowed_dashboard_origins())
```

with `from ..dashboard.oauth import allowed_dashboard_origins`. Keep the rest of the middleware block unchanged.

- [ ] **Step 4: Run** `uv run pytest tests/dashboard/test_app_cors.py tests/dashboard/test_dashboard_csrf.py -q` → PASS. **Step 5: Commit** `fix(dashboard): allow the dashboard origin for CORS without DASHBOARD_ALLOWED_ORIGINS`.

---

### Task 5: Boot from the LangSmith default snapshot when none is configured

**Files:**
- Modify: `agent/integrations/langsmith.py` (`_create_sandbox_with_retry`, `LangSmithProvider.get_or_create`, `validate_startup_config`)
- Modify: `ui/src/components/SandboxSettingsPanel.tsx` (copy for the `unset` state)
- Test: `tests/sandbox/test_langsmith_sandbox_config.py` (append)

- [ ] **Step 1: Failing tests:** one asserting `LangSmithProvider.validate_startup_config()` does not raise and logs at INFO (use `caplog`) when `DEFAULT_SANDBOX_SNAPSHOT_ID` is unset; one driving `LangSmithProvider(api_key="k").get_or_create(snapshot_id=None)` with a fake `AsyncSandboxClient` (monkeypatch `langsmith_integration.AsyncSandboxClient`) and asserting `create_sandbox` was called with `snapshot_id=None`. Follow the fake-client shape already used in that file.

- [ ] **Step 2: Implement:** `_create_sandbox_with_retry(..., snapshot_id: str | None, ...)`; in `get_or_create` replace the `if not snapshot_id: raise ValueError(...)` block with a one-time `logger.info("No base snapshot configured; booting from the LangSmith default snapshot")` guarded by a module flag `_DEFAULT_SNAPSHOT_NOTICE_LOGGED`. `validate_startup_config` logs INFO: `"DEFAULT_SANDBOX_SNAPSHOT_ID is not set; sandboxes boot from the LangSmith default snapshot (git, gh, Python, Node preinstalled). Set it or the admin Base snapshot to use a custom image."`. Update the `unset` copy in `SandboxSettingsPanel.tsx` to say sandboxes use the LangSmith default snapshot (check `ui/src/components/*.test.tsx` for asserted text and update it).

- [ ] **Step 3: Run** `uv run pytest tests/sandbox -q` and `pnpm --filter open-swe-dashboard run typecheck` → PASS. **Step 4: Commit** `feat(sandbox): fall back to the LangSmith default snapshot`.

---

### Task 6: Team-settings default repo is canonical; retire Slack-specific and hardcoded defaults

**Files:**
- Modify: `agent/utils/repo.py:6` (default owner `""`)
- Modify: `agent/webhooks/common.py:328-331, 800-890` (default owner from team setting; keep env fallbacks last)
- Modify: `agent/webhooks/linear_routes.py:82-84`
- Test: `tests/slack/test_slack_context.py` (append), `tests/webhooks/test_linear_default_owner.py` (create)

- [ ] **Step 1: Failing tests:** (a) `extract_repo_from_text("repo:widgets")` with `DEFAULT_REPO_OWNER` unset returns `None`; (b) `get_slack_repo_config` with all env repo vars empty and `get_team_default_repo` returning `{"owner": "acme", "name": "widgets"}` resolves `repo:tools` in the channel topic to `acme/tools`; (c) Linear route uses the team default owner for `repo:tools` shorthand (monkeypatch `common.get_team_default_repo`).

- [ ] **Step 2: Implement:** In `common.py` set `DEFAULT_REPO_OWNER = os.environ.get("DEFAULT_REPO_OWNER", "")`. In `get_slack_repo_config`, before the channel-description step:

```python
team_repo_config = await get_team_default_repo()
default_owner = (
    SLACK_REPO_OWNER.strip() or DEFAULT_REPO_OWNER or (team_repo_config or {}).get("owner", "")
)
default_name = SLACK_REPO_NAME.strip() or DEFAULT_REPO_NAME
```

and later `repo_config = team_repo_config` instead of a second lookup. In `linear_routes.py`:

```python
    default_owner = common.DEFAULT_REPO_OWNER or (
        (await common.get_team_default_repo() or {}).get("owner", "")
    )
    repo_config = common.extract_repo_from_text(comment_body, default_owner=default_owner)
```

`utils/repo.py`: `_DEFAULT_REPO_OWNER = os.environ.get("DEFAULT_REPO_OWNER", "")`; when `default_owner` is empty and the shorthand has no slash, leave `owner` unset so the function falls through to URL extraction.

- [ ] **Step 3: Run** `uv run pytest tests/slack tests/webhooks tests/github/test_github_issue_webhook.py -q` → PASS. **Step 4: Commit** `refactor(repo): make the team default repository canonical`.

---

### Task 7: Startup configuration report and deprecation warnings

**Files:** Create `agent/utils/startup_config.py`; Modify `agent/api/app.py` (lifespan); Create `tests/utils/test_startup_config.py`.

**Interfaces:** `deprecated_env_warnings(env: Mapping[str, str]) -> list[str]`, `configuration_summary(env: Mapping[str, str]) -> list[str]`, `log_startup_configuration() -> None`.

- [ ] **Step 1: Failing tests:** `deprecated_env_warnings({"GITHUB_APP_ID": "1", "GITHUB_APP_CLIENT_ID": "Iv1"})` mentions `GITHUB_APP_ID`; `{"SLACK_REPO_OWNER": "x"}` mentions team settings; `{"LANGSMITH_TRACING_PROJECT_ID_PROD": "p"}` and `{"LANGCHAIN_PROJECT": "x"}` each produce one warning; empty env → `[]`. `configuration_summary` with the seven minimum vars set → lines containing `GitHub: enabled`, `Slack: enabled`, `Dashboard: disabled`; missing `GITHUB_WEBHOOK_SECRET` → `GitHub: missing GITHUB_WEBHOOK_SECRET`. No secret values appear in any returned string (assert each value like `"xoxb-secret"` is absent).

- [ ] **Step 2: Implement:**

```python
"""Startup report of the effective configuration and deprecated settings."""

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

_DEPRECATIONS: tuple[tuple[str, str], ...] = (
    (
        "GITHUB_APP_ID",
        "GITHUB_APP_CLIENT_ID is the GitHub App JWT issuer; GITHUB_APP_ID is only used when it is unset.",
    ),
    ("SLACK_REPO_OWNER", "set the default repository in Admin → Team settings instead."),
    ("SLACK_REPO_NAME", "set the default repository in Admin → Team settings instead."),
    ("DEFAULT_REPO_OWNER", "set the default repository in Admin → Team settings instead."),
    ("DEFAULT_REPO_NAME", "set the default repository in Admin → Team settings instead."),
    ("LANGSMITH_TRACING_PROJECT_ID_PROD", "trace links resolve projects by name; remove it."),
    ("LANGCHAIN_PROJECT", "graphs pin their own tracing projects; it has no effect on Open SWE."),
)

_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LangSmith", ("LANGSMITH_API_KEY_PROD",)),
    ("GitHub", ("GITHUB_APP_CLIENT_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_WEBHOOK_SECRET")),
    ("Slack", ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")),
    ("Linear", ("LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET")),
    (
        "Dashboard",
        (
            "GITHUB_APP_CLIENT_SECRET",
            "DASHBOARD_JWT_SECRET",
            "TOKEN_ENCRYPTION_KEY",
            "DASHBOARD_BASE_URL",
            "DASHBOARD_API_BASE_URL",
        ),
    ),
)


def deprecated_env_warnings(env: Mapping[str, str]) -> list[str]:
    return [
        f"{name} is deprecated: {hint}" for name, hint in _DEPRECATIONS if env.get(name, "").strip()
    ]


def configuration_summary(env: Mapping[str, str]) -> list[str]:
    lines: list[str] = []
    for surface, names in _SURFACES:
        missing = [n for n in names if not env.get(n, "").strip()]
        if not missing:
            lines.append(f"{surface}: enabled")
        elif len(missing) == len(names):
            lines.append(f"{surface}: disabled")
        else:
            lines.append(f"{surface}: missing {', '.join(missing)}")
    return lines


def log_startup_configuration() -> None:
    for line in configuration_summary(os.environ):
        logger.info("config: %s", line)
    for warning in deprecated_env_warnings(os.environ):
        logger.warning("config: %s", warning)
```

Wire into `lifespan` after `validate_local_dev_llm_config()`: `log_startup_configuration()` then `await ensure_slack_bot_identity()` wrapped in `try/except Exception: logger.debug(...)`.

- [ ] **Step 3: Run** `uv run pytest tests/utils/test_startup_config.py -q` → PASS. **Step 4: Commit** `feat(config): log the effective configuration and deprecated settings at startup`.

---

### Task 8: Guided installer (`make setup`)

**Files:** Create `scripts/setup_env.py`; Modify `Makefile` (add `setup`); Create `tests/scripts/test_setup_env.py`.

**Interfaces:** `generate_dashboard_jwt_secret() -> str` (64 hex chars from `secrets.token_hex(32)`), `generate_token_encryption_key() -> str` (`Fernet.generate_key().decode()`), `generate_webhook_secret() -> str`, `parse_env(text: str) -> dict[str, str]`, `render_env(existing_text: str, updates: dict[str, str]) -> str` (replaces values in place, appends new keys under a `# Added by scripts/setup_env.py` line, preserves comments and unknown keys), `read_private_key(path: str) -> str` (returns single-line with literal `\n`), `collect_answers(ask, ask_secret, defaults) -> dict[str, str]` (pure, injectable prompts), `main(argv) -> int`.

- [ ] **Step 1: Failing tests:** distinct outputs across two generations of each secret; Fernet key round-trips through `cryptography.fernet.Fernet`; JWT secret is 64 hex chars; `render_env` keeps an unrelated `FOO=bar` line and a comment, replaces `SLACK_BOT_TOKEN=""` with the new value, appends missing keys once, and never emits an empty value for a key in `updates`; `read_private_key` on a temp PEM returns one line containing `\n` escapes and both `BEGIN`/`END` markers; `collect_answers` with `gateway=True` does not ask for a model key and sets `LANGSMITH_GATEWAY_ENABLED=true`; `main(["--output", tmp, "--non-interactive"])` with the seven minimum vars in `os.environ` writes a file with mode `0o600` containing generated `DASHBOARD_JWT_SECRET` and `TOKEN_ENCRYPTION_KEY` that differ from each other.

- [ ] **Step 2: Implement** `scripts/setup_env.py` with `argparse` (`--output`, `--non-interactive`, `--force` to regenerate existing secrets, `--no-dashboard`), prompts via `input`/`getpass.getpass` for secrets, and an epilogue that prints only variable names written plus the freshly generated `GITHUB_WEBHOOK_SECRET` (the one value the user must paste into GitHub). Add to `Makefile`:

```make
setup:
	uv run python scripts/setup_env.py
```

and a help line `'setup                        - guided .env setup for GitHub + Slack'`.

- [ ] **Step 3: Run** `uv run pytest tests/scripts -q` → PASS; `make lint` → clean. **Step 4: Commit** `feat(setup): add a guided .env installer that generates the dashboard secrets`.

---

### Task 9: Documentation rewrite

**Files:** Rewrite `docs/INSTALLATION.md`; Modify `docs/CUSTOMIZATION.md` (sandbox env block, "Default repository" section); Modify `README.md` "Getting started".

- [ ] **Step 1:** New `INSTALLATION.md` structure: Prerequisites → Quick start (clone, `uv sync`, ngrok, GitHub App with only the four fields needed, Slack app from manifest with only token + signing secret, `make setup`, `make dev`, verify) → "Your `.env` should now contain" block with exactly the seven minimum variables and the two generated ones → Optional add-ons, each in `<details>`: Dashboard (client secret, base URLs, admins, CORS extra origins), Per-user GitHub OAuth via LangSmith, Linear, "Sign in with Slack", Custom sandbox snapshot & environments, Other sandbox providers, Reviewer/analyzer, Web search, Mention handles & allowlists, Production deployment → Advanced overrides table (every deprecated/override var with its discovery rule) → Troubleshooting (updated messages). Preserve every existing fact (GitHub App permissions, Slack manifest, OIDC admin auth, key rotation, Docker run).
- [ ] **Step 2:** `CUSTOMIZATION.md`: snapshot block marked optional with the platform-default sentence; "Default repository" points to Admin → Team settings and lists env vars as deprecated seeds.
- [ ] **Step 3:** README: replace "Complete the required `.env`..." paragraph with `make setup` flow.
- [ ] **Step 4:** Commit `docs: rewrite installation around the GitHub + Slack happy path`.

---

### Task 10: Verification

- [ ] `make lint && make format-check && make typecheck && make test` → all green; paste summary counts in the handoff.
- [ ] `pnpm --filter open-swe-dashboard run typecheck` (Task 5 UI copy).
- [ ] `grep -rn "GITHUB_APP_ID\b" docs README.md` returns only the Advanced overrides row.
- [ ] Manual smoke (no real secrets): `uv run python scripts/setup_env.py --non-interactive --output /tmp/x.env` with fake values; confirm file mode 600 and two distinct generated secrets.
