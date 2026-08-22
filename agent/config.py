"""The only module that reads the process environment for application config.

Every setting is exposed as a *function*, never a module constant. Deployments
rotate keys and hydrate secrets after import, so a value frozen at import time
is a value that goes stale without anything noticing. Each variable is parsed in
exactly one place here; the rest of ``agent/`` calls the accessor at the point
of use.

Genuinely process-level reads (``HOME``/``PATH`` handed to a shell,
``GIT_CONFIG_GLOBAL`` probes, the queue's own ``BG_JOB_ISOLATED_LOOPS`` switch)
stay where they are — they configure a process, not this application.
"""

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlparse

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
DEFAULT_LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"
DEFAULT_LANGSMITH_APP_URL = "https://smith.langchain.com"
DEFAULT_LANGSMITH_HOST_API_URL = "https://api.host.langchain.com"
DEFAULT_GATEWAY_BASE_URL = "https://gateway.smith.langchain.com"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
COMPLETION_WEBHOOK_PATH = "/webhooks/run-complete"

LangSmithPurpose = Literal["prod", "platform", "gateway", "sandbox"]


def environ() -> Mapping[str, str]:
    """The live environment, for the rare reader that wants the whole mapping."""
    return os.environ


def _raw(name: str) -> str | None:
    return os.environ.get(name)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value


def _opt(name: str) -> str | None:
    """Stripped value, or ``None`` when unset or blank."""
    return _env(name).strip() or None


def _csv(name: str) -> tuple[str, ...]:
    return tuple(entry.strip() for entry in _env(name).split(",") if entry.strip())


def _csv_lower(name: str) -> frozenset[str]:
    return frozenset(entry.lower() for entry in _csv(name))


def _flag(name: str, *, default: bool = False) -> bool:
    value = _env(name).strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def _lenient_float(name: str, default: float) -> float:
    """Positive float, falling back to ``default`` (with a warning) when unusable."""
    raw = _env(name).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s must be positive, got %s; using %s", name, value, default)
        return default
    return value


def _lenient_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """Integer, falling back to ``default`` (with a warning) when unusable."""
    raw = _env(name).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return default if minimum is not None and value < minimum else value


def _strict_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """Integer that refuses to guess: a malformed value raises at startup."""
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{name} must be an integer, got {raw!r}"
        raise ValueError(msg) from exc
    if minimum is not None and value < minimum:
        msg = f"{name} must be >= {minimum}, got {value}"
        raise ValueError(msg)
    return value


def _base_url(name: str) -> str | None:
    """A configured base URL with surrounding space and trailing slashes removed."""
    return _env(name).strip().rstrip("/") or None


# --- Deployment ------------------------------------------------------------


def langgraph_url() -> str:
    """URL of the LangGraph runtime serving this deployment."""
    return _env("LANGGRAPH_URL") or _env("LANGGRAPH_URL_PROD") or DEFAULT_LANGGRAPH_URL


def configured_langgraph_url() -> str | None:
    """The explicitly configured LangGraph URL, without the local-dev default."""
    return _opt("LANGGRAPH_URL") or _opt("LANGGRAPH_URL_PROD")


def langgraph_client() -> LangGraphClient:
    """HTTP client for the deployment at ``langgraph_url()``.

    For callers outside the graph runtime (webhooks, dispatch, dashboard jobs).
    Code that runs *inside* a graph — factories, middleware, tools — must use
    :func:`in_process_langgraph_client` instead.
    """
    return get_client(url=langgraph_url())


def in_process_langgraph_client() -> LangGraphClient:
    """Loopback client for code running inside the LangGraph server.

    ``get_client()`` with no URL uses the server's in-process ASGI transport:
    no network hop, no API-key auth, and a fast failure outside the server.
    Routing in-graph calls over HTTP instead would make every run call its own
    deployment (and, in tests, retry against a dead port for seconds).
    """
    return get_client()


def is_local_dev() -> bool:
    """Whether this process is a developer's ``langgraph dev``, not a deployment.

    Keyed on the LangGraph URL resolving to loopback: a deployment points
    ``LANGGRAPH_URL``/``LANGGRAPH_URL_PROD`` at its own origin, and the default
    when neither is set is the local dev server.
    """
    return (urlparse(langgraph_url()).hostname or "").lower() in _LOOPBACK_HOSTS


def agent_version_metadata() -> dict[str, str]:
    """Run metadata tagging traces with the deployment revision, if known."""
    revision = _opt("LANGCHAIN_REVISION_ID")
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


def debug_tracemalloc() -> bool:
    return bool(_opt("DEBUG_TRACEMALLOC"))


def debug_tracemalloc_frames(default: int) -> int:
    return _lenient_int("DEBUG_TRACEMALLOC_FRAMES", default)


# --- Webhook authenticity --------------------------------------------------


def github_webhook_secret() -> str:
    return _env("GITHUB_WEBHOOK_SECRET")


def slack_signing_secret() -> str:
    return _env("SLACK_SIGNING_SECRET")


def linear_webhook_secret() -> str:
    return _env("LINEAR_WEBHOOK_SECRET")


def run_complete_webhook_secret() -> str | None:
    return _opt("RUN_COMPLETE_WEBHOOK_SECRET")


def completion_webhook_base_url() -> str:
    """Where the platform posts run completion; relative until a deployment sets it."""
    return _env("COMPLETION_WEBHOOK_URL") or COMPLETION_WEBHOOK_PATH


# --- GitHub app and OAuth --------------------------------------------------


def github_app_id() -> str:
    return _env("GITHUB_APP_ID")


def github_app_private_key() -> str:
    return _env("GITHUB_APP_PRIVATE_KEY")


def github_app_installation_id() -> str:
    return _env("GITHUB_APP_INSTALLATION_ID")


def github_app_oauth() -> tuple[str, str]:
    """``(client_id, client_secret)`` for the GitHub App's OAuth flow."""
    return _env("GITHUB_APP_CLIENT_ID"), _env("GITHUB_APP_CLIENT_SECRET")


def github_oauth_provider_id() -> str:
    return _env("GITHUB_OAUTH_PROVIDER_ID")


# --- GitHub allow-lists ----------------------------------------------------


def allowed_github_orgs() -> frozenset[str]:
    """Orgs this deployment may act in. Empty means no org allow-list."""
    return _csv_lower("ALLOWED_GITHUB_ORGS")


def allowed_github_repos() -> frozenset[str]:
    """``owner/name`` entries allowed on top of :func:`allowed_github_orgs`."""
    return _csv_lower("ALLOWED_GITHUB_REPOS")


def public_repo_org_gate() -> str:
    """Org whose members may trigger the agent on public repos. Empty disables the gate."""
    return _env("PUBLIC_REPO_ORG_GATE").strip()


def extra_internal_bot_logins() -> frozenset[str]:
    return frozenset(_csv("EXTRA_INTERNAL_BOT_LOGINS"))


def open_swe_mention_tags() -> tuple[str, ...]:
    """Handles that address this deployment. Empty means the built-in defaults."""
    return tuple(tag.lower() for tag in _csv("OPEN_SWE_MENTION_TAGS"))


# --- Repositories ----------------------------------------------------------


def default_repo_owner() -> str:
    """Fallback repo owner. Empty means the deployment has no default."""
    return _env("DEFAULT_REPO_OWNER").strip()


def default_repo_name() -> str:
    return _env("DEFAULT_REPO_NAME").strip()


def slack_repo_owner() -> str:
    return _env("SLACK_REPO_OWNER").strip() or default_repo_owner()


def slack_repo_name() -> str:
    return _env("SLACK_REPO_NAME").strip() or default_repo_name()


# --- Dashboard -------------------------------------------------------------


def dashboard_base_url() -> str | None:
    """Origin the dashboard frontend is served from, or ``None`` when unconfigured."""
    return _base_url("DASHBOARD_BASE_URL")


def dashboard_api_base_url() -> str | None:
    """Origin this API is reachable at, used to build OAuth redirect URIs."""
    return _base_url("DASHBOARD_API_BASE_URL")


def dashboard_api_is_https() -> bool:
    return _env("DASHBOARD_API_BASE_URL").strip().startswith("https://")


def dashboard_allowed_origins() -> tuple[str, ...]:
    """Extra origins allowed to call the dashboard API, exactly as configured."""
    return _csv("DASHBOARD_ALLOWED_ORIGINS")


def dashboard_jwt_secret() -> str:
    return _env("DASHBOARD_JWT_SECRET")


def configured_admins() -> frozenset[str]:
    return _csv_lower("CONFIGURED_ADMINS")


def observability_authorized_emails() -> frozenset[str]:
    return _csv_lower("OBSERVABILITY_AUTHORIZED_EMAILS")


def admin_oidc_audience() -> str:
    """Expected OIDC audience for admin automation. Empty means the built-in default."""
    return _env("ADMIN_OIDC_AUDIENCE").strip()


def admin_oidc_subjects() -> tuple[str, ...]:
    """Allowed OIDC subjects (``owner/repo:ref``) or bare repositories."""
    return _csv("ADMIN_OIDC_SUBJECTS")


def notion_mcp_client_name() -> str:
    return _env("NOTION_MCP_CLIENT_NAME", "Open SWE")


def token_encryption_key() -> str | None:
    """Fernet key list for tokens at rest (comma/newline separated, newest first)."""
    return _opt("TOKEN_ENCRYPTION_KEY")


def local_auth_token() -> str | None:
    return _opt("OPEN_SWE_LOCAL_AUTH_TOKEN")


# --- Slack -----------------------------------------------------------------


def slack_bot_token() -> str:
    return _env("SLACK_BOT_TOKEN")


def slack_bot_user_id() -> str:
    return _env("SLACK_BOT_USER_ID")


def slack_bot_username() -> str:
    return _env("SLACK_BOT_USERNAME")


def slack_oauth_client() -> tuple[str, str]:
    """``(client_id, client_secret)`` for Sign in with Slack."""
    return _env("SLACK_CLIENT_ID"), _env("SLACK_CLIENT_SECRET")


def slack_team_id() -> str:
    """Workspace linking is restricted to this Slack team id when set."""
    return _env("SLACK_TEAM_ID")


# --- Linear ----------------------------------------------------------------


def linear_api_key() -> str:
    return _env("LINEAR_API_KEY")


# --- LangSmith -------------------------------------------------------------


def _langsmith_endpoint() -> str:
    return _env("LANGSMITH_ENDPOINT").strip() or DEFAULT_LANGSMITH_ENDPOINT


def _langsmith_prod_endpoint() -> str:
    return _env("LANGSMITH_ENDPOINT_PROD").strip() or _langsmith_endpoint()


def langsmith_credentials(purpose: LangSmithPurpose) -> tuple[str, str] | None:
    """``(api_key, api_url)`` for one LangSmith audience, or ``None`` if unconfigured.

    The four audiences want different keys, and the differences are deliberate:
    each branch below says why. Nothing else in ``agent/`` may re-derive them.
    """
    if purpose == "prod":
        # Tracing projects, thread costs and the reviewer-outcomes dataset all
        # live in the prod tenant, so its key and endpoint travel together.
        prod_key = _opt("LANGSMITH_API_KEY_PROD")
        if prod_key:
            return prod_key, _langsmith_prod_endpoint()
        key = _opt("LANGSMITH_API_KEY") or _opt("LANGCHAIN_API_KEY")
        return (key, _langsmith_endpoint()) if key else None
    if purpose == "platform":
        # LangGraph Cloud injects LANGSMITH_API_KEY for the platform APIs, so it
        # wins here even where the prod key would win elsewhere.
        key = _opt("LANGSMITH_API_KEY") or _opt("LANGSMITH_API_KEY_PROD")
        return (key, _langsmith_endpoint()) if key else None
    if purpose == "gateway":
        # The gateway rejects keys without `gateway:invoke`, which the injected
        # platform key often lacks — hence a gateway-specific key first.
        key = (
            _opt("LANGSMITH_GATEWAY_API_KEY")
            or _opt("LANGSMITH_API_KEY_PROD")
            or _opt("LANGSMITH_API_KEY")
        )
        return (key, langsmith_gateway_base_url()) if key else None
    # Sandboxes may live in their own workspace, so the override pair replaces
    # the platform key and endpoint together.
    key = _opt("SANDBOX_LANGSMITH_API_KEY")
    if not key:
        platform = langsmith_credentials("platform")
        key = platform[0] if platform else None
    return (key, sandbox_langsmith_endpoint()) if key else None


def langsmith_prod_api_key() -> str:
    """``LANGSMITH_API_KEY_PROD`` itself — the marker that this is a deployment."""
    return _env("LANGSMITH_API_KEY_PROD")


def sandbox_langsmith_endpoint() -> str:
    """LangSmith API root used for sandbox operations."""
    return _env("SANDBOX_LANGSMITH_ENDPOINT").strip() or _langsmith_endpoint()


def langsmith_host_api_url() -> str:
    return _env("LANGSMITH_HOST_API_URL").strip() or DEFAULT_LANGSMITH_HOST_API_URL


def langsmith_tenant_id() -> str | None:
    return _opt("LANGSMITH_TENANT_ID_PROD")


def langsmith_app_url() -> str:
    """Base URL of the LangSmith UI, for building human-facing trace links."""
    return _env("LANGSMITH_URL_PROD").strip() or DEFAULT_LANGSMITH_APP_URL


def langsmith_tracing_project_id() -> str | None:
    return _opt("LANGSMITH_TRACING_PROJECT_ID_PROD")


def service_auth_jwt_secret() -> str:
    """Secret for minting per-user LangSmith service JWTs."""
    return _env("X_SERVICE_AUTH_JWT_SECRET")


def user_id_api_key_map() -> str:
    return _env("USER_ID_API_KEY_MAP")


def reviewer_outcomes_dataset() -> str:
    return _env("REVIEWER_OUTCOMES_DATASET", "openswe-reviewer-outcomes")


def eval_langsmith_project() -> str | None:
    return _opt("EVAL_LANGSMITH_PROJECT")


# --- LLM gateway -----------------------------------------------------------


def langsmith_gateway_base_url() -> str:
    return _env("LANGSMITH_GATEWAY_BASE_URL").strip().rstrip("/") or DEFAULT_GATEWAY_BASE_URL


def langsmith_gateway_enabled_default() -> bool:
    """Deployment-level default for routing model calls through the gateway."""
    return _flag("LANGSMITH_GATEWAY_ENABLED")


def langsmith_gateway_openai_use_responses() -> bool:
    """Whether gateway-routed OpenAI keeps the Responses API. Unset means yes."""
    raw = _raw("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES")
    return True if raw is None else raw.strip().lower() in _TRUTHY


# --- Models ----------------------------------------------------------------

_PROVIDER_API_KEY_ENV: dict[str, str] = {
    "openai:": "OPENAI_API_KEY",
    "anthropic:": "ANTHROPIC_API_KEY",
    "google_genai:": "GOOGLE_API_KEY",
    "groq:": "GROQ_API_KEY",
    "fireworks:": "FIREWORKS_API_KEY",
}


def default_llm_model_id() -> str | None:
    return _opt("LLM_MODEL_ID")


def fallback_llm_model_id() -> str | None:
    return _opt("LLM_FALLBACK_MODEL_ID")


def openai_base_url() -> str | None:
    return _opt("OPENAI_BASE_URL") or _opt("OPENAI_API_BASE")


def openai_api_key() -> str | None:
    return _opt("OPENAI_API_KEY")


def openai_oauth_broker() -> tuple[str, str] | None:
    """The desktop app's local OpenAI credential broker: ``(token_url, bearer)``.

    Only a loopback ``/token`` endpoint is accepted — the broker hands out the
    signed-in user's ChatGPT access token, so it must never be reachable off
    this machine.
    """
    url = _opt("OPEN_SWE_OPENAI_OAUTH_BROKER_URL")
    token = _opt("OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN")
    if not url or not token:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/token":
        return None
    return url, token


def missing_provider_api_key(model_id: str) -> str | None:
    """Name of the provider key ``model_id`` needs but the environment lacks.

    OpenAI models can run on the desktop broker's OAuth credentials instead of
    a key, so with the broker configured nothing is missing.
    """
    for prefix, name in _PROVIDER_API_KEY_ENV.items():
        if model_id.startswith(prefix):
            if _opt(name) or (prefix == "openai:" and openai_oauth_broker() is not None):
                return None
            return name
    return None


def model_call_timeout_seconds(default: float) -> float:
    return _lenient_float("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", default)


def wrapup_timeout_seconds(default: int) -> int:
    return _lenient_int("OPEN_SWE_WRAPUP_TIMEOUT_SECONDS", default, minimum=1)


def tool_loader_timeout_seconds(default: float) -> float:
    return _lenient_float("TOOL_LOADER_TIMEOUT_SECONDS", default)


# --- Sandboxes -------------------------------------------------------------


def sandbox_provider() -> str:
    """The configured sandbox provider; the default is stated here and nowhere else."""
    return _env("SANDBOX_TYPE").strip() or "langsmith"


def is_langsmith_sandbox() -> bool:
    return sandbox_provider() == "langsmith"


def default_sandbox_snapshot_id() -> str | None:
    return _opt("DEFAULT_SANDBOX_SNAPSHOT_ID")


def sandbox_fs_capacity_bytes(default: int) -> int:
    return _strict_int("DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES", default)


def sandbox_vcpus(default: int) -> int:
    return _strict_int("DEFAULT_SANDBOX_VCPUS", default)


def sandbox_mem_bytes(default: int) -> int:
    return _strict_int("DEFAULT_SANDBOX_MEM_BYTES", default)


def sandbox_idle_ttl_seconds(default: int) -> int:
    return _strict_int("DEFAULT_SANDBOX_IDLE_TTL_SECONDS", default, minimum=0)


def sandbox_delete_after_stop_seconds(default: int) -> int:
    return _strict_int("DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS", default, minimum=0)


def sandbox_execute_client_grace_seconds(default: int) -> int:
    return _strict_int("SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS", default)


def sandbox_create_extra_fields() -> dict[str, Any]:
    """Extra JSON fields merged into the sandbox-create request body."""
    raw = _env("SANDBOX_CREATE_EXTRA_JSON").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"SANDBOX_CREATE_EXTRA_JSON must be valid JSON, got {raw!r}"
        raise ValueError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"SANDBOX_CREATE_EXTRA_JSON must be a JSON object, got {type(parsed).__name__}"
        raise ValueError(msg)
    return parsed


def environment_snapshot_prefix() -> str:
    return _env("ENVIRONMENT_SNAPSHOT_PREFIX").strip()


def repo_snapshot_base_image() -> str:
    return _env("REPO_SNAPSHOT_BASE_IMAGE").strip()


def repo_snapshot_stale_build_seconds(default: int) -> int:
    return max(_lenient_int("REPO_SNAPSHOT_STALE_BUILD_SECONDS", default), 0)


def repo_snapshot_build_timeout_seconds(default: int) -> int:
    return _strict_int("REPO_SNAPSHOT_BUILD_TIMEOUT_SECONDS", default)


def local_sandbox_root_dir() -> str | None:
    return _opt("LOCAL_SANDBOX_ROOT_DIR")


def local_projects_file() -> str | None:
    """Allow-list of local project directories desktop runs may open."""
    return _opt("OPEN_SWE_LOCAL_PROJECTS_FILE")


def local_artifacts_dir() -> str | None:
    """Where desktop runs keep the agent's scratch files, outside the project."""
    return _opt("OPEN_SWE_LOCAL_ARTIFACTS_DIR")


def modal_app_name() -> str:
    return _env("MODAL_APP_NAME", "open-swe")


def e2b_api_key() -> str | None:
    return _opt("E2B_API_KEY")


def e2b_template() -> str | None:
    """E2B template id. Set-but-blank is a misconfiguration, not "use the default"."""
    raw = _raw("E2B_TEMPLATE")
    if raw is None:
        return None
    template = raw.strip()
    if not template:
        msg = "E2B_TEMPLATE must not be empty"
        raise ValueError(msg)
    return template


def runloop_api_key() -> str | None:
    return _opt("RUNLOOP_API_KEY")


def daytona_api_key() -> str | None:
    return _opt("DAYTONA_API_KEY")


def daytona_snapshot(default: str) -> str:
    snapshot = _env("DAYTONA_SANDBOX_SNAPSHOT", default).strip()
    if not snapshot:
        msg = "DAYTONA_SANDBOX_SNAPSHOT must not be empty"
        raise ValueError(msg)
    return snapshot


# --- Integrations ----------------------------------------------------------


def exa_api_key() -> str | None:
    return _opt("EXA_API_KEY")


def datadog_mcp_toolsets(default: str) -> str:
    return _env("DATADOG_MCP_TOOLSETS", default).strip() or default


def corridor_mcp_token() -> str:
    for name in ("CORRIDOR_API_TOKEN", "CORRIDOR_MCP_TOKEN", "CORRIDOR_TOKEN"):
        if value := _opt(name):
            return value
    return ""


def corridor_mcp_url() -> str:
    for name in ("CORRIDOR_MCP_URL", "CORRIDOR_MCP_SERVER_URL"):
        if value := _opt(name):
            return value
    return ""


def stagehand_local_mode() -> bool:
    return _env("STAGEHAND_ENV", "LOCAL").strip().upper() != "BROWSERBASE"


def stagehand_model(default: str) -> str:
    return _env("STAGEHAND_MODEL", default)


def stagehand_model_api_key() -> str | None:
    return _opt("STAGEHAND_MODEL_API_KEY") or _opt("MODEL_API_KEY") or _opt("ANTHROPIC_API_KEY")


def stagehand_headless() -> bool:
    return _flag("STAGEHAND_HEADLESS", default=True)


def stagehand_local_chrome_path() -> str | None:
    return _opt("STAGEHAND_LOCAL_CHROME_PATH")


def browserbase_api_key() -> str | None:
    return _opt("BROWSERBASE_API_KEY")


def browserbase_project_id() -> str | None:
    return _opt("BROWSERBASE_PROJECT_ID")


# --- Prompts and skills ----------------------------------------------------


def default_prompt_path() -> str | None:
    return _opt("DEFAULT_PROMPT_PATH")


def api_standards_skill_handle() -> str:
    return _env("API_STANDARDS_SKILL_HANDLE", "api-standards")
