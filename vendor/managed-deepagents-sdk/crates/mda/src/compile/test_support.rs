//! Shared fixtures for the compile unit tests.
//!
//! `compile`/`compile_for_deploy` mirror the public entry points but supply
//! a mock vendored runtime, so tests do not need a real installation.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;

use super::{CompileSummary, DeployContext};
use crate::project::{Language, Project};
use crate::toolchain;

pub(crate) fn setup_mock_ts_runtime(root: &Path) {
    let dist = root.join("node_modules/managed-deepagents/dist");
    fs::create_dir_all(&dist).unwrap();
    fs::write(dist.join("index.js"), "// mock runtime\n").unwrap();
    let runtime_dir = dist.join("runtime");
    fs::create_dir_all(&runtime_dir).unwrap();
    fs::write(runtime_dir.join("index.js"), "// mock runtime entry\n").unwrap();
}

fn setup_mock_py_runtime(root: &Path) {
    let rt = root.join("_mock_runtime");
    fs::create_dir_all(&rt).unwrap();
    fs::write(rt.join("__init__.py"), "# mock\n").unwrap();
    fs::write(rt.join("runtime.py"), "# mock runtime\n").unwrap();
    fs::write(rt.join("_connectors.py"), "# mock connectors\n").unwrap();
    fs::write(rt.join("schedules.py"), "# mock schedules\n").unwrap();
}

fn mock_runtime_source(project: &Project) -> PathBuf {
    match project.language {
        Language::TypeScript => project.root.join("node_modules/managed-deepagents/dist"),
        Language::Python => project.root.join("_mock_runtime"),
    }
}

pub(super) fn compile(project: &Project, out_dir: &Path) -> Result<CompileSummary> {
    let runtime_source = mock_runtime_source(project);
    super::compile(project, out_dir, &runtime_source)
}

pub(super) fn compile_refresh(project: &Project, out_dir: &Path) -> Result<CompileSummary> {
    let runtime_source = mock_runtime_source(project);
    super::compile_refresh(project, out_dir, &runtime_source)
}

pub(super) fn compile_for_deploy(
    project: &Project,
    out_dir: &Path,
    context: &DeployContext,
) -> Result<CompileSummary> {
    let runtime_source = mock_runtime_source(project);
    super::compile_for_deploy(project, out_dir, context, &runtime_source)
}

pub(super) fn compile_for_eval(project: &Project, out_dir: &Path) -> Result<CompileSummary> {
    let runtime_source = mock_runtime_source(project);
    let api_version = super::resolved_langgraph_api_version()?;
    super::compile_for_eval_with_api_version(project, out_dir, &runtime_source, &api_version, None)
}

pub(super) fn compile_for_eval_with_model_override(
    project: &Project,
    out_dir: &Path,
    model_override: &str,
) -> Result<CompileSummary> {
    let runtime_source = mock_runtime_source(project);
    let api_version = super::resolved_langgraph_api_version()?;
    super::compile_for_eval_with_api_version(
        project,
        out_dir,
        &runtime_source,
        &api_version,
        Some(model_override),
    )
}

pub(super) fn with_py_runtime<F, R>(root: &Path, f: F) -> R
where
    F: FnOnce() -> R,
{
    setup_mock_py_runtime(root);
    f()
}

/// Whether a working Python 3 interpreter is on PATH, so Python-AST tests can
/// be skipped (rather than failing) on hosts without it.
pub(super) fn python3_available() -> bool {
    toolchain::python3_available()
}

/// Whether `npm` is on PATH, so TypeScript lockfile materialization tests
/// can be skipped on hosts without it.
pub(super) fn npm_available() -> bool {
    toolchain::npm_available()
}

/// Opt into real `npm install --package-lock-only` for lockfile assertions.
///
/// Default compile helpers stub the lockfile so unit tests stay offline.
pub(super) fn with_live_npm_lockfile<F, R>(f: F) -> R
where
    F: FnOnce() -> R,
{
    super::manifest::with_live_npm_lockfile(f)
}
