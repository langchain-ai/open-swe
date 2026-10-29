//! Pure helpers for the `deploy` command, ported from the langgraph CLI's
//! `deploy-helpers.mts`. Kept free of I/O so they can be unit-tested.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::Error;
use serde_json::Value;

use crate::langsmith::{
    langsmith_api_base_url, langsmith_host_backend_url, lookup_env, parse_host_url,
    resolve_login_api_url, self_hosted_host_backend_url, Secret,
};

/// The Agent Server URL channel webhooks point at: absolute HTTPS without
/// credentials, query, or fragment.
pub(crate) fn validated_agent_server_url(raw: &str) -> anyhow::Result<reqwest::Url> {
    use anyhow::Context;
    let url = reqwest::Url::parse(raw).context("Agent Server URL is invalid")?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.cannot_be_a_base()
    {
        anyhow::bail!(
            "Agent Server URL must be absolute HTTPS without credentials, query, or fragment"
        );
    }
    Ok(url)
}

/// Default host backend API URL.
pub const DEFAULT_HOST_URL: &str = "https://api.host.langchain.com";

/// Local langchainplus host-backend (Deployments API `/v2/deployments`).
pub const DEFAULT_LOCAL_HOST_BACKEND_URL: &str = "http://localhost:8300";

/// Env var for the LangSmith platform API (Fleet, login, secrets).
///
/// Also selects the matching host backend for SaaS (`api.smith` ↔ `api.host`)
/// and the local nginx→`:8300` split when set to `http://localhost:1980`.
pub const LANGSMITH_ENDPOINT_ENV: &str = "LANGSMITH_ENDPOINT";

/// Env var that scopes deploy + runtime LangSmith clients to a workspace.
pub const WORKSPACE_ID_ENV: &str = "LANGSMITH_WORKSPACE_ID";

/// Deploy-owned full endpoint for the LangSmith managed-sandbox API.
pub const MDA_SANDBOX_API_ENDPOINT_ENV: &str = "MDA_SANDBOX_API_ENDPOINT";

/// Removed env names that previously meant the same id as [`WORKSPACE_ID_ENV`].
pub const REMOVED_WORKSPACE_ID_ENVS: &[&str] = &[
    "LANGCHAIN_WORKSPACE_ID",
    "LANGSMITH_TENANT_ID",
    "LANGCHAIN_TENANT_ID",
];

/// Revision statuses that end status polling (success, failure, or external
/// interruption).
pub fn is_terminal_status(status: &str) -> bool {
    matches!(
        status,
        "DEPLOYED" | "CREATE_FAILED" | "BUILD_FAILED" | "DEPLOY_FAILED" | "SKIPPED" | "INTERRUPTED"
    )
}

/// Statuses that indicate the deployment failed.
pub fn is_failed_status(status: &str) -> bool {
    matches!(status, "CREATE_FAILED" | "BUILD_FAILED" | "DEPLOY_FAILED")
}

/// Whether a revision was interrupted/cancelled from another interface (e.g.
/// the LangSmith UI) rather than building to a success or failure here.
pub fn is_interrupted_status(status: &str) -> bool {
    matches!(status, "INTERRUPTED")
}

/// Env vars that must never be forwarded as deployment secrets (auth, runtime
/// wiring, reserved platform configuration). Mirrors `RESERVED_ENV_VARS`.
pub const RESERVED_ENV_VARS: &[&str] = &[
    crate::langsmith::BUILD_ARTIFACTS_TOKEN_ENV,
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_PROJECT",
    "LANGSMITH_LANGGRAPH_GIT_REPO",
    "LANGGRAPH_GIT_REPO_PATH",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_WORKSPACE_ID",
    "LANGGRAPH_HOST_API_KEY",
    "LANGSMITH_CONTROL_PLANE_API_KEY",
    "POSTGRES_URI",
    "POSTGRES_PASSWORD",
    "DATABASE_URI",
    "LANGSMITH_LANGGRAPH_GIT_REF",
    "LANGSMITH_LANGGRAPH_GIT_REF_SHA",
    "LANGGRAPH_AUTH_TYPE",
    "LANGSMITH_AUTH_ENDPOINT",
    "LANGSMITH_TENANT_ID",
    "LANGSMITH_AUTH_VERIFY_TENANT_ID",
    "LANGSMITH_HOST_PROJECT_ID",
    "LANGSMITH_HOST_PROJECT_NAME",
    "LANGSMITH_HOST_REVISION_ID",
    "LOG_JSON",
    "LOG_DICT_TRACEBACKS",
    "REDIS_URI",
    "LANGCHAIN_CALLBACKS_BACKGROUND",
    "DD_TRACE_PSYCOPG_ENABLED",
    "DD_TRACE_REDIS_ENABLED",
    "LANGSMITH_DEPLOYMENT_NAME",
    "LANGGRAPH_CLOUD_LICENSE_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_ENDPOINT",
    "POSTGRES_URI_CUSTOM",
    "REDIS_URI_CUSTOM",
    "PATH",
    "PORT",
    "MOUNT_PREFIX",
    "LSD_ENV",
    "LSD_DD_API_KEY",
    "LSD_DD_ENDPOINT",
    "LSD_DEPLOYMENT_TYPE",
    // Deploy-owned, per-revision credential for internal MDA loopback calls.
    "MDA_HANDOFF_SECRET",
    // Deploy-owned: the runtime names its sandboxes after this, and `mda delete`
    // looks them up by the real deployment name.
    "MDA_DEPLOYMENT_NAME",
    // Deploy-owned: baked recipe snapshot name the runtime clones for new threads.
    "MDA_SANDBOX_RECIPE_SNAPSHOT",
    // Deploy-owned: runtime sandbox calls must use the same platform API surface
    // as deploy preflight, rather than deriving one from the tracing endpoint.
    "MDA_SANDBOX_API_ENDPOINT",
    // CLI-only: point channel app setup at another Agent Auth. The key belongs
    // to that LangSmith, never to the deployment.
    "MDA_AGENT_AUTH_URL",
    "MDA_AGENT_AUTH_API_KEY",
    "MDA_AGENT_AUTH_TENANT_ID",
    // CLI-only: Agent Auth's OAuth callback, which a new Linear app lists, when
    // the CLI cannot derive it (the local stack sets it).
    "MDA_AGENT_AUTH_OAUTH_CALLBACK_URL",
    // CLI-only: where a new GitHub App is created, or an existing App with its
    // webhook secret and installation, without a prompt. Agent Auth keeps the
    // App; the deployment never gets them as env vars (`GH_WEBHOOK_SECRET` is
    // the runtime's own override and is uploaded).
    "MDA_GITHUB_APP_OWNER",
    "MDA_GITHUB_APP_ID",
    "MDA_GITHUB_APP_PRIVATE_KEY_FILE",
    "MDA_GITHUB_APP_INSTALLATION_ID",
    "MDA_GITHUB_WEBHOOK_SECRET",
    // CLI-only: a Linear app's credentials instead of the prompts. Agent Auth
    // keeps them; the deployment never gets them as env vars.
    "MDA_LINEAR_CLIENT_ID",
    "MDA_LINEAR_CLIENT_SECRET",
    "MDA_LINEAR_WEBHOOK_SECRET",
    "MDA_LINEAR_WORKSPACE",
    // CLI-only: a brought GitHub App's webhook URL that stays where it is.
    "MDA_GITHUB_WEBHOOK_ELSEWHERE",
    // CLI-only: the user's own Slack app's credentials instead of the prompts.
    // Trigger Server keeps them; the deployment never gets them as env vars.
    "MDA_SLACK_BOT_TOKEN",
    "MDA_SLACK_SIGNING_SECRET",
];

/// Convert env vars into deployment secrets, dropping reserved and empty values.
pub fn secrets_from_env(env_vars: &BTreeMap<String, String>) -> Vec<Secret> {
    env_vars
        .iter()
        .filter(|(name, value)| !value.is_empty() && !RESERVED_ENV_VARS.contains(&name.as_str()))
        .map(|(name, value)| Secret {
            name: name.clone(),
            value: value.clone(),
        })
        .collect()
}

/// Deploy-owned credential for internal MDA loopback calls.
pub const MDA_HANDOFF_SECRET_ENV: &str = "MDA_HANDOFF_SECRET";

/// Inject a generated internal handoff credential, replacing any stale value.
pub fn set_mda_handoff_secret(secrets: &mut Vec<Secret>, value: &str) {
    if let Some(existing) = secrets
        .iter_mut()
        .find(|secret| secret.name == MDA_HANDOFF_SECRET_ENV)
    {
        existing.value = value.to_string();
        return;
    }
    secrets.push(Secret {
        name: MDA_HANDOFF_SECRET_ENV.to_string(),
        value: value.to_string(),
    });
}

/// Set `LANGSMITH_WORKSPACE_ID` on the deployment to the resolved workspace id.
///
/// The runtime must be scoped to the same workspace the deployment is created
/// in, so the resolved id replaces any value carried over from `.env` —
/// otherwise `--workspace-id` would move the deployment while leaving runtime
/// sandbox / SDK calls pointed at the old workspace.
pub fn set_workspace_id_secret(secrets: &mut Vec<Secret>, workspace_id: &str) {
    if workspace_id.is_empty() {
        return;
    }
    if let Some(existing) = secrets
        .iter_mut()
        .find(|secret| secret.name == WORKSPACE_ID_ENV)
    {
        existing.value = workspace_id.to_string();
        return;
    }
    secrets.push(Secret {
        name: WORKSPACE_ID_ENV.to_string(),
        value: workspace_id.to_string(),
    });
}

/// Point runtime sandbox clients at the same platform API used by deploy.
pub fn ensure_sandbox_api_endpoint_secret(secrets: &mut Vec<Secret>, platform_api_url: &str) {
    let Some(value) = sandbox_api_endpoint(platform_api_url) else {
        return;
    };
    secrets.retain(|secret| secret.name != MDA_SANDBOX_API_ENDPOINT_ENV);
    secrets.push(Secret {
        name: MDA_SANDBOX_API_ENDPOINT_ENV.to_string(),
        value,
    });
}

/// Build the full managed-sandbox endpoint from a resolved platform API URL.
pub fn sandbox_api_endpoint(platform_api_url: &str) -> Option<String> {
    let platform_api_url = platform_api_url.trim().trim_end_matches('/');
    (!platform_api_url.is_empty()).then(|| format!("{platform_api_url}/v2/sandboxes"))
}

/// Deploy-time deployment name handed to the runtime for resource attribution.
pub const MDA_DEPLOYMENT_NAME_ENV: &str = "MDA_DEPLOYMENT_NAME";

/// Deploy-owned recipe snapshot name the runtime clones for new thread sandboxes.
pub const MDA_SANDBOX_RECIPE_SNAPSHOT_ENV: &str = "MDA_SANDBOX_RECIPE_SNAPSHOT";

/// Segment between the deployment name and the recipe digest in a baked
/// snapshot name (`{deployment}--setup-{digest}`).
pub const RECIPE_SETUP_SEGMENT: &str = "setup-";

/// Hex characters kept from the SHA-256 of setup bytes + bake bases.
pub const RECIPE_DIGEST_LENGTH: usize = 16;

/// LangGraph graphs / assistant id (`langgraph.json` graphs key). Channel
/// ingress (Slack, GitHub) reads this so runs target the compiled graph instead
/// of the hardcoded `"agent"` fallback.
pub const MDA_ASSISTANT_ID_ENV: &str = "MDA_ASSISTANT_ID";
/// The LangSmith UI of the environment the deployment runs in, for links that
/// channels post into LangSmith Studio.
pub const MDA_LANGSMITH_URL_ENV: &str = "MDA_LANGSMITH_URL";

/// Separator between the deployment name and the identity digest in a managed
/// sandbox name.
///
/// Doubled so a deployment name can never be a prefix-match for another one:
/// `agent--<digest>` is not matched by the `agent-staging--` prefix. Mirrors the
/// `--u--` / `--t--` convention used for Context Hub child repos.
pub const SANDBOX_NAME_SEPARATOR: &str = "--";

/// Inject `MDA_DEPLOYMENT_NAME` so the runtime can name the sandboxes it
/// creates after the deployment that owns them.
///
/// The platform reserves `LANGSMITH_DEPLOYMENT_NAME`, but nothing guarantees it
/// is present in the container, and it is not forwarded from `.env`. Passing the
/// deploy-time name explicitly is what lets `mda delete` find those sandboxes
/// again.
///
/// Deploy owns this value and overwrites anything already in `secrets`. A stale
/// or copied `.env` entry would otherwise prefix this deployment's sandboxes
/// with another deployment's name, putting them out of reach of its own
/// `mda delete` and inside the blast radius of the other one's.
pub fn ensure_deployment_name_secret(secrets: &mut Vec<Secret>, name: &str) {
    if name.is_empty() {
        return;
    }
    secrets.retain(|secret| secret.name != MDA_DEPLOYMENT_NAME_ENV);
    secrets.push(Secret {
        name: MDA_DEPLOYMENT_NAME_ENV.to_string(),
        value: name.to_string(),
    });
}

/// Inject [`MDA_SANDBOX_RECIPE_SNAPSHOT_ENV`] so the runtime clones the baked
/// recipe for new threads.
///
/// Same ownership rule as [`ensure_deployment_name_secret`]: deploy/dev owns the
/// value and overwrites a stale `.env` copy.
pub fn ensure_recipe_snapshot_secret(secrets: &mut Vec<Secret>, recipe_name: &str) {
    if recipe_name.is_empty() {
        return;
    }
    secrets.retain(|secret| secret.name != MDA_SANDBOX_RECIPE_SNAPSHOT_ENV);
    secrets.push(Secret {
        name: MDA_SANDBOX_RECIPE_SNAPSHOT_ENV.to_string(),
        value: recipe_name.to_string(),
    });
}

/// Drop a previously injected recipe secret when the project has no sandbox.
pub fn clear_recipe_snapshot_secret(secrets: &mut Vec<Secret>) {
    secrets.retain(|secret| secret.name != MDA_SANDBOX_RECIPE_SNAPSHOT_ENV);
}

/// Host's max length for sandbox and snapshot names.
pub const HOST_SANDBOX_NAME_LIMIT: usize = 63;

/// Deployment component length that keeps `{component}--setup-{digest}` ≤ 63.
const MAX_RECIPE_DEPLOYMENT_COMPONENT_LENGTH: usize = HOST_SANDBOX_NAME_LIMIT
    - SANDBOX_NAME_SEPARATOR.len()
    - RECIPE_SETUP_SEGMENT.len()
    - RECIPE_DIGEST_LENGTH;

/// Deterministic recipe snapshot name for a deployment + digest.
///
/// Long deployment names are shortened the same way thread sandbox names are, so
/// `{component}--setup-{16}` stays within Host's 63-character limit.
pub fn recipe_snapshot_name(deployment: &str, digest: &str) -> String {
    format!(
        "{}{SANDBOX_NAME_SEPARATOR}{RECIPE_SETUP_SEGMENT}{digest}",
        sandbox_deployment_component(deployment, MAX_RECIPE_DEPLOYMENT_COMPONENT_LENGTH)
    )
}

/// Prefix shared by every recipe snapshot a deployment owns.
pub fn recipe_snapshot_prefix(deployment: &str) -> String {
    format!(
        "{}{SANDBOX_NAME_SEPARATOR}{RECIPE_SETUP_SEGMENT}",
        sandbox_deployment_component(deployment, MAX_RECIPE_DEPLOYMENT_COMPONENT_LENGTH)
    )
}

/// How the runtime must reference the baked recipe, given the tags Host assigned.
///
/// Host resolves a bare snapshot name as `{name}:latest`. A snapshot built from a
/// Docker image inherits the *image's* tag instead (`…--setup-abc` tagged
/// `0.1.1`), so staging the bare name makes every thread create fail with
/// `snapshot "…:latest" not found`. Pin the assigned tag whenever `latest` is
/// absent; a captured snapshot that already carries `latest` keeps the bare name.
pub fn recipe_snapshot_reference(name: &str, tags: &[String]) -> String {
    if tags.iter().any(|tag| tag == "latest") {
        return name.to_string();
    }
    match tags.iter().find(|tag| !tag.trim().is_empty()) {
        Some(tag) => format!("{name}:{tag}"),
        None => name.to_string(),
    }
}

/// Deployment-owned Host registry name for private Docker pulls.
pub fn sandbox_registry_name(deployment: &str) -> String {
    format!("{deployment}{SANDBOX_NAME_SEPARATOR}registry")
}

/// Hex digest (truncated to [`RECIPE_DIGEST_LENGTH`]) of setup bytes and optional
/// bake bases. Same inputs always produce the same digest.
///
/// `registry` and `fs_capacity_bytes` are only hashed when present so existing
/// digests stay stable for projects that do not set them.
pub fn recipe_digest(
    setup_bytes: &[u8],
    snapshot_name: Option<&str>,
    snapshot_id: Option<&str>,
    docker_image: Option<&str>,
    registry: Option<&str>,
    fs_capacity_bytes: Option<u64>,
) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(setup_bytes);
    hasher.update([0]);
    hasher.update(snapshot_name.unwrap_or("").as_bytes());
    hasher.update([0]);
    hasher.update(snapshot_id.unwrap_or("").as_bytes());
    hasher.update([0]);
    hasher.update(docker_image.unwrap_or("").as_bytes());
    if let Some(registry) = registry {
        hasher.update([0]);
        hasher.update(registry.as_bytes());
    }
    if let Some(capacity) = fs_capacity_bytes {
        hasher.update(b"\0fs_capacity_bytes=");
        hasher.update(capacity.to_string().as_bytes());
    }
    // digest 0.11's output array no longer implements `LowerHex`; encode bytes.
    let digest = hasher.finalize();
    let full: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    full[..RECIPE_DIGEST_LENGTH].to_string()
}

/// Whether `name` is a recipe snapshot owned by `deployment`.
///
/// Requires the `{deployment}--setup-` prefix and a non-empty digest suffix so a
/// neighbour deployment whose name merely shares a prefix is not swept up.
pub fn is_deployment_recipe_snapshot(name: &str, deployment: &str) -> bool {
    let Some(digest) = name.strip_prefix(&recipe_snapshot_prefix(deployment)) else {
        return false;
    };
    !digest.is_empty() && !digest.contains(SANDBOX_NAME_SEPARATOR)
}

/// Temporary base snapshots a bake creates before `setup.sh` runs:
/// `dockerbase` (built from `dockerImage`) and `sourcebase` (captured from an
/// existing sandbox).
pub const INTERMEDIATE_SNAPSHOT_KINDS: [&str; 2] = ["dockerbase", "sourcebase"];

/// Name of a temporary bake base snapshot (`{deployment}--{kind}-{digest}`).
///
/// Long deployment names are shortened like recipe names so the result stays
/// within Host's 63-character limit and remains attributable to one deployment.
pub fn intermediate_snapshot_name(deployment: &str, kind: &str, digest: &str) -> String {
    format!("{}{digest}", intermediate_snapshot_prefix(deployment, kind))
}

fn intermediate_snapshot_prefix(deployment: &str, kind: &str) -> String {
    let max_component = HOST_SANDBOX_NAME_LIMIT
        - SANDBOX_NAME_SEPARATOR.len()
        - kind.len()
        - 1
        - RECIPE_DIGEST_LENGTH;
    format!(
        "{}{SANDBOX_NAME_SEPARATOR}{kind}-",
        sandbox_deployment_component(deployment, max_component)
    )
}

/// Whether `name` is a temporary bake base snapshot owned by `deployment`.
///
/// Requires an exact recipe digest after the prefix so a neighbour deployment
/// whose name merely shares a prefix is not swept up.
pub fn is_deployment_intermediate_snapshot(name: &str, deployment: &str) -> bool {
    INTERMEDIATE_SNAPSHOT_KINDS.iter().any(|kind| {
        name.strip_prefix(&intermediate_snapshot_prefix(deployment, kind))
            .is_some_and(is_recipe_digest)
    })
}

fn is_recipe_digest(value: &str) -> bool {
    value.len() == RECIPE_DIGEST_LENGTH
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// Whether `name` is any bake snapshot (recipe or temporary base) owned by
/// `deployment`.
pub fn is_deployment_bake_snapshot(name: &str, deployment: &str) -> bool {
    is_deployment_recipe_snapshot(name, deployment)
        || is_deployment_intermediate_snapshot(name, deployment)
}

/// Prefix that lists every bake snapshot `deployment` owns in one request.
///
/// Neighbour deployments can share it; filter results with
/// [`is_deployment_bake_snapshot`].
pub fn bake_snapshot_list_prefix(deployment: &str) -> String {
    INTERMEDIATE_SNAPSHOT_KINDS
        .iter()
        .map(|kind| intermediate_snapshot_prefix(deployment, kind))
        .fold(recipe_snapshot_prefix(deployment), |shared, prefix| {
            common_prefix(&shared, &prefix).to_string()
        })
}

fn common_prefix<'a>(left: &'a str, right: &str) -> &'a str {
    let end = left
        .char_indices()
        .zip(right.chars())
        .take_while(|((_, a), b)| a == b)
        .last()
        .map_or(0, |((index, ch), _)| index + ch.len_utf8());
    &left[..end]
}

/// Inject [`MDA_ASSISTANT_ID_ENV`] so channel ingress invokes the compiled graph.
///
/// Same ownership rule as [`ensure_deployment_name_secret`]: deploy/dev owns the
/// value and overwrites a stale `.env` copy.
pub fn ensure_assistant_id_secret(secrets: &mut Vec<Secret>, assistant_id: &str) {
    if assistant_id.is_empty() {
        return;
    }
    secrets.retain(|secret| secret.name != MDA_ASSISTANT_ID_ENV);
    secrets.push(Secret {
        name: MDA_ASSISTANT_ID_ENV.to_string(),
        value: assistant_id.to_string(),
    });
}

/// Inject [`MDA_LANGSMITH_URL_ENV`] so channels link to the deployment's own
/// LangSmith environment, the same one deploy prints as the dashboard.
///
/// Same ownership rule as [`ensure_deployment_name_secret`]: deploy owns the
/// value and overwrites a stale `.env` copy.
pub fn ensure_langsmith_url_secret(secrets: &mut Vec<Secret>, url: &str) {
    if url.is_empty() {
        return;
    }
    secrets.retain(|secret| secret.name != MDA_LANGSMITH_URL_ENV);
    secrets.push(Secret {
        name: MDA_LANGSMITH_URL_ENV.to_string(),
        value: url.to_string(),
    });
}

/// Prefix shared by every managed sandbox a deployment creates.
///
/// This is the stable primary-name prefix. Deployments whose full primary name
/// fits Host's limit keep this prefix across runtime upgrades.
pub fn sandbox_name_prefix(deployment_name: &str) -> String {
    format!(
        "{}{SANDBOX_NAME_SEPARATOR}",
        sandbox_deployment_component(deployment_name, MAX_PRIMARY_DEPLOYMENT_COMPONENT_LENGTH)
    )
}

/// Prefixes `mda delete` must query for primary and shortened fallback names.
///
/// Collision fallback names reserve nine characters for `-<8 hex>`, so their
/// deployment component may be shorter than the stable primary component.
pub fn sandbox_name_prefixes(deployment_name: &str) -> Vec<String> {
    let primary = sandbox_name_prefix(deployment_name);
    let fallback = format!(
        "{}{SANDBOX_NAME_SEPARATOR}",
        sandbox_deployment_component(deployment_name, MAX_FALLBACK_DEPLOYMENT_COMPONENT_LENGTH)
    );
    if fallback == primary {
        vec![primary]
    } else {
        vec![primary, fallback]
    }
}

/// Hex characters in the identity digest the runtimes append to a sandbox name.
const SANDBOX_DIGEST_LENGTH: usize = 12;

/// Hex characters in the collision-fallback suffix the runtimes add when a
/// deterministic sandbox name is already taken.
const FALLBACK_SUFFIX_LENGTH: usize = 8;

/// Deployment component lengths that keep primary and fallback names within
/// Host's 63-character limit.
const MAX_PRIMARY_DEPLOYMENT_COMPONENT_LENGTH: usize = 49;
const MAX_FALLBACK_DEPLOYMENT_COMPONENT_LENGTH: usize = 40;

/// Hex characters used to distinguish truncated deployment names.
const SANDBOX_DEPLOYMENT_DIGEST_LENGTH: usize = 8;

pub(crate) fn sandbox_deployment_component(deployment_name: &str, max_length: usize) -> String {
    use sha2::{Digest, Sha256};

    if deployment_name.chars().count() <= max_length {
        return deployment_name.to_string();
    }
    let digest = Sha256::digest(deployment_name.as_bytes());
    let full_digest: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    let prefix_length = max_length - SANDBOX_DEPLOYMENT_DIGEST_LENGTH - 1;
    let prefix: String = deployment_name.chars().take(prefix_length).collect();
    format!(
        "{prefix}-{}",
        &full_digest[..SANDBOX_DEPLOYMENT_DIGEST_LENGTH]
    )
}

/// Whether `sandbox_name` is one this deployment's runtime created.
///
/// The prefix alone is not conclusive: a deployment may itself be named with a
/// `--` in it, so `my-agent--` would otherwise sweep up sandboxes belonging to a
/// separate `my-agent--foo` deployment. Requiring the remainder to be exactly
/// the digest the runtimes generate (optionally plus their collision-fallback
/// suffix) keeps teardown from deleting a neighbour's sandbox.
pub fn is_deployment_sandbox(sandbox_name: &str, deployment_name: &str) -> bool {
    sandbox_name_prefixes(deployment_name)
        .iter()
        .filter_map(|prefix| sandbox_name.strip_prefix(prefix))
        .any(is_managed_sandbox_suffix)
}

fn is_managed_sandbox_suffix(suffix: &str) -> bool {
    let (digest, fallback) = match suffix.split_once('-') {
        Some((digest, fallback)) => (digest, Some(fallback)),
        None => (suffix, None),
    };
    if digest.len() != SANDBOX_DIGEST_LENGTH || !is_lowercase_hex(digest) {
        return false;
    }
    match fallback {
        None => true,
        Some(value) => value.len() == FALLBACK_SUFFIX_LENGTH && is_lowercase_hex(value),
    }
}

fn is_lowercase_hex(value: &str) -> bool {
    !value.is_empty()
        && value
            .chars()
            .all(|ch| ch.is_ascii_digit() || ('a'..='f').contains(&ch))
}

/// LangGraph Agent Server CORS allowlist (exact origins, comma-separated).
pub const CORS_ALLOW_ORIGINS_ENV: &str = "CORS_ALLOW_ORIGINS";

/// Public browser origin used for Connect redirects and CORS derivation.
pub const MDA_PUBLIC_APP_URL_ENV: &str = "MDA_PUBLIC_APP_URL";

/// Public Agent Server base URL used for Slack OAuth `redirect_uri`.
pub const MDA_PUBLIC_API_URL_ENV: &str = "MDA_PUBLIC_API_URL";

/// Outcome of reading `MDA_PUBLIC_API_URL` for Events / Connect URLs.
#[derive(Debug, PartialEq, Eq)]
pub enum PublicApiUrl {
    Https(String),
    Missing,
    NotHttps(String),
}

/// Read and classify `MDA_PUBLIC_API_URL` from a merged env map.
pub fn resolve_https_public_api_url(env_vars: &BTreeMap<String, String>) -> PublicApiUrl {
    let Some(raw) = env_vars
        .get(MDA_PUBLIC_API_URL_ENV)
        .map(|value| value.trim().trim_end_matches('/'))
        .filter(|value| !value.is_empty())
        .map(str::to_string)
    else {
        return PublicApiUrl::Missing;
    };
    if raw.starts_with("https://") {
        PublicApiUrl::Https(raw)
    } else {
        PublicApiUrl::NotHttps(raw)
    }
}

/// Resolve host-backend and platform (Fleet) API bases from `LANGSMITH_ENDPOINT`.
///
/// One env var is enough for every SaaS environment: set the platform API
/// (`https://eu.api.smith.langchain.com`, `https://beta.api.smith.langchain.com`,
/// …) and the matching host backend is derived (`…api.host…`).
///
/// Local LangSmith serves Deployments on host-backend `:8300` and Fleet on
/// nginx `:1980`. `LANGSMITH_ENDPOINT=http://localhost:1980` therefore pairs
/// with [`DEFAULT_LOCAL_HOST_BACKEND_URL`].
///
/// Self-hosted LangSmith serves the platform API at the configured origin and
/// the Deployment API below `/api-host` on that origin.
pub fn resolve_langsmith_api_bases(env_vars: &BTreeMap<String, String>) -> (String, String) {
    let endpoint = lookup_env(LANGSMITH_ENDPOINT_ENV, env_vars, &mut |key| {
        std::env::var(key).ok()
    })
    .map(|value| value.trim_end_matches('/').to_string());

    match endpoint.as_deref() {
        Some(endpoint) if is_local_langsmith_nginx(endpoint) => (
            DEFAULT_LOCAL_HOST_BACKEND_URL.to_string(),
            endpoint.trim_end_matches('/').to_string(),
        ),
        Some(endpoint) if endpoint.contains("host.langchain.com") => {
            // Allow pointing at the host backend directly; derive platform.
            let host = endpoint.trim_end_matches('/').to_string();
            let platform = langsmith_api_base_url(&host).unwrap_or_else(|| host.clone());
            (host, platform)
        }
        Some(endpoint) => {
            let platform = endpoint.trim_end_matches('/').to_string();
            let host = langsmith_host_backend_url(&platform)
                .or_else(|| self_hosted_host_backend_url(&platform))
                .unwrap_or_else(|| platform.clone());
            (host, platform)
        }
        None => {
            let host = DEFAULT_HOST_URL.to_string();
            let platform = langsmith_api_base_url(&host).unwrap_or_else(|| host.clone());
            (host, platform)
        }
    }
}

/// Platform API origin for device login and Fleet.
///
/// Prefers process env then project `.env` for `LANGSMITH_ENDPOINT`, then
/// derives from the host backend (`*.api.host.langchain.com` →
/// `*.api.smith.langchain.com`).
pub fn resolve_platform_api_url(host_url: &str, env_vars: &BTreeMap<String, String>) -> String {
    resolve_platform_api_url_with(host_url, env_vars, |key| std::env::var(key).ok())
}

fn resolve_platform_api_url_with(
    host_url: &str,
    env_vars: &BTreeMap<String, String>,
    process_env: impl Fn(&str) -> Option<String>,
) -> String {
    resolve_login_api_url(host_url, |key| {
        lookup_env(key, env_vars, &mut |key| process_env(key))
    })
}

/// Local `poe run-auth` nginx that fronts Fleet but not `/v2/deployments`.
fn is_local_langsmith_nginx(endpoint: &str) -> bool {
    let parts = parse_host_url(endpoint);
    let loopback = matches!(parts.hostname, "localhost" | "127.0.0.1" | "::1" | "[::1]");
    if !loopback {
        return false;
    }
    // parse_host_url strips the port from hostname; recover it from the authority.
    let authority = parts
        .trimmed
        .strip_prefix(&format!("{}://", parts.scheme))
        .unwrap_or(parts.trimmed)
        .split('/')
        .next()
        .unwrap_or("");
    authority.ends_with(":1980")
}

/// Optional comma-separated extra browser origins for CORS.
pub const MDA_CORS_ORIGINS_ENV: &str = "MDA_CORS_ORIGINS";

/// Inject `MDA_PUBLIC_API_URL` from the known Agent Server runtime URL.
///
/// Slack OAuth `redirect_uri` must be the public deployment URL; the ASGI
/// request origin on LangGraph Host is often an internal loopback. Skips when
/// the secret is already present or `runtime_url` is empty/loopback.
pub fn ensure_public_api_url(secrets: &mut Vec<Secret>, runtime_url: &str) {
    if secrets
        .iter()
        .any(|secret| secret.name == MDA_PUBLIC_API_URL_ENV)
    {
        return;
    }
    let trimmed = runtime_url.trim().trim_end_matches('/');
    if trimmed.is_empty() || is_loopback_http_url(trimmed) {
        return;
    }
    secrets.push(Secret {
        name: MDA_PUBLIC_API_URL_ENV.to_string(),
        value: trimmed.to_string(),
    });
}

fn is_loopback_http_url(url: &str) -> bool {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    let host = rest.split('/').next().unwrap_or(rest);
    let hostname = host.split(':').next().unwrap_or(host);
    matches!(hostname, "localhost" | "127.0.0.1" | "::1")
}

/// Inject `CORS_ALLOW_ORIGINS` from `MDA_PUBLIC_APP_URL` / `MDA_CORS_ORIGINS`.
///
/// LangGraph owns browser CORS for the whole Agent Server (including `/ok` and
/// custom `http.app` routes). The managed HTTP app must not add a second
/// `Access-Control-Allow-Origin`. Does nothing when `CORS_ALLOW_ORIGINS` is
/// already present or no public origins are configured.
pub fn ensure_cors_allow_origins(secrets: &mut Vec<Secret>, env_vars: &BTreeMap<String, String>) {
    if secrets
        .iter()
        .any(|secret| secret.name == CORS_ALLOW_ORIGINS_ENV)
    {
        return;
    }
    let origins = resolve_cors_allow_origins(env_vars);
    if origins.is_empty() {
        return;
    }
    secrets.push(Secret {
        name: CORS_ALLOW_ORIGINS_ENV.to_string(),
        value: origins.join(","),
    });
}

/// Build the exact-origin CORS allowlist for LangGraph `CORS_ALLOW_ORIGINS`.
///
/// Origins are taken verbatim from `MDA_PUBLIC_APP_URL` and `MDA_CORS_ORIGINS`
/// (trailing slashes stripped). Callers must list every browser origin that
/// should be allowed — no host-specific expansion.
pub fn resolve_cors_allow_origins(env_vars: &BTreeMap<String, String>) -> Vec<String> {
    let mut origins = BTreeSet::new();
    if let Some(public_app) = env_vars.get(MDA_PUBLIC_APP_URL_ENV) {
        let origin = public_app.trim().trim_end_matches('/');
        if !origin.is_empty() {
            origins.insert(origin.to_string());
        }
    }
    if let Some(extra) = env_vars.get(MDA_CORS_ORIGINS_ENV) {
        for part in extra.split(',') {
            let origin = part.trim().trim_end_matches('/');
            if !origin.is_empty() {
                origins.insert(origin.to_string());
            }
        }
    }
    origins.into_iter().collect()
}

/// Whether an error chain represents an HTTP client timeout.
pub fn is_request_timeout(err: &Error) -> bool {
    err.chain().any(|cause| {
        cause
            .downcast_ref::<reqwest::Error>()
            .is_some_and(reqwest::Error::is_timeout)
            || cause.to_string().contains("operation timed out")
    })
}

/// Find a deployment by exact name match within a list-deployments response.
pub fn find_deployment_by_name<'a>(response: &'a Value, name: &str) -> Option<&'a Value> {
    response
        .get("resources")?
        .as_array()?
        .iter()
        .find(|deployment| deployment.get("name").and_then(Value::as_str) == Some(name))
}

/// Read the server-owned Managed Deep Agent marker off a deployment object.
///
/// `None` means the object carries no marker at all, which is not the same as
/// "not a Managed Deep Agent": list responses are trimmed, so a marker absent
/// here may still be present on the full object from `GET /v2/deployments/{id}`.
/// Callers that use the marker to decide whether overwriting is safe must treat
/// `None` as unknown and re-read the full object rather than assuming `false`.
///
/// Both spellings are accepted: `create_deployment` sends the flag as
/// `managed_deep_agent`, and the host backend is expected to surface the
/// persisted marker as `is_managed_deep_agent`.
pub fn deployment_managed_deep_agent_marker(deployment: &Value) -> Option<bool> {
    ["is_managed_deep_agent", "managed_deep_agent"]
        .iter()
        .find_map(|key| deployment.get(*key).and_then(Value::as_bool))
}

/// Whether a deployment is a known Managed Deep Agent. An absent marker is not
/// a claim of ownership, so it reads as `false`; see
/// [`deployment_managed_deep_agent_marker`] when the distinction matters.
pub fn deployment_is_managed_deep_agent(deployment: &Value) -> bool {
    deployment_managed_deep_agent_marker(deployment) == Some(true)
}

/// The agent's deployment within a `list_agent_deployments` response, skipping
/// preview deployments.
pub fn find_agent_deployment(response: &Value) -> Option<&Value> {
    response
        .get("resources")?
        .as_array()?
        .iter()
        .find(|deployment| deployment.get("is_preview") != Some(&Value::Bool(true)))
}

/// Find a deployment ID by exact name match within a list-deployments response.
pub fn find_deployment_id_by_name(response: &Value, name: &str) -> Option<String> {
    find_deployment_by_name(response, name)?
        .get("id")?
        .as_str()
        .map(str::to_string)
}

/// Derive the Context Hub API base URL from the host backend API URL.
pub fn context_hub_base_url(host_url: &str) -> String {
    let parts = parse_host_url(host_url);
    const API_HOST: &str = "api.host.langchain.com";
    // Local langchainplus splits host-backend (:8300) from the LangSmith API /
    // Context Hub surface (nginx :1980). SaaS maps api.host → api.smith; mirror
    // that split when deploying against a local host-backend.
    if parts.hostname == "localhost" || parts.hostname == "127.0.0.1" {
        if local_host_backend_port(parts.trimmed) == Some(8300) {
            return format!("{}://{}:1980", parts.scheme, parts.hostname);
        }
        return parts.trimmed.to_string();
    }
    if parts.hostname == API_HOST {
        return "https://api.smith.langchain.com".to_string();
    }
    if let Some(prefix) = parts.hostname.strip_suffix(&format!(".{API_HOST}")) {
        return format!("https://{prefix}.api.smith.langchain.com");
    }
    parts.trimmed.to_string()
}

/// Resolve Context Hub independently from the Deployment API.
///
/// Known SaaS and local-development host mappings stay authoritative. A custom
/// host has no sibling-service mapping, so Context Hub uses the configured
/// platform API instead of inheriting a path such as `/api-host`.
pub fn resolve_context_hub_base_url(host_url: &str, platform_api_url: &str) -> String {
    let derived = context_hub_base_url(host_url);
    if derived == host_url.trim_end_matches('/') {
        platform_api_url.trim_end_matches('/').to_string()
    } else {
        derived
    }
}

/// Port from a `scheme://host:port` URL, if present.
fn local_host_backend_port(host_url: &str) -> Option<u16> {
    let after_scheme = host_url.split_once("://").map(|(_, rest)| rest)?;
    let host_port = after_scheme.split('/').next().unwrap_or("");
    let port = host_port.split_once(':').map(|(_, port)| port)?;
    port.parse().ok()
}

/// Compute the LangSmith plans-and-billing URL for a workspace's organization
/// (Settings > Billing > Plans and Pricing).
///
/// The dashboard's `/o/{id}` segment is a *workspace* id despite its name, and
/// LangSmith resolves the organization from it, so any workspace in the
/// organization reaches the same billing page.
///
/// Returns `None` unless the id is a hyphenated UUID. The id can come from
/// `LANGSMITH_WORKSPACE_ID` or `--workspace-id`, and a link is only worth
/// printing when it is well-formed — a value carrying path or whitespace
/// characters would render as a misleading URL.
pub fn plan_upgrade_url(dashboard_url: &str, workspace_id: &str) -> Option<String> {
    // `Uuid::parse_str` also accepts simple/URN/braced forms; the dashboard
    // path expects the canonical hyphenated id.
    if workspace_id.len() != uuid::fmt::Hyphenated::LENGTH {
        return None;
    }
    uuid::Uuid::parse_str(workspace_id).ok()?;
    Some(format!(
        "{dashboard_url}/o/{workspace_id}/settings/payments"
    ))
}

/// Compute the LangSmith dashboard URL for a deployment, if a tenant is known.
pub fn deployment_status_url(
    deployment: &Value,
    deployment_id: &str,
    dashboard_url: &str,
) -> Option<String> {
    let tenant_id = deployment_tenant_id(deployment)?;
    Some(format!(
        "{dashboard_url}/o/{tenant_id}/host/deployments/{deployment_id}"
    ))
}

/// Read the workspace ("tenant") that owns a deployment off a host-backend
/// deployment object. Present on list, create, get, and patch responses alike.
pub fn deployment_tenant_id(deployment: &Value) -> Option<&str> {
    deployment
        .get("tenant_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|tenant_id| !tenant_id.is_empty())
}

/// Extract the deployed Agent Server URL from host-backend deployment payloads.
pub fn deployment_runtime_url(deployment: &Value) -> Option<String> {
    const TOP_LEVEL_KEYS: &[&str] = &[
        "deployment_url",
        "api_url",
        "runtime_url",
        "host_url",
        "app_url",
        "url",
    ];

    for key in TOP_LEVEL_KEYS {
        if let Some(url) = http_url(deployment.get(*key)) {
            return Some(url);
        }
    }

    for nested_key in ["source_config", "active_revision", "latest_revision"] {
        if let Some(object) = deployment.get(nested_key) {
            for key in TOP_LEVEL_KEYS.iter().copied().chain(["custom_url"]) {
                if let Some(url) = http_url(object.get(key)) {
                    return Some(url);
                }
            }
        }
    }

    None
}

fn http_url(value: Option<&Value>) -> Option<String> {
    let url = value?.as_str()?.trim().trim_end_matches('/').to_string();
    (url.starts_with("http://") || url.starts_with("https://")).then_some(url)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn interrupted_status_is_terminal_but_not_failed() {
        assert!(is_terminal_status("INTERRUPTED"));
        assert!(is_interrupted_status("INTERRUPTED"));
        assert!(!is_failed_status("INTERRUPTED"));
        assert!(!is_interrupted_status("DEPLOYED"));
        assert!(!is_interrupted_status("BUILD_FAILED"));
    }

    #[test]
    fn secrets_skip_reserved_and_empty() {
        let mut env = BTreeMap::new();
        env.insert("OPENAI_API_KEY".to_string(), "sk-123".to_string());
        env.insert("LANGSMITH_API_KEY".to_string(), "ls-secret".to_string());
        env.insert(
            "LANGSMITH_GATEWAY_API_KEY".to_string(),
            "gateway-secret".to_string(),
        );
        env.insert("EMPTY".to_string(), "".to_string());

        let secrets = secrets_from_env(&env);
        let names = secrets
            .iter()
            .map(|secret| secret.name.as_str())
            .collect::<Vec<_>>();
        assert_eq!(names, vec!["LANGSMITH_GATEWAY_API_KEY", "OPENAI_API_KEY"]);
    }

    #[test]
    fn generated_handoff_secret_is_injected_under_deploy_owned_name() {
        let mut secrets = Vec::new();
        set_mda_handoff_secret(&mut secrets, "generated-secret");

        assert_eq!(
            (secrets[0].name.as_str(), secrets[0].value.as_str()),
            (MDA_HANDOFF_SECRET_ENV, "generated-secret")
        );
    }

    #[test]
    fn set_workspace_id_secret_injects_when_missing() {
        let mut secrets = vec![Secret {
            name: "OPENAI_API_KEY".to_string(),
            value: "sk".to_string(),
        }];
        set_workspace_id_secret(&mut secrets, "ws-1");
        assert_eq!(secrets.len(), 2);
        assert_eq!(secrets[1].name, WORKSPACE_ID_ENV);
        assert_eq!(secrets[1].value, "ws-1");
    }

    #[test]
    fn set_workspace_id_secret_replaces_existing() {
        let mut secrets = vec![Secret {
            name: WORKSPACE_ID_ENV.to_string(),
            value: "ws-old".to_string(),
        }];
        set_workspace_id_secret(&mut secrets, "ws-new");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].value, "ws-new");
    }

    #[test]
    fn set_workspace_id_secret_skips_empty() {
        let mut secrets = vec![Secret {
            name: WORKSPACE_ID_ENV.to_string(),
            value: "ws-old".to_string(),
        }];
        set_workspace_id_secret(&mut secrets, "");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].value, "ws-old");
    }

    #[test]
    fn sandbox_api_endpoint_uses_platform_surface_and_replaces_stale_value() {
        let mut secrets = vec![Secret {
            name: MDA_SANDBOX_API_ENDPOINT_ENV.to_string(),
            value: "https://stale.example/v2/sandboxes".to_string(),
        }];

        ensure_sandbox_api_endpoint_secret(&mut secrets, "https://self-hosted.example/");

        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_SANDBOX_API_ENDPOINT_ENV);
        assert_eq!(secrets[0].value, "https://self-hosted.example/v2/sandboxes");
    }

    #[test]
    fn ensure_assistant_id_secret_injects_and_overwrites() {
        let mut secrets = vec![Secret {
            name: MDA_ASSISTANT_ID_ENV.to_string(),
            value: "stale".to_string(),
        }];
        ensure_assistant_id_secret(&mut secrets, "gmail-typescript-example");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_ASSISTANT_ID_ENV);
        assert_eq!(secrets[0].value, "gmail-typescript-example");
    }

    #[test]
    fn ensure_langsmith_url_secret_injects_and_overwrites() {
        let mut secrets = vec![Secret {
            name: MDA_LANGSMITH_URL_ENV.to_string(),
            value: "https://stale.example".to_string(),
        }];
        ensure_langsmith_url_secret(&mut secrets, "https://eu.smith.langchain.com");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_LANGSMITH_URL_ENV);
        assert_eq!(secrets[0].value, "https://eu.smith.langchain.com");
    }

    #[test]
    fn langsmith_endpoint_is_not_forwarded_from_env() {
        // Host rejects LANGSMITH_ENDPOINT as a user secret (HTTP 400 reserved).
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "https://eu.api.smith.langchain.com".to_string(),
        );
        assert!(secrets_from_env(&env).is_empty());
    }

    #[test]
    fn sandbox_api_endpoint_is_not_forwarded_from_env() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_SANDBOX_API_ENDPOINT_ENV.to_string(),
            "https://untrusted.example/v2/sandboxes".to_string(),
        );

        assert!(secrets_from_env(&env).is_empty());
    }

    #[test]
    fn ensure_deployment_name_secret_injects_when_missing() {
        let mut secrets = Vec::new();
        ensure_deployment_name_secret(&mut secrets, "my-agent");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_DEPLOYMENT_NAME_ENV);
        assert_eq!(secrets[0].value, "my-agent");
    }

    #[test]
    fn ensure_deployment_name_secret_overrides_any_env_value() {
        // A stale/copied `.env` value would otherwise name this deployment's
        // sandboxes after a different deployment.
        let mut secrets = vec![Secret {
            name: MDA_DEPLOYMENT_NAME_ENV.to_string(),
            value: "some-other-agent".to_string(),
        }];
        ensure_deployment_name_secret(&mut secrets, "my-agent");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].value, "my-agent");
    }

    #[test]
    fn ensure_deployment_name_secret_skips_empty() {
        let mut secrets = Vec::new();
        ensure_deployment_name_secret(&mut secrets, "");
        assert!(secrets.is_empty());
    }

    #[test]
    fn recipe_digest_is_stable_and_length_bounded() {
        let a = recipe_digest(
            b"#!/bin/sh\necho hi\n",
            Some("base"),
            None,
            None,
            None,
            None,
        );
        let b = recipe_digest(
            b"#!/bin/sh\necho hi\n",
            Some("base"),
            None,
            None,
            None,
            None,
        );
        assert_eq!(a, b);
        assert_eq!(a.len(), RECIPE_DIGEST_LENGTH);
        assert!(a.chars().all(|ch| ch.is_ascii_hexdigit()));
        let changed = recipe_digest(
            b"#!/bin/sh\necho bye\n",
            Some("base"),
            None,
            None,
            None,
            None,
        );
        assert_ne!(a, changed);
        let other_base = recipe_digest(
            b"#!/bin/sh\necho hi\n",
            None,
            Some("id-1"),
            None,
            None,
            None,
        );
        assert_ne!(a, other_base);
        let with_registry = recipe_digest(
            b"#!/bin/sh\necho hi\n",
            None,
            None,
            Some("ghcr.io/acme/base:1"),
            Some("ghcr"),
            None,
        );
        assert_ne!(
            with_registry,
            recipe_digest(
                b"#!/bin/sh\necho hi\n",
                None,
                None,
                Some("ghcr.io/acme/base:1"),
                None,
                None
            )
        );
        assert_ne!(
            recipe_digest(b"", None, None, Some("python:3.12"), None, None),
            recipe_digest(b"", None, None, Some("python:3.12"), None, Some(2 << 30))
        );
    }

    #[test]
    fn intermediate_snapshots_match_only_their_own_deployment() {
        let digest = "640ecb01b34ba969";
        for kind in INTERMEDIATE_SNAPSHOT_KINDS {
            let name = intermediate_snapshot_name("agent", kind, digest);
            assert_eq!(name, format!("agent--{kind}-{digest}"));
            assert!(is_deployment_intermediate_snapshot(&name, "agent"));
            assert!(is_deployment_bake_snapshot(&name, "agent"));
            // Neighbours that share a prefix with `agent`.
            assert!(!is_deployment_intermediate_snapshot(&name, "agent--x"));
            assert!(!is_deployment_intermediate_snapshot(
                &intermediate_snapshot_name("agent--x", kind, digest),
                "agent"
            ));
            assert!(!is_deployment_intermediate_snapshot(
                &intermediate_snapshot_name("agent-x", kind, digest),
                "agent"
            ));
            assert!(!is_deployment_intermediate_snapshot(
                &format!("agent--{kind}-{digest}--setup-{digest}"),
                "agent"
            ));
        }
        assert!(!is_deployment_intermediate_snapshot(
            "agent--dockerbase-abc",
            "agent"
        ));
        assert!(!is_deployment_intermediate_snapshot(
            "agent--setup-640ecb01b34ba969",
            "agent"
        ));
    }

    #[test]
    fn intermediate_snapshot_name_stays_within_the_host_limit() {
        let deployment = "mda-integ-sandbox-recipe-ts-pr-339-abcdef";
        for kind in INTERMEDIATE_SNAPSHOT_KINDS {
            let name = intermediate_snapshot_name(deployment, kind, "0123456789abcdef");
            assert!(name.len() <= HOST_SANDBOX_NAME_LIMIT, "{name}");
            assert!(is_deployment_intermediate_snapshot(&name, deployment));
            assert!(!is_deployment_intermediate_snapshot(
                &name,
                "mda-integ-sandbox-recipe-ts-pr-339-abcdeg"
            ));
        }
    }

    #[test]
    fn bake_snapshot_list_prefix_covers_recipes_and_intermediates() {
        for deployment in ["agent", "mda-integ-sandbox-recipe-ts-pr-339-abcdef"] {
            let prefix = bake_snapshot_list_prefix(deployment);
            assert!(!prefix.is_empty());
            assert!(recipe_snapshot_name(deployment, "0123456789abcdef").starts_with(&prefix));
            for kind in INTERMEDIATE_SNAPSHOT_KINDS {
                assert!(
                    intermediate_snapshot_name(deployment, kind, "0123456789abcdef")
                        .starts_with(&prefix)
                );
            }
        }
        assert_eq!(bake_snapshot_list_prefix("agent"), "agent--");
    }

    #[test]
    fn recipe_snapshot_name_uses_setup_segment() {
        assert_eq!(
            recipe_snapshot_name("my-agent", "abcd1234efgh5678"),
            "my-agent--setup-abcd1234efgh5678"
        );
    }

    #[test]
    fn recipe_snapshot_name_stays_within_the_host_limit() {
        let deployment = "mda-integ-sandbox-recipe-ts-pr-339-abcdef";
        let name = recipe_snapshot_name(deployment, "0123456789abcdef");

        assert!(
            name.len() <= HOST_SANDBOX_NAME_LIMIT,
            "{name} ({})",
            name.len()
        );
        // Same literal as the integration-test helper's mirror of this rule
        // (`integration-tests/src/helpers/snapshots.test.ts`) — the two must
        // agree or the suite looks for a snapshot bake never created.
        assert_eq!(
            name,
            "mda-integ-sandbox-recipe-ts-pr-61ebbf7d--setup-0123456789abcdef"
        );
    }

    #[test]
    fn recipe_reference_pins_the_tag_host_assigned() {
        // A Docker-built recipe inherits the image tag, so the bare name would
        // resolve as `…:latest` and 404 at thread create.
        assert_eq!(
            recipe_snapshot_reference("my-agent--setup-abc", &["0.1.1".to_string()]),
            "my-agent--setup-abc:0.1.1"
        );
    }

    #[test]
    fn recipe_reference_stays_bare_when_latest_exists_or_no_tags() {
        assert_eq!(
            recipe_snapshot_reference("my-agent--setup-abc", &["latest".to_string()]),
            "my-agent--setup-abc"
        );
        assert_eq!(
            recipe_snapshot_reference(
                "my-agent--setup-abc",
                &["0.1.1".to_string(), "latest".to_string()]
            ),
            "my-agent--setup-abc"
        );
        assert_eq!(
            recipe_snapshot_reference("my-agent--setup-abc", &[]),
            "my-agent--setup-abc"
        );
        assert_eq!(
            recipe_snapshot_reference("my-agent--setup-abc", &["  ".to_string()]),
            "my-agent--setup-abc"
        );
    }

    #[test]
    fn recognizes_recipe_snapshots_this_deployment_owns() {
        assert!(is_deployment_recipe_snapshot(
            "my-agent--setup-abcd1234efgh5678",
            "my-agent"
        ));
        assert!(!is_deployment_recipe_snapshot(
            "my-agent--setup-",
            "my-agent"
        ));
        assert!(!is_deployment_recipe_snapshot(
            "my-agent-staging--setup-abcd1234efgh5678",
            "my-agent"
        ));
        assert!(!is_deployment_recipe_snapshot(
            "my-agent--0123456789ab",
            "my-agent"
        ));
        assert!(!is_deployment_recipe_snapshot(
            "other--setup-abcd1234efgh5678",
            "my-agent"
        ));
    }

    #[test]
    fn ensure_recipe_snapshot_secret_injects_and_overwrites() {
        let mut secrets = vec![Secret {
            name: MDA_SANDBOX_RECIPE_SNAPSHOT_ENV.to_string(),
            value: "stale".to_string(),
        }];
        ensure_recipe_snapshot_secret(&mut secrets, "my-agent--setup-abc");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_SANDBOX_RECIPE_SNAPSHOT_ENV);
        assert_eq!(secrets[0].value, "my-agent--setup-abc");
        clear_recipe_snapshot_secret(&mut secrets);
        assert!(secrets.is_empty());
    }

    #[test]
    fn recipe_snapshot_env_is_not_forwarded_from_env() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_SANDBOX_RECIPE_SNAPSHOT_ENV.to_string(),
            "my-agent--setup-abc".to_string(),
        );
        assert!(secrets_from_env(&env).is_empty());
    }

    #[test]
    fn deployment_name_is_not_forwarded_from_env() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_DEPLOYMENT_NAME_ENV.to_string(),
            "some-other-agent".to_string(),
        );
        assert!(secrets_from_env(&env).is_empty());
    }

    #[test]
    fn sandbox_prefix_cannot_match_a_longer_deployment_name() {
        let prefix = sandbox_name_prefix("my-agent");
        assert_eq!(prefix, "my-agent--");
        assert!(format!("my-agent{SANDBOX_NAME_SEPARATOR}abc123").starts_with(&prefix));
        // The doubled separator is what keeps a sibling deployment out.
        assert!(!format!("my-agent-staging{SANDBOX_NAME_SEPARATOR}abc123").starts_with(&prefix));
    }

    #[test]
    fn sandbox_prefix_preserves_long_deployment_when_primary_name_fits() {
        let deployment = "mda-integ-sandbox-recipe-ts-pr-339-4d66e3";
        let prefix = sandbox_name_prefix(deployment);

        assert_eq!(prefix, format!("{deployment}--"));
    }

    #[test]
    fn recognizes_primary_and_shortened_fallback_names_for_long_deployments() {
        let deployment = "mda-integ-sandbox-recipe-ts-pr-339-4d66e3";
        let prefixes = sandbox_name_prefixes(deployment);
        let primary = format!("{}0123456789ab", prefixes[0]);
        let fallback = format!("{}0123456789ab-0a1b2c3d", prefixes[1]);

        assert!(is_deployment_sandbox(&primary, deployment));
        assert!(is_deployment_sandbox(&fallback, deployment));
    }

    #[test]
    fn recognizes_sandboxes_this_deployment_created() {
        assert!(is_deployment_sandbox("my-agent--0123456789ab", "my-agent"));
        // Collision fallback suffix.
        assert!(is_deployment_sandbox(
            "my-agent--0123456789ab-0a1b2c3d",
            "my-agent"
        ));
    }

    #[test]
    fn rejects_sandboxes_belonging_to_another_deployment() {
        // A neighbour whose own name contains the separator would otherwise be
        // swept up by the `my-agent--` prefix.
        assert!(!is_deployment_sandbox(
            "my-agent--foo--0123456789ab",
            "my-agent"
        ));
        assert!(!is_deployment_sandbox(
            "my-agent-staging--0123456789ab",
            "my-agent"
        ));
        assert!(!is_deployment_sandbox("other--0123456789ab", "my-agent"));
    }

    #[test]
    fn rejects_names_that_do_not_carry_a_scope_digest() {
        // Hand-named sandboxes that merely start with the deployment name are
        // not ours to delete.
        assert!(!is_deployment_sandbox("my-agent--scratch", "my-agent"));
        assert!(!is_deployment_sandbox("my-agent--0123456789", "my-agent"));
        assert!(!is_deployment_sandbox(
            "my-agent--0123456789abc",
            "my-agent"
        ));
        assert!(!is_deployment_sandbox("my-agent--0123456789AB", "my-agent"));
        assert!(!is_deployment_sandbox(
            "my-agent--0123456789ab-xyz",
            "my-agent"
        ));
        assert!(!is_deployment_sandbox("my-agent", "my-agent"));
    }

    #[test]
    fn resolve_cors_allow_origins_merges_exact_origins() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_PUBLIC_APP_URL_ENV.to_string(),
            "https://app.example.com/".to_string(),
        );
        env.insert(
            MDA_CORS_ORIGINS_ENV.to_string(),
            "http://localhost:4900, https://preview.example.com/".to_string(),
        );
        assert_eq!(
            resolve_cors_allow_origins(&env),
            vec![
                "http://localhost:4900".to_string(),
                "https://app.example.com".to_string(),
                "https://preview.example.com".to_string(),
            ]
        );
    }

    #[test]
    fn ensure_cors_allow_origins_injects_when_missing() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_PUBLIC_APP_URL_ENV.to_string(),
            "https://app.example.com".to_string(),
        );
        let mut secrets = Vec::new();
        ensure_cors_allow_origins(&mut secrets, &env);
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, CORS_ALLOW_ORIGINS_ENV);
        assert_eq!(secrets[0].value, "https://app.example.com");
    }

    #[test]
    fn login_api_url_derives_eu_and_staging_from_host() {
        // Empty lookup, so an ambient `LANGSMITH_ENDPOINT` cannot short-circuit
        // the derivation under test.
        assert_eq!(
            resolve_login_api_url("https://eu.api.host.langchain.com", |_| None),
            "https://eu.api.smith.langchain.com"
        );
        assert_eq!(
            resolve_login_api_url("https://beta.api.host.langchain.com", |_| None),
            "https://beta.api.smith.langchain.com"
        );
    }

    #[test]
    fn platform_api_url_prefers_process_env_over_dotenv() {
        let dotenv = BTreeMap::from([(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "https://eu.api.smith.langchain.com".to_string(),
        )]);
        let blank_dotenv =
            BTreeMap::from([(LANGSMITH_ENDPOINT_ENV.to_string(), "   ".to_string())]);
        let process = |key: &str| {
            (key == LANGSMITH_ENDPOINT_ENV)
                .then(|| "https://from-process-env.example.com".to_string())
        };
        let no_process = |_: &str| None;

        // The process env wins over `.env` for this CLI setting (unlike
        // deployment secrets, which only ever come from `.env`).
        assert_eq!(
            resolve_platform_api_url_with(DEFAULT_HOST_URL, &dotenv, process),
            "https://from-process-env.example.com"
        );
        assert_eq!(
            resolve_platform_api_url_with(DEFAULT_HOST_URL, &blank_dotenv, process),
            "https://from-process-env.example.com"
        );
        assert_eq!(
            resolve_platform_api_url_with(DEFAULT_HOST_URL, &dotenv, no_process),
            "https://eu.api.smith.langchain.com"
        );
    }

    #[test]
    fn resolve_langsmith_api_bases_splits_local_nginx_and_host_backend() {
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "http://localhost:1980/".to_string(),
        );
        let (host, platform) = resolve_langsmith_api_bases(&env);
        assert_eq!(host, DEFAULT_LOCAL_HOST_BACKEND_URL);
        assert_eq!(platform, "http://localhost:1980");
    }

    #[test]
    fn resolve_langsmith_api_bases_derives_host_from_platform_endpoint() {
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "https://beta.api.smith.langchain.com".to_string(),
        );
        let (host, platform) = resolve_langsmith_api_bases(&env);
        assert_eq!(host, "https://beta.api.host.langchain.com");
        assert_eq!(platform, "https://beta.api.smith.langchain.com");
    }

    #[test]
    fn resolve_langsmith_api_bases_derives_self_hosted_deployment_path() {
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "http://langsmith.localhost/".to_string(),
        );

        assert_eq!(
            resolve_langsmith_api_bases(&env),
            (
                "http://langsmith.localhost/api-host".to_string(),
                "http://langsmith.localhost".to_string(),
            )
        );
    }

    #[test]
    fn resolve_langsmith_api_bases_puts_local_stack_host_backend_below_api_host() {
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "http://localhost:21980".to_string(),
        );

        let (host, _) = resolve_langsmith_api_bases(&env);

        assert_eq!(host, "http://localhost:21980/api-host");
    }

    #[test]
    fn resolve_langsmith_api_bases_strips_sdk_rest_path_from_self_hosted_endpoint() {
        let mut env = BTreeMap::new();
        env.insert(
            LANGSMITH_ENDPOINT_ENV.to_string(),
            "https://langsmith.example.com/api/v1/".to_string(),
        );

        let (host, _) = resolve_langsmith_api_bases(&env);

        assert_eq!(host, "https://langsmith.example.com/api-host");
    }

    #[test]
    fn https_public_url_is_accepted() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_PUBLIC_API_URL_ENV.to_string(),
            "https://abc.trycloudflare.com/".to_string(),
        );
        assert_eq!(
            resolve_https_public_api_url(&env),
            PublicApiUrl::Https("https://abc.trycloudflare.com".to_string())
        );
    }

    #[test]
    fn missing_public_url_is_reported() {
        assert_eq!(
            resolve_https_public_api_url(&BTreeMap::new()),
            PublicApiUrl::Missing
        );
    }

    #[test]
    fn http_public_url_is_rejected() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_PUBLIC_API_URL_ENV.to_string(),
            "http://localhost:2024".to_string(),
        );
        assert_eq!(
            resolve_https_public_api_url(&env),
            PublicApiUrl::NotHttps("http://localhost:2024".to_string())
        );
    }

    #[test]
    fn ensure_public_api_url_injects_non_loopback() {
        let mut secrets = Vec::new();
        ensure_public_api_url(&mut secrets, "https://slack-abc.us.langgraph.app/");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].name, MDA_PUBLIC_API_URL_ENV);
        assert_eq!(secrets[0].value, "https://slack-abc.us.langgraph.app");
    }

    #[test]
    fn ensure_public_api_url_skips_loopback_and_existing() {
        let mut secrets = Vec::new();
        ensure_public_api_url(&mut secrets, "http://localhost:5557");
        assert!(secrets.is_empty());
        secrets.push(Secret {
            name: MDA_PUBLIC_API_URL_ENV.to_string(),
            value: "https://already.set".to_string(),
        });
        ensure_public_api_url(&mut secrets, "https://other.example.com");
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].value, "https://already.set");
    }

    #[test]
    fn ensure_cors_allow_origins_preserves_explicit() {
        let mut env = BTreeMap::new();
        env.insert(
            MDA_PUBLIC_APP_URL_ENV.to_string(),
            "https://app.example.com".to_string(),
        );
        let mut secrets = vec![Secret {
            name: CORS_ALLOW_ORIGINS_ENV.to_string(),
            value: "https://explicit.example.com".to_string(),
        }];
        ensure_cors_allow_origins(&mut secrets, &env);
        assert_eq!(secrets.len(), 1);
        assert_eq!(secrets[0].value, "https://explicit.example.com");
    }

    #[test]
    fn request_timeout_detects_timeout_messages() {
        assert!(is_request_timeout(&anyhow::anyhow!(
            "POST /v2/deployments request failed: operation timed out"
        )));
        assert!(!is_request_timeout(&anyhow::anyhow!(
            "deployment not found"
        )));
    }

    #[test]
    fn find_deployment_by_name_matches_exact_name() {
        let response = json!({
            "resources": [
                { "id": "dep-1", "name": "my-agent-copy" },
                { "id": "dep-2", "name": "my-agent" }
            ]
        });

        assert_eq!(
            find_deployment_by_name(&response, "my-agent")
                .and_then(|deployment| deployment.get("id"))
                .and_then(Value::as_str),
            Some("dep-2")
        );
    }

    #[test]
    fn find_deployment_id_returns_none_when_name_is_missing() {
        let response = json!({
            "resources": [{ "id": "dep-1", "name": "other" }]
        });

        assert_eq!(find_deployment_id_by_name(&response, "missing"), None);
    }

    #[test]
    fn deployment_is_managed_deep_agent_requires_true_server_marker() {
        assert!(deployment_is_managed_deep_agent(
            &json!({ "is_managed_deep_agent": true })
        ));
    }

    #[test]
    fn deployment_is_managed_deep_agent_rejects_missing_server_marker() {
        assert!(!deployment_is_managed_deep_agent(&json!({})));
    }

    #[test]
    fn deployment_is_managed_deep_agent_rejects_false_server_marker() {
        assert!(!deployment_is_managed_deep_agent(
            &json!({ "is_managed_deep_agent": false })
        ));
    }

    #[test]
    fn deployment_is_managed_deep_agent_accepts_the_request_side_spelling() {
        assert!(deployment_is_managed_deep_agent(
            &json!({ "managed_deep_agent": true })
        ));
    }

    #[test]
    fn managed_deep_agent_marker_distinguishes_absent_from_false() {
        assert_eq!(deployment_managed_deep_agent_marker(&json!({})), None);
        assert_eq!(
            deployment_managed_deep_agent_marker(&json!({ "is_managed_deep_agent": false })),
            Some(false)
        );
        assert_eq!(
            deployment_managed_deep_agent_marker(&json!({ "is_managed_deep_agent": true })),
            Some(true)
        );
    }

    #[test]
    fn managed_deep_agent_marker_ignores_non_boolean_values() {
        assert_eq!(
            deployment_managed_deep_agent_marker(&json!({ "is_managed_deep_agent": "true" })),
            None
        );
    }

    #[test]
    fn status_url_uses_the_authoritative_byoc_dashboard_origin() {
        let deployment = json!({ "tenant_id": "workspace-1" });

        assert_eq!(
            deployment_status_url(
                &deployment,
                "deployment-1",
                "https://aws.smith.langchain.com"
            )
            .as_deref(),
            Some("https://aws.smith.langchain.com/o/workspace-1/host/deployments/deployment-1")
        );
    }

    #[test]
    fn status_url_requires_tenant() {
        let with_tenant = json!({ "tenant_id": "t-1" });
        assert_eq!(
            deployment_status_url(&with_tenant, "dep-1", "https://smith.langchain.com").as_deref(),
            Some("https://smith.langchain.com/o/t-1/host/deployments/dep-1")
        );
        assert_eq!(
            deployment_status_url(&json!({}), "dep-1", "https://smith.langchain.com"),
            None
        );
        assert_eq!(
            deployment_status_url(
                &json!({ "tenant_id": "  " }),
                "dep-1",
                "https://smith.langchain.com"
            ),
            None
        );
    }

    #[test]
    fn plan_upgrade_url_points_at_the_workspace_billing_page() {
        assert_eq!(
            plan_upgrade_url(
                "https://smith.langchain.com",
                "7109fb64-c8b8-48d5-a107-4ab03748e980"
            )
            .as_deref(),
            Some(
                "https://smith.langchain.com/o/7109fb64-c8b8-48d5-a107-4ab03748e980/settings/payments"
            )
        );
    }

    #[test]
    fn plan_upgrade_url_rejects_a_workspace_id_that_is_not_a_uuid() {
        // A configured `LANGSMITH_WORKSPACE_ID` reaches this unvalidated, and a
        // link is only worth printing when the id cannot reshape the path.
        for workspace_id in [
            "",
            "not-a-uuid",
            "../../settings",
            "7109fb64-c8b8-48d5-a107-4ab03748e980/x",
            "7109fb64c8b848d5a1074ab03748e980",
            "7109fb64-c8b8-48d5-a107-4ab03748e98g",
        ] {
            assert_eq!(
                plan_upgrade_url("https://smith.langchain.com", workspace_id),
                None,
                "expected no link for {workspace_id:?}"
            );
        }
    }

    #[test]
    fn deployment_runtime_url_prefers_known_runtime_fields() {
        let deployment = json!({
            "deployment_url": "https://runtime.example.com/",
            "url": "https://dashboard.example.com"
        });
        assert_eq!(
            deployment_runtime_url(&deployment).as_deref(),
            Some("https://runtime.example.com")
        );
    }

    #[test]
    fn deployment_runtime_url_reads_nested_custom_url() {
        let deployment = json!({
            "source_config": {
                "custom_url": "https://custom.example.com/"
            }
        });
        assert_eq!(
            deployment_runtime_url(&deployment).as_deref(),
            Some("https://custom.example.com")
        );
    }

    #[test]
    fn context_hub_url_maps_from_host_backend_url() {
        assert_eq!(
            context_hub_base_url("https://api.host.langchain.com"),
            "https://api.smith.langchain.com"
        );
        assert_eq!(
            context_hub_base_url("https://eu.api.host.langchain.com"),
            "https://eu.api.smith.langchain.com"
        );
        assert_eq!(
            context_hub_base_url("http://localhost:8123"),
            "http://localhost:8123"
        );
        assert_eq!(
            context_hub_base_url("http://localhost:8300"),
            "http://localhost:1980"
        );
        assert_eq!(
            context_hub_base_url("http://127.0.0.1:8300"),
            "http://127.0.0.1:1980"
        );
        assert_eq!(
            context_hub_base_url("https://custom.example.com"),
            "https://custom.example.com"
        );
    }

    #[test]
    fn context_hub_url_uses_platform_for_self_hosted_deployment_path() {
        assert_eq!(
            resolve_context_hub_base_url(
                "http://langsmith.localhost/api-host",
                "http://langsmith.localhost",
            ),
            "http://langsmith.localhost"
        );
    }
}
