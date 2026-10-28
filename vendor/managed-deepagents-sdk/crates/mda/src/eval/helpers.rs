//! Path resolution, project digests, and small eval utilities.

use anyhow::{bail, Context, Result};
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::Path;
use std::process::Command;

use crate::project::Project;
use crate::version;

use super::types::EVAL_ARTIFACT_FORMAT;

pub(crate) struct ProjectDigestInputs<'a> {
    pub(crate) model_override: Option<&'a str>,
    pub(crate) runtime_source: &'a Path,
    pub(crate) langgraph_api_version: &'a str,
    pub(crate) python_version: Option<&'a str>,
    pub(crate) source_revision: Option<&'a str>,
    pub(crate) dirty: bool,
}

pub(crate) fn project_digest(
    project: &Project,
    inputs: &ProjectDigestInputs<'_>,
) -> Result<String> {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    EVAL_ARTIFACT_FORMAT.hash(&mut hasher);
    // Include the CLI/runtime package version so artifact caches invalidate when
    // MDA upgrades without project changes.
    version::package_version().hash(&mut hasher);
    project.language.hash(&mut hasher);
    inputs.model_override.hash(&mut hasher);
    inputs.langgraph_api_version.hash(&mut hasher);
    inputs.python_version.hash(&mut hasher);
    inputs.source_revision.hash(&mut hasher);
    inputs.dirty.hash(&mut hasher);
    for rel in &project.files {
        rel.hash(&mut hasher);
        let path = project.root.join(rel);
        fs::read(&path)
            .with_context(|| format!("failed to read {}", path.display()))?
            .hash(&mut hasher);
    }
    hash_runtime_tree(inputs.runtime_source, &mut hasher)?;
    Ok(format!("{:016x}", hasher.finish()))
}

pub(crate) fn tree_digest(root: &Path) -> Result<String> {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    hash_runtime_tree(root, &mut hasher)?;
    Ok(format!("{:016x}", hasher.finish()))
}

fn hash_runtime_tree(runtime_source: &Path, hasher: &mut impl Hasher) -> Result<()> {
    hash_runtime_dir(runtime_source, runtime_source, hasher)
}

fn hash_runtime_dir(root: &Path, dir: &Path, hasher: &mut impl Hasher) -> Result<()> {
    let mut entries = fs::read_dir(dir)
        .with_context(|| format!("failed to read bundled runtime directory {}", dir.display()))?
        .collect::<std::io::Result<Vec<_>>>()
        .with_context(|| {
            format!(
                "failed to enumerate bundled runtime directory {}",
                dir.display()
            )
        })?;
    entries.sort_by_key(fs::DirEntry::file_name);

    for entry in entries {
        let path = entry.path();
        let relative = path.strip_prefix(root).with_context(|| {
            format!(
                "bundled runtime entry {} escaped {}",
                path.display(),
                root.display()
            )
        })?;
        let metadata = fs::symlink_metadata(&path).with_context(|| {
            format!("failed to inspect bundled runtime entry {}", path.display())
        })?;
        relative.hash(hasher);
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            0_u8.hash(hasher);
            hash_permissions(&metadata, hasher);
            fs::read_link(&path)
                .with_context(|| {
                    format!("failed to read bundled runtime symlink {}", path.display())
                })?
                .hash(hasher);
        } else if file_type.is_dir() {
            1_u8.hash(hasher);
            hash_runtime_dir(root, &path, hasher)?;
        } else if file_type.is_file() {
            2_u8.hash(hasher);
            hash_permissions(&metadata, hasher);
            fs::read(&path)
                .with_context(|| format!("failed to read bundled runtime file {}", path.display()))?
                .hash(hasher);
        } else {
            3_u8.hash(hasher);
            hash_permissions(&metadata, hasher);
        }
    }
    Ok(())
}

#[cfg(unix)]
fn hash_permissions(metadata: &fs::Metadata, hasher: &mut impl Hasher) {
    use std::os::unix::fs::PermissionsExt;

    metadata.permissions().mode().hash(hasher);
}

#[cfg(not(unix))]
fn hash_permissions(metadata: &fs::Metadata, hasher: &mut impl Hasher) {
    metadata.permissions().readonly().hash(hasher);
}

pub(crate) fn git_revision(root: &Path) -> Result<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("rev-parse")
        .arg("HEAD")
        .output()?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        bail!("git rev-parse failed")
    }
}

pub(crate) fn git_dirty(root: &Path) -> Result<bool> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("status")
        .arg("--porcelain")
        .output()?;
    if !output.status.success() {
        bail!("git status failed")
    }
    Ok(!output.stdout.is_empty())
}

pub(crate) fn looks_like_project_root(dir: &Path) -> bool {
    dir.join("agent.ts").is_file()
        || dir.join("agent.tsx").is_file()
        || dir.join("agent.py").is_file()
}
