//! Validate the GitHub channel manifest the runtime emits into
//! `channel-manifests.json`.
//!
//! `channels.github({ on })` publishes the event keys it handles (for example
//! `issues.opened`) as `providerConfig.events`. The CLI turns them into the
//! App's webhook events and the permissions those events need.
//! `channels.github({ app })` adds `providerConfig.app`: extra permissions for
//! the agent's token and the repositories the channel takes events from, which
//! the installation must include. A new App
//! is named after the agent, and the first deploy asks where to create it.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::compile::ChannelRequirements;
use crate::deploy::channel_app::{app_channel_manifest, AppChannelManifest};
use crate::github::validate_login;

pub(crate) const PROVIDER: &str = "github";

/// GitHub limits App names to 34 characters.
const APP_NAME_MAX_CHARS: usize = 34;
const MAX_PERMISSIONS: usize = 50;
/// Keeps the manifest and the deploy report readable.
const MAX_REPOSITORIES: usize = 500;

/// Event keys the runtime dispatches, with the App permission each needs to
/// receive the event and respond to it.
const SUPPORTED_EVENT_KEYS: [(&str, &str, PermissionLevel); 24] = [
    ("issue_comment.created", "issues", PermissionLevel::Write),
    ("issue_comment.edited", "issues", PermissionLevel::Write),
    ("issues.edited", "issues", PermissionLevel::Write),
    ("issues.labeled", "issues", PermissionLevel::Write),
    ("issues.opened", "issues", PermissionLevel::Write),
    ("issues.reopened", "issues", PermissionLevel::Write),
    (
        "pull_request.closed",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.converted_to_draft",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.edited",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.labeled",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.opened",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.ready_for_review",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.reopened",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request.synchronize",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request_review.dismissed",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request_review.edited",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request_review.submitted",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request_review_comment.created",
        "pull_requests",
        PermissionLevel::Write,
    ),
    (
        "pull_request_review_comment.edited",
        "pull_requests",
        PermissionLevel::Write,
    ),
    ("check_run.completed", "checks", PermissionLevel::Read),
    ("check_suite.completed", "checks", PermissionLevel::Read),
    ("push", "contents", PermissionLevel::Read),
    ("status", "statuses", PermissionLevel::Read),
    ("workflow_run.completed", "actions", PermissionLevel::Read),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
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
    #[serde(default)]
    events: Vec<String>,
    #[serde(default)]
    app: Option<GitHubAppOptions>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct GitHubAppOptions {
    #[serde(default)]
    permissions: BTreeMap<String, String>,
    /// `None` allows every repository; an empty list is rejected.
    #[serde(default)]
    repositories: Option<Vec<String>>,
}

/// Find and validate the GitHub channel, if the project declares one.
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
        provider_config,
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

    let handled = validate_event_keys(provider_config.events)?;
    let (events, mut permissions) = app_events_and_permissions(&handled);
    let app = provider_config.app.unwrap_or_default();
    let mut event_permissions: Vec<EventPermission> = permissions
        .iter()
        .filter(|(name, _)| name.as_str() != "metadata")
        .map(|(name, level)| EventPermission {
            name: name.clone(),
            level: *level,
            events: events_needing(&handled, name, *level),
        })
        .collect();
    for (name, level) in validate_permissions(app.permissions)? {
        let entry = permissions.entry(name.clone()).or_insert(level);
        if *entry < level {
            // app.permissions sets this level, not the events.
            event_permissions.retain(|permission| permission.name != name);
        }
        *entry = (*entry).max(level);
    }
    let repositories = validate_repositories(app.repositories)?;

    if !wait {
        bail!("An agent with a GitHub channel cannot be deployed with --no-wait.");
    }

    Ok(Some(GitHubChannel {
        name,
        events_path,
        handled,
        events,
        permissions,
        app_name: default_app_name(assistant_id),
        repositories,
        event_permissions,
    }))
}

/// Extra token permissions: GitHub permission names at `read` or `write`.
fn validate_permissions(
    permissions: BTreeMap<String, String>,
) -> Result<BTreeMap<String, PermissionLevel>> {
    if permissions.len() > MAX_PERMISSIONS {
        bail!("GitHub channel app.permissions must list at most {MAX_PERMISSIONS} permissions.");
    }
    permissions
        .into_iter()
        .map(|(name, level)| {
            let valid_name = !name.is_empty()
                && name.len() <= 64
                && name.starts_with(|c: char| c.is_ascii_lowercase())
                && name.chars().all(|c| c.is_ascii_lowercase() || c == '_');
            if !valid_name {
                bail!("GitHub channel app.permissions has an invalid permission name '{name}'; use GitHub's names, such as contents.");
            }
            let level = PermissionLevel::parse(&level).with_context(|| {
                format!("GitHub channel app.permissions.{name} must be read or write.")
            })?;
            Ok((name, level))
        })
        .collect()
}

/// `owner/name` repositories of one owner, sorted and deduplicated. Empty
/// when unset, meaning every repository of the installation.
fn validate_repositories(repositories: Option<Vec<String>>) -> Result<Vec<String>> {
    let Some(repositories) = repositories else {
        return Ok(Vec::new());
    };
    if repositories.is_empty() {
        // An empty list would read as "no repositories" at runtime but as
        // "every repository" here; neither should be a silent choice.
        bail!("GitHub channel app.repositories must list at least one repository; omit it to allow every repository.");
    }
    let mut valid = BTreeSet::new();
    for repository in repositories {
        let repository = repository.trim();
        let Some((owner, name)) = repository.split_once('/') else {
            bail!("GitHub channel app.repositories entry '{repository}' must be owner/name.");
        };
        let valid_name = !name.is_empty()
            && name.len() <= 100
            && name != "."
            && name != ".."
            && name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'));
        if validate_login(owner).is_err() || !valid_name {
            bail!("GitHub channel app.repositories entry '{repository}' must be owner/name.");
        }
        // GitHub names are case-insensitive.
        valid.insert(repository.to_ascii_lowercase());
    }
    if valid.len() > MAX_REPOSITORIES {
        bail!("GitHub channel app.repositories must list at most {MAX_REPOSITORIES} repositories.");
    }
    let owners: BTreeSet<&str> = valid
        .iter()
        .filter_map(|repository| repository.split_once('/').map(|(owner, _)| owner))
        .collect();
    if owners.len() > 1 {
        bail!(
            "GitHub channel app.repositories must all belong to one owner, because one installation covers them (found: {}).",
            owners.into_iter().collect::<Vec<_>>().join(", ")
        );
    }
    Ok(valid.into_iter().collect())
}

fn validate_event_keys(keys: Vec<String>) -> Result<Vec<String>> {
    let mut keys: Vec<String> = keys.into_iter().map(|key| key.trim().to_string()).collect();
    if let Some(unsupported) = keys
        .iter()
        .find(|key| !SUPPORTED_EVENT_KEYS.iter().any(|(known, ..)| known == key))
    {
        let supported: Vec<&str> = SUPPORTED_EVENT_KEYS.iter().map(|(key, ..)| *key).collect();
        bail!(
            "GitHub channel event '{unsupported}' is not supported. Supported events: {}.",
            supported.join(", ")
        );
    }
    keys.sort();
    keys.dedup();
    Ok(keys)
}

/// The App's webhook events (the part before the action) and the strongest
/// permission any handled key needs, plus the `metadata: read` every App has.
fn app_events_and_permissions(
    handled: &[String],
) -> (Vec<String>, BTreeMap<String, PermissionLevel>) {
    let mut events = BTreeSet::new();
    let mut permissions = BTreeMap::from([("metadata".to_string(), PermissionLevel::Read)]);
    for (key, permission, level) in SUPPORTED_EVENT_KEYS {
        if !handled.iter().any(|handled| handled == key) {
            continue;
        }
        let event = key.split_once('.').map_or(key, |(event, _)| event);
        events.insert(event.to_string());
        let entry = permissions.entry(permission.to_string()).or_insert(level);
        *entry = (*entry).max(level);
    }
    (events.into_iter().collect(), permissions)
}

/// Handled event keys that need `permission` at `level`, sorted.
fn events_needing(handled: &[String], permission: &str, level: PermissionLevel) -> Vec<String> {
    let mut events: Vec<String> = SUPPORTED_EVENT_KEYS
        .iter()
        .filter(|(key, needed, needed_level)| {
            *needed == permission && *needed_level == level && handled.iter().any(|h| h == key)
        })
        .map(|(key, ..)| key.to_string())
        .collect();
    events.sort();
    events
}

fn default_app_name(assistant_id: &str) -> String {
    let name: String = assistant_id.chars().take(APP_NAME_MAX_CHARS).collect();
    name.trim_end_matches(['-', '_', ' ']).to_string()
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    fn manifest(name: &str) -> Value {
        json!({
            "name": name,
            "provider": "github",
            "autoReply": false,
            "eventsPath": format!("/channels/{name}/events"),
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": {
                "events": ["issues.opened", "issue_comment.created", "workflow_run.completed", "issues.opened"]
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
    fn derives_app_events_and_permissions_from_handled_keys() {
        let slack = json!({ "name": "slack", "provider": "slack", "transport": "trigger_server" });
        let channel = prepare(
            &requirements(vec![("github", manifest("github")), ("slack", slack)]),
            true,
            "a-very-long-assistant-name-that-exceeds-the-limit",
        )
        .unwrap()
        .unwrap();
        assert_eq!(channel.name, "github");
        assert_eq!(channel.events_path, "/channels/github/events");
        assert_eq!(
            channel.handled,
            vec![
                "issue_comment.created",
                "issues.opened",
                "workflow_run.completed"
            ]
        );
        assert_eq!(
            channel.events,
            vec!["issue_comment", "issues", "workflow_run"]
        );
        assert_eq!(
            channel.permissions,
            BTreeMap::from([
                ("actions".to_string(), PermissionLevel::Read),
                ("issues".to_string(), PermissionLevel::Write),
                ("metadata".to_string(), PermissionLevel::Read),
            ])
        );
        assert_eq!(channel.app_name, "a-very-long-assistant-name-that-ex");
    }

    #[test]
    fn ci_and_push_events_ask_only_to_read() {
        let handled: Vec<String> = [
            "push",
            "status",
            "check_run.completed",
            "check_suite.completed",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let (events, permissions) = app_events_and_permissions(&handled);
        assert_eq!(events, vec!["check_run", "check_suite", "push", "status"]);
        assert_eq!(
            permissions,
            BTreeMap::from([
                ("checks".to_string(), PermissionLevel::Read),
                ("contents".to_string(), PermissionLevel::Read),
                ("metadata".to_string(), PermissionLevel::Read),
                ("statuses".to_string(), PermissionLevel::Read),
            ])
        );
    }

    #[test]
    fn app_options_shape_the_token() {
        let mut value = manifest("github");
        value["providerConfig"]["app"] = json!({
            "permissions": { "contents": "read", "issues": "read" },
            "repositories": ["acme/web", "Acme/API", "acme/api"],
        });
        let channel = prepare(&requirements(vec![("github", value)]), true, "agent")
            .unwrap()
            .unwrap();
        assert_eq!(channel.app_name, "agent");
        assert_eq!(channel.repositories, vec!["acme/api", "acme/web"]);
        // Extra permissions merge; the events' write level wins over read.
        assert_eq!(
            channel.permissions,
            BTreeMap::from([
                ("actions".to_string(), PermissionLevel::Read),
                ("contents".to_string(), PermissionLevel::Read),
                ("issues".to_string(), PermissionLevel::Write),
                ("metadata".to_string(), PermissionLevel::Read),
            ])
        );
        let from_events = vec![
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
        ];
        assert_eq!(channel.event_permissions, from_events);

        let channel = prepare(
            &requirements(vec![("github", manifest("github"))]),
            true,
            "agent",
        )
        .unwrap()
        .unwrap();
        assert!(channel.repositories.is_empty());
        assert_eq!(channel.event_permissions, from_events);

        // A level app.permissions raises is not the events' to explain.
        let mut value = manifest("github");
        value["providerConfig"]["app"] = json!({ "permissions": { "actions": "write" } });
        let channel = prepare(&requirements(vec![("github", value)]), true, "agent")
            .unwrap()
            .unwrap();
        assert_eq!(channel.event_permissions, from_events[1..]);
    }

    #[test]
    fn rejects_invalid_app_options() {
        for (app, expected) in [
            (
                json!({ "permissions": { "Contents": "read" } }),
                "invalid permission name",
            ),
            (
                json!({ "permissions": { "contents": "admin" } }),
                "must be read or write",
            ),
            (json!({ "repositories": [] }), "at least one repository"),
            (json!({ "repositories": ["api"] }), "must be owner/name"),
            (json!({ "repositories": ["acme/.."] }), "must be owner/name"),
            (
                json!({ "repositories": ["acme/a b"] }),
                "must be owner/name",
            ),
            (
                json!({ "repositories": ["acme/a/b"] }),
                "must be owner/name",
            ),
            (
                json!({ "repositories": ["acme/api", "other/web"] }),
                "one owner",
            ),
            (json!({ "public": true }), "configuration is invalid"),
        ] {
            let mut value = manifest("github");
            value["providerConfig"]["app"] = app.clone();
            let error = prepare(&requirements(vec![("github", value)]), true, "agent").unwrap_err();
            let message = format!("{error:#}");
            assert!(message.contains(expected), "{app}: {message}");
        }
    }

    #[test]
    fn a_receipt_only_channel_has_no_events() {
        let mut value = manifest("github");
        value["providerConfig"] = json!({ "events": [] });
        let channel = prepare(&requirements(vec![("github", value)]), true, "agent")
            .unwrap()
            .unwrap();
        assert!(channel.handled.is_empty());
        assert!(channel.events.is_empty());
        assert_eq!(
            channel.permissions,
            BTreeMap::from([("metadata".to_string(), PermissionLevel::Read)])
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
                Box::new(|v| v["providerConfig"]["permissions"] = json!({})),
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
                "event",
                Box::new(|v| v["providerConfig"]["events"] = json!(["push.created"])),
                "event 'push.created' is not supported",
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
