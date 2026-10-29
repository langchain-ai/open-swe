use std::collections::VecDeque;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};

use super::*;
use crate::compile::ChannelRequirements;
use crate::langsmith::test_support::serve_sequence;
use crate::langsmith::{ApiError, ApiKey};

const WEBHOOK: &str = "https://agent.example/channels/linear/events";
const REDIRECT: &str = "https://api.smith.example/v1/agent-auth/oauth/callback";

fn channel(sessions: bool) -> LinearChannel {
    let manifest = json!({
        "name": "linear",
        "provider": "linear",
        "autoReply": true,
        "eventsPath": "/channels/linear/events",
        "providerConfig": super::manifest::provider_config(sessions),
    });
    prepare(
        &ChannelRequirements::from([("linear".to_string(), manifest)]),
        true,
        "triage-agent",
        std::path::Path::new("."),
        &[],
    )
    .unwrap()
    .unwrap()
}

fn sessions() -> LinearChannel {
    channel(true)
}

fn credentials() -> AppCredentials {
    AppCredentials {
        client_id: "client-1".into(),
        client_secret: "secret-1".into(),
        webhook_secret: "lin_wh_signing".into(),
    }
}

fn session(status: &str, redirect: &str) -> OAuthAuthorizationSession {
    serde_json::from_value(json!({
        "id": "session-1",
        "status": status,
        "verification_url": format!(
            "https://linear.app/oauth/authorize?actor=app&redirect_uri={}",
            redirect.replace(':', "%3A").replace('/', "%2F")
        ),
    }))
    .unwrap()
}

/// Records what setup did; answers come from the queues.
struct Fake {
    interactive: bool,
    connections: VecDeque<Option<AppConnection>>,
    env_credentials: Option<AppCredentials>,
    env_webhook_secret: Option<String>,
    workspace: Option<String>,
    install: OAuthAuthorizationSession,
    statuses: VecDeque<String>,
    retry: VecDeque<bool>,
    fail_create: bool,
    /// Whether deleting a workspace connection is refused, as for anyone
    /// but a workspace admin.
    forbid_connection_delete: bool,
    app_source: AppSource,
    asked_app_source: bool,
    existing_settings: Option<String>,
    /// Answers to "added the redirect URI?"; Yes once they run out.
    redirect_answers: VecDeque<bool>,
    /// Answers to "set the app's settings?"; Yes once they run out.
    settings_answers: VecDeque<bool>,
    calls: Vec<String>,
}

impl Fake {
    fn new() -> Self {
        Self {
            interactive: true,
            connections: VecDeque::new(),
            env_credentials: None,
            env_webhook_secret: None,
            workspace: Some("acme".into()),
            install: session("pending", REDIRECT),
            statuses: VecDeque::from(["completed".to_string()]),
            retry: VecDeque::new(),
            fail_create: false,
            forbid_connection_delete: false,
            app_source: AppSource::New,
            asked_app_source: false,
            existing_settings: None,
            redirect_answers: VecDeque::new(),
            settings_answers: VecDeque::new(),
            calls: Vec::new(),
        }
    }

    fn called(&self, prefix: &str) -> Vec<&String> {
        self.calls
            .iter()
            .filter(|call| call.starts_with(prefix))
            .collect()
    }
}

impl SetupUi for Fake {
    fn interactive(&self) -> bool {
        self.interactive
    }
    fn retry_or_skip(&mut self, _error: &anyhow::Error) -> Result<bool> {
        self.calls.push("retry_or_skip".into());
        Ok(self.retry.pop_front().unwrap_or(false))
    }
    fn success(&mut self, message: &str) -> Result<()> {
        self.calls.push(format!("success {message}"));
        Ok(())
    }
    fn info(&mut self, message: &str) -> Result<()> {
        self.calls.push(format!("info {message}"));
        Ok(())
    }
    fn warning(&mut self, message: &str) -> Result<()> {
        self.calls.push(format!("warning {message}"));
        Ok(())
    }
}

impl SetupEnv for Fake {
    fn app_connection(&mut self) -> Result<Option<AppConnection>> {
        Ok(self.connections.pop_front().flatten())
    }
    fn credentials_from_env(&mut self) -> Result<Option<AppCredentials>> {
        Ok(self.env_credentials.clone())
    }
    fn webhook_secret_from_env(&mut self) -> Result<Option<String>> {
        Ok(self.env_webhook_secret.clone())
    }
    fn workspace_key(&mut self) -> Result<Option<String>> {
        Ok(self.workspace.clone())
    }
    fn configured_workspace_key(&self) -> Option<String> {
        self.workspace.clone()
    }
    fn choose_app_source(&mut self) -> Result<AppSource> {
        self.asked_app_source = true;
        Ok(self.app_source)
    }
    fn show_existing_app_settings(&mut self, settings: &str) -> Result<()> {
        self.existing_settings = Some(settings.to_string());
        Ok(())
    }
    fn open_create_page(&mut self, url: &str) -> Result<()> {
        self.calls.push(format!("open_create_page {url}"));
        Ok(())
    }
    fn ask_credentials(&mut self) -> Result<AppCredentials> {
        self.calls.push("ask_credentials".into());
        Ok(credentials())
    }
    fn ask_webhook_secret(&mut self) -> Result<String> {
        self.calls.push("ask_webhook_secret".into());
        Ok("lin_wh_signing".into())
    }
    fn create_app(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<AppConnection> {
        self.calls.push(format!(
            "create_app {} {} {}",
            credentials.client_id,
            credentials.webhook_secret,
            channel.scopes.join(",")
        ));
        if self.fail_create {
            return Err(anyhow!("Agent Auth is down"));
        }
        Ok(owned("app-1", "conn-1", AgentGrant::Missing).unwrap())
    }
    fn store_webhook_secret(&mut self, secret: &str) -> Result<()> {
        self.calls.push(format!("store_webhook_secret {secret}"));
        Ok(())
    }
    fn start_install(&mut self, connection_id: &str) -> Result<OAuthAuthorizationSession> {
        self.calls.push(format!("start_install {connection_id}"));
        Ok(self.install.clone())
    }
    fn confirm_redirect_uri(&mut self, actual: &str) -> Result<bool> {
        self.calls.push(format!("confirm_redirect_uri {actual}"));
        Ok(self.redirect_answers.pop_front().unwrap_or(true))
    }
    fn confirm_app_settings(&mut self) -> Result<bool> {
        self.calls.push("confirm_app_settings".into());
        Ok(self.settings_answers.pop_front().unwrap_or(true))
    }
    fn open_install_page(&mut self, _url: &str) -> Result<()> {
        self.calls.push("open_install_page".into());
        Ok(())
    }
    fn next_install_status(&mut self, _session_id: &str) -> Result<String> {
        Ok(self
            .statuses
            .pop_front()
            .unwrap_or_else(|| "failed".to_string()))
    }
    fn delete_app(&mut self, app_id: &str) -> Result<()> {
        self.calls.push(format!("delete_app {app_id}"));
        Ok(())
    }
    fn delete_connection(&mut self, connection_id: &str) -> Result<()> {
        self.calls
            .push(format!("delete_connection {connection_id}"));
        if self.forbid_connection_delete {
            return Err(ApiError {
                status: reqwest::StatusCode::FORBIDDEN,
                detail: "missing permission workspaces:manage".into(),
            })
            .context("failed to delete the connection");
        }
        Ok(())
    }
    fn deployment_id(&self) -> &str {
        "dep-1"
    }
    fn webhook_url(&self) -> &str {
        WEBHOOK
    }
    fn redirect_uri(&self) -> &str {
        REDIRECT
    }
}

/// The deployment's Agent Auth app.
fn owned(id: &str, connection: &str, grant: AgentGrant) -> Option<AppConnection> {
    Some(AppConnection {
        connection_id: connection.into(),
        store: AppStore::App { id: id.into() },
        grant,
    })
}

/// The workspace connections an earlier mda set up.
fn legacy(id: &str, secret: Option<&str>, grant: AgentGrant) -> Option<AppConnection> {
    Some(AppConnection {
        connection_id: id.into(),
        store: AppStore::Connections {
            webhook_secret_id: secret.map(str::to_string),
        },
        grant,
    })
}

fn manifest_of(url: &str) -> Value {
    let url = reqwest::Url::parse(url).unwrap();
    let (_, manifest) = url
        .query_pairs()
        .find(|(key, _)| key == "manifest")
        .unwrap();
    serde_json::from_str(&manifest).unwrap()
}

#[test]
fn creates_and_installs_a_new_app_from_a_filled_in_page() {
    let mut env = Fake::new();
    run_setup(&mut env, &sessions()).unwrap();

    let opened = env.called("open_create_page");
    let url = opened[0].trim_start_matches("open_create_page ");
    assert!(url.starts_with("https://linear.app/acme/settings/api/applications/new?manifest="));
    let manifest = manifest_of(url);
    assert_eq!(manifest["oauth"]["client_name"], "triage-agent");
    assert!(manifest["display"]["description"]
        .as_str()
        .unwrap()
        .starts_with("A Managed Deep Agent"));
    assert!(manifest["display"].get("iconUrl").is_none());
    assert_eq!(manifest["oauth"]["redirect_uris"], json!([REDIRECT]));
    assert_eq!(manifest["webhook"]["url"], WEBHOOK);
    assert_eq!(
        manifest["webhook"]["resourceTypes"],
        json!([
            "AgentSessionEvent",
            "OAuthAuthorization",
            "PermissionChange"
        ])
    );
    // One app holds the credentials and the signing secret.
    assert_eq!(
        env.calls[1..5],
        [
            "ask_credentials".to_string(),
            "create_app client-1 lin_wh_signing read,write,app:mentionable,app:assignable".into(),
            "start_install conn-1".into(),
            "open_install_page".into(),
        ]
    );
    assert_eq!(
        env.calls[5],
        format!("success Linear channel ready · the Linear app sends its events to {WEBHOOK}")
    );
    assert!(env.called("store_webhook_secret").is_empty());
    assert!(env.called("delete_").is_empty());
    assert!(env.asked_app_source);
    // Said once, on the deploy that installs the app.
    assert_eq!(
        env.called("info Linear has no API to change an app").len(),
        1
    );
}

#[test]
fn fills_in_the_configured_name_and_description() {
    let mut manifest = json!({
        "name": "linear",
        "provider": "linear",
        "eventsPath": "/channels/linear/events",
        "providerConfig": super::manifest::provider_config(true),
    });
    manifest["providerConfig"]["app"] = json!({
        "name": "Triage Bot",
        "description": "Triages new issues.",
    });
    let channel = prepare(
        &ChannelRequirements::from([("linear".to_string(), manifest)]),
        true,
        "triage-agent",
        std::path::Path::new("."),
        &[],
    )
    .unwrap()
    .unwrap();
    let url = create_app_url(Some("acme"), &channel, WEBHOOK, REDIRECT).unwrap();
    let manifest = manifest_of(&url);
    assert_eq!(manifest["oauth"]["client_name"], "Triage Bot");
    assert_eq!(
        manifest["display"],
        json!({ "description": "Triages new issues." })
    );
}

#[test]
fn points_the_icon_at_the_one_the_deployment_serves() {
    let mut channel = sessions();
    channel.app_icon = Some("assets/icon.png".into());
    let url = create_app_url(Some("acme"), &channel, WEBHOOK, REDIRECT).unwrap();
    assert_eq!(
        manifest_of(&url)["display"]["iconUrl"],
        "https://agent.example/channels/linear/icon"
    );
}

#[test]
fn uses_credentials_from_the_env_without_a_page() {
    let mut env = Fake::new();
    env.interactive = false;
    env.env_credentials = Some(credentials());
    run_setup(&mut env, &sessions()).unwrap();
    assert!(env.called("open_create_page").is_empty());
    assert!(env.called("ask_credentials").is_empty());
    assert_eq!(env.called("create_app").len(), 1);
    assert!(env.called("store_webhook_secret").is_empty());

    // Without a terminal and without them, setup says what to set.
    let mut unattended = Fake::new();
    unattended.interactive = false;
    unattended.workspace = None;
    let error = run_setup(&mut unattended, &sessions()).unwrap_err();
    assert!(format!("{error:#}").contains(CLIENT_ID_ENV));
    assert!(format!("{error:#}").contains("settings/api/applications/new"));
    // Without the workspace, the link opens wherever the person last was.
    assert!(format!("{error:#}").contains(WORKSPACE_ENV));
    let mut scoped = Fake::new();
    scoped.interactive = false;
    let error = run_setup(&mut scoped, &sessions()).unwrap_err();
    assert!(format!("{error:#}").contains("linear.app/acme/settings/api"));
    assert!(!format!("{error:#}").contains(WORKSPACE_ENV));
}

#[test]
fn lists_an_env_apps_settings_and_waits_for_them_in_a_terminal() {
    let mut env = Fake::new();
    env.env_credentials = Some(credentials());
    env.settings_answers = VecDeque::from([false]);
    run_setup(&mut env, &sessions()).unwrap();

    let warning = env.called("warning Point your Linear app")[0].clone();
    assert!(warning.contains(&format!("Redirect URIs: add {REDIRECT}")));
    assert!(warning.contains(&format!("URL {WEBHOOK}")));
    assert!(warning.contains("Linear can't tell deploy what the app has set"));
    assert!(!warning.contains("prompts below"));
    // "No" asks again; the install page opens only after "Yes".
    assert_eq!(env.called("confirm_app_settings").len(), 2);
    assert_eq!(env.called("info Linear refuses the install").len(), 1);
    let confirmed = env
        .calls
        .iter()
        .rposition(|call| call == "confirm_app_settings")
        .unwrap();
    let open = env
        .calls
        .iter()
        .position(|call| call == "open_install_page")
        .unwrap();
    assert!(confirmed < open);

    // Without a terminal the settings come before the install link, unasked.
    let mut unattended = Fake::new();
    unattended.interactive = false;
    unattended.env_credentials = Some(credentials());
    run_setup(&mut unattended, &sessions()).unwrap();
    assert!(unattended.called("confirm_app_settings").is_empty());
    let listed = unattended
        .calls
        .iter()
        .position(|call| call.starts_with("warning Point your Linear app"))
        .unwrap();
    let open = unattended
        .calls
        .iter()
        .position(|call| call == "open_install_page")
        .unwrap();
    assert!(listed < open);

    // A new app from deploy's page is filled in, so nothing is listed.
    let mut created = Fake::new();
    run_setup(&mut created, &sessions()).unwrap();
    assert!(created.called("warning Point your Linear app").is_empty());
    assert!(created.called("confirm_app_settings").is_empty());
}

#[test]
fn tells_a_wrong_client_id_or_secret_from_an_answer_that_says_nothing() {
    let invalid = |error: &str| format!(r#"{{"error":"{error}","error_description":"x"}}"#);
    let secret = credentials_verdict(400, &invalid("invalid_secret")).unwrap_err();
    assert!(secret.to_string().contains(CLIENT_SECRET_ENV), "{secret}");
    let client = credentials_verdict(400, &invalid("invalid_client")).unwrap_err();
    assert!(client.to_string().contains(CLIENT_ID_ENV), "{client}");
    // Right credentials: Linear only refuses the made-up code.
    assert!(credentials_verdict(400, &invalid("invalid_grant")).is_ok());
    // Anything else can't tell, so deploy goes on.
    assert!(credentials_verdict(500, &invalid("invalid_client")).is_ok());
    assert!(credentials_verdict(400, "not json").is_ok());
    assert!(credentials_verdict(200, "{}").is_ok());
}

#[test]
fn says_when_agent_auth_redirects_elsewhere() {
    let mut env = Fake::new();
    env.install = session(
        "pending",
        "https://tunnel.trycloudflare.com/v1/agent-auth/oauth/callback",
    );
    run_setup(&mut env, &sessions()).unwrap();
    // Asked about before the install page opens, not after.
    let confirm = env
        .calls
        .iter()
        .position(|call| {
            call == "confirm_redirect_uri https://tunnel.trycloudflare.com/v1/agent-auth/oauth/callback"
        })
        .unwrap();
    let open = env
        .calls
        .iter()
        .position(|call| call == "open_install_page")
        .unwrap();
    assert!(confirm < open);

    // A redirect URI the new app already lists needs nothing.
    let mut listed = Fake::new();
    run_setup(&mut listed, &sessions()).unwrap();
    assert!(listed.called("confirm_redirect_uri").is_empty());
}

#[test]
fn a_no_to_the_redirect_uri_asks_again_before_the_install_page_opens() {
    let tunnel = "https://tunnel.trycloudflare.com/v1/agent-auth/oauth/callback";
    let mut env = Fake::new();
    env.install = session("pending", tunnel);
    env.redirect_answers = VecDeque::from([false, false]);
    run_setup(&mut env, &sessions()).unwrap();
    assert_eq!(env.called("confirm_redirect_uri").len(), 3);
    assert_eq!(env.called("info Linear refuses the install").len(), 2);
    let last_confirm = env
        .calls
        .iter()
        .rposition(|call| call.starts_with("confirm_redirect_uri"))
        .unwrap();
    let open = env
        .calls
        .iter()
        .position(|call| call == "open_install_page")
        .unwrap();
    assert!(last_confirm < open);

    // Without a terminal nobody is asked: deploy prints the URI and goes on.
    let mut unattended = Fake::new();
    unattended.interactive = false;
    unattended.env_credentials = Some(credentials());
    unattended.install = session("pending", tunnel);
    run_setup(&mut unattended, &sessions()).unwrap();
    assert!(unattended.called("confirm_redirect_uri").is_empty());
    assert_eq!(
        unattended
            .called("warning Agent Auth expects Linear to redirect")
            .len(),
        1
    );
}

#[test]
fn removes_an_unfinished_install_and_offers_to_try_again() {
    let mut env = Fake::new();
    env.statuses = VecDeque::from(["failed".to_string(), "completed".to_string()]);
    env.retry = VecDeque::from([true]);
    run_setup(&mut env, &sessions()).unwrap();
    // The failed attempt's app goes, with its connection and secret.
    assert_eq!(env.called("delete_"), ["delete_app app-1"]);
    assert_eq!(env.called("create_app").len(), 2);
    assert_eq!(env.called("retry_or_skip").len(), 1);
    assert_eq!(env.called("start_install").len(), 2);
    assert_eq!(env.called("success").len(), 1);

    // Skipping continues the deploy without the channel.
    let mut skip = Fake::new();
    skip.fail_create = true;
    run_setup(&mut skip, &sessions()).unwrap();
    assert!(skip.called("warning")[0].contains("Continuing without the Linear channel"));
}

#[test]
fn finishes_what_an_earlier_setup_left() {
    // An earlier mda's install, missing the signing secret.
    let mut env = Fake::new();
    env.connections = VecDeque::from([legacy("conn-1", None, AgentGrant::Active)]);
    run_setup(&mut env, &sessions()).unwrap();
    assert_eq!(
        env.calls[..2],
        [
            "ask_webhook_secret".to_string(),
            "store_webhook_secret lin_wh_signing".into()
        ]
    );
    assert!(env.called("start_install").is_empty());

    // An install that never finished starts over with new credentials.
    let mut stale = Fake::new();
    stale.connections = VecDeque::from([owned("app-0", "conn-0", AgentGrant::Missing)]);
    run_setup(&mut stale, &sessions()).unwrap();
    assert_eq!(stale.called("delete_"), ["delete_app app-0"]);
    assert_eq!(stale.called("ask_credentials").len(), 1);
    assert_eq!(stale.called("create_app").len(), 1);

    // So does an earlier mda's, whose connections go one by one.
    let mut stale = Fake::new();
    stale.connections = VecDeque::from([legacy("conn-0", Some("secret-0"), AgentGrant::Missing)]);
    run_setup(&mut stale, &sessions()).unwrap();
    assert_eq!(
        stale.called("delete_"),
        ["delete_connection secret-0", "delete_connection conn-0"]
    );
    assert_eq!(stale.called("create_app").len(), 1);

    // A ready app needs nothing, and setup never asks Linear about it.
    let mut ready = Fake::new();
    ready.connections = VecDeque::from([owned("app-1", "conn-1", AgentGrant::Active)]);
    run_setup(&mut ready, &sessions()).unwrap();
    assert!(ready.calls[0].starts_with("success Linear channel ready · "));
    assert!(ready.called("start_install").is_empty());
    assert!(ready.called("delete_").is_empty());

    // An earlier mda's ready connections are kept as they are.
    let mut kept = Fake::new();
    kept.connections = VecDeque::from([legacy("conn-1", Some("secret-1"), AgentGrant::Active)]);
    run_setup(&mut kept, &sessions()).unwrap();
    assert!(kept.calls[0].starts_with("success Linear channel ready · "));
    assert_eq!(kept.calls.len(), 1, "{:?}", kept.calls);
}

#[test]
fn says_where_the_app_changes_once_when_it_is_installed() {
    let mut env = Fake::new();
    run_setup(&mut env, &sessions()).unwrap();
    let notes = env.called("info Linear has no API to change an app");
    assert_eq!(notes.len(), 1, "{:?}", env.calls);
    assert!(notes[0].contains("app name, description or icon"));
    assert!(notes[0].contains("https://linear.app/acme/settings/api"));

    // Without a workspace key, it names where the page is.
    let mut unscoped = Fake::new();
    unscoped.workspace = None;
    run_setup(&mut unscoped, &sessions()).unwrap();
    assert!(
        unscoped.called("info Linear has no API to change an app")[0]
            .contains("Settings → Administration → API")
    );

    // A redeploy that keeps the app only says it is ready.
    let mut kept = Fake::new();
    kept.connections = VecDeque::from([owned("app-1", "conn-1", AgentGrant::Active)]);
    run_setup(&mut kept, &sessions()).unwrap();
    assert_eq!(
        kept.calls,
        [format!(
            "success Linear channel ready · the Linear app sends its events to {WEBHOOK}"
        )]
    );
}

#[test]
fn asks_for_consent_again_when_the_grant_lapsed() {
    // The app and its secret stay: only the admin's approval is needed.
    let mut env = Fake::new();
    env.connections = VecDeque::from([owned("app-0", "conn-0", AgentGrant::AuthorizationRequired)]);
    run_setup(&mut env, &sessions()).unwrap();
    assert!(env.calls[0].starts_with("warning Linear stopped renewing"));
    assert_eq!(
        env.calls[1..3],
        [
            "start_install conn-0".to_string(),
            "open_install_page".into()
        ]
    );
    assert!(env.called("delete_").is_empty());
    assert!(env.called("ask_credentials").is_empty());
    assert!(env.called("open_create_page").is_empty());
    assert!(env.called("create_app").is_empty());
    assert_eq!(env.called("success").len(), 1);

    // A missing signing secret of an earlier mda's install is stored along
    // the way.
    let mut unsigned = Fake::new();
    unsigned.connections =
        VecDeque::from([legacy("conn-0", None, AgentGrant::AuthorizationRequired)]);
    run_setup(&mut unsigned, &sessions()).unwrap();
    assert_eq!(
        unsigned.called("store_webhook_secret"),
        ["store_webhook_secret lin_wh_signing"]
    );
    assert_eq!(unsigned.called("start_install"), ["start_install conn-0"]);

    // Without a terminal, deploy prints the link and waits for it.
    let mut unattended = Fake::new();
    unattended.interactive = false;
    unattended.connections =
        VecDeque::from([owned("app-0", "conn-0", AgentGrant::AuthorizationRequired)]);
    run_setup(&mut unattended, &sessions()).unwrap();
    assert_eq!(unattended.called("open_install_page").len(), 1);
    assert_eq!(unattended.called("success").len(), 1);

    // A consent that fails keeps the app, to approve again next time.
    let mut refused = Fake::new();
    refused.statuses = VecDeque::from(["failed".to_string()]);
    refused.connections =
        VecDeque::from([owned("app-0", "conn-0", AgentGrant::AuthorizationRequired)]);
    run_setup(&mut refused, &sessions()).unwrap();
    assert!(refused.called("delete_").is_empty());
    assert_eq!(refused.called("retry_or_skip").len(), 1);
    assert!(refused
        .called("warning")
        .last()
        .unwrap()
        .contains("Continuing without the Linear channel"));
}

#[test]
fn asks_for_consent_again_when_the_grant_was_revoked() {
    // A revoke keeps the app and its secret, like a lapsed grant: it does not
    // delete the connection or ask for a new app.
    let mut env = Fake::new();
    env.connections = VecDeque::from([owned("app-0", "conn-0", AgentGrant::Revoked)]);
    run_setup(&mut env, &sessions()).unwrap();
    assert!(
        env.calls[0].starts_with("warning This deployment's access to the Linear app was revoked"),
        "{:?}",
        env.calls
    );
    assert_eq!(
        env.calls[1..3],
        [
            "start_install conn-0".to_string(),
            "open_install_page".into()
        ]
    );
    assert!(env.called("delete_").is_empty());
    assert!(env.called("ask_credentials").is_empty());
    assert!(env.called("create_app").is_empty());
    assert_eq!(env.called("success").len(), 1);
}

#[test]
fn warns_about_a_grant_status_it_does_not_know_and_keeps_the_app() {
    let unknown = || owned("app-1", "conn-1", AgentGrant::Unknown("suspended".into()));
    let mut env = Fake::new();
    env.connections = VecDeque::from([unknown()]);
    run_setup(&mut env, &sessions()).unwrap();
    let warnings = env.called("warning");
    assert_eq!(warnings.len(), 1, "{:?}", env.calls);
    assert!(warnings[0].contains("`suspended`"), "{}", warnings[0]);
    // Neither healthy without a word nor a reason to fail or re-consent.
    assert!(env.called("start_install").is_empty());
    assert!(env.called("delete_").is_empty());
    assert!(env.called("create_app").is_empty());
    assert_eq!(env.called("success").len(), 1);

    // A missing signing secret is still stored, also without a terminal.
    let mut unsigned = Fake::new();
    unsigned.interactive = false;
    unsigned.env_webhook_secret = Some("lin_wh_from_env".into());
    unsigned.connections = VecDeque::from([legacy(
        "conn-1",
        None,
        AgentGrant::Unknown("suspended".into()),
    )]);
    run_setup(&mut unsigned, &sessions()).unwrap();
    assert_eq!(
        unsigned.called("store_webhook_secret"),
        ["store_webhook_secret lin_wh_from_env"]
    );
}

#[test]
fn sets_up_nothing_without_handled_events() {
    let mut env = Fake::new();
    run_setup(&mut env, &channel(false)).unwrap();
    assert_eq!(env.calls.len(), 1);
    assert!(env.calls[0].starts_with("info The Linear channel handles no events"));
}

#[test]
fn reads_the_workspace_key_from_a_url_or_the_key() {
    assert_eq!(workspace_key("Acme").unwrap(), "acme");
    assert_eq!(
        workspace_key("https://linear.app/acme-inc/team/ENG/active").unwrap(),
        "acme-inc"
    );
    for invalid in ["", "acme/../x", "https://linear.app/", "a b"] {
        assert!(workspace_key(invalid).is_err(), "{invalid}");
    }
    let url = create_app_url(None, &sessions(), WEBHOOK, REDIRECT).unwrap();
    assert!(url.starts_with("https://linear.app/settings/api/applications/new?manifest="));
}

#[test]
fn stores_a_missing_signing_secret_from_the_env_alone() {
    let installed = || legacy("conn-1", None, AgentGrant::Active);
    // Without a terminal, the signing secret alone repairs the install.
    let mut env = Fake::new();
    env.interactive = false;
    env.connections = VecDeque::from([installed()]);
    env.env_webhook_secret = Some("lin_wh_from_env".into());
    run_setup(&mut env, &sessions()).unwrap();
    assert_eq!(
        env.called("store_webhook_secret"),
        ["store_webhook_secret lin_wh_from_env"]
    );
    assert!(env.called("ask_").is_empty());
    assert!(env.called("start_install").is_empty());

    // Without it, deploy names just that variable.
    let mut missing = Fake::new();
    missing.interactive = false;
    missing.connections = VecDeque::from([installed()]);
    let error = run_setup(&mut missing, &sessions()).unwrap_err();
    assert!(
        error.to_string().contains("Set MDA_LINEAR_WEBHOOK_SECRET"),
        "{error}"
    );
    assert!(
        !error.to_string().contains("MDA_LINEAR_CLIENT_ID"),
        "{error}"
    );
}

#[test]
fn reads_the_signing_secret_alone_from_the_env() {
    let vars = |pairs: &[(&str, &str)]| {
        pairs
            .iter()
            .map(|(key, value)| (key.to_string(), value.to_string()))
            .collect::<BTreeMap<_, _>>()
    };
    assert_eq!(
        webhook_secret_env(&vars(&[(WEBHOOK_SECRET_ENV, "lin_wh_abc")])).unwrap(),
        Some("lin_wh_abc".to_string())
    );
    assert_eq!(webhook_secret_env(&vars(&[])).unwrap(), None);
    // Checked like a pasted one, and never echoed.
    let error = webhook_secret_env(&vars(&[(WEBHOOK_SECRET_ENV, "not-a-secret")])).unwrap_err();
    assert!(error.to_string().contains(WEBHOOK_SECRET_ENV), "{error}");
    assert!(!error.to_string().contains("not-a-secret"), "{error}");
    // The other two are not needed for it, while a new app still needs all three.
    assert!(credentials_env(&vars(&[(WEBHOOK_SECRET_ENV, "lin_wh_abc")])).is_err());
    // In a terminal, the signing secret alone repairs an installed app.
    assert!(interactive_credentials_env(&vars(&[(WEBHOOK_SECRET_ENV, "lin_wh_abc")])).is_ok());
    assert!(interactive_credentials_env(&vars(&[])).is_ok());
    assert!(interactive_credentials_env(&vars(&[(CLIENT_ID_ENV, "id")])).is_err());
}

#[test]
fn uses_an_existing_app_pointed_at_the_deployment() {
    let mut env = Fake {
        app_source: AppSource::Existing,
        ..Fake::new()
    };
    run_setup(&mut env, &sessions()).unwrap();

    assert!(env.called("open_create_page").is_empty());
    let settings = env.existing_settings.unwrap();
    assert!(settings.contains("https://linear.app/acme/settings/api"));
    assert!(settings.contains(&format!("Redirect URIs: add {REDIRECT}")));
    assert!(settings.contains(&format!("URL {WEBHOOK}")));
    assert!(settings.contains("events: AgentSessionEvent, OAuthAuthorization, PermissionChange"));
    assert_eq!(
        env.calls[..4],
        [
            "ask_credentials".to_string(),
            "create_app client-1 lin_wh_signing read,write,app:mentionable,app:assignable".into(),
            "start_install conn-1".into(),
            "open_install_page".into(),
        ]
    );
}

#[test]
fn env_credentials_or_no_terminal_never_ask_where_the_app_comes_from() {
    let mut with_env = Fake {
        env_credentials: Some(credentials()),
        ..Fake::new()
    };
    run_setup(&mut with_env, &sessions()).unwrap();
    assert!(!with_env.asked_app_source);

    let mut unattended = Fake {
        interactive: false,
        ..Fake::new()
    };
    assert!(run_setup(&mut unattended, &sessions()).is_err());
    assert!(!unattended.asked_app_source);
}

#[test]
fn says_who_can_delete_an_earlier_mdas_unfinished_setup() {
    // Only a workspace admin can delete an earlier mda's connections, so
    // anyone else learns who can, rather than a bare 403.
    let mut env = Fake::new();
    env.interactive = false;
    env.env_credentials = Some(credentials());
    env.forbid_connection_delete = true;
    env.connections = VecDeque::from([legacy("conn-0", Some("secret-0"), AgentGrant::Missing)]);
    let error = run_setup(&mut env, &sessions()).unwrap_err().to_string();
    assert!(error.contains("LangSmith workspace admin"), "{error}");
    assert!(
        error.contains("mda connections delete mda/linear-app/dep-1/webhook-secret"),
        "{error}"
    );
    assert!(
        error.contains("mda connections delete mda/linear-app/dep-1`"),
        "{error}"
    );
    assert!(!error.contains("HTTP 403"), "{error}");
    // Nothing new is created over the old connections.
    assert!(env.called("create_app").is_empty());
}

fn agent_auth(base: &str) -> langsmith::Client {
    langsmith::Client::new(base, base, ApiKey::new("lsv2_key"), None).unwrap()
}

const CONNECTION: &str = "11111111-2222-3333-4444-555555555555";

#[test]
fn looks_up_the_deployments_linear_app() {
    let apps = json!([
        { "id": "app-gh", "provider": "github", "slug": "mda/github-app/dep-1", "connection_id": "conn-gh" },
        {
            "id": "app-1", "provider": "linear", "source": "existing",
            "slug": "mda/linear-app/dep-1", "connection_id": CONNECTION,
        },
    ]);
    let (base, requests) = serve_sequence(vec![
        ("200 OK", apps.to_string()),
        // The app's connection, with no grant yet.
        ("200 OK", json!({ "connection_id": CONNECTION }).to_string()),
    ]);
    let found = lookup_app_connection(&agent_auth(&base), "dep-1")
        .unwrap()
        .unwrap();
    assert_eq!(
        found,
        AppConnection {
            connection_id: CONNECTION.into(),
            store: AppStore::App { id: "app-1".into() },
            grant: AgentGrant::Missing,
        }
    );
    assert!(requests
        .recv()
        .unwrap()
        .starts_with("GET /v1/agent-auth/apps?owner_id=dep-1 "));
    let grant = requests.recv().unwrap();
    assert!(grant.contains(&format!("/{CONNECTION}?")), "{grant}");
}

#[test]
fn finds_an_earlier_mdas_connections_without_an_app() {
    let connections = json!({
        "items": [
            { "connection_id": "conn-0", "slug": "mda/linear-app/dep-1", "kind": "oauth" },
            { "connection_id": "secret-0", "slug": "mda/linear-app/dep-1/webhook-secret", "kind": "secret" },
            { "connection_id": "other", "slug": "mda/linear-app/dep-10", "kind": "oauth" },
        ],
    });
    let (base, _requests) = serve_sequence(vec![
        ("200 OK", "[]".to_string()),
        ("200 OK", connections.to_string()),
        ("200 OK", json!({ "connection_id": "conn-0" }).to_string()),
    ]);
    let found = lookup_app_connection(&agent_auth(&base), "dep-1")
        .unwrap()
        .unwrap();
    assert_eq!(found.connection_id, "conn-0");
    assert_eq!(
        found.store,
        AppStore::Connections {
            webhook_secret_id: Some("secret-0".into())
        }
    );

    // Neither: no app yet.
    let (base, _requests) = serve_sequence(vec![
        // A LangSmith without Agent Auth apps.
        ("404 Not Found", "{}".to_string()),
        ("200 OK", json!({ "items": [] }).to_string()),
    ]);
    assert_eq!(
        lookup_app_connection(&agent_auth(&base), "dep-1").unwrap(),
        None
    );
}
