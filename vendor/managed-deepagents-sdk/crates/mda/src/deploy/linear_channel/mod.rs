//! Linear channel setup during `mda deploy`.
//!
//! A Linear channel needs a Linear OAuth app installed with `actor=app` in the
//! workspace, its webhook pointed at the deployment. Linear has no public API
//! to create apps, so a workspace admin creates it from a page deploy fills
//! in, and pastes back its client ID, client secret and webhook signing
//! secret. Agent Auth then keeps them:
//!
//! - an agent-owned OAuth connection, `mda/linear-app/{deployment}`, whose
//!   token the runtime reads as `mda/linear-token` and Agent Auth renews;
//! - the signing secret as the secret connection next to it, which the runtime
//!   verifies deliveries with.
//!
//! The CLI never reads the app's token. It goes by what Agent Auth records
//! about the agent's grant: none, active, or lapsed and waiting for consent
//! again, which only needs the admin to approve the install once more.
//!
//! Setup runs once the deployment is live, because the app's webhook points at
//! the Agent Server URL. `mda delete` removes both connections (see
//! [`teardown`]); the app itself stays in Linear.

mod manifest;
pub(crate) mod teardown;
#[cfg(test)]
mod tests;

use std::collections::BTreeMap;

use anyhow::{anyhow, bail, Context, Result};
use cliclack::log;
use serde_json::{json, Map, Value};

use crate::connection::{poll_oauth_session, wait_for_oauth_status, OAuthWaitMessages};
use crate::deploy::channel_app::{
    self, agent_auth, find_connection, is_interactive, override_value, AppSlugs, SetupUi,
};
use crate::interrupt::Interrupt;
use crate::langsmith::{
    self, AgentGrant, ConnectionOwnerPolicy, CreateOAuthConnection, OAuthAuthorizationSession,
};

pub(crate) use crate::deploy::channel_app::WEBHOOK_SECRET_SUFFIX;
pub(crate) use manifest::{prepare, LinearChannel, PROVIDER};

const AUTHORIZE_URL: &str = "https://linear.app/oauth/authorize";
const TOKEN_URL: &str = "https://api.linear.app/oauth/token";
/// Where Linear's apps are created, scoped to a workspace when deploy knows it.
const CREATE_APP_PATH: &str = "settings/api/applications/new";
/// Where a workspace's apps are listed, each with its settings page.
const APPS_PATH: &str = "settings/api";

/// CLI-only overrides for setup without a terminal; never uploaded.
pub(crate) const CLIENT_ID_ENV: &str = "MDA_LINEAR_CLIENT_ID";
pub(crate) const CLIENT_SECRET_ENV: &str = "MDA_LINEAR_CLIENT_SECRET";
pub(crate) const WEBHOOK_SECRET_ENV: &str = "MDA_LINEAR_WEBHOOK_SECRET";
pub(crate) const WORKSPACE_ENV: &str = "MDA_LINEAR_WORKSPACE";

/// Linear's webhook signing secrets start with this.
const SIGNING_SECRET_PREFIX: &str = "lin_wh_";

/// The agent-owned OAuth connection that holds a deployment's app, and the
/// signing secret stored next to it ([`WEBHOOK_SECRET_SUFFIX`]).
pub(crate) const SLUGS: AppSlugs = AppSlugs::new("mda/linear-app/");

const INSTALL_MESSAGES: OAuthWaitMessages = OAuthWaitMessages {
    failed: "The Linear app install did not complete. Check that a Linear workspace admin approved it and that the app's Redirect URIs include Agent Auth's callback, then run `mda deploy` again.",
    expired: "The Linear app install was not approved in time; run `mda deploy` again.",
    unexpected: "The Linear app install returned an unexpected status",
};

const CONSENT_MESSAGES: OAuthWaitMessages = OAuthWaitMessages {
    failed: "Approving the Linear app again did not complete. Check that a Linear workspace admin approved it, then run `mda deploy` again. If the app was deleted in Linear, delete this deployment's mda/linear-app connections with `mda connections delete` and deploy again to set up a new app.",
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

/// The deployment's Linear app in Agent Auth.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppConnection {
    pub(crate) connection_id: String,
    /// The webhook secret's connection, when stored.
    pub(crate) webhook_secret_id: Option<String>,
    /// The agent's grant on it: whether the app is installed, and whether
    /// it needs consent again.
    pub(crate) grant: AgentGrant,
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
    /// Show and open the page that creates the app, filled in.
    fn open_create_page(&mut self, url: &str) -> Result<()>;
    /// Ask for what the new app's settings page shows.
    fn ask_credentials(&mut self) -> Result<AppCredentials>;
    /// Ask for the app's webhook signing secret alone.
    fn ask_webhook_secret(&mut self) -> Result<String>;
    /// Register the app with Agent Auth as the deployment's connection.
    fn create_connection(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<String>;
    /// Store the signing secret next to the app's connection.
    fn store_webhook_secret(&mut self, secret: &str) -> Result<()>;
    /// Start the install, or the consent again, that a workspace admin
    /// approves.
    fn start_install(&mut self, connection_id: &str) -> Result<OAuthAuthorizationSession>;
    /// Agent Auth sends Linear a redirect URI the new app may not list: say so,
    /// and in a terminal wait until it is added.
    fn confirm_redirect_uri(&mut self, actual: &str) -> Result<()>;
    /// Show and open the consent page.
    fn open_install_page(&mut self, url: &str) -> Result<()>;
    /// Wait for the install to change and return its status.
    fn next_install_status(&mut self, session_id: &str) -> Result<String>;
    /// Remove what a setup that did not finish left in Agent Auth.
    fn delete_connection(&mut self, connection_id: &str) -> Result<()>;
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

/// Check, before anything is deployed, that setup will be able to finish:
/// without a terminal, a new app's credentials must come from the env.
pub(crate) fn preflight(
    client: &langsmith::Client,
    channel: &LinearChannel,
    deployment_id: Option<&str>,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    if is_interactive() || !channel_needs_app(channel) {
        return Ok(());
    }
    // A new app's credentials cover every case. A partial set is checked
    // below, once it is known whether a new app is needed.
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
            if connection.webhook_secret_id.is_none() && webhook_secret_env(env_vars)?.is_none() {
                bail!(MISSING_SECRET);
            }
            return Ok(());
        }
    }
    credentials?;
    let workspace_known = override_value(WORKSPACE_ENV, env_vars).is_some();
    bail!(unattended_setup_message(None, workspace_known))
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
        "This agent's Linear channel needs a Linear app, and without a terminal deploy cannot ask for it. Have an admin of the Linear workspace create one{page}, then set {CLIENT_ID_ENV}, {CLIENT_SECRET_ENV} and {WEBHOOK_SECRET_ENV} in the shell or .env and run `mda deploy` again. Deploy prints the install link to approve.{workspace}"
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
        "Linear channel ready: the Linear app sends its events to {}",
        env.webhook_url()
    ))?;
    if kept {
        let page = env.configured_workspace_key().map_or_else(
            || "Settings → Administration → API in Linear".to_string(),
            |key| format!("https://linear.app/{key}/{APPS_PATH}"),
        );
        env.info(&format!(
            "This deployment keeps its Linear app. The app's name, description and icon only apply when deploy creates it, and Linear has no API to change them, so change them on the app's settings page ({page})."
        ))?;
    }
    env.info(
        "Linear has no API to change an app's webhook URL. If this deployment moves to another URL, update it on the app's settings page in Linear.",
    )
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
            remove(
                env,
                &connection.connection_id,
                connection.webhook_secret_id.as_deref(),
            )?;
            install_new_app(env, channel)?;
            return Ok(false);
        }
    }
    Ok(true)
}

/// The signing secret of an installed app, when an earlier setup did not store
/// it.
fn store_missing_secret(env: &mut dyn SetupEnv, connection: &AppConnection) -> Result<()> {
    if connection.webhook_secret_id.is_some() {
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
        Some(credentials) => credentials,
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
    let connection_id = env.create_connection(channel, &credentials)?;
    let result = env
        .store_webhook_secret(&credentials.webhook_secret)
        .and_then(|()| approve(env, &connection_id, &INSTALL_MESSAGES));
    if result.is_err() {
        // The next attempt asks for the credentials again, so leave nothing
        // behind that could hold wrong ones.
        let secret_id = env
            .app_connection()
            .ok()
            .flatten()
            .and_then(|connection| connection.webhook_secret_id);
        if let Err(cleanup) = remove(env, &connection_id, secret_id.as_deref()) {
            env.warning(&format!(
                "Could not remove the Linear app's unfinished connection ({cleanup:#})."
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
                env.confirm_redirect_uri(&actual)?;
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

fn remove(env: &mut dyn SetupEnv, connection_id: &str, secret_id: Option<&str>) -> Result<()> {
    if let Some(secret_id) = secret_id {
        env.delete_connection(secret_id)?;
    }
    env.delete_connection(connection_id)
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

/// The deployment's app connection, the agent's grant on it, and the secret
/// stored next to it.
fn lookup_app_connection(
    agent_auth: &langsmith::Client,
    deployment_id: &str,
) -> Result<Option<AppConnection>> {
    let listed = agent_auth.list_connections_raw()?;
    let Some(connection) = find_connection(&listed, &SLUGS.connection(deployment_id)) else {
        return Ok(None);
    };
    let grant = agent_auth.agent_oauth_grant(&connection.connection_id, deployment_id)?;
    Ok(Some(AppConnection {
        connection_id: connection.connection_id.clone(),
        webhook_secret_id: find_connection(
            &listed,
            &SLUGS.secret(deployment_id, WEBHOOK_SECRET_SUFFIX),
        )
        .map(|secret| secret.connection_id.clone()),
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
            log::info(format!(
                "Using the Linear app from {CLIENT_ID_ENV}. Agent Auth stores it, so you can remove the {CLIENT_SECRET_ENV} and {WEBHOOK_SECRET_ENV} values afterwards."
            ))?;
        }
        Ok(credentials)
    }

    fn webhook_secret_from_env(&mut self) -> Result<Option<String>> {
        webhook_secret_env(self.env_vars)
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

    fn create_connection(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<String> {
        let mut authorization_params = Map::new();
        authorization_params.insert("actor".into(), Value::from("app"));
        authorization_params.insert("prompt".into(), Value::from("consent"));
        let slug = SLUGS.connection(self.deployment_id);
        let connection = self
            .agent_auth
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: &slug,
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: None,
                client_id: Some(&credentials.client_id),
                client_secret: Some(&credentials.client_secret),
                display_name: "Linear",
                scopes: &channel.scopes,
                allowed_scopes: &channel.allowed_scopes,
                authorize_url: Some(AUTHORIZE_URL),
                token_url: Some(TOKEN_URL),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &authorization_params,
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                client_registration_url: None,
                mcp_server_url: None,
            })
            .context("failed to register the Linear app with Agent Auth")?;
        Ok(connection.connection_id)
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

    fn confirm_redirect_uri(&mut self, actual: &str) -> Result<()> {
        let warning = format!(
            "Agent Auth expects Linear to redirect to {actual}. Add it under Redirect URIs on the Linear app's settings page before approving the install, or Linear answers \"Invalid redirect_uri\"."
        );
        log::warning(&warning)?;
        if !self.interactive {
            return Ok(());
        }
        cliclack::confirm("Added it? Deploy opens the install page next.")
            .initial_value(true)
            .interact()?;
        Ok(())
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

    fn delete_connection(&mut self, connection_id: &str) -> Result<()> {
        self.agent_auth.delete_connection(connection_id).map(|_| ())
    }

    fn webhook_url(&self) -> &str {
        &self.webhook_url
    }

    fn redirect_uri(&self) -> &str {
        &self.redirect_uri
    }
}
