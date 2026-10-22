use std::fs;
use std::io::IsTerminal;
use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use console::style;
use reqwest::Url;
use serde::Deserialize;

use super::channel_icon::channel_icon_file;
use super::trigger_icon;
use crate::compile::ChannelRequirements;
use crate::langsmith::{
    self, ChannelSetupRequest, ChannelSetupResponse, ChannelSetupStatus, ChannelSetupTarget,
    SlackChannelApp, SlackChannelOptions, SlackChannelSetup, SlackTriggerMode,
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

    fn prompt_message(&self) -> &str {
        match self {
            Self::Authorize { .. } => {
                "Authorize LangSmith to set up your Slack app using the link below."
            }
            Self::Approve { message, .. } => message,
        }
    }

    fn continue_label(&self) -> &'static str {
        match self {
            Self::Authorize { .. } => "Press Enter once authorization is complete",
            Self::Approve { .. } => "Press Enter once workspace approval is complete",
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

struct ContinueTheme;

impl cliclack::Theme for ContinueTheme {
    fn format_header(&self, _state: &cliclack::ThemeState, _prompt: &str) -> String {
        String::new()
    }

    fn format_select_item(
        &self,
        state: &cliclack::ThemeState,
        selected: bool,
        label: &str,
        _hint: &str,
    ) -> String {
        if !selected || !matches!(state, cliclack::ThemeState::Active) {
            return String::new();
        }

        format!(
            "{}  {} {label}\n",
            style("└").cyan().for_stderr(),
            style("●").cyan().for_stderr()
        )
    }

    fn format_footer_with_message(&self, _state: &cliclack::ThemeState, _message: &str) -> String {
        String::new()
    }
}

struct ResetTheme;

impl Drop for ResetTheme {
    fn drop(&mut self) {
        cliclack::reset_theme();
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
        auto_reply,
        required_permissions,
        required_env,
        provider_config,
    } = serde_json::from_value(value.clone()).context("Slack channel configuration is invalid.")?;
    if !auto_reply {
        bail!(
            "Slack channel configuration requires {}=true.",
            language.ident("autoReply", "auto_reply")
        );
    }
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
        bail!("An agent with channels cannot be deployed with --no-wait.");
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

pub(crate) fn register(
    client: &langsmith::Client,
    prepared: &PreparedTriggerChannel,
    agent_server_url: &str,
    deployment_id: &str,
    assistant_id: &str,
) -> Result<()> {
    let request = build_setup_request(prepared, agent_server_url, deployment_id, assistant_id)?;
    let interactive = std::io::stdin().is_terminal() && std::io::stderr().is_terminal();
    run_registration(
        interactive,
        || client.setup_trigger_channel(&request),
        wait_for_action,
    )
}

// This URL must stay unwrapped and selectable inside the interactive flow.
#[expect(
    clippy::print_stderr,
    reason = "unwrapped interactive authorization URL"
)]
fn wait_for_action(action: &RequiredAction) -> Result<()> {
    cliclack::log::step(format!(
        "{}\n{}",
        action.heading(),
        style(action.prompt_message()).dim().for_stderr()
    ))?;
    let bar = style("│").dim().for_stderr();
    if let Some(url) = action.url() {
        eprintln!("{}", style(url.as_str()).black().bright().for_stderr());
        eprintln!("{bar}");
    }
    cliclack::set_theme(ContinueTheme);
    let _reset_theme = ResetTheme;
    let _: () = cliclack::select("")
        .item((), action.continue_label(), "")
        .interact()?;
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

fn run_registration<S, W>(interactive: bool, mut setup: S, mut wait: W) -> Result<()>
where
    S: FnMut() -> Result<ChannelSetupResponse>,
    W: FnMut(&RequiredAction) -> Result<()>,
{
    loop {
        match classify_setup_response(setup()?)? {
            SetupState::Active => return Ok(()),
            SetupState::ActionRequired(action) if interactive => wait(&action)?,
            SetupState::ActionRequired(action) => bail!("{}", action.retry_guidance()),
        }
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

    fn render_continue_prompt(state: &cliclack::ThemeState, label: &str) -> String {
        let theme = ContinueTheme;
        format!(
            "{}{}{}",
            cliclack::Theme::format_header(&theme, state, ""),
            cliclack::Theme::format_select_item(&theme, state, true, label, ""),
            cliclack::Theme::format_footer_with_message(&theme, state, ""),
        )
    }

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
            "An agent with channels cannot be deployed with --no-wait."
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
    fn prepare_rejects_a_trigger_channel_with_auto_reply_disabled() {
        let mut manifest = requirements().remove("slack").unwrap();
        manifest["autoReply"] = json!(false);
        let requirements = [("slack".to_string(), manifest)].into_iter().collect();

        let error = prepare(&requirements, true).unwrap_err();

        assert!(error.to_string().contains("autoReply=true"));
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

    #[test]
    fn interactive_registration_retries_once_after_waiting() {
        let mut responses =
            vec![authorization_setup_response(), active_setup_response()].into_iter();
        let mut setup_calls = 0;
        let mut wait_calls = 0;

        run_registration(
            true,
            || {
                setup_calls += 1;
                Ok(responses.next().unwrap())
            },
            |_| {
                wait_calls += 1;
                Ok(())
            },
        )
        .unwrap();

        assert_eq!((setup_calls, wait_calls), (2, 1));
    }

    #[test]
    fn interactive_registration_waits_for_each_incomplete_response() {
        let mut responses = vec![
            authorization_setup_response(),
            pending_approval_setup_response(),
            active_setup_response(),
        ]
        .into_iter();
        let mut setup_calls = 0;
        let mut wait_calls = 0;

        run_registration(
            true,
            || {
                setup_calls += 1;
                Ok(responses.next().unwrap())
            },
            |_| {
                wait_calls += 1;
                Ok(())
            },
        )
        .unwrap();

        assert_eq!((setup_calls, wait_calls), (3, 2));
    }

    #[test]
    fn unattended_registration_returns_guidance_without_waiting() {
        let mut setup_calls = 0;
        let mut wait_calls = 0;

        let error = run_registration(
            false,
            || {
                setup_calls += 1;
                Ok(authorization_setup_response())
            },
            |_| {
                wait_calls += 1;
                Ok(())
            },
        )
        .unwrap_err();

        assert_eq!(
            (setup_calls, wait_calls, error.to_string()),
            (
                1,
                0,
                "Slack channel setup requires authorization. Authorize the Slack app at https://trigger.example/authorize, then rerun `mda deploy`.".to_string(),
            )
        );
    }

    #[test]
    fn interactive_registration_propagates_prompt_interruption_without_retrying() {
        let mut setup_calls = 0;

        let error = run_registration(
            true,
            || {
                setup_calls += 1;
                Ok(authorization_setup_response())
            },
            |_| Err(anyhow!("prompt interrupted")),
        )
        .unwrap_err();

        assert_eq!(
            (setup_calls, error.to_string()),
            (1, "prompt interrupted".into())
        );
    }

    #[test]
    fn authorization_action_has_copyable_presentation_values() {
        let action = RequiredAction::Authorize {
            message: "Slack channel setup requires authorization.".to_string(),
            url: Url::parse("https://trigger.example/authorize?state=resume-setup").unwrap(),
        };

        assert_eq!(
            (
                action.heading(),
                action.message(),
                action.prompt_message(),
                action.url().map(Url::as_str),
                action.continue_label(),
            ),
            (
                "Connect Slack",
                "Slack channel setup requires authorization.",
                "Authorize LangSmith to set up your Slack app using the link below.",
                Some("https://trigger.example/authorize?state=resume-setup"),
                "Press Enter once authorization is complete",
            )
        );
    }

    #[test]
    fn approval_action_has_waiting_presentation_values() {
        let action = RequiredAction::Approve {
            message: "Slack channel setup is pending workspace approval.".to_string(),
            url: None,
        };

        assert_eq!(
            (
                action.heading(),
                action.message(),
                action.prompt_message(),
                action.url(),
                action.continue_label(),
            ),
            (
                "Approve Slack",
                "Slack channel setup is pending workspace approval.",
                "Slack channel setup is pending workspace approval.",
                None,
                "Press Enter once workspace approval is complete",
            )
        );
    }

    #[test]
    fn continue_prompt_renders_as_one_action_line_while_active() {
        let rendered = render_continue_prompt(
            &cliclack::ThemeState::Active,
            "Press Enter once authorization is complete",
        );

        assert_eq!(
            console::strip_ansi_codes(&rendered),
            "└  ● Press Enter once authorization is complete\n"
        );
    }

    #[test]
    fn continue_prompt_clears_its_action_line_when_submitted() {
        let rendered = render_continue_prompt(
            &cliclack::ThemeState::Submit,
            "Press Enter once authorization is complete",
        );

        assert!(rendered.is_empty());
    }
}
