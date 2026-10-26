//! What is reported about this invocation, held in one process-wide store so
//! any module can read or extend it without threading it through calls.
//!
//! - main calls [`ReportableFacts::detect`] then [`init_facts`] once, before
//!   the command runs and only when telemetry is on. `detect` finds the
//!   LangSmith key the command would use, without prompting, and asks
//!   LangSmith who it belongs to.
//! - Commands call [`update_facts`] as they learn more, such as the deployment
//!   they act on, and [`refresh_identity`] after a sign-in.
//! - Readers call [`facts`]: the `command_completed` event at exit and the OTel
//!   logger for every record. While telemetry is off it returns `None`, so
//!   updates and lookups do nothing.

use std::path::PathBuf;
use std::sync::{Mutex, PoisonError};
use std::time::Duration;

use clap::{ArgMatches, Command};
use serde::Serialize;

// TODO: move LangSmith URL and key resolution out of `deploy` so telemetry
// does not depend on a command module.
use crate::deploy::langsmith_context::LangSmithEndpoints;
use crate::deploy::{client_without_prompt, env_file};
use crate::langsmith::{AuthIdentity, Client};

use super::command::CommandInvocation;
use super::metadata::InvocationMetadata;
use super::stage::CurrentStage;

/// How long an identity lookup for telemetry may take before it is skipped.
const IDENTITY_LOOKUP_TIMEOUT: Duration = Duration::from_secs(1);

/// Everything reported about this invocation.
#[derive(Clone, Default, Serialize)]
pub struct ReportableFacts {
    #[serde(flatten)]
    pub run: InvocationMetadata,
    #[serde(flatten)]
    pub command: CommandInvocation,
    #[serde(flatten)]
    pub auth: AuthInfo,
    #[serde(flatten)]
    pub deployment: DeploymentInfo,
    /// The command stage running now. Never serialized, so PostHog never sees
    /// it; the OTel logger adds it to each record itself.
    #[serde(skip)]
    pub stage: Option<CurrentStage>,
}

impl ReportableFacts {
    /// What is known before the command runs, and a client for the key the
    /// command would use without prompting, which the OTel logger sends with.
    /// The key's identity is looked up within [`IDENTITY_LOOKUP_TIMEOUT`]. The
    /// command fills in deployment facts.
    pub fn detect(command: &Command, matches: &ArgMatches) -> (Self, Option<Client>) {
        let (platform_url, client) = langsmith_target(matches);
        let identity = client
            .as_ref()
            .and_then(|client| client.auth_identity_within(IDENTITY_LOOKUP_TIMEOUT));
        let facts = Self {
            run: InvocationMetadata::detect(),
            command: CommandInvocation::from_matches(command, matches).unwrap_or_default(),
            auth: AuthInfo::new(identity, &platform_url),
            deployment: DeploymentInfo::default(),
            stage: None,
        };
        (facts, client)
    }
}

/// The LangSmith platform URL the command targets, and a client for the key it
/// would use there, from `--path`, `--host-url` and `--workspace-id` at any
/// subcommand level and the project's env file.
fn langsmith_target(matches: &ArgMatches) -> (String, Option<Client>) {
    fn arg<T: Clone + Send + Sync + 'static>(args: &ArgMatches, name: &str) -> Option<T> {
        args.try_get_one::<T>(name).ok().flatten().cloned()
    }
    let mut project_dir = PathBuf::from(".");
    let (mut host_url, mut workspace_id) = (None::<String>, None::<String>);
    let mut args = matches;
    loop {
        project_dir = arg(args, "path").unwrap_or(project_dir);
        host_url = arg(args, "host_url").or(host_url);
        workspace_id = arg(args, "workspace_id").or(workspace_id);
        let Some((_, child)) = args.subcommand() else {
            break;
        };
        args = child;
    }
    // `dev` and `chat` fall back to `.env.local` when there is no `.env`.
    let env_path = if matches!(matches.subcommand_name(), Some("dev" | "chat")) {
        env_file::project_env_persist_path(&project_dir)
    } else {
        project_dir.join(".env")
    };
    let env = env_file::parse_env_file(&env_path);
    let endpoints = LangSmithEndpoints::resolve(&env, host_url.as_deref());
    let client = client_without_prompt(
        &env,
        &endpoints.host_url,
        &endpoints.platform_api_url,
        workspace_id.as_deref(),
    );
    (endpoints.platform_api_url, client)
}

/// Who is running the command, as verified by LangSmith.
#[derive(Clone, Default, Serialize)]
pub struct AuthInfo {
    pub user_id: Option<String>,
    pub org_id: Option<String>,
    pub workspace_id: Option<String>,
    pub langsmith_host_url: Option<String>,
}

/// The deployment the command acts on, set by the command once it knows.
#[derive(Clone, Default, Serialize)]
pub struct DeploymentInfo {
    pub deployment_id: Option<String>,
    pub deployment_name: Option<String>,
}

/// The process's facts. Empty while telemetry is off.
struct FactsStore(Mutex<Option<ReportableFacts>>);

impl FactsStore {
    const fn new() -> Self {
        Self(Mutex::new(None))
    }

    fn init(&self, facts: ReportableFacts) {
        *self.0.lock().unwrap_or_else(PoisonError::into_inner) = Some(facts);
    }

    fn get(&self) -> Option<ReportableFacts> {
        self.0
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }

    /// `change` runs without the store locked, so it may log.
    fn update(&self, change: impl FnOnce(&mut ReportableFacts)) {
        let Some(mut updated) = self.get() else {
            return;
        };
        change(&mut updated);
        *self.0.lock().unwrap_or_else(PoisonError::into_inner) = Some(updated);
    }
}

static FACTS: FactsStore = FactsStore::new();

/// Store the facts for this invocation. Call once, before dispatch.
pub fn init_facts(facts: ReportableFacts) {
    FACTS.init(facts);
}

/// A copy of the current facts, or `None` when telemetry is off.
pub fn facts() -> Option<ReportableFacts> {
    FACTS.get()
}

/// Change the current facts. Does nothing when telemetry is off.
pub fn update_facts(change: impl FnOnce(&mut ReportableFacts)) {
    FACTS.update(change);
}

/// Re-read identity after the credential may have changed, such as a sign-in
/// during deploy. Makes no request when telemetry is off.
pub fn refresh_identity(client: &Client) {
    if facts().is_none() {
        return;
    }
    if let Some(identity) = client.auth_identity_within(IDENTITY_LOOKUP_TIMEOUT) {
        update_facts(|facts| {
            facts.auth = AuthInfo {
                langsmith_host_url: facts.auth.langsmith_host_url.take(),
                ..AuthInfo::from(identity)
            };
        });
    }
}

impl AuthInfo {
    /// The verified identity, when known, and the LangSmith origin either way.
    fn new(identity: Option<AuthIdentity>, platform_url: &str) -> Self {
        Self {
            langsmith_host_url: normalized_origin(platform_url),
            ..identity.map(Self::from).unwrap_or_default()
        }
    }
}

impl From<AuthIdentity> for AuthInfo {
    fn from(identity: AuthIdentity) -> Self {
        Self {
            user_id: identity.user_id,
            org_id: identity.org_id,
            workspace_id: identity.workspace_id,
            langsmith_host_url: None,
        }
    }
}

fn normalized_origin(raw: &str) -> Option<String> {
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
    fn updates_apply_only_after_init() {
        let store = FactsStore::new();
        let name = |facts: &mut ReportableFacts| {
            facts.deployment.deployment_name = Some("agent".to_string());
        };

        store.update(name);
        assert!(store.get().is_none());

        store.init(ReportableFacts::default());
        store.update(name);
        assert_eq!(
            store.get().unwrap().deployment.deployment_name.as_deref(),
            Some("agent")
        );
    }

    // No test in this crate calls `init_facts`, so the store stays `None`.
    #[test]
    fn opted_out_runs_make_no_identity_request() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let client = Client::for_test(&base);

        refresh_identity(&client);

        assert!(listener.accept().is_err());
        assert!(facts().is_none());
    }
}
