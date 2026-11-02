//! Shared types and constants for `mda evals`.

use std::path::PathBuf;

use crate::project::Language;

pub(crate) const EVAL_DATASET_DIR: &str = "evals";
pub(crate) const GENERATED_EVAL_DIR: &str = ".mda/evals";
/// Bump when eval artifact layout/compile contracts change so cached builds invalidate.
pub(crate) const EVAL_ARTIFACT_FORMAT: &str = "harbor-v9";

/// Harbor host adapter embedded in the CLI so both npm and PyPI installs work
/// without a separately published `mda-harbor` package.
pub(crate) const HARBOR_ADAPTER_INIT: &str = include_str!("../../harbor/mda_harbor/__init__.py");
pub(crate) const HARBOR_ADAPTER_AGENT: &str = include_str!("../../harbor/mda_harbor/agent.py");
pub(crate) const HARBOR_JOB_PLUGIN: &str = include_str!("../../harbor/mda_harbor/job_plugin.py");
pub(crate) const HARBOR_LANGSMITH_PLUGIN: &str =
    include_str!("../../harbor/mda_harbor/langsmith_plugin.py");
pub(crate) const HARBOR_NODE_RUNNER: &str =
    include_str!("../../harbor/mda_harbor/runners/node_runner.mjs");
pub(crate) const HARBOR_PYTHON_RUNNER: &str =
    include_str!("../../harbor/mda_harbor/runners/python_runner.py");

#[derive(Debug)]
pub struct InitOptions {
    /// Project root (e.g. `./myAgent` or `.`).
    pub path: PathBuf,
}

#[derive(Debug)]
pub struct InitSummary {
    pub project_root: PathBuf,
    pub language: Language,
    pub dataset_dir: PathBuf,
    pub job_config_path: PathBuf,
}

#[derive(Debug)]
pub struct CompileOptions {
    /// Project root (e.g. `./myAgent` or `.`).
    pub path: PathBuf,
    /// Optional explicit model override recorded in the artifact manifest.
    pub model: Option<String>,
    /// Optional Harbor job UUID selecting an isolated generated artifact.
    pub artifact_id: Option<String>,
}

#[derive(Debug)]
pub struct CompileSummary {
    pub artifact_dir: PathBuf,
    pub model_name: Option<String>,
    pub artifact_sha256: String,
}
