//! The icon file a channel names in its options, as a path relative to the
//! project's `channels/` directory: the Slack app's and the Linear app's.
//! Each channel checks the image itself; this checks where the file is.

use std::fs;
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, Context, Result};

/// The icon as a plain relative path: no root, `.`, `..`, `\`, or empty
/// segment. Linear's icon must be one, since the deployment serves it by the
/// same path and checks it the same way. `label` names the option in errors.
pub(crate) fn relative_icon_path<'a>(icon: &'a str, label: &str) -> Result<&'a Path> {
    let path = Path::new(icon);
    if icon.is_empty()
        || icon.contains('\\')
        || icon.split('/').any(str::is_empty)
        || !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        bail!("{label} must be a path relative to channels/");
    }
    Ok(path)
}

/// The icon's file inside a regular `channels/` directory, reached without
/// links, as a regular file of at most `max_bytes`. Any relative path without
/// `..` names it, as Slack's icon always could, such as `./assets/icon.png`;
/// a channel that needs a plain path checks [`relative_icon_path`] first.
pub(crate) fn channel_icon_file(
    project_root: &Path,
    icon: &str,
    label: &str,
    max_bytes: u64,
) -> Result<PathBuf> {
    let relative = Path::new(icon);
    if icon.is_empty()
        || relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        bail!("{label} must be a path relative to channels/");
    }
    let root = project_root
        .canonicalize()
        .with_context(|| format!("failed to resolve project root for {label}"))?;
    let channels_path = root.join("channels");
    let channels_metadata = fs::symlink_metadata(&channels_path)
        .with_context(|| format!("failed to inspect {}", channels_path.display()))?;
    if channels_metadata.file_type().is_symlink() || !channels_metadata.is_dir() {
        bail!("{label} directory must be a regular channels/ directory");
    }
    let channels = channels_path
        .canonicalize()
        .with_context(|| format!("failed to resolve channels/ for {label}"))?;

    // No link on the way may lead out of channels/, or anywhere else.
    let mut candidate = channels.clone();
    for component in relative.components() {
        let Component::Normal(part) = component else {
            continue;
        };
        candidate.push(part);
        let metadata = fs::symlink_metadata(&candidate)
            .with_context(|| format!("failed to inspect {label} {icon}"))?;
        if metadata.file_type().is_symlink() {
            bail!("{label} path must not contain symlinks");
        }
    }
    let resolved = candidate
        .canonicalize()
        .with_context(|| format!("failed to resolve {label} {icon}"))?;
    if !resolved.starts_with(&channels) {
        bail!("{label} must remain within channels/");
    }
    let metadata =
        fs::metadata(&resolved).with_context(|| format!("failed to inspect {label} {icon}"))?;
    if !metadata.is_file() {
        bail!("{label} must be a regular file");
    }
    if metadata.len() > max_bytes {
        bail!("{label} must be at most 1 MB");
    }
    Ok(resolved)
}
