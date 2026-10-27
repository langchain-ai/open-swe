//! Evaluation setup and artifact compilation for `mda evals`.
//!
//! Initialization owns the user config and Harbor adapter. Compilation only
//! builds the managed agent artifact under `.mda/evals/artifact/`.

mod artifact;
mod harbor;
mod helpers;
mod init;
mod types;

#[cfg(test)]
#[path = "tests.rs"]
mod tests;

pub use init::{coding_agent_prompt, init, show_initialized, show_non_interactive_next_steps};
pub use types::{CompileOptions, CompileSummary, InitOptions};

use anyhow::{Context, Result};
use std::path::Path;

use crate::cli_output::{show_sequence, SequenceItem};
use crate::compile as compile_cmd;
use crate::install::Installation;

use artifact::{build_eval_artifact, read_artifact_manifest};
use helpers::looks_like_project_root;
use init::{ensure_project_directory, validate_project_directory_path};
use types::GENERATED_EVAL_DIR;

/// Compile the project into the deterministic eval artifact directory.
pub fn compile(options: CompileOptions) -> Result<CompileSummary> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(&options.path)?;
    if !looks_like_project_root(&project.root) {
        anyhow::bail!(
            "{} does not look like an MDA project (expected agent.ts/agent.py)",
            project.root.display()
        );
    }

    let model_override = options.model.map(|model| model.trim().to_string());
    if model_override.as_deref().is_some_and(str::is_empty) {
        anyhow::bail!("eval model override must not be blank");
    }
    let model_name = match model_override.as_ref() {
        Some(model) => Some(model.clone()),
        None => compile_cmd::read_agent_model(&project)?,
    };

    let generated_relative = Path::new(GENERATED_EVAL_DIR);
    let artifact_relative = match options.artifact_id {
        Some(artifact_id) => {
            let artifact_id = uuid::Uuid::parse_str(&artifact_id)
                .with_context(|| format!("invalid eval artifact ID {artifact_id:?}"))?;
            generated_relative
                .join("artifacts")
                .join(artifact_id.hyphenated().to_string())
        }
        None => generated_relative.join("artifact"),
    };
    validate_project_directory_path(&project.root, &artifact_relative)?;
    let artifact_parent_relative = artifact_relative
        .parent()
        .context("eval artifact path is missing its generated parent")?;
    let artifact_parent = ensure_project_directory(&project.root, artifact_parent_relative)?;
    let artifact_name = artifact_relative
        .file_name()
        .context("eval artifact path is missing its directory name")?;
    let artifact_dir = artifact_parent.join(artifact_name);
    let artifact_dir = build_eval_artifact(
        &project,
        installation.runtime_source(),
        model_name.as_deref(),
        model_override.as_deref(),
        &artifact_dir,
    )?;
    let manifest = read_artifact_manifest(&artifact_dir)?;
    let artifact_sha256 = manifest
        .get("artifact_sha256")
        .and_then(|value| value.as_str())
        .context("eval artifact manifest is missing artifact_sha256")?
        .to_string();

    let summary = CompileSummary {
        artifact_dir,
        model_name,
        artifact_sha256,
    };
    show_compile_summary(&project.root, &summary)?;
    Ok(summary)
}

fn compile_result_items(project_root: &Path, summary: &CompileSummary) -> [SequenceItem; 1] {
    let rel = |path: &Path| -> String {
        path.strip_prefix(project_root)
            .unwrap_or(path)
            .display()
            .to_string()
    };
    let details = [
        rel(&summary.artifact_dir),
        format!(
            "Model: {}",
            summary
                .model_name
                .as_deref()
                .unwrap_or("authored (dynamic)")
        ),
        format!("Digest: {}", summary.artifact_sha256),
    ];
    [SequenceItem::section("Eval artifact ready", details)]
}

fn show_compile_summary(project_root: &Path, summary: &CompileSummary) -> Result<()> {
    show_sequence(&compile_result_items(project_root, summary))
}
