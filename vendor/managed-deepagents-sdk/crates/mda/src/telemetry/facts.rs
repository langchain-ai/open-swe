use std::time::Duration;

use serde::Serialize;

use crate::deploy::env_file;
use crate::deploy::langsmith_context::{resolve_workspace_id, LangSmithEndpoints};
use crate::langsmith::{self, ApiKey, Client, Login};

use super::command::CommandContext;

const LOOKUP_TIMEOUT: Duration = Duration::from_secs(1);

/// Attribution facts reported with `command_completed`. Collected before
/// dispatch; handlers overwrite fields they learn while running.
#[derive(Default, Serialize)]
pub struct ReportableFacts {
    /// Set only by `collect`, so opted-out runs make no telemetry lookups.
    #[serde(skip)]
    enabled: bool,
    #[serde(flatten)]
    pub auth: AuthInfo,
    #[serde(flatten)]
    pub deployment: DeploymentInfo,
}

#[derive(Default, Serialize)]
pub struct AuthInfo {
    pub user_id: Option<String>,
    pub org_id: Option<String>,
    pub workspace_id: Option<String>,
    pub langsmith_host_url: Option<String>,
}

#[derive(Default, Serialize)]
pub struct DeploymentInfo {
    pub deployment_id: Option<String>,
    pub deployment_name: Option<String>,
}

impl ReportableFacts {
    /// Best-effort; every failure leaves fields `None`.
    pub fn collect(context: CommandContext) -> Self {
        let env = env_file::parse_env_file(&context.env_path);
        let endpoints = LangSmithEndpoints::resolve(&env, context.host_url.as_deref());
        let mut facts = Self {
            enabled: true,
            auth: AuthInfo {
                langsmith_host_url: normalized_origin(&endpoints.platform_api_url),
                ..AuthInfo::default()
            },
            deployment: DeploymentInfo {
                deployment_id: context.deployment_id.clone(),
                deployment_name: context.deployment_name.clone(),
            },
        };
        let Some(client) = client(&context, &env, endpoints) else {
            return facts;
        };
        // Run both lookups concurrently; the scope joins before dispatch.
        let needs_lookup = facts.deployment.deployment_id.is_none();
        let deployment_id = std::thread::scope(|scope| {
            let lookup =
                needs_lookup.then(|| scope.spawn(|| lookup_deployment_id(&client, &context)));
            facts.refresh_identity(&client);
            lookup.and_then(|handle| handle.join().ok().flatten())
        });
        if deployment_id.is_some() {
            facts.deployment.deployment_id = deployment_id;
        }
        facts
    }

    /// Overwrite identity fields from the client's credential, when verified.
    pub fn refresh_identity(&mut self, client: &Client) {
        if !self.enabled {
            return;
        }
        let Ok(client) = client.clone().with_timeout(LOOKUP_TIMEOUT) else {
            return;
        };
        if let Some(identity) = client.auth_identity() {
            self.auth.user_id = identity.user_id;
            self.auth.org_id = identity.org_id;
            self.auth.workspace_id = identity.workspace_id;
        }
    }
}

fn lookup_deployment_id(client: &Client, context: &CommandContext) -> Option<String> {
    let deployment = crate::deploy::find_target_deployment(
        client,
        context.deployment_name.as_deref()?,
        context.deployment_agent.as_ref(),
    )
    .ok()??;
    deployment.get("id")?.as_str().map(str::to_string)
}

fn client(
    context: &CommandContext,
    env: &std::collections::BTreeMap<String, String>,
    endpoints: LangSmithEndpoints,
) -> Option<Client> {
    let key = langsmith::resolve_preferred_env(
        langsmith::API_KEY_ENV,
        langsmith::REMOVED_API_KEY_ENVS,
        None,
        env,
        |key| std::env::var(key).ok(),
        "",
    )
    .ok()?
    .or_else(|| Login::for_api_url(&endpoints.platform_api_url).cached_api_key())?;
    let workspace = resolve_workspace_id(env, context.workspace_id.as_deref(), "").ok()?;
    endpoints
        .with_auth(ApiKey::new(key), workspace)
        .client()
        .and_then(|client| client.with_timeout(LOOKUP_TIMEOUT))
        .ok()
}

pub(super) fn normalized_origin(raw: &str) -> Option<String> {
    let url = reqwest::Url::parse(raw.trim()).ok()?;
    matches!(url.scheme(), "http" | "https").then(|| url.origin().ascii_serialization())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_origin_excludes_credentials_and_paths() {
        assert_eq!(
            normalized_origin("https://user:secret@EXAMPLE.com:8443/path?q#f").as_deref(),
            Some("https://example.com:8443")
        );
    }

    #[test]
    fn opted_out_facts_make_no_identity_request() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let client = Client::for_test(&base);
        let mut facts = ReportableFacts::default();
        facts.refresh_identity(&client);
        assert!(listener.accept().is_err());
        assert!(facts.auth.user_id.is_none());
    }
}
