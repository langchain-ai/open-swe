//! An agent's GitHub App, as Agent Auth reports it.
//!
//! Agent Auth holds the App's private key, so it, not the CLI or the
//! deployment, signs GitHub App requests. `GET /v1/agent-auth/apps/{id}`
//! returns the App's state as GitHub reports it for the CLI to report on, and
//! `PATCH` points its webhook at the deployment.

use std::collections::BTreeMap;

use anyhow::Result;
use reqwest::{Method, StatusCode};
use serde::Deserialize;
use serde_json::json;

use super::github_app_setup::app_path;
use super::{ApiError, Client};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GitHubAccount {
    pub(crate) login: String,
    #[serde(rename = "type", default)]
    pub(crate) kind: String,
}

impl GitHubAccount {
    pub(crate) fn is_organization(&self) -> bool {
        self.kind == "Organization"
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GitHubApp {
    pub(crate) id: u64,
    pub(crate) slug: String,
    #[serde(default)]
    pub(crate) name: String,
    pub(crate) owner: GitHubAccount,
    #[serde(default)]
    pub(crate) permissions: BTreeMap<String, String>,
    #[serde(default)]
    pub(crate) events: Vec<String>,
    /// The App's bot account, once Agent Auth has recorded it.
    #[serde(default)]
    pub(crate) bot_id: Option<u64>,
    /// Where the App's webhook points; `None` when it has none.
    #[serde(default)]
    pub(crate) webhook_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GitHubInstallation {
    pub(crate) id: u64,
    pub(crate) account: GitHubAccount,
    /// `all` or `selected`.
    #[serde(default)]
    pub(crate) repository_selection: String,
    #[serde(default)]
    pub(crate) repositories: Vec<String>,
    #[serde(default)]
    pub(crate) repositories_truncated: bool,
    /// Permissions the installation has accepted.
    #[serde(default)]
    pub(crate) permissions: BTreeMap<String, String>,
    #[serde(default)]
    pub(crate) suspended_at: Option<String>,
    #[serde(default)]
    pub(crate) settings_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GitHubInstallationRequest {
    pub(crate) account: GitHubAccount,
    #[serde(default)]
    pub(crate) requester: Option<GitHubAccount>,
}

/// The App as GitHub reports it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct GitHubChannelStatus {
    pub(crate) app: GitHubApp,
    #[serde(default)]
    pub(crate) installations: Vec<GitHubInstallation>,
    #[serde(default)]
    pub(crate) installation_requests: Vec<GitHubInstallationRequest>,
}

/// Result of one report. Only `Unavailable` is not the developer's to fix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum GitHubSyncOutcome {
    /// Boxed: the report is much larger than the other outcomes.
    Synced(Box<GitHubChannelStatus>),
    /// Agent Auth has no GitHub App for this agent, or the caller cannot see
    /// it (403/404).
    MissingCredentials,
    /// GitHub rejected the stored credentials (422).
    InvalidCredentials,
    /// The App is still pending, so GitHub has nothing to report.
    NotCreated,
    /// Network, GitHub, or server trouble; carries a short reason.
    Unavailable(String),
}

/// An app as `GET /v1/agent-auth/apps/{id}` returns it.
#[derive(Deserialize)]
struct AppDetails {
    #[serde(default)]
    app_metadata: Option<DetailsMetadata>,
    github: Option<GitHubChannelStatus>,
}

#[derive(Deserialize)]
struct DetailsMetadata {
    #[serde(default)]
    bot_id: Option<String>,
}

impl Client {
    /// The agent's App as GitHub reports it now.
    pub(crate) fn github_app_report(&self, app_id: &str) -> GitHubSyncOutcome {
        let base = match self.public_platform_base() {
            Ok(base) => base,
            Err(error) => return GitHubSyncOutcome::Unavailable(format!("{error:#}")),
        };
        // Deploy checks the repositories a channel is limited to, so the report
        // lists each installation's; Agent Auth leaves them out otherwise.
        let result = self.request(
            &base,
            Method::GET,
            &app_path(app_id),
            &[("include", "repositories")],
            None,
        );
        classify_sync_result(result)
    }

    /// Point the agent's App's webhook at `webhook_url`, replacing whatever it
    /// points at now. Callers ask first when that is someone else's.
    pub(crate) fn point_github_app_webhook(&self, app_id: &str, webhook_url: &str) -> Result<()> {
        let base = self.public_platform_base()?;
        let payload = json!({
            "manifest": { "webhook_url": webhook_url },
            "overwrite_external": true,
        });
        self.request(&base, Method::PATCH, &app_path(app_id), &[], Some(payload))?;
        Ok(())
    }
}

fn classify_sync_result(result: Result<serde_json::Value>) -> GitHubSyncOutcome {
    match result {
        Ok(value) => match serde_json::from_value::<AppDetails>(value) {
            Ok(AppDetails {
                github: Some(mut status),
                app_metadata,
            }) => {
                status.app.bot_id = app_metadata
                    .and_then(|metadata| metadata.bot_id)
                    .and_then(|bot_id| bot_id.parse().ok());
                GitHubSyncOutcome::Synced(Box::new(status))
            }
            Ok(AppDetails { github: None, .. }) => GitHubSyncOutcome::NotCreated,
            Err(_) => GitHubSyncOutcome::Unavailable(
                "Agent Auth returned an unexpected GitHub App status".to_string(),
            ),
        },
        Err(error) => match error.downcast_ref::<ApiError>().map(|api| api.status) {
            Some(StatusCode::NOT_FOUND | StatusCode::FORBIDDEN) => {
                GitHubSyncOutcome::MissingCredentials
            }
            Some(StatusCode::UNPROCESSABLE_ENTITY) => GitHubSyncOutcome::InvalidCredentials,
            Some(status) => GitHubSyncOutcome::Unavailable(format!("HTTP {}", status.as_u16())),
            None if error.chain().any(|cause| cause.is::<reqwest::Error>()) => {
                GitHubSyncOutcome::Unavailable("Agent Auth could not be reached".to_string())
            }
            None => GitHubSyncOutcome::Unavailable(format!("{error:#}")),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::langsmith::test_support::serve_once;

    fn client(base: &str) -> Client {
        Client::new_with_platform(base, Some(base), "lsv2_test_key", None).unwrap()
    }

    const APP: &str = "11111111-2222-3333-4444-555555555555";

    #[test]
    fn the_report_reads_the_app_from_agent_auth() {
        let body = json!({
            "id": APP, "source": "manifest", "slug": "mda/github-app/dep",
            "app_metadata": { "slug": "acme-triage", "html_url": "https://github.com/apps/acme-triage", "bot_id": "4242" },
            "github": {
                "app": {
                    "id": 42, "slug": "acme-triage", "name": "acme-triage",
                    "owner": { "login": "acme", "type": "Organization" },
                    "permissions": { "issues": "write", "metadata": "read" },
                    "events": ["issues"],
                    "webhook_url": "https://agent.example/channels/github/events"
                },
                "installations": [{
                    "id": 7, "account": { "login": "acme", "type": "Organization" },
                    "repository_selection": "selected", "repositories": ["acme/api"],
                    "repositories_truncated": true,
                    "permissions": { "issues": "write", "metadata": "read" },
                    "settings_url": "https://github.com/organizations/acme/settings/installations/7"
                }],
                "installation_requests": [{ "account": { "login": "globex", "type": "Organization" } }]
            }
        })
        .to_string();
        let (base, requests) = serve_once("200 OK", body);

        let outcome = client(&base).github_app_report(APP);

        let request = requests.recv().unwrap();
        assert!(
            request.starts_with(&format!(
                "GET /v1/agent-auth/apps/{APP}?include=repositories HTTP/1.1"
            )),
            "{request}"
        );
        assert!(
            request
                .to_ascii_lowercase()
                .contains("x-api-key: lsv2_test_key"),
            "{request}"
        );
        let GitHubSyncOutcome::Synced(status) = outcome else {
            panic!("expected a report, got {outcome:?}");
        };
        assert_eq!(status.app.slug, "acme-triage");
        assert_eq!(status.app.bot_id, Some(4242));
        assert_eq!(
            status.app.webhook_url.as_deref(),
            Some("https://agent.example/channels/github/events")
        );
        assert!(status.app.owner.is_organization());
        assert_eq!(status.installations[0].repositories, vec!["acme/api"]);
        assert!(status.installations[0].repositories_truncated);
        assert_eq!(status.installation_requests[0].account.login, "globex");
    }

    #[test]
    fn a_pending_app_has_no_report() {
        let (base, _) = serve_once("200 OK", json!({ "id": APP, "slug": "x" }).to_string());
        assert_eq!(
            client(&base).github_app_report(APP),
            GitHubSyncOutcome::NotCreated
        );
    }

    #[test]
    fn the_webhook_is_pointed_with_a_patch() {
        let (base, requests) = serve_once("200 OK", "{}".to_string());
        client(&base)
            .point_github_app_webhook(APP, "https://agent.example/channels/github/events")
            .unwrap();
        let request = requests.recv().unwrap();
        assert!(
            request.starts_with(&format!("PATCH /v1/agent-auth/apps/{APP} HTTP/1.1")),
            "{request}"
        );
        assert!(
            request.contains(
                r#""manifest":{"webhook_url":"https://agent.example/channels/github/events"}"#
            ),
            "{request}"
        );
        assert!(
            request.contains(r#""overwrite_external":true"#),
            "{request}"
        );
    }

    #[test]
    fn the_report_maps_error_statuses() {
        for (status, expected) in [
            ("404 Not Found", GitHubSyncOutcome::MissingCredentials),
            ("403 Forbidden", GitHubSyncOutcome::MissingCredentials),
            (
                "422 Unprocessable Entity",
                GitHubSyncOutcome::InvalidCredentials,
            ),
            (
                "502 Bad Gateway",
                GitHubSyncOutcome::Unavailable("HTTP 502".into()),
            ),
        ] {
            let (base, _) = serve_once(status, r#"{"error":"x","message":"y"}"#.to_string());
            assert_eq!(client(&base).github_app_report(APP), expected, "{status}");
        }
    }

    #[test]
    fn the_report_rejects_a_malformed_answer() {
        let (base, _) = serve_once("200 OK", r#"{"github":{"unexpected":true}}"#.to_string());
        assert!(matches!(
            client(&base).github_app_report(APP),
            GitHubSyncOutcome::Unavailable(_)
        ));
    }

    #[test]
    fn the_report_names_an_unreachable_agent_auth() {
        assert_eq!(
            client("http://127.0.0.1:1").github_app_report(APP),
            GitHubSyncOutcome::Unavailable("Agent Auth could not be reached".to_string())
        );
    }
}
