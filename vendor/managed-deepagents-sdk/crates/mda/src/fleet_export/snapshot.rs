//! A saved Fleet agent and the typed view mappers read.
//!
//! Field names mirror the Fleet API (`smith-go/fleet/agents/types.go`,
//! `smith-go/fleet/schedules/types.go`). Only fields a mapper reads are typed;
//! the raw responses stay in [`Snapshot`] so a saved agent loses nothing.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The Fleet responses export reads, as `.data/<agent-id>/agent.json` stores
/// them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct Snapshot {
    /// `GET /v1/fleet/agents/{id}?include=files`.
    pub agent: Value,
    /// Every item of `GET /v1/fleet/agents/{id}/schedules`.
    pub schedules: Vec<Value>,
    /// `GET /v1/fleet/mcp-servers`, reduced so no header value is saved.
    #[serde(default)]
    pub mcp_servers: Vec<WorkspaceMcpServer>,
    /// The workspace's connections, to tell which slugs already exist.
    #[serde(default)]
    pub connections: Vec<WorkspaceConnection>,
    /// For each Managed Tools server URL the agent uses (without a trailing
    /// slash), the tools its `tools/list` marks to ask before running, or
    /// `None` when that lookup failed.
    #[serde(default)]
    pub managed_tools_ask_first: BTreeMap<String, Option<BTreeSet<String>>>,
    /// `GET /v1/fleet/access-profiles/{id}` for each of the sandbox's
    /// `policy_ids`. Credentials in them are workspace secret names, not values.
    #[serde(default)]
    pub access_profiles: Vec<Value>,
    /// Whether Fleet confirmed the middleware beta, so a missing `middleware`
    /// means the agent has none.
    #[serde(default = "confirmed_by_default")]
    pub middleware_beta_confirmed: bool,
    /// The caller's `GET /v1/triggers/list?agent_id=` registrations, or `None`
    /// when Trigger Server refused the list.
    #[serde(default = "no_triggers")]
    pub triggers: Option<Vec<TriggerRegistration>>,
}

// Snapshots saved before this field existed weren't asked for channels.
fn no_triggers() -> Option<Vec<TriggerRegistration>> {
    Some(Vec::new())
}

/// A Trigger Server registration: a channel or inbox trigger. Reduced to what
/// export reads, so no app ids or OAuth provider are saved.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct TriggerRegistration {
    pub template_id: String,
    /// `active` or `paused`.
    #[serde(default)]
    pub status: String,
    #[serde(default, deserialize_with = "null_as_default")]
    pub config: TriggerConfig,
}

/// The registration's settings. Custom Slack apps set the first three
/// (`app/triggers/slack/custom_app.py`); the shared Slack app sets
/// `channel_name`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct TriggerConfig {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub app_name: Option<String>,
    /// `mention`, `thread` or `channel`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trigger_mode: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_bot_triggers: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub channel_name: Option<String>,
}

// Snapshots saved before this field existed came from the beta.
fn confirmed_by_default() -> bool {
    true
}

/// A workspace MCP server, as much of it as export needs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct WorkspaceMcpServer {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub auth_type: McpAuthType,
    #[serde(default, deserialize_with = "null_as_default")]
    pub headers: Vec<HeaderName>,
}

/// A header the server sends. The API also returns its value, which has no
/// field here so it is never read or saved.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct HeaderName {
    pub key: String,
}

/// How a workspace MCP server signs in (`auth_type` in the Fleet API).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum McpAuthType {
    Oauth,
    Headers,
    #[default]
    #[serde(other)]
    None,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct WorkspaceConnection {
    /// Agent Auth connection id, which access profile rules refer to.
    #[serde(default)]
    pub id: String,
    pub slug: String,
    /// `secret` or `oauth2`.
    pub kind: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct FleetAgent {
    pub name: String,
    #[serde(default)]
    pub system_prompt: Option<String>,
    #[serde(default)]
    pub model: Option<AgentModel>,
    #[serde(default)]
    pub backend: Option<AgentBackend>,
    #[serde(default)]
    pub tools: Option<ToolsConfig>,
    #[serde(default)]
    pub subagents: Vec<SubagentSpec>,
    #[serde(default)]
    pub middleware: Vec<AgentMiddleware>,
    #[serde(default)]
    pub files: BTreeMap<String, FileEntry>,
    #[serde(default)]
    pub permissions: Option<Permissions>,
    #[serde(skip)]
    pub schedules: Vec<Schedule>,
    #[serde(skip)]
    pub middleware_beta_confirmed: bool,
    #[serde(skip)]
    pub workspace_mcp_servers: Vec<WorkspaceMcpServer>,
    #[serde(skip)]
    pub workspace_connections: Vec<WorkspaceConnection>,
    #[serde(skip)]
    pub managed_tools_ask_first: BTreeMap<String, Option<BTreeSet<String>>>,
    #[serde(skip)]
    pub access_profiles: Vec<AccessProfile>,
    #[serde(skip)]
    pub triggers: Option<Vec<TriggerRegistration>>,
}

impl FleetAgent {
    /// The agent response alone, without the other saved responses.
    pub(crate) fn parse(agent: &Value) -> Result<Self> {
        serde_json::from_value(agent.clone())
            .context("Fleet agent response is not in the expected shape")
    }

    pub(crate) fn from_snapshot(snapshot: &Snapshot) -> Result<Self> {
        let mut agent = Self::parse(&snapshot.agent)?;
        agent.middleware_beta_confirmed = snapshot.middleware_beta_confirmed;
        agent.workspace_mcp_servers = snapshot.mcp_servers.clone();
        agent.workspace_connections = snapshot.connections.clone();
        agent.managed_tools_ask_first = snapshot.managed_tools_ask_first.clone();
        agent.triggers = snapshot.triggers.clone();
        agent.access_profiles = snapshot
            .access_profiles
            .iter()
            .map(|profile| serde_json::from_value(profile.clone()))
            .collect::<Result<_, _>>()
            .context("Fleet access profile is not in the expected shape")?;
        agent.schedules = snapshot
            .schedules
            .iter()
            .map(|schedule| serde_json::from_value(schedule.clone()))
            .collect::<Result<_, _>>()
            .context("Fleet schedule is not in the expected shape")?;
        Ok(agent)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AgentModel {
    /// `provider:model` for built-in models, a workspace model id otherwise.
    #[serde(default)]
    pub id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AgentBackend {
    /// `state` (no computer), `sandbox` or `computer_environment`.
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub sandbox_config: Option<SandboxConfig>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub(crate) struct SandboxConfig {
    /// `thread` (one sandbox per thread) or `agent` (one for every thread).
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub snapshot_id: Option<String>,
    /// Access profiles, fetched into [`Snapshot::access_profiles`].
    #[serde(default, deserialize_with = "null_as_default")]
    pub policy_ids: Vec<String>,
    #[serde(default)]
    pub idle_ttl_seconds: Option<u64>,
    #[serde(default)]
    pub delete_after_stop_seconds: Option<u64>,
}

/// A Fleet access profile (sandbox policy): what the sandbox may reach and
/// which credentials its proxy adds (`smith-go/fleet/accessprofiles/types.go`).
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AccessProfile {
    #[serde(default)]
    pub id: String,
    #[serde(default, deserialize_with = "null_as_default")]
    pub rules: Vec<AccessRule>,
    #[serde(default)]
    pub network: Option<Network>,
    /// AWS and GCP signing rules; their credentials are workspace secret names.
    #[serde(default, deserialize_with = "null_as_default")]
    pub proxy_rules: Vec<Value>,
    #[serde(default, deserialize_with = "null_as_default")]
    pub env_vars: BTreeMap<String, String>,
}

/// Adds `inject` headers, filled from `source`, to requests for `match_hosts`.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AccessRule {
    #[serde(default)]
    pub match_hosts: Vec<String>,
    pub source: RuleSource,
    #[serde(default)]
    pub inject: Vec<Inject>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct RuleSource {
    /// `secret`, `connection` (an OAuth provider) or `agent_auth`.
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub provider_id: String,
    #[serde(default)]
    pub connection_id: String,
    /// The workspace secret's name.
    #[serde(default)]
    pub key: String,
    /// OAuth scopes a `connection` source requests.
    #[serde(default, deserialize_with = "null_as_default")]
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct Inject {
    pub header: String,
    pub value: InjectValue,
}

/// A header value: `template`, or `http_basic` from `username` and
/// `password`. `{value}` and `{access_token}` stand for the credential.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct InjectValue {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub template: String,
    #[serde(default)]
    pub username: String,
    #[serde(default)]
    pub password: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct Network {
    /// `allow`, `deny` or `block_all`.
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default, deserialize_with = "null_as_default")]
    pub hosts: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub(crate) struct ToolsConfig {
    #[serde(default, deserialize_with = "null_as_default")]
    pub tools: Vec<ToolEntry>,
    /// Tool name to `true`, `false`, or an approval config object.
    #[serde(default, deserialize_with = "null_as_default")]
    pub interrupt_config: BTreeMap<String, Value>,
}

// Fleet sends `null` for these when `tools.json` was written without them.
fn null_as_default<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Default + Deserialize<'de>,
{
    Ok(Option::<T>::deserialize(deserializer)?.unwrap_or_default())
}

/// A `tools.json` entry. Fleet also accepts a bare tool name, and an entry
/// without a URL; both mean its own built-in tool server.
#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub(crate) enum ToolEntry {
    Name(String),
    Spec(ToolSpec),
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct ToolSpec {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub mcp_server_url: Option<String>,
    #[serde(default)]
    pub mcp_server_name: Option<String>,
}

/// Whose credentials the Fleet agent runs with.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum Identity {
    /// One set of credentials for every caller.
    Shared,
    /// The caller's own credentials; Fleet's default.
    #[default]
    #[serde(other)]
    Personal,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct Permissions {
    #[serde(default)]
    pub identity: Identity,
    /// `user` (private to its owner, the default) or `tenant` (published to
    /// the workspace).
    #[serde(default)]
    pub visibility: String,
    /// Access level to the user ids granted it (`read`, `run`, `write`).
    #[serde(default, deserialize_with = "null_as_default")]
    pub shared_users: BTreeMap<String, Option<Vec<String>>>,
}

impl Permissions {
    /// Anyone besides the owner can open the agent.
    pub(crate) fn is_shared(&self) -> bool {
        self.visibility == "tenant"
            || self
                .shared_users
                .values()
                .flatten()
                .any(|users| !users.is_empty())
    }
}

/// A Fleet subagent, from `subagents/<name>/AGENTS.md` and `tools.json`.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct SubagentSpec {
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// Unset runs on the agent's model.
    #[serde(default)]
    pub model: Option<AgentModel>,
    /// Fleet echoes the prompt as `instructions` too, its older name.
    #[serde(default)]
    pub system_prompt: String,
    #[serde(default)]
    pub instructions: String,
    #[serde(default)]
    pub tools: Option<ToolsConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AgentMiddleware {
    pub id: String,
    #[serde(default = "enabled_by_default")]
    pub is_enabled: bool,
}

// Fleet treats middleware without `is_enabled` as enabled.
fn enabled_by_default() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct FileEntry {
    /// `file` (inline, the default) or `skill` (a linked workspace skill).
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub content: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct Schedule {
    pub cron: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn workspace_servers_keep_header_names_and_drop_values() {
        let server: WorkspaceMcpServer = serde_json::from_value(json!({
            "name": "Acme",
            "url": "https://mcp.acme.dev/mcp",
            "auth_type": "headers",
            "headers": [{ "key": "Authorization", "value": "Bearer secret" }],
        }))
        .unwrap();
        assert_eq!(server.auth_type, McpAuthType::Headers);
        assert_eq!(server.headers[0].key, "Authorization");
        assert!(!serde_json::to_string(&server).unwrap().contains("secret"));
    }

    #[test]
    fn unknown_auth_types_and_identities_fall_back() {
        let server: WorkspaceMcpServer =
            serde_json::from_value(json!({ "auth_type": "magic", "headers": null })).unwrap();
        assert_eq!(server.auth_type, McpAuthType::None);
        let permissions: Permissions =
            serde_json::from_value(json!({ "identity": "team" })).unwrap();
        assert_eq!(permissions.identity, Identity::Personal);
    }
}
