use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use reqwest::Url;
use serde::Deserialize;

use super::channel_app::{self, is_interactive, SetupUi, WaitTick};
use super::channel_icon::channel_icon_file;
use super::slack_app::{self, SlackApp};
use super::trigger_icon;
use crate::compile::ChannelRequirements;
use crate::langsmith::{
    self, is_transient_setup_error, ChannelSetupRequest, ChannelSetupResponse, ChannelSetupStatus,
    ChannelSetupTarget, SlackAppInUse, SlackChannelApp, SlackChannelOptions, SlackChannelSetup,
    SlackTriggerMode,
};
use crate::project::Language;

const SLACK_ICON_IMAGE_MAX_BYTES: usize = 1_000_000;
const SLACK_ICON_IMAGE_SIZE_PX: u32 = 512;
const SLACK_APP_NAME_MAX_CHARS: usize = 35;
const DEFAULT_SLACK_BACKGROUND_COLOR: &str = "#09090f";
const PNG_MIN_BYTES: usize = 33;
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

fn png_dimensions(image: &[u8]) -> Option<(u32, u32)> {
    let width = u32::from_be_bytes(image.get(16..20)?.try_into().ok()?);
    let height = u32::from_be_bytes(image.get(20..24)?.try_into().ok()?);
    Some((width, height))
}

fn validate_slack_icon(image: &[u8]) -> Result<()> {
    if image.len() > SLACK_ICON_IMAGE_MAX_BYTES {
        bail!("Slack channel icon must be at most 1 MB");
    }
    if image.len() < PNG_MIN_BYTES
        || !image.starts_with(PNG_SIGNATURE)
        || image.get(12..16) != Some(b"IHDR")
    {
        bail!("Slack channel icon must be a valid PNG");
    }
    if png_dimensions(image) != Some((SLACK_ICON_IMAGE_SIZE_PX, SLACK_ICON_IMAGE_SIZE_PX)) {
        bail!("Slack channel icon must be 512x512px");
    }
    Ok(())
}

struct ResolvedSlackIcon {
    image: Vec<u8>,
    background_color: String,
}

fn resolve_slack_icon(
    project_root: &Path,
    name: &str,
    icon: Option<&str>,
    configured_background_color: Option<&str>,
) -> Result<ResolvedSlackIcon> {
    match icon {
        Some(icon) => Ok(ResolvedSlackIcon {
            image: read_slack_icon(project_root, icon)?,
            background_color: configured_background_color
                .unwrap_or(DEFAULT_SLACK_BACKGROUND_COLOR)
                .to_string(),
        }),
        None => {
            let generated = trigger_icon::generate(name)?;
            Ok(ResolvedSlackIcon {
                image: generated.png,
                background_color: configured_background_color
                    .map(str::to_string)
                    .unwrap_or(generated.background_color),
            })
        }
    }
}

fn read_slack_icon(project_root: &Path, icon: &str) -> Result<Vec<u8>> {
    let resolved = channel_icon_file(
        project_root,
        icon,
        "Slack channel icon",
        SLACK_ICON_IMAGE_MAX_BYTES as u64,
    )?;
    let image =
        fs::read(&resolved).with_context(|| format!("failed to read Slack channel icon {icon}"))?;
    validate_slack_icon(&image)?;
    Ok(image)
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ChannelManifestHeader {
    name: String,
    provider: String,
    transport: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SlackManifest {
    name: String,
    provider: String,
    transport: String,
    auto_reply: bool,
    required_permissions: Vec<String>,
    required_env: Vec<String>,
    /// Read by the connection checks, not by setup.
    #[serde(default)]
    agent_connections: Option<serde_json::Value>,
    provider_config: SlackProviderConfig,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SlackProviderConfig {
    app: SlackProviderAppConfig,
    trigger_on_all_messages: bool,
    // SDKs only emit this for modes older CLIs cannot express.
    #[serde(default)]
    trigger_mode: Option<SlackTriggerMode>,
    allow_bot_triggers: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SlackProviderAppConfig {
    name: Option<String>,
    description: Option<String>,
    icon: Option<String>,
    background_color: Option<String>,
}

#[derive(Debug)]
struct PreparedSlackApp {
    name: String,
    description: String,
    icon: Vec<u8>,
    background_color: String,
}

#[derive(Debug)]
pub(crate) struct PreparedTriggerChannel {
    name: String,
    app: PreparedSlackApp,
    trigger_on_all_messages: bool,
    trigger_mode: Option<SlackTriggerMode>,
    allow_bot_triggers: bool,
}

#[derive(Debug, PartialEq)]
enum SetupState {
    Active,
    ActionRequired(RequiredAction),
}

#[derive(Debug, PartialEq)]
enum RequiredAction {
    Authorize { message: String, url: Url },
    Approve { message: String, url: Option<Url> },
}

impl RequiredAction {
    fn heading(&self) -> &'static str {
        match self {
            Self::Authorize { .. } => "Connect Slack",
            Self::Approve { .. } => "Approve Slack",
        }
    }

    fn message(&self) -> &str {
        match self {
            Self::Authorize { message, .. } | Self::Approve { message, .. } => message,
        }
    }

    fn url(&self) -> Option<&Url> {
        match self {
            Self::Authorize { url, .. } => Some(url),
            Self::Approve { url, .. } => url.as_ref(),
        }
    }

    /// What the user does with the action's link.
    fn instruction(&self) -> &'static str {
        match self {
            Self::Authorize { .. } => "Authorize LangSmith to set up your Slack app.",
            Self::Approve { .. } => {
                "Install the Slack app. A workspace admin may need to approve it first."
            }
        }
    }

    fn retry_guidance(&self) -> String {
        let action = match self {
            Self::Authorize { .. } => "Authorize",
            Self::Approve { .. } => "Approve",
        };
        match self.url() {
            Some(url) => {
                format!(
                    "{} {action} the Slack app at {url}, then rerun `mda deploy`.",
                    self.message()
                )
            }
            None => format!(
                "{} {action} the Slack app, then rerun `mda deploy`.",
                self.message()
            ),
        }
    }
}

pub(crate) fn prepare(
    requirements: &ChannelRequirements,
    wait: bool,
    project_root: &Path,
    language: Language,
    assistant_id: &str,
) -> Result<Option<PreparedTriggerChannel>> {
    let mut trigger_requirement = None;
    for (map_name, value) in requirements {
        let header: ChannelManifestHeader = serde_json::from_value(value.clone()).context(
            "channel requirements must include valid name, provider, and optional transport",
        )?;
        if header.transport.as_deref() != Some("trigger_server") {
            continue;
        }
        if trigger_requirement.is_some() {
            bail!("Deploy currently supports one channel per agent.");
        }
        trigger_requirement = Some((map_name, value, header));
    }
    let Some((map_name, value, header)) = trigger_requirement else {
        return Ok(None);
    };

    if map_name != &header.name {
        bail!("Channel name must match its manifest key.");
    }
    if header.provider != "slack" {
        bail!("Deploy currently supports only Slack channels.");
    }

    let SlackManifest {
        name,
        provider: _provider,
        transport: _transport,
        // Either value deploys: the channel itself posts the run's answer, or
        // with `reply: "none"` leaves it to the agent; Trigger Server never does.
        auto_reply: _auto_reply,
        required_permissions,
        required_env,
        agent_connections: _agent_connections,
        provider_config,
    } = serde_json::from_value(value.clone()).context("Slack channel configuration is invalid.")?;
    if !required_permissions.is_empty() {
        bail!(
            "Slack channel configuration requires {}=[].",
            language.ident("requiredPermissions", "required_permissions")
        );
    }
    if !required_env.is_empty() {
        bail!(
            "Slack channel configuration requires {}=[].",
            language.ident("requiredEnv", "required_env")
        );
    }
    if !wait {
        bail!("An agent with a Slack channel cannot be deployed with --no-wait.");
    }
    let trigger_on_all_messages = match provider_config.trigger_mode {
        Some(mode)
            if provider_config.trigger_on_all_messages != (mode == SlackTriggerMode::All) =>
        {
            bail!(
                "Slack channel configuration has a {} that conflicts with {}; set only {}.",
                language.ident("triggerOnAllMessages", "trigger_on_all_messages"),
                language.ident("triggerMode", "trigger_mode"),
                language.ident("triggerMode", "trigger_mode")
            );
        }
        Some(mode) => mode == SlackTriggerMode::All,
        None => provider_config.trigger_on_all_messages,
    };
    let SlackProviderAppConfig {
        name: authored_app_name,
        description,
        icon,
        background_color,
    } = provider_config.app;
    let app_name = authored_app_name.unwrap_or_else(|| default_slack_app_name(assistant_id));
    let resolved_icon = resolve_slack_icon(
        project_root,
        &app_name,
        icon.as_deref(),
        background_color.as_deref(),
    )?;

    Ok(Some(PreparedTriggerChannel {
        name,
        app: PreparedSlackApp {
            name: app_name,
            description: description.unwrap_or_default(),
            icon: resolved_icon.image,
            background_color: resolved_icon.background_color,
        },
        trigger_on_all_messages,
        // "all" travels as the legacy flag so older Trigger Servers read it.
        trigger_mode: provider_config
            .trigger_mode
            .filter(|mode| *mode != SlackTriggerMode::All),
        allow_bot_triggers: provider_config.allow_bot_triggers,
    }))
}

fn default_slack_app_name(assistant_id: &str) -> String {
    assistant_id
        .chars()
        .take(SLACK_APP_NAME_MAX_CHARS)
        .collect::<String>()
        .trim_end_matches('-')
        .to_string()
}

fn endpoint_url(agent_server_url: &str, channel_name: &str) -> Result<Url> {
    let mut url = super::helpers::validated_agent_server_url(agent_server_url)?;
    url.path_segments_mut()
        .map_err(|_| anyhow!("Agent Server URL cannot be a base URL"))?
        .pop_if_empty()
        .extend(["v1", "channels", channel_name, "events"]);
    Ok(url)
}

fn build_setup_request(
    prepared: &PreparedTriggerChannel,
    agent_server_url: &str,
    deployment_id: &str,
    assistant_id: &str,
) -> Result<ChannelSetupRequest> {
    Ok(ChannelSetupRequest {
        provider: "slack".to_string(),
        target: ChannelSetupTarget {
            deployment_id: deployment_id.to_string(),
            endpoint_url: endpoint_url(agent_server_url, &prepared.name)?.to_string(),
            channel_name: prepared.name.clone(),
            agent_id: assistant_id.to_string(),
        },
        slack: slack_setup(prepared),
    })
}

fn slack_setup(prepared: &PreparedTriggerChannel) -> SlackChannelSetup {
    SlackChannelSetup {
        app: SlackChannelApp {
            name: prepared.app.name.clone(),
            description: prepared.app.description.clone(),
            image: format!(
                "data:image/png;base64,{}",
                BASE64.encode(&prepared.app.icon)
            ),
            background_color: prepared.app.background_color.clone(),
        },
        options: SlackChannelOptions {
            trigger_on_all_messages: prepared.trigger_on_all_messages,
            trigger_mode: prepared.trigger_mode,
            allow_bot_triggers: prepared.allow_bot_triggers,
        },
        customer_app: None,
    }
}

/// The channel as a build artifact: everything host-backend needs to call the
/// channel setup API once the Agent Server URL and deployment id are known.
pub(crate) fn artifact(prepared: &PreparedTriggerChannel) -> serde_json::Value {
    serde_json::json!({
        "provider": "slack",
        "name": prepared.name,
        "slack": slack_setup(prepared),
    })
}

const SKIPPED_SETUP: &str = "Continuing without the Slack channel. The deployment is live, but it receives no Slack events until you run `mda deploy` again and finish setting up the Slack app.";

/// Fail before uploading when registering the channel could not finish. The
/// env credentials are checked always. Without a terminal, a new channel
/// needs them unless LangSmith can create the Slack app on its own: it
/// cannot on a self-hosted LangSmith, and first needs the user to authorize
/// it in Slack when they have not yet.
pub(crate) fn preflight(
    client: &langsmith::Client,
    prepared: &PreparedTriggerChannel,
    deployment_id: Option<&str>,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    if slack_app::credentials_env(env_vars)?.is_some() || is_interactive() {
        return Ok(());
    }
    let registered = deployment_id.map(|id| registration(client, id, &prepared.name));
    unattended_outcome(registered, || unattended_problem(client))
}

/// Why LangSmith can't create the Slack app without a terminal, if it can't.
fn unattended_problem(client: &langsmith::Client) -> Option<String> {
    let self_hosted = client.is_self_hosted();
    // Not knowing whether LangSmith can create the app leaves it to setup.
    let manager = if self_hosted {
        None
    } else {
        client.slack_manager().ok().flatten()
    };
    slack_app::unattended_problem(self_hosted, manager.as_ref())
}

/// Whether Trigger Server already has the deployment's channel, so deploy
/// keeps its Slack app instead of asking for one; an error when it can't say.
fn registration(
    client: &langsmith::Client,
    deployment_id: &str,
    channel_name: &str,
) -> Result<bool> {
    Ok(client
        .trigger_channels(deployment_id)?
        .iter()
        .any(|channel| channel.provider == "slack" && channel.channel_name == channel_name))
}

/// Whether a deploy without a terminal or the user's own Slack app can set up
/// the channel: one Trigger Server already has keeps its app, and otherwise
/// LangSmith has to be able to create one (`problem` says why it can't). A
/// failed lookup then says so, not that the channel needs an app: it may well
/// work already.
fn unattended_outcome(
    registered: Option<Result<bool>>,
    problem: impl FnOnce() -> Option<String>,
) -> Result<()> {
    if matches!(registered, Some(Ok(true))) {
        return Ok(());
    }
    match (problem(), registered) {
        (None, _) => Ok(()),
        (Some(_), Some(Err(error))) => Err(lookup_failed(&error)),
        (Some(problem), _) => bail!(problem),
    }
}

fn lookup_failed(error: &anyhow::Error) -> anyhow::Error {
    anyhow!(
        "Could not check whether this agent's Slack channel is already set up ({error:#}). Rerun `mda deploy` to try again, or set MDA_SLACK_BOT_TOKEN and MDA_SLACK_SIGNING_SECRET to your app's bot token and signing secret."
    )
}

/// Register the channel with Trigger Server for the live deployment. As for
/// the GitHub and Linear channels, a failed attempt offers to try again or to
/// continue without the channel; without a terminal the error ends setup.
pub(crate) fn register(
    client: &langsmith::Client,
    prepared: &PreparedTriggerChannel,
    agent_server_url: &str,
    deployment_id: &str,
    assistant_id: &str,
    env_vars: &BTreeMap<String, String>,
) -> Result<()> {
    let mut ui = LiveUi {
        interactive: is_interactive(),
    };
    let registered = channel_app::retry_or_skip(
        &mut ui,
        (),
        SKIPPED_SETUP,
        |ui, ()| {
            register_once(
                client,
                prepared,
                agent_server_url,
                deployment_id,
                assistant_id,
                env_vars,
                ui.interactive,
            )
        },
        |_| Ok(()),
    )?;
    if registered.is_some() {
        ui.success(READY)?;
    }
    Ok(())
}

const READY: &str =
    "Slack channel ready · the Slack app sends its events to this deployment through LangSmith";

/// What Slack setup shows in a terminal.
struct LiveUi {
    interactive: bool,
}

impl SetupUi for LiveUi {
    fn interactive(&self) -> bool {
        self.interactive
    }

    fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool> {
        channel_app::ask_retry_or_skip("Slack channel setup", "Slack", error)
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

/// One attempt at registering the channel, choosing its app when Trigger
/// Server has none for it yet.
fn register_once(
    client: &langsmith::Client,
    prepared: &PreparedTriggerChannel,
    agent_server_url: &str,
    deployment_id: &str,
    assistant_id: &str,
    env_vars: &BTreeMap<String, String>,
    interactive: bool,
) -> Result<()> {
    let mut request = build_setup_request(prepared, agent_server_url, deployment_id, assistant_id)?;
    let registered = match registration(client, deployment_id, &prepared.name) {
        Ok(registered) => registered,
        // Without a terminal, deploy can't ask for an app instead.
        Err(error)
            if !interactive
                && slack_app::credentials_env(env_vars)?.is_none()
                && unattended_problem(client).is_some() =>
        {
            return Err(lookup_failed(&error));
        }
        Err(error) => {
            // Asking again is harmless: Trigger Server keeps a registered app.
            let _ = cliclack::log::warning(format!(
                "Could not check whether the Slack channel is already set up: {error}"
            ));
            false
        }
    };
    let customer_app = if registered {
        // Trigger Server keeps the app's credentials; ones in the env must match.
        slack_app::credentials_env(env_vars)?
    } else {
        let mut choice = slack_app::LiveChoice::new(client, &request.slack, env_vars, interactive);
        match slack_app::choose_app(&mut choice)? {
            SlackApp::Managed => None,
            SlackApp::Customer(app) => Some(app),
        }
    };
    if customer_app.is_some() {
        request.slack.customer_app = customer_app;
        return register_customer_app(
            interactive,
            |move_from| {
                if let Some(app) = request.slack.customer_app.as_mut() {
                    app.move_from_deployment = move_from.map(str::to_string);
                }
                client.setup_trigger_channel(&request)
            },
            confirm_slack_app_move,
        );
    }
    run_registration(
        || client.setup_trigger_channel(&request),
        |action| show_action(action, interactive),
        channel_app::wait,
    )
}

/// Register the user's own app, which Trigger Server activates at once. When
/// another deployment in the workspace uses the app, offer to move it here.
fn register_customer_app<S, C>(interactive: bool, mut setup: S, confirm_move: C) -> Result<()>
where
    S: FnMut(Option<&str>) -> Result<ChannelSetupResponse>,
    C: FnOnce(&str) -> Result<bool>,
{
    let response = match setup(None) {
        Err(error) => {
            let Some(SlackAppInUse { deployment_id }) = error.downcast_ref::<SlackAppInUse>()
            else {
                return Err(error);
            };
            let deployment_id = deployment_id.clone();
            if !interactive {
                bail!(
                    "Slack app is connected to deployment {deployment_id} and stays there. Rerun `mda deploy` in a terminal to move it to this deployment, or use another Slack app."
                );
            }
            if !confirm_move(&deployment_id)? {
                bail!(
                    "Slack app stays connected to deployment {deployment_id}. Use another Slack app for this deployment."
                );
            }
            let response = setup(Some(&deployment_id))?;
            let _ = cliclack::log::info(format!(
                "Moved the Slack app from deployment {deployment_id}."
            ));
            response
        }
        response => response?,
    };
    match classify_setup_response(response)? {
        SetupState::Active => Ok(()),
        SetupState::ActionRequired(_) => {
            bail!("Slack channel setup did not finish with your Slack app. Check its bot token and signing secret, then rerun `mda deploy`.")
        }
    }
}

fn confirm_slack_app_move(deployment_id: &str) -> Result<bool> {
    cliclack::confirm(format!(
        "Slack app is connected to deployment {deployment_id}. Move it to this deployment? That deployment's Slack channel stops receiving events."
    ))
    .initial_value(true)
    .interact()
    .context("failed to confirm moving the Slack app")
}

/// Show what the user does in Slack before setup can finish. In a terminal
/// the link also opens in the browser; without one, whoever runs deploy (a
/// coding agent, say) hands it to the person who can act on it.
fn show_action(action: &RequiredAction, interactive: bool) -> Result<()> {
    let heading = action.heading();
    let instruction = action.instruction();
    let message = match (action.url(), interactive) {
        (Some(url), true) => format!(
            "{heading}\n{instruction} If your browser does not open, visit:\n{url}\nSetup continues here once Slack reports it. Press Ctrl-C to skip."
        ),
        (Some(url), false) => format!(
            "{heading}\n{instruction} Open this link in a browser signed in to Slack. Deploy waits up to an hour for it to finish.\n{url}"
        ),
        (None, true) => format!(
            "{heading}\nWaiting for a Slack workspace admin to approve the Slack app. Press Ctrl-C to skip."
        ),
        (None, false) => format!(
            "{heading}\nWaiting for a Slack workspace admin to approve the Slack app. Deploy waits up to an hour for it."
        ),
    };
    cliclack::log::step(message)?;
    if let (Some(url), true) = (action.url(), interactive) {
        crate::langsmith::login::open_browser(url.as_str());
    }
    Ok(())
}

fn classify_setup_response(response: ChannelSetupResponse) -> Result<SetupState> {
    for id in [
        response.channel_id.as_deref(),
        response.source_registration_id.as_deref(),
    ]
    .into_iter()
    .flatten()
    {
        uuid::Uuid::parse_str(id)
            .map_err(|_| anyhow!("Channel setup returned an invalid response."))?;
    }
    if matches!(response.status, ChannelSetupStatus::Active) {
        if response.channel_id.is_none() {
            bail!("Channel setup returned an invalid response.");
        }
        return Ok(SetupState::Active);
    }

    let mut message = match response.status {
        ChannelSetupStatus::AuthorizationRequired => {
            "Slack channel setup requires authorization.".to_string()
        }
        ChannelSetupStatus::PendingApproval => {
            "Slack channel setup is pending workspace approval.".to_string()
        }
        ChannelSetupStatus::Active => return Ok(SetupState::Active),
    };
    if let Some(detail) = response.message.as_deref().and_then(safe_message) {
        message.push(' ');
        message.push_str(detail);
    }
    let authorization_url = response
        .authorization_url
        .as_deref()
        .and_then(safe_authorization_url);
    match response.status {
        ChannelSetupStatus::AuthorizationRequired => {
            let Some(url) = authorization_url else {
                bail!("{message} Authorize the Slack app, then rerun `mda deploy`.");
            };
            Ok(SetupState::ActionRequired(RequiredAction::Authorize {
                message,
                url,
            }))
        }
        ChannelSetupStatus::PendingApproval => {
            Ok(SetupState::ActionRequired(RequiredAction::Approve {
                message,
                url: authorization_url,
            }))
        }
        ChannelSetupStatus::Active => Ok(SetupState::Active),
    }
}

/// How often setup asks Trigger Server again while it waits for the user.
/// Each check retries the setup, which calls Slack, so it is not as eager as
/// the GitHub installation check.
const ACTION_POLL_INTERVAL: Duration = Duration::from_secs(5);
/// How long setup waits for the user, as GitHub setup does.
const ACTION_WAIT_LIMIT: Duration = Duration::from_secs(60 * 60);

/// Ask Trigger Server to set the channel up until it is active. While it
/// waits for the user to authorize LangSmith or to install the app, `show`
/// shows what to do (again only when that changes), and setup asks again
/// after each `wait`. Once the user has been shown what to do, a request that
/// may succeed when made again (a timeout, 5xx or 429) doesn't end the wait,
/// as the GitHub installation check keeps going; any other error does. Ctrl-C
/// stops waiting; so does the hour running out.
fn run_registration<S, A, W>(mut setup: S, mut show: A, mut wait: W) -> Result<()>
where
    S: FnMut() -> Result<ChannelSetupResponse>,
    A: FnMut(&RequiredAction) -> Result<()>,
    W: FnMut(Duration) -> WaitTick,
{
    let mut shown: Option<(&'static str, bool)> = None;
    let mut last: Option<RequiredAction> = None;
    let mut waited = Duration::ZERO;
    loop {
        match setup() {
            Ok(response) => {
                let action = match classify_setup_response(response)? {
                    SetupState::Active => return Ok(()),
                    SetupState::ActionRequired(action) => action,
                };
                // Every answer carries a fresh link, and the one shown still works.
                let step = (action.heading(), action.url().is_some());
                if shown != Some(step) {
                    show(&action)?;
                    shown = Some(step);
                }
                last = Some(action);
            }
            Err(error) if last.is_some() && is_transient_setup_error(&error) => {}
            Err(error) => return Err(error),
        }
        if waited >= ACTION_WAIT_LIMIT {
            let guidance = last.as_ref().map(RequiredAction::retry_guidance);
            bail!(
                "Stopped waiting for Slack after an hour. {}",
                guidance.unwrap_or_default()
            );
        }
        if wait(ACTION_POLL_INTERVAL) == WaitTick::Interrupted {
            return Err(std::io::Error::from(std::io::ErrorKind::Interrupted).into());
        }
        waited += ACTION_POLL_INTERVAL;
    }
}

fn safe_message(message: &str) -> Option<&str> {
    let trimmed = message.trim();
    (!trimmed.is_empty()
        && trimmed.chars().count() <= 256
        && !trimmed.chars().any(char::is_control))
    .then_some(trimmed)
}

fn safe_authorization_url(raw_url: &str) -> Option<Url> {
    if raw_url.chars().any(char::is_control) {
        return None;
    }
    let url = Url::parse(raw_url).ok()?;
    (url.scheme() == "https"
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
        && url.fragment().is_none())
    .then_some(url)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::{json, Value};

    use super::*;

    fn prepare(
        requirements: &ChannelRequirements,
        wait: bool,
    ) -> Result<Option<PreparedTriggerChannel>> {
        super::prepare(
            requirements,
            wait,
            Path::new("."),
            Language::TypeScript,
            "assistant-id",
        )
    }

    fn png_icon(width: u32, height: u32) -> Vec<u8> {
        let mut icon = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR".to_vec();
        icon.extend_from_slice(&width.to_be_bytes());
        icon.extend_from_slice(&height.to_be_bytes());
        icon.resize(33, 0);
        icon
    }

    fn requirements() -> ChannelRequirements {
        [(
            "slack".to_string(),
            json!({
                "name": "slack",
                "provider": "slack",
                "transport": "trigger_server",
                "autoReply": true,
                "requiredPermissions": [],
                "requiredEnv": [],
                "providerConfig": {
                    "app": {
                        "name": "Support Bot",
                        "description": "Answers support questions",
                        "backgroundColor": "#a1b2c3"
                    },
                    "triggerOnAllMessages": false,
                    "allowBotTriggers": false
                }
            }),
        )]
        .into_iter()
        .collect()
    }

    fn direct_slack_manifest(name: &str) -> Value {
        json!({
            "name": name,
            "provider": "slack",
            "autoReply": true,
            "requiredPermissions": ["app_mentions:read", "chat:write"],
            "requiredEnv": ["SLACK_SIGNING_SECRET", "SLACK_BOT_TOKEN"],
            "providerConfig": {
                "mentionBehavior": "strip",
                "filters": {"allowSharedConversations": false},
                "conversation": {
                    "appMention": "thread",
                    "directMessage": "conversation"
                }
            }
        })
    }

    fn active_setup_response() -> ChannelSetupResponse {
        ChannelSetupResponse {
            status: ChannelSetupStatus::Active,
            channel_id: Some("a6190f67-865d-4705-8171-35c90e64c730".to_string()),
            source_registration_id: None,
            authorization_url: None,
            message: None,
        }
    }

    fn authorization_setup_response() -> ChannelSetupResponse {
        ChannelSetupResponse {
            status: ChannelSetupStatus::AuthorizationRequired,
            channel_id: None,
            source_registration_id: None,
            authorization_url: Some("https://trigger.example/authorize".to_string()),
            message: None,
        }
    }

    fn pending_approval_setup_response() -> ChannelSetupResponse {
        ChannelSetupResponse {
            status: ChannelSetupStatus::PendingApproval,
            channel_id: None,
            source_registration_id: None,
            authorization_url: None,
            message: None,
        }
    }

    const HOLDER: &str = "b809e66e-0000-4000-8000-000000000002";

    fn in_use() -> anyhow::Error {
        SlackAppInUse {
            deployment_id: HOLDER.into(),
        }
        .into()
    }

    #[test]
    fn a_slack_app_another_deployment_uses_moves_once_confirmed() {
        let mut moves = Vec::new();
        let mut asked = None;

        register_customer_app(
            true,
            |move_from| {
                moves.push(move_from.map(str::to_string));
                if move_from.is_none() {
                    Err(in_use())
                } else {
                    Ok(active_setup_response())
                }
            },
            |deployment_id| {
                asked = Some(deployment_id.to_string());
                Ok(true)
            },
        )
        .unwrap();

        assert_eq!(asked.as_deref(), Some(HOLDER));
        assert_eq!(moves, vec![None, Some(HOLDER.to_string())]);
    }

    #[test]
    fn a_declined_slack_app_move_leaves_it_where_it_is() {
        let mut calls = 0;

        let error = register_customer_app(
            true,
            |_| {
                calls += 1;
                Err(in_use())
            },
            |_| Ok(false),
        )
        .unwrap_err();

        assert_eq!(calls, 1);
        assert!(error
            .to_string()
            .contains(&format!("stays connected to deployment {HOLDER}")));
    }

    #[test]
    fn unattended_deploys_never_move_a_slack_app() {
        let mut calls = 0;

        let error = register_customer_app(
            false,
            |_| {
                calls += 1;
                Err(in_use())
            },
            |_| -> Result<bool> { panic!("an unattended deploy must not ask") },
        )
        .unwrap_err();

        assert_eq!(calls, 1);
        assert!(error.to_string().contains(HOLDER));
    }

    #[test]
    fn other_customer_app_setup_failures_are_not_offered_a_move() {
        let error = register_customer_app(
            true,
            |_| {
                Err(anyhow!(
                    "Channel setup failed (HTTP 409): This Slack app is already in use."
                ))
            },
            |_| -> Result<bool> { panic!("only an app another deployment uses is moved") },
        )
        .unwrap_err();

        assert!(error.to_string().contains("already in use"));
    }

    #[test]
    fn unattended_deploys_without_an_app_go_on_only_for_a_registered_channel() {
        let no_app = || Some(slack_app::unattended_message(true));
        let failed = || Some(Err(anyhow!("Channel listing failed (HTTP 503).")));
        assert!(unattended_outcome(Some(Ok(true)), no_app).is_ok());
        assert_eq!(
            unattended_outcome(Some(Ok(false)), no_app)
                .unwrap_err()
                .to_string(),
            slack_app::unattended_message(true)
        );
        assert_eq!(
            unattended_outcome(None, no_app).unwrap_err().to_string(),
            slack_app::unattended_message(true)
        );
        // A failed listing names itself, not a missing Slack app.
        let error = unattended_outcome(failed(), no_app)
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("Channel listing failed (HTTP 503)."),
            "{error}"
        );
        assert!(error.contains("Could not check"), "{error}");
        // LangSmith can create the app, so nothing stops the deploy.
        assert!(unattended_outcome(failed(), || None).is_ok());
        assert!(unattended_outcome(Some(Ok(false)), || None).is_ok());
    }

    #[test]
    fn prepare_accepts_the_trigger_slack_manifest() {
        let prepared = prepare(&requirements(), true).unwrap().unwrap();

        assert_eq!(prepared.name, "slack");
        assert!(!prepared.trigger_on_all_messages);
        assert_eq!(prepared.trigger_mode, None);
        assert!(!prepared.allow_bot_triggers);
    }

    #[test]
    fn prepare_sends_trigger_mode_only_when_it_is_set() {
        for (provider_config, options) in [
            (
                json!({"triggerOnAllMessages": false}),
                json!({"triggerOnAllMessages": false, "allowBotTriggers": false}),
            ),
            (
                json!({"triggerOnAllMessages": true}),
                json!({"triggerOnAllMessages": true, "allowBotTriggers": false}),
            ),
            (
                json!({"triggerOnAllMessages": false, "triggerMode": "thread"}),
                json!({
                    "triggerOnAllMessages": false,
                    "triggerMode": "thread",
                    "allowBotTriggers": false
                }),
            ),
            (
                json!({"triggerOnAllMessages": false, "triggerMode": "mention"}),
                json!({
                    "triggerOnAllMessages": false,
                    "triggerMode": "mention",
                    "allowBotTriggers": false
                }),
            ),
            (
                json!({"triggerOnAllMessages": true, "triggerMode": "all"}),
                json!({"triggerOnAllMessages": true, "allowBotTriggers": false}),
            ),
        ] {
            let mut requirements = requirements();
            let config = requirements.get_mut("slack").unwrap()["providerConfig"]
                .as_object_mut()
                .unwrap();
            for (key, value) in provider_config.as_object().unwrap() {
                config.insert(key.clone(), value.clone());
            }
            let prepared = prepare(&requirements, true).unwrap().unwrap();

            assert_eq!(
                serde_json::to_value(slack_setup(&prepared).options).unwrap(),
                options
            );
        }
    }

    #[test]
    fn prepare_rejects_a_legacy_flag_that_conflicts_with_trigger_mode() {
        let mut requirements = requirements();
        let config = &mut requirements.get_mut("slack").unwrap()["providerConfig"];
        config["triggerOnAllMessages"] = json!(true);
        config["triggerMode"] = json!("thread");

        let error = prepare(&requirements, true).unwrap_err().to_string();
        assert!(error.contains("conflicts with triggerMode"), "{error}");

        let mut requirements = self::requirements();
        requirements.get_mut("slack").unwrap()["providerConfig"]["triggerMode"] = json!("all");
        assert!(prepare(&requirements, true).is_err());

        let mut requirements = self::requirements();
        requirements.get_mut("slack").unwrap()["providerConfig"]["triggerMode"] = json!("channel");
        assert!(prepare(&requirements, true).is_err());
    }

    #[test]
    fn prepare_defaults_missing_app_metadata_from_the_assistant_id() {
        let mut requirements = requirements();
        requirements.get_mut("slack").unwrap()["providerConfig"]["app"] = json!({});
        let assistant_id = "abcdefghijklmnopqrstuvwxyzabcdefghij";

        let prepared = super::prepare(
            &requirements,
            true,
            Path::new("."),
            Language::TypeScript,
            assistant_id,
        )
        .unwrap()
        .unwrap();
        let expected_name = "abcdefghijklmnopqrstuvwxyzabcdefghi";
        let generated = trigger_icon::generate(expected_name).unwrap();

        assert_eq!(prepared.app.name, expected_name);
        assert_eq!(prepared.app.description, "");
        assert_eq!(prepared.app.icon, generated.png);
    }

    #[test]
    fn prepare_accepts_a_custom_icon_path() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("channels/assets")).unwrap();
        let icon = png_icon(512, 512);
        fs::write(root.path().join("channels/assets/support.png"), &icon).unwrap();
        let mut requirements = requirements();
        requirements.get_mut("slack").unwrap()["providerConfig"]["app"]["icon"] =
            json!("assets/support.png");

        let prepared = super::prepare(
            &requirements,
            true,
            root.path(),
            Language::TypeScript,
            "assistant-id",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            (prepared.app.icon, prepared.app.background_color),
            (icon, "#a1b2c3".to_string())
        );
    }

    #[test]
    fn resolve_slack_icon_derives_image_and_background_when_icon_is_omitted() {
        let root = tempfile::tempdir().unwrap();

        let icon = resolve_slack_icon(root.path(), "Support Bot", None, None).unwrap();
        let generated = trigger_icon::generate("Support Bot").unwrap();

        assert_eq!(
            (icon.image, icon.background_color),
            (generated.png, generated.background_color)
        );
    }

    #[test]
    fn resolve_slack_icon_keeps_authored_background_when_icon_is_omitted() {
        let root = tempfile::tempdir().unwrap();

        let icon = resolve_slack_icon(root.path(), "Support Bot", None, Some("#a1b2c3")).unwrap();
        let generated = trigger_icon::generate("Support Bot").unwrap();

        assert_eq!(icon.image, generated.png);
        assert_eq!(icon.background_color, "#a1b2c3");
        assert_ne!(icon.background_color, generated.background_color);
    }

    #[test]
    fn resolve_slack_icon_keeps_an_explicit_default_background_when_icon_is_omitted() {
        let root = tempfile::tempdir().unwrap();

        let icon = resolve_slack_icon(
            root.path(),
            "Support Bot",
            None,
            Some(DEFAULT_SLACK_BACKGROUND_COLOR),
        )
        .unwrap();
        let generated = trigger_icon::generate("Support Bot").unwrap();

        assert_eq!(icon.image, generated.png);
        assert_eq!(icon.background_color, DEFAULT_SLACK_BACKGROUND_COLOR);
        assert_ne!(icon.background_color, generated.background_color);
    }

    #[test]
    fn read_slack_icon_rejects_an_absolute_path() {
        let root = tempfile::tempdir().unwrap();
        let icon = tempfile::NamedTempFile::new().unwrap();

        let error = read_slack_icon(root.path(), icon.path().to_str().unwrap()).unwrap_err();

        assert!(error.to_string().contains("relative to channels/"));
    }

    #[test]
    fn read_slack_icon_takes_any_relative_path_inside_channels() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("channels/assets")).unwrap();
        let png = png_icon(512, 512);
        fs::write(root.path().join("channels/assets/icon.png"), &png).unwrap();

        // Paths Slack's icon has always accepted, unlike Linear's plain paths.
        for icon in ["./assets/icon.png", "assets/./icon.png", "assets//icon.png"] {
            assert_eq!(read_slack_icon(root.path(), icon).unwrap(), png, "{icon}");
        }
    }

    #[test]
    fn read_slack_icon_rejects_parent_traversal() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        fs::write(root.path().join("outside.png"), b"outside").unwrap();

        let error = read_slack_icon(root.path(), "../outside.png").unwrap_err();

        assert!(error.to_string().contains("relative to channels/"));
    }

    #[cfg(unix)]
    #[test]
    fn read_slack_icon_rejects_a_symlink() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        fs::write(root.path().join("outside.png"), b"outside").unwrap();
        symlink(
            root.path().join("outside.png"),
            root.path().join("channels/icon.png"),
        )
        .unwrap();

        let error = read_slack_icon(root.path(), "icon.png").unwrap_err();

        assert!(error.to_string().contains("symlink"));
    }

    #[test]
    fn read_slack_icon_rejects_a_directory() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("channels/assets")).unwrap();

        let error = read_slack_icon(root.path(), "assets").unwrap_err();

        assert!(error.to_string().contains("regular file"));
    }

    #[test]
    fn prepare_rejects_a_jpeg_icon() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        fs::write(root.path().join("channels/icon.jpg"), b"\xff\xd8\xff").unwrap();
        let mut requirements = requirements();
        requirements.get_mut("slack").unwrap()["providerConfig"]["app"]["icon"] = json!("icon.jpg");

        let error = super::prepare(
            &requirements,
            true,
            root.path(),
            Language::TypeScript,
            "assistant-id",
        )
        .unwrap_err();

        assert!(error.to_string().contains("valid PNG"));
    }

    #[test]
    fn read_slack_icon_rejects_incorrect_dimensions() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        fs::write(root.path().join("channels/icon.png"), png_icon(256, 512)).unwrap();

        let error = read_slack_icon(root.path(), "icon.png").unwrap_err();

        assert!(error.to_string().contains("512x512"));
    }

    #[test]
    fn read_slack_icon_rejects_an_oversized_png() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        let mut icon = png_icon(512, 512);
        icon.resize(1_000_001, 0);
        fs::write(root.path().join("channels/icon.png"), icon).unwrap();

        let error = read_slack_icon(root.path(), "icon.png").unwrap_err();

        assert!(error.to_string().contains("1 MB"));
    }

    #[test]
    fn prepare_ignores_a_direct_slack_manifest() {
        let requirements = [("slack".to_string(), direct_slack_manifest("slack"))]
            .into_iter()
            .collect();

        let prepared = prepare(&requirements, false).unwrap();

        assert!(prepared.is_none());
    }

    #[test]
    fn prepare_ignores_slack_without_trigger_transport_when_required_env_is_empty() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest.as_object_mut().unwrap().remove("transport");
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let prepared = prepare(&requirements, false).unwrap();

        assert!(prepared.is_none());
    }

    #[test]
    fn prepare_ignores_a_non_trigger_transport() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["transport"] = json!("direct");
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let prepared = prepare(&requirements, false).unwrap();

        assert!(prepared.is_none());
    }

    #[test]
    fn prepare_rejects_a_non_string_transport() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["transport"] = json!(42);
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert_eq!(
            error.to_string(),
            "channel requirements must include valid name, provider, and optional transport"
        );
    }

    #[test]
    fn prepare_selects_the_trigger_manifest_among_other_channels() {
        let mut requirements = requirements();
        requirements.insert("internal".to_string(), direct_slack_manifest("internal"));

        let prepared = prepare(&requirements, true).unwrap().unwrap();

        assert_eq!(prepared.name, "slack");
    }

    #[test]
    fn prepare_rejects_no_wait_for_a_trigger_channel() {
        let error = prepare(&requirements(), false).unwrap_err();

        assert_eq!(
            error.to_string(),
            "An agent with a Slack channel cannot be deployed with --no-wait."
        );
    }

    #[test]
    fn prepare_rejects_a_trigger_channel_with_a_non_slack_provider() {
        let requirements = [(
            "teams".to_string(),
            json!({
                "name": "teams",
                "provider": "teams",
                "transport": "trigger_server",
                "providerConfig": {
                    "tenantId": "tenant-id"
                }
            }),
        )]
        .into_iter()
        .collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert_eq!(
            error.to_string(),
            "Deploy currently supports only Slack channels."
        );
    }

    #[test]
    fn prepare_accepts_a_trigger_channel_that_leaves_replying_to_the_agent() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["autoReply"] = json!(false);
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let prepared = prepare(&requirements, true).unwrap().unwrap();

        assert_eq!(prepared.name, "slack");
    }

    #[test]
    fn prepare_rejects_a_trigger_channel_with_required_permissions() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["requiredPermissions"] = json!(["chat:write"]);
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert!(error.to_string().contains("requiredPermissions=[]"));
    }

    #[test]
    fn prepare_rejects_a_trigger_channel_with_required_env() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["requiredEnv"] = json!(["SLACK_BOT_TOKEN"]);
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert!(error.to_string().contains("requiredEnv=[]"));
    }

    #[test]
    fn prepare_accepts_a_noncanonical_channel_name() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["name"] = json!("support");
        let requirements = [("support".to_string(), manifest)].into_iter().collect();

        let prepared = prepare(&requirements, true).unwrap().unwrap();

        assert_eq!(prepared.name, "support");
    }

    #[test]
    fn prepare_rejects_a_channel_name_that_differs_from_its_map_key() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["name"] = json!("support");
        let requirements = [("help".to_string(), manifest)].into_iter().collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert_eq!(
            error.to_string(),
            "Channel name must match its manifest key."
        );
    }

    #[test]
    fn prepare_rejects_multiple_trigger_channels() {
        let mut support = requirements().remove("slack").unwrap();
        support["name"] = json!("support");
        let mut requirements = requirements();
        requirements.insert("support".to_string(), support);

        let error = prepare(&requirements, true).unwrap_err();

        assert_eq!(
            error.to_string(),
            "Deploy currently supports one channel per agent."
        );
    }

    #[test]
    fn setup_request_matches_the_trigger_server_contract() {
        let prepared = prepare(&requirements(), true).unwrap().unwrap();
        let request = build_setup_request(
            &prepared,
            "https://agent.example/base",
            "deployment-id",
            "assistant-id",
        )
        .unwrap();
        let mut value = serde_json::to_value(request).unwrap();
        let image = value["slack"]["app"]["image"].as_str().unwrap().to_string();
        value["slack"]["app"]["image"] = Value::String("<embedded png>".to_string());

        assert_eq!(
            value,
            json!({
                "provider": "slack",
                "target": {
                    "deployment_id": "deployment-id",
                    "endpoint_url": "https://agent.example/base/v1/channels/slack/events",
                    "channel_name": "slack",
                    "agent_id": "assistant-id"
                },
                "slack": {
                    "app": {
                        "name": "Support Bot",
                        "description": "Answers support questions",
                        "image": "<embedded png>",
                        "background_color": "#a1b2c3"
                    },
                    "options": {
                        "triggerOnAllMessages": false,
                        "allowBotTriggers": false
                    }
                }
            })
        );
        assert!(image.starts_with("data:image/png;base64,"));
    }

    #[test]
    fn setup_request_embeds_the_prepared_custom_icon() {
        let mut prepared = prepare(&requirements(), true).unwrap().unwrap();
        prepared.app.icon = b"custom-icon".to_vec();

        let request = build_setup_request(
            &prepared,
            "https://agent.example",
            "deployment-id",
            "assistant-id",
        )
        .unwrap();

        assert_eq!(
            request.slack.app.image,
            "data:image/png;base64,Y3VzdG9tLWljb24="
        );
    }

    #[test]
    fn setup_request_derives_the_icon_and_background_from_the_app_name() {
        let mut support_requirements = requirements();
        support_requirements.get_mut("slack").unwrap()["providerConfig"]["app"]
            .as_object_mut()
            .unwrap()
            .remove("backgroundColor");
        let support = prepare(&support_requirements, true).unwrap().unwrap();
        let mut billing_requirements = support_requirements.clone();
        billing_requirements.get_mut("slack").unwrap()["providerConfig"]["app"]["name"] =
            json!("Billing Bot");
        let billing = prepare(&billing_requirements, true).unwrap().unwrap();

        let support_request = build_setup_request(
            &support,
            "https://agent.example",
            "deployment-id",
            "assistant-id",
        )
        .unwrap();
        let billing_request = build_setup_request(
            &billing,
            "https://agent.example",
            "deployment-id",
            "assistant-id",
        )
        .unwrap();

        assert_ne!(
            (
                support_request.slack.app.image,
                support_request.slack.app.background_color
            ),
            (
                billing_request.slack.app.image,
                billing_request.slack.app.background_color
            )
        );
    }

    #[test]
    fn setup_request_targets_the_declared_channel_name() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["name"] = json!("support");
        let requirements = [("support".to_string(), manifest)].into_iter().collect();
        let prepared = prepare(&requirements, true).unwrap().unwrap();

        let request = build_setup_request(
            &prepared,
            "https://agent.example",
            "deployment-id",
            "assistant-id",
        )
        .unwrap();

        assert_eq!(
            request.target.endpoint_url,
            "https://agent.example/v1/channels/support/events"
        );
    }

    #[test]
    fn active_response_accepts_a_channel_uuid() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::Active,
            channel_id: Some("a6190f67-865d-4705-8171-35c90e64c730".to_string()),
            source_registration_id: None,
            authorization_url: None,
            message: None,
        };

        assert_eq!(
            classify_setup_response(response).unwrap(),
            SetupState::Active
        );
    }

    #[test]
    fn active_response_rejects_a_non_uuid_channel_id() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::Active,
            channel_id: Some("channel-id".to_string()),
            source_registration_id: None,
            authorization_url: None,
            message: None,
        };

        assert_eq!(
            classify_setup_response(response).unwrap_err().to_string(),
            "Channel setup returned an invalid response."
        );
    }

    #[test]
    fn active_response_requires_a_channel_id() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::Active,
            channel_id: None,
            source_registration_id: None,
            authorization_url: None,
            message: None,
        };

        assert_eq!(
            classify_setup_response(response).unwrap_err().to_string(),
            "Channel setup returned an invalid response."
        );
    }

    #[test]
    fn active_response_rejects_a_blank_channel_id() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::Active,
            channel_id: Some("   ".to_string()),
            source_registration_id: None,
            authorization_url: None,
            message: None,
        };

        assert!(classify_setup_response(response).is_err());
    }

    #[test]
    fn authorization_response_classifies_a_valid_https_url() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::AuthorizationRequired,
            channel_id: None,
            source_registration_id: None,
            authorization_url: Some(
                "https://trigger.example/authorize?state=resume-setup".to_string(),
            ),
            message: None,
        };

        assert_eq!(
            classify_setup_response(response).unwrap(),
            SetupState::ActionRequired(RequiredAction::Authorize {
                message: "Slack channel setup requires authorization.".to_string(),
                url: Url::parse("https://trigger.example/authorize?state=resume-setup").unwrap(),
            })
        );
    }

    #[test]
    fn pending_approval_omits_an_unsafe_url() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::PendingApproval,
            channel_id: None,
            source_registration_id: None,
            authorization_url: Some("http://unsafe.example/approve".to_string()),
            message: None,
        };

        assert_eq!(
            classify_setup_response(response).unwrap(),
            SetupState::ActionRequired(RequiredAction::Approve {
                message: "Slack channel setup is pending workspace approval.".to_string(),
                url: None,
            })
        );
    }

    #[test]
    fn authorization_response_rejects_an_unsafe_url() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::AuthorizationRequired,
            channel_id: None,
            source_registration_id: None,
            authorization_url: Some("http://unsafe.example/authorize".to_string()),
            message: None,
        };

        let rendered = classify_setup_response(response).unwrap_err().to_string();

        assert_eq!(
            rendered,
            "Slack channel setup requires authorization. Authorize the Slack app, then rerun `mda deploy`."
        );
    }

    #[test]
    fn pending_approval_includes_a_safe_trigger_message() {
        let response = ChannelSetupResponse {
            status: ChannelSetupStatus::PendingApproval,
            channel_id: None,
            source_registration_id: None,
            authorization_url: None,
            message: Some("Reconnect the existing Slack installation.".to_string()),
        };

        assert_eq!(
            classify_setup_response(response).unwrap(),
            SetupState::ActionRequired(RequiredAction::Approve {
                message: "Slack channel setup is pending workspace approval. Reconnect the existing Slack installation.".to_string(),
                url: None,
            })
        );
    }

    /// Run [`run_registration`] over `responses`, recording each setup call,
    /// shown action and wait. `interrupt_at` is the wait that gets Ctrl-C.
    fn register_with(
        responses: Vec<ChannelSetupResponse>,
        interrupt_at: Option<usize>,
    ) -> (Result<()>, Vec<String>) {
        let mut responses = responses.into_iter();
        let log = std::cell::RefCell::new(Vec::new());
        let mut waits = 0;
        let result = run_registration(
            || {
                log.borrow_mut().push("setup".to_string());
                Ok(responses.next().expect("unexpected setup call"))
            },
            |action| {
                log.borrow_mut().push(format!("show {}", action.heading()));
                Ok(())
            },
            |duration| {
                assert_eq!(duration, ACTION_POLL_INTERVAL);
                waits += 1;
                log.borrow_mut().push("wait".to_string());
                if interrupt_at == Some(waits) {
                    WaitTick::Interrupted
                } else {
                    WaitTick::Elapsed
                }
            },
        );
        (result, log.into_inner())
    }

    #[test]
    fn registration_keeps_waiting_through_a_failed_request_once_the_link_is_shown() {
        let transient = || {
            Err(anyhow::Error::new(crate::langsmith::TransientSetupError(
                "Channel setup failed (HTTP 503).".into(),
            )))
        };
        let mut responses: std::collections::VecDeque<Result<ChannelSetupResponse>> =
            std::collections::VecDeque::from([
                Ok(authorization_setup_response()),
                transient(),
                Ok(active_setup_response()),
            ]);
        let mut shows = 0;
        let result = run_registration(
            || responses.pop_front().expect("unexpected setup call"),
            |_| {
                shows += 1;
                Ok(())
            },
            |_| WaitTick::Elapsed,
        );
        assert!(result.is_ok(), "{result:?}");
        assert_eq!(shows, 1);

        // Before anything is shown, and for any other error, it ends.
        for first in [
            transient(),
            Err(anyhow!("Channel setup failed (HTTP 400).")),
        ] {
            let mut responses = std::collections::VecDeque::from([first]);
            let result = run_registration(
                || responses.pop_front().expect("unexpected setup call"),
                |_| Ok(()),
                |_| WaitTick::Elapsed,
            );
            assert!(result.is_err());
        }
        let mut responses: std::collections::VecDeque<Result<ChannelSetupResponse>> =
            std::collections::VecDeque::from([
                Ok(authorization_setup_response()),
                Err(anyhow!("Channel setup failed (HTTP 400).")),
            ]);
        let error = run_registration(
            || responses.pop_front().expect("unexpected setup call"),
            |_| Ok(()),
            |_| WaitTick::Elapsed,
        )
        .unwrap_err();
        assert!(error.to_string().contains("HTTP 400"), "{error}");
    }

    #[test]
    fn registration_shows_the_link_and_polls_until_active() {
        let (result, log) = register_with(
            vec![
                authorization_setup_response(),
                authorization_setup_response(),
                active_setup_response(),
            ],
            None,
        );

        result.unwrap();
        assert_eq!(
            log,
            [
                "setup",
                "show Connect Slack",
                "wait",
                "setup",
                "wait",
                "setup"
            ]
        );
    }

    #[test]
    fn registration_shows_each_new_step_once() {
        let (result, log) = register_with(
            vec![
                authorization_setup_response(),
                pending_approval_setup_response(),
                pending_approval_setup_response(),
                active_setup_response(),
            ],
            None,
        );

        result.unwrap();
        assert_eq!(
            log,
            [
                "setup",
                "show Connect Slack",
                "wait",
                "setup",
                "show Approve Slack",
                "wait",
                "setup",
                "wait",
                "setup"
            ]
        );
    }

    #[test]
    fn registration_stops_waiting_on_ctrl_c() {
        let (result, log) = register_with(
            vec![
                authorization_setup_response(),
                authorization_setup_response(),
            ],
            Some(2),
        );

        assert!(crate::error::is_cancellation(&result.unwrap_err()));
        assert_eq!(
            log,
            ["setup", "show Connect Slack", "wait", "setup", "wait"]
        );
    }

    #[test]
    fn registration_gives_up_after_an_hour_with_guidance() {
        let polls = (ACTION_WAIT_LIMIT.as_secs() / ACTION_POLL_INTERVAL.as_secs()) as usize;
        let (result, log) = register_with(
            (0..=polls)
                .map(|_| authorization_setup_response())
                .collect(),
            None,
        );

        assert_eq!(
            result.unwrap_err().to_string(),
            "Stopped waiting for Slack after an hour. Slack channel setup requires authorization. Authorize the Slack app at https://trigger.example/authorize, then rerun `mda deploy`."
        );
        assert_eq!(
            log.iter().filter(|entry| *entry == "setup").count(),
            polls + 1
        );
        assert_eq!(log.iter().filter(|entry| *entry == "wait").count(), polls);
        assert_eq!(
            log.iter().filter(|entry| entry.starts_with("show")).count(),
            1
        );
    }

    #[test]
    fn registration_propagates_a_failed_setup_call_without_waiting() {
        let mut waits = 0;
        let error = run_registration(
            || Err(anyhow!("Channel setup request failed.")),
            |_| Ok(()),
            |_| {
                waits += 1;
                WaitTick::Elapsed
            },
        )
        .unwrap_err();

        assert_eq!(
            (error.to_string(), waits),
            ("Channel setup request failed.".to_string(), 0)
        );
    }

    #[test]
    fn authorization_action_has_presentation_values() {
        let action = RequiredAction::Authorize {
            message: "Slack channel setup requires authorization.".to_string(),
            url: Url::parse("https://trigger.example/authorize?state=resume-setup").unwrap(),
        };

        assert_eq!(
            (
                action.heading(),
                action.message(),
                action.instruction(),
                action.url().map(Url::as_str),
            ),
            (
                "Connect Slack",
                "Slack channel setup requires authorization.",
                "Authorize LangSmith to set up your Slack app.",
                Some("https://trigger.example/authorize?state=resume-setup"),
            )
        );
    }

    #[test]
    fn approval_action_has_presentation_values() {
        let action = RequiredAction::Approve {
            message: "Slack channel setup is pending workspace approval.".to_string(),
            url: None,
        };

        assert_eq!(
            (
                action.heading(),
                action.message(),
                action.instruction(),
                action.url(),
            ),
            (
                "Approve Slack",
                "Slack channel setup is pending workspace approval.",
                "Install the Slack app. A workspace admin may need to approve it first.",
                None,
            )
        );
    }
}
