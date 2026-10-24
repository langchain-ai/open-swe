//! Eval artifact compilation and manifest helpers.

use anyhow::{bail, Context, Result};
use cliclack::{log, spinner};
use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use serde::Serialize;
use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};
use tar::{Archive, Builder};

use crate::compile;
use crate::project::{Language, Project};

use super::helpers::{git_dirty, git_revision, project_digest, tree_digest, ProjectDigestInputs};

const MANIFEST_SCHEMA: &str = "mda.eval-artifact/v2";

struct ArtifactInputs {
    langgraph_api_version: String,
    /// Resolved Python runtime for the build, for Python projects only. Kept
    /// here so the manifest cannot claim a different interpreter than the one
    /// `langgraph.json` pins.
    python_version: Option<String>,
    source_revision: Option<String>,
    dirty: bool,
}

#[derive(Serialize)]
struct ArtifactManifest<'a> {
    schema_version: &'static str,
    artifact_sha256: &'a str,
    project_tree_hash: &'a str,
    language: &'static str,
    node_version: Option<&'static str>,
    python_version: Option<&'a str>,
    graph: &'a str,
    adapter_protocol_version: u8,
    model_name: Option<&'a str>,
    model_override: Option<&'a str>,
    langgraph_api_version: &'a str,
    source_revision: Option<&'a str>,
    dirty: bool,
    runtime_version: String,
}

impl ArtifactInputs {
    fn resolve(project: &Project) -> Result<Self> {
        Ok(Self {
            langgraph_api_version: compile::resolved_langgraph_api_version()?,
            // TypeScript evals ignore the Python runtime, so its configuration
            // is not read or validated for them.
            python_version: match project.language {
                Language::Python => Some(compile::resolved_python_version(&project.root)?.version),
                Language::TypeScript => None,
            },
            source_revision: git_revision(&project.root).ok(),
            dirty: git_dirty(&project.root).unwrap_or(true),
        })
    }
}

fn expected_manifest(
    project: &Project,
    artifact_sha256: &str,
    project_tree_hash: &str,
    graph: &str,
    model_name: Option<&str>,
    model_override: Option<&str>,
    inputs: &ArtifactInputs,
) -> Result<serde_json::Value> {
    let (language, node_version, python_version) = match project.language {
        Language::TypeScript => ("typescript", Some("22"), None),
        Language::Python => ("python", None, inputs.python_version.as_deref()),
    };
    serde_json::to_value(ArtifactManifest {
        schema_version: MANIFEST_SCHEMA,
        artifact_sha256,
        project_tree_hash,
        language,
        node_version,
        python_version,
        graph,
        adapter_protocol_version: 1,
        model_name,
        model_override,
        langgraph_api_version: &inputs.langgraph_api_version,
        source_revision: inputs.source_revision.as_deref(),
        dirty: inputs.dirty,
        runtime_version: crate::version::package_version(),
    })
    .context("failed to serialize expected eval artifact manifest")
}

/// Compile the managed agent into a stable eval artifact at
/// `.mda/evals/artifact/`.
pub(crate) fn build_eval_artifact(
    project: &Project,
    runtime_source: &Path,
    model_name: Option<&str>,
    model_override: Option<&str>,
    artifact_dir: &Path,
) -> Result<PathBuf> {
    let inputs = ArtifactInputs::resolve(project)?;
    let digest = project_digest(
        project,
        &ProjectDigestInputs {
            model_override,
            runtime_source,
            langgraph_api_version: &inputs.langgraph_api_version,
            python_version: inputs.python_version.as_deref(),
            source_revision: inputs.source_revision.as_deref(),
            dirty: inputs.dirty,
        },
    )?;
    let project_dir = artifact_dir.join("project");
    let manifest_path = artifact_dir.join("mda-eval-manifest.json");

    let artifact_exists = match fs::symlink_metadata(artifact_dir) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            bail!(
                "eval artifact path must not be a symlink: {}",
                artifact_dir.display()
            )
        }
        Ok(metadata) if metadata.is_dir() => {
            if cache_is_current(
                project,
                artifact_dir,
                &digest,
                model_name,
                model_override,
                &inputs,
            )? {
                let _ = log::info(format!("Using cached eval artifact ({digest})"));
                return Ok(artifact_dir.to_path_buf());
            }
            true
        }
        Ok(_) => bail!(
            "eval artifact path is not a directory: {}",
            artifact_dir.display()
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => {
            return Err(error)
                .with_context(|| format!("failed to inspect {}", artifact_dir.display()))
        }
    };

    let sp = spinner();
    sp.start(format!("Compiling eval artifact ({digest})"));
    if artifact_exists {
        fs::remove_dir_all(artifact_dir)
            .with_context(|| format!("failed to clear {}", artifact_dir.display()))?;
    }
    fs::create_dir(artifact_dir)
        .with_context(|| format!("failed to create {}", artifact_dir.display()))?;
    let compile_summary = match compile::compile_for_eval_with_api_version(
        project,
        &project_dir,
        runtime_source,
        &inputs.langgraph_api_version,
        model_override,
    ) {
        Ok(summary) => summary,
        Err(err) => {
            sp.error("Eval artifact compile failed");
            return Err(compile::compilation(err));
        }
    };
    let project_tree_hash = tree_digest(&project_dir)?;

    let manifest = expected_manifest(
        project,
        &digest,
        &project_tree_hash,
        &compile_summary.assistant_id,
        model_name,
        model_override,
        &inputs,
    )?;
    fs::write(
        &manifest_path,
        format!("{}\n", serde_json::to_string_pretty(&manifest)?),
    )?;
    let tar_gz = File::create(artifact_dir.join("project.tar.gz"))?;
    let encoder = GzEncoder::new(tar_gz, Compression::default());
    let mut tar = Builder::new(encoder);
    tar.append_dir_all("project", &project_dir)
        .context("failed to write eval artifact tar archive")?;
    tar.finish()
        .context("failed to finish eval artifact tar archive")?;
    let encoder = tar
        .into_inner()
        .context("failed to recover eval artifact gzip stream")?;
    encoder
        .finish()
        .context("failed to finish eval artifact gzip stream")?;
    sp.stop(format!(
        "Compiled eval artifact ({digest}, graph `{}`)",
        compile_summary.assistant_id
    ));
    Ok(artifact_dir.to_path_buf())
}

fn cache_is_current(
    project: &Project,
    artifact_dir: &Path,
    digest: &str,
    model_name: Option<&str>,
    model_override: Option<&str>,
    inputs: &ArtifactInputs,
) -> Result<bool> {
    let project_dir = artifact_dir.join("project");
    if !is_real_directory(&project_dir)?
        || !is_regular_file(&project_dir.join(".mda-build"))?
        || !is_regular_file(&project_dir.join("langgraph.json"))?
        || !is_regular_file(&artifact_dir.join("project.tar.gz"))?
        || !is_regular_file(&artifact_dir.join("mda-eval-manifest.json"))?
    {
        return Ok(false);
    }

    if !compile::has_current_build_marker(&project_dir)? {
        return Ok(false);
    }

    let manifest_path = artifact_dir.join("mda-eval-manifest.json");
    let manifest_bytes = fs::read(&manifest_path)
        .with_context(|| format!("failed to read {}", manifest_path.display()))?;
    let Ok(manifest) = serde_json::from_slice::<serde_json::Value>(&manifest_bytes) else {
        return Ok(false);
    };
    let project_tree_hash = tree_digest(&project_dir)?;
    let assistant_id = crate::agent_config::agent_assistant_id(project)?;
    let expected = expected_manifest(
        project,
        digest,
        &project_tree_hash,
        &assistant_id,
        model_name,
        model_override,
        inputs,
    )?;
    if manifest != expected {
        return Ok(false);
    }

    if !langgraph_is_current(project, &project_dir, &assistant_id)? {
        return Ok(false);
    }
    archive_is_current(artifact_dir)
}

fn langgraph_is_current(project: &Project, project_dir: &Path, assistant_id: &str) -> Result<bool> {
    let path = project_dir.join("langgraph.json");
    let contents = fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let Ok(langgraph) = serde_json::from_slice::<serde_json::Value>(&contents) else {
        return Ok(false);
    };
    let expected_graph = compile::eval_graph_export(project.language);
    Ok(langgraph
        .as_object()
        .and_then(|root| root.get("graphs"))
        .and_then(serde_json::Value::as_object)
        .and_then(|graphs| graphs.get(assistant_id))
        .and_then(serde_json::Value::as_str)
        == Some(expected_graph.as_str()))
}

fn archive_is_current(artifact_dir: &Path) -> Result<bool> {
    let path = artifact_dir.join("project.tar.gz");
    let file = File::open(&path).with_context(|| format!("failed to open {}", path.display()))?;
    let decoder = GzDecoder::new(file);
    let mut archive = Archive::new(decoder);
    let Ok(entries) = archive.entries() else {
        return Ok(false);
    };
    let mut contains_langgraph = false;
    for entry in entries {
        let Ok(mut entry) = entry else {
            return Ok(false);
        };
        let is_langgraph = match entry.path() {
            Ok(entry_path) => entry_path.as_ref() == Path::new("project/langgraph.json"),
            Err(_) => return Ok(false),
        };
        contains_langgraph |= is_langgraph;
        if io::copy(&mut entry, &mut io::sink()).is_err() {
            return Ok(false);
        }
    }
    let mut decoder = archive.into_inner();
    if io::copy(&mut decoder, &mut io::sink()).is_err() {
        return Ok(false);
    }
    Ok(contains_langgraph)
}

fn is_real_directory(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(metadata.is_dir() && !metadata.file_type().is_symlink()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error).with_context(|| format!("failed to inspect {}", path.display())),
    }
}

fn is_regular_file(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => Ok(metadata.is_file() && !metadata.file_type().is_symlink()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error).with_context(|| format!("failed to inspect {}", path.display())),
    }
}

pub(crate) fn read_artifact_manifest(artifact_dir: &Path) -> Result<serde_json::Value> {
    let manifest_path = artifact_dir.join("mda-eval-manifest.json");
    let manifest = serde_json::from_str(
        &fs::read_to_string(&manifest_path)
            .with_context(|| format!("failed to read {}", manifest_path.display()))?,
    )
    .with_context(|| format!("invalid eval manifest at {}", manifest_path.display()))?;
    Ok(manifest)
}
