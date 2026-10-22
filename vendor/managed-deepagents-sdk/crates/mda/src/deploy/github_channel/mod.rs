//! GitHub channel setup during `mda deploy`.
//!
//! A GitHub channel needs a GitHub App, stored in Agent Auth as an app the
//! deployment owns, with a `github_app` OAuth connection that mints its
//! tokens. Agent Auth owns the App flow: it serves the page that sends the
//! manifest to GitHub, exchanges GitHub's code for the App's credentials, and
//! records the installation. The CLI never sees the private key of an App it
//! creates.
//!
//! Setup runs once the deployment is live, because the App's webhook points at
//! the Agent Server URL:
//!
//! 1. If the deployment has no installed App, start a hosted setup and wait
//!    for the user to create and install the App in the browser, or store an
//!    existing App's ID and key and record its installation.
//! 2. Ask Agent Auth for the App's report, point its webhook at the
//!    deployment, and report what is ready and what someone still has to fix
//!    on GitHub.
//! 3. When the channel needs other permissions than the agent's token has,
//!    and the installation already grants them, renew the token with them.
//!
//! `mda delete` removes the App and its secrets (see [`teardown`]).

mod app_manifest;
mod manifest;
mod report;
pub(crate) mod teardown;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use cliclack::log;

use crate::connection::wait_for_oauth_session;
use crate::deploy::channel_app::{
    self, agent_auth, find_connection, is_interactive, override_value, AppSlugs, SetupUi,
    AGENT_AUTH_URL_ENV,
};
use crate::error::is_cancellation;
use crate::github::{validate_login, PrivateKeyPem};
use crate::interrupt::Interrupt;
use crate::langsmith::github_app_setup::{
    AgentAuthApp, ExistingApp, GitHubAppManifest, GitHubAppSessionStart, GitHubAppState,
};
use crate::langsmith::github_channel::{
    GitHubChannelStatus, GitHubInstallation, GitHubSyncOutcome,
};
use crate::langsmith::{self, ApiError, ConnectionOwnerPolicy, OAuthAuthorizationSession};
use crate::secret_input::value_from_file;
use app_manifest::AppOwner;

pub(crate) use channel_app::WEBHOOK_SECRET_SUFFIX;
pub(crate) use manifest::{prepare, GitHubChannel, PROVIDER};

/// A GitHub App setup session that expired.
const SETUP_EXPIRED: &str = "The GitHub App setup was not finished in time. If an organization owner has to approve the installation, ask them to, then run `mda deploy` again.";
const INSTALL_POLL_INTERVAL: Duration = Duration::from_secs(3);
const INSTALL_WAIT_LIMIT: Duration = Duration::from_secs(30 * 60);
/// GitHub can briefly fail right after an App is created or installed.
const SYNC_ATTEMPTS: usize = 6;
const SYNC_RETRY_DELAY: Duration = Duration::from_secs(5);

/// Env override for the App's webhook secret. The runtime reads the secret
/// and the bot user ID from Agent Auth unless the env sets them.
const WEBHOOK_SECRET_ENV: &str = "GH_WEBHOOK_SECRET";

/// Secret connections next to the App's, which the runtime reads. Agent Auth
/// stores the webhook secret ([`WEBHOOK_SECRET_SUFFIX`]) with the App; deploy
/// stores the bot ID once Agent Auth has recorded it.
pub(crate) const BOT_ID_SUFFIX: &str = "/bot-id";

/// Where a brought App's webhook stays on purpose instead of pointing at this
/// deployment, e.g. a service that receives the events and forwards them.
/// Deploy stores one marker per kept URL, `…/webhook-elsewhere/<digest>`, next
/// to the App's connection, and only checks that it exists: it never reads a
/// stored value back.
pub(crate) const WEBHOOK_ELSEWHERE_SUFFIX: &str = "/webhook-elsewhere/";
/// Keeps a brought App's webhook at this URL without a terminal to ask.
const WEBHOOK_ELSEWHERE_ENV: &str = "MDA_GITHUB_WEBHOOK_ELSEWHERE";
/// Hex characters of a kept URL's SHA-256 in its marker's slug.
const WEBHOOK_DIGEST_LENGTH: usize = 16;

const NO_HANDLERS: &str = "The GitHub channel handles no events yet, so no GitHub App is set up. Add handlers with `channels.github({ onEvent: { ... } })` and deploy again.";

const SKIPPED_SETUP: &str = "Continuing without the GitHub channel. The deployment is live, but it receives no GitHub events until you run `mda deploy` again and finish setting up the GitHub App.";

/// Where a new App is created: an organization login, or [`PERSONAL_OWNER`].
/// Without a terminal deploy cannot ask, so this must be set.
const APP_OWNER_ENV: &str = "MDA_GITHUB_APP_OWNER";
const PERSONAL_OWNER: &str = "@me";

const OWNER_REQUIRED: &str = "This agent's GitHub channel needs a GitHub App, and without a terminal deploy cannot ask where to create it. Set MDA_GITHUB_APP_OWNER in the shell or .env to the GitHub organization that should own the App, or to @me for the personal account of whoever creates it, then run `mda deploy` again.";

const APP_ID_HINT: &str = "Enter the numeric App ID from the App's settings page.";
const INSTALLATION_ID_HINT: &str =
    "Enter the numeric installation ID from the end of the installation's settings URL.";

const EXISTING_APP_NEEDS_TERMINAL: &str = "This agent's GitHub App was brought in by its ID and key and is not installed in exactly one account yet. Install it on GitHub, or run `mda deploy` in a terminal once to choose or install it.";

/// The `github_app` connection that holds a deployment's App, and the values
/// stored next to it. One App belongs to one connection, so the slug is per
/// deployment.
pub(crate) const SLUGS: AppSlugs = AppSlugs::new("mda/github-app/");

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AppSource {
    New,
    Existing,
}

/// The deployment's App in Agent Auth, with its `github_app` connection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppConnection {
    pub(crate) app: AgentAuthApp,
    /// The connection's OAuth provider; empty while the App is pending.
    pub(crate) provider_id: String,
    pub(crate) state: GitHubAppState,
    /// `permission:level` scopes the agent's token is minted with, sorted.
    pub(crate) scopes: Vec<String>,
    /// Whether the agent already holds a credential on it, i.e. the App is
    /// installed and set up.
    pub(crate) installed: bool,
    /// Whether the App's webhook secret and bot ID are stored next to it.
    pub(crate) webhook_secret_stored: bool,
    pub(crate) bot_id_stored: bool,
    /// Digests of the webhook URLs the App keeps on purpose; see
    /// [`WEBHOOK_ELSEWHERE_SUFFIX`].
    pub(crate) kept_webhooks: BTreeSet<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WaitTick {
    Elapsed,
    Interrupted,
}

/// Everything setup does that touches the terminal, the network or Agent
/// Auth, so the flow itself is testable.
pub(crate) trait SetupEnv: SetupUi {
    /// The deployment's GitHub App, if it has one.
    fn app_connection(&mut self) -> Result<Option<AppConnection>>;
    fn choose_app_source(&mut self) -> Result<AppSource>;
    /// Create a pending App built from the channel under `owner`, with the
    /// session that creates and installs it.
    fn create_app(
        &mut self,
        channel: &GitHubChannel,
        owner: &AppOwner,
    ) -> Result<(AppConnection, OAuthAuthorizationSession)>;
    /// Prompt for an existing App and its webhook secret and store it.
    fn store_existing_app(&mut self) -> Result<AppConnection>;
    /// Ask where a new App should be created.
    fn choose_owner(&mut self) -> Result<AppOwner>;
    /// Ask for the installation of an App the user brought.
    fn ask_installation_id(&mut self) -> Result<String>;
    /// Pick one of several installations of an App the user brought.
    fn choose_installation(&mut self, installations: &[GitHubInstallation]) -> Result<String>;
    /// The webhook secret of an App the user brought: `GH_WEBHOOK_SECRET`
    /// when set, else asked for. `None` when neither is possible.
    fn webhook_secret(&mut self) -> Result<Option<String>>;
    /// Store a value the runtime reads as the secret connection next to the
    /// App's, e.g. [`WEBHOOK_SECRET_SUFFIX`].
    fn store_app_value(&mut self, suffix: &str, value: &str) -> Result<()>;
    fn start_session(
        &mut self,
        connection: &AppConnection,
        start: &GitHubAppSessionStart,
    ) -> Result<OAuthAuthorizationSession>;
    /// Show and open the page that creates or installs the App.
    fn open_setup_page(&mut self, url: &str, creating: bool) -> Result<()>;
    /// Wait for the session to change and return it.
    fn next_session(&mut self, session_id: &str) -> Result<OAuthAuthorizationSession>;
    /// The session's status now, without waiting for it to change.
    fn session_status(&mut self, session_id: &str) -> Result<String>;
    /// Mint the agent's token with `scopes`. Agent Auth drops the current
    /// token, so a session has to renew it.
    fn update_token_scopes(&mut self, provider_id: &str, scopes: &[String]) -> Result<()>;
    /// The App as GitHub reports it now.
    fn sync(&mut self) -> GitHubSyncOutcome;
    /// Where this deployment receives the App's events.
    fn webhook_url(&self) -> &str;
    /// Ask whether to point an App's webhook, which goes to `current`, at
    /// this deployment.
    fn confirm_webhook_replace(&mut self, current: &str) -> Result<bool>;
    /// Point the App's webhook at this deployment.
    fn point_webhook(&mut self) -> Result<()>;
    /// `MDA_GITHUB_WEBHOOK_ELSEWHERE`: a webhook URL the App keeps without a
    /// terminal to ask.
    fn webhook_elsewhere_override(&self) -> Option<&str>;
    /// Delete the markers of webhook URLs the App kept.
    fn forget_kept_webhooks(&mut self) -> Result<()>;
    /// Show and open the install page while setup waits for the install.
    fn open_install_page(&mut self, url: &str) -> Result<()>;
    fn wait(&mut self, duration: Duration) -> WaitTick;
}

/// Run GitHub App setup against a live deployment.
pub(crate) fn setup(
    client: &langsmith::Client,
    channel: &GitHubChannel,
    agent_server_url: &str,
    deployment_id: &str,
    project_root: &Path,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    let webhook_url = channel_app::webhook_url(agent_server_url, &channel.events_path)?;
    let agent_auth = agent_auth(client, env_vars)?;
    if let Some(url) = override_value(AGENT_AUTH_URL_ENV, env_vars) {
        log::info(format!("Using Agent Auth at {url}"))?;
    }
    let mut env = LiveSetup {
        agent_auth,
        deployment_id,
        agent_server_url,
        webhook_url,
        project_root,
        interactive: is_interactive(),
        app_id: None,
        webhook_secret_override: override_value(WEBHOOK_SECRET_ENV, env_vars),
        owner_override: override_value(APP_OWNER_ENV, env_vars),
        webhook_elsewhere_override: override_value(WEBHOOK_ELSEWHERE_ENV, env_vars),
    };
    run_setup(&mut env, channel)
}

/// Check, before anything is deployed, that GitHub App setup will be able to
/// finish. A malformed `MDA_GITHUB_APP_OWNER` always fails here. Without a
/// terminal, setup that would need an answer nobody can give fails here too,
/// instead of after the deployment is live. `deployment_id` is the existing
/// deployment, if any.
pub(crate) fn preflight(
    client: &langsmith::Client,
    channel: &GitHubChannel,
    deployment_id: Option<&str>,
    runtime_url: Option<&str>,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    let owner = override_value(APP_OWNER_ENV, env_vars)
        .map(|owner| parse_owner(&owner))
        .transpose()?;
    let interactive = is_interactive();
    if interactive || channel.handled.is_empty() {
        return Ok(());
    }
    let Some(deployment_id) = deployment_id else {
        return match unattended_setup_problem(None, owner.is_some()) {
            Some(problem) => bail!(problem),
            None => Ok(()),
        };
    };
    let agent_auth = agent_auth(client, env_vars)?;
    let connection = lookup_app_connection(&agent_auth, deployment_id)
        .context("failed to check the GitHub App before deploying")?;
    if let Some(problem) = unattended_setup_problem(connection.as_ref(), owner.is_some()) {
        bail!(problem);
    }
    // A brought App whose webhook points elsewhere would fail the deploy
    // once it is live; say so before anything is uploaded.
    let Some(connection) = connection.filter(|connection| connection.installed) else {
        return Ok(());
    };
    let Some(expected) =
        runtime_url.and_then(|url| channel_app::webhook_url(url, &channel.events_path).ok())
    else {
        return Ok(());
    };
    if !webhook_needs_consent(&connection, &expected) {
        return Ok(());
    }
    // A failed check is left to setup, which reports it after the deploy.
    if let GitHubSyncOutcome::Synced(status) = agent_auth.github_app_report(&connection.app.id) {
        if let Some(current) = status.app.webhook_url.as_deref() {
            let kept = override_value(WEBHOOK_ELSEWHERE_ENV, env_vars);
            if !current.is_empty()
                && current != expected
                && !keeps_webhook(&connection, current, kept.as_deref())
            {
                bail!(webhook_elsewhere(&status.app.slug, current, &expected));
            }
        }
    }
    Ok(())
}

/// Whether moving the App's webhook to `expected` takes it from a receiver
/// someone else set up. Agent Auth points an App it created at the
/// deployment from the start, and records where deploy last pointed it.
fn webhook_needs_consent(connection: &AppConnection, expected: &str) -> bool {
    connection.app.manifest.webhook_url != expected
}

/// An App has one webhook URL. An App Agent Auth points at this deployment
/// follows it; one whose webhook serves something else only moves with
/// consent, and without a terminal deploy fails instead. A "No" is
/// remembered for that URL, so later deploys, with a terminal or without,
/// keep it there and don't ask again.
fn claim_webhook(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    mut status: GitHubChannelStatus,
) -> Result<GitHubChannelStatus> {
    let current = status.app.webhook_url.clone().unwrap_or_default();
    if current == env.webhook_url() {
        forget_kept_webhooks(env, connection)?;
        return Ok(status);
    }
    if !current.is_empty() && webhook_needs_consent(connection, env.webhook_url()) {
        if keeps_webhook(connection, &current, env.webhook_elsewhere_override()) {
            if !connection.kept_webhooks.contains(&webhook_digest(&current)) {
                remember_kept_webhook(env, &current)?;
            }
            let kept = webhook_kept(&status.app.slug, &current, env.webhook_url());
            env.info(&kept)?;
            return Ok(status);
        }
        if !env.interactive() {
            bail!(webhook_elsewhere(
                &status.app.slug,
                &current,
                env.webhook_url()
            ));
        }
        match env.confirm_webhook_replace(&current) {
            Ok(true) => {}
            Ok(false) => {
                remember_kept_webhook(env, &current)?;
                let kept = webhook_kept(&status.app.slug, &current, env.webhook_url());
                env.info(&kept)?;
                return Ok(status);
            }
            // The deployment is already live: Escape or Ctrl-C keeps the
            // webhook where it is for now, instead of failing the deploy, and
            // the next deploy asks again.
            Err(error) if is_cancellation(&error) => {
                env.warning(&format!(
                    "GitHub App {} keeps sending its events to {current}, so this deployment receives none. Run `mda deploy` again to point it here.",
                    status.app.slug
                ))?;
                return Ok(status);
            }
            Err(error) => return Err(error),
        }
    }
    match env.point_webhook() {
        Ok(()) => {
            status.app.webhook_url = Some(env.webhook_url().to_string());
            forget_kept_webhooks(env, connection)?;
        }
        Err(error) => env.warning(&format!(
            "Could not point GitHub App {}'s webhook at this deployment ({error:#}), so it receives no events. Run `mda deploy` again to retry.",
            status.app.slug
        ))?,
    }
    Ok(status)
}

/// SHA-256 of a webhook URL, shortened, for its marker's slug.
fn webhook_digest(url: &str) -> String {
    use sha2::{Digest, Sha256};

    Sha256::digest(url.trim().as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>()[..WEBHOOK_DIGEST_LENGTH]
        .to_string()
}

/// Whether the App's webhook stays at `url` on purpose: a marker an earlier
/// deploy stored for it, or `MDA_GITHUB_WEBHOOK_ELSEWHERE` naming it.
fn keeps_webhook(connection: &AppConnection, url: &str, kept: Option<&str>) -> bool {
    connection.kept_webhooks.contains(&webhook_digest(url))
        || kept.is_some_and(|kept| kept.trim() == url.trim())
}

/// Remember that the App's webhook stays at `url`. Never fails the deploy:
/// without the marker, the next deploy just asks again.
fn remember_kept_webhook(env: &mut dyn SetupEnv, url: &str) -> Result<()> {
    let suffix = format!("{WEBHOOK_ELSEWHERE_SUFFIX}{}", webhook_digest(url));
    if let Err(error) = env.store_app_value(&suffix, url.trim()) {
        env.warning(&format!(
            "Could not remember to keep the GitHub App's webhook at {url} ({error:#}), so the next deploy asks again."
        ))?;
    }
    Ok(())
}

/// The webhook points at this deployment now, so an earlier "keep it there"
/// no longer applies. Never fails the deploy.
fn forget_kept_webhooks(env: &mut dyn SetupEnv, connection: &AppConnection) -> Result<()> {
    if connection.kept_webhooks.is_empty() {
        return Ok(());
    }
    if let Err(error) = env.forget_kept_webhooks() {
        env.warning(&format!(
            "Could not forget the webhook URL the GitHub App kept before ({error:#}). Deploy ignores it while the webhook points here."
        ))?;
    }
    Ok(())
}

fn webhook_kept(slug: &str, current: &str, expected: &str) -> String {
    format!(
        "GitHub App {slug} keeps sending its events to {current}. Forward the ones this agent handles to {expected}, with the body and GitHub's headers unchanged. Deploy doesn't ask again while the webhook stays there; to receive the events here instead, set the App's webhook URL to {expected}."
    )
}

fn webhook_elsewhere(slug: &str, current: &str, expected: &str) -> String {
    format!(
        "GitHub App {slug} sends its events to {current}. An App has one webhook URL, so deploy does not move it without asking. Change it to {expected} on the App's settings page, or run `mda deploy` in a terminal to confirm the change, then deploy again. If it stays there on purpose, for example because that service forwards the events here, run `mda deploy` in a terminal once and keep it, or set {WEBHOOK_ELSEWHERE_ENV}={current}."
    )
}

/// How Agent Auth reports that GitHub refused the permissions a token asks for.
const PERMISSIONS_REJECTED: &str = "GitHub rejected the requested permissions";

/// GitHub refused the agent's first token because the installation lacks a
/// permission the channel needs; the channel config is the other way out.
fn explain_refused_token(error: anyhow::Error) -> anyhow::Error {
    let refused = error.downcast_ref::<ApiError>().is_some_and(|api| {
        api.status == reqwest::StatusCode::BAD_GATEWAY && api.detail.contains(PERMISSIONS_REJECTED)
    });
    if !refused {
        return error;
    }
    error.context(
        "GitHub refused the agent's token. Grant what is missing to the App on GitHub and accept it on the installation, or remove the handlers or app.permissions that need it from the channel, then try again",
    )
}

/// Why setup without a terminal cannot finish, if it cannot.
fn unattended_setup_problem(
    connection: Option<&AppConnection>,
    owner_set: bool,
) -> Option<&'static str> {
    match connection {
        Some(connection) if connection.installed => None,
        // The install page only needs a link.
        Some(connection) if connection.state == GitHubAppState::Created => None,
        Some(connection) if connection.state == GitHubAppState::External => {
            Some(EXISTING_APP_NEEDS_TERMINAL)
        }
        // A new App, or one GitHub has not created yet.
        _ if owner_set => None,
        _ => Some(OWNER_REQUIRED),
    }
}

/// The deployment's App and what is stored next to it.
fn lookup_app_connection(
    agent_auth: &langsmith::Client,
    deployment_id: &str,
) -> Result<Option<AppConnection>> {
    let Some(app) = agent_auth
        .agent_apps(deployment_id)
        .context("failed to look up the GitHub App")?
        .into_iter()
        .find(|app| app.provider == PROVIDER)
    else {
        return Ok(None);
    };
    let listed = agent_auth
        .list_connections_raw()
        .context("failed to look up the GitHub App connection")?;
    let stored =
        |suffix: &str| find_connection(&listed, &format!("{}{suffix}", app.slug)).is_some();
    let webhook_secret_stored = stored(WEBHOOK_SECRET_SUFFIX);
    let bot_id_stored = stored(BOT_ID_SUFFIX);
    let kept_prefix = format!("{}{WEBHOOK_ELSEWHERE_SUFFIX}", app.slug);
    let kept_webhooks: BTreeSet<String> = listed
        .iter()
        .filter_map(|connection| connection.slug.strip_prefix(&kept_prefix))
        .map(str::to_string)
        .collect();
    let state = app.state();
    let Some(connection_id) = app.connection_id.clone() else {
        // Pending: GitHub has not created the App, so it has no connection.
        return Ok(Some(AppConnection {
            app,
            provider_id: String::new(),
            state,
            scopes: Vec::new(),
            installed: false,
            webhook_secret_stored,
            bot_id_stored,
            kept_webhooks,
        }));
    };
    let provider_id = listed
        .iter()
        .find(|connection| connection.connection_id == connection_id)
        .and_then(|connection| connection.oauth_provider_id.clone())
        .context("the GitHub App's connection has no OAuth provider")?;
    let scopes = agent_auth.github_app_scopes(&provider_id)?;
    let installed = agent_auth.connection_has_agent_credential(&connection_id, deployment_id)?;
    Ok(Some(AppConnection {
        app,
        provider_id,
        state,
        scopes,
        installed,
        webhook_secret_stored,
        bot_id_stored,
        kept_webhooks,
    }))
}

/// The deployment whose Agent Auth app holds the GitHub App `app_id`, with
/// that app: the deployment named by the connection on the provider Agent
/// Auth registered for the App, once it is confirmed to own an app with that
/// App ID. `None` when no deployment's GitHub channel holds it.
fn app_holder(
    agent_auth: &langsmith::Client,
    app_id: &str,
) -> Result<Option<(String, AgentAuthApp)>> {
    let providers = agent_auth.github_app_provider_ids(app_id)?;
    if providers.is_empty() {
        return Ok(None);
    }
    let connections = agent_auth.list_connections_raw()?;
    let deployments = connections
        .iter()
        .filter(|connection| {
            connection
                .oauth_provider_id
                .as_ref()
                .is_some_and(|id| providers.contains(id))
        })
        .filter_map(|connection| SLUGS.deployment_of(&connection.slug));
    for deployment_id in deployments {
        let owned = agent_auth
            .agent_apps(deployment_id)?
            .into_iter()
            .find(|app| app.provider == PROVIDER && app.app_id.as_deref() == Some(app_id));
        if let Some(app) = owned {
            return Ok(Some((deployment_id.to_string(), app)));
        }
    }
    Ok(None)
}

/// Agent Auth refused the App because another of its apps holds it.
fn app_taken(error: &anyhow::Error) -> bool {
    error
        .chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())
        .is_some_and(|api| api.status == reqwest::StatusCode::CONFLICT)
}

pub(crate) fn run_setup(env: &mut dyn SetupEnv, channel: &GitHubChannel) -> Result<()> {
    if channel.handled.is_empty() {
        return env.info(NO_HANDLERS);
    }
    let connection = match env.app_connection()? {
        Some(connection) if connection.installed => connection,
        // The deployment is already live: a failed step offers to try again
        // or to continue without GitHub. Without a terminal nobody can
        // choose, so the error ends the deploy.
        current => {
            let set_up = channel_app::retry_or_skip(
                env,
                current,
                SKIPPED_SETUP,
                |env, current| set_up_app(env, channel, current),
                // GitHub may have created the App before the step failed.
                |env| env.app_connection(),
            )?;
            match set_up {
                Some(connection) => connection,
                None => return Ok(()),
            }
        }
    };

    let Some(status) = sync_once(env)? else {
        return Ok(());
    };
    let mut status = claim_webhook(env, &connection, status)?;
    if status.installations.is_empty() && env.interactive() {
        status = wait_for_installation(env, status)?;
    }

    let report = report::build(channel, &status);
    if let Some(ready) = &report.ready {
        env.success(ready)?;
    }
    for warning in &report.warnings {
        env.warning(warning)?;
    }
    for note in &report.notes {
        env.info(note)?;
    }
    store_app_values(env, &connection, &status)?;
    sync_token_scopes(env, channel, &connection, &status)
}

/// The runtime reads the App's webhook secret and bot ID from Agent Auth, so
/// nobody puts them in `.env`. Agent Auth stores the webhook secret with the
/// App and records its bot ID; deploy stores the bot ID where the runtime
/// reads it. Never fails the deploy.
fn store_app_values(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    status: &GitHubChannelStatus,
) -> Result<()> {
    if !connection.webhook_secret_stored && connection.state == GitHubAppState::External {
        match store_webhook_secret(env) {
            Ok(true) => {}
            Ok(false) => env.warning(
                "Agent Auth has no webhook secret for this GitHub App, so the channel rejects its events. Run `mda deploy` in a terminal to add it.",
            )?,
            Err(error) if is_cancellation(&error) => env.warning(
                "Skipped adding the GitHub App's webhook secret. The channel rejects its events until you run `mda deploy` again and add it.",
            )?,
            Err(error) => env.warning(&format!(
                "Could not store the GitHub App's webhook secret ({error:#}). Run `mda deploy` again to add it."
            ))?,
        }
    }
    if !connection.bot_id_stored {
        let stored = match status.app.bot_id {
            Some(bot_id) => env.store_app_value(BOT_ID_SUFFIX, &bot_id.to_string()),
            None => Err(anyhow::anyhow!(
                "GitHub did not report the App's bot account"
            )),
        };
        if let Err(error) = stored {
            env.warning(&format!(
                "Could not store the GitHub App's bot ID ({error:#}), so the channel cannot handle its events yet. Run `mda deploy` again."
            ))?;
        }
    }
    Ok(())
}

/// Ask for a brought App's webhook secret and store it. `false` when it
/// cannot be asked for.
fn store_webhook_secret(env: &mut dyn SetupEnv) -> Result<bool> {
    let Some(secret) = env.webhook_secret()? else {
        return Ok(false);
    };
    env.store_app_value(WEBHOOK_SECRET_SUFFIX, &secret)?;
    Ok(true)
}

/// Reconcile the agent's token with the channel's permissions. A change
/// that drops a permission or lowers its level never leaves the agent with
/// the broader token: it is narrowed now, or the agent has no token until a
/// later deploy can mint the narrower one.
fn sync_token_scopes(
    env: &mut dyn SetupEnv,
    channel: &GitHubChannel,
    connection: &AppConnection,
    status: &GitHubChannelStatus,
) -> Result<()> {
    let active: Vec<&GitHubInstallation> = status
        .installations
        .iter()
        .filter(|installation| installation.suspended_at.is_none())
        .collect();
    // Without an installation there is no token to describe or renew.
    if active.is_empty() {
        return Ok(());
    }
    let scopes = permission_scopes(channel);
    if connection.scopes == scopes {
        return env.info(&report::token_summary(
            channel, status, active[0], &scopes, false,
        ));
    }
    let narrowing = report::narrows(&connection.scopes, &scopes);
    let installation = match active.as_slice() {
        [installation] => *installation,
        _ if narrowing => {
            return drop_token(
                env,
                connection,
                &scopes,
                "The GitHub App is installed more than once, so the agent's GitHub token cannot be renewed with fewer permissions",
            )
        }
        _ => {
            return env.info(
                "The GitHub App is installed more than once, so the agent's GitHub token keeps its current permissions.",
            )
        }
    };
    let mut target = scopes.clone();
    if !report::grants(&channel.permissions, &installation.permissions) {
        if !narrowing {
            return env.info(&report::token_summary(
                channel,
                status,
                installation,
                &connection.scopes,
                false,
            ));
        }
        // Narrow now to what the installation grants; the rest follows once
        // it grants them.
        target = report::granted_scopes(&channel.permissions, &installation.permissions);
        if target.is_empty() {
            return drop_token(
                env,
                connection,
                &scopes,
                "The installation grants none of the permissions above",
            );
        }
    }
    match renew_token(env, connection, &target, installation.id) {
        Ok(()) => env.info(&report::token_summary(
            channel,
            status,
            installation,
            &target,
            true,
        )),
        Err(RenewalFailure { error, token_kept: true }) if narrowing => env.warning(&format!(
            "Could not update the agent's GitHub token ({error:#}), so it still has its previous, broader permissions. Run `mda deploy` again to retry."
        )),
        Err(RenewalFailure { error, token_kept: true }) => env.warning(&format!(
            "Could not update the agent's GitHub token ({error:#}), so it keeps its current permissions. Run `mda deploy` again to retry."
        )),
        Err(RenewalFailure { error, token_kept: false }) => env.warning(&format!(
            "Could not update the agent's GitHub token ({error:#}), and the agent has no GitHub token until it is renewed. Run `mda deploy` again to set it up."
        )),
    }
}

/// Set the narrower scopes without minting a token, which drops the
/// agent's current one: no token beats a broader one.
fn drop_token(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    scopes: &[String],
    reason: &str,
) -> Result<()> {
    match env.update_token_scopes(&connection.provider_id, scopes) {
        Ok(()) => env.warning(&format!(
            "{reason}, and it may not keep its broader ones, so the agent has no GitHub token for now. Run `mda deploy` again once that is fixed."
        )),
        Err(error) => env.warning(&format!(
            "Could not update the agent's GitHub token ({error:#}), so it still has its previous, broader permissions. Run `mda deploy` again to retry."
        )),
    }
}

struct RenewalFailure {
    error: anyhow::Error,
    /// Whether the agent still has a working token.
    token_kept: bool,
}

/// Change the token's scopes and mint it again. When minting fails, the old
/// scopes come back only if they are not broader than the new ones; a
/// narrowing leaves the agent without a token instead.
fn renew_token(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    scopes: &[String],
    installation_id: u64,
) -> std::result::Result<(), RenewalFailure> {
    if let Err(error) = env.update_token_scopes(&connection.provider_id, scopes) {
        return Err(RenewalFailure {
            error,
            token_kept: true,
        });
    }
    let Err(error) = mint_agent_token(env, connection, installation_id) else {
        return Ok(());
    };
    if report::narrows(&connection.scopes, scopes) {
        return Err(RenewalFailure {
            error,
            token_kept: false,
        });
    }
    let token_kept = env
        .update_token_scopes(&connection.provider_id, &connection.scopes)
        .and_then(|()| mint_agent_token(env, connection, installation_id))
        .is_ok();
    Err(RenewalFailure { error, token_kept })
}

/// Give the agent a token on the installation with the provider's scopes.
fn mint_agent_token(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    installation_id: u64,
) -> Result<()> {
    let start = GitHubAppSessionStart::Installation(installation_id.to_string());
    let session = env.start_session(connection, &start)?;
    if session.status != "completed" {
        bail!("Agent Auth did not renew the token");
    }
    Ok(())
}

/// Get the agent a credential on the App's connection, creating the
/// connection first when there is none. An abandoned setup resumes on the
/// connection it left behind.
fn set_up_app(
    env: &mut dyn SetupEnv,
    channel: &GitHubChannel,
    connection: Option<AppConnection>,
) -> Result<AppConnection> {
    // The owner is chosen before anything is created, so a missing owner
    // leaves no App behind.
    let (current, created) = match connection {
        Some(current) => (current, None),
        None => match env.choose_app_source()? {
            AppSource::New => {
                let owner = env.choose_owner()?;
                let (created, session) = env.create_app(channel, &owner)?;
                (created, Some(session))
            }
            // Asked with the App's ID, key and webhook secret.
            AppSource::Existing => (env.store_existing_app()?, None),
        },
    };
    let session = match (created, current.state) {
        (Some(session), _) => session,
        (None, GitHubAppState::Pending) => {
            let organization = match env.choose_owner()? {
                AppOwner::Organization(org) => Some(org),
                AppOwner::Personal => None,
            };
            start_session(
                env,
                &current,
                &GitHubAppSessionStart::Create { organization },
            )?
        }
        (None, GitHubAppState::Created) => {
            start_session(env, &current, &GitHubAppSessionStart::Install)?
        }
        (None, GitHubAppState::External) => brought_app_session(env, &current)?,
    };
    if session.status != "completed" {
        env.open_setup_page(
            &session.verification_url,
            current.state == GitHubAppState::Pending,
        )?;
        follow_setup_session(env, session)?;
    }
    env.success("The GitHub App is set up for this agent.")?;
    // GitHub created the App, or the agent now holds its credential.
    Ok(env.app_connection()?.unwrap_or(current))
}

/// Follow a setup session until GitHub has created (when needed) and
/// installed the App.
fn follow_setup_session(
    env: &mut dyn SetupEnv,
    mut session: OAuthAuthorizationSession,
) -> Result<()> {
    loop {
        match session.status.as_str() {
            "pending" => session = env.next_session(&session.id)?,
            "completed" => return Ok(()),
            "failed" => {
                // Agent Auth lists every App it could not store on the app,
                // so their pages can be named.
                let stranded = match session.error_code.as_deref() {
                    Some("app_store_failed") => env
                        .app_connection()
                        .ok()
                        .flatten()
                        .map(|connection| {
                            let urls = connection.app.stranded_urls();
                            urls.into_iter().map(str::to_string).collect()
                        })
                        .unwrap_or_default(),
                    _ => Vec::new(),
                };
                bail!(setup_failure_message(
                    session.error_code.as_deref(),
                    &stranded
                ))
            }
            "expired" => bail!(SETUP_EXPIRED),
            other => bail!("GitHub App setup returned an unexpected status: {other}."),
        }
    }
}

/// Why a setup session failed, from its `error_code`. `stranded` are the
/// pages of the Apps GitHub created that Agent Auth could not store, newest
/// last.
fn setup_failure_message(code: Option<&str>, stranded: &[String]) -> String {
    match code {
        Some("app_creation_denied") => {
            "GitHub did not create the App. Create it on the GitHub page, or choose another account.".to_string()
        }
        Some("conversion_failed") => {
            "GitHub could not hand over the new App; its one-time code may have expired. If GitHub shows the App, delete it there before trying again.".to_string()
        }
        Some("connection_failed") => {
            "Agent Auth could not look up the GitHub App it was setting up, so it did not finish creating it. If GitHub shows the App, delete it there before trying again.".to_string()
        }
        Some("app_store_failed") => {
            let message = "The App was created on GitHub, but Agent Auth could not store it. Delete it on GitHub before trying again.";
            match stranded {
                [] => message.to_string(),
                [url] => format!("{message}\n{url}"),
                urls => format!(
                    "{message} Earlier attempts left Apps on GitHub too; delete them all (newest last):\n{}",
                    urls.join("\n")
                ),
            }
        }
        Some(other) => format!("GitHub App setup failed ({other})."),
        None => "GitHub App setup failed. If GitHub created the App, delete it on GitHub before trying again.".to_string(),
    }
}

fn start_session(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
    start: &GitHubAppSessionStart,
) -> Result<OAuthAuthorizationSession> {
    env.start_session(connection, start)
        .map_err(explain_refused_token)
}

/// The session that gives the agent a credential on an App the user brought.
/// GitHub does not tell Agent Auth about installs made outside its sessions,
/// so it records the one active installation GitHub reports, or one the user
/// picks. With none, the user installs the App through an install session.
/// The user names the installation only when GitHub cannot be asked.
fn brought_app_session(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
) -> Result<OAuthAuthorizationSession> {
    let installation = match sync_with_retry(env)? {
        GitHubSyncOutcome::Synced(status) => match active_installations(&status).as_slice() {
            [installation] => installation.id.to_string(),
            [] if env.interactive() => return install_brought_app(env, connection),
            several @ [_, _, ..] if env.interactive() => env.choose_installation(several)?,
            _ => env.ask_installation_id()?,
        },
        _ => env.ask_installation_id()?,
    };
    start_session(
        env,
        connection,
        &GitHubAppSessionStart::Installation(installation),
    )
}

/// Install a brought App through an install session, so GitHub's redirect
/// completes it in Agent Auth. An App whose setup URL does not lead back to
/// Agent Auth installs without completing the session, so GitHub's report is
/// watched too, and whichever shows the installation first wins.
fn install_brought_app(
    env: &mut dyn SetupEnv,
    connection: &AppConnection,
) -> Result<OAuthAuthorizationSession> {
    let mut session = start_session(env, connection, &GitHubAppSessionStart::Install)?;
    if session.status == "completed" {
        return Ok(session);
    }
    env.open_install_page(&session.verification_url)?;
    let mut reported_request = false;
    loop {
        // Short polls, so setup goes on soon after the click even when
        // GitHub's redirect never reaches Agent Auth.
        if env.wait(INSTALL_POLL_INTERVAL) == WaitTick::Interrupted {
            return Err(std::io::Error::from(std::io::ErrorKind::Interrupted).into());
        }
        match env.session_status(&session.id)?.as_str() {
            "pending" => {}
            "completed" => {
                session.status = "completed".into();
                return Ok(session);
            }
            "failed" => bail!(setup_failure_message(None, &[])),
            "expired" => bail!(SETUP_EXPIRED),
            other => bail!("GitHub App setup returned an unexpected status: {other}."),
        }
        let GitHubSyncOutcome::Synced(status) = env.sync() else {
            continue;
        };
        let installation = match active_installations(&status).as_slice() {
            [] => {
                if let (false, Some(request)) =
                    (reported_request, status.installation_requests.first())
                {
                    env.info(&format!(
                        "Waiting for an owner of {} to approve the installation. Press Ctrl-C to stop waiting.",
                        request.account.login
                    ))?;
                    reported_request = true;
                }
                continue;
            }
            [installation] => installation.id.to_string(),
            several => env.choose_installation(several)?,
        };
        return start_session(
            env,
            connection,
            &GitHubAppSessionStart::Installation(installation),
        );
    }
}

fn active_installations(status: &GitHubChannelStatus) -> Vec<GitHubInstallation> {
    status
        .installations
        .iter()
        .filter(|installation| installation.suspended_at.is_none())
        .cloned()
        .collect()
}

/// Sync, retrying while a freshly deployed Agent Server warms up.
fn sync_with_retry(env: &mut dyn SetupEnv) -> Result<GitHubSyncOutcome> {
    let mut outcome = env.sync();
    for attempt in 1..SYNC_ATTEMPTS {
        if !matches!(outcome, GitHubSyncOutcome::Unavailable(_)) {
            break;
        }
        if attempt == 1 {
            env.info("Retrying the GitHub App check…")?;
        }
        if env.wait(SYNC_RETRY_DELAY) == WaitTick::Interrupted {
            break;
        }
        outcome = env.sync();
    }
    Ok(outcome)
}

/// One sync (with retries). `None` means the deploy should finish without a
/// GitHub report.
fn sync_once(env: &mut dyn SetupEnv) -> Result<Option<GitHubChannelStatus>> {
    match sync_with_retry(env)? {
        GitHubSyncOutcome::Synced(status) => Ok(Some(*status)),
        GitHubSyncOutcome::MissingCredentials => {
            env.warning(
                "Agent Auth has no GitHub App for this agent. Check its connection with `mda connections list`, then run `mda deploy` again.",
            )?;
            Ok(None)
        }
        GitHubSyncOutcome::InvalidCredentials => {
            env.warning(
                "GitHub rejected the stored App credentials. Delete the deployment with `mda delete` to remove its App from Agent Auth, then deploy again to set up a new one.",
            )?;
            Ok(None)
        }
        GitHubSyncOutcome::NotCreated => {
            env.warning(
                "GitHub has not created this agent's App yet. Run `mda deploy` again to finish setting it up.",
            )?;
            Ok(None)
        }
        GitHubSyncOutcome::Unavailable(reason) => {
            env.warning(&format!(
                "The deployment is live, but the GitHub App could not be checked ({reason}). Run `mda deploy` again to retry."
            ))?;
            Ok(None)
        }
    }
}

fn wait_for_installation(
    env: &mut dyn SetupEnv,
    mut status: GitHubChannelStatus,
) -> Result<GitHubChannelStatus> {
    env.open_install_page(&app_manifest::install_url(&status.app.slug))?;
    let mut waited = Duration::ZERO;
    let mut reported_request = false;
    while waited < INSTALL_WAIT_LIMIT {
        if env.wait(INSTALL_POLL_INTERVAL) == WaitTick::Interrupted {
            env.warning("Skipped waiting for the GitHub App to be installed.")?;
            return Ok(status);
        }
        waited += INSTALL_POLL_INTERVAL;
        let GitHubSyncOutcome::Synced(next) = env.sync() else {
            continue;
        };
        if !next.installations.is_empty() {
            return Ok(*next);
        }
        if !reported_request {
            if let Some(request) = next.installation_requests.first() {
                env.info(&format!(
                    "Waiting for an owner of {} to approve the installation. Press Ctrl-C to stop waiting.",
                    request.account.login
                ))?;
                reported_request = true;
            }
        }
        status = *next;
    }
    env.warning("Stopped waiting for the GitHub App to be installed.")?;
    Ok(status)
}

/// GitHub App permissions as `permission:level` scopes, sorted.
fn permission_scopes(channel: &GitHubChannel) -> Vec<String> {
    channel
        .permissions
        .iter()
        .map(|(name, level)| format!("{name}:{}", level.as_str()))
        .collect()
}

fn app_manifest(
    channel: &GitHubChannel,
    agent_server_url: &str,
    webhook_url: &str,
) -> GitHubAppManifest {
    GitHubAppManifest {
        name: channel.app_name.clone(),
        url: agent_server_url.to_string(),
        webhook_url: webhook_url.to_string(),
        permissions: channel
            .permissions
            .iter()
            .map(|(name, level)| (name.clone(), level.as_str().to_string()))
            .collect(),
        events: channel.events.clone(),
    }
}

struct LiveSetup<'a> {
    /// Client for Agent Auth calls; see [`channel_app::agent_auth`].
    agent_auth: langsmith::Client,
    deployment_id: &'a str,
    agent_server_url: &'a str,
    webhook_url: String,
    project_root: &'a Path,
    interactive: bool,
    /// The App's ID in Agent Auth once known, so reports need no lookup.
    app_id: Option<String>,
    /// `GH_WEBHOOK_SECRET` from the shell or `.env`, for a brought App.
    webhook_secret_override: Option<String>,
    /// `MDA_GITHUB_APP_OWNER` from the shell or `.env`.
    owner_override: Option<String>,
    /// `MDA_GITHUB_WEBHOOK_ELSEWHERE` from the shell or `.env`.
    webhook_elsewhere_override: Option<String>,
}

impl LiveSetup<'_> {
    /// The App Agent Auth just stored, as setup tracks it. Its connection and
    /// provider are looked up once setup needs them.
    fn track(&mut self, app: AgentAuthApp, webhook_secret_stored: bool) -> AppConnection {
        self.app_id = Some(app.id.clone());
        AppConnection {
            state: app.state(),
            app,
            provider_id: String::new(),
            scopes: Vec::new(),
            installed: false,
            webhook_secret_stored,
            bot_id_stored: false,
            kept_webhooks: BTreeSet::new(),
        }
    }

    /// GitHub sends an App's events to one webhook, so the App serves one
    /// deployment at a time. When another deployment holds it, offer to move
    /// it here by deleting only that deployment's Agent Auth app for it: its
    /// link to the App, with the key, token and webhook secret stored for it.
    fn release_app(&mut self, app_id: &str) -> Result<()> {
        let holder = app_holder(&self.agent_auth, app_id)
            .context("failed to look up which deployment holds the GitHub App")?;
        let Some((deployment_id, app)) = holder.filter(|(id, _)| id != self.deployment_id) else {
            bail!(
                "GitHub App {app_id} is already connected to another Agent Auth app in this \
                 workspace. Use another App, or remove that app's connection first."
            );
        };
        // Agent Auth uninstalls the App when it lets go of it, so the
        // installation is made again once the App is here.
        let moved = cliclack::confirm(format!(
            "GitHub App {app_id} is connected to deployment {deployment_id}. Move it to this \
             deployment? That deployment's GitHub channel stops receiving events, and the App is \
             uninstalled on GitHub, so you install it again next."
        ))
        .initial_value(true)
        .interact()
        .context("failed to confirm moving the GitHub App")?;
        if !moved {
            bail!(
                "GitHub App {app_id} stays connected to deployment {deployment_id}. Use another \
                 App for this deployment."
            );
        }
        self.agent_auth.delete_agent_app(&app.id).with_context(|| {
            format!("failed to release GitHub App {app_id} from deployment {deployment_id}")
        })?;
        log::info(format!(
            "Released GitHub App {app_id} from deployment {deployment_id}."
        ))?;
        Ok(())
    }

    /// Where to register the new App. No GitHub lookup: the user says whether
    /// it belongs to an organization, and GitHub's own page reports an org
    /// that does not exist or that the user cannot create Apps in.
    fn ask_owner(&self) -> Result<AppOwner> {
        let suggested = github_owner_from_remote(self.project_root);
        let mut prompt = cliclack::select("Where should the GitHub App be created?");
        if let Some(org) = &suggested {
            prompt = prompt.item(
                OwnerChoice::Suggested,
                format!("Organization {org}"),
                "from this repository",
            );
        }
        let choice = prompt
            .item(OwnerChoice::OtherOrganization, "Another organization…", "")
            .item(
                OwnerChoice::Personal,
                "My personal GitHub account",
                "the account signed in to GitHub in your browser",
            )
            .interact()
            .context("failed to choose where to create the GitHub App")?;
        match (choice, suggested) {
            (OwnerChoice::Suggested, Some(org)) => Ok(AppOwner::Organization(org)),
            (OwnerChoice::Personal, _) => Ok(AppOwner::Personal),
            _ => {
                let org: String = cliclack::input("Organization name")
                    .placeholder("e.g. acme")
                    .validate(|value: &String| validate_login(value.trim()))
                    .interact()
                    .context("failed to read the organization name")?;
                Ok(AppOwner::Organization(org.trim().to_string()))
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OwnerChoice {
    Suggested,
    OtherOrganization,
    Personal,
}

impl SetupUi for LiveSetup<'_> {
    fn interactive(&self) -> bool {
        self.interactive
    }

    fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool> {
        channel_app::ask_retry_or_skip("GitHub App setup", "GitHub", error)
    }

    fn success(&mut self, message: &str) -> Result<()> {
        channel_app::show_success(message)
    }

    fn info(&mut self, message: &str) -> Result<()> {
        channel_app::show_info(message)
    }

    fn warning(&mut self, message: &str) -> Result<()> {
        channel_app::show_warning(message)
    }
}

impl SetupEnv for LiveSetup<'_> {
    fn app_connection(&mut self) -> Result<Option<AppConnection>> {
        let connection = lookup_app_connection(&self.agent_auth, self.deployment_id)?;
        self.app_id = connection
            .as_ref()
            .map(|connection| connection.app.id.clone());
        Ok(connection)
    }

    fn choose_app_source(&mut self) -> Result<AppSource> {
        // An existing App needs its private key, which only a prompt takes.
        if !self.interactive {
            return Ok(AppSource::New);
        }
        cliclack::select("This agent's GitHub channel needs a GitHub App")
            .item(AppSource::New, "Create a new GitHub App", "recommended")
            .item(
                AppSource::Existing,
                "Use an existing GitHub App",
                "enter its App ID and private key file",
            )
            .interact()
            .context("failed to choose a GitHub App")
    }

    fn create_app(
        &mut self,
        channel: &GitHubChannel,
        owner: &AppOwner,
    ) -> Result<(AppConnection, OAuthAuthorizationSession)> {
        let manifest = app_manifest(channel, self.agent_server_url, &self.webhook_url);
        let organization = match owner {
            AppOwner::Organization(org) => Some(org.as_str()),
            AppOwner::Personal => None,
        };
        let created = self.agent_auth.create_github_app(
            self.deployment_id,
            &SLUGS.connection(self.deployment_id),
            &manifest,
            organization,
        )?;
        // Agent Auth stores the webhook secret once GitHub creates the App.
        Ok((self.track(created.app, true), created.session))
    }

    fn store_existing_app(&mut self) -> Result<AppConnection> {
        let app_id: String = cliclack::input("GitHub App ID")
            .placeholder("shown under About on the App's settings page")
            .validate(|value: &String| parse_github_id(value, APP_ID_HINT).map(|_| ()))
            .interact()
            .context("failed to read the GitHub App ID")?;
        let path: String = cliclack::input("Path to the App's private key (.pem)")
            .validate(|value: &String| read_private_key(Path::new(value.trim())).map(|_| ()))
            .interact()
            .context("failed to read the private key path")?;
        let app_id = parse_github_id(&app_id, APP_ID_HINT)?.to_string();
        let key = read_private_key(Path::new(path.trim()))?;
        let Some(webhook_secret) = self.webhook_secret()? else {
            bail!("The GitHub App's webhook secret is required.");
        };
        // The token starts with the App's permissions; the scope sync after
        // setup narrows it to the channel's once the installation grants them.
        let slug = SLUGS.connection(self.deployment_id);
        let existing = ExistingApp {
            app_id: &app_id,
            private_key: key.expose(),
            webhook_secret: &webhook_secret,
        };
        let app = match self
            .agent_auth
            .bring_github_app(self.deployment_id, &slug, &existing)
        {
            Err(error) if app_taken(&error) => {
                self.release_app(&app_id)?;
                self.agent_auth
                    .bring_github_app(self.deployment_id, &slug, &existing)?
            }
            result => result?,
        };
        Ok(self.track(app, true))
    }

    fn webhook_secret(&mut self) -> Result<Option<String>> {
        if let Some(secret) = self.webhook_secret_override.clone() {
            log::info(format!(
                "Storing {WEBHOOK_SECRET_ENV} in Agent Auth. You can remove it from .env."
            ))?;
            return Ok(Some(secret));
        }
        if !self.interactive {
            return Ok(None);
        }
        let secret: String = cliclack::password(
            "The App's webhook secret (under Webhook on its settings page)",
        )
        .mask('•')
        .validate(|value: &String| {
            if value.trim().is_empty() {
                Err("Enter the webhook secret. If the App has none, set one on its settings page first.")
            } else {
                Ok(())
            }
        })
        .interact()
        .context("failed to read the webhook secret")?;
        Ok(Some(secret.trim().to_string()))
    }

    fn store_app_value(&mut self, suffix: &str, value: &str) -> Result<()> {
        self.agent_auth
            .create_workspace_connection(
                &SLUGS.secret(self.deployment_id, suffix),
                ConnectionOwnerPolicy::Agent,
                Some(self.deployment_id),
                Some(value),
            )
            .map(|_| ())
    }

    fn choose_owner(&mut self) -> Result<AppOwner> {
        if let Some(owner) = &self.owner_override {
            return parse_owner(owner);
        }
        if !self.interactive {
            bail!(OWNER_REQUIRED);
        }
        self.ask_owner()
    }

    fn ask_installation_id(&mut self) -> Result<String> {
        if !self.interactive {
            bail!(EXISTING_APP_NEEDS_TERMINAL);
        }
        let id: String = cliclack::input("GitHub App installation ID")
            .placeholder("the number at the end of the installation's settings URL")
            .validate(|value: &String| parse_github_id(value, INSTALLATION_ID_HINT).map(|_| ()))
            .interact()
            .context("failed to read the installation ID")?;
        Ok(parse_github_id(&id, INSTALLATION_ID_HINT)?.to_string())
    }

    fn choose_installation(&mut self, installations: &[GitHubInstallation]) -> Result<String> {
        let mut prompt = cliclack::select(
            "The GitHub App is installed in several accounts. Which one should this agent use?",
        );
        for installation in installations {
            let kind = if installation.account.is_organization() {
                "organization"
            } else {
                "personal account"
            };
            prompt = prompt.item(installation.id, &installation.account.login, kind);
        }
        let id = prompt
            .interact()
            .context("failed to choose the GitHub App installation")?;
        Ok(id.to_string())
    }

    fn start_session(
        &mut self,
        connection: &AppConnection,
        start: &GitHubAppSessionStart,
    ) -> Result<OAuthAuthorizationSession> {
        self.agent_auth
            .start_github_app_session(&connection.app, self.deployment_id, start)
    }

    fn open_setup_page(&mut self, url: &str, creating: bool) -> Result<()> {
        let action = if creating {
            "Create and install the GitHub App"
        } else {
            "Install the GitHub App"
        };
        if !self.interactive {
            // Whoever runs deploy, e.g. a coding agent, hands this link to
            // the person who can create the App on GitHub.
            log::step(format!(
                "{action} for this agent: open this link in a browser signed in to GitHub. Deploy waits up to an hour for it to finish.\n{url}"
            ))?;
            return Ok(());
        }
        log::step(format!(
            "{action} in your browser. If it does not open, visit:\n{url}\nPress Ctrl-C to skip."
        ))?;
        crate::langsmith::login::open_browser(url);
        Ok(())
    }

    fn next_session(&mut self, session_id: &str) -> Result<OAuthAuthorizationSession> {
        wait_for_oauth_session(&self.agent_auth, session_id, &Interrupt::install()?)
    }

    fn session_status(&mut self, session_id: &str) -> Result<String> {
        Ok(self
            .agent_auth
            .get_oauth_authorization_session(session_id, 0)
            .context("failed to check the GitHub App installation")?
            .status)
    }

    fn update_token_scopes(&mut self, provider_id: &str, scopes: &[String]) -> Result<()> {
        self.agent_auth
            .update_github_app_scopes(provider_id, scopes)
    }

    fn sync(&mut self) -> GitHubSyncOutcome {
        let Some(app_id) = self.app_id.clone() else {
            return GitHubSyncOutcome::MissingCredentials;
        };
        self.agent_auth.github_app_report(&app_id)
    }

    fn webhook_url(&self) -> &str {
        &self.webhook_url
    }

    fn confirm_webhook_replace(&mut self, current: &str) -> Result<bool> {
        cliclack::confirm(format!(
            "This GitHub App sends its events to {current}. Point its webhook at this deployment instead? Whatever receives them now stops getting them."
        ))
        .initial_value(false)
        .interact()
        .context("failed to confirm the GitHub App webhook")
    }

    fn point_webhook(&mut self) -> Result<()> {
        let app_id = self
            .app_id
            .clone()
            .context("the GitHub App is not set up yet")?;
        self.agent_auth
            .point_github_app_webhook(&app_id, &self.webhook_url)
    }

    fn webhook_elsewhere_override(&self) -> Option<&str> {
        self.webhook_elsewhere_override.as_deref()
    }

    fn forget_kept_webhooks(&mut self) -> Result<()> {
        let prefix = SLUGS.secret(self.deployment_id, WEBHOOK_ELSEWHERE_SUFFIX);
        for connection in self.agent_auth.list_connections_raw()? {
            if connection.slug.starts_with(&prefix) {
                self.agent_auth
                    .delete_connection(&connection.connection_id)?;
            }
        }
        Ok(())
    }

    fn open_install_page(&mut self, url: &str) -> Result<()> {
        log::step(format!(
            "Install the GitHub App on your repositories. If your browser does not open, visit:\n{url}\nSetup continues here once GitHub reports the installation, so you can close the page GitHub opens after it. Press Ctrl-C to skip."
        ))?;
        crate::langsmith::login::open_browser(url);
        Ok(())
    }

    fn wait(&mut self, duration: Duration) -> WaitTick {
        let Ok(interrupt) = Interrupt::install() else {
            std::thread::sleep(duration);
            return WaitTick::Elapsed;
        };
        interrupt.sleep_until_interrupted(duration);
        if interrupt.is_requested() {
            WaitTick::Interrupted
        } else {
            WaitTick::Elapsed
        }
    }
}

/// `MDA_GITHUB_APP_OWNER`: an organization login, or `@me`.
fn parse_owner(value: &str) -> Result<AppOwner> {
    let value = value.trim();
    if value == PERSONAL_OWNER {
        return Ok(AppOwner::Personal);
    }
    validate_login(value).with_context(|| {
        format!("{APP_OWNER_ENV} must be a GitHub organization name or {PERSONAL_OWNER}.")
    })?;
    Ok(AppOwner::Organization(value.to_string()))
}

/// A GitHub App or installation ID; `hint` says where to find it.
fn parse_github_id(value: &str, hint: &'static str) -> Result<u64> {
    let value = value.trim();
    match value.parse::<u64>() {
        Ok(id) if id > 0 && value.chars().all(|c| c.is_ascii_digit()) => Ok(id),
        _ => bail!(hint),
    }
}

fn read_private_key(path: &Path) -> Result<PrivateKeyPem> {
    PrivateKeyPem::parse(value_from_file(path)?)
}

/// Owner of the project's `origin` remote when it is hosted on github.com.
fn github_owner_from_remote(root: &Path) -> Option<String> {
    let output = Command::new("git")
        .args(["config", "--get", "remote.origin.url"])
        .current_dir(root)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_github_owner(String::from_utf8(output.stdout).ok()?.trim())
}

fn parse_github_owner(remote: &str) -> Option<String> {
    let path = remote
        .strip_prefix("git@github.com:")
        .or_else(|| remote.strip_prefix("ssh://git@github.com/"))
        .or_else(|| remote.strip_prefix("https://github.com/"))
        .or_else(|| remote.strip_prefix("http://github.com/"))?;
    let owner = path.split('/').next()?;
    validate_login(owner).ok()?;
    Some(owner.to_string())
}

#[cfg(test)]
mod tests;
