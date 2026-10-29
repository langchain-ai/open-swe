//! Read the Linear channel manifest the runtime emits into
//! `channel-manifests.json`.
//!
//! `channels.linear(...)` works out what its app needs, from the events it
//! handles and its options, and puts it in `providerConfig`: the handled
//! events, whether it answers agent sessions, the webhook resource types, the
//! OAuth scopes and how the app looks. The CLI uses it as given, and checks
//! only the icon file, which only it can see in the build.

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

/// Linear limits app names to 80 characters.
const APP_NAME_MAX_CHARS: usize = 80;
/// Largest icon the deployment serves.
const ICON_MAX_BYTES: u64 = 1024 * 1024;
const ICON_LABEL: &str = "Linear channel app.icon";
const PNG_MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";
const JPEG_MAGIC: &[u8] = b"\xff\xd8\xff";
const DEFAULT_APP_DESCRIPTION: &str =
    "A Managed Deep Agent that works on issues in this workspace.";

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
    events: Vec<String>,
    sessions: bool,
    webhook_resource_types: Vec<String>,
    scopes: Vec<String>,
    allowed_scopes: Vec<String>,
    #[serde(default)]
    app: LinearAppOptions,
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
}

/// Find the Linear channel, if the project declares one.
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
        provider_config: config,
        ..
    }) = app_channel_manifest::<LinearProviderConfig>(requirements, PROVIDER, "Linear")?
    else {
        return Ok(None);
    };
    let app_icon = config
        .app
        .icon
        .as_deref()
        .map(|icon| validate_icon_file(project_root, project_files, icon))
        .transpose()?;
    if !wait {
        bail!("An agent with a Linear channel cannot be deployed with --no-wait.");
    }

    Ok(Some(LinearChannel {
        name,
        events_path,
        handled: config.events,
        sessions: config.sessions,
        resource_types: config.webhook_resource_types,
        scopes: config.scopes,
        allowed_scopes: config.allowed_scopes,
        app_name: config
            .app
            .name
            .unwrap_or_else(|| default_app_name(assistant_id)),
        app_description: config
            .app
            .description
            .unwrap_or_else(|| DEFAULT_APP_DESCRIPTION.to_string()),
        app_icon,
    }))
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

/// The `providerConfig` `channels.linear(...)` emits with `sessions` on or
/// off and no handlers of its own.
#[cfg(test)]
pub(super) fn provider_config(sessions: bool) -> serde_json::Value {
    if !sessions {
        return serde_json::json!({
            "events": [],
            "sessions": false,
            "webhookResourceTypes": ["OAuthAuthorization"],
            "scopes": ["read", "write"],
            "allowedScopes": ["read", "write", "customer:read", "customer:write", "initiative:read", "initiative:write"],
        });
    }
    serde_json::json!({
        "events": ["AgentSessionEvent.created", "AgentSessionEvent.prompted"],
        "sessions": true,
        "webhookResourceTypes": ["AgentSessionEvent", "OAuthAuthorization", "PermissionChange"],
        "scopes": ["read", "write", "app:mentionable", "app:assignable"],
        "allowedScopes": [
            "read", "write", "app:mentionable", "app:assignable",
            "customer:read", "customer:write", "initiative:read", "initiative:write",
        ],
    })
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::*;

    fn requirements(manifest: Value) -> ChannelRequirements {
        ChannelRequirements::from([("linear".to_string(), manifest)])
    }

    fn manifest(sessions: bool) -> Value {
        json!({
            "name": "linear",
            "provider": "linear",
            "autoReply": true,
            "eventsPath": "/channels/linear/events",
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": provider_config(sessions),
        })
    }

    #[test]
    fn uses_the_app_setup_the_runtime_emits() {
        let channel = prepare(
            &requirements(manifest(true)),
            true,
            "triage-agent",
            Path::new("."),
            &[],
        )
        .unwrap()
        .unwrap();
        assert!(channel.sessions);
        assert_eq!(
            channel.handled,
            ["AgentSessionEvent.created", "AgentSessionEvent.prompted"]
        );
        assert_eq!(
            channel.resource_types,
            [
                "AgentSessionEvent",
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

        let mut branded = manifest(false);
        branded["providerConfig"]["app"] = json!({
            "name": "Triage Bot",
            "description": "Triages new issues.",
        });
        let branded = prepare(&requirements(branded), true, "a", Path::new("."), &[])
            .unwrap()
            .unwrap();
        assert!(!branded.sessions);
        assert_eq!(branded.app_name, "Triage Bot");
        assert_eq!(branded.app_description, "Triages new issues.");
        // Linear refuses app names that are too short or mention Linear.
        assert_eq!(
            prepare(
                &requirements(manifest(false)),
                true,
                "a",
                Path::new("."),
                &[]
            )
            .unwrap()
            .unwrap()
            .app_name,
            "Deep Agent"
        );
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
            let mut iconed = manifest(false);
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
            &requirements(manifest(true)),
            false,
            "a",
            Path::new("."),
            &[]
        )
        .is_err());
        let mut unknown = manifest(true);
        // Like Slack's and GitHub's, the app takes no icon URL.
        unknown["providerConfig"]["app"] = json!({ "iconUrl": "https://acme.dev/icon.png" });
        assert!(prepare(&requirements(unknown), true, "a", Path::new("."), &[]).is_err());
        let mut transport = manifest(true);
        transport["transport"] = json!("trigger_server");
        assert!(prepare(&requirements(transport), true, "a", Path::new("."), &[]).is_err());

        let two = ChannelRequirements::from([
            ("a".to_string(), manifest(true)),
            ("b".to_string(), manifest(true)),
        ]);
        assert!(prepare(&two, true, "a", Path::new("."), &[]).is_err());
        assert!(
            prepare(&ChannelRequirements::new(), true, "a", Path::new("."), &[])
                .unwrap()
                .is_none()
        );
    }
}
