use std::collections::{BTreeMap, BTreeSet, VecDeque};

use serde_json::json;

use super::manifest::PermissionLevel;
use super::*;
use crate::langsmith::github_app_setup::AppMetadata;

const DEPLOYMENT: &str = "0f7d5c42-f907-4b4f-b8ae-ff8801592265";

fn channel() -> GitHubChannel {
    GitHubChannel {
        name: "github".into(),
        events_path: "/channels/github/events".into(),
        handled: vec!["issues.opened".into()],
        events: vec!["issues".into()],
        permissions: BTreeMap::from([
            ("issues".to_string(), PermissionLevel::Write),
            ("metadata".to_string(), PermissionLevel::Read),
        ]),
        app_name: "acme-triage".into(),
        repositories: vec![],
        event_permissions: vec![],
    }
}

fn status(installed: bool) -> GitHubChannelStatus {
    let installations = if installed {
        json!([{
            "id": 9, "account": { "login": "acme", "type": "Organization" },
            "repository_selection": "selected", "repositories": ["acme/api"],
            "permissions": { "issues": "write", "metadata": "read" }
        }])
    } else {
        json!([])
    };
    serde_json::from_value(json!({
        "app": {
            "id": 42, "slug": "acme-triage",
            "owner": { "login": "acme", "type": "Organization" },
            "permissions": { "issues": "write", "metadata": "read" },
            "events": ["issues"],
            "webhook_url": WEBHOOK
        },
        "installations": installations
    }))
    .unwrap()
}

const SESSION: &str = "99999999-8888-7777-6666-555555555555";
const APP: &str = "33333333-4444-5555-6666-777777777777";
const CONNECTION: &str = "11111111-2222-3333-4444-555555555555";
const WEBHOOK: &str = "https://agent.example/channels/github/events";
const OTHER_WEBHOOK: &str = "https://ci.example/hooks/github";
const PROVIDER: &str = "22222222-3333-4444-5555-666666666666";

fn session(status: &str) -> OAuthAuthorizationSession {
    OAuthAuthorizationSession {
        id: SESSION.into(),
        status: status.into(),
        verification_url: "https://aa.example/v1/agent-auth/github-app/start?state=s".into(),
        error_code: None,
    }
}

fn failed(code: &str) -> OAuthAuthorizationSession {
    OAuthAuthorizationSession {
        error_code: Some(code.into()),
        ..session("failed")
    }
}

/// The agent's App in Agent Auth. A brought App's webhook starts out at
/// whatever served it before; Agent Auth points an App it creates here.
fn app(state: GitHubAppState) -> AgentAuthApp {
    let (source, created, webhook) = match state {
        GitHubAppState::Pending => ("manifest", false, WEBHOOK),
        GitHubAppState::Created => ("manifest", true, WEBHOOK),
        GitHubAppState::External => ("existing", true, OTHER_WEBHOOK),
    };
    serde_json::from_value(json!({
        "id": APP, "provider": "github", "source": source, "slug": SLUGS.connection(DEPLOYMENT),
        "app_id": created.then_some("42"), "connection_id": created.then_some(CONNECTION),
        "manifest": { "webhook_url": webhook },
        "app_metadata": created.then(|| json!({
            "slug": "acme-triage", "html_url": "https://github.com/apps/acme-triage"
        })),
    }))
    .unwrap()
}

fn connection(state: GitHubAppState, installed: bool) -> AppConnection {
    AppConnection {
        app: app(state),
        provider_id: PROVIDER.into(),
        state,
        scopes: vec!["issues:write".into(), "metadata:read".into()],
        installed,
        webhook_secret_stored: true,
        bot_id_stored: true,
        kept_webhooks: BTreeSet::new(),
    }
}

/// An App the user brought, before deploy stored its webhook secret and bot ID.
fn brought(installed: bool) -> AppConnection {
    AppConnection {
        webhook_secret_stored: false,
        bot_id_stored: false,
        ..connection(GitHubAppState::External, installed)
    }
}

#[derive(Default)]
struct FakeEnv {
    interactive: bool,
    connection: Option<AppConnection>,
    /// What app_connection reports once a setup step has failed.
    after_failure: Option<AppConnection>,
    source: Option<AppSource>,
    /// What the CLI-only env vars choose, so nobody is asked.
    env_source: Option<AppSource>,
    start_errors: VecDeque<anyhow::Error>,
    started: Option<OAuthAuthorizationSession>,
    polls: VecDeque<OAuthAuthorizationSession>,
    /// Ctrl-C while waiting for the session.
    interrupt_polls: bool,
    syncs: VecDeque<GitHubSyncOutcome>,
    ticks: VecDeque<WaitTick>,
    retries: VecDeque<bool>,
    scope_errors: VecDeque<anyhow::Error>,
    /// What webhook_secret returns; the fake never logs the value.
    webhook_secret: Option<String>,
    owner_error: Option<anyhow::Error>,
    /// The answer to replacing a webhook that points elsewhere.
    replace_webhook: bool,
    /// Escape or Ctrl-C at the webhook prompt.
    cancel_webhook_replace: bool,
    /// `MDA_GITHUB_WEBHOOK_ELSEWHERE`.
    webhook_elsewhere: Option<String>,
    /// `MDA_GITHUB_APP_INSTALLATION_ID`.
    installation_env: Option<String>,
    log: Vec<String>,
}

impl FakeEnv {
    fn interactive() -> Self {
        Self {
            interactive: true,
            webhook_secret: Some("whsec".into()),
            ..Self::default()
        }
    }
}

impl SetupUi for FakeEnv {
    fn interactive(&self) -> bool {
        self.interactive
    }

    fn retry_or_skip(&mut self, error: &anyhow::Error) -> Result<bool> {
        self.log.push(format!("retry? {error}"));
        if let Some(next) = self.after_failure.take() {
            self.connection = Some(next);
        }
        Ok(self.retries.pop_front().expect("unexpected retry prompt"))
    }

    fn success(&mut self, message: &str) -> Result<()> {
        self.log.push(format!("success: {message}"));
        Ok(())
    }

    fn info(&mut self, message: &str) -> Result<()> {
        self.log.push(format!("info: {message}"));
        Ok(())
    }

    fn warning(&mut self, message: &str) -> Result<()> {
        self.log.push(format!("warning: {message}"));
        Ok(())
    }
}

impl SetupEnv for FakeEnv {
    fn app_connection(&mut self) -> Result<Option<AppConnection>> {
        Ok(self.connection.clone())
    }

    fn choose_app_source(&mut self) -> Result<AppSource> {
        self.log.push("choose".into());
        Ok(self.source.expect("app source"))
    }

    fn app_source_from_env(&self) -> Option<AppSource> {
        self.env_source
    }

    fn create_app(
        &mut self,
        channel: &GitHubChannel,
        owner: &AppOwner,
    ) -> Result<(AppConnection, OAuthAuthorizationSession)> {
        self.log
            .push(format!("create {} for {owner:?}", channel.app_name));
        self.connection = Some(connection(GitHubAppState::Pending, false));
        let session = self.started.clone().unwrap_or_else(|| session("pending"));
        Ok((connection(GitHubAppState::Pending, false), session))
    }

    fn store_existing_app(&mut self) -> Result<AppConnection> {
        self.log.push("existing".into());
        Ok(brought(false))
    }

    fn choose_owner(&mut self) -> Result<AppOwner> {
        self.log.push("owner".into());
        match self.owner_error.take() {
            Some(error) => Err(error),
            None => Ok(AppOwner::Organization("acme".into())),
        }
    }

    fn ask_installation_id(&mut self) -> Result<String> {
        self.log.push("installation".into());
        Ok("777".into())
    }

    fn installation_from_env(&self) -> Option<String> {
        self.installation_env.clone()
    }

    fn choose_installation(&mut self, installations: &[GitHubInstallation]) -> Result<String> {
        let ids: Vec<String> = installations.iter().map(|i| i.id.to_string()).collect();
        self.log
            .push(format!("choose installation {}", ids.join(",")));
        Ok(ids.last().cloned().unwrap_or_default())
    }

    fn webhook_secret(&mut self) -> Result<Option<String>> {
        self.log.push("webhook secret".into());
        Ok(self.webhook_secret.clone())
    }

    fn store_app_value(&mut self, suffix: &str, value: &str) -> Result<()> {
        assert!(!value.is_empty());
        self.log.push(format!("store {suffix}"));
        Ok(())
    }

    fn start_session(
        &mut self,
        connection: &AppConnection,
        start: &GitHubAppSessionStart,
    ) -> Result<OAuthAuthorizationSession> {
        self.log
            .push(format!("start {} {start:?}", connection.app.id));
        if let Some(error) = self.start_errors.pop_front() {
            return Err(error);
        }
        // Recording an installation completes at once, like Agent Auth's.
        Ok(self.started.clone().unwrap_or_else(|| match start {
            GitHubAppSessionStart::Installation(_) => session("completed"),
            _ => session("pending"),
        }))
    }

    fn open_setup_page(&mut self, url: &str, creating: bool) -> Result<()> {
        self.log.push(format!("open {url} creating={creating}"));
        Ok(())
    }

    fn next_session(&mut self, session_id: &str) -> Result<OAuthAuthorizationSession> {
        self.log.push(format!("poll {session_id}"));
        if self.interrupt_polls {
            return Err(std::io::Error::from(std::io::ErrorKind::Interrupted).into());
        }
        Ok(self.polls.pop_front().expect("unexpected poll"))
    }

    fn session_status(&mut self, session_id: &str) -> Result<String> {
        self.log.push(format!("status {session_id}"));
        Ok(self.polls.pop_front().expect("unexpected status").status)
    }

    fn update_token_scopes(&mut self, provider_id: &str, scopes: &[String]) -> Result<()> {
        self.log
            .push(format!("scopes {provider_id} {}", scopes.join(",")));
        match self.scope_errors.pop_front() {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }

    fn sync(&mut self) -> GitHubSyncOutcome {
        self.log.push("sync".into());
        self.syncs.pop_front().expect("unexpected sync")
    }

    fn webhook_url(&self) -> &str {
        WEBHOOK
    }

    fn confirm_webhook_replace(&mut self, current: &str) -> Result<bool> {
        self.log.push(format!("replace webhook {current}?"));
        if self.cancel_webhook_replace {
            return Err(
                anyhow::Error::new(std::io::Error::from(std::io::ErrorKind::Interrupted))
                    .context("failed to confirm the GitHub App webhook"),
            );
        }
        Ok(self.replace_webhook)
    }

    fn point_webhook(&mut self) -> Result<()> {
        self.log.push("point webhook".into());
        Ok(())
    }

    fn webhook_elsewhere_override(&self) -> Option<&str> {
        self.webhook_elsewhere.as_deref()
    }

    fn forget_kept_webhooks(&mut self) -> Result<()> {
        self.log.push("forget kept webhooks".into());
        Ok(())
    }

    fn open_install_page(&mut self, url: &str) -> Result<()> {
        self.log.push(format!("open install {url}"));
        Ok(())
    }

    fn wait(&mut self, _duration: Duration) -> WaitTick {
        self.ticks.pop_front().unwrap_or(WaitTick::Elapsed)
    }
}

const READY: &str =
    "success: GitHub channel ready · App acme-triage (acme) · installed on acme/api";
const SET_UP: &str = "success: The GitHub App is set up for this agent.";
/// The token summary once the agent's token has what the channel needs.
const TOKEN: &str = "info: The agent's GitHub token has:\n  issues  write  from app.permissions";

#[test]
fn an_installed_app_syncs_and_reports_without_prompting() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Created, true));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec!["sync".to_string(), READY.to_string(), TOKEN.to_string()]
    );
}

#[test]
fn without_a_terminal_setup_prints_the_link_and_waits() {
    // The fake's owner stands in for MDA_GITHUB_APP_OWNER.
    let mut env = FakeEnv {
        env_source: Some(AppSource::New),
        polls: VecDeque::from([session("completed")]),
        syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(status(true)))]),
        ..FakeEnv::default()
    };

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        &env.log[..3],
        &[
            "owner".to_string(),
            format!(
                "create acme-triage for {:?}",
                AppOwner::Organization("acme".into())
            ),
            "open https://aa.example/v1/agent-auth/github-app/start?state=s creating=true"
                .to_string(),
        ]
    );
    assert!(env.log.contains(&SET_UP.to_string()));
}

#[test]
fn env_credentials_skip_the_app_prompt_in_a_terminal_too() {
    for (source, step) in [(AppSource::New, "owner"), (AppSource::Existing, "existing")] {
        let mut env = FakeEnv::interactive();
        env.env_source = Some(source);
        env.started = Some(session("completed"));
        env.replace_webhook = true;
        env.syncs = VecDeque::from([
            GitHubSyncOutcome::Synced(Box::new(status(true))),
            GitHubSyncOutcome::Synced(Box::new(status(true))),
        ]);

        run_setup(&mut env, &channel()).unwrap();

        assert!(!env.log.contains(&"choose".to_string()), "{:?}", env.log);
        assert_eq!(env.log[0], step, "{:?}", env.log);
    }
}

#[test]
fn without_a_terminal_a_missing_owner_fails_before_anything_is_created() {
    let mut env = FakeEnv {
        owner_error: Some(anyhow::anyhow!(OWNER_REQUIRED)),
        ..FakeEnv::default()
    };

    let error = run_setup(&mut env, &channel()).unwrap_err().to_string();

    assert_eq!(error, OWNER_REQUIRED);
    // No question, no retry prompt and no pending connection left behind.
    assert_eq!(env.log, vec!["owner".to_string()]);
}

#[test]
fn the_owner_setting_names_an_organization_or_the_personal_account() {
    assert_eq!(
        parse_owner(" acme ").unwrap(),
        AppOwner::Organization("acme".into())
    );
    assert_eq!(parse_owner("@me").unwrap(), AppOwner::Personal);
    for invalid in ["", "-acme", "acme/web", "@you"] {
        assert!(parse_owner(invalid).is_err(), "{invalid}");
    }
}

#[test]
fn a_new_app_is_created_and_installed_through_one_session() {
    let mut env = FakeEnv::interactive();
    env.source = Some(AppSource::New);
    env.polls.push_back(session("pending"));
    env.polls.push_back(session("completed"));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec![
            "choose".to_string(),
            "owner".to_string(),
            format!(
                "create acme-triage for {:?}",
                AppOwner::Organization("acme".into())
            ),
            "open https://aa.example/v1/agent-auth/github-app/start?state=s creating=true"
                .to_string(),
            format!("poll {SESSION}"),
            format!("poll {SESSION}"),
            SET_UP.to_string(),
            "sync".to_string(),
            READY.to_string(),
            TOKEN.to_string(),
        ]
    );
}

#[test]
fn an_abandoned_setup_resumes_on_its_connection() {
    for (state, start, creating) in [
        (
            GitHubAppState::Pending,
            "Create { organization: Some(\"acme\") }",
            true,
        ),
        (GitHubAppState::Created, "Install", false),
    ] {
        let mut env = FakeEnv::interactive();
        env.connection = Some(connection(state, false));
        env.polls.push_back(session("completed"));
        env.syncs
            .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));

        run_setup(&mut env, &channel()).unwrap();

        assert!(!env.log.contains(&"choose".to_string()), "{state:?}");
        assert!(
            env.log.contains(&format!("start {APP} {start}")),
            "{state:?}"
        );
        assert!(
            env.log
                .iter()
                .any(|line| line.ends_with(&format!("creating={creating}"))),
            "{state:?}"
        );
    }
}

#[test]
fn an_existing_app_completes_with_the_installation_github_reports() {
    let mut env = FakeEnv::interactive();
    env.source = Some(AppSource::Existing);
    env.started = Some(session("completed"));
    env.syncs = VecDeque::from([
        GitHubSyncOutcome::Synced(Box::new(status(true))),
        GitHubSyncOutcome::Synced(Box::new(status(true))),
    ]);
    env.replace_webhook = true;

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        &env.log[..5],
        &[
            "choose".to_string(),
            "existing".to_string(),
            "sync".to_string(),
            format!("start {APP} Installation(\"9\")"),
            SET_UP.to_string(),
        ]
    );
    // The status reported no bot ID, so deploy says why it could not store one.
    assert!(env
        .log
        .iter()
        .any(|line| line.starts_with("warning: Could not store the GitHub App's bot ID")));
    assert!(!env.log.iter().any(|line| line.contains(".env")));
}

#[test]
fn a_brought_app_gets_its_bot_id_from_the_report() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(AppConnection {
        webhook_secret_stored: true,
        ..brought(true)
    });
    let mut reported = status(true);
    reported.app.bot_id = Some(4242);
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(reported)));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            READY.to_string(),
            format!("store {BOT_ID_SUFFIX}"),
            TOKEN.to_string(),
        ]
    );
}

#[test]
fn a_brought_app_without_a_stored_webhook_secret_gets_one() {
    // Set up before deploy stored brought Apps' secrets.
    let mut env = FakeEnv::interactive();
    env.connection = Some(AppConnection {
        bot_id_stored: true,
        ..brought(true)
    });
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    run_setup(&mut env, &channel()).unwrap();
    assert!(env.log.ends_with(&[
        "webhook secret".to_string(),
        format!("store {WEBHOOK_SECRET_SUFFIX}"),
        TOKEN.to_string(),
    ]));

    // Nobody to ask: the deploy still succeeds and says what to do.
    let mut env = FakeEnv {
        connection: Some(AppConnection {
            bot_id_stored: true,
            ..brought(true)
        }),
        syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(status(true)))]),
        ..FakeEnv::default()
    };
    run_setup(&mut env, &channel()).unwrap();
    assert!(!env.log.iter().any(|line| line.starts_with("store")));
    assert!(env
        .log
        .iter()
        .any(|line| line.contains("Agent Auth has no webhook secret for this GitHub App")));
}

#[test]
fn a_failed_or_expired_session_offers_retry() {
    for status in ["failed", "expired"] {
        let mut env = FakeEnv::interactive();
        env.source = Some(AppSource::New);
        env.polls.push_back(session(status));
        env.retries.push_back(false);

        run_setup(&mut env, &channel()).unwrap();

        assert!(
            env.log.iter().any(|line| line.starts_with("retry? ")),
            "{status}"
        );
        assert_eq!(
            env.log.last().unwrap(),
            &format!("warning: {SKIPPED_SETUP}")
        );
    }
}

#[test]
fn apps_agent_auth_could_not_store_are_named_for_deletion() {
    // GitHub created Apps that Agent Auth could not store, on this attempt
    // and an earlier one: the pending app lists them, oldest first.
    let mut pending = connection(GitHubAppState::Pending, false);
    pending.app.stranded_apps = ["acme-triage", "acme-triage-2"]
        .map(|slug| AppMetadata {
            slug: slug.into(),
            html_url: format!("https://github.com/apps/{slug}"),
        })
        .into();
    let mut env = FakeEnv::interactive();
    env.connection = Some(pending);
    env.polls.push_back(failed("app_store_failed"));
    env.retries.push_back(false);

    run_setup(&mut env, &channel()).unwrap();

    let retry = env
        .log
        .iter()
        .find(|line| line.starts_with("retry? "))
        .expect("retry prompt");
    assert!(
        retry.contains("Agent Auth could not store it. Delete it on GitHub"),
        "{retry}"
    );
    assert!(
        retry.ends_with(
            "\nhttps://github.com/apps/acme-triage\nhttps://github.com/apps/acme-triage-2"
        ),
        "{retry}"
    );
}

#[test]
fn failure_messages_for_each_error_code() {
    assert!(setup_failure_message(Some("app_creation_denied"), &[])
        .starts_with("GitHub did not create"));
    assert!(setup_failure_message(Some("conversion_failed"), &[]).contains("one-time code"));
    assert!(setup_failure_message(Some("connection_failed"), &[])
        .starts_with("Agent Auth could not look up the GitHub App"));
    assert_eq!(
        setup_failure_message(Some("app_store_failed"), &[]),
        "The App was created on GitHub, but Agent Auth could not store it. Delete it on GitHub before trying again."
    );
    assert!(setup_failure_message(
        Some("app_store_failed"),
        &["https://github.com/apps/acme-triage".into()]
    )
    .ends_with("before trying again.\nhttps://github.com/apps/acme-triage"));
    assert_eq!(
        setup_failure_message(Some("new_code"), &[]),
        "GitHub App setup failed (new_code)."
    );
    assert!(setup_failure_message(None, &[]).starts_with("GitHub App setup failed."));
}

#[test]
fn a_retry_starts_a_new_session_on_the_app_it_created() {
    let mut env = FakeEnv::interactive();
    env.source = Some(AppSource::New);
    env.polls.push_back(session("expired"));
    env.retries.push_back(true);
    env.polls.push_back(session("completed"));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));

    run_setup(&mut env, &channel()).unwrap();

    let created = env
        .log
        .iter()
        .filter(|line| line.starts_with("create "))
        .count();
    assert_eq!(created, 1, "{:?}", env.log);
    assert!(
        env.log.contains(&format!(
            "start {APP} Create {{ organization: Some(\"acme\") }}"
        )),
        "{:?}",
        env.log
    );
    assert!(env.log.ends_with(&[READY.to_string(), TOKEN.to_string()]));
}

fn brought_and_started() -> FakeEnv {
    let mut env = FakeEnv::interactive();
    env.connection = Some(AppConnection {
        webhook_secret_stored: true,
        ..brought(false)
    });
    env.started = Some(session("completed"));
    env
}

/// A brought App that GitHub reports with no installation.
fn uninstalled_brought_app() -> FakeEnv {
    let mut env = FakeEnv::interactive();
    env.connection = Some(AppConnection {
        webhook_secret_stored: true,
        ..brought(false)
    });
    env
}

#[test]
fn an_uninstalled_brought_app_installs_through_an_install_session() {
    let mut env = uninstalled_brought_app();
    env.polls = VecDeque::from([session("completed")]);
    env.syncs = VecDeque::from([
        GitHubSyncOutcome::Synced(Box::new(status(false))),
        GitHubSyncOutcome::Synced(Box::new(status(true))),
    ]);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        &env.log[..4],
        &[
            "sync".to_string(),
            format!("start {APP} Install"),
            "open install https://aa.example/v1/agent-auth/github-app/start?state=s".to_string(),
            format!("status {SESSION}"),
        ]
    );
    assert!(!env.log.iter().any(|line| line.contains("Installation(")));
}

#[test]
fn an_install_github_reports_first_is_recorded_without_the_session() {
    // An App whose setup URL does not lead back to Agent Auth never completes
    // the install session.
    let mut env = uninstalled_brought_app();
    env.polls = VecDeque::from([session("pending")]);
    env.syncs = VecDeque::from([
        GitHubSyncOutcome::Synced(Box::new(status(false))),
        GitHubSyncOutcome::Synced(Box::new(status(true))),
        GitHubSyncOutcome::Synced(Box::new(status(true))),
    ]);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        &env.log[..6],
        &[
            "sync".to_string(),
            format!("start {APP} Install"),
            "open install https://aa.example/v1/agent-auth/github-app/start?state=s".to_string(),
            format!("status {SESSION}"),
            "sync".to_string(),
            format!("start {APP} Installation(\"9\")"),
        ]
    );
}

#[test]
fn ctrl_c_while_a_brought_app_installs_skips_the_channel() {
    let mut env = uninstalled_brought_app();
    env.ticks = VecDeque::from([WaitTick::Interrupted]);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(status(false)))]);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log.last().unwrap(),
        &format!("warning: {SKIPPED_SETUP}")
    );
}

#[test]
fn an_existing_app_in_several_accounts_lets_the_user_pick_one() {
    let mut env = brought_and_started();
    let mut several = status(true);
    let mut other = several.installations[0].clone();
    other.id = 10;
    other.account.login = "octo".into();
    several.installations.push(other);
    env.syncs = VecDeque::from([
        GitHubSyncOutcome::Synced(Box::new(several.clone())),
        GitHubSyncOutcome::Synced(Box::new(several)),
    ]);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        &env.log[..3],
        &[
            "sync".to_string(),
            "choose installation 9,10".to_string(),
            format!("start {APP} Installation(\"10\")"),
        ]
    );
}

#[test]
fn an_existing_app_in_several_accounts_takes_the_installation_from_the_env() {
    let mut env = brought_and_started();
    assert!(env.interactive);
    env.installation_env = Some("10".into());
    let mut several = status(true);
    let mut other = several.installations[0].clone();
    other.id = 10;
    several.installations.push(other);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(several))]);

    run_setup(&mut env, &channel()).unwrap();

    // In a terminal too, MDA_GITHUB_APP_INSTALLATION_ID picks it: nobody is asked.
    assert_eq!(env.log[0], format!("start {APP} Installation(\"10\")"));
    assert!(
        !env.log
            .iter()
            .any(|line| line.starts_with("choose installation")),
        "{:?}",
        env.log
    );
}

#[test]
fn an_existing_app_asks_for_its_installation_only_when_github_cannot_be_asked() {
    let mut env = brought_and_started();
    env.syncs = (0..SYNC_ATTEMPTS)
        .map(|_| GitHubSyncOutcome::Unavailable("timed out".into()))
        .chain([GitHubSyncOutcome::Synced(Box::new(status(true)))])
        .collect();

    run_setup(&mut env, &channel()).unwrap();

    let asked = env
        .log
        .iter()
        .position(|line| line == "installation")
        .unwrap();
    assert_eq!(
        env.log[..asked]
            .iter()
            .filter(|line| *line == "sync")
            .count(),
        SYNC_ATTEMPTS
    );
    assert_eq!(
        env.log[asked + 1],
        format!("start {APP} Installation(\"777\")")
    );
}

#[test]
fn ctrl_c_while_waiting_skips_without_asking() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Created, false));
    env.interrupt_polls = true;

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log.last().unwrap(),
        &format!("warning: {SKIPPED_SETUP}")
    );
    assert!(!env.log.iter().any(|line| line.starts_with("retry?")));
}

#[test]
fn uninstalled_app_waits_for_installation_when_agent_auth_reports() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Created, true));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(false))));
    env.ticks.push_back(WaitTick::Interrupted);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            "open install https://github.com/apps/acme-triage/installations/new"
                .to_string(),
            "warning: Skipped waiting for the GitHub App to be installed.".to_string(),
            "warning: GitHub App acme-triage is not installed yet. Install it on your repositories at https://github.com/apps/acme-triage/installations/new".to_string(),
        ]
    );
}

#[test]
fn the_manifest_comes_from_the_channel() {
    let manifest = app_manifest(
        &channel(),
        "https://agent.example",
        "https://agent.example/channels/github/events",
    );
    assert_eq!(manifest.name, "acme-triage");
    assert_eq!(manifest.url, "https://agent.example");
    assert_eq!(manifest.events, vec!["issues".to_string()]);
    assert_eq!(
        manifest.permissions,
        BTreeMap::from([
            ("issues".to_string(), "write".to_string()),
            ("metadata".to_string(), "read".to_string()),
        ])
    );
    assert_eq!(
        SLUGS.connection(DEPLOYMENT),
        format!("mda/github-app/{DEPLOYMENT}")
    );
    assert_eq!(
        permission_scopes(&channel()),
        vec!["issues:write".to_string(), "metadata:read".to_string()]
    );
}

#[test]
fn github_id_parsing() {
    assert_eq!(parse_github_id(" 123 ", APP_ID_HINT).unwrap(), 123);
    for invalid in ["", "0", "-1", "12a", "+5"] {
        assert!(parse_github_id(invalid, APP_ID_HINT).is_err(), "{invalid}");
    }
    // Each prompt says which ID it wants.
    assert_eq!(
        parse_github_id("abc", INSTALLATION_ID_HINT)
            .unwrap_err()
            .to_string(),
        INSTALLATION_ID_HINT
    );
}

#[test]
fn private_key_file_reading() {
    const PEM: &str = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n";
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("app.pem");
    std::fs::write(&key, PEM).unwrap();
    assert_eq!(read_private_key(&key).unwrap().expose(), PEM);

    let not_key = dir.path().join("notes.txt");
    std::fs::write(&not_key, "hello").unwrap();
    assert!(read_private_key(&not_key).is_err());
    assert!(read_private_key(&dir.path().join("missing.pem")).is_err());
    assert!(read_private_key(dir.path()).is_err());
}

#[test]
fn github_owner_from_remote_urls() {
    for (remote, owner) in [
        ("git@github.com:acme/agent.git", Some("acme")),
        ("https://github.com/acme/agent", Some("acme")),
        ("ssh://git@github.com/acme-inc/agent.git", Some("acme-inc")),
        ("https://gitlab.com/acme/agent", None),
        ("git@github.com:-bad/agent.git", None),
    ] {
        assert_eq!(parse_github_owner(remote).as_deref(), owner, "{remote}");
    }
}

#[test]
fn a_channel_without_handlers_skips_app_setup() {
    let mut env = FakeEnv::interactive();
    let mut receipt_only = channel();
    receipt_only.handled.clear();
    receipt_only.events.clear();

    run_setup(&mut env, &receipt_only).unwrap();
    assert_eq!(env.log, vec![format!("info: {NO_HANDLERS}")]);
}

#[test]
fn sync_retries_while_agent_auth_is_unavailable() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Created, true));
    env.syncs
        .push_back(GitHubSyncOutcome::Unavailable("timeout".into()));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            "info: Retrying the GitHub App check…".to_string(),
            "sync".to_string(),
            READY.to_string(),
            TOKEN.to_string(),
        ]
    );
}

#[test]
fn a_retry_picks_up_an_app_github_created_meanwhile() {
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Pending, false));
    env.polls.push_back(session("expired"));
    env.retries.push_back(true);
    env.polls.push_back(session("completed"));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    // Agent Auth filled the provider before the first session expired.
    env.after_failure = Some(connection(GitHubAppState::Created, false));

    run_setup(&mut env, &channel()).unwrap();

    let starts: Vec<&String> = env
        .log
        .iter()
        .filter(|line| line.starts_with("start "))
        .collect();
    assert_eq!(starts.len(), 2, "{:?}", env.log);
    assert!(starts[1].ends_with(" Install"), "{:?}", env.log);
}

/// An installed App whose token was minted for fewer permissions than the
/// channel now needs.
fn drifted() -> FakeEnv {
    let mut env = FakeEnv::interactive();
    let mut installed = connection(GitHubAppState::Created, true);
    installed.scopes = vec!["issues:read".into(), "metadata:read".into()];
    env.connection = Some(installed);
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    env
}

#[test]
fn a_granted_permission_change_renews_the_token() {
    let mut env = drifted();
    env.started = Some(session("completed"));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            READY.to_string(),
            format!("scopes {PROVIDER} issues:write,metadata:read"),
            format!("start {APP} Installation(\"9\")"),
            "info: The agent's GitHub token now has:\n  issues  write  from app.permissions"
                .to_string(),
        ]
    );
}

#[test]
fn an_ungranted_permission_change_keeps_the_token() {
    let mut env = drifted();
    let mut ungranted = status(true);
    ungranted.installations[0]
        .permissions
        .insert("issues".into(), "read".into());
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(ungranted))]);

    run_setup(&mut env, &channel()).unwrap();

    assert!(
        !env.log.iter().any(|line| line.starts_with("scopes")),
        "{:?}",
        env.log
    );
    assert_eq!(
        *env.log.last().unwrap(),
        [
            "info: The agent's GitHub token has:",
            "  issues  read   from app.permissions",
            "",
            "Waiting for GitHub:",
            "  issues  write  from app.permissions",
            "",
            "1. An owner of acme accepts them at https://github.com/organizations/acme/settings/installations/9",
            "2. Run `mda deploy` again.",
        ]
        .join("\n")
    );
}

#[test]
fn a_failed_token_renewal_only_warns() {
    // The scopes could not change, so the token is as it was.
    let mut env = drifted();
    env.scope_errors
        .push_back(anyhow::anyhow!("Agent Auth unavailable"));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log.last().unwrap(),
        "warning: Could not update the agent's GitHub token (Agent Auth unavailable), so it keeps its current permissions. Run `mda deploy` again to retry."
    );
}

#[test]
fn a_failed_token_renewal_restores_the_old_scopes() {
    // Changing the scopes dropped the token: the old scopes go back and the
    // token is renewed with them, so the next deploy retries the change.
    let mut env = drifted();
    env.start_errors
        .push_back(anyhow::anyhow!("session failed"));
    env.started = Some(session("completed"));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log[2..],
        [
            format!("scopes {PROVIDER} issues:write,metadata:read"),
            format!("start {APP} Installation(\"9\")"),
            format!("scopes {PROVIDER} issues:read,metadata:read"),
            format!("start {APP} Installation(\"9\")"),
            "warning: Could not update the agent's GitHub token (session failed), so it keeps its current permissions. Run `mda deploy` again to retry.".to_string(),
        ]
    );

    // Restoring failed too: the warning says the agent has no token.
    let mut env = drifted();
    env.started = Some(session("pending"));
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log.last().unwrap(),
        "warning: Could not update the agent's GitHub token (Agent Auth did not renew the token), and the agent has no GitHub token until it is renewed. Run `mda deploy` again to set it up."
    );
}

/// An installed App whose token was minted for more permissions than the
/// channel now needs: contents:write is dropped.
fn narrowed() -> FakeEnv {
    let mut env = FakeEnv::interactive();
    let mut installed = connection(GitHubAppState::Created, true);
    installed.scopes = vec![
        "contents:write".into(),
        "issues:write".into(),
        "metadata:read".into(),
    ];
    env.connection = Some(installed);
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    env
}

#[test]
fn a_narrowing_does_not_wait_for_ungranted_permissions() {
    // The channel drops contents:write and adds pull_requests:write, which
    // the installation has not granted: the token narrows to what it has.
    let mut wider = channel();
    wider
        .permissions
        .insert("pull_requests".into(), PermissionLevel::Write);
    let mut env = narrowed();
    env.started = Some(session("completed"));

    run_setup(&mut env, &wider).unwrap();

    let token: Vec<&String> = env
        .log
        .iter()
        .filter(|line| {
            line.starts_with("scopes ")
                || line.starts_with("start ")
                || line.contains("agent's GitHub token")
        })
        .collect();
    assert_eq!(
        token,
        [
            &format!("scopes {PROVIDER} issues:write,metadata:read"),
            &format!("start {APP} Installation(\"9\")"),
            &[
                "info: The agent's GitHub token now has:",
                "  issues         write  from app.permissions",
                "",
                "Waiting for GitHub:",
                "  pull_requests  write  from app.permissions",
                "",
                "1. The App owner grants pull_requests (write) at https://github.com/organizations/acme/settings/apps/acme-triage/permissions",
                "2. An owner of acme accepts them at https://github.com/organizations/acme/settings/installations/9",
                "3. Run `mda deploy` again.",
            ]
            .join("\n"),
        ]
    );
}

#[test]
fn a_failed_narrowing_never_restores_the_broader_token() {
    let mut env = narrowed();
    env.start_errors
        .push_back(anyhow::anyhow!("session failed"));
    env.started = Some(session("completed"));

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log[2..],
        [
            format!("scopes {PROVIDER} issues:write,metadata:read"),
            format!("start {APP} Installation(\"9\")"),
            "warning: Could not update the agent's GitHub token (session failed), and the agent has no GitHub token until it is renewed. Run `mda deploy` again to set it up.".to_string(),
        ]
    );

    // Agent Auth refused the narrower scopes, so the old token is still
    // there, and the warning says it is broader.
    let mut env = narrowed();
    env.scope_errors
        .push_back(anyhow::anyhow!("Agent Auth unavailable"));
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log.last().unwrap(),
        "warning: Could not update the agent's GitHub token (Agent Auth unavailable), so it still has its previous, broader permissions. Run `mda deploy` again to retry."
    );
}

#[test]
fn a_narrowing_drops_the_token_it_cannot_renew() {
    // With two installations there is no one installation to mint for, so
    // the broader token goes and none replaces it.
    let mut env = narrowed();
    let mut twice = status(true);
    let mut other = twice.installations[0].clone();
    other.id = 10;
    twice.installations.push(other);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(twice))]);

    run_setup(&mut env, &channel()).unwrap();

    assert_eq!(
        env.log[2..],
        [
            format!("scopes {PROVIDER} issues:write,metadata:read"),
            "warning: The GitHub App is installed more than once, so the agent's GitHub token cannot be renewed with fewer permissions, and it may not keep its broader ones, so the agent has no GitHub token for now. Run `mda deploy` again once that is fixed.".to_string(),
        ]
    );

    // The installation grants none of the narrower permissions either.
    let mut env = narrowed();
    let mut bare = status(true);
    bare.installations[0].permissions.clear();
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(bare))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(
        !env.log.iter().any(|line| line.starts_with("start ")),
        "{:?}",
        env.log
    );
    assert!(env
        .log
        .last()
        .unwrap()
        .contains("the agent has no GitHub token"));
}

#[test]
fn several_installations_keep_the_token() {
    let mut env = drifted();
    let mut twice = status(true);
    let mut other = twice.installations[0].clone();
    other.id = 10;
    twice.installations.push(other);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(twice))]);

    run_setup(&mut env, &channel()).unwrap();

    assert!(
        !env.log.iter().any(|line| line.starts_with("scopes")),
        "{:?}",
        env.log
    );
    assert!(env.log.last().unwrap().contains("installed more than once"));
}

const TEST_KEY: &str = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----";

fn existing_env() -> ExistingAppEnv {
    ExistingAppEnv {
        app_id: "42".into(),
        private_key: PrivateKeyPem::parse(TEST_KEY).unwrap(),
        webhook_secret: "whsec".into(),
    }
}

#[test]
fn unattended_setup_needs_an_owner_or_an_existing_app_only_for_a_new_app() {
    let none = AppEnv::default();
    let owner = AppEnv {
        owner: Some(AppOwner::Personal),
        ..AppEnv::default()
    };
    let existing = AppEnv {
        existing: Some(existing_env()),
        ..AppEnv::default()
    };
    let installation = AppEnv {
        installation_id: Some("777".into()),
        ..AppEnv::default()
    };
    // No deployment or connection yet: a new App needs an owner, or the env
    // brings an existing one.
    assert_eq!(unattended_setup_problem(None, &none), Some(OWNER_REQUIRED));
    assert_eq!(unattended_setup_problem(None, &owner), None);
    assert_eq!(unattended_setup_problem(None, &existing), None);
    let pending = connection(GitHubAppState::Pending, false);
    assert_eq!(
        unattended_setup_problem(Some(&pending), &none),
        Some(OWNER_REQUIRED)
    );
    assert_eq!(unattended_setup_problem(Some(&pending), &owner), None);
    // A created App only needs its install link.
    let created = connection(GitHubAppState::Created, false);
    assert_eq!(unattended_setup_problem(Some(&created), &none), None);
    // A brought App needs its installation, from a prompt or the env.
    let brought = connection(GitHubAppState::External, false);
    assert_eq!(
        unattended_setup_problem(Some(&brought), &owner),
        Some(EXISTING_APP_NEEDS_TERMINAL)
    );
    assert_eq!(
        unattended_setup_problem(Some(&brought), &installation),
        None
    );
    for state in [
        GitHubAppState::Pending,
        GitHubAppState::Created,
        GitHubAppState::External,
    ] {
        let installed = connection(state, true);
        assert_eq!(unattended_setup_problem(Some(&installed), &none), None);
    }
}

#[test]
fn the_app_env_vars_are_checked_as_a_group_and_never_echo_values() {
    let dir = tempfile::tempdir().unwrap();
    let key = dir.path().join("app.pem");
    std::fs::write(&key, TEST_KEY).unwrap();
    let not_a_key = dir.path().join("notes.txt");
    std::fs::write(&not_a_key, "hello").unwrap();
    let vars = |pairs: &[(&str, &str)]| {
        pairs
            .iter()
            .map(|(key, value)| (key.to_string(), value.to_string()))
            .collect::<BTreeMap<_, _>>()
    };
    let key_path = key.to_str().unwrap();

    assert!(app_env(&vars(&[])).unwrap().existing.is_none());
    let brought = app_env(&vars(&[
        (APP_ID_ENV, "42"),
        (PRIVATE_KEY_FILE_ENV, key_path),
        (WEBHOOK_SECRET_ENV, "whsec-value"),
        (INSTALLATION_ID_ENV, "777"),
    ]))
    .unwrap();
    let existing = brought.existing.unwrap();
    assert_eq!(existing.app_id, "42");
    assert_eq!(existing.webhook_secret, "whsec-value");
    assert_eq!(brought.installation_id.as_deref(), Some("777"));
    assert!(!format!("{existing:?}").contains("whsec-value"));

    for (pairs, names) in [
        (vec![(APP_ID_ENV, "42")], PRIVATE_KEY_FILE_ENV),
        (
            vec![(APP_ID_ENV, "42"), (PRIVATE_KEY_FILE_ENV, key_path)],
            WEBHOOK_SECRET_ENV,
        ),
        (
            vec![
                (APP_ID_ENV, "app"),
                (PRIVATE_KEY_FILE_ENV, key_path),
                (WEBHOOK_SECRET_ENV, "whsec-value"),
            ],
            APP_ID_ENV,
        ),
        (
            vec![
                (APP_ID_ENV, "42"),
                (PRIVATE_KEY_FILE_ENV, not_a_key.to_str().unwrap()),
                (WEBHOOK_SECRET_ENV, "whsec-value"),
            ],
            PRIVATE_KEY_FILE_ENV,
        ),
        (vec![(INSTALLATION_ID_ENV, "x9")], INSTALLATION_ID_ENV),
        (
            vec![
                (APP_OWNER_ENV, "acme"),
                (APP_ID_ENV, "42"),
                (PRIVATE_KEY_FILE_ENV, key_path),
                (WEBHOOK_SECRET_ENV, "whsec-value"),
            ],
            APP_OWNER_ENV,
        ),
    ] {
        let error = format!("{:#}", app_env(&vars(&pairs)).unwrap_err());
        assert!(error.contains(names), "{names}: {error}");
        assert!(!error.contains("whsec-value"), "{error}");
        assert!(!error.contains("hello"), "{error}");
    }
}

#[test]
fn the_cli_webhook_secret_is_never_uploaded_but_the_runtime_override_is() {
    let env_vars = BTreeMap::from([
        (WEBHOOK_SECRET_ENV.to_string(), "cli".to_string()),
        (APP_ID_ENV.to_string(), "42".to_string()),
        (PRIVATE_KEY_FILE_ENV.to_string(), "app.pem".to_string()),
        (INSTALLATION_ID_ENV.to_string(), "7".to_string()),
        ("GH_WEBHOOK_SECRET".to_string(), "runtime".to_string()),
    ]);
    let secrets = crate::deploy::helpers::secrets_from_env(&env_vars);
    let names: Vec<_> = secrets.iter().map(|secret| secret.name.as_str()).collect();
    assert_eq!(names, ["GH_WEBHOOK_SECRET"]);
}

/// A brought App, installed, whose webhook the report says points at `url`.
fn webhook_at(url: &str) -> (FakeEnv, GitHubChannelStatus) {
    let mut env = FakeEnv::interactive();
    env.connection = Some(AppConnection {
        webhook_secret_stored: true,
        bot_id_stored: true,
        ..brought(true)
    });
    let mut reported = status(true);
    reported.app.webhook_url = Some(url.into());
    (env, reported)
}

#[test]
fn a_brought_apps_webhook_moves_only_with_consent() {
    let (mut env, elsewhere) = webhook_at(OTHER_WEBHOOK);
    env.replace_webhook = true;
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]);
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            format!("replace webhook {OTHER_WEBHOOK}?"),
            "point webhook".to_string(),
            READY.to_string(),
            TOKEN.to_string(),
        ]
    );

    // Declined: deploy remembers to keep it there, and the report still runs.
    let (mut env, elsewhere) = webhook_at(OTHER_WEBHOOK);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(!env.log.contains(&"point webhook".to_string()));
    assert!(env.log.contains(&format!(
        "store {WEBHOOK_ELSEWHERE_SUFFIX}{}",
        webhook_digest(OTHER_WEBHOOK)
    )));
    assert!(env.log.iter().any(|line| line.starts_with("info:")
        && line.contains(&format!("keeps sending its events to {OTHER_WEBHOOK}"))));
    assert!(!env.log.iter().any(|line| line.starts_with("warning:")));

    // Escape or Ctrl-C at the prompt keeps it there for now, without
    // remembering, rather than failing the live deploy.
    let (mut env, elsewhere) = webhook_at(OTHER_WEBHOOK);
    env.cancel_webhook_replace = true;
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(!env.log.contains(&"point webhook".to_string()));
    assert!(!env.log.iter().any(|line| line.starts_with("store")));
    assert!(env.log.iter().any(|line| line.starts_with("warning:")
        && line.contains(&format!("keeps sending its events to {OTHER_WEBHOOK}"))));

    // Already pointing here: nothing to do. Unset: nobody to take it from.
    let (mut env, here) = webhook_at(WEBHOOK);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(here))]);
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log,
        vec!["sync".to_string(), READY.to_string(), TOKEN.to_string()]
    );
    let (mut env, unset) = webhook_at("");
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(unset))]);
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            "point webhook".to_string(),
            READY.to_string(),
            TOKEN.to_string(),
        ]
    );
}

#[test]
fn an_app_agent_auth_points_here_follows_without_asking() {
    // Someone moved the webhook of an App Agent Auth created for this
    // deployment; deploy points it back, also without a terminal.
    let mut env = FakeEnv {
        connection: Some(connection(GitHubAppState::Created, true)),
        ..FakeEnv::default()
    };
    let mut moved = status(true);
    moved.app.webhook_url = Some(OTHER_WEBHOOK.into());
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(moved))]);
    run_setup(&mut env, &channel()).unwrap();
    assert_eq!(
        env.log,
        vec![
            "sync".to_string(),
            "point webhook".to_string(),
            READY.to_string(),
            TOKEN.to_string(),
        ]
    );
}

#[test]
fn without_a_terminal_a_brought_apps_other_webhook_fails_the_deploy() {
    let (env, elsewhere) = webhook_at(OTHER_WEBHOOK);
    let mut env = FakeEnv {
        interactive: false,
        syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]),
        ..env
    };
    let error = run_setup(&mut env, &channel()).unwrap_err().to_string();
    assert_eq!(
        error,
        webhook_elsewhere("acme-triage", OTHER_WEBHOOK, WEBHOOK)
    );
    assert!(!env
        .log
        .iter()
        .any(|line| line.starts_with("replace webhook")));
}

/// [`webhook_at`] for an App an earlier deploy kept at `kept`.
fn kept_at(url: &str, kept: &str) -> (FakeEnv, GitHubChannelStatus) {
    let (mut env, reported) = webhook_at(url);
    if let Some(connection) = env.connection.as_mut() {
        connection.kept_webhooks = BTreeSet::from([webhook_digest(kept)]);
    }
    (env, reported)
}

#[test]
fn a_kept_webhook_is_not_asked_about_again() {
    // With a terminal and without: no question, no failure, one line saying
    // where to forward the events.
    for interactive in [true, false] {
        let (env, elsewhere) = kept_at(OTHER_WEBHOOK, OTHER_WEBHOOK);
        let mut env = FakeEnv {
            interactive,
            syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]),
            ..env
        };
        run_setup(&mut env, &channel()).unwrap();
        assert!(!env
            .log
            .iter()
            .any(|line| line.starts_with("replace webhook")
                || line.starts_with("store")
                || line == "point webhook"));
        assert!(env.log.iter().any(|line| line.starts_with("info:")
            && line.contains(&format!("Forward the ones this agent handles to {WEBHOOK}"))));
    }

    // The webhook moved to yet another receiver: that one wasn't agreed to.
    let (mut env, elsewhere) = kept_at("https://new.example/hook", OTHER_WEBHOOK);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(env
        .log
        .contains(&"replace webhook https://new.example/hook?".to_string()));
}

#[test]
fn a_kept_webhook_is_forgotten_once_it_points_here() {
    // Someone pointed it here on GitHub.
    let (mut env, here) = kept_at(WEBHOOK, OTHER_WEBHOOK);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(here))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(env.log.contains(&"forget kept webhooks".to_string()));

    // Deploy moves it here with consent.
    let (mut env, elsewhere) = kept_at("https://new.example/hook", OTHER_WEBHOOK);
    env.replace_webhook = true;
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]);
    run_setup(&mut env, &channel()).unwrap();
    let point = env.log.iter().position(|line| line == "point webhook");
    let forget = env
        .log
        .iter()
        .position(|line| line == "forget kept webhooks");
    assert!(point.is_some() && forget > point, "{:?}", env.log);

    // Nothing kept: nothing to forget.
    let (mut env, here) = webhook_at(WEBHOOK);
    env.syncs = VecDeque::from([GitHubSyncOutcome::Synced(Box::new(here))]);
    run_setup(&mut env, &channel()).unwrap();
    assert!(!env.log.contains(&"forget kept webhooks".to_string()));
}

#[test]
fn the_env_keeps_a_webhook_without_a_terminal() {
    let (env, elsewhere) = webhook_at(OTHER_WEBHOOK);
    let mut env = FakeEnv {
        interactive: false,
        webhook_elsewhere: Some(format!(" {OTHER_WEBHOOK} ")),
        syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]),
        ..env
    };
    run_setup(&mut env, &channel()).unwrap();
    // Remembered, so later deploys need no env.
    assert!(env.log.contains(&format!(
        "store {WEBHOOK_ELSEWHERE_SUFFIX}{}",
        webhook_digest(OTHER_WEBHOOK)
    )));
    assert!(!env.log.contains(&"point webhook".to_string()));

    // It names one URL; another one still fails without a terminal.
    let (env, elsewhere) = webhook_at("https://new.example/hook");
    let mut env = FakeEnv {
        interactive: false,
        webhook_elsewhere: Some(OTHER_WEBHOOK.into()),
        syncs: VecDeque::from([GitHubSyncOutcome::Synced(Box::new(elsewhere))]),
        ..env
    };
    assert!(run_setup(&mut env, &channel()).is_err());
}

#[test]
fn a_webhook_digest_is_short_and_stable() {
    let digest = webhook_digest(OTHER_WEBHOOK);
    assert_eq!(digest.len(), WEBHOOK_DIGEST_LENGTH);
    assert!(digest.chars().all(|c| c.is_ascii_hexdigit()));
    assert_eq!(webhook_digest(&format!(" {OTHER_WEBHOOK}\n")), digest);
    assert_ne!(webhook_digest(WEBHOOK), digest);
}

#[test]
fn a_refused_first_token_says_how_to_fix_it() {
    let mut env = FakeEnv::interactive();
    env.source = Some(AppSource::Existing);
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    env.start_errors.push_back(
        langsmith::ApiError {
            status: reqwest::StatusCode::BAD_GATEWAY,
            detail: format!(
                "Request only permissions the GitHub App installation grants: {PERMISSIONS_REJECTED}"
            ),
        }
        .into(),
    );
    env.retries.push_back(false);

    run_setup(&mut env, &channel()).unwrap();

    let prompt = env
        .log
        .iter()
        .find(|line| line.starts_with("retry? "))
        .expect("retry prompt");
    assert!(
        prompt.starts_with("retry? GitHub refused the agent's token. Grant what is missing"),
        "{prompt}"
    );
    assert!(
        prompt.contains("remove the handlers or app.permissions"),
        "{prompt}"
    );

    // Other failures, including other GitHub errors, keep their own message.
    let other = explain_refused_token(anyhow::anyhow!("Agent Auth unavailable"));
    assert_eq!(other.to_string(), "Agent Auth unavailable");
    let upstream = explain_refused_token(
        langsmith::ApiError {
            status: reqwest::StatusCode::BAD_GATEWAY,
            detail: "OAuth provider request failed".into(),
        }
        .into(),
    );
    assert!(!upstream.to_string().contains("GitHub refused"));
}

#[test]
fn repositories_the_installation_lacks_are_reported_and_leave_the_token() {
    // Tokens reach what the installation does, so repositories never change
    // the token; deploy only says which ones to add.
    let mut limited = channel();
    limited.repositories = vec!["acme/api".into(), "acme/web".into()];
    let mut env = FakeEnv::interactive();
    env.connection = Some(connection(GitHubAppState::Created, true));
    env.syncs
        .push_back(GitHubSyncOutcome::Synced(Box::new(status(true))));
    run_setup(&mut env, &limited).unwrap();
    assert!(
        !env.log.iter().any(|line| line.starts_with("scopes")),
        "{:?}",
        env.log
    );
    assert!(env.log.iter().any(|line| line
        == "warning: The installation on acme does not include acme/web, which this channel is limited to. Add them at https://github.com/organizations/acme/settings/installations/9"));
}

fn agent_auth_stub(
    responses: Vec<(&'static str, serde_json::Value)>,
) -> (langsmith::Client, std::sync::mpsc::Receiver<String>) {
    let (url, rx) = crate::langsmith::test_support::serve_sequence(
        responses
            .into_iter()
            .map(|(status, body)| (status, body.to_string()))
            .collect(),
    );
    let client = langsmith::Client::new(
        &url,
        &url,
        langsmith::ApiKey::new("lsv2_test"),
        Some("tenant".into()),
    )
    .unwrap();
    (client, rx)
}

fn github_app_providers() -> serde_json::Value {
    json!({
        "items": [
            { "id": "p-1", "slug": "mda-github-app-dep-1", "client_id": "42", "grant_type": "github_app" },
            { "id": "p-2", "slug": "mda-github-app-dep-2", "client_id": "43", "grant_type": "github_app" }
        ],
        "next_cursor": null
    })
}

fn oauth_connection(slug: &str, provider_id: &str) -> serde_json::Value {
    json!({
        "id": format!("c-{slug}"),
        "slug": slug,
        "credential": { "kind": "oauth2", "oauth_provider_id": provider_id },
    })
}

#[test]
fn app_holder_finds_the_deployment_that_owns_the_app() {
    let (client, rx) = agent_auth_stub(vec![
        ("200 OK", github_app_providers()),
        (
            "200 OK",
            json!({
                "items": [
                    oauth_connection("mda/github-app/dep-2", "p-2"),
                    oauth_connection("mda/github-app/dep-1", "p-1"),
                ],
                "next_cursor": null
            }),
        ),
        (
            "200 OK",
            json!([{
                "id": "app-1", "provider": "github", "source": "manifest",
                "slug": "mda/github-app/dep-1", "app_id": "42",
            }]),
        ),
    ]);

    let (deployment_id, app) = app_holder(&client, "42").unwrap().unwrap();

    assert_eq!(deployment_id, "dep-1");
    assert_eq!(app.id, "app-1");
    let requests: Vec<String> = rx.try_iter().collect();
    assert!(requests[2].starts_with("GET /v1/agent-auth/apps?owner_id=dep-1 "));
}

#[test]
fn app_holder_needs_the_deployment_to_own_an_app_with_the_app_id() {
    let (client, _rx) = agent_auth_stub(vec![
        ("200 OK", github_app_providers()),
        (
            "200 OK",
            json!({
                "items": [oauth_connection("mda/github-app/dep-1", "p-1")],
                "next_cursor": null
            }),
        ),
        (
            "200 OK",
            json!([{
                "id": "app-1", "provider": "github", "source": "existing",
                "slug": "mda/github-app/dep-1", "app_id": "7",
            }]),
        ),
    ]);

    assert!(app_holder(&client, "42").unwrap().is_none());
}

#[test]
fn app_holder_ignores_connections_deploy_did_not_create() {
    let (client, rx) = agent_auth_stub(vec![
        ("200 OK", github_app_providers()),
        (
            "200 OK",
            json!({
                "items": [
                    oauth_connection("someone-else/github", "p-1"),
                    oauth_connection("mda/github-app/dep-1/bot-id", "p-1"),
                ],
                "next_cursor": null
            }),
        ),
    ]);

    assert!(app_holder(&client, "42").unwrap().is_none());
    assert_eq!(rx.try_iter().count(), 2);
}

#[test]
fn app_holder_stops_when_no_provider_mints_for_the_app() {
    let (client, rx) = agent_auth_stub(vec![("200 OK", github_app_providers())]);

    assert!(app_holder(&client, "99").unwrap().is_none());
    assert_eq!(rx.try_iter().count(), 1);
}

#[test]
fn app_taken_reads_the_conflict_status() {
    let conflict = anyhow::Error::new(ApiError {
        status: reqwest::StatusCode::CONFLICT,
        detail: "the GitHub App already belongs to another Agent Auth app".into(),
    })
    .context("failed to store the GitHub App");
    let invalid = anyhow::Error::new(ApiError {
        status: reqwest::StatusCode::UNPROCESSABLE_ENTITY,
        detail: "invalid private key".into(),
    });

    assert!(app_taken(&conflict));
    assert!(!app_taken(&invalid));
}
