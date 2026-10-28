//! Read the GitHub channel manifest the runtime emits into
//! `channel-manifests.json`.
//!
//! `channels.github(...)` works out what its App needs, from the events it
//! handles and its `app` options, and puts it in `providerConfig`: the handled
//! events, the webhook events, the permissions and the repositories. The CLI
//! uses it as given. A new App is named after the agent, and the first deploy
//! asks where to create it.

use std::collections::BTreeMap;

use anyhow::{bail, Result};
use serde::Deserialize;

use crate::compile::ChannelRequirements;
use crate::deploy::channel_app::{app_channel_manifest, AppChannelManifest};

pub(crate) const PROVIDER: &str = "github";

/// GitHub limits App names to 34 characters.
const APP_NAME_MAX_CHARS: usize = 34;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum PermissionLevel {
    Read,
    Write,
}

impl PermissionLevel {
    pub(crate) fn parse(value: &str) -> Option<Self> {
        match value {
            "read" => Some(Self::Read),
            "write" => Some(Self::Write),
            _ => None,
        }
    }

    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Write => "write",
        }
    }
}

/// A validated GitHub channel, ready for App setup.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct GitHubChannel {
    /// Channel name (file stem), also the manifest key.
    pub(crate) name: String,
    /// Events route on the Agent Server, e.g. `/channels/github/events`.
    pub(crate) events_path: String,
    /// Handled event keys, e.g. `issues.opened`. Empty for a receipt-only channel.
    pub(crate) handled: Vec<String>,
    /// App webhook events, sorted and deduplicated.
    pub(crate) events: Vec<String>,
    /// App permissions, always including `metadata: read`.
    pub(crate) permissions: BTreeMap<String, PermissionLevel>,
    /// Name of a new App: the agent's, cut to GitHub's limit.
    pub(crate) app_name: String,
    /// Repositories the channel is limited to, as `owner/name`, sorted. All
    /// share one owner. Empty means every repository of the installation.
    pub(crate) repositories: Vec<String>,
    /// Permissions whose level the handled events set, by name; `metadata`,
    /// which every App has, is left out.
    pub(crate) event_permissions: Vec<EventPermission>,
}

/// A token permission at the level handled events need: read to receive
/// them, write to also reply where they happened.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct EventPermission {
    pub(crate) name: String,
    pub(crate) level: PermissionLevel,
    /// Handled event keys that need this level, sorted.
    pub(crate) events: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GitHubProviderConfig {
    events: Vec<String>,
    webhook_events: Vec<String>,
    permissions: Vec<AppPermission>,
    #[serde(default)]
    repositories: Vec<String>,
}

/// A permission the App asks for, with the handled events that need it; no
/// events when the level comes from `app.permissions` or is `metadata`.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AppPermission {
    name: String,
    level: PermissionLevel,
    events: Vec<String>,
}

/// Find the GitHub channel, if the project declares one.
///
/// `wait` is false for `mda deploy --no-wait`: App setup needs the live Agent
/// Server URL, so that combination is rejected before anything is uploaded.
pub(crate) fn prepare(
    requirements: &ChannelRequirements,
    wait: bool,
    assistant_id: &str,
) -> Result<Option<GitHubChannel>> {
    let Some(AppChannelManifest {
        name,
        auto_reply,
        events_path,
        provider_config: config,
        ..
    }) = app_channel_manifest::<GitHubProviderConfig>(requirements, PROVIDER, "GitHub")?
    else {
        return Ok(None);
    };
    if auto_reply {
        bail!(
            "GitHub channels do not reply automatically; give the agent a GitHub MCP server to act on GitHub."
        );
    }

    if !wait {
        bail!("An agent with a GitHub channel cannot be deployed with --no-wait.");
    }

    let event_permissions = config
        .permissions
        .iter()
        .filter(|permission| !permission.events.is_empty())
        .map(|permission| EventPermission {
            name: permission.name.clone(),
            level: permission.level,
            events: permission.events.clone(),
        })
        .collect();
    Ok(Some(GitHubChannel {
        name,
        events_path,
        handled: config.events,
        events: config.webhook_events,
        permissions: config
            .permissions
            .into_iter()
            .map(|permission| (permission.name, permission.level))
            .collect(),
        app_name: default_app_name(assistant_id),
        repositories: config.repositories,
        event_permissions,
    }))
}

fn default_app_name(assistant_id: &str) -> String {
    let name: String = assistant_id.chars().take(APP_NAME_MAX_CHARS).collect();
    name.trim_end_matches(['-', '_', ' ']).to_string()
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    /// What `channels.github(...)` emits for issue, comment and workflow
    /// handlers with `app: { permissions: { contents: "read" } }`.
    fn manifest(name: &str) -> Value {
        json!({
            "name": name,
            "provider": "github",
            "autoReply": false,
            "eventsPath": format!("/channels/{name}/events"),
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": {
                "events": ["issue_comment.created", "issues.opened", "workflow_run.completed"],
                "webhookEvents": ["issue_comment", "issues", "workflow_run"],
                "permissions": [
                    { "name": "actions", "level": "read", "events": ["workflow_run.completed"] },
                    { "name": "contents", "level": "read", "events": [] },
                    { "name": "issues", "level": "write", "events": ["issue_comment.created", "issues.opened"] },
                    { "name": "metadata", "level": "read", "events": [] }
                ]
            }
        })
    }

    fn requirements(entries: Vec<(&str, Value)>) -> ChannelRequirements {
        entries
            .into_iter()
            .map(|(name, value)| (name.to_string(), value))
            .collect()
    }

    #[test]
    fn returns_none_without_a_github_channel() {
        let slack = json!({ "name": "slack", "provider": "slack", "transport": "trigger_server" });
        assert_eq!(
            prepare(&requirements(vec![("slack", slack)]), false, "agent").unwrap(),
            None
        );
    }

    #[test]
    fn uses_the_app_setup_the_runtime_emits() {
        let slack = json!({ "name": "slack", "provider": "slack", "transport": "trigger_server" });
        let mut github = manifest("github");
        github["providerConfig"]["repositories"] = json!(["acme/api", "acme/web"]);
        let channel = prepare(
            &requirements(vec![("github", github), ("slack", slack)]),
            true,
            "a-very-long-assistant-name-that-exceeds-the-limit",
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            channel,
            GitHubChannel {
                name: "github".into(),
                events_path: "/channels/github/events".into(),
                handled: vec![
                    "issue_comment.created".into(),
                    "issues.opened".into(),
                    "workflow_run.completed".into()
                ],
                events: vec![
                    "issue_comment".into(),
                    "issues".into(),
                    "workflow_run".into()
                ],
                permissions: BTreeMap::from([
                    ("actions".to_string(), PermissionLevel::Read),
                    ("contents".to_string(), PermissionLevel::Read),
                    ("issues".to_string(), PermissionLevel::Write),
                    ("metadata".to_string(), PermissionLevel::Read),
                ]),
                app_name: "a-very-long-assistant-name-that-ex".into(),
                repositories: vec!["acme/api".into(), "acme/web".into()],
                event_permissions: vec![
                    EventPermission {
                        name: "actions".into(),
                        level: PermissionLevel::Read,
                        events: vec!["workflow_run.completed".into()],
                    },
                    EventPermission {
                        name: "issues".into(),
                        level: PermissionLevel::Write,
                        events: vec!["issue_comment.created".into(), "issues.opened".into()],
                    },
                ],
            }
        );
    }

    #[test]
    fn rejects_two_github_channels() {
        let error = prepare(
            &requirements(vec![("github", manifest("github")), ("gh", manifest("gh"))]),
            true,
            "agent",
        )
        .unwrap_err()
        .to_string();
        assert_eq!(
            error,
            "An agent can have only one GitHub channel (found: gh, github)."
        );
    }

    #[test]
    fn rejects_no_wait() {
        let error = prepare(
            &requirements(vec![("github", manifest("github"))]),
            false,
            "agent",
        )
        .unwrap_err()
        .to_string();
        assert_eq!(
            error,
            "An agent with a GitHub channel cannot be deployed with --no-wait."
        );
    }

    #[test]
    fn rejects_invalid_configuration() {
        type Mutation = Box<dyn Fn(&mut Value)>;
        let cases: Vec<(&str, Mutation, &str)> = vec![
            (
                "unknown field",
                Box::new(|v| v["extra"] = json!(1)),
                "configuration is invalid",
            ),
            (
                "unknown provider config",
                Box::new(|v| v["providerConfig"]["app"] = json!({})),
                "configuration is invalid",
            ),
            (
                "permission level",
                Box::new(|v| v["providerConfig"]["permissions"][0]["level"] = json!("admin")),
                "configuration is invalid",
            ),
            (
                "transport",
                Box::new(|v| v["transport"] = json!("trigger_server")),
                "must not set a transport",
            ),
            (
                "auto reply",
                Box::new(|v| v["autoReply"] = json!(true)),
                "do not reply automatically",
            ),
            (
                "env",
                Box::new(|v| v["requiredEnv"] = json!(["X"])),
                "requiredEnv",
            ),
            (
                "events path",
                Box::new(|v| v["eventsPath"] = json!("https://x/events")),
                "absolute path",
            ),
            (
                "name mismatch",
                Box::new(|v| v["name"] = json!("other")),
                "must match its manifest key",
            ),
        ];
        for (label, mutate, expected) in cases {
            let mut value = manifest("github");
            mutate(&mut value);
            let error = prepare(&requirements(vec![("github", value)]), true, "agent").unwrap_err();
            let message = format!("{error:#}");
            assert!(message.contains(expected), "{label}: {message}");
        }
    }
}
