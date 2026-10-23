//! Client for the LangSmith APIs the CLI talks to.
//!
//! LangSmith exposes a few HTTP surfaces, all authenticated with the same
//! credential (and optional workspace/tenant scope):
//!
//! - the **host backend** (`api.host.langchain.com`), which manages LangGraph
//!   Deployments — listing/creating deployments, requesting source-upload URLs,
//!   triggering builds, and polling revisions (see [`deployments`]), plus
//!   reading a deployment's Agent Server logs (see [`logs`]); and
//! - the **platform API** (`api.smith.langchain.com`), used here to read a
//!   workspace's configured secret names (see [`secrets`]), to list/delete
//!   managed sandboxes on teardown (see [`sandboxes`]), and to name the
//!   organization and workspace a deploy writes into (see [`workspaces`]); and
//! - **Context Hub**, used during deploy to sync durable agent context (see
//!   [`ContextHubClient`]).
//!
//! Credentials are a `LANGSMITH_API_KEY`: either a durable key the developer
//! pasted into `.env`, or a short-lived PAT minted after device login via
//! [`login`] and cached in the machine state directory. [`Client`] holds the
//! shared HTTP client and credentials for the host backend and platform API;
//! those endpoint groups live in the submodules as `impl Client` blocks.
//! Context Hub has its own client because it uses a separate base URL. The host
//! backend portion is ported from the langgraph CLI's `host-backend.mts`.

mod agent_auth;
pub(crate) mod agent_server;
mod agents;
mod context_hub;
mod crons;
mod deployments;
pub(crate) mod github_app_setup;
pub(crate) mod github_channel;
pub(crate) mod login;
mod logs;
mod registries;
mod sandboxes;
mod secrets;
mod snapshots;
#[cfg(test)]
pub(crate) mod test_support;
mod trigger_server;
mod workspaces;

pub(crate) use agent_auth::redact_secret;
pub(crate) use agent_auth::AgentGrant;
pub(crate) use agent_auth::OAuthAuthorizationSession;
pub use agent_auth::{ConnectionOwnerPolicy, ConnectionSummary, CreateOAuthConnection};
pub use context_hub::ContextHubClient;
pub(crate) use context_hub::RepoOwnership;
pub use logs::{LogEntry, LogLevel, LogOrder, LogPage, LogQuery, LogTimestamp};
pub use sandboxes::{CreateSandboxBoxOptions, SandboxCommandResult, SandboxSummary};
pub use snapshots::{SnapshotDelete, SnapshotSummary, DEFAULT_SNAPSHOT_READY_TIMEOUT};
pub(crate) use trigger_server::{
    ChannelSetupRequest, ChannelSetupResponse, ChannelSetupStatus, ChannelSetupTarget,
    SlackChannelApp, SlackChannelOptions, SlackChannelSetup, SlackTriggerMode,
};
pub use workspaces::{DeployTarget, WorkspaceSummary};

pub(crate) use login::{
    is_cli_session_api_key, note_session_api_key, resolve_login_api_url, Credential, Login,
};

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use clap::ValueEnum;
use reqwest::blocking::Client as HttpClient;
use reqwest::{Method, StatusCode};
use serde::Serialize;
use serde_json::Value;

/// Accepted env var for the LangSmith / host-backend API key.
pub const API_KEY_ENV: &str = "LANGSMITH_API_KEY";

/// Runner-only credential for reporting artifacts for one GitHub build revision.
pub const BUILD_ARTIFACTS_TOKEN_ENV: &str = "MDA_BUILD_ARTIFACTS_TOKEN";

/// Removed API key env names that previously aliased [`API_KEY_ENV`].
pub const REMOVED_API_KEY_ENVS: &[&str] = &["LANGCHAIN_API_KEY"];

/// Legacy env name for authenticating a constructed client to LangSmith Gateway.
///
/// Older `mda init --gateway` scaffolds read this instead of [`API_KEY_ENV`],
/// which is reserved and never reaches a deployment. Deploy still fills a blank
/// alias from the resolved LangSmith key so those projects keep working; new
/// projects should use `langsmith:` model strings or a provider key directly.
pub const GATEWAY_API_KEY_ENV: &str = "LANGSMITH_GATEWAY_API_KEY";

/// Resolve a preferred env var from CLI override, process env, then project `.env`.
///
/// When the preferred name is unset but a removed alias is present, returns an
/// error of the form `{alias} is no longer supported; set {preferred}{hint}`.
pub fn resolve_preferred_env(
    preferred: &str,
    removed: &[&str],
    cli_override: Option<&str>,
    env_file: &BTreeMap<String, String>,
    mut process_env: impl FnMut(&str) -> Option<String>,
    replacement_hint: &str,
) -> Result<Option<String>> {
    if let Some(value) = cli_override
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return Ok(Some(value.to_string()));
    }
    if let Some(value) = lookup_env(preferred, env_file, &mut process_env) {
        return Ok(Some(value));
    }
    for key in removed {
        if lookup_env(key, env_file, &mut process_env).is_some() {
            bail!("{key} is no longer supported; set {preferred}{replacement_hint}");
        }
    }
    Ok(None)
}

/// Where [`lookup_env_with_origin`] found a value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EnvOrigin {
    Shell,
    EnvFile,
}

/// Look up a non-empty trimmed env value from process env, then project `.env`.
pub(crate) fn lookup_env(
    key: &str,
    env_file: &BTreeMap<String, String>,
    process_env: &mut impl FnMut(&str) -> Option<String>,
) -> Option<String> {
    lookup_env_with_origin(key, env_file, process_env).map(|(value, _)| value)
}

/// Like [`lookup_env`], also reporting which source supplied the value.
///
/// The shell wins so a one-off `KEY=value mda …` or a CI-injected variable is
/// not silently shadowed by a project file. When both are set and disagree,
/// warn once per key so a stale shell export is not silently used either.
pub(crate) fn lookup_env_with_origin(
    key: &str,
    env_file: &BTreeMap<String, String>,
    process_env: &mut impl FnMut(&str) -> Option<String>,
) -> Option<(String, EnvOrigin)> {
    match process_env(key).and_then(|value| non_empty_trimmed(&value)) {
        Some(from_shell) => {
            warn_if_shell_shadows_env_file(key, &from_shell, env_file);
            Some((from_shell, EnvOrigin::Shell))
        }
        None => env_file
            .get(key)
            .and_then(|value| non_empty_trimmed(value))
            .map(|value| (value, EnvOrigin::EnvFile)),
    }
}

/// Warn once per key when a shell value shadows a different, non-empty value
/// in the project `.env`. For callers that already chose the shell value
/// without going through [`lookup_env_with_origin`]. Returns whether they
/// differ; a trailing `/` alone does not count.
pub(crate) fn warn_if_shell_shadows_env_file(
    key: &str,
    from_shell: &str,
    env_file: &BTreeMap<String, String>,
) -> bool {
    let differs = env_file
        .get(key)
        .and_then(|value| non_empty_trimmed(value))
        .is_some_and(|from_file| {
            from_file.trim_end_matches('/') != from_shell.trim().trim_end_matches('/')
        });
    if differs {
        warn_shell_shadows_env_file(key);
    }
    differs
}

fn non_empty_trimmed(value: &str) -> Option<String> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_string())
}

/// Names the key only: values are often credentials.
fn warn_shell_shadows_env_file(key: &str) {
    static WARNED: OnceLock<Mutex<BTreeSet<String>>> = OnceLock::new();
    let first = WARNED
        .get_or_init(Default::default)
        .lock()
        .map(|mut warned| warned.insert(key.to_string()))
        .unwrap_or(false);
    if first {
        let _ = cliclack::log::warning(format!(
            "{key} is set in both your shell and .env with different values; using the shell \
             value. Unset it in your shell to use the .env value."
        ));
    }
}

/// Per-request timeout. Source uploads use their own client/timeout.
pub(crate) const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// Canonical tenant header sent on LangSmith / Context Hub requests.
pub(crate) const TENANT_HEADER: &str = "X-Tenant-Id";

/// First-time deployment creation can provision backend resources and routinely
/// exceeds [`REQUEST_TIMEOUT`]. Source uploads use their own client/timeout.
pub(crate) const CREATE_DEPLOYMENT_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Clone, Copy)]
struct RequestOptions {
    timeout: Duration,
    studio_auth: bool,
}

const DEFAULT_REQUEST_OPTIONS: RequestOptions = RequestOptions {
    timeout: REQUEST_TIMEOUT,
    studio_auth: false,
};

/// A deployment secret (an environment variable injected into the deployment).
#[derive(Debug, Clone, Serialize)]
pub struct Secret {
    /// Environment variable name.
    pub name: String,
    /// Environment variable value.
    pub value: String,
}

/// Deployment type accepted by `mda deploy --deployment-type` and forwarded to
/// the LangSmith host backend.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, ValueEnum)]
pub enum DeploymentType {
    #[default]
    Dev,
    Prod,
}

impl DeploymentType {
    /// Wire value sent to the LangSmith host backend.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Dev => "dev",
            Self::Prod => "prod",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum AgentEnvironment {
    Development,
    Staging,
    Production,
}

impl AgentEnvironment {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Development => "development",
            Self::Staging => "staging",
            Self::Production => "production",
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct DeploymentAgent {
    pub agent_id: String,
    pub environment: AgentEnvironment,
}

impl DeploymentAgent {
    /// Prefix for the deploy-owned resources (sandboxes, Context Hub repos,
    /// recipes). The host names the deployment itself, so look that up with
    /// `list_agent_deployments` instead.
    ///
    /// Production keeps the bare agent ID: a name-only deploy lands in the
    /// production environment too, so switching it to agent mode keeps its
    /// memories and sandboxes. Other environments get a suffix to stay apart.
    pub fn resource_name(&self) -> Result<String> {
        match self.environment {
            AgentEnvironment::Production => Ok(self.agent_id.clone()),
            environment => crate::naming::normalize_name(&format!(
                "{}-{}",
                self.agent_id,
                environment.as_str()
            )),
        }
    }
}

/// Blocking JSON client for the LangSmith APIs.
///
/// Sends the credential (and optional tenant) on every request and routes calls
/// to either the host backend or the platform API depending on the endpoint
/// group.
#[derive(Clone)]
pub struct Client {
    http: HttpClient,
    credential: Credential,
    tenant_id: Option<String>,
    /// Host backend base URL (`https://api.host.langchain.com`), trailing slash
    /// trimmed.
    host_backend_url: String,
    /// LangSmith platform API base URL (`https://api.smith.langchain.com`),
    /// derived from the host backend URL. `None` for hosts whose platform API
    /// location is unknown (localhost, custom/self-hosted backends).
    platform_api_url: Option<String>,
}

impl Client {
    /// LangSmith platform API root used by Fleet and sandbox registry APIs.
    pub(crate) fn platform_base(&self) -> Result<&str> {
        self.platform_api_url
            .as_deref()
            .context("the LangSmith platform API is not available for this host")
    }

    /// Whether this client reaches Deployments through a self-hosted frontend.
    pub(crate) fn is_self_hosted(&self) -> bool {
        is_self_hosted_host_backend_url(&self.host_backend_url)
    }

    /// Public platform API base for this LangSmith installation.
    ///
    /// Self-hosted frontends reserve `/api` for backend traffic. SaaS and the
    /// direct local-development stack expose platform services at their API
    /// origin instead.
    pub(crate) fn public_platform_base(&self) -> Result<String> {
        let platform = self.platform_base()?;
        if !self.is_self_hosted() {
            return Ok(platform.to_string());
        }
        self_hosted_public_api_base_url(platform).context("LangSmith platform API URL is invalid")
    }

    /// Create a client for `host_backend_url` authenticated with a static API
    /// key. Prefer [`Self::with_credential`] when the caller may have signed in
    /// through the device authorization flow.
    pub fn new(
        host_backend_url: &str,
        api_key: impl Into<String>,
        tenant_id: Option<String>,
    ) -> Result<Self> {
        Self::with_credential(
            host_backend_url,
            Credential::ApiKey(api_key.into()),
            tenant_id,
        )
    }

    /// Like [`Self::new`], but with an explicit platform API base (Fleet,
    /// workspace secrets, Slack apps).
    ///
    /// Local LangSmith splits these: host-backend (`:8300`, `/v2/deployments`)
    /// vs nginx (`:1980`, `/v1/fleet/*`). SaaS maps `api.host.*` → `api.smith.*`
    /// when `platform_api_url` is `None`.
    pub fn new_with_platform(
        host_backend_url: &str,
        platform_api_url: Option<&str>,
        api_key: impl Into<String>,
        tenant_id: Option<String>,
    ) -> Result<Self> {
        Self::with_credential_and_platform(
            host_backend_url,
            platform_api_url,
            Credential::ApiKey(api_key.into()),
            tenant_id,
        )
    }

    /// Create a client for `host_backend_url`, deriving the matching LangSmith
    /// platform API URL and sending `credential` (and optional `tenant_id`) on
    /// every request.
    pub(crate) fn with_credential(
        host_backend_url: &str,
        credential: Credential,
        tenant_id: Option<String>,
    ) -> Result<Self> {
        Self::with_credential_and_platform(host_backend_url, None, credential, tenant_id)
    }

    pub(crate) fn with_credential_and_platform(
        host_backend_url: &str,
        platform_api_url: Option<&str>,
        credential: Credential,
        tenant_id: Option<String>,
    ) -> Result<Self> {
        if host_backend_url.is_empty() {
            bail!("Host backend URL is required");
        }
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build HTTP client")?;
        let host_backend_url = host_backend_url.trim_end_matches('/').to_string();
        let platform_api_url = platform_api_url
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| value.trim_end_matches('/').to_string())
            .or_else(|| langsmith_api_base_url(&host_backend_url))
            // Non-SaaS single-origin installs: Fleet often shares the host base.
            .or_else(|| Some(host_backend_url.clone()));
        Ok(Self {
            http,
            credential,
            tenant_id,
            platform_api_url,
            host_backend_url,
        })
    }

    /// Set the default request timeout for this owned client instance.
    pub(crate) fn with_timeout(mut self, timeout: Duration) -> Result<Self> {
        self.http = crate::http::client_builder()
            .timeout(timeout)
            .build()
            .context("failed to build HTTP client")?;
        Ok(self)
    }

    /// Resolve the current `X-Api-Key` value.
    fn api_key(&self) -> Result<String> {
        self.credential.api_key(&self.http)
    }

    /// Whether a LangSmith platform API base is configured for this host — always
    /// `true`, since `with_credential_and_platform` falls back to the host backend URL.
    pub fn platform_api_available(&self) -> bool {
        self.platform_api_url.is_some()
    }

    /// Perform a request against `base_url` and decode the JSON response (or
    /// `Null` when empty).
    fn request(
        &self,
        base_url: &str,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
    ) -> Result<Value> {
        self.request_inner(
            base_url,
            method,
            path,
            params,
            payload,
            DEFAULT_REQUEST_OPTIONS,
        )
    }

    /// Like [`Self::request`], but declares the LangSmith studio auth scheme
    /// (`x-auth-scheme: langsmith`).
    ///
    /// Requests to a deployed Agent Server pass through the app's auth stack. A
    /// managed-identity deployment installs a custom `authenticate` handler that
    /// requires an end-user bearer token, which the CLI (authenticating as the
    /// workspace via `X-Api-Key`) does not have. LangGraph only routes a request
    /// to its LangSmith studio auth backend — which validates the workspace key —
    /// when this header is present; otherwise the custom handler runs and rejects
    /// the call with a 401. Use this for control-plane calls against the Agent
    /// Server (e.g. cron reconciliation).
    fn request_agent_server(
        &self,
        base_url: &str,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
    ) -> Result<Value> {
        self.request_inner(
            base_url,
            method,
            path,
            params,
            payload,
            RequestOptions {
                studio_auth: true,
                ..DEFAULT_REQUEST_OPTIONS
            },
        )
    }

    /// Like [`Self::request`], but treats HTTP 404 as `Ok(None)`.
    ///
    /// Teardown paths are idempotent: a resource that is already gone is a
    /// success, not an error.
    fn request_optional(
        &self,
        base_url: &str,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
    ) -> Result<Option<Value>> {
        let url = format!("{base_url}{path}");
        let method_label = method.clone();
        let api_key = self.api_key()?;
        let (status, text) = send_json_request(
            &self.http,
            method,
            &url,
            &api_key,
            self.tenant_id.as_deref(),
            params,
            payload.as_ref(),
            false,
        )
        .with_context(|| format!("{method_label} {path} request failed"))?;

        if status == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        decode_json_body(&text, path).map(Some)
    }

    fn request_with_timeout(
        &self,
        base_url: &str,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
        timeout: Duration,
    ) -> Result<Value> {
        self.request_inner(
            base_url,
            method,
            path,
            params,
            payload,
            RequestOptions {
                timeout,
                ..DEFAULT_REQUEST_OPTIONS
            },
        )
    }

    fn request_inner(
        &self,
        base_url: &str,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
        options: RequestOptions,
    ) -> Result<Value> {
        // Custom timeout: build a one-off client rather than mutating the shared one.
        let owned_http = if options.timeout == REQUEST_TIMEOUT {
            None
        } else {
            Some(
                crate::http::client_builder()
                    .timeout(options.timeout)
                    .build()
                    .context("failed to build HTTP client")?,
            )
        };
        let http = owned_http.as_ref().unwrap_or(&self.http);

        let url = format!("{base_url}{path}");
        let method_label = method.clone();
        let api_key = self.api_key()?;
        let (status, text) = send_json_request(
            http,
            method,
            &url,
            &api_key,
            self.tenant_id.as_deref(),
            params,
            payload.as_ref(),
            options.studio_auth,
        )
        .with_context(|| format!("{method_label} {path} request failed"))?;

        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }

        decode_json_body(&text, path)
    }
}

/// Shared authenticated JSON request used by [`Client`] and [`ContextHubClient`].
#[expect(
    clippy::too_many_arguments,
    reason = "thin transport helper shared by both HTTP clients"
)]
pub(crate) fn send_json_request(
    http: &HttpClient,
    method: Method,
    url: &str,
    api_key: &str,
    tenant_id: Option<&str>,
    params: &[(&str, &str)],
    payload: Option<&Value>,
    studio_auth: bool,
) -> Result<(StatusCode, String)> {
    let needs_empty_body = payload.is_none() && method != Method::GET && method != Method::HEAD;
    let mut req = http
        .request(method, url)
        .header("X-Api-Key", api_key)
        .header("Accept", "application/json");
    if studio_auth {
        req = req.header("x-auth-scheme", "langsmith");
    }
    if let Some(tenant) = tenant_id {
        req = req.header(TENANT_HEADER, tenant);
    }
    if !params.is_empty() {
        req = req.query(params);
    }
    if let Some(body) = payload {
        req = req.json(body);
    } else if needs_empty_body {
        // Bodyless POST/PATCH requests still need a Content-Length: some
        // gateways (e.g. Google frontends) reject them with HTTP 411
        // otherwise. An empty body makes reqwest send `Content-Length: 0`.
        req = req.body(Vec::<u8>::new());
    }

    let response = req.send()?;
    let status = response.status();
    let text = response
        .text()
        .with_context(|| format!("failed to read response body from {url}"))?;
    Ok((status, text))
}

pub(crate) fn decode_json_body(text: &str, context: &str) -> Result<Value> {
    if text.is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(text).with_context(|| format!("failed to decode response from {context}"))
}

/// An unsuccessful HTTP response from a LangSmith API.
///
/// Carries the status next to the humanized `detail` so callers can react to a
/// specific server condition — `mda deploy` turns the host backend's
/// entitlement 403s into a plan upsell (see [`crate::deploy`]) — instead of
/// pattern-matching a message that has already been flattened to a string.
///
/// `Display` keeps the `{detail} (HTTP {code})` shape the CLI has always
/// printed, so every caller that only forwards the error reads the same as
/// before — bar a response with no body at all, which used to name the status
/// twice.
#[derive(Debug)]
pub struct ApiError {
    /// Status the API returned.
    pub status: StatusCode,
    /// The server's explanation, humanized by [`humanize_error_body`], falling
    /// back to the raw body and then to a stand-in when the response carried
    /// nothing to explain itself with.
    pub detail: String,
}

impl ApiError {
    pub(crate) fn from_response(status: StatusCode, text: &str) -> Self {
        let detail = humanize_error_body(text)
            .or_else(|| (!text.is_empty()).then(|| text.to_string()))
            // `Display` always prints the status, so this must not repeat it.
            .unwrap_or_else(|| "the server sent no detail".to_string());
        Self { status, detail }
    }
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} (HTTP {})", self.detail, self.status.as_u16())
    }
}

impl std::error::Error for ApiError {}

/// Parsed pieces of a host-backend style URL (scheme + hostname, no port/path).
#[derive(Debug, Clone, Copy)]
pub(crate) struct HostUrlParts<'a> {
    pub scheme: &'a str,
    /// Hostname without port or path (e.g. `eu.api.host.langchain.com`).
    pub hostname: &'a str,
    /// Original URL with a trailing slash trimmed.
    pub trimmed: &'a str,
}

/// Split a host-backend URL into scheme + hostname for sibling-service mapping.
pub(crate) fn parse_host_url(host_url: &str) -> HostUrlParts<'_> {
    let trimmed = host_url.trim_end_matches('/');
    let (scheme, after_scheme) = match trimmed.split_once("://") {
        Some((scheme, rest)) if !scheme.is_empty() => (scheme, rest),
        _ => ("https", trimmed),
    };
    let host_port = after_scheme.split('/').next().unwrap_or("");
    let hostname = host_port.split(':').next().unwrap_or("");
    HostUrlParts {
        scheme,
        hostname,
        trimmed,
    }
}

/// Derive the LangSmith REST API base URL (`https://api.smith.langchain.com`)
/// from the host backend API URL, preserving any regional prefix.
///
/// The host backend (`api.host.langchain.com`) and the LangSmith API
/// (`api.smith.langchain.com`) are sibling services that share a regional
/// prefix (`eu.api.host.langchain.com` ↔ `eu.api.smith.langchain.com`), so the
/// mapping is a straight `host.` → `smith.` substitution on the hostname.
/// Returns `None` for hosts that are not `*.host.langchain.com` (localhost,
/// custom/self-hosted backends), where the platform API location is unknown.
pub(crate) fn langsmith_api_base_url(host_url: &str) -> Option<String> {
    let parts = parse_host_url(host_url);
    const API_HOST_SUFFIX: &str = "host.langchain.com";
    if !parts.hostname.ends_with(API_HOST_SUFFIX) {
        return None;
    }
    let smith = parts
        .hostname
        .replacen("host.langchain.com", "smith.langchain.com", 1);
    Some(format!("{}://{smith}", parts.scheme))
}

/// Inverse of [`langsmith_api_base_url`]: platform API → host backend.
///
/// Only maps `api.smith.langchain.com` and `{region}.api.smith.langchain.com`
/// (not bare dashboard hosts like `smith.langchain.com`).
pub(crate) fn langsmith_host_backend_url(platform_url: &str) -> Option<String> {
    let parts = parse_host_url(platform_url);
    let is_api_smith = parts.hostname == "api.smith.langchain.com"
        || parts.hostname.ends_with(".api.smith.langchain.com");
    if !is_api_smith {
        return None;
    }
    let host = parts
        .hostname
        .replacen("smith.langchain.com", "host.langchain.com", 1);
    Some(format!("{}://{host}", parts.scheme))
}

/// Whether `host_backend_url` is the Deployment API mounted by a self-hosted
/// frontend.
///
/// API origins and hostnames are configurable for self-hosted installations,
/// so the resolved `/api-host` mount is the authoritative signal.
pub(crate) fn is_self_hosted_host_backend_url(host_backend_url: &str) -> bool {
    reqwest::Url::parse(host_backend_url)
        .is_ok_and(|url| url.path().trim_end_matches('/').rsplit('/').next() == Some("api-host"))
}

/// Derive the Deployment API base mounted by a self-hosted frontend.
pub(crate) fn self_hosted_host_backend_url(platform_url: &str) -> Option<String> {
    self_hosted_url_with_suffix(platform_url, "api-host")
}

/// Derive the public backend API base mounted by a self-hosted frontend.
pub(crate) fn self_hosted_public_api_base_url(platform_url: &str) -> Option<String> {
    self_hosted_url_with_suffix(platform_url, "api")
}

fn self_hosted_url_with_suffix(platform_url: &str, suffix: &str) -> Option<String> {
    let mut url = reqwest::Url::parse(platform_url).ok()?;
    if !matches!(url.scheme(), "http" | "https") {
        return None;
    }
    let path = url.path().trim_end_matches('/');
    let path = path.strip_suffix("/api/v1").unwrap_or(path);
    if path.rsplit('/').next() != Some(suffix) {
        url.set_path(&format!("{path}/{suffix}"));
    }
    url.set_query(None);
    url.set_fragment(None);
    Some(url.as_str().trim_end_matches('/').to_string())
}

/// Turn a host-backend error response body into a concise, human-readable
/// message, avoiding raw JSON in user-facing errors.
///
/// Handles FastAPI-style payloads where `detail` is a string, an array of
/// `{loc, msg}` validation errors, or an `{error, message}` envelope. Returns
/// `None` when the body has no recognizable detail (the caller falls back to
/// the raw text or status).
fn humanize_error_body(text: &str) -> Option<String> {
    let value: Value = serde_json::from_str(text).ok()?;
    let detail = value.get("detail")?;
    match detail {
        Value::String(message) => Some(message.clone()),
        Value::Array(items) => {
            let messages: Vec<String> = items.iter().filter_map(format_validation_item).collect();
            if messages.is_empty() {
                None
            } else {
                Some(messages.join("; "))
            }
        }
        Value::Object(fields) => fields
            .get("message")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| Some(detail.to_string())),
        other => Some(other.to_string()),
    }
}

/// Format a single FastAPI validation error into a concise, human-readable
/// message that surfaces the offending property path.
///
/// Well-known error `type`s are rewritten into friendlier phrasing (e.g.
/// `missing` → `Missing required field 'secrets'`); anything else falls back to
/// the server-provided `msg`, prefixed with the property path when available.
fn format_validation_item(item: &Value) -> Option<String> {
    let field = field_path(item);
    let error_type = item.get("type").and_then(Value::as_str).unwrap_or("");

    match error_type {
        "missing" | "value_error.missing" => Some(match &field {
            Some(field) => format!("Missing required field '{field}'"),
            None => "Missing a required field".to_string(),
        }),
        _ => {
            let message = item.get("msg").and_then(Value::as_str)?;
            Some(match &field {
                Some(field) => format!("Invalid value for '{field}': {message}"),
                None => message.to_string(),
            })
        }
    }
}

/// Extract the dotted property path from a validation error's `loc`, dropping
/// the leading `body`/`query` location prefix. Returns `None` when empty.
fn field_path(item: &Value) -> Option<String> {
    let mut path = String::new();
    let mut skipping_prefix = true;
    for part in item.get("loc").and_then(Value::as_array)? {
        let owned_number;
        let segment = match part {
            Value::String(segment) => segment.as_str(),
            Value::Number(index) => {
                owned_number = index.to_string();
                owned_number.as_str()
            }
            _ => continue,
        };
        if skipping_prefix && (segment == "body" || segment == "query") {
            continue;
        }
        skipping_prefix = false;
        if !path.is_empty() {
            path.push('.');
        }
        path.push_str(segment);
    }
    (!path.is_empty()).then_some(path)
}

/// Extract a required string field from a JSON object, erroring helpfully.
pub fn require_str(value: &Value, key: &str, context: &str) -> Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| anyhow!("{context}: response missing string field '{key}'"))
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    fn server(status: &str, body: &str) -> (String, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let response = format!(
            "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut bytes = [0; 4096];
            let n = stream.read(&mut bytes).unwrap();
            stream.write_all(response.as_bytes()).unwrap();
            String::from_utf8_lossy(&bytes[..n]).into_owned()
        });
        (base, handle)
    }

    #[test]
    fn pat_service_and_failed_lookups_report_only_verified_ids() {
        for (status, body, user, workspace) in [
            (
                "200 OK",
                r#"{"ls_user_id":"person","organization_id":"org","tenant_id":"workspace","user_email":"excluded"}"#,
                Some("person"),
                Some("workspace"),
            ),
            (
                "200 OK",
                r#"{"user_id":"service","organization_id":"org","tenant_id":"workspace"}"#,
                None,
                Some("workspace"),
            ),
            (
                "200 OK",
                r#"{"ls_user_id":"person","organization_id":"org","tenant_id":"other"}"#,
                None,
                None,
            ),
            ("401 Unauthorized", "{}", None, None),
            ("200 OK", "not json", None, None),
        ] {
            let (base, handle) = server(status, body);
            let client =
                Client::new_with_platform(&base, Some(&base), "test-key", Some("workspace".into()))
                    .unwrap();
            let identity = client.auth_identity().unwrap_or_default();
            assert_eq!(identity.user_id.as_deref(), user);
            assert_eq!(identity.workspace_id.as_deref(), workspace);
            assert_eq!(identity.org_id.as_deref(), workspace.map(|_| "org"));
            let request = handle.join().unwrap().to_lowercase();
            assert!(request.starts_with("get /auth/public "));
            assert!(request.contains("x-tenant-id: workspace"));
            let serialized = serde_json::to_string(&identity).unwrap();
            assert!(!serialized.contains("excluded"));
            assert!(!serialized.contains("test-key"));
        }
    }

    #[test]
    fn post_command_deployment_lookup_matches_exact_name_or_agent() {
        for agent_mode in [false, true] {
            let (base, handle) = server(
                "200 OK",
                r#"{"resources":[{"name":"agent-preview","id":"preview","is_preview":true},{"name":"agent","id":"deployment"}]}"#,
            );
            let client =
                Client::new_with_platform(&base, Some(&base), "test-key", Some("workspace".into()))
                    .unwrap();
            let agent = agent_mode.then(|| DeploymentAgent {
                agent_id: "agent".into(),
                environment: AgentEnvironment::Staging,
            });
            assert_eq!(
                crate::deploy::find_target_deployment(&client, "agent", agent.as_ref())
                    .unwrap()
                    .unwrap()["id"],
                "deployment"
            );
            let request = handle.join().unwrap();
            assert!(request.contains(if agent_mode {
                "agent_id=agent&agent_environment=staging"
            } else {
                "name_contains=agent"
            }));
        }
        let (base, handle) = server("200 OK", r#"{"resources":[]}"#);
        let client = Client::new_with_platform(&base, Some(&base), "test-key", None).unwrap();
        assert!(
            crate::deploy::find_target_deployment(&client, "deleted", None)
                .unwrap()
                .is_none()
        );
        handle.join().unwrap();
    }

    #[test]
    fn lookup_timeout_does_not_fail_the_command() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            let (_stream, _) = listener.accept().unwrap();
            thread::sleep(Duration::from_secs(2));
        });
        let client = Client::new_with_platform(&base, Some(&base), "test-key", None)
            .unwrap()
            .with_timeout(Duration::from_secs(1))
            .unwrap();
        let start = std::time::Instant::now();
        assert!(client.auth_identity().is_none());
        assert!(start.elapsed() < Duration::from_millis(1750));
        handle.join().unwrap();
    }
    use super::*;

    #[test]
    fn resolve_preferred_env_prefers_cli_override() {
        let mut env_file = BTreeMap::new();
        env_file.insert(API_KEY_ENV.to_string(), "from-env".to_string());
        assert_eq!(
            resolve_preferred_env(
                API_KEY_ENV,
                REMOVED_API_KEY_ENVS,
                Some("from-cli"),
                &env_file,
                |_| None,
                " instead",
            )
            .unwrap(),
            Some("from-cli".to_string())
        );
    }

    #[test]
    fn resolve_preferred_env_process_beats_env_file() {
        let mut env_file = BTreeMap::new();
        env_file.insert(API_KEY_ENV.to_string(), "from-dotenv".to_string());
        let process = BTreeMap::from([(API_KEY_ENV.to_string(), "from-process".to_string())]);
        assert_eq!(
            resolve_preferred_env(
                API_KEY_ENV,
                REMOVED_API_KEY_ENVS,
                None,
                &env_file,
                |key| process.get(key).cloned(),
                " instead",
            )
            .unwrap(),
            Some("from-process".to_string())
        );
    }

    #[test]
    fn shell_shadows_env_file_only_on_a_real_difference() {
        let env_file = BTreeMap::from([
            (
                "LANGSMITH_ENDPOINT".to_string(),
                "https://eu.example/".to_string(),
            ),
            ("LANGSMITH_PROJECT".to_string(), "  ".to_string()),
        ]);
        assert!(warn_if_shell_shadows_env_file(
            "LANGSMITH_ENDPOINT",
            "https://us.example",
            &env_file
        ));
        assert!(!warn_if_shell_shadows_env_file(
            "LANGSMITH_ENDPOINT",
            " https://eu.example ",
            &env_file
        ));
        // Blank or missing in `.env`: nothing is shadowed.
        assert!(!warn_if_shell_shadows_env_file(
            "LANGSMITH_PROJECT",
            "from-shell",
            &env_file
        ));
        assert!(!warn_if_shell_shadows_env_file(
            "LANGSMITH_WORKSPACE_ID",
            "from-shell",
            &env_file
        ));
    }

    #[test]
    fn lookup_env_falls_back_to_env_file_when_process_blank() {
        let env_file = BTreeMap::from([(API_KEY_ENV.to_string(), " from-dotenv ".to_string())]);
        let process = BTreeMap::from([(API_KEY_ENV.to_string(), "   ".to_string())]);
        assert_eq!(
            lookup_env_with_origin(API_KEY_ENV, &env_file, &mut |key| process.get(key).cloned()),
            Some(("from-dotenv".to_string(), EnvOrigin::EnvFile))
        );
        assert_eq!(
            lookup_env_with_origin(API_KEY_ENV, &BTreeMap::new(), &mut |_| Some(
                " from-shell ".to_string()
            )),
            Some(("from-shell".to_string(), EnvOrigin::Shell))
        );
    }

    #[test]
    fn resolve_preferred_env_allows_removed_alias_when_preferred_set() {
        let mut env_file = BTreeMap::new();
        env_file.insert(API_KEY_ENV.to_string(), " ls-key ".to_string());
        env_file.insert("LANGCHAIN_API_KEY".to_string(), "legacy".to_string());
        assert_eq!(
            resolve_preferred_env(
                API_KEY_ENV,
                REMOVED_API_KEY_ENVS,
                None,
                &env_file,
                |_| None,
                " instead (same value, renamed)",
            )
            .unwrap(),
            Some("ls-key".to_string())
        );
    }

    #[test]
    fn resolve_preferred_env_errors_on_removed_alias() {
        let mut env_file = BTreeMap::new();
        env_file.insert("LANGCHAIN_API_KEY".to_string(), "legacy".to_string());
        let err = resolve_preferred_env(
            API_KEY_ENV,
            REMOVED_API_KEY_ENVS,
            None,
            &env_file,
            |_| None,
            " instead (same value, renamed)",
        )
        .unwrap_err();
        assert!(err.to_string().contains("LANGCHAIN_API_KEY"));
        assert!(err.to_string().contains(API_KEY_ENV));
    }

    #[test]
    fn resolve_preferred_env_returns_none_when_unset() {
        assert_eq!(
            resolve_preferred_env(
                API_KEY_ENV,
                REMOVED_API_KEY_ENVS,
                None,
                &BTreeMap::new(),
                |_| None,
                " instead",
            )
            .unwrap(),
            None
        );
    }

    #[test]
    fn humanizes_missing_field_errors_with_path() {
        let body =
            r#"{"detail":[{"type":"missing","loc":["body","secrets"],"msg":"Field required"}]}"#;
        assert_eq!(
            humanize_error_body(body).as_deref(),
            Some("Missing required field 'secrets'")
        );
    }

    #[test]
    fn humanizes_nested_value_errors_with_path() {
        let body = r#"{"detail":[{"type":"string_type","loc":["body","source_config","deployment_type"],"msg":"Input should be a valid string"}]}"#;
        assert_eq!(
            humanize_error_body(body).as_deref(),
            Some(
                "Invalid value for 'source_config.deployment_type': Input should be a valid string"
            )
        );
    }

    #[test]
    fn joins_multiple_validation_errors() {
        let body = r#"{"detail":[{"type":"missing","loc":["body","name"],"msg":"Field required"},{"type":"missing","loc":["body","secrets"],"msg":"Field required"}]}"#;
        assert_eq!(
            humanize_error_body(body).as_deref(),
            Some("Missing required field 'name'; Missing required field 'secrets'")
        );
    }

    #[test]
    fn humanizes_string_detail() {
        let body = r#"{"detail":"Deployment not found"}"#;
        assert_eq!(
            humanize_error_body(body).as_deref(),
            Some("Deployment not found")
        );
    }

    #[test]
    fn humanizes_structured_error_detail() {
        let body = r#"{"detail":{"error":"FeatureDisabled","message":"Sandbox feature is not enabled for this organization"}}"#;
        assert_eq!(
            humanize_error_body(body).as_deref(),
            Some("Sandbox feature is not enabled for this organization")
        );
    }

    #[test]
    fn api_errors_read_as_detail_then_status() {
        let err = ApiError::from_response(
            StatusCode::FORBIDDEN,
            r#"{"detail":"Managed Deep Agents is not enabled for this organization"}"#,
        );
        assert_eq!(
            err.to_string(),
            "Managed Deep Agents is not enabled for this organization (HTTP 403)"
        );
    }

    #[test]
    fn api_errors_keep_an_unrecognized_body_verbatim() {
        let err = ApiError::from_response(StatusCode::BAD_GATEWAY, "upstream exploded");
        assert_eq!(err.detail, "upstream exploded");
        assert_eq!(err.to_string(), "upstream exploded (HTTP 502)");
    }

    #[test]
    fn api_errors_state_the_status_once_when_there_is_no_detail() {
        let err = ApiError::from_response(StatusCode::FORBIDDEN, "");
        assert_eq!(err.to_string(), "the server sent no detail (HTTP 403)");
    }

    #[test]
    fn returns_none_for_unrecognized_body() {
        assert_eq!(humanize_error_body("not json"), None);
        assert_eq!(humanize_error_body("{}"), None);
    }

    #[test]
    fn langsmith_api_url_maps_regional_and_prod_hosts() {
        assert_eq!(
            langsmith_api_base_url("https://api.host.langchain.com").as_deref(),
            Some("https://api.smith.langchain.com")
        );
        assert_eq!(
            langsmith_api_base_url("https://eu.api.host.langchain.com/").as_deref(),
            Some("https://eu.api.smith.langchain.com")
        );
        assert_eq!(
            langsmith_api_base_url("https://beta.api.host.langchain.com").as_deref(),
            Some("https://beta.api.smith.langchain.com")
        );
        assert_eq!(
            langsmith_api_base_url("https://dev.api.host.langchain.com").as_deref(),
            Some("https://dev.api.smith.langchain.com")
        );
    }

    #[test]
    fn langsmith_host_backend_url_maps_from_platform_api() {
        assert_eq!(
            langsmith_host_backend_url("https://api.smith.langchain.com").as_deref(),
            Some("https://api.host.langchain.com")
        );
        assert_eq!(
            langsmith_host_backend_url("https://eu.api.smith.langchain.com/").as_deref(),
            Some("https://eu.api.host.langchain.com")
        );
        assert_eq!(
            langsmith_host_backend_url("https://beta.api.smith.langchain.com").as_deref(),
            Some("https://beta.api.host.langchain.com")
        );
        // Dashboard hosts are not platform API origins.
        assert_eq!(
            langsmith_host_backend_url("https://smith.langchain.com"),
            None
        );
    }

    #[test]
    fn self_hosted_detection_uses_the_api_host_path_segment() {
        assert!(is_self_hosted_host_backend_url(
            "https://langsmith.example.com/api-host"
        ));
        assert!(is_self_hosted_host_backend_url(
            "https://langsmith.example.com/base/api-host/"
        ));
        assert!(!is_self_hosted_host_backend_url(
            "https://api.host.langchain.com"
        ));
        assert!(!is_self_hosted_host_backend_url(
            "https://langsmith.example.com/api-hosted"
        ));
    }

    #[test]
    fn self_hosted_url_derivation_preserves_the_installation_base_path() {
        assert_eq!(
            self_hosted_host_backend_url("https://langsmith.example.com/base/api/v1?x=1#y")
                .as_deref(),
            Some("https://langsmith.example.com/base/api-host")
        );
        assert_eq!(
            self_hosted_public_api_base_url("https://langsmith.example.com/base/api/v1?x=1#y")
                .as_deref(),
            Some("https://langsmith.example.com/base/api")
        );
    }

    #[test]
    fn langsmith_api_url_is_none_for_non_langchain_hosts() {
        assert_eq!(langsmith_api_base_url("http://localhost:8080"), None);
        assert_eq!(langsmith_api_base_url("https://example.com"), None);
    }

    #[test]
    fn platform_api_availability_follows_host() {
        let prod = Client::new("https://api.host.langchain.com", "k", None).unwrap();
        assert!(prod.platform_api_available());
        assert_eq!(
            prod.platform_api_url.as_deref(),
            Some("https://api.smith.langchain.com")
        );
        let local = Client::new("http://localhost:1980", "k", None).unwrap();
        assert!(local.platform_api_available());
        assert_eq!(
            local.platform_api_url.as_deref(),
            Some("http://localhost:1980")
        );
        let split = Client::new_with_platform(
            "http://localhost:8300",
            Some("http://localhost:1980"),
            "k",
            None,
        )
        .unwrap();
        assert_eq!(split.host_backend_url, "http://localhost:8300");
        assert_eq!(
            split.platform_api_url.as_deref(),
            Some("http://localhost:1980")
        );
    }
}
