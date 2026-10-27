//! Where a command's LangSmith calls go and who they go as.
//!
//! Every command that talks to LangSmith resolves the same four things in the
//! same order — host backend, platform API, API key, workspace — before it
//! builds a [`Client`] or [`ContextHubClient`]. [`LangSmithContext::resolve`]
//! does that once so `--host-url` and `--workspace-id` mean the same thing in
//! every command.

use std::collections::BTreeMap;

use anyhow::Result;

use super::helpers::{
    resolve_context_hub_base_url, resolve_langsmith_api_bases, resolve_platform_api_url,
    REMOVED_WORKSPACE_ID_ENVS, WORKSPACE_ID_ENV,
};
use crate::langsmith::{resolve_preferred_env, ApiKey, Client, ContextHubClient};

/// Completes the removed-alias error for commands that take `--workspace-id`.
pub(crate) const WORKSPACE_FLAG_HINT: &str = " or pass --workspace-id instead";

/// The LangSmith installation a command targets.
#[derive(Debug, Clone)]
pub(crate) struct LangSmithEndpoints {
    /// Host backend (Deployments) base URL.
    pub host_url: String,
    /// Platform API (Fleet, secrets, sandboxes, device login) base URL.
    pub platform_api_url: String,
}

impl LangSmithEndpoints {
    /// A non-blank `host_override` (`--host-url`) wins; otherwise
    /// `LANGSMITH_ENDPOINT` from `env_vars`, then the process env, picks the
    /// installation. The platform API is derived from the chosen host.
    pub(crate) fn resolve(
        env_vars: &BTreeMap<String, String>,
        host_override: Option<&str>,
    ) -> Self {
        let host_url = host_override
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| resolve_langsmith_api_bases(env_vars).0);
        let platform_api_url = resolve_platform_api_url(&host_url, env_vars);
        Self {
            host_url,
            platform_api_url,
        }
    }

    /// Pair these endpoints with an already-resolved key and workspace.
    pub(crate) fn with_auth(
        self,
        api_key: ApiKey,
        workspace_id: Option<String>,
    ) -> LangSmithContext {
        LangSmithContext {
            endpoints: self,
            api_key,
            workspace_id,
        }
    }
}

/// Endpoints plus the API key and workspace every LangSmith request carries.
#[derive(Debug, Clone)]
pub(crate) struct LangSmithContext {
    pub endpoints: LangSmithEndpoints,
    pub api_key: ApiKey,
    pub workspace_id: Option<String>,
}

impl LangSmithContext {
    /// Resolve endpoints, then the API key, then the workspace.
    ///
    /// `api_key` receives the platform API URL (device login and the CLI PAT
    /// cache are keyed by it) and decides whether a missing key prompts, fails,
    /// or falls back. The workspace is `workspace_flag` when non-blank, else
    /// `LANGSMITH_WORKSPACE_ID` from `env_vars`, then the process env;
    /// `workspace_hint` completes the error for a removed alias.
    pub(crate) fn resolve(
        env_vars: &BTreeMap<String, String>,
        host_override: Option<&str>,
        workspace_flag: Option<&str>,
        workspace_hint: &str,
        api_key: impl FnOnce(&str) -> Result<ApiKey>,
    ) -> Result<Self> {
        let endpoints = LangSmithEndpoints::resolve(env_vars, host_override);
        let api_key = api_key(&endpoints.platform_api_url)?;
        let workspace_id = resolve_workspace_id(env_vars, workspace_flag, workspace_hint)?;
        Ok(endpoints.with_auth(api_key, workspace_id))
    }

    pub(crate) fn host_url(&self) -> &str {
        &self.endpoints.host_url
    }

    pub(crate) fn platform_api_url(&self) -> &str {
        &self.endpoints.platform_api_url
    }

    /// Client for the host backend and platform API.
    pub(crate) fn client(&self) -> Result<Client> {
        Client::new(
            self.host_url(),
            self.platform_api_url(),
            self.api_key.clone(),
            self.workspace_id.clone(),
        )
    }

    /// Client for the Context Hub that pairs with these endpoints.
    pub(crate) fn context_hub_client(&self) -> Result<ContextHubClient> {
        ContextHubClient::new(
            &resolve_context_hub_base_url(self.host_url(), self.platform_api_url()),
            self.host_url(),
            self.api_key.clone(),
            self.workspace_id.clone(),
        )
    }
}

/// `workspace_flag` when non-blank, else `LANGSMITH_WORKSPACE_ID` from
/// `env_vars`, then the process env. Errors on a removed alias.
pub(crate) fn resolve_workspace_id(
    env_vars: &BTreeMap<String, String>,
    workspace_flag: Option<&str>,
    workspace_hint: &str,
) -> Result<Option<String>> {
    resolve_preferred_env(
        WORKSPACE_ID_ENV,
        REMOVED_WORKSPACE_ID_ENVS,
        workspace_flag,
        env_vars,
        |key| std::env::var(key).ok(),
        workspace_hint,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_host_override_beats_langsmith_endpoint() {
        let env = BTreeMap::from([(
            "LANGSMITH_ENDPOINT".to_string(),
            "https://eu.api.smith.langchain.com".to_string(),
        )]);
        let endpoints = LangSmithEndpoints::resolve(&env, Some(" https://api.host.langchain.com "));
        assert_eq!(endpoints.host_url, "https://api.host.langchain.com");
    }

    #[test]
    fn a_blank_host_override_falls_back_to_langsmith_endpoint() {
        let env = BTreeMap::from([(
            "LANGSMITH_ENDPOINT".to_string(),
            "https://eu.api.smith.langchain.com".to_string(),
        )]);
        let endpoints = LangSmithEndpoints::resolve(&env, Some("  "));
        assert_eq!(endpoints.host_url, "https://eu.api.host.langchain.com");
        assert_eq!(
            endpoints.platform_api_url,
            "https://eu.api.smith.langchain.com"
        );
    }
}
