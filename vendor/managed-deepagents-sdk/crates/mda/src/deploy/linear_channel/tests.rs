use std::collections::VecDeque;

use anyhow::{anyhow, Result};
use serde_json::{json, Value};

use super::*;
use crate::compile::ChannelRequirements;

const WEBHOOK: &str = "https://agent.example/channels/linear/events";
const REDIRECT: &str = "https://api.smith.example/v1/agent-auth/oauth/callback";

fn channel(events: &[&str]) -> LinearChannel {
    let manifest = json!({
        "name": "linear",
        "provider": "linear",
        "autoReply": true,
        "eventsPath": "/channels/linear/events",
        "providerConfig": { "events": events },
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
    channel(&["AgentSessionEvent.created", "AgentSessionEvent.prompted"])
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
    fn create_connection(
        &mut self,
        channel: &LinearChannel,
        credentials: &AppCredentials,
    ) -> Result<String> {
        self.calls.push(format!(
            "create_connection {} {}",
            credentials.client_id,
            channel.scopes.join(",")
        ));
        if self.fail_create {
            return Err(anyhow!("Agent Auth is down"));
        }
        Ok("conn-1".into())
    }
    fn store_webhook_secret(&mut self, secret: &str) -> Result<()> {
        self.calls.push(format!("store_webhook_secret {secret}"));
        Ok(())
    }
    fn start_install(&mut self, connection_id: &str) -> Result<OAuthAuthorizationSession> {
        self.calls.push(format!("start_install {connection_id}"));
        Ok(self.install.clone())
    }
    fn confirm_redirect_uri(&mut self, actual: &str) -> Result<()> {
        self.calls.push(format!("confirm_redirect_uri {actual}"));
        Ok(())
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
    fn delete_connection(&mut self, connection_id: &str) -> Result<()> {
        self.calls
            .push(format!("delete_connection {connection_id}"));
        Ok(())
    }
    fn webhook_url(&self) -> &str {
        WEBHOOK
    }
    fn redirect_uri(&self) -> &str {
        REDIRECT
    }
}

fn app(id: &str, secret: Option<&str>, grant: AgentGrant) -> Option<AppConnection> {
    Some(AppConnection {
        connection_id: id.into(),
        webhook_secret_id: secret.map(str::to_string),
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
    assert_eq!(
        env.calls[1..6],
        [
            "ask_credentials".to_string(),
            "create_connection client-1 read,write,app:mentionable,app:assignable".into(),
            "store_webhook_secret lin_wh_signing".into(),
            "start_install conn-1".into(),
            "open_install_page".into(),
        ]
    );
    assert_eq!(
        env.calls[6],
        format!("success Linear channel ready: the Linear app sends its events to {WEBHOOK}")
    );
    assert!(env.called("delete_connection").is_empty());
    // A new app gets what the channel says; only a kept one needs the note.
    assert!(!env
        .calls
        .iter()
        .any(|call| call.contains("keeps its Linear app")));
}

#[test]
fn fills_in_the_configured_name_and_description() {
    let manifest = json!({
        "name": "linear",
        "provider": "linear",
        "eventsPath": "/channels/linear/events",
        "providerConfig": {
            "events": ["AgentSessionEvent.created"],
            "app": {
                "name": "Triage Bot",
                "description": "Triages new issues.",
            },
        },
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
    assert_eq!(env.called("store_webhook_secret").len(), 1);

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
fn removes_an_unfinished_install_and_offers_to_try_again() {
    let mut env = Fake::new();
    env.statuses = VecDeque::from(["failed".to_string(), "completed".to_string()]);
    // The failed attempt left the connection and its secret.
    env.connections = VecDeque::from([
        None,
        app("conn-1", Some("secret-1"), AgentGrant::Missing),
        None,
    ]);
    env.retry = VecDeque::from([true]);
    run_setup(&mut env, &sessions()).unwrap();
    assert_eq!(
        env.called("delete_connection"),
        ["delete_connection secret-1", "delete_connection conn-1"]
    );
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
    // Installed, but the signing secret is missing.
    let mut env = Fake::new();
    env.connections = VecDeque::from([app("conn-1", None, AgentGrant::Active)]);
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
    stale.connections = VecDeque::from([app("conn-0", Some("secret-0"), AgentGrant::Missing)]);
    run_setup(&mut stale, &sessions()).unwrap();
    assert_eq!(
        stale.called("delete_connection"),
        ["delete_connection secret-0", "delete_connection conn-0"]
    );
    assert_eq!(stale.called("ask_credentials").len(), 1);

    // A ready app needs nothing, and setup never asks Linear about it.
    let mut ready = Fake::new();
    ready.connections = VecDeque::from([app("conn-1", Some("secret-1"), AgentGrant::Active)]);
    run_setup(&mut ready, &sessions()).unwrap();
    assert!(ready.calls[0].starts_with("success Linear channel ready: "));
    assert!(ready.called("start_install").is_empty());
    assert!(ready.called("delete_connection").is_empty());
}

#[test]
fn says_where_a_kept_apps_name_and_description_change() {
    let mut env = Fake::new();
    env.connections = VecDeque::from([app("conn-1", Some("secret-1"), AgentGrant::Active)]);
    run_setup(&mut env, &sessions()).unwrap();
    assert_eq!(
        env.called("info This deployment keeps its Linear app")
            .len(),
        1,
        "{:?}",
        env.calls
    );
    assert!(env.calls[1].contains("only apply when deploy creates it"));
    assert!(env.calls[1].contains("https://linear.app/acme/settings/api"));

    // Without a workspace key, it names where the page is.
    let mut unscoped = Fake::new();
    unscoped.workspace = None;
    unscoped.connections = VecDeque::from([app("conn-1", Some("secret-1"), AgentGrant::Active)]);
    run_setup(&mut unscoped, &sessions()).unwrap();
    assert!(unscoped.calls[1].contains("Settings → Administration → API"));
}

#[test]
fn asks_for_consent_again_when_the_grant_lapsed() {
    // The app and its secret stay: only the admin's approval is needed.
    let mut env = Fake::new();
    env.connections = VecDeque::from([app(
        "conn-0",
        Some("secret-0"),
        AgentGrant::AuthorizationRequired,
    )]);
    run_setup(&mut env, &sessions()).unwrap();
    assert!(env.calls[0].starts_with("warning Linear stopped renewing"));
    assert_eq!(
        env.calls[1..3],
        [
            "start_install conn-0".to_string(),
            "open_install_page".into()
        ]
    );
    assert!(env.called("delete_connection").is_empty());
    assert!(env.called("ask_credentials").is_empty());
    assert!(env.called("open_create_page").is_empty());
    assert!(env.called("create_connection").is_empty());
    assert_eq!(env.called("success").len(), 1);

    // A missing signing secret is stored along the way.
    let mut unsigned = Fake::new();
    unsigned.connections = VecDeque::from([app("conn-0", None, AgentGrant::AuthorizationRequired)]);
    run_setup(&mut unsigned, &sessions()).unwrap();
    assert_eq!(
        unsigned.called("store_webhook_secret"),
        ["store_webhook_secret lin_wh_signing"]
    );
    assert_eq!(unsigned.called("start_install"), ["start_install conn-0"]);

    // Without a terminal, deploy prints the link and waits for it.
    let mut unattended = Fake::new();
    unattended.interactive = false;
    unattended.connections = VecDeque::from([app(
        "conn-0",
        Some("secret-0"),
        AgentGrant::AuthorizationRequired,
    )]);
    run_setup(&mut unattended, &sessions()).unwrap();
    assert_eq!(unattended.called("open_install_page").len(), 1);
    assert_eq!(unattended.called("success").len(), 1);

    // A consent that fails keeps the app, to approve again next time.
    let mut refused = Fake::new();
    refused.statuses = VecDeque::from(["failed".to_string()]);
    refused.connections = VecDeque::from([app(
        "conn-0",
        Some("secret-0"),
        AgentGrant::AuthorizationRequired,
    )]);
    run_setup(&mut refused, &sessions()).unwrap();
    assert!(refused.called("delete_connection").is_empty());
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
    env.connections = VecDeque::from([app("conn-0", Some("secret-0"), AgentGrant::Revoked)]);
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
    assert!(env.called("delete_connection").is_empty());
    assert!(env.called("ask_credentials").is_empty());
    assert!(env.called("create_connection").is_empty());
    assert_eq!(env.called("success").len(), 1);
}

#[test]
fn warns_about_a_grant_status_it_does_not_know_and_keeps_the_app() {
    let unknown = || {
        app(
            "conn-1",
            Some("secret-1"),
            AgentGrant::Unknown("suspended".into()),
        )
    };
    let mut env = Fake::new();
    env.connections = VecDeque::from([unknown()]);
    run_setup(&mut env, &sessions()).unwrap();
    let warnings = env.called("warning");
    assert_eq!(warnings.len(), 1, "{:?}", env.calls);
    assert!(warnings[0].contains("`suspended`"), "{}", warnings[0]);
    // Neither healthy without a word nor a reason to fail or re-consent.
    assert!(env.called("start_install").is_empty());
    assert!(env.called("delete_connection").is_empty());
    assert!(env.called("create_connection").is_empty());
    assert_eq!(env.called("success").len(), 1);

    // A missing signing secret is still stored, also without a terminal.
    let mut unsigned = Fake::new();
    unsigned.interactive = false;
    unsigned.env_webhook_secret = Some("lin_wh_from_env".into());
    unsigned.connections =
        VecDeque::from([app("conn-1", None, AgentGrant::Unknown("suspended".into()))]);
    run_setup(&mut unsigned, &sessions()).unwrap();
    assert_eq!(
        unsigned.called("store_webhook_secret"),
        ["store_webhook_secret lin_wh_from_env"]
    );
}

#[test]
fn sets_up_nothing_without_handled_events() {
    let mut env = Fake::new();
    run_setup(&mut env, &channel(&[])).unwrap();
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
    let installed = || app("conn-1", None, AgentGrant::Active);
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
}
