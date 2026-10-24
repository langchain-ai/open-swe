//! Linear channel setup during `mda deploy`.
//!
//! A Linear channel needs a Linear OAuth app installed with `actor=app` in the
//! workspace, its webhook pointed at the deployment. Linear has no public API
//! to create apps, so a workspace admin creates it from a page deploy fills
//! in, and pastes back its client ID, client secret and webhook signing
//! secret. Deploy brings them into Agent Auth as an app the deployment owns,
//! which keeps:
//!
//! - the app's OAuth connection, `mda/linear-app/{deployment}`, whose token
//!   the runtime reads as `mda/linear-token` and Agent Auth renews;
//! - the signing secret next to it, at `{slug}/webhook-secret`, which the
//!   runtime verifies deliveries with.
//!
//! Deleting the app takes both with it, and needs only the right to update
//! the deployment. Deployments set up by an earlier mda hold the same two
//! slugs as plain workspace connections, which only a workspace admin can
//! delete: setup keeps using them, and says so when they have to go.
//!
//! The CLI never reads the app's token. It goes by what Agent Auth records
//! about the agent's grant: none, active, or lapsed and waiting for consent
//! again, which only needs the admin to approve the install once more.
//!
//! Setup runs once the deployment is live, because the app's webhook points at
//! the Agent Server URL. `mda delete` removes the Agent Auth app (see
//! [`teardown`]); the app itself stays in Linear.

mod manifest;
pub(crate) mod teardown;
#[cfg(test)]
mod tests;

use std::collections::BTreeMap;

use anyhow::{anyhow, bail, Context, Result};
use cliclack::log;
use reqwest::StatusCode;
use serde_json::{json, Value};

use crate::connection::{poll_oauth_session, wait_for_oauth_status, OAuthWaitMessages};
use crate::deploy::channel_app::{
    self, agent_auth, env_app_notice, find_connection, is_interactive, override_value, AppSlugs,
    SetupUi,
};
use crate::interrupt::Interrupt;
use crate::langsmith::linear_app::ExistingLinearApp;
use crate::langsmith::{
    self, AgentGrant, ApiError, ConnectionOwnerPolicy, OAuthAuthorizationSession,
};

pub(crate) use crate::deploy::channel_app::WEBHOOK_SECRET_SUFFIX;
pub(crate) use manifest::{prepare, LinearChannel, PROVIDER};

/// Linear's token endpoint, which deploy asks whether a client ID and secret
/// belong together. Agent Auth builds the app's OAuth provider itself.
const TOKEN_URL: &str = "https://api.linear.app/oauth/token";
/// Where Linear's apps are created, scoped to a workspace when deploy knows it.
const CREATE_APP_PATH: &str = "settings/api/applications/new";
/// Where a workspace's apps are listed, each with its settings page.
const APPS_PATH: &str = "settings/api";

/// CLI-only settings that replace setup's prompts, with or without a
/// terminal; never uploaded.
pub(crate) const CLIENT_ID_ENV: &str = "MDA_LINEAR_CLIENT_ID";
pub(crate) const CLIENT_SECRET_ENV: &str = "MDA_LINEAR_CLIENT_SECRET";
pub(crate) const WEBHOOK_SECRET_ENV: &str = "MDA_LINEAR_WEBHOOK_SECRET";
pub(crate) const WORKSPACE_ENV: &str = "MDA_LINEAR_WORKSPACE";

/// Linear's webhook signing secrets start with this.
const SIGNING_SECRET_PREFIX: &str = "lin_wh_";

/// The slug of a deployment's Agent Auth app, which names its OAuth
/// connection, and of the signing secret stored next to it
/// ([`WEBHOOK_SECRET_SUFFIX`]).
pub(crate) const SLUGS: AppSlugs = AppSlugs::new("mda/linear-app/");

const INSTALL_MESSAGES: OAuthWaitMessages = OAuthWaitMessages {
    failed: "The Linear app install did not complete. Check that a Linear workspace admin approved it and that the app's Redirect URIs include Agent Auth's callback, then run `mda deploy` again.",
    expired: "The Linear app install was not approved in time; run `mda deploy` again.",
    unexpected: "The Linear app install returned an unexpected status",
};

const CONSENT_MESSAGES: OAuthWaitMessages = OAuthWaitMessages {
    failed: "Approving the Linear app again did not complete. Check that a Linear workspace admin approved it, then run `mda deploy` again. If the app was deleted in Linear, `mda delete` removes the deployment with its Linear app in Agent Auth; deploy again to set up a new app.",
    expired: "The Linear app was not approved again in time; run `mda deploy` again.",
    unexpected: "Approving the Linear app again returned an unexpected status",
};

const NO_EVENTS: &str = "The Linear channel handles no events, so no Linear app is set up. Add handlers with `channels.linear({ onEvent: { ... } })`, or turn agent sessions on, and deploy again.";

const LAPSED_GRANT: &str = "Linear stopped renewing this deployment's Linear app access, so a Linear workspace admin has to approve the app again. The app and its connection stay as they are.";

const REVOKED_GRANT: &str = "This deployment's access to the Linear app was revoked, so a Linear workspace admin has to approve the app again. The app and its connection stay as they are.";

/// An Agent Auth grant status this CLI does not know: setup keeps the app as
/// it is and says so, rather than treating the grant as healthy unremarked.
fn unknown_grant_warning(status: &str) -> String {
    format!(
        "Agent Auth reports this deployment's Linear app access as `{status}`, a status this version of mda does not know. Keeping the app as it is; if Linear events do not arrive, have a Linear workspace admin approve the app again, or update mda."
    )
}

const MISSING_SECRET: &str = "The Linear app is installed, but its webhook signing secret is not stored. Set MDA_LINEAR_WEBHOOK_SECRET to the app's signing secret (lin_wh_…, on the app's settings page in Linear) and run `mda deploy` again.";

const SKIPPED_SETUP: &str = "Continuing without the Linear channel. The deployment is live, but it receives no Linear events until you run `mda deploy` again and finish setting up the Linear app.";

/// Why the plain workspace connections an earlier mda set up must go, and who
/// can delete them.
fn legacy_connections_message(deployment_id: &str) -> String {
    let connection = SLUGS.connection(deployment_id);
    let secret = SLUGS.secret(deployment_id, WEBHOOK_SECRET_SUFFIX);
    format!(
        "An earlier version of mda stored this deployment's Linear app as the workspace connections {connection} and {secret}, and setting the app up again means deleting them first, which only a LangSmith workspace admin can do. Ask one to run `mda connections delete {secret}` and `mda connections delete {connection}`, or `mda deploy` once themselves, then run `mda deploy` again."
    )
}

/// The deployment's Linear app in Agent Auth.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppConnection {
    /// The app's OAuth connection, which the agent's grant is on.
    pub(crate) connection_id: String,
    pub(crate) store: AppStore,
    /// The agent's grant on it: whether the app is installed, and whether
    /// it needs consent again.
    pub(crate) grant: AgentGrant,
}

/// How Agent Auth holds the deployment's Linear app.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum AppStore {
    /// An Agent Auth app the deployment owns. It keeps the signing secret
    /// next to its connection, and deleting it takes both.
    App { id: String },
    /// Plain workspace connections, as an earlier mda set them up. Only a
    /// workspace admin can delete them.
    Connections {
        /// The webhook secret's connection, when stored.
        webhook_secret_id: Option<String>,
    },
}

impl AppConnection {
    /// Whether Agent Auth has the app's webhook signing secret. An app always
    /// has it: deploy stores the app with it.
    pub(crate) fn has_webhook_secret(&self) -> bool {
        match &self.store {
            AppStore::App { .. } => true,
            AppStore::Connections { webhook_secret_id } => webhook_secret_id.is_some(),
        }
    }
}

/// Where the Linear app comes from when the deployment has none.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AppSource {
    /// One an admin creates from the page deploy fills in.
    New,
    /// One the workspace already has, pointed at this deployment.
    Existing,
}

/// What a Linear admin copies from a new app's settings page.
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct AppCredentials {
    pub(crate) client_id: String,
    pub(crate) client_secret: String,
    pub(crate) webhook_secret: String,
}

impl std::fmt::Debug for AppCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AppCredentials")
            .field("client_id", &self.client_id)
            .finish_non_exhaustive()
    }
}

/// Everything setup does that touches the terminal, the network or Agent
/// Auth, so the flow itself is testable.
pub(crate) trait SetupEnv: SetupUi {
    /// The deployment's app connection, if any.
    fn app_connection(&mut self) -> Result<Option<AppConnection>>;
    /// The new app's credentials from the CLI-only env vars, when all are set.
    fn credentials_from_env(&mut self) -> Result<Option<AppCredentials>>;
    /// The signing secret alone from [`WEBHOOK_SECRET_ENV`], for an installed
    /// app that is missing only that.
    fn webhook_secret_from_env(&mut self) -> Result<Option<String>>;
    /// The workspace's URL key, from [`WORKSPACE_ENV`] or asked for in a
    /// terminal; without either, `None`.
    fn workspace_key(&mut self) -> Result<Option<String>>;
    /// The workspace's URL key from [`WORKSPACE_ENV`] alone, never asked for.
    fn configured_workspace_key(&self) -> Option<String>;
    /// Ask whether to create a new app or use one the workspace has.
    fn choose_app_source(&mut self) -> Result<AppSource>;
    /// Show what an existing app must be set to.
    fn show_existing_app_settings(&mut self, settings: &str) -> Result<()>;
    /// Show and open the page that creates the app, filled in.
    fn open_create_page(&mut self, url: &str) -> Result<()>;
    /// Ask for what the new app's settings page shows.
    fn ask_credentials(&mut self) -> Result<AppCredentials>;
    /// Ask for the app's webhook signing secret alone.
    fn ask_webhook_secret(&mut self) -> Result<String>;
    /// Bring the app into Agent Auth as one the deployment owns, with its
    /// signing secret. Returns it with its connection.
    fn create_app(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<AppConnection>;
    /// Store the signing secret next to an earlier mda's app connection.
    fn store_webhook_secret(&mut self, secret: &str) -> Result<()>;
    /// Start the install, or the consent again, that a workspace admin
    /// approves.
    fn start_install(&mut self, connection_id: &str) -> Result<OAuthAuthorizationSession>;
    /// Ask whether the app lists the redirect URI Agent Auth sends Linear.
    fn confirm_redirect_uri(&mut self, actual: &str) -> Result<bool>;
    /// Ask whether an app from the env is set as deploy listed.
    fn confirm_app_settings(&mut self) -> Result<bool>;
    /// Show and open the consent page.
    fn open_install_page(&mut self, url: &str) -> Result<()>;
    /// Wait for the install to change and return its status.
    fn next_install_status(&mut self, session_id: &str) -> Result<String>;
    /// Remove the deployment's Agent Auth app with everything it holds.
    fn delete_app(&mut self, app_id: &str) -> Result<()>;
    /// Remove one of an earlier mda's workspace connections.
    fn delete_connection(&mut self, connection_id: &str) -> Result<()>;
    /// The deployment the app belongs to.
    fn deployment_id(&self) -> &str;
    /// Where this deployment receives the app's events.
    fn webhook_url(&self) -> &str;
    /// Agent Auth's OAuth callback, which a new app lists as its redirect URI.
    fn redirect_uri(&self) -> &str;
}

/// Run Linear app setup against a live deployment.
pub(crate) fn setup(
    client: &langsmith::Client,
    channel: &LinearChannel,
    agent_server_url: &str,
    deployment_id: &str,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    let webhook_url = channel_app::webhook_url(agent_server_url, &channel.events_path)?;
    let agent_auth = agent_auth(client, env_vars)?;
    let redirect_uri = channel_app::oauth_callback_url(&agent_auth, env_vars)?;
    let mut env = LiveSetup {
        agent_auth,
        deployment_id,
        webhook_url,
        redirect_uri,
        interactive: is_interactive(),
        env_vars,
    };
    run_setup(&mut env, channel)
}

/// Check, before anything is deployed, that setup will be able to finish.
/// Malformed or incomplete CLI-only env vars always fail here. Without a
/// terminal, a new app's credentials must come from the env.
pub(crate) fn preflight(
    client: &langsmith::Client,
    channel: &LinearChannel,
    deployment_id: Option<&str>,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    if !channel_needs_app(channel) {
        return Ok(());
    }
    if let Some(key) = override_value(WORKSPACE_ENV, env_vars) {
        workspace_key(&key).with_context(|| format!("{WORKSPACE_ENV} is not valid"))?;
    }
    webhook_secret_env(env_vars)?;
    // A wrong client ID or secret fails here, before anything is uploaded or
    // stored in Agent Auth.
    if let Ok(Some(credentials)) = credentials_env(env_vars) {
        check_credentials(&credentials)?;
    }
    // A new app's credentials cover every case. Without a terminal, a
    // partial set fails below only once it is known that a new app is needed:
    // the signing secret alone repairs an installed app.
    if is_interactive() {
        return interactive_credentials_env(env_vars);
    }
    let credentials = credentials_env(env_vars);
    if matches!(credentials, Ok(Some(_))) {
        return Ok(());
    }
    if let Some(deployment_id) = deployment_id {
        let agent_auth = agent_auth(client, env_vars)?;
        let connection = lookup_app_connection(&agent_auth, deployment_id)
            .context("failed to check the Linear app before deploying")?;
        // An installed app is kept, also one waiting for consent again, which
        // deploy prints the link for. It may need its signing secret alone.
        if let Some(connection) =
            connection.filter(|connection| connection.grant != AgentGrant::Missing)
        {
            if !connection.has_webhook_secret() && webhook_secret_env(env_vars)?.is_none() {
                bail!(MISSING_SECRET);
            }
            return Ok(());
        }
    }
    credentials?;
    let workspace_known = override_value(WORKSPACE_ENV, env_vars).is_some();
    bail!(unattended_setup_message(None, workspace_known))
}

/// How long the credential check may take before deploy goes on without it.
const CREDENTIALS_CHECK_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

/// Whether Linear knows the app's client ID and secret. Linear has no API for
/// that, but its token endpoint checks the client before the code, so a code
/// that cannot exist gets an answer naming a wrong client or secret. Only
/// such an answer fails: anything else, such as a network error or an answer
/// Linear may change, says nothing, and deploy goes on.
fn check_credentials(credentials: &AppCredentials) -> Result<()> {
    let response = reqwest::blocking::Client::builder()
        .timeout(CREDENTIALS_CHECK_TIMEOUT)
        .build()
        .and_then(|http| {
            http.post(TOKEN_URL)
                .form(&[
                    ("grant_type", "authorization_code"),
                    ("code", "mda-deploy-credentials-check"),
                    ("client_id", credentials.client_id.as_str()),
                    ("client_secret", credentials.client_secret.as_str()),
                ])
                .send()
        });
    let Ok(response) = response else {
        return Ok(());
    };
    let status = response.status().as_u16();
    credentials_verdict(status, &response.text().unwrap_or_default())
}

/// What Linear's token endpoint says about a client ID and secret.
fn credentials_verdict(status: u16, body: &str) -> Result<()> {
    if status != 400 && status != 401 {
        return Ok(());
    }
    let error = serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|body| body.get("error")?.as_str().map(str::to_string));
    match error.as_deref() {
        Some("invalid_secret") => bail!(
            "Linear refused {CLIENT_SECRET_ENV}: it is not the client secret of the app {CLIENT_ID_ENV} names. Copy it from the app's settings page and run `mda deploy` again."
        ),
        Some("invalid_client") => bail!(
            "Linear knows no app with {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV}. Copy both from the app's settings page and run `mda deploy` again."
        ),
        _ => Ok(()),
    }
}

fn channel_needs_app(channel: &LinearChannel) -> bool {
    !channel.handled.is_empty()
}

fn unattended_setup_message(create_url: Option<&str>, workspace_known: bool) -> String {
    let page = create_url.map_or_else(String::new, |url| format!(" at {url}"));
    // Linear opens an unscoped link in the workspace the person last used,
    // where they may not be an admin.
    let workspace = if workspace_known {
        String::new()
    } else {
        format!(" Set {WORKSPACE_ENV} to the workspace's URL key, as in https://linear.app/<key>/, so the link opens in that workspace.")
    };
    format!(
        "This agent's Linear channel needs a Linear app, and without a terminal deploy cannot ask for it. Have an admin of the Linear workspace create one{page}, or use an app the workspace has, then set {CLIENT_ID_ENV}, {CLIENT_SECRET_ENV} and {WEBHOOK_SECRET_ENV} in the shell or .env and run `mda deploy` again. Deploy prints the install link to approve.{workspace}"
    )
}

pub(crate) fn run_setup(env: &mut dyn SetupEnv, channel: &LinearChannel) -> Result<()> {
    if !channel_needs_app(channel) {
        return env.info(NO_EVENTS);
    }
    let current = env.app_connection()?;
    let set_up = channel_app::retry_or_skip(
        env,
        current,
        SKIPPED_SETUP,
        |env, current| set_up_app(env, channel, current),
        |env| env.app_connection(),
    )?;
    let Some(kept) = set_up else {
        return Ok(());
    };
    env.success(&format!(
        "Linear channel ready · the Linear app sends its events to {}",
        env.webhook_url()
    ))?;
    if kept {
        return Ok(());
    }
    // Said once, when the app is installed, rather than on every deploy.
    let page = env.configured_workspace_key().map_or_else(
        || "Settings → Administration → API in Linear".to_string(),
        |key| format!("https://linear.app/{key}/{APPS_PATH}"),
    );
    env.info(&format!(
        "Linear has no API to change an app, so later deploys keep it as it is now. If the channel's app name, description or icon change, or this deployment moves to another URL, change them on the app's settings page ({page})."
    ))
}

/// Install the app, or finish what an earlier setup left undone. Returns
/// whether an app created earlier is kept.
fn set_up_app(
    env: &mut dyn SetupEnv,
    channel: &LinearChannel,
    current: Option<AppConnection>,
) -> Result<bool> {
    let Some(connection) = current else {
        install_new_app(env, channel)?;
        return Ok(false);
    };
    match &connection.grant {
        AgentGrant::Active => {
            store_missing_secret(env, &connection)?;
        }
        // The grant lapsed or was revoked: the app and its secret stay, and
        // the admin approves it again.
        AgentGrant::AuthorizationRequired | AgentGrant::Revoked => {
            let reason = if connection.grant == AgentGrant::Revoked {
                REVOKED_GRANT
            } else {
                LAPSED_GRANT
            };
            env.warning(reason)?;
            store_missing_secret(env, &connection)?;
            approve(env, &connection.connection_id, &CONSENT_MESSAGES)?;
        }
        // A status Agent Auth added after this CLI: warn, naming it, and
        // keep going without consent, so a harmless new status does not
        // break deploys.
        AgentGrant::Unknown(status) => {
            env.warning(&unknown_grant_warning(status))?;
            store_missing_secret(env, &connection)?;
        }
        // An install that did not finish: start again with fresh credentials.
        AgentGrant::Missing => {
            env.info("A previous Linear app setup did not finish; starting it again.")?;
            remove(env, &connection)?;
            install_new_app(env, channel)?;
            return Ok(false);
        }
    }
    Ok(true)
}

/// The signing secret of an installed app, when an earlier setup did not store
/// it. Only an earlier mda's connections can lack it.
fn store_missing_secret(env: &mut dyn SetupEnv, connection: &AppConnection) -> Result<()> {
    if connection.has_webhook_secret() {
        return Ok(());
    }
    let secret = match env.webhook_secret_from_env()? {
        Some(secret) => secret,
        None if env.interactive() => env.ask_webhook_secret()?,
        None => bail!(MISSING_SECRET),
    };
    env.store_webhook_secret(&secret)
}

fn install_new_app(env: &mut dyn SetupEnv, channel: &LinearChannel) -> Result<()> {
    let credentials = match env.credentials_from_env()? {
        Some(credentials) => {
            confirm_app_settings(env, channel)?;
            credentials
        }
        None if env.interactive() && env.choose_app_source()? == AppSource::Existing => {
            env.show_existing_app_settings(&existing_app_settings(
                channel,
                env.webhook_url(),
                env.redirect_uri(),
                env.configured_workspace_key().as_deref(),
            ))?;
            env.ask_credentials()?
        }
        None => {
            let workspace = env.workspace_key()?;
            let url = create_app_url(
                workspace.as_deref(),
                channel,
                env.webhook_url(),
                env.redirect_uri(),
            )?;
            if !env.interactive() {
                bail!(unattended_setup_message(Some(&url), workspace.is_some()));
            }
            env.open_create_page(&url)?;
            env.ask_credentials()?
        }
    };
    let app = env.create_app(channel, &credentials)?;
    let result = approve(env, &app.connection_id, &INSTALL_MESSAGES);
    if result.is_err() {
        // The next attempt asks for the credentials again, so leave nothing
        // behind that could hold wrong ones.
        if let Err(cleanup) = remove(env, &app) {
            env.warning(&format!(
                "Could not remove the Linear app's unfinished setup from Agent Auth ({cleanup:#})."
            ))?;
        }
    }
    result
}

/// Have a workspace admin approve the app on its connection: the first
/// install, or consent again once the grant lapsed.
fn approve(
    env: &mut dyn SetupEnv,
    connection_id: &str,
    messages: &OAuthWaitMessages,
) -> Result<()> {
    let session = env.start_install(connection_id)?;
    if session.status != "completed" {
        if let Some(actual) = redirect_uri_of(&session.verification_url) {
            if actual != env.redirect_uri() {
                confirm_redirect_uri(env, &actual)?;
            }
        }
        env.open_install_page(&session.verification_url)?;
    }
    poll_oauth_session(
        &session.status,
        || env.next_install_status(&session.id),
        messages,
    )
}

/// An app from the env was made outside deploy, so it may not point at this
/// deployment, and Linear has no API that says what it has set: list the
/// settings, and in a terminal wait until they are set. "No" asks again, since
/// Linear refuses the install without the redirect URI; Escape stops setting
/// up the channel.
fn confirm_app_settings(env: &mut dyn SetupEnv, channel: &LinearChannel) -> Result<()> {
    let settings = app_settings(
        channel,
        env.webhook_url(),
        env.redirect_uri(),
        env.configured_workspace_key().as_deref(),
    );
    env.warning(&format!(
        "{settings} Linear can't tell deploy what the app has set, so check these before the install is approved."
    ))?;
    if !env.interactive() {
        return Ok(());
    }
    while !env.confirm_app_settings()? {
        env.info(
            "Linear refuses the install until the app lists the redirect URI above. Set it, then answer Yes, or press Esc to stop setting up the Linear channel.",
        )?;
    }
    Ok(())
}

/// Agent Auth sends Linear a redirect URI the new app may not list: say so,
/// and in a terminal wait until it is added. "No" asks again, since Linear
/// refuses the install until then; Escape stops setting up the channel.
fn confirm_redirect_uri(env: &mut dyn SetupEnv, actual: &str) -> Result<()> {
    env.warning(&format!(
        "Agent Auth expects Linear to redirect to {actual}. Add it under Redirect URIs on the Linear app's settings page before approving the install, or Linear answers \"Invalid redirect_uri\"."
    ))?;
    if !env.interactive() {
        return Ok(());
    }
    while !env.confirm_redirect_uri(actual)? {
        env.info(&format!(
            "Linear refuses the install until the app lists {actual} under Redirect URIs. Add it, then answer Yes, or press Esc to stop setting up the Linear channel."
        ))?;
    }
    Ok(())
}

/// Delete the deployment's app from Agent Auth. An earlier mda's connections
/// need a workspace admin, so a refusal says who can delete them.
fn remove(env: &mut dyn SetupEnv, connection: &AppConnection) -> Result<()> {
    let webhook_secret_id = match &connection.store {
        AppStore::App { id } => return env.delete_app(id),
        AppStore::Connections { webhook_secret_id } => webhook_secret_id.as_deref(),
    };
    let result = webhook_secret_id
        .map_or(Ok(()), |secret_id| env.delete_connection(secret_id))
        .and_then(|()| env.delete_connection(&connection.connection_id));
    result.map_err(|error| {
        if is_forbidden(&error) {
            anyhow!(legacy_connections_message(env.deployment_id()))
        } else {
            error
        }
    })
}

fn is_forbidden(error: &anyhow::Error) -> bool {
    error
        .chain()
        .filter_map(|cause| cause.downcast_ref::<ApiError>())
        .any(|api| api.status == StatusCode::FORBIDDEN)
}

/// The redirect URI an authorize URL sends Linear's answer to.
fn redirect_uri_of(verification_url: &str) -> Option<String> {
    let url = reqwest::Url::parse(verification_url).ok()?;
    url.query_pairs()
        .find(|(key, _)| key == "redirect_uri")
        .map(|(_, value)| value.into_owned())
}

/// The page that creates the app, filled in from an app manifest so the admin
/// only has to create it. Linear's pages are scoped to the workspace.
pub(crate) fn create_app_url(
    workspace: Option<&str>,
    channel: &LinearChannel,
    webhook_url: &str,
    redirect_uri: &str,
) -> Result<String> {
    let base = match workspace {
        Some(key) => format!(
            "https://linear.app/{}/{CREATE_APP_PATH}",
            workspace_key(key)?
        ),
        None => format!("https://linear.app/{CREATE_APP_PATH}"),
    };
    let mut display = json!({ "description": channel.app_description });
    if channel.app_icon.is_some() {
        // The deployment serves the channel's icon next to its events route.
        let base = webhook_url
            .strip_suffix(channel.events_path.as_str())
            .context("the Linear webhook URL does not end in the channel's events path")?;
        display["iconUrl"] = json!(format!("{base}/channels/{}/icon", channel.name));
    }
    let manifest = json!({
        "$schema": "https://linear.app/.well-known/oauth-app-manifest.schema.json",
        "schemaVersion": "1.0.0",
        "distribution": "private",
        "display": display,
        "developer": { "name": "LangSmith" },
        "oauth": {
            "client_name": channel.app_name,
            "client_uri": "https://www.langchain.com/langsmith",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
        },
        "webhook": {
            "url": webhook_url,
            "enabled": true,
            "resourceTypes": channel.resource_types,
        },
    });
    let mut url = reqwest::Url::parse(&base).context("invalid Linear app page URL")?;
    url.query_pairs_mut()
        .append_pair("manifest", &manifest.to_string());
    Ok(url.to_string())
}

/// What an app the workspace already has must be set to, the same as a new
/// app's page would fill in. Linear has no API to change an app, so an admin
/// sets these on its settings page.
pub(crate) fn existing_app_settings(
    channel: &LinearChannel,
    webhook_url: &str,
    redirect_uri: &str,
    workspace: Option<&str>,
) -> String {
    format!(
        "{} Then copy its client ID, client secret and webhook signing secret into the prompts below.",
        app_settings(channel, webhook_url, redirect_uri, workspace)
    )
}

/// The settings an app must have for this deployment, and what changing its
/// webhook does.
fn app_settings(
    channel: &LinearChannel,
    webhook_url: &str,
    redirect_uri: &str,
    workspace: Option<&str>,
) -> String {
    let page = workspace.map_or_else(
        || "Settings → Administration → API in Linear".to_string(),
        |key| format!("https://linear.app/{key}/{APPS_PATH}"),
    );
    format!(
        "Point your Linear app at this deployment on its settings page ({page}):\n\
         \n  Redirect URIs: add {redirect_uri}\n\
         \x20 Webhooks: turn on, URL {webhook_url}\n\
         \x20   events: {}\n\
         \nLinear sends an app's webhooks to one URL, so whatever received this app's \
         events stops receiving them.",
        channel.resource_types.join(", ")
    )
}

/// A workspace's URL key, from the key itself or any `linear.app` URL in it.
pub(crate) fn workspace_key(input: &str) -> Result<String> {
    let input = input.trim();
    let key = match input.strip_prefix("https://linear.app/") {
        Some(rest) => rest.split('/').next().unwrap_or_default(),
        None => input,
    }
    .to_ascii_lowercase();
    let valid = !key.is_empty()
        && key.len() <= 64
        && key
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if !valid {
        bail!("Enter the workspace's URL key, as in https://linear.app/<key>/.");
    }
    Ok(key)
}

/// The new app's credentials from the CLI-only env vars: all three, or none.
fn credentials_env(env_vars: &BTreeMap<String, String>) -> Result<Option<AppCredentials>> {
    let value = |key| override_value(key, env_vars);
    match (
        value(CLIENT_ID_ENV),
        value(CLIENT_SECRET_ENV),
        value(WEBHOOK_SECRET_ENV),
    ) {
        (Some(client_id), Some(client_secret), Some(webhook_secret)) => {
            validate_signing_secret(&webhook_secret).map_err(anyhow::Error::msg)?;
            Ok(Some(AppCredentials {
                client_id,
                client_secret,
                webhook_secret,
            }))
        }
        (None, None, None) => Ok(None),
        _ => bail!(
            "Set all of {CLIENT_ID_ENV}, {CLIENT_SECRET_ENV} and {WEBHOOK_SECRET_ENV}, or none of them."
        ),
    }
}

/// In a terminal, a partial set of a new app's credentials fails, but the
/// signing secret alone doesn't: it repairs an installed app, and a new app's
/// other credentials are asked for.
fn interactive_credentials_env(env_vars: &BTreeMap<String, String>) -> Result<()> {
    let new_app = [CLIENT_ID_ENV, CLIENT_SECRET_ENV]
        .into_iter()
        .any(|key| override_value(key, env_vars).is_some());
    if new_app {
        credentials_env(env_vars)?;
    }
    Ok(())
}

/// The signing secret alone, for an installed app that is missing only that.
fn webhook_secret_env(env_vars: &BTreeMap<String, String>) -> Result<Option<String>> {
    let Some(secret) = override_value(WEBHOOK_SECRET_ENV, env_vars) else {
        return Ok(None);
    };
    validate_signing_secret(&secret)
        .map_err(|reason| anyhow!("{WEBHOOK_SECRET_ENV} is not valid: {reason}"))?;
    Ok(Some(secret))
}

fn validate_signing_secret(value: &str) -> std::result::Result<(), &'static str> {
    if value.trim().starts_with(SIGNING_SECRET_PREFIX) {
        Ok(())
    } else {
        Err("Enter the webhook signing secret: it starts with lin_wh_ and is shown under Webhooks on the app's page.")
    }
}

/// [`WORKSPACE_ENV`] as a workspace key, when set and valid.
fn configured_workspace_key(env_vars: &BTreeMap<String, String>) -> Option<String> {
    override_value(WORKSPACE_ENV, env_vars).and_then(|key| workspace_key(&key).ok())
}

/// The deployment's Linear app and the agent's grant on it: its Agent Auth
/// app, or else the workspace connections an earlier mda set up.
fn lookup_app_connection(
    agent_auth: &langsmith::Client,
    deployment_id: &str,
) -> Result<Option<AppConnection>> {
    let app = agent_auth
        .agent_apps_if_supported(deployment_id)?
        .unwrap_or_default()
        .into_iter()
        .find(|app| app.provider == PROVIDER);
    let (connection_id, store) = match app {
        Some(app) => (
            app.connection_id.context(
                "Agent Auth returned the deployment's Linear app without its connection",
            )?,
            AppStore::App { id: app.id },
        ),
        None => {
            let listed = agent_auth.list_connections_raw()?;
            let Some(connection) = find_connection(&listed, &SLUGS.connection(deployment_id))
            else {
                return Ok(None);
            };
            let webhook_secret_id =
                find_connection(&listed, &SLUGS.secret(deployment_id, WEBHOOK_SECRET_SUFFIX))
                    .map(|secret| secret.connection_id.clone());
            (
                connection.connection_id.clone(),
                AppStore::Connections { webhook_secret_id },
            )
        }
    };
    let grant = agent_auth.agent_oauth_grant(&connection_id, deployment_id)?;
    Ok(Some(AppConnection {
        connection_id,
        store,
        grant,
    }))
}

struct LiveSetup<'a> {
    agent_auth: langsmith::Client,
    deployment_id: &'a str,
    webhook_url: String,
    redirect_uri: String,
    interactive: bool,
    env_vars: &'a BTreeMap<String, String>,
}

impl SetupUi for LiveSetup<'_> {
    fn interactive(&self) -> bool {
        self.interactive
    }

    fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool> {
        channel_app::ask_retry_or_skip("Linear app setup", "Linear", error)
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
        lookup_app_connection(&self.agent_auth, self.deployment_id)
            .context("failed to look up the Linear app in Agent Auth")
    }

    fn credentials_from_env(&mut self) -> Result<Option<AppCredentials>> {
        let credentials = credentials_env(self.env_vars)?;
        if credentials.is_some() {
            log::info(env_app_notice(
                "Linear app",
                "Agent Auth",
                &[CLIENT_ID_ENV, CLIENT_SECRET_ENV, WEBHOOK_SECRET_ENV],
            ))?;
        }
        Ok(credentials)
    }

    fn webhook_secret_from_env(&mut self) -> Result<Option<String>> {
        let secret = webhook_secret_env(self.env_vars)?;
        if secret.is_some() {
            log::info(format!(
                "Using the Linear app's webhook signing secret from {WEBHOOK_SECRET_ENV}. Agent Auth keeps it, so you can remove {WEBHOOK_SECRET_ENV} afterwards."
            ))?;
        }
        Ok(secret)
    }

    fn workspace_key(&mut self) -> Result<Option<String>> {
        if let Some(key) = override_value(WORKSPACE_ENV, self.env_vars) {
            return workspace_key(&key).map(Some);
        }
        if !self.interactive() {
            return Ok(None);
        }
        // Required: Linear opens a link without a workspace in the one the
        // person last used, where they may not be an admin.
        let input: String = cliclack::input(
            "Linear workspace for the app (its URL or key, as in https://linear.app/<key>/)",
        )
        .placeholder("acme, or https://linear.app/acme/team/ENG/active")
        .validate(|value: &String| {
            workspace_key(value)
                .map(|_| ())
                .map_err(|error| error.to_string())
        })
        .interact()
        .context("failed to read the Linear workspace")?;
        workspace_key(&input).map(Some)
    }

    fn configured_workspace_key(&self) -> Option<String> {
        configured_workspace_key(self.env_vars)
    }

    fn choose_app_source(&mut self) -> Result<AppSource> {
        cliclack::select("This agent's Linear channel needs a Linear app")
            .item(
                AppSource::New,
                "Create a new Linear app",
                "recommended: deploy opens Linear's create page, filled in",
            )
            .item(
                AppSource::Existing,
                "Use an existing Linear app",
                "enter its client ID, client secret and webhook signing secret",
            )
            .interact()
            .context("failed to choose a Linear app")
    }

    fn show_existing_app_settings(&mut self, settings: &str) -> Result<()> {
        log::step(settings)?;
        Ok(())
    }

    fn open_create_page(&mut self, url: &str) -> Result<()> {
        log::step(format!(
            "Create the Linear app for this agent. Only admins of the Linear workspace can open this page; anyone else sees \"Not found\". The page opens filled in, so check it and click Create. If your browser does not open, visit:\n{url}\nThen copy the app's client ID, client secret and webhook signing secret into the prompts below.\n\nNot an admin? Send the link to one: they click Create and send you the three values, or set {CLIENT_ID_ENV}, {CLIENT_SECRET_ENV} and {WEBHOOK_SECRET_ENV} and run `mda deploy` themselves."
        ))?;
        crate::langsmith::login::open_browser(url);
        Ok(())
    }

    fn ask_credentials(&mut self) -> Result<AppCredentials> {
        let client_id: String = cliclack::input("Client ID")
            .validate(|value: &String| {
                if value.trim().is_empty() {
                    Err("Enter the client ID shown on the app's page.")
                } else {
                    Ok(())
                }
            })
            .interact()
            .context("failed to read the Linear client ID")?;
        let client_secret: String = cliclack::password("Client secret")
            .mask('•')
            .validate(|value: &String| {
                if value.trim().is_empty() {
                    Err("Enter the client secret shown on the app's page.")
                } else {
                    Ok(())
                }
            })
            .interact()
            .context("failed to read the Linear client secret")?;
        let webhook_secret = self.ask_webhook_secret()?;
        Ok(AppCredentials {
            client_id: client_id.trim().to_string(),
            client_secret: client_secret.trim().to_string(),
            webhook_secret,
        })
    }

    fn ask_webhook_secret(&mut self) -> Result<String> {
        let secret: String = cliclack::password("Webhook signing secret")
            .mask('•')
            .validate(|value: &String| validate_signing_secret(value))
            .interact()
            .context("failed to read the Linear webhook signing secret")?;
        Ok(secret.trim().to_string())
    }

    fn create_app(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<AppConnection> {
        let app = self
            .agent_auth
            .bring_linear_app(
                self.deployment_id,
                &SLUGS.connection(self.deployment_id),
                &ExistingLinearApp {
                    client_id: &credentials.client_id,
                    client_secret: &credentials.client_secret,
                    webhook_secret: &credentials.webhook_secret,
                    scopes: &channel.scopes,
                    allowed_scopes: &channel.allowed_scopes,
                },
            )
            .context("failed to register the Linear app with Agent Auth")?;
        Ok(AppConnection {
            connection_id: app
                .connection_id
                .context("Agent Auth returned the Linear app without its connection")?,
            store: AppStore::App { id: app.id },
            grant: AgentGrant::Missing,
        })
    }

    fn store_webhook_secret(&mut self, secret: &str) -> Result<()> {
        self.agent_auth
            .create_workspace_connection(
                &SLUGS.secret(self.deployment_id, WEBHOOK_SECRET_SUFFIX),
                ConnectionOwnerPolicy::Agent,
                Some(self.deployment_id),
                Some(secret),
            )
            .map(|_| ())
            .context("failed to store the Linear webhook signing secret")
    }

    fn start_install(&mut self, connection_id: &str) -> Result<OAuthAuthorizationSession> {
        self.agent_auth
            .create_agent_oauth_authorization_session(connection_id, self.deployment_id)
            .context("failed to start the Linear app install")
    }

    fn confirm_redirect_uri(&mut self, _actual: &str) -> Result<bool> {
        cliclack::confirm("Added it? Deploy opens the install page next.")
            .initial_value(true)
            .interact()
            .context("failed to confirm the Linear app's redirect URI")
    }

    fn confirm_app_settings(&mut self) -> Result<bool> {
        cliclack::confirm("Set them? Deploy opens the install page next.")
            .initial_value(true)
            .interact()
            .context("failed to confirm the Linear app's settings")
    }

    fn open_install_page(&mut self, url: &str) -> Result<()> {
        if !self.interactive {
            // Whoever runs deploy, e.g. a coding agent, hands this link to a
            // Linear workspace admin.
            log::step(format!(
                "Install the Linear app: a Linear workspace admin opens this link and approves it. Deploy waits for it.\n{url}"
            ))?;
            return Ok(());
        }
        log::step(format!(
            "Install the Linear app in your browser: a Linear workspace admin approves it. If it does not open, visit:\n{url}\nPress Ctrl-C to skip."
        ))?;
        crate::langsmith::login::open_browser(url);
        Ok(())
    }

    fn next_install_status(&mut self, session_id: &str) -> Result<String> {
        wait_for_oauth_status(&self.agent_auth, session_id, &Interrupt::install()?)
    }

    fn delete_app(&mut self, app_id: &str) -> Result<()> {
        self.agent_auth
            .delete_agent_app(app_id)
            .map(|_| ())
            .context("failed to delete the deployment's Linear app from Agent Auth")
    }

    fn delete_connection(&mut self, connection_id: &str) -> Result<()> {
        self.agent_auth.delete_connection(connection_id).map(|_| ())
    }

    fn deployment_id(&self) -> &str {
        self.deployment_id
    }

    fn webhook_url(&self) -> &str {
        &self.webhook_url
    }

    fn redirect_uri(&self) -> &str {
        &self.redirect_uri
    }
}
