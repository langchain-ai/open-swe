//! Which Slack app a Slack channel uses, asked when `mda deploy` registers the
//! channel for the first time, as the GitHub and Linear channels ask for theirs:
//!
//! - one LangSmith creates and manages, in the workspace the user authorized
//!   LangSmith in (hosted LangSmith only);
//! - a new one the user creates from the manifest Trigger Server prepares;
//! - one the user already has, pointed at Trigger Server.
//!
//! The user's own app's bot token and signing secret go to Trigger Server, which
//! keeps them in Agent Auth, so a redeploy asks nothing. `MDA_SLACK_BOT_TOKEN`
//! and `MDA_SLACK_SIGNING_SECRET` supply them instead of the prompts, with or
//! without a terminal.

use std::collections::BTreeMap;

use anyhow::{anyhow, bail, Context, Result};
use cliclack::log;
use reqwest::Url;
use serde_json::Value;

use super::channel_app::{env_app_notice, override_value};
use crate::langsmith::{self, SlackChannelSetup, SlackCustomerApp, SlackManager};

/// CLI-only: a Slack app's bot token and signing secret when deploy cannot ask.
pub(crate) const BOT_TOKEN_ENV: &str = "MDA_SLACK_BOT_TOKEN";
pub(crate) const SIGNING_SECRET_ENV: &str = "MDA_SLACK_SIGNING_SECRET";

const BOT_TOKEN_PREFIX: &str = "xoxb-";

/// Why a new Slack channel cannot get an app without a terminal: LangSmith
/// cannot create one here (`self_hosted`, or not offered), or would first need
/// the user to authorize it in Slack.
pub(crate) fn unattended_message(self_hosted: bool) -> String {
    let managed = if self_hosted {
        ""
    } else {
        " To have LangSmith create and manage the app instead, run `mda deploy` in a terminal once."
    };
    format!(
        "This agent's Slack channel needs a Slack app, and without a terminal deploy cannot ask for it. Create one, or use one you have, then set {BOT_TOKEN_ENV} and {SIGNING_SECRET_ENV} in the shell or .env to its bot token and signing secret and run `mda deploy` again.{managed}"
    )
}

/// Whether a new Slack channel can get an app without a terminal and without
/// the env credentials: only one LangSmith creates in a workspace the user
/// already authorized it in. An unknown `manager` is tried.
pub(crate) fn unattended_problem(
    self_hosted: bool,
    manager: Option<&SlackManager>,
) -> Option<String> {
    match manager {
        _ if self_hosted => Some(unattended_message(true)),
        Some(SlackManager::Unavailable) => Some(unattended_message(true)),
        Some(SlackManager::NotAuthorized) => Some(unattended_message(false)),
        Some(SlackManager::Authorized { .. }) | None => None,
    }
}

/// The Slack app a channel is registered with.
#[derive(Debug)]
pub(crate) enum SlackApp {
    /// One LangSmith creates and manages.
    Managed,
    /// One the user owns.
    Customer(SlackCustomerApp),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AppSource {
    Managed,
    Manifest,
    Existing,
}

/// One answer to "which Slack app?".
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppOption {
    pub(crate) source: AppSource,
    pub(crate) label: String,
    pub(crate) hint: &'static str,
}

/// What choosing an app needs from LangSmith and the terminal.
pub(crate) trait ChoiceEnv {
    fn interactive(&self) -> bool;
    fn self_hosted(&self) -> bool;
    fn credentials_from_env(&mut self) -> Result<Option<SlackCustomerApp>>;
    /// Whether LangSmith can create the app, and where; `None` when unknown.
    fn manager(&mut self) -> Option<SlackManager>;
    fn choose(&mut self, options: &[AppOption]) -> Result<AppSource>;
    fn info(&mut self, message: &str) -> Result<()>;
    fn manifest(&mut self) -> Result<Value>;
    fn show_create_page(&mut self, link: &Url) -> Result<()>;
    fn show_existing_settings(&mut self, settings: &str) -> Result<()>;
    fn ask_credentials(&mut self) -> Result<SlackCustomerApp>;
}

/// Which Slack app to register the channel with.
pub(crate) fn choose_app(env: &mut impl ChoiceEnv) -> Result<SlackApp> {
    if let Some(credentials) = env.credentials_from_env()? {
        return Ok(SlackApp::Customer(credentials));
    }
    let self_hosted = env.self_hosted();
    let manager = if self_hosted { None } else { env.manager() };
    if !env.interactive() {
        if let Some(problem) = unattended_problem(self_hosted, manager.as_ref()) {
            bail!(problem);
        }
        let workspace = match &manager {
            Some(SlackManager::Authorized { team_name }) => format!(" in {team_name}"),
            _ => String::new(),
        };
        env.info(&format!(
            "Without a terminal, LangSmith creates and manages this agent's Slack app{workspace}. To use your own app instead, set {BOT_TOKEN_ENV} and {SIGNING_SECRET_ENV}."
        ))?;
        return Ok(SlackApp::Managed);
    }
    let options = app_options(self_hosted, manager.as_ref());
    match env.choose(&options)? {
        AppSource::Managed => Ok(SlackApp::Managed),
        AppSource::Manifest => {
            let link = manifest_link(&env.manifest()?)?;
            env.show_create_page(&link)?;
            Ok(SlackApp::Customer(env.ask_credentials()?))
        }
        AppSource::Existing => {
            let settings = existing_app_settings(&env.manifest()?)?;
            env.show_existing_settings(&settings)?;
            Ok(SlackApp::Customer(env.ask_credentials()?))
        }
    }
}

/// The answers deploy offers: LangSmith only creates apps on hosted LangSmith,
/// and names the workspace when the user already authorized it in one.
pub(crate) fn app_options(self_hosted: bool, manager: Option<&SlackManager>) -> Vec<AppOption> {
    let option = |source, label: &str, hint| AppOption {
        source,
        label: label.to_string(),
        hint,
    };
    let existing = option(
        AppSource::Existing,
        "Use an existing Slack app",
        "enter its bot token and signing secret",
    );
    if self_hosted || manager == Some(&SlackManager::Unavailable) {
        return vec![
            option(
                AppSource::Manifest,
                "Create a new Slack app",
                "recommended: from a manifest LangSmith prepares",
            ),
            existing,
        ];
    }
    if let Some(SlackManager::Authorized { team_name }) = manager {
        return vec![
            option(
                AppSource::Managed,
                &format!("Create a new Slack app in {team_name}"),
                "recommended: LangSmith creates and manages it",
            ),
            existing,
        ];
    }
    vec![
        option(
            AppSource::Managed,
            "Let LangSmith create and manage a new Slack app",
            "recommended: authorize LangSmith in Slack first",
        ),
        option(
            AppSource::Manifest,
            "Create a new Slack app yourself",
            "from a manifest LangSmith prepares",
        ),
        existing,
    ]
}

/// The bot token and signing secret from the CLI-only env vars: both, or
/// neither.
pub(crate) fn credentials_env(
    env_vars: &BTreeMap<String, String>,
) -> Result<Option<SlackCustomerApp>> {
    let value = |key| override_value(key, env_vars).map(|value| value.trim().to_string());
    match (value(BOT_TOKEN_ENV), value(SIGNING_SECRET_ENV)) {
        (Some(bot_token), Some(signing_secret)) => {
            validate_bot_token(&bot_token)
                .map_err(|problem| anyhow!("{BOT_TOKEN_ENV} is not valid: {problem}"))?;
            validate_signing_secret(&signing_secret)
                .map_err(|problem| anyhow!("{SIGNING_SECRET_ENV} is not valid: {problem}"))?;
            Ok(Some(SlackCustomerApp {
                bot_token,
                signing_secret,
                move_from_deployment: None,
            }))
        }
        (None, None) => Ok(None),
        _ => bail!("Set both {BOT_TOKEN_ENV} and {SIGNING_SECRET_ENV}, or neither."),
    }
}

/// Slack's link that creates an app from `manifest`.
pub(crate) fn manifest_link(manifest: &Value) -> Result<Url> {
    let mut url = Url::parse("https://api.slack.com/apps")?;
    url.query_pairs_mut()
        .append_pair("new_app", "1")
        .append_pair(
            "manifest_json",
            &serde_json::to_string(manifest).context("Failed to serialize the Slack manifest.")?,
        );
    Ok(url)
}

/// Where the user changes an existing Slack app's settings.
const SLACK_APPS_URL: &str = "https://api.slack.com/apps";

/// Width a wrapped list of events or scopes fits in, after its indent.
const SETTINGS_LIST_WIDTH: usize = 72;
/// Indent of a step's detail lines, under the step's text.
const PAD: &str = "     ";
/// Indent of a URL or list inside a step.
const INDENT: &str = "       ";

/// What an existing app must be set to for Trigger Server to receive its events,
/// from the manifest Trigger Server would create the app with, as numbered
/// steps in the order of the app's settings sidebar. Trigger Server cannot
/// change an app it doesn't manage, so the user sets these in Slack.
pub(crate) fn existing_app_settings(manifest: &Value) -> Result<String> {
    let text = |pointer: &str| {
        manifest
            .pointer(pointer)
            .and_then(Value::as_str)
            .filter(|value| !value.chars().any(char::is_control))
    };
    let list = |pointer: &str| {
        manifest
            .pointer(pointer)
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .filter(|item| !item.chars().any(char::is_control))
                    .collect::<Vec<_>>()
            })
            .filter(|items| !items.is_empty())
    };
    let incomplete = || anyhow!("Trigger Server returned an incomplete Slack app manifest.");
    let events_url = text("/settings/event_subscriptions/request_url").ok_or_else(incomplete)?;
    let events = list("/settings/event_subscriptions/bot_events").ok_or_else(incomplete)?;
    let interactivity_url = text("/settings/interactivity/request_url").ok_or_else(incomplete)?;
    let scopes = list("/oauth_config/scopes/bot").ok_or_else(incomplete)?;
    let enabled = |pointer: &str| manifest.pointer(pointer).and_then(Value::as_bool) == Some(true);

    let mut steps = vec![
        "Socket Mode: turn it off if it is on. Slack sends events to a URL only without it."
            .to_string(),
        format!(
            "Event Subscriptions: turn on Enable Events and set the Request URL to\n\
             {INDENT}{events_url}\n\
             {PAD}Under Subscribe to bot events, add\n{}",
            wrap_list(&events, INDENT)
        ),
        format!(
            "Interactivity & Shortcuts: turn on Interactivity and set the Request URL to\n\
             {INDENT}{interactivity_url}"
        ),
    ];
    if enabled("/features/app_home/messages_tab_enabled") {
        steps.push(
            "App Home: turn on the Messages Tab and allow users to send messages from it, so \
             people can DM the agent."
                .to_string(),
        );
    }
    if manifest.pointer("/features/agent_view").is_some() {
        steps.push(
            "Agents & AI Apps: turn it on, so Slack shows the agent's progress and a Stop button."
                .to_string(),
        );
    }
    steps.push(format!(
        "OAuth & Permissions: under Bot Token Scopes, add\n{}\n\
         {PAD}Then click Reinstall to Workspace at the top of the page.",
        wrap_list(&scopes, INDENT)
    ));

    let mut message = format!(
        "Set up your Slack app at {SLACK_APPS_URL}: pick the app, then open each page below from \
         its sidebar.\n"
    );
    for (number, step) in steps.iter().enumerate() {
        message.push_str(&format!("\n  {}. {step}", number + 1));
    }
    message.push_str(
        "\n\nThen copy the app's Bot User OAuth Token (OAuth & Permissions, starts with xoxb-) \
         and its Signing Secret (Basic Information, under App Credentials) into the prompts \
         below. Slack sends an app's events to one URL, so anything else that receives this \
         app's events now stops receiving them.",
    );
    Ok(message)
}

/// `items` joined by commas, wrapped to lines that start with `indent`.
fn wrap_list(items: &[&str], indent: &str) -> String {
    let mut lines = vec![String::new()];
    for (index, item) in items.iter().enumerate() {
        let item = if index + 1 < items.len() {
            format!("{item},")
        } else {
            (*item).to_string()
        };
        let line = lines.last_mut().expect("lines starts non-empty");
        if !line.is_empty() && line.len() + 1 + item.len() > SETTINGS_LIST_WIDTH {
            lines.push(item);
        } else {
            if !line.is_empty() {
                line.push(' ');
            }
            line.push_str(&item);
        }
    }
    lines
        .iter()
        .map(|line| format!("{indent}{line}"))
        .collect::<Vec<_>>()
        .join("\n")
}

fn validate_bot_token(value: &str) -> std::result::Result<(), &'static str> {
    let value = value.trim();
    if value.is_empty() {
        return Err("enter the app's Bot User OAuth Token, under OAuth & Permissions");
    }
    if !value.starts_with(BOT_TOKEN_PREFIX) || value.chars().any(char::is_whitespace) {
        return Err("a bot token starts with xoxb-");
    }
    Ok(())
}

fn validate_signing_secret(value: &str) -> std::result::Result<(), &'static str> {
    let value = value.trim();
    if value.is_empty() || value.chars().any(char::is_whitespace) {
        return Err("enter the app's Signing Secret, under Basic Information");
    }
    Ok(())
}

/// The choice as `mda deploy` asks it.
pub(crate) struct LiveChoice<'a> {
    client: &'a langsmith::Client,
    slack: &'a SlackChannelSetup,
    env_vars: &'a BTreeMap<String, String>,
    interactive: bool,
}

impl<'a> LiveChoice<'a> {
    pub(crate) fn new(
        client: &'a langsmith::Client,
        slack: &'a SlackChannelSetup,
        env_vars: &'a BTreeMap<String, String>,
        interactive: bool,
    ) -> Self {
        Self {
            client,
            slack,
            env_vars,
            interactive,
        }
    }
}

impl ChoiceEnv for LiveChoice<'_> {
    fn interactive(&self) -> bool {
        self.interactive
    }

    fn self_hosted(&self) -> bool {
        self.client.is_self_hosted()
    }

    fn credentials_from_env(&mut self) -> Result<Option<SlackCustomerApp>> {
        let credentials = credentials_env(self.env_vars)?;
        if credentials.is_some() {
            log::info(env_app_notice(
                "Slack app",
                "LangSmith",
                &[BOT_TOKEN_ENV, SIGNING_SECRET_ENV],
            ))?;
        }
        Ok(credentials)
    }

    fn manager(&mut self) -> Option<SlackManager> {
        // Not knowing only drops the workspace's name from the choice.
        self.client.slack_manager().unwrap_or_else(|error| {
            let _ = log::warning(format!(
                "Could not check whether LangSmith can create Slack apps for you: {error}"
            ));
            None
        })
    }

    fn choose(&mut self, options: &[AppOption]) -> Result<AppSource> {
        let mut prompt = cliclack::select("This agent's Slack channel needs a Slack app");
        for option in options {
            prompt = prompt.item(option.source, &option.label, option.hint);
        }
        prompt.interact().context("failed to choose a Slack app")
    }

    fn info(&mut self, message: &str) -> Result<()> {
        Ok(log::info(message)?)
    }

    fn manifest(&mut self) -> Result<Value> {
        self.client
            .slack_app_manifest(&self.slack.app, &self.slack.options)
    }

    fn show_create_page(&mut self, link: &Url) -> Result<()> {
        log::step(format!(
            "Create the Slack app for this agent in your browser: Slack opens with the app filled in. If your browser does not open, visit:\n{link}\nInstall the app to your workspace, then copy its Bot User OAuth Token (OAuth & Permissions) and Signing Secret (Basic Information) into the prompts below."
        ))?;
        crate::langsmith::login::open_browser(link.as_str());
        Ok(())
    }

    fn show_existing_settings(&mut self, settings: &str) -> Result<()> {
        log::step(settings)?;
        crate::langsmith::login::open_browser(SLACK_APPS_URL);
        Ok(())
    }

    fn ask_credentials(&mut self) -> Result<SlackCustomerApp> {
        let bot_token: String = cliclack::password("Bot User OAuth Token (xoxb-…)")
            .mask('•')
            .validate(|value: &String| validate_bot_token(value))
            .interact()
            .context("failed to read the Slack bot token")?;
        let signing_secret: String = cliclack::password("Signing Secret")
            .mask('•')
            .validate(|value: &String| validate_signing_secret(value))
            .interact()
            .context("failed to read the Slack signing secret")?;
        Ok(SlackCustomerApp {
            bot_token: bot_token.trim().to_string(),
            signing_secret: signing_secret.trim().to_string(),
            move_from_deployment: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[derive(Default)]
    struct FakeChoice {
        interactive: bool,
        self_hosted: bool,
        env_credentials: Option<SlackCustomerApp>,
        manager: Option<SlackManager>,
        answer: Option<AppSource>,
        offered: Vec<AppOption>,
        create_page: Option<String>,
        existing_settings: Option<String>,
        asked_credentials: bool,
        asked_manager: bool,
        notes: Vec<String>,
    }

    fn credentials() -> SlackCustomerApp {
        SlackCustomerApp {
            bot_token: "xoxb-test".to_string(),
            signing_secret: "signing-test".to_string(),
            move_from_deployment: None,
        }
    }

    fn manifest() -> Value {
        json!({
            "display_information": {"name": "Support"},
            "oauth_config": {"scopes": {"bot": ["chat:write", "channels:history"]}},
            "settings": {
                "event_subscriptions": {
                    "request_url": "https://triggers.example/v1/triggers/webhooks/template",
                    "bot_events": ["app_mention", "message.im"]
                },
                "interactivity": {
                    "is_enabled": true,
                    "request_url": "https://triggers.example/v1/triggers/webhooks/slack-custom-app/interactivity"
                }
            }
        })
    }

    impl ChoiceEnv for FakeChoice {
        fn interactive(&self) -> bool {
            self.interactive
        }

        fn self_hosted(&self) -> bool {
            self.self_hosted
        }

        fn credentials_from_env(&mut self) -> Result<Option<SlackCustomerApp>> {
            Ok(self.env_credentials.clone())
        }

        fn manager(&mut self) -> Option<SlackManager> {
            self.asked_manager = true;
            self.manager.clone()
        }

        fn choose(&mut self, options: &[AppOption]) -> Result<AppSource> {
            self.offered = options.to_vec();
            self.answer.ok_or_else(|| anyhow!("no answer"))
        }

        fn info(&mut self, message: &str) -> Result<()> {
            self.notes.push(message.to_string());
            Ok(())
        }

        fn manifest(&mut self) -> Result<Value> {
            Ok(manifest())
        }

        fn show_create_page(&mut self, link: &Url) -> Result<()> {
            self.create_page = Some(link.to_string());
            Ok(())
        }

        fn show_existing_settings(&mut self, settings: &str) -> Result<()> {
            self.existing_settings = Some(settings.to_string());
            Ok(())
        }

        fn ask_credentials(&mut self) -> Result<SlackCustomerApp> {
            self.asked_credentials = true;
            Ok(credentials())
        }
    }

    fn sources(options: &[AppOption]) -> Vec<AppSource> {
        options.iter().map(|option| option.source).collect()
    }

    #[test]
    fn env_credentials_need_no_question() {
        let mut env = FakeChoice {
            interactive: true,
            env_credentials: Some(credentials()),
            ..FakeChoice::default()
        };

        let app = choose_app(&mut env).unwrap();

        assert!(matches!(app, SlackApp::Customer(app) if app.bot_token == "xoxb-test"));
        assert!(env.offered.is_empty());
        assert!(!env.asked_manager);
    }

    #[test]
    fn unattended_hosted_deploy_creates_a_managed_app_in_an_authorized_workspace() {
        for manager in [
            Some(SlackManager::Authorized {
                team_name: "Acme".to_string(),
            }),
            None,
        ] {
            let mut env = FakeChoice {
                manager,
                ..FakeChoice::default()
            };

            assert!(matches!(choose_app(&mut env).unwrap(), SlackApp::Managed));
            assert!(env.asked_manager);
            // Not silently: it says so, and how to use your own app.
            assert_eq!(env.notes.len(), 1);
            assert!(env.notes[0].contains(BOT_TOKEN_ENV), "{:?}", env.notes);
        }
    }

    #[test]
    fn unattended_hosted_deploy_that_needs_authorization_fails_with_the_env_hint() {
        for manager in [SlackManager::NotAuthorized, SlackManager::Unavailable] {
            let mut env = FakeChoice {
                manager: Some(manager),
                ..FakeChoice::default()
            };

            let error = choose_app(&mut env).unwrap_err().to_string();

            assert!(error.contains(BOT_TOKEN_ENV), "{error}");
            assert!(error.contains("in the shell or .env"), "{error}");
            assert!(error.contains("run `mda deploy` again"), "{error}");
        }
        assert!(
            unattended_problem(false, Some(&SlackManager::NotAuthorized))
                .unwrap()
                .contains("in a terminal once")
        );
        assert!(!unattended_problem(false, Some(&SlackManager::Unavailable))
            .unwrap()
            .contains("in a terminal once"));
    }

    #[test]
    fn unattended_self_hosted_deploy_needs_the_env_credentials() {
        let mut env = FakeChoice {
            self_hosted: true,
            ..FakeChoice::default()
        };

        let error = choose_app(&mut env).unwrap_err().to_string();

        assert!(error.contains(BOT_TOKEN_ENV));
        assert!(error.contains(SIGNING_SECRET_ENV));
    }

    #[test]
    fn hosted_with_an_authorized_workspace_offers_it() {
        let mut env = FakeChoice {
            interactive: true,
            manager: Some(SlackManager::Authorized {
                team_name: "Acme".to_string(),
            }),
            answer: Some(AppSource::Managed),
            ..FakeChoice::default()
        };

        assert!(matches!(choose_app(&mut env).unwrap(), SlackApp::Managed));
        assert_eq!(
            sources(&env.offered),
            [AppSource::Managed, AppSource::Existing]
        );
        assert_eq!(env.offered[0].label, "Create a new Slack app in Acme");
        assert!(!env.asked_credentials);
    }

    #[test]
    fn hosted_without_authorization_offers_all_three() {
        for manager in [Some(SlackManager::NotAuthorized), None] {
            let options = app_options(false, manager.as_ref());

            assert_eq!(
                sources(&options),
                [AppSource::Managed, AppSource::Manifest, AppSource::Existing]
            );
            assert_eq!(
                options[0].label,
                "Let LangSmith create and manage a new Slack app"
            );
        }
    }

    #[test]
    fn without_app_management_only_the_users_own_app_is_offered() {
        for (self_hosted, manager) in [(true, None), (false, Some(SlackManager::Unavailable))] {
            let options = app_options(self_hosted, manager.as_ref());

            assert_eq!(
                sources(&options),
                [AppSource::Manifest, AppSource::Existing]
            );
        }
    }

    #[test]
    fn self_hosted_never_asks_about_app_management() {
        let mut env = FakeChoice {
            interactive: true,
            self_hosted: true,
            answer: Some(AppSource::Existing),
            ..FakeChoice::default()
        };

        choose_app(&mut env).unwrap();

        assert!(!env.asked_manager);
    }

    #[test]
    fn a_new_app_opens_slack_with_the_manifest_then_asks_its_credentials() {
        let mut env = FakeChoice {
            interactive: true,
            self_hosted: true,
            answer: Some(AppSource::Manifest),
            ..FakeChoice::default()
        };

        let app = choose_app(&mut env).unwrap();

        assert!(matches!(app, SlackApp::Customer(_)));
        let link = Url::parse(env.create_page.as_deref().unwrap()).unwrap();
        let manifest_json = link
            .query_pairs()
            .find(|(key, _)| key == "manifest_json")
            .unwrap()
            .1
            .into_owned();
        assert_eq!(
            serde_json::from_str::<Value>(&manifest_json).unwrap(),
            manifest()
        );
        assert!(env.asked_credentials);
    }

    #[test]
    fn an_existing_app_is_told_where_to_point_then_asked_its_credentials() {
        let mut env = FakeChoice {
            interactive: true,
            answer: Some(AppSource::Existing),
            ..FakeChoice::default()
        };

        assert!(matches!(
            choose_app(&mut env).unwrap(),
            SlackApp::Customer(_)
        ));
        let settings = env.existing_settings.unwrap();
        assert!(settings.contains(
            "Request URL to\n       https://triggers.example/v1/triggers/webhooks/template\n"
        ));
        assert!(settings.contains("bot events, add\n       app_mention, message.im\n"));
        assert!(settings.contains(
            "Request URL to\n       https://triggers.example/v1/triggers/webhooks/slack-custom-app/interactivity\n"
        ));
        assert!(settings.contains("Bot Token Scopes, add\n       chat:write, channels:history\n"));
        assert!(settings.contains("  1. Socket Mode"));
        assert!(!settings.contains("App Home"));
        assert!(!settings.contains("Agents & AI Apps"));
        assert!(env.asked_credentials);
    }

    #[test]
    fn existing_app_settings_cover_dms_and_agent_view_when_the_manifest_asks() {
        let mut manifest = manifest();
        manifest["features"] = json!({
            "app_home": {"messages_tab_enabled": true},
            "agent_view": {"agent_description": "Support"}
        });

        let settings = existing_app_settings(&manifest).unwrap();

        assert!(settings.contains("  4. App Home: turn on the Messages Tab"));
        assert!(settings.contains("  5. Agents & AI Apps: turn it on"));
        assert!(settings.contains("  6. OAuth & Permissions"));
    }

    #[test]
    fn settings_lists_wrap_on_whole_items() {
        let scopes = ["channels:history"; 8];

        let wrapped = wrap_list(&scopes, "  ");

        let lines: Vec<_> = wrapped.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines
            .iter()
            .all(|line| line.starts_with("  channels:history")
                && line.len() <= 2 + SETTINGS_LIST_WIDTH));
        assert!(lines[1].ends_with("channels:history"));
    }

    #[test]
    fn existing_app_settings_need_a_complete_manifest() {
        let mut incomplete = manifest();
        incomplete["settings"]
            .as_object_mut()
            .unwrap()
            .remove("interactivity");

        assert!(existing_app_settings(&incomplete).is_err());
    }

    #[test]
    fn env_credentials_come_in_pairs_and_never_echo_values() {
        let env = |pairs: &[(&str, &str)]| {
            pairs
                .iter()
                .map(|(key, value)| (key.to_string(), value.to_string()))
                .collect::<BTreeMap<_, _>>()
        };

        assert!(credentials_env(&env(&[])).unwrap().is_none());
        let only_token = credentials_env(&env(&[(BOT_TOKEN_ENV, "xoxb-secret-token")]))
            .unwrap_err()
            .to_string();
        assert!(only_token.contains(SIGNING_SECRET_ENV));
        assert!(!only_token.contains("xoxb-secret-token"));
        let user_token = credentials_env(&env(&[
            (BOT_TOKEN_ENV, "xoxp-user-token"),
            (SIGNING_SECRET_ENV, "signing"),
        ]))
        .unwrap_err()
        .to_string();
        assert!(user_token.contains("xoxb-"));
        assert!(!user_token.contains("xoxp-user-token"));
        let both = credentials_env(&env(&[
            (BOT_TOKEN_ENV, " xoxb-token "),
            (SIGNING_SECRET_ENV, "signing"),
        ]))
        .unwrap()
        .unwrap();
        assert_eq!(both.bot_token, "xoxb-token");
        assert_eq!(both.signing_secret, "signing");
    }

    #[test]
    fn credentials_never_print() {
        let printed = format!("{:?}", credentials());

        assert!(!printed.contains("xoxb-test"));
        assert!(!printed.contains("signing-test"));
    }
}
