//! What the channels that set up an app during `mda deploy` share: GitHub
//! ([`super::github_channel`]) and Linear ([`super::linear_channel`]), which
//! keep theirs in Agent Auth, and Slack ([`super::trigger_channels`]), which
//! Trigger Server keeps.
//!
//! - the Agent Auth client, with the hidden overrides that point only its
//!   calls at another LangSmith;
//! - CLI-only settings from the project's `.env` or the shell;
//! - the webhook URL an app sends the deployment's events to;
//! - the slugs of the app's connection and the secrets stored next to it;
//! - the loop that offers to try a failed setup step again, or to continue
//!   the deploy without the channel;
//! - finding the provider's one channel in the project and the manifest checks
//!   every app channel shares.

use std::collections::BTreeMap;
use std::io::IsTerminal;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use cliclack::log;

use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::Value;

use crate::compile::ChannelRequirements;
use crate::deploy::helpers::validated_agent_server_url;
use crate::error::is_cancellation;
use crate::interrupt::Interrupt;
use crate::langsmith::{self, ConnectionSummary};

/// Hidden overrides that send only Agent Auth calls to another LangSmith,
/// e.g. a local Agent Auth while the deployment itself runs in the cloud.
pub(crate) const AGENT_AUTH_URL_ENV: &str = "MDA_AGENT_AUTH_URL";
const AGENT_AUTH_API_KEY_ENV: &str = "MDA_AGENT_AUTH_API_KEY";
const AGENT_AUTH_TENANT_ENV: &str = "MDA_AGENT_AUTH_TENANT_ID";

/// Where Agent Auth's OAuth callback is, when it is not where the CLI derives
/// it from the LangSmith URL. The local stack sets it (`mda-local env`), since
/// its Agent Auth is configured with a callback the CLI cannot know.
pub(crate) const OAUTH_CALLBACK_ENV: &str = "MDA_AGENT_AUTH_OAUTH_CALLBACK_URL";

/// The secret connection next to an app's, holding its webhook secret.
pub(crate) const WEBHOOK_SECRET_SUFFIX: &str = "/webhook-secret";

/// A channel manifest of an app channel; `C` is the provider's config.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct AppChannelManifest<C> {
    pub(crate) name: String,
    #[expect(dead_code, reason = "selected by provider before deserializing")]
    provider: String,
    #[serde(default)]
    transport: Option<String>,
    #[serde(default)]
    pub(crate) auto_reply: bool,
    pub(crate) events_path: String,
    #[serde(default)]
    required_permissions: Vec<String>,
    #[serde(default)]
    required_env: Vec<String>,
    #[serde(default)]
    #[expect(
        dead_code,
        reason = "part of the channel manifest schema; unused by app setup"
    )]
    user_connections: Option<Value>,
    #[serde(default)]
    #[expect(
        dead_code,
        reason = "part of the channel manifest schema; read by connection checks"
    )]
    agent_connections: Option<Value>,
    pub(crate) provider_config: C,
}

/// The provider's channel, if the project declares one, after the checks every
/// app channel shares: there is only one, it is named by its key, it receives
/// events directly on an absolute path, and declares no permissions or env.
/// `label` names the provider in errors, such as `GitHub`.
pub(crate) fn app_channel_manifest<C: DeserializeOwned>(
    requirements: &ChannelRequirements,
    provider: &str,
    label: &str,
) -> Result<Option<AppChannelManifest<C>>> {
    let channels: Vec<(&String, &Value)> = requirements
        .iter()
        .filter(|(_, manifest)| manifest.get("provider").and_then(Value::as_str) == Some(provider))
        .collect();
    let (key, value) = match channels.as_slice() {
        [] => return Ok(None),
        [single] => *single,
        many => {
            let names: Vec<&str> = many.iter().map(|(name, _)| name.as_str()).collect();
            bail!(
                "An agent can have only one {label} channel (found: {}).",
                names.join(", ")
            );
        }
    };
    let manifest: AppChannelManifest<C> = serde_json::from_value(value.clone())
        .with_context(|| format!("{label} channel configuration is invalid."))?;
    if &manifest.name != key {
        bail!("Channel name must match its manifest key.");
    }
    if manifest.transport.is_some() {
        bail!("{label} channels receive events directly and must not set a transport.");
    }
    if !manifest.required_permissions.is_empty() || !manifest.required_env.is_empty() {
        bail!("{label} channel configuration must not declare requiredPermissions or requiredEnv.");
    }
    let path = &manifest.events_path;
    if !path.starts_with('/') || path.contains("://") || path.contains('?') {
        bail!("{label} channel events path must be an absolute path on the Agent Server.");
    }
    Ok(Some(manifest))
}

/// Whether deploy can ask questions: both a terminal to read from and one to
/// show prompts on.
pub(crate) fn is_interactive() -> bool {
    std::io::stdin().is_terminal() && std::io::stderr().is_terminal()
}

/// A CLI-only setting from the shell, else from the project's `.env`, like
/// other CLI settings. The deploy never uploads these as secrets (see
/// `RESERVED_ENV_VARS`).
pub(crate) fn override_value(key: &str, env_vars: &BTreeMap<String, String>) -> Option<String> {
    langsmith::lookup_env_or_process(key, env_vars)
}

/// The client for Agent Auth calls, honoring the hidden overrides in the shell
/// or the project's `.env`.
pub(crate) fn agent_auth(
    client: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
) -> Result<langsmith::Client> {
    agent_auth_client(client, |key| override_value(key, env_vars))
}

/// The client for Agent Auth calls: the deploy client, unless the hidden
/// `MDA_AGENT_AUTH_URL` override points them at another LangSmith.
pub(crate) fn agent_auth_client(
    client: &langsmith::Client,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<langsmith::Client> {
    let Some(url) = lookup(AGENT_AUTH_URL_ENV) else {
        return Ok(client.clone());
    };
    let api_key = lookup(AGENT_AUTH_API_KEY_ENV).with_context(|| {
        format!("{AGENT_AUTH_URL_ENV} is set, so {AGENT_AUTH_API_KEY_ENV} must be set too")
    })?;
    let tenant = lookup(AGENT_AUTH_TENANT_ENV);
    langsmith::Client::new(&url, &url, langsmith::ApiKey::new(api_key), tenant)
}

/// Where Agent Auth's OAuth callback is, for an app that must list it before
/// Agent Auth starts its first authorization: [`OAUTH_CALLBACK_ENV`] when
/// set, else what Agent Auth publishes, else the callback on the LangSmith API
/// Agent Auth is reached at. The install session later names the redirect URI
/// Agent Auth really uses, and setup checks it then.
pub(crate) fn oauth_callback_url(
    agent_auth: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
) -> Result<String> {
    if let Some(url) = configured_oauth_callback(override_value(OAUTH_CALLBACK_ENV, env_vars))? {
        return Ok(url);
    }
    // Only a 404 (`Ok(None)`) means "not published": an older Agent Auth, or
    // a callback it does not publish (not HTTPS). Any other failure, such as
    // a network error, a 5xx or a rejected API key, says nothing about the
    // callback, and guessing it would break the app's OAuth install later.
    let published = agent_auth
        .published_oauth_redirect_uri()
        .context("failed to read Agent Auth's OAuth callback from its client metadata")?;
    match published {
        Some(url) => Ok(url),
        None => agent_auth.oauth_redirect_uri(),
    }
}

/// [`OAUTH_CALLBACK_ENV`], checked.
fn configured_oauth_callback(configured: Option<String>) -> Result<Option<String>> {
    let Some(url) = configured else {
        return Ok(None);
    };
    let valid = reqwest::Url::parse(&url)
        .is_ok_and(|parsed| matches!(parsed.scheme(), "http" | "https") && parsed.has_host());
    if !valid {
        anyhow::bail!("{OAUTH_CALLBACK_ENV} must be an http(s) URL");
    }
    Ok(Some(url))
}

/// `a`, `a and b`, `a, b and c`.
pub(crate) fn join_names(names: &[&str]) -> String {
    match names {
        [] => String::new(),
        [only] => (*only).to_string(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// What deploy says when a channel's app comes from CLI-only env vars: which
/// ones, and that every one of them can go once `store` keeps the app.
pub(crate) fn env_app_notice(app: &str, store: &str, vars: &[&str]) -> String {
    let names = join_names(vars);
    format!(
        "Using the {app} from {names}. {store} keeps its credentials, so you can remove {names} afterwards."
    )
}

/// Validated `https://` Agent Server URL plus the channel's events path.
pub(crate) fn webhook_url(agent_server_url: &str, events_path: &str) -> Result<String> {
    validated_agent_server_url(agent_server_url)?;
    Ok(format!(
        "{}{events_path}",
        agent_server_url.trim_end_matches('/')
    ))
}

/// The slugs of a channel's app connection, one per deployment, and of the
/// secrets stored next to it.
#[derive(Debug, Clone, Copy)]
pub(crate) struct AppSlugs {
    /// What every deployment's app connection slug starts with, e.g.
    /// `mda/github-app/`.
    prefix: &'static str,
}

impl AppSlugs {
    pub(crate) const fn new(prefix: &'static str) -> Self {
        Self { prefix }
    }

    /// The connection that holds the deployment's app.
    pub(crate) fn connection(self, deployment_id: &str) -> String {
        format!("{}{deployment_id}", self.prefix)
    }

    /// A value stored next to the app's connection, e.g.
    /// [`WEBHOOK_SECRET_SUFFIX`].
    pub(crate) fn secret(self, deployment_id: &str, suffix: &str) -> String {
        format!("{}{suffix}", self.connection(deployment_id))
    }

    /// The deployment an app connection slug belongs to.
    pub(crate) fn deployment_of(self, slug: &str) -> Option<&str> {
        slug.strip_prefix(self.prefix)
            .filter(|deployment_id| !deployment_id.is_empty() && !deployment_id.contains('/'))
    }
}

/// The listed connection with `slug`.
pub(crate) fn find_connection<'a>(
    listed: &'a [ConnectionSummary],
    slug: &str,
) -> Option<&'a ConnectionSummary> {
    listed.iter().find(|connection| connection.slug == slug)
}

/// What a channel's setup shows, and how it asks what to do after a failed
/// step. Each channel's `SetupEnv` builds on it, so its flow stays testable.
pub(crate) trait SetupUi {
    fn interactive(&self) -> bool;
    /// A setup step failed: show why and ask whether to try again (`true`)
    /// or continue the deploy without the channel (`false`).
    fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool>;
    fn success(&mut self, message: &str) -> Result<()>;
    fn info(&mut self, message: &str) -> Result<()>;
    fn warning(&mut self, message: &str) -> Result<()>;
}

/// Run `attempt` until it succeeds. The deployment is already live, so a
/// failed attempt offers to try again, from what `refresh` finds then, or to
/// continue without the channel: `None`, after warning `skipped`. Without a
/// terminal nobody can choose, so the error ends the deploy.
pub(crate) fn retry_or_skip<E, C, T>(
    env: &mut E,
    mut current: C,
    skipped: &str,
    mut attempt: impl FnMut(&mut E, C) -> Result<T>,
    mut refresh: impl FnMut(&mut E) -> Result<C>,
) -> Result<Option<T>>
where
    E: SetupUi + ?Sized,
{
    loop {
        match attempt(env, current) {
            Ok(done) => return Ok(Some(done)),
            Err(error) if !env.interactive() => return Err(error),
            Err(error) => {
                if is_cancellation(&error) || !env.retry_or_skip(&error)? {
                    env.warning(skipped)?;
                    return Ok(None);
                }
                current = refresh(env)?;
            }
        }
    }
}

/// How a wait while setup polls for the user's action ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WaitTick {
    Elapsed,
    Interrupted,
}

/// Sleep for `duration`, or until Ctrl-C.
pub(crate) fn wait(duration: Duration) -> WaitTick {
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

/// [`SetupUi::retry_or_skip`] in a terminal: `setup` names what did not
/// finish ("GitHub App setup"), `channel` the channel ("GitHub").
pub(crate) fn ask_retry_or_skip(setup: &str, channel: &str, error: &anyhow::Error) -> Result<bool> {
    log::warning(format!("{setup} did not finish: {error:#}"))?;
    let retry = cliclack::select("How do you want to continue?")
        .item(true, "Try again", "")
        .item(
            false,
            format!("Continue without the {channel} channel"),
            "set it up on the next deploy",
        )
        .interact();
    // Escape or Ctrl-C at this prompt means "skip".
    Ok(retry.unwrap_or(false))
}

/// [`SetupUi`]'s messages in a terminal.
pub(crate) fn show_success(message: &str) -> Result<()> {
    Ok(log::success(message)?)
}

pub(crate) fn show_info(message: &str) -> Result<()> {
    Ok(log::info(message)?)
}

pub(crate) fn show_warning(message: &str) -> Result<()> {
    Ok(log::warning(message)?)
}

#[cfg(test)]
mod tests {
    use anyhow::anyhow;

    use super::*;

    #[test]
    fn webhook_url_requires_https() {
        assert_eq!(
            webhook_url("https://agent.example/", "/channels/github/events").unwrap(),
            "https://agent.example/channels/github/events"
        );
        for invalid in [
            "http://agent.example",
            "https://user:pass@agent.example",
            "https://agent.example?x=1",
            "not a url",
        ] {
            assert!(
                webhook_url(invalid, "/channels/github/events").is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn agent_auth_overrides_come_from_dotenv_and_never_become_secrets() {
        let env_vars = BTreeMap::from([
            (
                "MDA_AGENT_AUTH_URL".to_string(),
                " http://localhost:1980 ".to_string(),
            ),
            (
                "MDA_AGENT_AUTH_API_KEY".to_string(),
                "lsv2_local".to_string(),
            ),
            ("MDA_AGENT_AUTH_TENANT_ID".to_string(), String::new()),
            (
                OAUTH_CALLBACK_ENV.to_string(),
                "http://localhost:21980/api/v1/agent-auth/oauth/callback".to_string(),
            ),
            ("OPENAI_API_KEY".to_string(), "sk-test".to_string()),
        ]);
        assert_eq!(
            override_value("MDA_AGENT_AUTH_URL", &env_vars).as_deref(),
            Some("http://localhost:1980")
        );
        assert_eq!(override_value("MDA_AGENT_AUTH_TENANT_ID", &env_vars), None);

        let secrets = crate::deploy::helpers::secrets_from_env(&env_vars);
        let names: Vec<_> = secrets.iter().map(|secret| secret.name.as_str()).collect();
        assert_eq!(names, ["OPENAI_API_KEY"]);

        let missing_key = BTreeMap::from([(
            "MDA_AGENT_AUTH_URL".to_string(),
            "http://localhost:1980".to_string(),
        )]);
        let client = langsmith::Client::new(
            "http://localhost:1",
            "http://localhost:1",
            langsmith::ApiKey::new("lsv2_deploy"),
            None,
        )
        .unwrap();
        let Err(error) = agent_auth_client(&client, |key| missing_key.get(key).cloned()) else {
            panic!("an Agent Auth URL without an API key must be rejected");
        };
        assert!(format!("{error}").contains("MDA_AGENT_AUTH_API_KEY"));
    }

    #[test]
    fn a_configured_oauth_callback_is_checked() {
        let configured = "http://localhost:21980/api/v1/agent-auth/oauth/callback";
        assert_eq!(
            configured_oauth_callback(Some(configured.to_string()))
                .unwrap()
                .as_deref(),
            Some(configured)
        );
        assert_eq!(configured_oauth_callback(None).unwrap(), None);
        for invalid in ["not a url", "ftp://localhost/callback"] {
            let error = configured_oauth_callback(Some(invalid.into())).unwrap_err();
            assert!(error.to_string().contains(OAUTH_CALLBACK_ENV), "{error}");
        }
    }

    #[test]
    fn only_unpublished_client_metadata_falls_back_to_the_default_callback() {
        // The shell's override would win over what these tests serve.
        if std::env::var(OAUTH_CALLBACK_ENV).is_ok() {
            return;
        }
        let client = |url: &str| {
            let deploy = langsmith::Client::new(
                "http://localhost:1",
                "http://localhost:1",
                langsmith::ApiKey::new("lsv2_deploy"),
                None,
            )
            .unwrap();
            agent_auth_client(&deploy, |key| match key {
                AGENT_AUTH_URL_ENV => Some(url.to_string()),
                AGENT_AUTH_API_KEY_ENV => Some("lsv2_local".to_string()),
                _ => None,
            })
            .unwrap()
        };
        let none = BTreeMap::new();

        // Published: used as is.
        let (url, _rx) = langsmith::test_support::serve_once(
            "200 OK",
            serde_json::json!({
                "redirect_uris": ["https://api.smith.example/v1/agent-auth/oauth/callback"],
            })
            .to_string(),
        );
        assert_eq!(
            oauth_callback_url(&client(&url), &none).unwrap(),
            "https://api.smith.example/v1/agent-auth/oauth/callback"
        );

        // Not published (an older Agent Auth): the callback on its API.
        let (url, _rx) = langsmith::test_support::serve_once("404 Not Found", "{}".to_string());
        let agent_auth = client(&url);
        assert_eq!(
            oauth_callback_url(&agent_auth, &none).unwrap(),
            agent_auth.oauth_redirect_uri().unwrap()
        );

        // Any other failure says nothing about the callback, so it fails.
        for status in ["500 Internal Server Error", "401 Unauthorized"] {
            let (url, _rx) = langsmith::test_support::serve_once(status, "{}".to_string());
            let error = oauth_callback_url(&client(&url), &none).unwrap_err();
            assert!(
                format!("{error:#}").contains("OAuth callback"),
                "{status}: {error:#}"
            );
        }
    }

    #[test]
    fn env_notices_name_every_variable_they_used() {
        assert_eq!(join_names(&["A"]), "A");
        assert_eq!(join_names(&["A", "B"]), "A and B");
        assert_eq!(join_names(&["A", "B", "C"]), "A, B and C");
        assert_eq!(
            env_app_notice("Slack app", "LangSmith", &["X", "Y"]),
            "Using the Slack app from X and Y. LangSmith keeps its credentials, so you can remove X and Y afterwards."
        );
    }

    #[test]
    fn slugs_name_the_deployments_app_and_its_secrets() {
        let slugs = AppSlugs::new("mda/linear-app/");
        assert_eq!(slugs.connection("d-1"), "mda/linear-app/d-1");
        assert_eq!(
            slugs.secret("d-1", WEBHOOK_SECRET_SUFFIX),
            "mda/linear-app/d-1/webhook-secret"
        );
        assert_eq!(slugs.deployment_of("mda/linear-app/d-1"), Some("d-1"));
        for other in [
            "mda/linear-app/",
            "mda/linear-app/d-1/webhook-secret",
            "mda/github-app/d-1",
        ] {
            assert_eq!(slugs.deployment_of(other), None, "{other}");
        }
    }

    #[derive(Default)]
    struct Ui {
        interactive: bool,
        retries: Vec<bool>,
        log: Vec<String>,
    }

    impl SetupUi for Ui {
        fn interactive(&self) -> bool {
            self.interactive
        }
        fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool> {
            self.log.push(format!("retry? {error}"));
            Ok(self.retries.remove(0))
        }
        fn success(&mut self, message: &str) -> Result<()> {
            self.log.push(format!("success {message}"));
            Ok(())
        }
        fn info(&mut self, message: &str) -> Result<()> {
            self.log.push(format!("info {message}"));
            Ok(())
        }
        fn warning(&mut self, message: &str) -> Result<()> {
            self.log.push(format!("warning {message}"));
            Ok(())
        }
    }

    #[test]
    fn a_failed_step_is_retried_from_what_is_found_then_or_skipped() {
        let mut ui = Ui {
            interactive: true,
            retries: vec![true],
            ..Ui::default()
        };
        let done = retry_or_skip(
            &mut ui,
            0,
            "skipped",
            |_, attempt| {
                if attempt == 0 {
                    Err(anyhow!("first"))
                } else {
                    Ok(attempt)
                }
            },
            |_| Ok(1),
        )
        .unwrap();
        assert_eq!(done, Some(1));
        assert_eq!(ui.log, ["retry? first"]);

        let mut skip = Ui {
            interactive: true,
            retries: vec![false],
            ..Ui::default()
        };
        let done: Option<()> = retry_or_skip(
            &mut skip,
            (),
            "skipped",
            |_, ()| Err(anyhow!("no")),
            |_| Ok(()),
        )
        .unwrap();
        assert_eq!(done, None);
        assert_eq!(skip.log, ["retry? no", "warning skipped"]);

        // Without a terminal, the error ends the deploy.
        let mut unattended = Ui::default();
        let error = retry_or_skip(
            &mut unattended,
            (),
            "skipped",
            |_, ()| -> Result<()> { Err(anyhow!("no")) },
            |_| Ok(()),
        )
        .unwrap_err();
        assert_eq!(error.to_string(), "no");
        assert!(unattended.log.is_empty());
    }
}
