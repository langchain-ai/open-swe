"""Every environment variable Open SWE reads, declared once.

Read values lazily through ``ENV.<NAME>`` so late secret hydration, rotated keys
and test monkeypatching are all observed. An empty value counts as unset.
Deprecated names are honored only here, as ``aliases`` of the current name or as
entries flagged ``deprecated`` for the startup report; nothing else in the
codebase reads ``os.environ`` for configuration.
"""

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class EnvVar:
    name: str
    description: str
    default: str | None = None
    aliases: tuple[str, ...] = ()
    secret: bool = False
    deprecated: str | None = None
    # Current variable that makes this deprecated one redundant. When both are set
    # (LangGraph Platform injects the legacy LANGCHAIN_* names next to the
    # LANGSMITH_* ones) the deprecated one is not worth a warning.
    replaced_by: str | None = None

    def _lookup(self, environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
        env = os.environ if environ is None else environ
        for candidate in (self.name, *self.aliases):
            value = env.get(candidate, "").strip()
            if value:
                return candidate, value
        return None

    def source(self, environ: Mapping[str, str] | None = None) -> str | None:
        """The variable name that supplied the value, or None when unset."""
        found = self._lookup(environ)
        return found[0] if found else None

    def is_set(self, environ: Mapping[str, str] | None = None) -> bool:
        return self._lookup(environ) is not None

    def optional(self, environ: Mapping[str, str] | None = None) -> str | None:
        """The configured value, ignoring the declared default."""
        found = self._lookup(environ)
        return found[1] if found else None

    def get(self, default: str | None = None, environ: Mapping[str, str] | None = None) -> str:
        """The configured value, else ``default``, else the declared default, else ``""``."""
        value = self.optional(environ)
        if value is not None:
            return value
        if default is not None:
            return default
        return self.default or ""

    def require(self, environ: Mapping[str, str] | None = None) -> str:
        value = self.optional(environ)
        if value is None:
            raise KeyError(self.name)
        return value

    def get_int(self, default: int, environ: Mapping[str, str] | None = None) -> int:
        raw = self.optional(environ)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{self.name} must be an integer, got {raw!r}") from exc

    def get_bool(self, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
        raw = self.optional(environ)
        if raw is None:
            return default
        lowered = raw.lower()
        if lowered in _TRUTHY:
            return True
        if lowered in _FALSY:
            return False
        return default

    def get_list(self, environ: Mapping[str, str] | None = None) -> list[str]:
        """Comma-separated values, trimmed, empties dropped."""
        return [item.strip() for item in self.get(environ=environ).split(",") if item.strip()]


class Registry:
    def __init__(self) -> None:
        self._vars: dict[str, EnvVar] = {}

    def var(
        self,
        name: str,
        description: str,
        *,
        default: str | None = None,
        aliases: tuple[str, ...] = (),
        secret: bool = False,
        deprecated: str | None = None,
        replaced_by: str | None = None,
    ) -> EnvVar:
        if name in self._vars:
            raise ValueError(f"{name} declared twice")
        var = EnvVar(name, description, default, aliases, secret, deprecated, replaced_by)
        self._vars[name] = var
        return var

    def __getattr__(self, name: str) -> EnvVar:
        try:
            return self.__dict__["_vars"][name]
        except KeyError:
            raise AttributeError(f"undeclared environment variable {name}") from None

    def __getitem__(self, name: str) -> EnvVar:
        try:
            return self._vars[name]
        except KeyError:
            raise KeyError(f"undeclared environment variable {name}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._vars

    def variables(self) -> Iterator[EnvVar]:
        return iter(self._vars.values())

    def deprecated_in_use(self, environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
        """``(variable, hint)`` for every deprecated name present in ``environ``."""
        found: list[tuple[str, str]] = []
        for var in self._vars.values():
            if var.deprecated and var.is_set(environ):
                if var.replaced_by and self._vars[var.replaced_by].is_set(environ):
                    continue
                found.append((var.name, var.deprecated))
            source = var.source(environ)
            if source is not None and source != var.name:
                found.append((source, f"use {var.name} instead."))
        return found


ENV = Registry()

# --- LangSmith and LangGraph -------------------------------------------------------
ENV.var(
    "LANGSMITH_API_KEY",
    "LangSmith API key for sandboxes, trace links and feedback; the LangSmith SDK "
    "reads the same variable for tracing and LangGraph Platform injects it.",
    aliases=("LANGSMITH_API_KEY_PROD",),
    secret=True,
)
ENV.var(
    "LANGSMITH_ENDPOINT",
    "LangSmith API endpoint; set for self-hosted or regional LangSmith.",
    default="https://api.smith.langchain.com",
    aliases=("LANGSMITH_ENDPOINT_PROD",),
)
ENV.var(
    "LANGSMITH_TENANT_ID",
    "LangSmith workspace id used in trace links; discovered from the workspace when unset.",
    aliases=("LANGSMITH_TENANT_ID_PROD",),
)
ENV.var(
    "LANGSMITH_TRACING_PROJECT_ID",
    "Fallback tracing project id for trace links when the per-graph project cannot be "
    "resolved by name.",
    aliases=("LANGSMITH_TRACING_PROJECT_ID_PROD",),
)
ENV.var(
    "LANGSMITH_URL_PROD",
    "Explicit LangSmith web host for trace links.",
    deprecated="the web host is derived from LANGSMITH_ENDPOINT.",
)
ENV.var(
    "LANGSMITH_HOST_API_URL",
    "LangGraph Platform control-plane API, used by the legacy LangSmith-brokered GitHub auth.",
    default="https://api.host.langchain.com",
)
ENV.var("SANDBOX_LANGSMITH_API_KEY", "LangSmith key for a separate sandbox workspace.", secret=True)
ENV.var("SANDBOX_LANGSMITH_ENDPOINT", "LangSmith endpoint for a separate sandbox workspace.")
ENV.var(
    "LANGSMITH_GATEWAY_API_KEY",
    "LangSmith key with gateway:invoke for the LLM Gateway.",
    secret=True,
)
ENV.var(
    "LANGSMITH_GATEWAY_BASE_URL",
    "LangSmith LLM Gateway host.",
    default="https://gateway.smith.langchain.com",
)
ENV.var(
    "LANGSMITH_GATEWAY_ENABLED",
    "Route provider calls through the LangSmith LLM Gateway.",
    default="false",
)
ENV.var(
    "LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES",
    "Keep the OpenAI Responses API when routed through the gateway.",
    default="true",
)
ENV.var(
    "REVIEWER_OUTCOMES_DATASET",
    "LangSmith dataset for reviewer finding outcomes.",
    default="openswe-reviewer-outcomes",
)
ENV.var("EVAL_LANGSMITH_PROJECT", "LangSmith project reviewer evals trace into.")
ENV.var(
    "LANGGRAPH_URL",
    "URL of the LangGraph server the FastAPI side calls to create and stream runs.",
    default="http://localhost:2024",
    aliases=("LANGGRAPH_URL_PROD",),
)
ENV.var(
    "LANGCHAIN_REVISION_ID", "Revision id LangGraph Platform injects; attached to run metadata."
)
ENV.var(
    "LANGSMITH_TRACING",
    "Enables LangSmith tracing; read by the LangSmith SDK and injected by LangGraph Platform.",
)
ENV.var(
    "LANGCHAIN_API_KEY",
    "Legacy SDK alias.",
    deprecated="Open SWE reads LANGSMITH_API_KEY; this legacy alias is ignored.",
    replaced_by="LANGSMITH_API_KEY",
)
ENV.var(
    "LANGCHAIN_TRACING_V2",
    "Legacy SDK alias.",
    deprecated="set LANGSMITH_TRACING=true instead; this legacy alias is ignored by Open SWE.",
    replaced_by="LANGSMITH_TRACING",
)
ENV.var(
    "LANGCHAIN_PROJECT",
    "Legacy SDK alias, ignored: graphs pin their own tracing projects. LangGraph Platform "
    "injects it.",
)

# --- GitHub ------------------------------------------------------------------------------
ENV.var(
    "GITHUB_APP_ID",
    "Numeric GitHub App id.",
    deprecated=(
        "GITHUB_APP_CLIENT_ID is the GitHub App JWT issuer; GITHUB_APP_ID is only used when "
        "GITHUB_APP_CLIENT_ID is unset."
    ),
)
ENV.var("GITHUB_APP_CLIENT_ID", "GitHub App client id for the dashboard OAuth flow.")
ENV.var(
    "GITHUB_APP_CLIENT_SECRET",
    "GitHub App client secret for the dashboard OAuth flow.",
    secret=True,
)
ENV.var(
    "GITHUB_APP_PRIVATE_KEY", "GitHub App private key (PEM) used to sign app JWTs.", secret=True
)
ENV.var(
    "GITHUB_APP_INSTALLATION_ID",
    "GitHub App installation used when a run names none; discovered when the app has a "
    "single installation.",
)
ENV.var("GITHUB_WEBHOOK_SECRET", "HMAC secret for GitHub webhook deliveries.", secret=True)
ENV.var(
    "GITHUB_OAUTH_PROVIDER_ID", "LangSmith OAuth provider id for the legacy brokered GitHub auth."
)
ENV.var(
    "X_SERVICE_AUTH_JWT_SECRET",
    "Secret minting service JWTs for the legacy brokered GitHub auth.",
    secret=True,
)
ENV.var(
    "USER_ID_API_KEY_MAP", "Legacy per-user API key map for the brokered GitHub auth.", secret=True
)
ENV.var("OPEN_SWE_MENTION_TAGS", "Comma-separated handles this deployment answers to.")
ENV.var("EXTRA_INTERNAL_BOT_LOGINS", "Comma-separated bot logins treated as internal commenters.")
ENV.var(
    "ALLOWED_GITHUB_ORGS", "Comma-separated GitHub orgs allowed for webhooks and dashboard login."
)
ENV.var("ALLOWED_GITHUB_REPOS", "Comma-separated owner/repo pairs allowed for webhooks.")
ENV.var("PUBLIC_REPO_ORG_GATE", "Single org whose members may trigger runs on public repos.")
ENV.var(
    "DEFAULT_REPO_OWNER",
    "Default GitHub owner when a run names no repository.",
    deprecated="set the default repository in Admin → Team settings instead.",
)
ENV.var(
    "DEFAULT_REPO_NAME",
    "Default GitHub repository when a run names none.",
    deprecated="set the default repository in Admin → Team settings instead.",
)
ENV.var(
    "SLACK_REPO_OWNER",
    "Slack-specific default repository owner.",
    deprecated="set the default repository in Admin → Team settings instead.",
)
ENV.var(
    "SLACK_REPO_NAME",
    "Slack-specific default repository name.",
    deprecated="set the default repository in Admin → Team settings instead.",
)

# --- Slack and Linear ----------------------------------------------------------------------
ENV.var("SLACK_BOT_TOKEN", "Slack bot user OAuth token (xoxb-...).", secret=True)
ENV.var("SLACK_SIGNING_SECRET", "HMAC secret for Slack webhook deliveries.", secret=True)
ENV.var(
    "SLACK_BOT_USER_ID",
    "Slack user id of the bot, for mention detection; discovered via auth.test when unset.",
)
ENV.var(
    "SLACK_BOT_USERNAME",
    "Slack handle of the bot, for plain-text mention detection; discovered when unset.",
)
ENV.var("SLACK_CLIENT_ID", "Slack app client id for Sign in with Slack.")
ENV.var("SLACK_CLIENT_SECRET", "Slack app client secret for Sign in with Slack.", secret=True)
ENV.var("SLACK_TEAM_ID", "Restrict Sign in with Slack to one workspace.")
ENV.var("LINEAR_API_KEY", "Linear API key.", secret=True)
ENV.var("LINEAR_WEBHOOK_SECRET", "HMAC secret for Linear webhook deliveries.", secret=True)

# --- Dashboard ------------------------------------------------------------------------------
ENV.var("DASHBOARD_BASE_URL", "Public URL of the dashboard frontend.")
ENV.var(
    "DASHBOARD_API_BASE_URL", "Public URL browsers use for /dashboard/api/* and OAuth callbacks."
)
ENV.var("DASHBOARD_ALLOWED_ORIGINS", "Comma-separated extra origins allowed for credentialed CORS.")
ENV.var(
    "DASHBOARD_JWT_SECRET",
    "HMAC secret for dashboard session cookies and OAuth state.",
    secret=True,
)
ENV.var(
    "TOKEN_ENCRYPTION_KEY",
    "Fernet key(s) encrypting stored OAuth tokens, most recent first.",
    secret=True,
)
ENV.var("CONFIGURED_ADMINS", "Comma-separated GitHub logins or emails with admin access.")
ENV.var(
    "OBSERVABILITY_AUTHORIZED_EMAILS", "Comma-separated emails allowed to use observability tools."
)
ENV.var("ADMIN_OIDC_SUBJECTS", "Comma-separated GitHub Actions OIDC subjects allowed as admins.")
ENV.var("ADMIN_OIDC_AUDIENCE", "Audience required on admin OIDC tokens.", default="open-swe")
ENV.var(
    "NOTION_MCP_CLIENT_NAME",
    "Client name registered with the Notion MCP OAuth server.",
    default="Open SWE",
)
ENV.var("RUN_COMPLETE_WEBHOOK_SECRET", "Token authenticating /webhooks/run-complete.", secret=True)
ENV.var("COMPLETION_WEBHOOK_URL", "Where LangGraph posts run-completion webhooks.")

# --- Models and tools ------------------------------------------------------------------------
ENV.var("ANTHROPIC_API_KEY", "Anthropic API key.", secret=True)
ENV.var("OPENAI_API_KEY", "OpenAI API key (models and voice dictation).", secret=True)
ENV.var("OPENAI_BASE_URL", "OpenAI-compatible API base URL.", aliases=("OPENAI_API_BASE",))
ENV.var("GOOGLE_API_KEY", "Google AI API key.", secret=True)
ENV.var("GROQ_API_KEY", "Groq API key.", secret=True)
ENV.var("FIREWORKS_API_KEY", "Fireworks API key.", secret=True)
ENV.var("BASETEN_API_KEY", "Baseten API key.", secret=True)
ENV.var("MODEL_API_KEY", "Generic model API key handed to sandbox tooling.", secret=True)
ENV.var("LLM_MODEL_ID", "Default model in provider:model form.")
ENV.var(
    "LLM_REASONING_EFFORT",
    "Reasoning effort for the default model (low, medium, high, max) when no team or profile setting applies.",
)
ENV.var("LLM_FALLBACK_MODEL_ID", "Fallback model in provider:model form.")
ENV.var(
    "STAGEHAND_MODEL",
    "Model Stagehand browser automation uses.",
    default="anthropic/claude-sonnet-4-5",
)
ENV.var("STAGEHAND_MODEL_API_KEY", "API key for the Stagehand model.", secret=True)
ENV.var("STAGEHAND_HEADLESS", "Run the Stagehand browser headless.", default="true")
ENV.var(
    "STAGEHAND_LOCAL_CHROME_PATH",
    "Chromium binary for local Stagehand runs.",
    default="/usr/bin/chromium",
)
ENV.var("EXA_API_KEY", "Exa API key enabling web search.", secret=True)
ENV.var("DATADOG_MCP_TOOLSETS", "Datadog MCP toolsets to load.", default="core")
ENV.var("CORRIDOR_MCP_URL", "Corridor MCP server URL.", aliases=("CORRIDOR_MCP_SERVER_URL",))
ENV.var(
    "CORRIDOR_API_TOKEN",
    "Corridor API token for the MCP server.",
    aliases=("CORRIDOR_MCP_TOKEN", "CORRIDOR_TOKEN"),
    secret=True,
)
ENV.var(
    "API_STANDARDS_SKILL_HANDLE", "Hub handle of the API standards skill.", default="api-standards"
)
ENV.var("DEFAULT_PROMPT_PATH", "Path to a default prompt file.")
ENV.var("TOOL_LOADER_TIMEOUT_SECONDS", "Timeout for loading optional tool integrations.")
ENV.var("OPEN_SWE_MODEL_CALL_TIMEOUT_SECONDS", "Cap on a single model call.")
ENV.var("OPEN_SWE_WRAPUP_TIMEOUT_SECONDS", "Time granted to wrap up after a timeout.")

# --- Sandboxes ---------------------------------------------------------------------------------
ENV.var(
    "SANDBOX_TYPE",
    "Sandbox provider: langsmith, modal, daytona, runloop, e2b or local.",
    default="langsmith",
)
ENV.var("DEFAULT_SANDBOX_SNAPSHOT_ID", "Base LangSmith snapshot new sandboxes boot from.")
ENV.var("DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES", "Root filesystem size for new sandboxes.")
ENV.var("DEFAULT_SANDBOX_VCPUS", "vCPUs for new sandboxes.")
ENV.var("DEFAULT_SANDBOX_MEM_BYTES", "Memory for new sandboxes.")
ENV.var("DEFAULT_SANDBOX_IDLE_TTL_SECONDS", "Idle seconds before a sandbox stops; 0 disables.")
ENV.var(
    "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS", "Seconds after stop before deletion; 0 disables."
)
ENV.var("SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS", "Client-side grace past a command's own timeout.")
ENV.var("SANDBOX_CREATE_EXTRA_JSON", "JSON object merged into the sandbox create body.")
ENV.var("ENVIRONMENT_SNAPSHOT_PREFIX", "Prefix for environment snapshot names.", default="openswe")
ENV.var("LOCAL_SANDBOX_ROOT_DIR", "Root directory for the local sandbox provider.")
ENV.var(
    "GIT_CONFIG_GLOBAL",
    "Git's global config path; the local provider keeps it out of ~/.gitconfig.",
)
ENV.var("MODAL_APP_NAME", "Modal app name for the modal provider.", default="open-swe")
ENV.var("DAYTONA_API_KEY", "Daytona API key.", secret=True)
ENV.var(
    "DAYTONA_SANDBOX_SNAPSHOT",
    "Daytona snapshot new sandboxes boot from.",
    default="daytonaio/sandbox:0.6.0",
)
ENV.var("E2B_API_KEY", "E2B API key.", secret=True)
ENV.var("E2B_TEMPLATE", "E2B template new sandboxes boot from.")
ENV.var("RUNLOOP_API_KEY", "Runloop API key.", secret=True)

# --- Desktop, local auth and debugging -------------------------------------------------------
ENV.var(
    "OPEN_SWE_LOCAL_PROJECTS_FILE", "Allowlist file of local projects the desktop agent may open."
)
ENV.var("OPEN_SWE_LOCAL_WORKTREES_DIR", "Directory for desktop worktrees.")
ENV.var("OPEN_SWE_LOCAL_ARTIFACTS_DIR", "Directory for desktop artifacts.")
ENV.var("OPEN_SWE_LOCAL_AUTH_TOKEN", "Bearer token the desktop backend requires.", secret=True)
ENV.var(
    "OPEN_SWE_OPENAI_OAUTH_BROKER_URL", "Broker the desktop app uses to obtain OpenAI OAuth tokens."
)
ENV.var(
    "OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN",
    "Token authenticating to the OpenAI OAuth broker.",
    secret=True,
)
ENV.var("BG_JOB_ISOLATED_LOOPS", "LangGraph background-job event-loop isolation flag.")
ENV.var("DEBUG_TRACEMALLOC", "Start tracemalloc to attribute unclosed-session warnings.")
ENV.var("DEBUG_TRACEMALLOC_FRAMES", "Frames tracemalloc records per allocation.", default="25")
