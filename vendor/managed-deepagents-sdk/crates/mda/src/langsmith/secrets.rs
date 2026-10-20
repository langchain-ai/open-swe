//! LangSmith platform API endpoints for workspace secrets.
//!
//! Used by the deploy pre-flight to confirm a model provider's API key is
//! available to the deployment — either forwarded from the project's `.env` or
//! already configured as a workspace secret in LangSmith
//! (Settings > Workspaces > Secrets). Only the secret *names* are returned by
//! the API (never the values), which is all the credential check needs. Calls
//! go to the platform API (`api.smith.langchain.com`).

use anyhow::{Context, Result};
use reqwest::Method;
use serde::Deserialize;

use super::Client;

/// A single workspace secret entry. The LangSmith API returns only the key
/// name, never the value.
#[derive(Debug, Deserialize)]
struct SecretKey {
    key: String,
}

impl Client {
    /// Fetch the names of the secrets configured for the current LangSmith
    /// workspace from `GET /api/v1/workspaces/current/secrets`.
    ///
    /// Returns the secret key names (values are never exposed by the API).
    /// Errors when the platform API is unavailable for this host (see
    /// [`Client::platform_api_available`]) or the request fails; the caller
    /// decides whether an inability to verify should block the deploy.
    pub fn workspace_secret_keys(&self) -> Result<Vec<String>> {
        let base = self
            .platform_api_url
            .as_deref()
            .context("the LangSmith platform API is not available for this host")?;

        let value = self.request(
            base,
            Method::GET,
            "/api/v1/workspaces/current/secrets",
            &[],
            None,
        )?;

        // An empty body (`Null`) or an empty array both mean "no secrets
        // configured", which is a valid state.
        if value.is_null() {
            return Ok(Vec::new());
        }
        let secrets: Vec<SecretKey> = serde_json::from_value(value)
            .context("failed to decode the workspace secrets response")?;
        Ok(secrets.into_iter().map(|secret| secret.key).collect())
    }
}
