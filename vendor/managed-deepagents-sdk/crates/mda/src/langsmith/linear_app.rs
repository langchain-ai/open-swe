//! Linear apps as Agent Auth apps.
//!
//! Linear has no API to create an app, so a workspace admin creates it and
//! deploy brings it in by its client ID and secret. Agent Auth builds the
//! Linear OAuth provider for it, keeps its webhook signing secret at
//! `{slug}/webhook-secret`, and deletes all of it with the app.

use anyhow::{Context, Result};
use reqwest::Method;
use serde_json::json;

use super::agent_auth::redact_secret;
use super::github_app_setup::{AgentAuthApp, APPS_PATH};
use super::Client;

/// A Linear app the workspace has, as its settings page shows it.
pub(crate) struct ExistingLinearApp<'a> {
    pub(crate) client_id: &'a str,
    pub(crate) client_secret: &'a str,
    pub(crate) webhook_secret: &'a str,
    /// What the install asks for.
    pub(crate) scopes: &'a [String],
    /// What the agent's tokens may be narrowed to.
    pub(crate) allowed_scopes: &'a [String],
}

impl Client {
    /// Store an existing Linear app as the agent's. Neither secret ever shows
    /// up in errors.
    pub(crate) fn bring_linear_app(
        &self,
        owner_id: &str,
        slug: &str,
        app: &ExistingLinearApp<'_>,
    ) -> Result<AgentAuthApp> {
        let base = self.public_platform_base()?;
        let payload = json!({
            "provider": "linear",
            "source": "existing",
            "owner_id": owner_id,
            "slug": slug,
            "existing": {
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "webhook_secret": app.webhook_secret,
                "scopes": app.scopes,
                "allowed_scopes": app.allowed_scopes,
            },
        });
        let value = self
            .request(&base, Method::POST, APPS_PATH, &[], Some(payload))
            .map_err(|err| redact_secret(err, app.client_secret))
            .map_err(|err| redact_secret(err, app.webhook_secret))?;
        let created = value
            .get("app")
            .cloned()
            .context("Agent Auth did not return the Linear app")?;
        serde_json::from_value(created).context("failed to decode the Linear app")
    }
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    use super::*;
    use crate::langsmith::test_support::serve_once;
    use crate::langsmith::ApiKey;

    fn client(base: &str) -> Client {
        Client::new(base, base, ApiKey::new("lsv2_key"), Some("tenant-1".into())).unwrap()
    }

    fn app<'a>(scopes: &'a [String]) -> ExistingLinearApp<'a> {
        ExistingLinearApp {
            client_id: "client-1",
            client_secret: "linear-client-secret",
            webhook_secret: "lin_wh_signing",
            scopes,
            allowed_scopes: scopes,
        }
    }

    #[test]
    fn an_existing_app_is_brought_in_with_its_credentials_and_scopes() {
        let body = json!({
            "app": {
                "id": "app-1", "provider": "linear", "source": "existing",
                "slug": "mda/linear-app/dep-1", "connection_id": "conn-1",
            }
        });
        let (base, requests) = serve_once("201 Created", body.to_string());
        let scopes = vec!["read".to_string(), "write".to_string()];
        let created = client(&base)
            .bring_linear_app("dep-1", "mda/linear-app/dep-1", &app(&scopes))
            .unwrap();
        assert_eq!(created.id, "app-1");
        assert_eq!(created.connection_id.as_deref(), Some("conn-1"));

        let sent = requests.recv().unwrap();
        assert!(
            sent.starts_with("POST /v1/agent-auth/apps HTTP/1.1"),
            "{sent}"
        );
        let body: Value = serde_json::from_str(sent.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(
            body,
            json!({
                "provider": "linear",
                "source": "existing",
                "owner_id": "dep-1",
                "slug": "mda/linear-app/dep-1",
                "existing": {
                    "client_id": "client-1",
                    "client_secret": "linear-client-secret",
                    "webhook_secret": "lin_wh_signing",
                    "scopes": ["read", "write"],
                    "allowed_scopes": ["read", "write"],
                },
            })
        );
        // Agent Auth builds the OAuth provider itself.
        for field in ["authorize_url", "token_url", "authorization_params"] {
            assert!(!sent.contains(field), "{field} sent: {sent}");
        }
    }

    #[test]
    fn errors_never_echo_the_secrets() {
        let (base, _) = serve_once(
            "422 Unprocessable Entity",
            r#"{"detail":"Linear refused linear-client-secret and lin_wh_signing"}"#.to_string(),
        );
        let error = client(&base)
            .bring_linear_app("dep-1", "mda/linear-app/dep-1", &app(&[]))
            .unwrap_err();
        let message = format!("{error:#}");
        assert!(!message.contains("linear-client-secret"), "{message}");
        assert!(!message.contains("lin_wh_signing"), "{message}");
    }
}
