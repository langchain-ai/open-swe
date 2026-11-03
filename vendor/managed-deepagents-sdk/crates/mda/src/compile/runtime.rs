//! Vendoring the installed `managed-deepagents` runtime into the build output
//! so the deployed app carries the exact runtime that matches the CLI.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde_json::json;

use crate::project::Language;

use super::{sync_file_permissions, write_if_changed, RUNTIME_DIR};

/// Copy the `managed-deepagents` runtime from the developer's installed
/// environment into the build output so the deployed app carries the exact
/// runtime that matches the CLI.
pub(super) fn vendor_runtime(language: Language, source: &Path, out_dir: &Path) -> Result<()> {
    let target = out_dir.join(RUNTIME_DIR);
    fs::create_dir_all(&target)
        .with_context(|| format!("failed to create {}", target.display()))?;

    match language {
        Language::TypeScript => vendor_ts_runtime(source, &target),
        Language::Python => vendor_py_runtime(source, &target),
    }
}

/// Copy TypeScript runtime files (`dist/` contents) into `__runtime__/` and
/// write a minimal `package.json` so the directory is a valid npm workspace
/// package named `managed-deepagents`.
fn vendor_ts_runtime(source_dist: &Path, target: &Path) -> Result<()> {
    copy_dir_recursive(source_dist, target)?;

    // Keep subpath exports aligned with packages/npm/package.json so the
    // runtime barrel (`managed-deepagents/runtime`, …) resolves from the
    // vendored copy when connector-requirements emit installs build deps.
    let pkg = json!({
        "name": "managed-deepagents",
        "version": crate::version::package_version(),
        "type": "module",
        "main": "./index.js",
        "exports": {
            ".": "./index.js",
            "./runtime": "./runtime/index.js"
        }
    });
    let mut pkg_json =
        serde_json::to_string_pretty(&pkg).context("failed to serialize vendored package.json")?;
    pkg_json.push('\n');
    write_if_changed(&target.join("package.json"), pkg_json.as_bytes())
        .context("failed to write vendored package.json")?;

    Ok(())
}

/// Copy Python runtime files into `__runtime__/` as a pip-installable package.
///
/// Layout:
/// ```text
/// __runtime__/
///   pyproject.toml
///   managed_deepagents/
///     __init__.py
///     connectors/
///     runtime.py
/// ```
fn vendor_py_runtime(source_pkg: &Path, target: &Path) -> Result<()> {
    let pkg_dir = target.join("managed_deepagents");
    fs::create_dir_all(&pkg_dir)
        .with_context(|| format!("failed to create {}", pkg_dir.display()))?;

    // Copy the full package tree so subpackages (e.g. `connectors/`) and package
    // data (e.g. `py.typed`) resolve in the vendored runtime. Skip cache dirs and
    // the platform-specific CLI binary bundled for local installs.
    copy_pkg_dir_recursive(source_pkg, &pkg_dir)?;

    // Keep vendored dependencies aligned with packages/pypi/pyproject.toml.
    let runtime_version = crate::version::package_version();
    let pyproject = format!(
        "\
[project]\n\
name = \"managed-deepagents\"\n\
version = {runtime_version:?}\n\
requires-python = \">=3.11\"\n\
dependencies = [\n\
  \"pydantic>=2.7.4,<2.15\",\n\
  \"httpx>=0.28.1,<1\",\n\
  \"typing-extensions>=4.12.2,<5\",\n\
  \"deepagents>=0.7.30,<1\",\n\
  \"langchain>=1.4.6,<2\",\n\
  \"langchain-core>=1.6.2,<2\",\n\
  \"langgraph>=1.2.15,<2\",\n\
  \"langgraph-prebuilt>=1.1.1,<2\",\n\
  \"langgraph-sdk>=0.4.6,<0.5\",\n\
]\n\
\n\
[build-system]\n\
requires = [\"setuptools>=61\"]\n\
build-backend = \"setuptools.build_meta\"\n"
    );
    write_if_changed(&target.join("pyproject.toml"), pyproject.as_bytes())
        .context("failed to write vendored pyproject.toml")?;

    Ok(())
}

/// Whether a directory entry should be copied into the vendored Python runtime.
fn should_vendor_py_dir(name: &std::ffi::OsStr) -> bool {
    !matches!(name.to_str(), Some("__pycache__" | "_binaries"))
}

/// Recursively copy a Python package directory into the vendored runtime.
fn copy_pkg_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    for entry in fs::read_dir(src).with_context(|| format!("failed to read {}", src.display()))? {
        let entry = entry?;
        let name = entry.file_name();
        let path = entry.path();
        if path.is_dir() {
            if !should_vendor_py_dir(&name) {
                continue;
            }
            let sub_dst = dst.join(&name);
            fs::create_dir_all(&sub_dst)
                .with_context(|| format!("failed to create {}", sub_dst.display()))?;
            copy_pkg_dir_recursive(&path, &sub_dst)?;
            continue;
        }
        if path.extension().and_then(|ext| ext.to_str()) == Some("pyc") {
            continue;
        }
        copy_file_if_changed(&path, &dst.join(&name))?;
    }
    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    for entry in fs::read_dir(src).with_context(|| format!("failed to read {}", src.display()))? {
        let entry = entry?;
        let path = entry.path();
        if path.is_file() {
            copy_file_if_changed(&path, &dst.join(entry.file_name()))?;
        } else if path.is_dir() {
            let sub = dst.join(entry.file_name());
            fs::create_dir_all(&sub)
                .with_context(|| format!("failed to create {}", sub.display()))?;
            copy_dir_recursive(&path, &sub)?;
        }
    }
    Ok(())
}

/// Copy `src` onto `dst` without touching mtime when the bytes already match.
///
/// A `mda dev` refresh re-vendors the whole runtime tree; rewriting files that
/// did not change would wake the LangGraph watcher and restart the worker a
/// second time for every source edit.
pub(super) fn copy_file_if_changed(src: &Path, dst: &Path) -> Result<()> {
    let bytes = fs::read(src).with_context(|| format!("failed to read {}", src.display()))?;
    write_if_changed(dst, &bytes).with_context(|| format!("failed to copy {}", src.display()))?;
    sync_file_permissions(src, dst)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn vendor_py_runtime_preserves_package_metadata() {
        let source = tempfile::tempdir().unwrap();
        fs::write(source.path().join("__init__.py"), "").unwrap();
        let out = tempfile::tempdir().unwrap();
        vendor_py_runtime(source.path(), out.path()).unwrap();

        let vendored: toml_edit::DocumentMut =
            fs::read_to_string(out.path().join("pyproject.toml"))
                .unwrap()
                .parse()
                .unwrap();
        let published: toml_edit::DocumentMut =
            include_str!("../../../../packages/pypi/pyproject.toml")
                .parse()
                .unwrap();
        assert_eq!(
            vendored["project"]["version"].as_str(),
            Some(crate::version::package_version().as_str())
        );
        assert_eq!(
            vendored["project"]["requires-python"].as_str(),
            published["project"]["requires-python"].as_str()
        );
        assert_eq!(
            vendored["project"]["dependencies"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap())
                .collect::<Vec<_>>(),
            published["project"]["dependencies"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap())
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn vendor_py_runtime_copies_package_submodules() {
        let workspace = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent())
            .map(|p| p.join("packages/pypi/src/managed_deepagents"));
        let Some(workspace) = workspace.filter(|p| p.join("connectors/__init__.py").is_file())
        else {
            return;
        };

        let out = tempfile::tempdir().unwrap();
        vendor_py_runtime(&workspace, out.path()).unwrap();

        assert!(out
            .path()
            .join("managed_deepagents/connectors/__init__.py")
            .is_file());
        assert!(out
            .path()
            .join("managed_deepagents/connectors/mcp/load.py")
            .is_file());
        assert!(out.path().join("managed_deepagents/py.typed").is_file());
        assert!(!out.path().join("managed_deepagents/_binaries").exists());
        // Compatibility shims were removed; generated code imports runtime modules
        // under connectors/ directly.
        assert!(!out
            .path()
            .join("managed_deepagents/_connectors_manager.py")
            .exists());
        assert!(!out
            .path()
            .join("managed_deepagents/_github_sandbox.py")
            .exists());
        assert!(!out
            .path()
            .join("managed_deepagents/_langsmith_connector.py")
            .exists());
    }

    #[test]
    fn vendor_ts_runtime_exports_runtime_subpath() {
        let source = tempfile::tempdir().unwrap();
        fs::write(source.path().join("index.js"), "export {};\n").unwrap();
        fs::create_dir_all(source.path().join("runtime")).unwrap();
        fs::write(source.path().join("runtime/index.js"), "export {};\n").unwrap();

        let out = tempfile::tempdir().unwrap();
        vendor_ts_runtime(source.path(), out.path()).unwrap();

        let pkg: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(out.path().join("package.json")).unwrap())
                .unwrap();
        let exports = pkg.get("exports").unwrap();
        assert_eq!(pkg["version"], crate::version::package_version());
        assert_eq!(exports["."], "./index.js");
        assert_eq!(exports["./runtime"], "./runtime/index.js");
        assert!(out.path().join("runtime/index.js").is_file());
        // Legacy channel / evals subpaths were removed from the public package.json;
        // the vendored copy must not resurrect them.
        assert!(exports.get("./channels").is_none());
        assert!(exports.get("./evals").is_none());
    }

    #[test]
    fn copy_dir_recursive_names_unreadable_source() {
        let out = tempfile::tempdir().unwrap();
        let missing = out.path().join("missing");

        let err = copy_dir_recursive(&missing, out.path()).unwrap_err();
        assert_eq!(
            err.to_string(),
            format!("failed to read {}", missing.display())
        );
    }

    #[test]
    fn re_vendoring_leaves_unchanged_runtime_files_untouched() {
        let source = tempfile::tempdir().unwrap();
        let pkg = source.path().join("managed_deepagents");
        fs::create_dir_all(pkg.join("connectors")).unwrap();
        fs::write(pkg.join("__init__.py"), "# pkg\n").unwrap();
        fs::write(pkg.join("connectors/__init__.py"), "# connectors\n").unwrap();

        let out = tempfile::tempdir().unwrap();
        vendor_py_runtime(&pkg, out.path()).unwrap();
        let vendored = out.path().join("managed_deepagents/connectors/__init__.py");
        let mtime_before = fs::metadata(&vendored).unwrap().modified().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));

        // A `mda dev` refresh re-vendors the tree; identical files must keep
        // their mtime so the LangGraph watcher does not restart the worker.
        vendor_py_runtime(&pkg, out.path()).unwrap();
        assert_eq!(
            fs::metadata(&vendored).unwrap().modified().unwrap(),
            mtime_before
        );

        fs::write(pkg.join("connectors/__init__.py"), "# changed\n").unwrap();
        vendor_py_runtime(&pkg, out.path()).unwrap();
        assert_ne!(
            fs::metadata(&vendored).unwrap().modified().unwrap(),
            mtime_before
        );
    }

    #[test]
    fn vendor_py_runtime_skips_cache_dirs() {
        let source = tempfile::tempdir().unwrap();
        let pkg = source.path().join("managed_deepagents");
        fs::create_dir_all(pkg.join("connectors")).unwrap();
        fs::create_dir_all(pkg.join("__pycache__")).unwrap();
        fs::write(pkg.join("__init__.py"), "# pkg\n").unwrap();
        fs::write(pkg.join("connectors/__init__.py"), "# connectors\n").unwrap();
        fs::write(pkg.join("__pycache__/mod.cpython-311.pyc"), "cache").unwrap();

        let out = tempfile::tempdir().unwrap();
        vendor_py_runtime(&pkg, out.path()).unwrap();

        assert!(out
            .path()
            .join("managed_deepagents/connectors/__init__.py")
            .is_file());
        assert!(!out.path().join("managed_deepagents/__pycache__").exists());
    }
}
