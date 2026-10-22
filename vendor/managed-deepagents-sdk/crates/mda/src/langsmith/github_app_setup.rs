//! GitHub Apps as Agent Auth apps.
//!
//! The CLI creates the deployment's app from an App manifest and gets back
//! the session that creates and installs it: Agent Auth's page sends the
//! manifest to GitHub, stores the created App with its connection, and sends
//! the user on to install it; the session completes with the agent's
//! credential on the app's connection. The CLI never sees the private key of
//! an App Agent Auth creates; an App the user brings sends its key straight
//! to Agent Auth, which reads the App from GitHub with it.

use std::collections::BTreeMap;

use anyhow::{bail, Context, Result};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use super::agent_auth::{oauth_provider_path, redact_secret, OAUTH_AUTHORIZATION_SESSIONS_PATH};
use super::{Client, OAuthAuthorizationSession};

pub(super) const APPS_PATH: &str = "/v1/agent-auth/apps";

pub(super) fn app_path(app_id: &str) -> String {
    format!("{APPS_PATH}/{app_id}")
}

/// The part of the GitHub App manifest the caller controls.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct GitHubAppManifest {
    pub(crate) name: String,
    pub(crate) url: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub(crate) webhook_url: String,
    pub(crate) permissions: BTreeMap<String, String>,
    pub(crate) events: Vec<String>,
}

/// Where an agent's App stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GitHubAppState {
    /// GitHub has not created the App from its manifest yet.
    Pending,
    /// Agent Auth had GitHub create the App, so GitHub's install redirect
    /// comes back to Agent Auth.
    Created,
    /// The App was brought in by its ID and key; GitHub's redirects go
    /// wherever its owner set them.
    External,
}

/// An Agent Auth app, as the CLI needs it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct AgentAuthApp {
    pub(crate) id: String,
    #[serde(default)]
    pub(crate) provider: String,
    /// `manifest` or `existing`.
    #[serde(default)]
    pub(crate) source: String,
    /// The slug of the app's OAuth connection.
    pub(crate) slug: String,
    /// GitHub's App ID; absent while the App is pending.
    #[serde(default)]
    pub(crate) app_id: Option<String>,
    #[serde(default)]
    pub(crate) manifest: StoredManifest,
    #[serde(default)]
    pub(crate) app_metadata: Option<AppMetadata>,
    /// Apps GitHub created for this app that Agent Auth could not store,
    /// oldest first. They stay on GitHub until someone deletes them there.
    #[serde(default)]
    pub(crate) stranded_apps: Vec<AppMetadata>,
    #[serde(default)]
    pub(crate) connection_id: Option<String>,
}

/// What the CLI reads back from an app's manifest.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub(crate) struct StoredManifest {
    /// Where Agent Auth last pointed the App's webhook.
    #[serde(default)]
    pub(crate) webhook_url: String,
}

/// What GitHub assigned an app, or an App it created that Agent Auth could
/// not store.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct AppMetadata {
    pub(crate) slug: String,
    pub(crate) html_url: String,
}

impl AgentAuthApp {
    pub(crate) fn state(&self) -> GitHubAppState {
        match (&self.app_id, self.source.as_str()) {
            (None, _) => GitHubAppState::Pending,
            (Some(_), "existing") => GitHubAppState::External,
            (Some(_), _) => GitHubAppState::Created,
        }
    }

    /// The App's page on GitHub, once it exists.
    pub(crate) fn html_url(&self) -> Option<&str> {
        self.app_metadata
            .as_ref()
            .and_then(|metadata| github_page(&metadata.html_url))
    }

    /// The pages of the Apps GitHub created that Agent Auth could not store,
    /// oldest first.
    pub(crate) fn stranded_urls(&self) -> Vec<&str> {
        self.stranded_apps
            .iter()
            .filter_map(|stranded| github_page(&stranded.html_url))
            .collect()
    }
}

fn github_page(url: &str) -> Option<&str> {
    url.starts_with("https://github.com/").then_some(url)
}

/// A new app and the session that creates and installs it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct NewApp {
    pub(crate) app: AgentAuthApp,
    pub(crate) session: OAuthAuthorizationSession,
}

/// How a session on an agent's App should start.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum GitHubAppSessionStart {
    /// Create the pending App, under an organization or the personal account.
    Create { organization: Option<String> },
    /// Install the App Agent Auth created.
    Install,
    /// Record an installation that already exists.
    Installation(String),
}

/// An App the user brings in by its ID and key. Agent Auth mints its tokens
/// with the App's own permissions until deploy narrows them to the channel's.
pub(crate) struct ExistingApp<'a> {
    pub(crate) app_id: &'a str,
    pub(crate) private_key: &'a str,
    pub(crate) webhook_secret: &'a str,
}

impl Client {
    /// Create the agent's App from `manifest`, with the session that creates
    /// and installs it.
    pub(crate) fn create_github_app(
        &self,
        owner_id: &str,
        slug: &str,
        manifest: &GitHubAppManifest,
        organization: Option<&str>,
    ) -> Result<NewApp> {
        let base = self.public_platform_base()?;
        let mut payload = json!({
            "provider": "github",
            "source": "manifest",
            "owner_id": owner_id,
            "slug": slug,
            "manifest": manifest,
        });
        if let Some(org) = organization {
            payload["organization"] = json!(org);
        }
        let value = self.request(&base, Method::POST, APPS_PATH, &[], Some(payload))?;
        let created: NewApp =
            serde_json::from_value(value).context("failed to decode the GitHub App")?;
        if created.session.verification_url.trim().is_empty() {
            bail!("Agent Auth returned the GitHub App without a setup link");
        }
        Ok(created)
    }

    /// Store an existing App as the agent's. Agent Auth reads the App from
    /// GitHub with its key, which never shows up in errors.
    pub(crate) fn bring_github_app(
        &self,
        owner_id: &str,
        slug: &str,
        app: &ExistingApp<'_>,
    ) -> Result<AgentAuthApp> {
        let base = self.public_platform_base()?;
        let payload = json!({
            "provider": "github",
            "source": "existing",
            "owner_id": owner_id,
            "slug": slug,
            "existing": {
                "app_id": app.app_id,
                "private_key": app.private_key,
                "webhook_secret": app.webhook_secret,
            },
        });
        let value = self
            .request(&base, Method::POST, APPS_PATH, &[], Some(payload))
            .map_err(|err| redact_secret(err, app.private_key))
            .map_err(|err| redact_secret(err, app.webhook_secret))?;
        let created = value
            .get("app")
            .cloned()
            .context("Agent Auth did not return the GitHub App")?;
        serde_json::from_value(created).context("failed to decode the GitHub App")
    }

    /// The apps the agent owns.
    pub(crate) fn agent_apps(&self, owner_id: &str) -> Result<Vec<AgentAuthApp>> {
        self.agent_apps_if_supported(owner_id)?
            .context("this LangSmith does not support GitHub Apps through Agent Auth yet")
    }

    /// The apps `owner_id` owns, or `None` when this LangSmith has no Agent
    /// Auth apps, so no channel could have set one up.
    pub(crate) fn agent_apps_if_supported(
        &self,
        owner_id: &str,
    ) -> Result<Option<Vec<AgentAuthApp>>> {
        let base = self.public_platform_base()?;
        let Some(value) = self.request_optional(
            &base,
            Method::GET,
            APPS_PATH,
            &[("owner_id", owner_id)],
            None,
        )?
        else {
            return Ok(None);
        };
        serde_json::from_value(value)
            .map(Some)
            .context("failed to decode the agent's apps")
    }

    /// Delete an app with everything Agent Auth stored for it. `false` when it
    /// was already gone.
    pub(crate) fn delete_agent_app(&self, app_id: &str) -> Result<bool> {
        let base = self.public_platform_base()?;
        Ok(self
            .request_optional(&base, Method::DELETE, &app_path(app_id), &[], None)?
            .is_some())
    }

    /// The `permission:level` scopes the provider `provider_id` mints tokens
    /// with, sorted.
    pub(crate) fn github_app_scopes(&self, provider_id: &str) -> Result<Vec<String>> {
        let base = self.public_platform_base()?;
        let provider = self.request(
            &base,
            Method::GET,
            &oauth_provider_path(provider_id),
            &[],
            None,
        )?;
        let mut scopes: Vec<String> = provider
            .get("allowed_scopes")
            .and_then(Value::as_array)
            .map(|scopes| {
                scopes
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        scopes.sort();
        Ok(scopes)
    }

    /// Mint the provider's tokens with `scopes` from now on. Agent Auth
    /// invalidates existing credentials, so callers renew them right after.
    pub(crate) fn update_github_app_scopes(
        &self,
        provider_id: &str,
        scopes: &[String],
    ) -> Result<()> {
        let base = self.public_platform_base()?;
        let payload = json!({ "allowed_scopes": scopes, "default_scopes": scopes });
        self.request(
            &base,
            Method::PATCH,
            &oauth_provider_path(provider_id),
            &[],
            Some(payload),
        )?;
        Ok(())
    }

    /// Start a session that gives the agent `owner_id` a credential on its
    /// App: creating a pending App first, or installing a created one.
    pub(crate) fn start_github_app_session(
        &self,
        app: &AgentAuthApp,
        owner_id: &str,
        start: &GitHubAppSessionStart,
    ) -> Result<OAuthAuthorizationSession> {
        let mut fields = Map::new();
        match start {
            GitHubAppSessionStart::Create { organization } => {
                let base = self.public_platform_base()?;
                let mut payload = json!({
                    "agent_auth_app_id": app.id,
                    "owner_type": "agent",
                    "owner_id": owner_id,
                });
                if let Some(org) = organization {
                    payload["organization"] = json!(org);
                }
                let value = self.request(
                    &base,
                    Method::POST,
                    OAUTH_AUTHORIZATION_SESSIONS_PATH,
                    &[],
                    Some(payload),
                )?;
                return serde_json::from_value(value)
                    .context("failed to decode the GitHub App setup session");
            }
            GitHubAppSessionStart::Installation(id) => {
                fields.insert("installation_id".into(), json!(id));
            }
            GitHubAppSessionStart::Install => {}
        }
        let connection_id = app
            .connection_id
            .as_deref()
            .context("the GitHub App has no connection yet")?;
        self.start_agent_oauth_authorization_session(connection_id, owner_id, fields)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::langsmith::test_support::serve_once;

    fn client(base: &str) -> Client {
        Client::new_with_platform(base, Some(base), "lsv2_key", Some("tenant-1".into())).unwrap()
    }

    fn manifest() -> GitHubAppManifest {
        GitHubAppManifest {
            name: "acme-triage".into(),
            url: "https://agent.example".into(),
            webhook_url: "https://agent.example/channels/github/events".into(),
            permissions: BTreeMap::from([("issues".into(), "write".into())]),
            events: vec!["issues".into()],
        }
    }

    fn app(source: &str, app_id: Option<&str>) -> AgentAuthApp {
        serde_json::from_value(app_json(source, app_id)).unwrap()
    }

    fn app_json(source: &str, app_id: Option<&str>) -> Value {
        json!({
            "id": "app-1", "provider": "github", "source": source, "slug": "mda/github-app/x",
            "app_id": app_id, "connection_id": app_id.map(|_| "conn-1"),
            "manifest": { "webhook_url": "https://agent.example/channels/github/events" },
            "app_metadata": app_id.map(|_| json!({
                "slug": "acme-triage", "html_url": "https://github.com/apps/acme-triage", "bot_id": "9"
            })),
        })
    }

    #[test]
    fn a_new_app_carries_the_manifest_and_returns_its_session() {
        let body = json!({
            "app": { "id": "app-1", "source": "manifest", "slug": "mda/github-app/x" },
            "session": {
                "id": "11111111-2222-3333-4444-555555555555", "status": "pending",
                "verification_url": "https://aa.example/v1/agent-auth/github-app/start?state=s"
            }
        });
        let (base, requests) = serve_once("201 Created", body.to_string());
        let created = client(&base)
            .create_github_app("agent-1", "mda/github-app/x", &manifest(), Some("acme"))
            .unwrap();
        assert_eq!(created.app.id, "app-1");
        assert_eq!(created.app.state(), GitHubAppState::Pending);
        assert_eq!(created.session.status, "pending");
        let sent = requests.recv().unwrap();
        assert!(
            sent.starts_with("POST /v1/agent-auth/apps HTTP/1.1"),
            "{sent}"
        );
        for field in [
            r#""provider":"github""#,
            r#""source":"manifest""#,
            r#""owner_id":"agent-1""#,
            r#""slug":"mda/github-app/x""#,
            r#""organization":"acme""#,
            r#""permissions":{"issues":"write"}"#,
        ] {
            assert!(sent.contains(field), "{field} missing from {sent}");
        }
        assert!(!sent.contains("private_key"), "{sent}");
        assert!(!sent.contains("existing"), "{sent}");
    }

    #[test]
    fn an_existing_app_never_leaks_its_key_or_secret_in_errors() {
        let key = "-----BEGIN RSA PRIVATE KEY-----\nSECRETKEY\n-----END RSA PRIVATE KEY-----\n";
        let (base, requests) = serve_once(
            "422 Unprocessable Entity",
            r#"{"detail":"GitHub rejected the App's credentials SECRETKEY whsec-123"}"#.to_string(),
        );
        let error = client(&base)
            .bring_github_app(
                "agent-1",
                "mda/github-app/x",
                &ExistingApp {
                    app_id: "42",
                    private_key: key,
                    webhook_secret: "whsec-123",
                },
            )
            .unwrap_err();
        let message = format!("{error:#}");
        assert!(!message.contains("SECRETKEY"), "{message}");
        assert!(!message.contains("whsec-123"), "{message}");
        let sent = requests.recv().unwrap();
        let body: Value = serde_json::from_str(sent.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["source"], "existing", "{sent}");
        assert_eq!(body["existing"]["app_id"], "42", "{sent}");
        assert_eq!(body["existing"]["webhook_secret"], "whsec-123", "{sent}");
        for field in [
            "app_id",
            "private_key",
            "webhook_secret",
            "manifest",
            "organization",
        ] {
            assert!(
                body.get(field).is_none(),
                "{field} sent outside existing: {sent}"
            );
        }
        assert!(!sent.contains("scopes"), "{sent}");
    }

    #[test]
    fn apps_are_listed_by_owner_and_read_their_state() {
        let mut stranded = app_json("manifest", None);
        stranded["stranded_apps"] = json!([
            { "slug": "acme-triage", "html_url": "https://github.com/apps/acme-triage" },
            { "slug": "evil", "html_url": "https://evil.example/apps/evil" },
        ]);
        let body = json!([
            app_json("manifest", Some("42")),
            app_json("existing", Some("43")),
            app_json("manifest", None),
            stranded
        ]);
        let (base, requests) = serve_once("200 OK", body.to_string());
        let apps = client(&base).agent_apps("agent-1").unwrap();
        assert_eq!(
            apps.iter().map(AgentAuthApp::state).collect::<Vec<_>>(),
            [
                GitHubAppState::Created,
                GitHubAppState::External,
                GitHubAppState::Pending,
                GitHubAppState::Pending
            ]
        );
        assert_eq!(
            apps[0].html_url(),
            Some("https://github.com/apps/acme-triage")
        );
        assert_eq!(apps[2].html_url(), None);
        assert!(apps[2].stranded_urls().is_empty());
        assert_eq!(apps[3].html_url(), None);
        assert_eq!(
            apps[3].stranded_urls(),
            ["https://github.com/apps/acme-triage"]
        );
        let sent = requests.recv().unwrap();
        assert!(
            sent.starts_with("GET /v1/agent-auth/apps?owner_id=agent-1 "),
            "{sent}"
        );

        let (base, _requests) = serve_once("404 Not Found", "{}".to_string());
        let error = client(&base).agent_apps("agent-1").unwrap_err();
        assert!(
            format!("{error:#}").contains("does not support GitHub Apps"),
            "{error:#}"
        );
        let (base, _requests) = serve_once("404 Not Found", "{}".to_string());
        assert!(client(&base)
            .agent_apps_if_supported("agent-1")
            .unwrap()
            .is_none());
    }

    #[test]
    fn sessions_create_the_pending_app_or_install_the_created_one() {
        let pending = r#"{"id":"11111111-2222-3333-4444-555555555555","status":"pending","verification_url":"https://aa.example/start?state=s"}"#;
        for (app, start, expected, absent) in [
            (
                app("manifest", None),
                GitHubAppSessionStart::Create {
                    organization: Some("acme".into()),
                },
                r#""agent_auth_app_id":"app-1""#,
                "connection_id",
            ),
            (
                app("manifest", Some("42")),
                GitHubAppSessionStart::Install,
                r#""connection_id":"conn-1""#,
                "installation_id",
            ),
            (
                app("existing", Some("42")),
                GitHubAppSessionStart::Installation("777".into()),
                r#""installation_id":"777""#,
                "agent_auth_app_id",
            ),
        ] {
            let (base, requests) = serve_once("201 Created", pending.to_string());
            let session = client(&base)
                .start_github_app_session(&app, "agent-1", &start)
                .unwrap();
            assert_eq!(session.status, "pending");
            let sent = requests.recv().unwrap();
            assert!(
                sent.starts_with("POST /v1/agent-auth/oauth-authorization-sessions HTTP/1.1"),
                "{sent}"
            );
            assert!(sent.contains(expected), "{sent}");
            assert!(sent.contains(r#""owner_type":"agent""#), "{sent}");
            assert!(!sent.contains(absent), "{sent}");
        }
    }

    #[test]
    fn scopes_are_read_and_changed_on_the_provider() {
        let body = json!({ "allowed_scopes": ["metadata:read", "issues:write"] });
        let (base, requests) = serve_once("200 OK", body.to_string());
        let scopes = client(&base)
            .github_app_scopes("11111111-2222-3333-4444-555555555555")
            .unwrap();
        assert_eq!(scopes, vec!["issues:write", "metadata:read"]);
        assert!(requests.recv().unwrap().starts_with(
            "GET /v1/agent-auth/oauth-providers/11111111-2222-3333-4444-555555555555 "
        ));

        let (base, requests) = serve_once("200 OK", "{}".to_string());
        client(&base)
            .update_github_app_scopes(
                "11111111-2222-3333-4444-555555555555",
                &["issues:read".into()],
            )
            .unwrap();
        let sent = requests.recv().unwrap();
        assert!(
            sent.starts_with("PATCH /v1/agent-auth/oauth-providers/"),
            "{sent}"
        );
        assert!(
            sent.contains(r#""allowed_scopes":["issues:read"]"#),
            "{sent}"
        );
        assert!(
            sent.contains(r#""default_scopes":["issues:read"]"#),
            "{sent}"
        );
    }

    #[test]
    fn deleting_a_gone_app_is_not_an_error() {
        let (base, requests) = serve_once("404 Not Found", "{}".to_string());
        assert!(!client(&base).delete_agent_app("app-1").unwrap());
        assert!(requests
            .recv()
            .unwrap()
            .starts_with("DELETE /v1/agent-auth/apps/app-1 "));
    }
}
