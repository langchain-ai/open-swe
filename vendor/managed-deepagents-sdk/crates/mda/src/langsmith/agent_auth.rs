//! Agent-auth connection endpoints on the LangSmith platform API.
//!
//! Backs `mda connections`, which manages workspace-scoped connections (opaque
//! secrets and OAuth configuration). Workspace connections live on
//! `/v1/agent-auth/connections`; linked credential rows are rotated through
//! `/v1/agent-auth/credentials` when the connections API has no secret patch.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{anyhow, bail, Context, Result};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use super::{ApiError, Client};
use crate::error::{categorized, ErrorCategory};

pub(super) const CONNECTIONS_PATH: &str = "/v1/agent-auth/connections";
const OAUTH_PROVIDERS_PATH: &str = "/v1/agent-auth/oauth-providers";
const CREDENTIALS_PATH: &str = "/v1/agent-auth/credentials";
pub(super) const OAUTH_AUTHORIZATION_SESSIONS_PATH: &str =
    "/v1/agent-auth/oauth-authorization-sessions";
const OAUTH_CALLBACK_PATH: &str = "/v1/agent-auth/oauth/callback";
const OAUTH_CLIENT_METADATA_PATH: &str = "/v1/agent-auth/oauth/client-metadata.json";
const PRINCIPALS_PATH: &str = "/v1/agent-auth/principals";
const SAAS_PLATFORM_AUTH_PATH: &str = "/auth";
const SELF_HOSTED_PLATFORM_AUTH_PATH: &str = "/v1/auth";
const LANGSMITH_DEV_IDENTITY_PROVIDER: &str = "langsmith-dev";

const KIND_SECRET: &str = "secret";
const KIND_OAUTH2: &str = "oauth2";

const OWNER_POLICY_AGENT: &str = "agent";
const OWNER_POLICY_USER: &str = "user";

/// Who supplies material for a workspace connection at runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionOwnerPolicy {
    Agent,
    User,
}

impl ConnectionOwnerPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Agent => OWNER_POLICY_AGENT,
            Self::User => OWNER_POLICY_USER,
        }
    }
}

/// Items per list request. This is exactly the server's cap; asking for more is
/// a 400 rather than a clamp.
const PAGE_SIZE: usize = 100;

/// Deliberately carries no secret field: this is what `mda connections --json`
/// serializes, so a field here would be a field on stdout.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct ConnectionSummary {
    #[serde(alias = "credential_id", alias = "id")]
    pub connection_id: String,
    pub slug: String,
    #[serde(default)]
    pub display_name: String,
    /// `secret` or `oauth2`.
    pub kind: String,
    #[serde(default, rename = "oauth_provider_id")]
    pub oauth_provider_id: Option<String>,
    /// Defaulted so a missing timestamp cannot fail a whole listing.
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    /// Workspace OAuth provider slug when `kind=oauth2` (enriched client-side).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub oauth_app_slug: Option<String>,
    /// Catalog or custom service when `kind=oauth2` (enriched client-side).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service: Option<String>,
    /// `agent` (shared) or `user` (per-caller grant). Omitted when the server
    /// does not expose ownership on the connection row.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owner_policy: Option<String>,
}

impl ConnectionSummary {
    pub fn is_secret(&self) -> bool {
        self.kind == KIND_SECRET
    }

    pub fn is_user_owned(&self) -> bool {
        self.owner_policy.as_deref() == Some(OWNER_POLICY_USER)
    }
}

/// Fields for creating a workspace OAuth connection row.
///
/// Agent Auth creates the OAuth provider in the same transaction as the
/// connection (`credential.provider`). Linking an existing provider by id is
/// no longer supported.
#[derive(Debug, Clone, Copy)]
pub struct CreateOAuthConnection<'a> {
    pub slug: &'a str,
    pub owner_policy: ConnectionOwnerPolicy,
    pub service: Option<&'a str>,
    pub client_id: Option<&'a str>,
    pub client_secret: Option<&'a str>,
    pub display_name: &'a str,
    pub scopes: &'a [String],
    pub allowed_scopes: &'a [String],
    pub authorize_url: Option<&'a str>,
    pub token_url: Option<&'a str>,
    pub token_endpoint_auth_method: &'a str,
    pub authorization_params: &'a Map<String, Value>,
    pub grant_type: &'a str,
    pub token_params: &'a Map<String, Value>,
    /// Write-only headers for token and revocation requests. The server
    /// encrypts them and never returns them.
    pub token_request_headers: &'a Map<String, Value>,
    pub client_registration_url: Option<&'a str>,
    /// When set, Agent Auth runs MCP discovery + DCR instead of manual fields.
    pub mcp_server_url: Option<&'a str>,
}

#[derive(Debug, Clone, Deserialize)]
struct OAuthProviderRecord {
    id: String,
    slug: String,
    #[serde(default)]
    client_id: String,
    #[serde(default)]
    grant_type: String,
}

#[derive(Debug, Deserialize)]
struct ConnectionListPage {
    #[serde(default)]
    items: Vec<Value>,
    next_cursor: Option<String>,
}

#[derive(Debug, Deserialize)]
struct OAuthProviderListPage {
    #[serde(default)]
    items: Vec<OAuthProviderRecord>,
    next_cursor: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct DeveloperPrincipal {
    pub principal_id: String,
    pub workspace_id: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub(crate) struct OAuthAuthorizationSession {
    pub id: String,
    pub status: String,
    #[serde(default)]
    pub verification_url: String,
    /// Why a failed session failed, e.g. `app_store_failed`.
    #[serde(default)]
    pub error_code: Option<String>,
}

impl Client {
    /// Resolve the personal LangSmith API-key owner to a separate `mda dev` principal.
    pub(crate) fn resolve_developer_principal(&self) -> Result<DeveloperPrincipal> {
        let self_hosted = self.is_self_hosted();
        let base = self.public_platform_base()?;
        let auth_path = if self_hosted {
            SELF_HOSTED_PLATFORM_AUTH_PATH
        } else {
            SAAS_PLATFORM_AUTH_PATH
        };
        let auth = self
            .request(&base, Method::GET, auth_path, &[], None)
            .context("LangSmith could not identify the API key owner")?;
        let subject = auth
            .get("ls_user_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                anyhow!(
                    "user memory, user-owned connections, and managed tools in `mda dev` require \
                     a LangSmith Personal Access Token (PAT). Replace the configured \
                     LANGSMITH_API_KEY with a PAT from LangSmith Settings > API Keys > Personal."
                )
            })?;
        let workspace_id = auth
            .get("tenant_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .context("LangSmith auth response missing tenant_id")?;
        let payload = json!({
            "provider": LANGSMITH_DEV_IDENTITY_PROVIDER,
            "issuer": workspace_id,
            "subject": subject,
        });
        let principal = self
            .request(&base, Method::POST, PRINCIPALS_PATH, &[], Some(payload))
            .context("Agent Auth could not resolve the signed-in LangSmith developer")?;
        let principal_id = principal
            .get("principal")
            .and_then(|row| row.get("id"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .ok_or_else(|| anyhow!("Agent Auth principal response missing principal.id"))?;

        Ok(DeveloperPrincipal {
            principal_id: principal_id.to_string(),
            workspace_id: workspace_id.to_string(),
        })
    }

    /// Redirect URI to register with the OAuth provider.
    pub(crate) fn oauth_redirect_uri(&self) -> Result<String> {
        Ok(format!(
            "{}{OAUTH_CALLBACK_PATH}",
            self.public_platform_base()?
        ))
    }

    /// Create a workspace-scoped opaque connection.
    pub fn create_workspace_connection(
        &self,
        slug: &str,
        owner_policy: ConnectionOwnerPolicy,
        agent_owner_id: Option<&str>,
        secret: Option<&str>,
    ) -> Result<ConnectionSummary> {
        let base = self.public_platform_base()?;
        let payload = match owner_policy {
            ConnectionOwnerPolicy::User => {
                if secret.is_some() {
                    bail!("user-owned opaque connections are slots only; omit the secret value");
                }
                // Incomplete secret credential creates the connection row only;
                // per-caller values are collected at runtime.
                json!({
                    "slug": slug,
                    "display_name": slug,
                    "credential": { "kind": KIND_SECRET },
                })
            }
            ConnectionOwnerPolicy::Agent => {
                let secret = secret.context(
                    "a secret value is required to create an agent-owned opaque connection",
                )?;
                let owner_id = agent_owner_id.context(
                    "a deployment id is required to create an agent-owned opaque connection",
                )?;
                json!({
                    "slug": slug,
                    "display_name": slug,
                    "credential": {
                        "kind": KIND_SECRET,
                        "owner_type": "agent",
                        "owner_id": owner_id,
                        "value": secret,
                    },
                })
            }
        };
        let value = self
            .request(&base, Method::POST, CONNECTIONS_PATH, &[], Some(payload))
            .map_err(|err| match secret {
                Some(secret) => redact_secret(err, secret),
                None => err,
            })?;
        let mut connection = decode_connection(value)?;
        connection.owner_policy = Some(owner_policy.as_str().to_string());
        Ok(connection)
    }

    /// Create a workspace-scoped OAuth connection with a nested provider.
    pub fn create_oauth_workspace_connection(
        &self,
        options: CreateOAuthConnection<'_>,
    ) -> Result<ConnectionSummary> {
        let base = self.public_platform_base()?;
        let mut provider = oauth_provider_create_body(&options)?;
        // The provider is named after the connection unless it has its own
        // slug, and provider slugs can't have the "/" namespaces connection
        // slugs can (`mda/linear-app/<deployment>`).
        if options.slug.contains('/') {
            provider["slug"] = json!(options.slug.replace('/', "-"));
        }
        let payload = json!({
            "slug": options.slug,
            "display_name": options.slug,
            "credential": {
                "kind": KIND_OAUTH2,
                "provider": provider,
            },
        });
        let value = self
            .request(&base, Method::POST, CONNECTIONS_PATH, &[], Some(payload))
            .map_err(|err| redact_create_secrets(err, options.client_secret, options))?;
        let mut connection = decode_connection(value)?;
        connection.owner_policy = Some(options.owner_policy.as_str().to_string());
        if let Some(service) = options.service {
            connection.service = Some(service.to_string());
        }
        Ok(connection)
    }

    /// Start OAuth for an agent-owned credential.
    pub(crate) fn create_agent_oauth_authorization_session(
        &self,
        connection_id: &str,
        owner_id: &str,
    ) -> Result<OAuthAuthorizationSession> {
        self.start_agent_oauth_authorization_session(connection_id, owner_id, Map::new())
    }

    /// [`Self::create_agent_oauth_authorization_session`] with fields a grant
    /// adds, such as a GitHub App's `installation_id`.
    pub(crate) fn start_agent_oauth_authorization_session(
        &self,
        connection_id: &str,
        owner_id: &str,
        fields: Map<String, Value>,
    ) -> Result<OAuthAuthorizationSession> {
        let base = self.public_platform_base()?;
        let mut payload = fields;
        payload.insert("connection_id".into(), json!(connection_id));
        payload.insert("owner_type".into(), json!("agent"));
        payload.insert("owner_id".into(), json!(owner_id));
        let value = self.request(
            &base,
            Method::POST,
            OAUTH_AUTHORIZATION_SESSIONS_PATH,
            &[],
            Some(Value::Object(payload)),
        )?;
        let session: OAuthAuthorizationSession = serde_json::from_value(value)
            .context("failed to decode the OAuth authorization session")?;
        if session.id.trim().is_empty() {
            bail!("OAuth authorization session response missing id");
        }
        if session.status != "completed" && session.verification_url.trim().is_empty() {
            bail!("OAuth authorization session response missing verification_url");
        }
        Ok(session)
    }

    /// Wait for an OAuth authorization session to change state.
    pub(crate) fn get_oauth_authorization_session(
        &self,
        session_id: &str,
        wait_seconds: u8,
    ) -> Result<OAuthAuthorizationSession> {
        let base = self.public_platform_base()?;
        let wait_seconds = wait_seconds.to_string();
        let value = self.request(
            &base,
            Method::GET,
            &oauth_authorization_session_path(session_id),
            &[("wait_seconds", &wait_seconds)],
            None,
        )?;
        serde_json::from_value(value)
            .context("failed to decode the OAuth authorization session status")
    }

    /// List every connection row in the authenticated workspace.
    pub fn list_workspace_connections(&self) -> Result<Vec<ConnectionSummary>> {
        let mut connections = self.list_connections_raw()?;
        let providers = if connections
            .iter()
            .any(|connection| connection.oauth_provider_id.is_some())
        {
            self.oauth_provider_index()?
        } else {
            BTreeMap::new()
        };
        for connection in &mut connections {
            enrich_connection(connection, &providers);
        }
        Ok(connections)
    }

    /// Whether a connection carries a credential owned by `agent_owner_id`.
    ///
    /// The connections list is workspace-wide and takes no owner filter, so
    /// ownership is read one row at a time. A connection with no credential for
    /// the owner answers 200 without `credential.credential_id` rather than 404.
    pub fn connection_has_agent_credential(
        &self,
        connection_id: &str,
        agent_owner_id: &str,
    ) -> Result<bool> {
        let base = self.public_platform_base()?;
        let params = [
            ("credential_owner_type", OWNER_POLICY_AGENT),
            ("credential_owner_id", agent_owner_id),
        ];
        let found = self.request_optional(
            &base,
            Method::GET,
            &connection_path(connection_id),
            &params,
            None,
        )?;
        Ok(found.is_some_and(|value| credential_id(&value).is_some()))
    }

    /// The state of the OAuth grant an agent holds on a connection, from
    /// Agent Auth's credential metadata. Never reads the token itself.
    pub(crate) fn agent_oauth_grant(
        &self,
        connection_id: &str,
        agent_owner_id: &str,
    ) -> Result<AgentGrant> {
        let base = self.public_platform_base()?;
        let params = [
            ("credential_owner_type", OWNER_POLICY_AGENT),
            ("credential_owner_id", agent_owner_id),
        ];
        let Some(connection) = self.request_optional(
            &base,
            Method::GET,
            &connection_path(connection_id),
            &params,
            None,
        )?
        else {
            return Ok(AgentGrant::Missing);
        };
        let Some(credential_id) = credential_id(&connection) else {
            return Ok(AgentGrant::Missing);
        };
        // The credential's metadata: its stored status, no token.
        let path = format!("{CREDENTIALS_PATH}/{}", path_id(credential_id));
        let Some(credential) = self.request_optional(&base, Method::GET, &path, &[], None)? else {
            return Ok(AgentGrant::Missing);
        };
        Ok(AgentGrant::from_status(
            credential.pointer("/oauth2/status").and_then(Value::as_str),
        ))
    }

    /// The redirect URI Agent Auth sends OAuth providers, when it publishes
    /// it: its OAuth client metadata, served only for an HTTPS callback.
    pub(crate) fn published_oauth_redirect_uri(&self) -> Result<Option<String>> {
        let base = self.public_platform_base()?;
        let Some(metadata) =
            self.request_optional(&base, Method::GET, OAUTH_CLIENT_METADATA_PATH, &[], None)?
        else {
            return Ok(None);
        };
        Ok(metadata
            .get("redirect_uris")
            .and_then(Value::as_array)
            .and_then(|uris| uris.first())
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|uri| !uri.is_empty())
            .map(str::to_string))
    }

    pub fn find_workspace_connection_by_slug(
        &self,
        slug: &str,
    ) -> Result<Option<ConnectionSummary>> {
        let found = self
            .list_workspace_connections()?
            .into_iter()
            .find(|connection| connection.slug == slug);
        Ok(found)
    }

    pub fn delete_connection(&self, connection_id: &str) -> Result<bool> {
        let base = self.public_platform_base()?;
        let deleted = self
            .request_optional(
                &base,
                Method::DELETE,
                &connection_path(connection_id),
                &[],
                None,
            )?
            .is_some();
        Ok(deleted)
    }

    /// List every OAuth provider slug registered in the caller's workspace.
    pub fn list_oauth_provider_slugs(&self) -> Result<BTreeSet<String>> {
        Ok(self
            .oauth_provider_index()?
            .into_values()
            .map(|provider| provider.slug)
            .collect())
    }

    /// The OAuth providers that mint tokens for the GitHub App `app_id`. Agent
    /// Auth registers an App's provider with the App ID as its `client_id`.
    pub(crate) fn github_app_provider_ids(&self, app_id: &str) -> Result<BTreeSet<String>> {
        Ok(self
            .oauth_provider_index()?
            .into_values()
            .filter(|provider| provider.grant_type == "github_app" && provider.client_id == app_id)
            .map(|provider| provider.id)
            .collect())
    }

    /// Every connection row, without the OAuth provider slugs
    /// [`Self::list_workspace_connections`] looks up for display.
    pub(crate) fn list_connections_raw(&self) -> Result<Vec<ConnectionSummary>> {
        let base = self.public_platform_base()?;
        let page_size = PAGE_SIZE.to_string();
        let mut cursor: Option<String> = None;
        let mut connections = Vec::new();

        loop {
            let mut params = vec![("page_size", page_size.as_str())];
            if let Some(value) = cursor.as_deref() {
                params.push(("cursor", value));
            }
            let value = self.request(&base, Method::GET, CONNECTIONS_PATH, &params, None)?;
            if value.is_null() {
                return Ok(connections);
            }
            let page: ConnectionListPage = serde_json::from_value(value)
                .context("failed to decode the connection list response")?;
            for item in page.items {
                connections.push(decode_connection(item)?);
            }

            match page.next_cursor {
                Some(next) if cursor.as_deref() != Some(next.as_str()) => cursor = Some(next),
                Some(_) => bail!("connection list returned a repeated cursor"),
                None => return Ok(connections),
            }
        }
    }

    fn oauth_provider_index(&self) -> Result<BTreeMap<String, OAuthProviderRecord>> {
        let base = self.public_platform_base()?;
        let page_size = PAGE_SIZE.to_string();
        let mut cursor: Option<String> = None;
        let mut providers = BTreeMap::new();

        loop {
            let mut params = vec![("page_size", page_size.as_str())];
            if let Some(value) = cursor.as_deref() {
                params.push(("cursor", value));
            }
            let value = self.request(&base, Method::GET, OAUTH_PROVIDERS_PATH, &params, None)?;
            let page: OAuthProviderListPage = serde_json::from_value(value)
                .context("failed to decode the OAuth provider list response")?;
            for provider in page.items {
                providers.insert(provider.id.clone(), provider);
            }

            match page.next_cursor {
                Some(next) if cursor.as_deref() != Some(next.as_str()) => cursor = Some(next),
                Some(_) => bail!("OAuth provider list returned a repeated cursor"),
                None => return Ok(providers),
            }
        }
    }
}

/// Build the nested `credential.provider` object for connection create.
fn oauth_provider_create_body(options: &CreateOAuthConnection<'_>) -> Result<Value> {
    if let Some(mcp_server_url) = options.mcp_server_url {
        return Ok(json!({
            "display_name": options.display_name,
            "mcp_server_url": mcp_server_url,
        }));
    }
    let token_url = match options.token_url {
        Some(token_url) => token_url,
        None if options.grant_type == "client_credentials" => {
            return Err(categorized(
                ErrorCategory::UserInput,
                "This OAuth connection needs a token endpoint. Name a catalog service with --oauth, \
                 or pass --token-url.",
            ));
        }
        None => {
            return Err(categorized(
                ErrorCategory::UserInput,
                "This OAuth connection needs endpoints. Name a catalog service with --oauth, \
                 or pass --authorize-url and --token-url.",
            ));
        }
    };
    if options.grant_type != "client_credentials" && options.authorize_url.is_none() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "This OAuth connection needs endpoints. Name a catalog service with --oauth, \
             or pass --authorize-url and --token-url.",
        ));
    }
    let Some(client_id) = options.client_id else {
        return Err(categorized(
            ErrorCategory::UserInput,
            match options.client_registration_url {
                Some(url) => format!(
                    "This OAuth connection needs --client-id (and usually --secret-from-env \
                     for the client secret). Register an app at {url}, then pass the \
                     credentials."
                ),
                None => "This OAuth connection needs --client-id (and usually --secret-from-env \
                     for the client secret). Register an app, then pass the credentials."
                    .to_string(),
            },
        ));
    };
    if options.grant_type == "client_credentials" {
        if !matches!(
            options.token_endpoint_auth_method,
            "client_secret_basic" | "client_secret_post"
        ) {
            return Err(categorized(
                ErrorCategory::UserInput,
                "OAuth client credentials requires client_secret_basic or client_secret_post token endpoint authentication.",
            ));
        }
        let Some(secret) = options.client_secret else {
            return Err(categorized(
                ErrorCategory::UserInput,
                "OAuth client credentials requires a client secret.",
            ));
        };
        let provider = json!({
            "display_name": options.display_name,
            "client_id": client_id,
            "token_url": token_url,
            "token_endpoint_auth_method": options.token_endpoint_auth_method,
            "grant_type": options.grant_type,
            "token_params": options.token_params,
            "token_request_headers": options.token_request_headers,
            "default_scopes": options.scopes,
            "allowed_scopes": options.scopes,
            "client_secret": secret,
        });
        return Ok(provider);
    }
    let authorization_url = options.authorize_url.ok_or_else(|| {
        categorized(
            ErrorCategory::UserInput,
            "This OAuth connection needs endpoints. Name a catalog service with --oauth, \
             or pass --authorize-url and --token-url.",
        )
    })?;
    let mut provider = json!({
        "display_name": options.display_name,
        "client_id": client_id,
        "authorization_url": authorization_url,
        "token_url": token_url,
        "token_endpoint_auth_method": options.token_endpoint_auth_method,
        "grant_type": options.grant_type,
        "authorization_params": options.authorization_params,
        "token_params": options.token_params,
        "token_request_headers": options.token_request_headers,
        "default_scopes": options.scopes,
        "allowed_scopes": options.allowed_scopes,
    });
    if let Some(secret) = options.client_secret {
        provider["client_secret"] = json!(secret);
    }
    Ok(provider)
}

/// Fill display metadata and oauth provider slug.
///
/// Does not invent `owner_policy` when the connections API omits it. Deploy
/// and `connection check` skip ownership mismatch when `owner_policy` is absent;
/// MCP user connections are `kind=oauth2`, not opaque secrets.
fn enrich_connection(
    connection: &mut ConnectionSummary,
    providers: &BTreeMap<String, OAuthProviderRecord>,
) {
    if connection.display_name.is_empty() {
        connection.display_name = connection.slug.clone();
    }
    if let Some(provider_id) = connection.oauth_provider_id.as_deref() {
        if let Some(provider) = providers.get(provider_id) {
            connection.oauth_app_slug = Some(provider.slug.clone());
        }
    }
}

fn connection_path(connection_id: &str) -> String {
    format!("{CONNECTIONS_PATH}/{}", path_id(connection_id))
}

fn oauth_authorization_session_path(session_id: &str) -> String {
    format!(
        "{OAUTH_AUTHORIZATION_SESSIONS_PATH}/{}",
        path_id(session_id)
    )
}

pub(super) fn oauth_provider_path(provider_id: &str) -> String {
    format!("{OAUTH_PROVIDERS_PATH}/{}", path_id(provider_id))
}

/// The OAuth grant an agent holds on a connection, as Agent Auth records it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum AgentGrant {
    /// No credential: nobody approved the install, or it was removed.
    Missing,
    Active,
    /// The grant lapsed (the provider refused to renew it) and needs consent
    /// again. Agent Auth keeps the credential, and a new authorization
    /// session on the same connection makes it active again.
    AuthorizationRequired,
    /// The grant was revoked and Agent Auth is removing the credential. The
    /// app itself, its connection and webhook secret, is still good, so setup
    /// keeps it and has an admin approve it again, as for
    /// [`Self::AuthorizationRequired`]. Counting it as [`Self::Missing`] would
    /// delete the connection and ask for a brand-new app instead. It is its own
    /// variant so setup can say why consent is needed.
    Revoked,
    /// A status this CLI does not know, kept verbatim so setup can name it
    /// rather than quietly treating the grant as healthy.
    Unknown(String),
}

impl AgentGrant {
    /// Agent Auth's `oauth2.status`. A credential without a status is active,
    /// as Agent Auth treats it.
    fn from_status(status: Option<&str>) -> Self {
        match status {
            None | Some("active") => Self::Active,
            Some("authorization_required") => Self::AuthorizationRequired,
            Some("revocation_pending") => Self::Revoked,
            Some(other) => Self::Unknown(other.to_string()),
        }
    }
}

/// Agent Auth ids are UUIDs; anything else must not change the request path.
fn path_id(id: &str) -> &str {
    if uuid::Uuid::try_parse(id).is_ok() {
        id
    } else {
        "invalid"
    }
}

/// Linked credential id from a connection read with owner query parameters.
fn credential_id(connection: &Value) -> Option<&str> {
    connection
        .get("credential")?
        .get("credential_id")?
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

/// Flatten nested `credential.{kind,oauth_provider_id}` onto the summary shape
/// the CLI and deploy gates already speak.
pub(super) fn decode_connection(value: Value) -> Result<ConnectionSummary> {
    let Value::Object(mut obj) = value else {
        bail!("connection response must be a JSON object");
    };
    if let Some(credential) = obj.get("credential").cloned() {
        if let Some(kind) = credential.get("kind") {
            obj.insert("kind".into(), kind.clone());
        }
        if let Some(provider_id) = credential.get("oauth_provider_id") {
            obj.insert("oauth_provider_id".into(), provider_id.clone());
        }
    }
    if !obj.contains_key("kind") {
        bail!("connection response missing credential.kind");
    }
    serde_json::from_value(Value::Object(obj)).context("failed to decode the connection response")
}

/// [`ApiError::from_response`] falls back to the whole response body when it
/// finds no `detail` it can humanize, and the top-level handler prints the
/// entire error chain, so a server that echoed its input would print the secret.
///
/// A credential can also be echoed without its wrapper — a `Bearer <token>`
/// header value rejected as `invalid token: <token>` — so the header test
/// strips a `scheme ` prefix as well as matching the whole value.
pub(crate) fn redact_secret(err: anyhow::Error, secret: &str) -> anyhow::Error {
    let Some(api) = err
        .chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())
    else {
        return err;
    };
    let is_raw_body = api.detail.trim_start().starts_with(['{', '[']);
    if is_raw_body || detail_echoes_secret(&api.detail, secret) {
        // Rebuilt rather than replaced, so `error::category_of` can still read
        // the status off it.
        return anyhow!(ApiError {
            status: api.status,
            detail: "the response was withheld because it echoed the value".to_string(),
        });
    }
    err
}

/// Does an error detail repeat a secret, either whole or after one scheme
/// token such as `Bearer` / `Basic` / `token`?
fn detail_echoes_secret(detail: &str, secret: &str) -> bool {
    if detail.contains(secret) {
        return true;
    }
    let scheme = secret.split_once(' ').map(|(scheme, _)| scheme);
    match scheme {
        Some(scheme) if !scheme.contains('=') => secret
            .strip_prefix(scheme)
            .and_then(|rest| rest.strip_prefix(' '))
            .is_some_and(|credential| detail.contains(credential)),
        _ => false,
    }
}

/// Redact every write-only value a connection create carries: the client
/// secret and each token request header value. Any one of them failing the
/// same echo test withholds the response.
fn redact_create_secrets(
    err: anyhow::Error,
    client_secret: Option<&str>,
    options: CreateOAuthConnection<'_>,
) -> anyhow::Error {
    let mut err = match client_secret {
        Some(secret) => redact_secret(err, secret),
        None => err,
    };
    for value in options.token_request_headers.values() {
        if let Some(value) = value.as_str() {
            err = redact_secret(err, value);
        }
    }
    err
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    use serde_json::json;

    use super::*;
    use crate::langsmith::test_support::serve_once;
    use crate::langsmith::{Credential, REQUEST_TIMEOUT};

    const CONN: &str = "77777777-7777-7777-7777-777777777777";

    fn client_for(platform_api_url: String) -> Client {
        Client {
            http: crate::http::client_builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .unwrap(),
            credential: Credential::ApiKey("key".into()),
            tenant_id: Some("tenant".into()),
            host_backend_url: "http://unused.invalid".into(),
            platform_api_url: Some(platform_api_url),
        }
    }

    fn self_hosted_client_for(platform_api_url: String, base_path: &str) -> Client {
        let base_path = base_path.trim_matches('/');
        let host_backend_url = if base_path.is_empty() {
            format!("{platform_api_url}/api-host")
        } else {
            format!("{platform_api_url}/{base_path}/api-host")
        };
        let platform_api_url = if base_path.is_empty() {
            platform_api_url
        } else {
            format!("{platform_api_url}/{base_path}")
        };
        Client {
            http: crate::http::client_builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .unwrap(),
            credential: Credential::ApiKey("key".into()),
            tenant_id: Some("tenant".into()),
            host_backend_url,
            platform_api_url: Some(platform_api_url),
        }
    }

    fn developer_client_for(base_url: String) -> Client {
        Client {
            http: crate::http::client_builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .unwrap(),
            credential: Credential::ApiKey("personal-key".into()),
            tenant_id: Some("workspace-1".into()),
            // Auth introspection is a platform-API route; a request to the host
            // backend should fail the test rather than reach the stub server.
            host_backend_url: "http://unused.invalid".into(),
            platform_api_url: Some(base_url),
        }
    }

    /// Serves a fixed sequence of responses on successive connections.
    fn serve_pages(pages: Vec<(&'static str, String)>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for (status, body) in pages {
                let Ok((mut stream, _)) = listener.accept() else {
                    return;
                };
                let mut buf = Vec::new();
                let mut tmp = [0_u8; 1024];
                while let Ok(read) = stream.read(&mut tmp) {
                    if read == 0 {
                        break;
                    }
                    buf.extend_from_slice(&tmp[..read]);
                    let request = String::from_utf8_lossy(&buf);
                    if let Some(headers_end) = request.find("\r\n\r\n") {
                        let content_length = request
                            .lines()
                            .find_map(|line| {
                                let (name, value) = line.split_once(':')?;
                                (name.eq_ignore_ascii_case("content-length"))
                                    .then(|| value.trim().parse::<usize>().ok())
                                    .flatten()
                            })
                            .unwrap_or(0);
                        if buf.len() >= headers_end + 4 + content_length {
                            break;
                        }
                    }
                }
                let _ = tx.send(String::from_utf8(buf).unwrap());
                let response = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        (format!("http://{addr}"), rx)
    }

    fn request_json_body(request: &str) -> Value {
        let body = request.split("\r\n\r\n").nth(1).unwrap();
        serde_json::from_str(body).unwrap()
    }

    #[test]
    fn self_hosted_connections_use_the_public_api_prefix() {
        let (url, rx) = serve_once("200 OK", json!({ "items": [] }).to_string());
        let client = self_hosted_client_for(url, "");

        assert!(client.list_workspace_connections().unwrap().is_empty());

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /api/v1/agent-auth/connections?page_size=100 "),
            "{request}"
        );
    }

    #[test]
    fn self_hosted_agent_auth_preserves_the_frontend_base_path() {
        let client = self_hosted_client_for("https://langsmith.example.com".into(), "langsmith");

        assert_eq!(
            client.oauth_redirect_uri().unwrap(),
            "https://langsmith.example.com/langsmith/api/v1/agent-auth/oauth/callback"
        );
    }

    #[test]
    fn saas_agent_auth_keeps_the_direct_platform_route() {
        let client = client_for("https://api.smith.langchain.com".into());

        assert_eq!(
            client.oauth_redirect_uri().unwrap(),
            "https://api.smith.langchain.com/v1/agent-auth/oauth/callback"
        );
    }

    fn workspace_connection_json(slug: &str) -> Value {
        json!({
            "id": CONN,
            "slug": slug,
            "display_name": slug,
            "credential": { "kind": "secret" },
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        })
    }

    fn oauth_connection_json(slug: &str, provider_id: &str) -> Value {
        json!({
            "id": CONN,
            "slug": slug,
            "display_name": slug,
            "credential": {
                "kind": "oauth2",
                "oauth_provider_id": provider_id,
            },
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        })
    }

    #[test]
    fn resolve_developer_principal_uses_dev_provider_with_verified_langsmith_user() {
        let principal_id = "11111111-1111-4111-8111-111111111111";
        let (url, rx) = serve_pages(vec![
            (
                "200 OK",
                json!({
                    "ls_user_id": "22222222-2222-4222-8222-222222222222",
                    "tenant_id": "workspace-1",
                })
                .to_string(),
            ),
            (
                "201 Created",
                json!({ "principal": { "id": principal_id } }).to_string(),
            ),
        ]);
        let client = developer_client_for(url);

        let resolved = client.resolve_developer_principal().unwrap();

        assert_eq!(
            resolved,
            DeveloperPrincipal {
                principal_id: principal_id.into(),
                workspace_id: "workspace-1".into(),
            }
        );
        let auth_request = rx.recv().unwrap();
        assert!(auth_request.starts_with("GET /auth "), "{auth_request}");
        let principal_request = rx.recv().unwrap();
        assert!(principal_request.starts_with("POST /v1/agent-auth/principals "));
        assert_eq!(
            request_json_body(&principal_request),
            json!({
                "provider": "langsmith-dev",
                "issuer": "workspace-1",
                "subject": "22222222-2222-4222-8222-222222222222",
            })
        );
    }

    #[test]
    fn self_hosted_developer_principal_uses_the_public_auth_routes() {
        let principal_id = "11111111-1111-4111-8111-111111111111";
        let (url, rx) = serve_pages(vec![
            (
                "200 OK",
                json!({
                    "ls_user_id": "22222222-2222-4222-8222-222222222222",
                    "tenant_id": "workspace-1",
                })
                .to_string(),
            ),
            (
                "201 Created",
                json!({ "principal": { "id": principal_id } }).to_string(),
            ),
        ]);
        let client = self_hosted_client_for(url, "");

        client.resolve_developer_principal().unwrap();

        assert!(rx.recv().unwrap().starts_with("GET /api/v1/auth "));
        assert!(rx
            .recv()
            .unwrap()
            .starts_with("POST /api/v1/agent-auth/principals "));
    }

    #[test]
    fn resolve_developer_principal_rejects_service_key_auth() {
        let (url, _rx) = serve_once(
            "200 OK",
            json!({ "tenant_id": "workspace-1", "service_identity": "workspace" }).to_string(),
        );
        let client = developer_client_for(url);

        let error = client.resolve_developer_principal().unwrap_err();

        assert!(
            error
                .to_string()
                .contains("user memory, user-owned connections, and managed tools in `mda dev` require a LangSmith Personal Access Token (PAT)"),
            "{error}"
        );
    }

    #[test]
    fn create_agent_connection_for_existing_deployment_posts_agent_owner() {
        let (url, rx) = serve_once(
            "201 Created",
            workspace_connection_json("my-tavily-key").to_string(),
        );
        let client = client_for(url);

        let created = client
            .create_workspace_connection(
                "my-tavily-key",
                ConnectionOwnerPolicy::Agent,
                Some("11111111-1111-4111-8111-111111111111"),
                Some("s3cr3t"),
            )
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.contains("POST /v1/agent-auth/connections"),
            "unexpected request: {request}"
        );
        assert_eq!(
            request_json_body(&request),
            json!({
                "slug": "my-tavily-key",
                "display_name": "my-tavily-key",
                "credential": {
                    "kind": "secret",
                    "owner_type": "agent",
                    "owner_id": "11111111-1111-4111-8111-111111111111",
                    "value": "s3cr3t",
                },
            })
        );
        assert_eq!(created.slug, "my-tavily-key");
        assert_eq!(created.kind, "secret");
        assert_eq!(created.owner_policy.as_deref(), Some("agent"));
    }

    #[test]
    fn create_agent_connection_requires_a_deployment_id() {
        let client = client_for("http://unused.invalid".into());

        let error = client
            .create_workspace_connection(
                "my-tavily-key",
                ConnectionOwnerPolicy::Agent,
                None,
                Some("s3cr3t"),
            )
            .unwrap_err();

        assert!(
            error
                .to_string()
                .contains("a deployment id is required to create an agent-owned opaque connection"),
            "{error}"
        );
    }

    #[test]
    fn create_user_opaque_slot_posts_incomplete_secret_credential() {
        let (url, rx) = serve_once(
            "201 Created",
            workspace_connection_json("tavily-user").to_string(),
        );
        let client = client_for(url);

        let created = client
            .create_workspace_connection("tavily-user", ConnectionOwnerPolicy::User, None, None)
            .unwrap();

        let request = rx.recv().unwrap();
        assert_eq!(
            request_json_body(&request),
            json!({
                "slug": "tavily-user",
                "display_name": "tavily-user",
                "credential": { "kind": "secret" },
            })
        );
        assert_eq!(created.owner_policy.as_deref(), Some("user"));
    }

    #[test]
    fn list_workspace_connections_reads_connections_api() {
        let (url, rx) = serve_once(
            "200 OK",
            json!({ "items": [], "next_cursor": null }).to_string(),
        );
        let client = client_for(url);

        let found = client.list_workspace_connections().unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.contains("GET /v1/agent-auth/connections?page_size=100"),
            "{request}"
        );
        assert!(!request.contains("owner_type="), "{request}");
        assert!(found.is_empty());
    }

    #[test]
    fn connection_has_agent_credential_reads_the_owner_filtered_connection() {
        let mut body = workspace_connection_json("my-tavily-key");
        body["credential"]["credential_id"] = json!(CONN);
        let (url, rx) = serve_once("200 OK", body.to_string());
        let client = client_for(url);

        let owned = client
            .connection_has_agent_credential(CONN, "11111111-1111-4111-8111-111111111111")
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(request.contains("credential_owner_type=agent"), "{request}");
        assert!(
            request.contains("credential_owner_id=11111111-1111-4111-8111-111111111111"),
            "{request}"
        );
        assert!(owned);
    }

    #[test]
    fn grant_statuses_map_to_what_setup_does_with_them() {
        assert_eq!(AgentGrant::from_status(None), AgentGrant::Active);
        assert_eq!(AgentGrant::from_status(Some("active")), AgentGrant::Active);
        assert_eq!(
            AgentGrant::from_status(Some("authorization_required")),
            AgentGrant::AuthorizationRequired
        );
        // A revoke keeps the app and asks for consent again; it is not missing.
        assert_eq!(
            AgentGrant::from_status(Some("revocation_pending")),
            AgentGrant::Revoked
        );
        assert_eq!(
            AgentGrant::from_status(Some("something_new")),
            AgentGrant::Unknown("something_new".to_string())
        );
    }

    #[test]
    fn agent_oauth_grant_reads_the_credentials_status_never_its_token() {
        const CREDENTIAL: &str = "88888888-8888-4888-8888-888888888888";
        const AGENT: &str = "11111111-1111-4111-8111-111111111111";
        let mut connection = workspace_connection_json("mda/linear-app/x");
        connection["credential"]["credential_id"] = json!(CREDENTIAL);
        let metadata = |status: &str| {
            json!({
                "credential_id": CREDENTIAL,
                "kind": "oauth2",
                "oauth2": { "status": status, "token_type": "Bearer", "scopes": ["read"] },
            })
            .to_string()
        };
        let (url, rx) = serve_pages(vec![
            ("200 OK", connection.to_string()),
            ("200 OK", metadata("active")),
        ]);
        assert_eq!(
            client_for(url).agent_oauth_grant(CONN, AGENT).unwrap(),
            AgentGrant::Active
        );
        let owner = rx.recv().unwrap();
        assert!(owner.contains("credential_owner_id=11111111"), "{owner}");
        let read = rx.recv().unwrap();
        assert!(
            read.starts_with(&format!("GET /v1/agent-auth/credentials/{CREDENTIAL} ")),
            "{read}"
        );

        // A lapsed grant keeps its credential, marked for consent.
        let (url, _rx) = serve_pages(vec![
            ("200 OK", connection.to_string()),
            ("200 OK", metadata("authorization_required")),
        ]);
        assert_eq!(
            client_for(url).agent_oauth_grant(CONN, AGENT).unwrap(),
            AgentGrant::AuthorizationRequired
        );
        let (url, _rx) = serve_pages(vec![
            ("200 OK", connection.to_string()),
            ("200 OK", metadata("revocation_pending")),
        ]);
        assert_eq!(
            client_for(url).agent_oauth_grant(CONN, AGENT).unwrap(),
            AgentGrant::Revoked
        );
        // A status this CLI does not know is named, not taken as active.
        let (url, _rx) = serve_pages(vec![
            ("200 OK", connection.to_string()),
            ("200 OK", metadata("suspended")),
        ]);
        assert_eq!(
            client_for(url).agent_oauth_grant(CONN, AGENT).unwrap(),
            AgentGrant::Unknown("suspended".to_string())
        );
        // No credential for the agent at all.
        let (url, _rx) = serve_once(
            "200 OK",
            workspace_connection_json("mda/linear-app/x").to_string(),
        );
        assert_eq!(
            client_for(url).agent_oauth_grant(CONN, AGENT).unwrap(),
            AgentGrant::Missing
        );
    }

    #[test]
    fn published_oauth_redirect_uri_reads_agent_auths_client_metadata() {
        let (url, rx) = serve_once(
            "200 OK",
            json!({
                "client_id": "https://api.smith.example/v1/agent-auth/oauth/client-metadata.json",
                "redirect_uris": ["https://api.smith.example/v1/agent-auth/oauth/callback"],
            })
            .to_string(),
        );
        assert_eq!(
            client_for(url)
                .published_oauth_redirect_uri()
                .unwrap()
                .as_deref(),
            Some("https://api.smith.example/v1/agent-auth/oauth/callback")
        );
        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /v1/agent-auth/oauth/client-metadata.json "),
            "{request}"
        );
        // Not published, e.g. for an http:// callback.
        let (url, _rx) = serve_once("404 Not Found", "{}".to_string());
        assert_eq!(
            client_for(url).published_oauth_redirect_uri().unwrap(),
            None
        );
    }

    #[test]
    fn a_connection_without_a_credential_for_the_owner_is_not_owned() {
        let (url, _rx) = serve_once(
            "200 OK",
            workspace_connection_json("another-projects-key").to_string(),
        );
        let client = client_for(url);

        assert!(!client
            .connection_has_agent_credential(CONN, "11111111-1111-4111-8111-111111111111")
            .unwrap());
    }

    #[test]
    fn a_missing_connection_is_not_owned() {
        let (url, _rx) = serve_once(
            "404 Not Found",
            json!({ "detail": "not found" }).to_string(),
        );
        let client = client_for(url);

        assert!(!client
            .connection_has_agent_credential(CONN, "11111111-1111-4111-8111-111111111111")
            .unwrap());
    }

    #[test]
    fn list_workspace_connections_follows_next_cursor() {
        let page_one = json!({
            "items": [workspace_connection_json("alpha")],
            "next_cursor": "page-2",
        });
        let page_two = json!({
            "items": [workspace_connection_json("beta")],
            "next_cursor": null,
        });
        let (url, rx) = serve_pages(vec![
            ("200 OK", page_one.to_string()),
            ("200 OK", page_two.to_string()),
        ]);
        let client = client_for(url);

        let found = client.list_workspace_connections().unwrap();

        let first = rx.recv().unwrap();
        assert!(
            first.contains("GET /v1/agent-auth/connections?page_size=100")
                && !first.contains("cursor="),
            "{first}"
        );
        let second = rx.recv().unwrap();
        assert!(second.contains("cursor=page-2"), "{second}");
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].slug, "alpha");
        assert_eq!(found[1].slug, "beta");
        assert!(found[0].owner_policy.is_none());
    }

    #[test]
    fn list_oauth_provider_slugs_reads_the_workspace_registry() {
        let (url, rx) = serve_once(
            "200 OK",
            json!({
                "items": [
                    { "id": "11111111-1111-1111-1111-111111111111", "slug": "notion" },
                    { "id": "22222222-2222-2222-2222-222222222222", "slug": "linear" }
                ],
                "next_cursor": null
            })
            .to_string(),
        );
        let client = client_for(url);

        let slugs = client.list_oauth_provider_slugs().unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.contains("GET /v1/agent-auth/oauth-providers?page_size=100"),
            "unexpected request: {request}"
        );
        assert_eq!(
            slugs,
            ["linear".to_string(), "notion".to_string()]
                .into_iter()
                .collect()
        );
    }

    #[test]
    fn github_app_provider_ids_match_the_app_id() {
        let (url, _rx) = serve_once(
            "200 OK",
            json!({
                "items": [
                    { "id": "p-1", "slug": "mda-github-app-dep-1", "client_id": "42", "grant_type": "github_app" },
                    { "id": "p-2", "slug": "mda-github-app-dep-2", "client_id": "43", "grant_type": "github_app" },
                    { "id": "p-3", "slug": "notion", "client_id": "42", "grant_type": "authorization_code" }
                ],
                "next_cursor": null
            })
            .to_string(),
        );

        let ids = client_for(url).github_app_provider_ids("42").unwrap();

        assert_eq!(ids, ["p-1".to_string()].into_iter().collect());
    }

    #[test]
    fn create_oauth_workspace_connection_names_a_namespaced_provider() {
        let (url, rx) = serve_once(
            "201 Created",
            oauth_connection_json(
                "mda/linear-app/dep-1",
                "11111111-1111-1111-1111-111111111111",
            )
            .to_string(),
        );
        client_for(url)
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "mda/linear-app/dep-1",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: None,
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                scopes: &["read".to_string()],
                authorize_url: Some("https://linear.app/oauth/authorize"),
                token_url: Some("https://api.linear.app/oauth/token"),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &Map::new(),
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                display_name: "Linear",
                allowed_scopes: &["read".to_string()],
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap();

        let body = request_json_body(&rx.recv().unwrap());
        assert_eq!(body["slug"], "mda/linear-app/dep-1");
        assert_eq!(
            body["credential"]["provider"]["slug"],
            "mda-linear-app-dep-1"
        );
    }

    #[test]
    fn create_oauth_workspace_connection_posts_nested_provider() {
        let provider_id = "11111111-1111-1111-1111-111111111111";
        let (url, rx) = serve_once(
            "201 Created",
            oauth_connection_json("gtm-readonly", provider_id).to_string(),
        );
        let client = client_for(url);
        let params: Map<String, Value> =
            serde_json::from_value(json!({ "access_type": "offline" })).unwrap();
        let headers: Map<String, Value> =
            serde_json::from_value(json!({ "Authorization": "Bearer publishable-key" })).unwrap();

        let created = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "gtm-readonly",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: Some("google"),
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                scopes: &["openid".to_string()],
                authorize_url: Some("https://accounts.google.com/o/oauth2/v2/auth"),
                token_url: Some("https://oauth2.googleapis.com/token"),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &params,
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &headers,
                display_name: "Google",
                allowed_scopes: &["openid".to_string(), "email".to_string()],
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap();

        let create_request = rx.recv().unwrap();
        assert!(
            create_request.contains("POST /v1/agent-auth/connections"),
            "{create_request}"
        );
        assert_eq!(
            request_json_body(&create_request),
            json!({
                "slug": "gtm-readonly",
                "display_name": "gtm-readonly",
                "credential": {
                    "kind": "oauth2",
                    "provider": {
                        "display_name": "Google",
                        "client_id": "client-id",
                        "client_secret": "client-secret",
                        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
                        "token_url": "https://oauth2.googleapis.com/token",
                        "token_endpoint_auth_method": "client_secret_post",
                        "grant_type": "authorization_code",
                        "authorization_params": { "access_type": "offline" },
                        "token_params": {},
                        "token_request_headers": { "Authorization": "Bearer publishable-key" },
                        "default_scopes": ["openid"],
                        "allowed_scopes": ["openid", "email"],
                    },
                },
            })
        );
        assert_eq!(created.kind, "oauth2");
        assert_eq!(created.oauth_provider_id.as_deref(), Some(provider_id));
        assert_eq!(created.oauth_app_slug, None);
        assert_eq!(created.service.as_deref(), Some("google"));
    }

    #[test]
    fn create_client_credentials_connection_posts_token_only_provider() {
        let provider_id = "11111111-1111-1111-1111-111111111111";
        let (url, rx) = serve_once(
            "201 Created",
            oauth_connection_json("spotify", provider_id).to_string(),
        );
        let client = client_for(url);
        let token_params: Map<String, Value> =
            serde_json::from_value(json!({ "audience": "music-api" })).unwrap();
        let headers: Map<String, Value> =
            serde_json::from_value(json!({ "X-Provider-Version": "2024-01" })).unwrap();

        client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "spotify",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: Some("spotify"),
                client_id: Some("client-id"),
                client_secret: Some("fixture-secret"),
                display_name: "Spotify",
                scopes: &["playlist-read".to_string()],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: Some("https://accounts.spotify.com/api/token"),
                token_endpoint_auth_method: "client_secret_basic",
                authorization_params: &Map::new(),
                grant_type: "client_credentials",
                token_params: &token_params,
                token_request_headers: &headers,
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap();

        assert_eq!(
            request_json_body(&rx.recv().unwrap()),
            json!({
                "slug": "spotify",
                "display_name": "spotify",
                "credential": {
                    "kind": "oauth2",
                    "provider": {
                        "display_name": "Spotify",
                        "client_id": "client-id",
                        "client_secret": "fixture-secret",
                        "token_url": "https://accounts.spotify.com/api/token",
                        "token_endpoint_auth_method": "client_secret_basic",
                        "grant_type": "client_credentials",
                        "token_params": { "audience": "music-api" },
                        "token_request_headers": { "X-Provider-Version": "2024-01" },
                        "default_scopes": ["playlist-read"],
                        "allowed_scopes": ["playlist-read"],
                    },
                },
            })
        );
    }

    #[test]
    fn client_credentials_provider_allows_empty_scopes() {
        let provider = oauth_provider_create_body(&CreateOAuthConnection {
            slug: "service",
            owner_policy: ConnectionOwnerPolicy::Agent,
            service: None,
            client_id: Some("client-id"),
            client_secret: Some("fixture-secret"),
            display_name: "Service",
            scopes: &[],
            allowed_scopes: &[],
            authorize_url: None,
            token_url: Some("https://example.com/token"),
            token_endpoint_auth_method: "client_secret_post",
            authorization_params: &Map::new(),
            grant_type: "client_credentials",
            token_params: &Map::new(),
            token_request_headers: &Map::new(),
            client_registration_url: None,
            mcp_server_url: None,
        })
        .unwrap();

        assert_eq!(provider["default_scopes"], json!([]));
        assert_eq!(provider["allowed_scopes"], json!([]));
    }

    #[test]
    fn create_oauth_redacts_header_values_from_api_errors() {
        let (url, _rx) = serve_once(
            "400 Bad Request",
            "{\"detail\": \"invalid header value: Bearer pk_live_secret\"}".to_string(),
        );
        let client = client_for(url);
        let headers: Map<String, Value> =
            serde_json::from_value(json!({ "Authorization": "Bearer pk_live_secret" })).unwrap();

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "stripe-link",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: None,
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                display_name: "Stripe",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: Some("https://api.stripe.com/oauth/token"),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &Map::new(),
                grant_type: "client_credentials",
                token_params: &Map::new(),
                token_request_headers: &headers,
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap_err()
            .to_string();

        assert!(!error.contains("pk_live_secret"), "{error}");
        assert!(error.contains("withheld"), "{error}");
    }

    #[test]
    fn create_oauth_redacts_a_header_credential_echoed_without_its_scheme() {
        let (url, _rx) = serve_once(
            "400 Bad Request",
            "{\"detail\": \"invalid token: sk_live_partial\"}".to_string(),
        );
        let client = client_for(url);
        let headers: Map<String, Value> =
            serde_json::from_value(json!({ "Authorization": "Bearer sk_live_partial" })).unwrap();

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "stripe-link",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: None,
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                display_name: "Stripe",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: Some("https://api.stripe.com/oauth/token"),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &Map::new(),
                grant_type: "client_credentials",
                token_params: &Map::new(),
                token_request_headers: &headers,
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap_err()
            .to_string();

        assert!(!error.contains("sk_live_partial"), "{error}");
        assert!(error.contains("withheld"), "{error}");
    }

    #[test]
    fn create_oauth_redacts_header_values_when_the_body_is_echoed_raw() {
        let (url, _rx) = serve_once("400 Bad Request", "Bearer sk_live_raw not even json".into());
        let client = client_for(url);
        let headers: Map<String, Value> =
            serde_json::from_value(json!({ "X-Api-Key": "sk_live_raw" })).unwrap();

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "acme",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: None,
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                display_name: "Acme",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: Some("https://auth.acme.com/token"),
                token_endpoint_auth_method: "client_secret_post",
                authorization_params: &Map::new(),
                grant_type: "client_credentials",
                token_params: &Map::new(),
                token_request_headers: &headers,
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap_err()
            .to_string();

        assert!(!error.contains("sk_live_raw"), "{error}");
        assert!(error.contains("withheld"), "{error}");
    }

    #[test]
    fn client_credentials_provider_requires_a_client_secret() {
        let error = oauth_provider_create_body(&CreateOAuthConnection {
            slug: "spotify",
            owner_policy: ConnectionOwnerPolicy::Agent,
            service: Some("spotify"),
            client_id: Some("client-id"),
            client_secret: None,
            display_name: "Spotify",
            scopes: &[],
            allowed_scopes: &[],
            authorize_url: None,
            token_url: Some("https://accounts.spotify.com/api/token"),
            token_endpoint_auth_method: "client_secret_basic",
            authorization_params: &Map::new(),
            grant_type: "client_credentials",
            token_params: &Map::new(),
            token_request_headers: &Map::new(),
            client_registration_url: None,
            mcp_server_url: None,
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("requires a client secret"), "{error}");
    }

    #[test]
    fn client_credentials_provider_rejects_public_client_auth() {
        let error = oauth_provider_create_body(&CreateOAuthConnection {
            slug: "spotify",
            owner_policy: ConnectionOwnerPolicy::Agent,
            service: Some("spotify"),
            client_id: Some("client-id"),
            client_secret: Some("fixture-secret"),
            display_name: "Spotify",
            scopes: &[],
            allowed_scopes: &[],
            authorize_url: None,
            token_url: Some("https://accounts.spotify.com/api/token"),
            token_endpoint_auth_method: "none",
            authorization_params: &Map::new(),
            grant_type: "client_credentials",
            token_params: &Map::new(),
            token_request_headers: &Map::new(),
            client_registration_url: None,
            mcp_server_url: None,
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("client_secret_basic"), "{error}");
    }

    #[test]
    fn client_credentials_provider_requires_a_token_url() {
        let error = oauth_provider_create_body(&CreateOAuthConnection {
            slug: "spotify",
            owner_policy: ConnectionOwnerPolicy::Agent,
            service: Some("spotify"),
            client_id: Some("client-id"),
            client_secret: Some("fixture-secret"),
            display_name: "Spotify",
            scopes: &[],
            allowed_scopes: &[],
            authorize_url: None,
            token_url: None,
            token_endpoint_auth_method: "client_secret_basic",
            authorization_params: &Map::new(),
            grant_type: "client_credentials",
            token_params: &Map::new(),
            token_request_headers: &Map::new(),
            client_registration_url: None,
            mcp_server_url: None,
        })
        .unwrap_err()
        .to_string();

        assert!(error.contains("token endpoint"), "{error}");
    }

    #[test]
    fn create_agent_oauth_authorization_session_posts_agent_scope() {
        let (url, rx) = serve_once(
            "201 Created",
            json!({
                "id": "88888888-8888-8888-8888-888888888888",
                "status": "pending",
                "verification_url": "https://accounts.example.com/authorize?state=session",
            })
            .to_string(),
        );
        let client = client_for(url);

        let session = client
            .create_agent_oauth_authorization_session(CONN, "agent-id")
            .unwrap();

        let request = rx.recv().unwrap();
        assert_eq!(
            request_json_body(&request),
            json!({
                "connection_id": CONN,
                "owner_type": "agent",
                "owner_id": "agent-id",
            })
        );
        assert_eq!(
            session.verification_url,
            "https://accounts.example.com/authorize?state=session"
        );
    }

    #[test]
    fn completed_authorization_session_may_omit_verification_url() {
        let (url, _rx) = serve_once(
            "201 Created",
            json!({
                "id": "88888888-8888-8888-8888-888888888888",
                "status": "completed",
                "credential_id": "99999999-9999-9999-9999-999999999999",
            })
            .to_string(),
        );
        let client = client_for(url);

        let session = client
            .create_agent_oauth_authorization_session(CONN, "agent-id")
            .unwrap();

        assert_eq!(session.status, "completed");
        assert!(session.verification_url.is_empty());
    }

    #[test]
    fn pending_authorization_session_requires_verification_url() {
        let (url, _rx) = serve_once(
            "201 Created",
            json!({
                "id": "88888888-8888-8888-8888-888888888888",
                "status": "pending",
            })
            .to_string(),
        );
        let client = client_for(url);

        let error = client
            .create_agent_oauth_authorization_session(CONN, "agent-id")
            .unwrap_err()
            .to_string();

        assert!(error.contains("missing verification_url"), "{error}");
    }

    #[test]
    fn get_oauth_authorization_session_long_polls_for_completion() {
        let (url, rx) = serve_once(
            "200 OK",
            json!({
                "id": "88888888-8888-8888-8888-888888888888",
                "status": "completed",
                "credential_id": "99999999-9999-9999-9999-999999999999",
            })
            .to_string(),
        );
        let client = client_for(url);

        let session = client
            .get_oauth_authorization_session("88888888-8888-8888-8888-888888888888", 25)
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.contains(
                "GET /v1/agent-auth/oauth-authorization-sessions/88888888-8888-8888-8888-888888888888?wait_seconds=25"
            ),
            "{request}"
        );
        assert_eq!(session.status, "completed");
    }

    #[test]
    fn create_oauth_without_client_id_fails_closed() {
        let client = client_for("http://unused.invalid".into());

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "notion-docs",
                owner_policy: ConnectionOwnerPolicy::User,
                service: Some("notion"),
                client_id: None,
                client_secret: None,
                display_name: "Notion",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: Some("https://api.notion.com/v1/oauth/authorize"),
                token_url: Some("https://api.notion.com/v1/oauth/token"),
                token_endpoint_auth_method: "client_secret_basic",
                authorization_params: &Map::new(),
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                client_registration_url: Some("https://www.notion.so/my-integrations"),
                mcp_server_url: None,
            })
            .unwrap_err()
            .to_string();

        assert!(error.contains("--client-id"), "{error}");
        assert!(
            error.contains("https://www.notion.so/my-integrations"),
            "{error}"
        );
        assert!(!error.contains("/v1/agent-auth/oauth/callback"), "{error}");
    }

    #[test]
    fn registering_without_known_endpoints_fails_closed() {
        let client = client_for("http://unused.invalid".into());

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "acme-api",
                owner_policy: ConnectionOwnerPolicy::User,
                service: None,
                client_id: Some("client-id"),
                client_secret: Some("client-secret"),
                display_name: "acme-oauth",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: None,
                token_endpoint_auth_method: "client_secret_basic",
                authorization_params: &Map::new(),
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap_err()
            .to_string();

        assert!(error.contains("needs endpoints"), "{error}");
        assert!(error.contains("--authorize-url"), "{error}");
    }

    #[test]
    fn create_oauth_mcp_dcr_posts_mcp_server_url() {
        let provider_id = "11111111-1111-1111-1111-111111111111";
        let (url, rx) = serve_once(
            "201 Created",
            oauth_connection_json("notion-mcp", provider_id).to_string(),
        );
        let client = client_for(url);

        let created = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "notion-mcp",
                owner_policy: ConnectionOwnerPolicy::User,
                service: Some("notion"),
                client_id: None,
                client_secret: None,
                display_name: "Notion MCP",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: None,
                token_endpoint_auth_method: "none",
                authorization_params: &Map::new(),
                grant_type: "authorization_code",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                client_registration_url: None,
                mcp_server_url: Some("https://mcp.notion.com/mcp"),
            })
            .unwrap();

        assert_eq!(
            request_json_body(&rx.recv().unwrap()),
            json!({
                "slug": "notion-mcp",
                "display_name": "notion-mcp",
                "credential": {
                    "kind": "oauth2",
                    "provider": {
                        "display_name": "Notion MCP",
                        "mcp_server_url": "https://mcp.notion.com/mcp",
                    },
                },
            })
        );
        assert_eq!(created.oauth_provider_id.as_deref(), Some(provider_id));
    }

    #[test]
    fn list_workspace_connections_enriches_oauth_app_slug() {
        let provider_id = "11111111-1111-1111-1111-111111111111";
        let (url, rx) = serve_pages(vec![
            (
                "200 OK",
                json!({
                    "items": [oauth_connection_json("notion-docs", provider_id)],
                    "next_cursor": null,
                })
                .to_string(),
            ),
            (
                "200 OK",
                json!({
                    "items": [{ "id": provider_id, "slug": "notion" }],
                    "next_cursor": null,
                })
                .to_string(),
            ),
        ]);
        let client = client_for(url);

        let found = client.list_workspace_connections().unwrap();

        assert!(rx
            .recv()
            .unwrap()
            .contains("GET /v1/agent-auth/connections"));
        assert!(rx
            .recv()
            .unwrap()
            .contains("GET /v1/agent-auth/oauth-providers"));
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].kind, "oauth2");
        assert_eq!(found[0].oauth_app_slug.as_deref(), Some("notion"));
    }

    #[test]
    fn a_response_that_echoes_the_secret_is_not_printed() {
        // Built at runtime so static analysis does not read it as a hard-coded
        // credential.
        let secret = format!("{}-{}", "fixture", "value");
        // Items with no `msg` are the shape `humanize_error_body` gives up on,
        // which makes `ApiError` fall back to the entire raw response body.
        let body = json!({
            "detail": [{
                "loc": ["body", "secret"],
                "type": "string_too_long",
                "input": secret,
            }]
        })
        .to_string();
        let (url, _rx) = serve_once("422 Unprocessable Entity", body);
        let client = client_for(url);

        let error = client
            .create_workspace_connection(
                "openai-key",
                ConnectionOwnerPolicy::Agent,
                Some("11111111-1111-4111-8111-111111111111"),
                Some(&secret),
            )
            .unwrap_err();

        let rendered = format!("{error:#}");
        assert!(
            !rendered.contains(&secret),
            "the secret reached a user-facing error: {rendered}"
        );
        assert!(rendered.contains("422"), "{rendered}");
        // Redacting must not cost the status, which is what classifies the failure.
        assert_eq!(
            crate::error::category_of(&error),
            crate::error::ErrorCategory::Remote
        );
    }

    #[test]
    fn client_credentials_response_cannot_echo_the_client_secret() {
        let secret = format!("{}-{}", "oauth-fixture", "value");
        let body = json!({ "detail": [{ "input": secret }] }).to_string();
        let (url, _rx) = serve_once("422 Unprocessable Entity", body);
        let client = client_for(url);

        let error = client
            .create_oauth_workspace_connection(CreateOAuthConnection {
                slug: "spotify",
                owner_policy: ConnectionOwnerPolicy::Agent,
                service: Some("spotify"),
                client_id: Some("client-id"),
                client_secret: Some(&secret),
                display_name: "Spotify",
                scopes: &[],
                allowed_scopes: &[],
                authorize_url: None,
                token_url: Some("https://accounts.spotify.com/api/token"),
                token_endpoint_auth_method: "client_secret_basic",
                authorization_params: &Map::new(),
                grant_type: "client_credentials",
                token_params: &Map::new(),
                token_request_headers: &Map::new(),
                client_registration_url: None,
                mcp_server_url: None,
            })
            .unwrap_err();

        let rendered = format!("{error:#}");
        assert!(!rendered.contains(&secret), "secret leaked: {rendered}");
        assert!(rendered.contains("422"), "{rendered}");
    }
}
