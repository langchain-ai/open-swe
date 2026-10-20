//! Build a `tar.gz` of the compiled project for remote (internal-source)
//! builds.
//!
//! The compiled build directory (`.mda/build`) is already a clean, minimal
//! tree — it contains only the user's source files plus the generated
//! `langgraph.json` and managed entry module — so the archive includes every
//! regular file under it except local-only emit scratch (an isolated Python
//! venv and pip `--target` site, and the `node_modules` the TypeScript emit
//! installs to import channels/connectors). The remote builder reinstalls
//! dependencies from `package.json` / `package-lock.json`, so shipping a
//! host-platform `node_modules` only bloats the upload. The `langgraph.json`
//! therefore lives at the archive root (`configRel`).

use std::path::Path;

use anyhow::{bail, Context, Result};
use flate2::write::GzEncoder;
use flate2::Compression;
use walkdir::WalkDir;

use crate::compile::requirements::PYTHON_EMIT_SITE_DIR;
use crate::path_util::to_unix_path;
use crate::toolchain::PYTHON_EMIT_VENV_DIR;

/// npm install tree the TypeScript emit step populates; rebuilt remotely.
const NODE_MODULES_DIR: &str = "node_modules";

/// Hard archive size limit enforced by the host backend (200 MB).
const MAX_SIZE: u64 = 200 * 1024 * 1024;

/// A created in-memory source archive plus the metadata needed to upload it.
pub struct Archive {
    /// The gzipped tarball bytes.
    pub bytes: Vec<u8>,
    /// Config path relative to the archive root (always `langgraph.json`).
    pub config_rel: String,
}

/// Create a gzipped tarball of the compiled build, omitting local emit scratch.
pub fn create_archive(build_dir: &Path) -> Result<Archive> {
    let config_rel = "langgraph.json".to_string();
    if !build_dir.join(&config_rel).is_file() {
        bail!(
            "compiled build at {} is missing langgraph.json",
            build_dir.display()
        );
    }

    let encoder = GzEncoder::new(Vec::new(), Compression::default());
    let mut builder = tar::Builder::new(encoder);
    builder.follow_symlinks(false);

    for entry in WalkDir::new(build_dir)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !is_local_emit_scratch(build_dir, entry.path()))
    {
        let entry = entry.context("failed to walk build directory")?;
        if !entry.file_type().is_file() {
            continue;
        }
        let rel = entry
            .path()
            .strip_prefix(build_dir)
            .context("build file outside build directory")?;
        // Normalize to POSIX separators for portability across platforms.
        let rel_posix = to_unix_path(rel);
        builder
            .append_path_with_name(entry.path(), &rel_posix)
            .with_context(|| format!("failed to add {rel_posix} to archive"))?;
    }

    let encoder = builder
        .into_inner()
        .context("failed to finalize tar archive")?;
    let bytes = encoder.finish().context("failed to gzip archive")?;

    if bytes.len() as u64 > MAX_SIZE {
        bail!(
            "source archive is {:.1} MB, which exceeds the 200 MB limit",
            bytes.len() as f64 / (1024.0 * 1024.0)
        );
    }

    Ok(Archive { bytes, config_rel })
}

/// Local-only dirs under the build: the connector emit venv + pip `--target`
/// site, and `node_modules` from the TypeScript emit or `mda dev` (the
/// remote builder reinstalls from the lockfile).
fn is_local_emit_scratch(build_dir: &Path, path: &Path) -> bool {
    let Ok(rel) = path.strip_prefix(build_dir) else {
        return false;
    };
    let Some(first) = rel.components().next() else {
        return false;
    };
    matches!(
        first.as_os_str().to_str(),
        Some(PYTHON_EMIT_VENV_DIR | PYTHON_EMIT_SITE_DIR | NODE_MODULES_DIR)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::read::GzDecoder;
    use std::fs;
    use std::io::Cursor;
    use tar::Archive as TarArchive;
    use tempfile::tempdir;

    fn write_file(root: &Path, rel: &str, contents: &str) {
        let path = root.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, contents).unwrap();
    }

    fn archived_paths(bytes: &[u8]) -> Vec<String> {
        let decoder = GzDecoder::new(Cursor::new(bytes));
        let mut archive = TarArchive::new(decoder);
        let mut paths: Vec<String> = archive
            .entries()
            .unwrap()
            .map(|entry| {
                entry
                    .unwrap()
                    .path()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        paths.sort();
        paths
    }

    #[test]
    fn create_archive_omits_local_emit_scratch() {
        let root = tempdir().unwrap();
        let build = root.path().join("build");
        write_file(&build, "langgraph.json", "{}\n");
        write_file(&build, "agent.py", "agent = {}\n");
        write_file(
            &build,
            &format!("{PYTHON_EMIT_VENV_DIR}/pyvenv.cfg"),
            "home = /usr/bin\n",
        );
        write_file(
            &build,
            &format!("{PYTHON_EMIT_VENV_DIR}/bin/python"),
            "#!/bin/sh\n",
        );
        write_file(
            &build,
            &format!("{PYTHON_EMIT_SITE_DIR}/pydantic/__init__.py"),
            "",
        );
        write_file(&build, "node_modules/left-pad/index.js", "");
        // Only the top-level install tree is local; a nested source dir that
        // happens to share the name still ships.
        write_file(&build, "vendor/node_modules/keep.js", "");

        let archive = create_archive(&build).unwrap();
        assert_eq!(
            archived_paths(&archive.bytes),
            vec![
                "agent.py".to_string(),
                "langgraph.json".to_string(),
                "vendor/node_modules/keep.js".to_string(),
            ]
        );
    }

    #[test]
    fn create_archive_omits_typescript_emit_node_modules() {
        let root = tempdir().unwrap();
        let build = root.path().join("build");
        write_file(&build, "langgraph.json", "{}\n");
        write_file(&build, "package.json", "{}\n");
        write_file(&build, "package-lock.json", "{}\n");
        write_file(&build, "__runtime__/index.js", "export {};\n");
        write_file(&build, "node_modules/zod/package.json", "{}\n");
        write_file(&build, "node_modules/.bin/tsx", "#!/bin/sh\n");

        let archive = create_archive(&build).unwrap();
        assert_eq!(
            archived_paths(&archive.bytes),
            vec![
                "__runtime__/index.js".to_string(),
                "langgraph.json".to_string(),
                "package-lock.json".to_string(),
                "package.json".to_string(),
            ]
        );
    }
}
