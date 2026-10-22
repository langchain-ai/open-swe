//! Validate the Linear channel manifest the runtime emits into
//! `channel-manifests.json`.
//!
//! `channels.linear({ onEvent })` publishes the event keys it handles (for
//! example `Issue.create`, and the agent session events while sessions are on)
//! as `providerConfig.events`. The CLI turns them into the webhook resource
//! types and OAuth scopes the Linear app needs.

use std::collections::BTreeSet;
use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::compile::ChannelRequirements;
use crate::deploy::channel_app::{app_channel_manifest, AppChannelManifest};
use crate::deploy::channel_icon::{channel_icon_file, relative_icon_path};

pub(crate) const PROVIDER: &str = "linear";

/// Linear limits app names to 2-80 characters, and refuses ones naming Linear.
const APP_NAME_MIN_CHARS: usize = 2;
const APP_NAME_MAX_CHARS: usize = 80;
const APP_DESCRIPTION_MAX_CHARS: usize = 1000;
/// Largest icon the deployment serves.
const ICON_MAX_BYTES: u64 = 1024 * 1024;
const ICON_LABEL: &str = "Linear channel app.icon";
const PNG_MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";
const JPEG_MAGIC: &[u8] = b"\xff\xd8\xff";
const DEFAULT_APP_DESCRIPTION: &str =
    "A Managed Deep Agent that works on issues in this workspace.";
const MAX_TEAMS: usize = 100;

/// Event keys the runtime dispatches, with the webhook resource type that
/// delivers each.
const SUPPORTED_EVENT_KEYS: &[(&str, &str)] = &[
    ("Issue.create", "Issue"),
    ("Issue.update", "Issue"),
    ("Comment.create", "Comment"),
    ("AgentSessionEvent.created", "AgentSessionEvent"),
    ("AgentSessionEvent.prompted", "AgentSessionEvent"),
];

/// Resource types every app subscribes to: a revoked install, so the runtime
/// can say why events stopped.
const ALWAYS_SUBSCRIBED: &[&str] = &["OAuthAuthorization"];

/// Scopes every app asks for: reading the workspace, and writing comments and
/// agent activities.
const BASE_SCOPES: &[&str] = &["read", "write"];
/// Scopes that let people mention the app and delegate issues to it, which
/// opens agent sessions.
const SESSION_SCOPES: &[&str] = &["app:mentionable", "app:assignable"];
/// Scopes Linear can grant along with `write` without being asked for them.
/// Agent Auth refuses a grant beyond the allowed scopes, so they are allowed.
const IMPLIED_SCOPES: &[&str] = &[
    "customer:read",
    "customer:write",
    "initiative:read",
    "initiative:write",
];

/// A validated Linear channel, ready for app setup.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct LinearChannel {
    /// Channel name (file stem), also the manifest key.
    pub(crate) name: String,
    /// Events route on the Agent Server, e.g. `/channels/linear/events`.
    pub(crate) events_path: String,
    /// Handled event keys, e.g. `Issue.create`, sorted.
    pub(crate) handled: Vec<String>,
    /// Whether the channel answers agent sessions.
    pub(crate) sessions: bool,
    /// Webhook resource types the app subscribes to, sorted.
    pub(crate) resource_types: Vec<String>,
    /// OAuth scopes the install asks for.
    pub(crate) scopes: Vec<String>,
    /// The most the install may be granted: the scopes, and those Linear adds.
    pub(crate) allowed_scopes: Vec<String>,
    /// Name of a new app: the configured one, or the agent's within Linear's rules.
    pub(crate) app_name: String,
    /// Description of a new app.
    pub(crate) app_description: String,
    /// A PNG or JPEG under `channels/` the deployment serves as the app's
    /// icon, at `/channels/{name}/icon`.
    pub(crate) app_icon: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LinearProviderConfig {
    #[serde(default)]
    events: Vec<String>,
    #[serde(default)]
    app: Option<LinearAppOptions>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct LinearAppOptions {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    description: Option<String>,
    #[serde(default)]
    icon: Option<String>,
    /// `None` allows every team; the runtime filters by them.
    #[serde(default)]
    teams: Option<Vec<String>>,
}

/// Find and validate the Linear channel, if the project declares one.
///
/// `wait` is false for `mda deploy --no-wait`: app setup needs the live Agent
/// Server URL, so that combination is rejected before anything is uploaded.
pub(crate) fn prepare(
    requirements: &ChannelRequirements,
    wait: bool,
    assistant_id: &str,
    project_root: &Path,
    project_files: &[PathBuf],
) -> Result<Option<LinearChannel>> {
    let Some(AppChannelManifest {
        name,
        events_path,
        provider_config,
        ..
    }) = app_channel_manifest::<LinearProviderConfig>(requirements, PROVIDER, "Linear")?
    else {
        return Ok(None);
    };
    let handled = validate_event_keys(provider_config.events)?;
    let app = provider_config.app.unwrap_or_default();
    validate_teams(app.teams)?;
    let app_name = match app.name {
        Some(name) => validate_app_name(&name)?,
        None => default_app_name(assistant_id),
    };
    let app_description = match app.description {
        Some(description) => validate_app_description(&description)?,
        None => DEFAULT_APP_DESCRIPTION.to_string(),
    };
    let app_icon = app
        .icon
        .as_deref()
        .map(|icon| validate_icon_file(project_root, project_files, icon))
        .transpose()?;
    if !wait {
        bail!("An agent with a Linear channel cannot be deployed with --no-wait.");
    }

    let sessions = handled
        .iter()
        .any(|key| key.starts_with("AgentSessionEvent."));
    let mut resource_types: BTreeSet<&str> = ALWAYS_SUBSCRIBED.iter().copied().collect();
    for key in &handled {
        if let Some((_, resource)) = SUPPORTED_EVENT_KEYS.iter().find(|(known, _)| known == key) {
            resource_types.insert(resource);
        }
    }
    if sessions {
        // A change to the app's team access affects which sessions reach it.
        resource_types.insert("PermissionChange");
    }
    let mut scopes: Vec<String> = BASE_SCOPES.iter().map(ToString::to_string).collect();
    if sessions {
        scopes.extend(SESSION_SCOPES.iter().map(ToString::to_string));
    }
    let mut allowed_scopes = scopes.clone();
    allowed_scopes.extend(IMPLIED_SCOPES.iter().map(ToString::to_string));

    Ok(Some(LinearChannel {
        name,
        events_path,
        handled,
        sessions,
        resource_types: resource_types.into_iter().map(str::to_string).collect(),
        scopes,
        allowed_scopes,
        app_name,
        app_description,
        app_icon,
    }))
}

fn validate_event_keys(events: Vec<String>) -> Result<Vec<String>> {
    let mut handled = BTreeSet::new();
    for key in events {
        if !SUPPORTED_EVENT_KEYS.iter().any(|(known, _)| *known == key) {
            bail!("Linear channel handles an unsupported event '{key}'.");
        }
        handled.insert(key);
    }
    Ok(handled.into_iter().collect())
}

/// Team keys as in `ENG-123`: letters and digits.
fn validate_teams(teams: Option<Vec<String>>) -> Result<()> {
    let Some(teams) = teams else {
        return Ok(());
    };
    if teams.is_empty() {
        bail!("Linear channel app.teams must list at least one team key; omit it to allow every team.");
    }
    if teams.len() > MAX_TEAMS {
        bail!("Linear channel app.teams must list at most {MAX_TEAMS} teams.");
    }
    for team in &teams {
        let team = team.trim();
        if team.is_empty() || team.len() > 16 || !team.chars().all(|c| c.is_ascii_alphanumeric()) {
            bail!(
                "Linear channel app.teams has an invalid team key '{team}'; use keys such as ENG."
            );
        }
    }
    Ok(())
}

fn validate_app_name(name: &str) -> Result<String> {
    let name = name.trim();
    let lower = name.to_ascii_lowercase();
    let chars = name.chars().count();
    if !(APP_NAME_MIN_CHARS..=APP_NAME_MAX_CHARS).contains(&chars)
        || lower.contains("linear")
        || lower.contains("http://")
        || lower.contains("https://")
    {
        bail!("Linear channel app.name must be 2-80 characters, without \"Linear\" or a URL.");
    }
    Ok(name.to_string())
}

fn validate_app_description(description: &str) -> Result<String> {
    let description = description.trim();
    let chars = description.chars().count();
    if chars == 0 || chars > APP_DESCRIPTION_MAX_CHARS {
        bail!("Linear channel app.description must be 1-{APP_DESCRIPTION_MAX_CHARS} characters.");
    }
    Ok(description.to_string())
}

/// The icon the deployment serves: a PNG or JPEG file inside `channels/`, as
/// for the Slack channel's icon, of at most [`ICON_MAX_BYTES`], whose content
/// matches its extension. It must be one of `project_files`, the files the
/// build carries, since the deployment reads it from the build.
fn validate_icon_file(
    project_root: &Path,
    project_files: &[PathBuf],
    icon: &str,
) -> Result<String> {
    let relative = icon.trim();
    let path = relative_icon_path(relative, ICON_LABEL)?;
    let magic = match path
        .extension()
        .and_then(OsStr::to_str)
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("png") => PNG_MAGIC,
        Some("jpg" | "jpeg") => JPEG_MAGIC,
        _ => bail!("{ICON_LABEL} must be a PNG or JPEG image, such as assets/icon.png."),
    };
    // A link, or a file in a folder the build leaves out (such as build/ or
    // dist/), never reaches the deployment, which would then serve nothing.
    let built = Path::new("channels").join(path);
    if !project_files.iter().any(|file| file == &built) {
        if fs::symlink_metadata(project_root.join(&built)).is_ok() {
            bail!(
                "{ICON_LABEL} {relative} isn't part of the build: use a regular file, not a link, outside folders the build leaves out such as build/ or dist/."
            );
        }
        bail!("{ICON_LABEL} {relative} does not exist.");
    }
    let file = channel_icon_file(project_root, relative, ICON_LABEL, ICON_MAX_BYTES)?;
    let mut head = Vec::with_capacity(PNG_MAGIC.len());
    File::open(&file)
        .and_then(|opened| opened.take(PNG_MAGIC.len() as u64).read_to_end(&mut head))
        .with_context(|| format!("failed to read {ICON_LABEL} {relative}"))?;
    if !head.starts_with(magic) {
        bail!("{ICON_LABEL} {relative} is not the image its extension names.");
    }
    Ok(relative.to_string())
}

/// A new app is named after the agent. Linear refuses names that mention
/// Linear, so those get a generic one.
fn default_app_name(assistant_id: &str) -> String {
    let name: String = assistant_id
        .trim()
        .chars()
        .take(APP_NAME_MAX_CHARS)
        .collect();
    if name.chars().count() < 2 || name.to_ascii_lowercase().contains("linear") {
        return "Deep Agent".to_string();
    }
    name
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    fn requirements(manifest: Value) -> ChannelRequirements {
        ChannelRequirements::from([("linear".to_string(), manifest)])
    }

    fn manifest(events: &[&str]) -> Value {
        json!({
            "name": "linear",
            "provider": "linear",
            "autoReply": true,
            "eventsPath": "/channels/linear/events",
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": { "events": events },
        })
    }

    #[test]
    fn derives_resource_types_and_scopes_from_the_handled_events() {
        let channel = prepare(
            &requirements(manifest(&[
                "Issue.update",
                "AgentSessionEvent.created",
                "AgentSessionEvent.prompted",
            ])),
            true,
            "triage-agent",
            Path::new("."),
            &[],
        )
        .unwrap()
        .unwrap();
        assert!(channel.sessions);
        assert_eq!(
            channel.resource_types,
            [
                "AgentSessionEvent",
                "Issue",
                "OAuthAuthorization",
                "PermissionChange"
            ]
        );
        assert_eq!(
            channel.scopes,
            ["read", "write", "app:mentionable", "app:assignable"]
        );
        assert!(channel
            .allowed_scopes
            .contains(&"initiative:write".to_string()));
        assert_eq!(channel.app_name, "triage-agent");
        assert_eq!(channel.app_description, DEFAULT_APP_DESCRIPTION);
        assert_eq!(channel.app_icon, None);

        let mut branded = manifest(&["Issue.create"]);
        branded["providerConfig"]["app"] = json!({
            "name": " Triage Bot ",
            "description": "Triages new issues.",
        });
        let branded = prepare(&requirements(branded), true, "a", Path::new("."), &[])
            .unwrap()
            .unwrap();
        assert_eq!(branded.app_name, "Triage Bot");
        assert_eq!(branded.app_description, "Triages new issues.");

        let comments = prepare(
            &requirements(manifest(&["Comment.create"])),
            true,
            "a",
            Path::new("."),
            &[],
        )
        .unwrap()
        .unwrap();
        assert!(!comments.sessions);
        assert_eq!(comments.resource_types, ["Comment", "OAuthAuthorization"]);
        assert_eq!(comments.scopes, ["read", "write"]);
        // Linear refuses app names that are too short or mention Linear.
        assert_eq!(comments.app_name, "Deep Agent");
    }

    #[test]
    fn serves_an_icon_file_inside_channels() {
        // Like the Slack channel's icon, a path relative to channels/.
        let project = tempfile::tempdir().unwrap();
        let channels = project.path().join("channels");
        fs::create_dir_all(channels.join("assets")).unwrap();
        fs::write(channels.join("assets/icon.png"), b"\x89PNG\r\n\x1a\nrest").unwrap();
        fs::write(project.path().join("top.png"), b"\x89PNG\r\n\x1a\nrest").unwrap();
        fs::write(channels.join("fake.png"), b"<svg/>").unwrap();
        fs::write(
            channels.join("big.jpg"),
            vec![0xff; (ICON_MAX_BYTES + 1) as usize],
        )
        .unwrap();
        // On disk, but in a folder the build leaves out.
        fs::create_dir_all(channels.join("build")).unwrap();
        fs::write(channels.join("build/icon.png"), b"\x89PNG\r\n\x1a\nrest").unwrap();
        // What `project::load` carries into the build: regular files outside
        // ignored folders.
        let built: Vec<PathBuf> = [
            "channels/assets/icon.png",
            "channels/fake.png",
            "channels/big.jpg",
            "top.png",
        ]
        .into_iter()
        .map(PathBuf::from)
        .collect();
        let with_icon = |app: Value| {
            let mut iconed = manifest(&["Issue.create"]);
            iconed["providerConfig"]["app"] = app;
            prepare(&requirements(iconed), true, "a", project.path(), &built)
        };

        let channel = with_icon(json!({ "icon": " assets/icon.png " }))
            .unwrap()
            .unwrap();
        assert_eq!(channel.app_icon.as_deref(), Some("assets/icon.png"));
        for icon in [
            "missing.png",
            // At the project's root, not under channels/.
            "top.png",
            "fake.png",
            "big.jpg",
            "../icon.png",
            "/etc/icon.png",
            "./assets/icon.png",
            "assets//icon.png",
            "assets\\icon.png",
            "icon.svg",
        ] {
            assert!(with_icon(json!({ "icon": icon })).is_err(), "{icon}");
        }
        // The deployment serves the icon from the build, so one the build
        // doesn't carry fails here rather than as a broken link in Linear.
        let error = with_icon(json!({ "icon": "build/icon.png" })).unwrap_err();
        assert!(
            error.to_string().contains("isn't part of the build"),
            "{error}"
        );
        // A link is refused, whether it leads out of channels/ or not.
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(
                channels.join("assets/icon.png"),
                channels.join("alias.png"),
            )
            .unwrap();
            let error = with_icon(json!({ "icon": "alias.png" })).unwrap_err();
            assert!(
                error.to_string().contains("isn't part of the build"),
                "{error}"
            );
            let outside = tempfile::tempdir().unwrap();
            fs::write(outside.path().join("icon.png"), b"\x89PNG\r\n\x1a\n").unwrap();
            std::os::unix::fs::symlink(
                outside.path().join("icon.png"),
                channels.join("linked.png"),
            )
            .unwrap();
            assert!(with_icon(json!({ "icon": "linked.png" })).is_err());
        }
    }

    #[test]
    fn rejects_what_setup_cannot_honor() {
        assert!(prepare(
            &requirements(manifest(&["Project.create"])),
            true,
            "a",
            Path::new("."),
            &[]
        )
        .is_err());
        assert!(prepare(
            &requirements(manifest(&["Issue.create"])),
            false,
            "a",
            Path::new("."),
            &[]
        )
        .is_err());
        let mut teams = manifest(&["Issue.create"]);
        teams["providerConfig"]["app"] = json!({ "teams": [] });
        assert!(prepare(&requirements(teams.clone()), true, "a", Path::new("."), &[]).is_err());
        teams["providerConfig"]["app"] = json!({ "teams": ["ENG-1"] });
        assert!(prepare(&requirements(teams.clone()), true, "a", Path::new("."), &[]).is_err());
        teams["providerConfig"]["app"] = json!({ "teams": ["ENG"] });
        assert!(prepare(&requirements(teams), true, "a", Path::new("."), &[]).is_ok());
        for app in [
            json!({ "name": "x" }),
            json!({ "name": "My Linear bot" }),
            json!({ "name": "see https://acme.dev" }),
            json!({ "description": "x".repeat(1001) }),
            // Like Slack's and GitHub's, the app takes no icon URL.
            json!({ "iconUrl": "https://acme.dev/icon.png" }),
        ] {
            let mut branded = manifest(&["Issue.create"]);
            branded["providerConfig"]["app"] = app.clone();
            assert!(
                prepare(&requirements(branded), true, "a", Path::new("."), &[]).is_err(),
                "{app}"
            );
        }
        let mut transport = manifest(&["Issue.create"]);
        transport["transport"] = json!("trigger_server");
        assert!(prepare(&requirements(transport), true, "a", Path::new("."), &[]).is_err());

        let two = ChannelRequirements::from([
            ("a".to_string(), manifest(&[])),
            ("b".to_string(), manifest(&[])),
        ]);
        assert!(prepare(&two, true, "a", Path::new("."), &[]).is_err());
        assert!(
            prepare(&ChannelRequirements::new(), true, "a", Path::new("."), &[])
                .unwrap()
                .is_none()
        );
    }
}
