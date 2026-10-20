//! Harbor adapter staging and initial job-config emission for `mda evals init`.

use anyhow::{Context, Result};
use serde::Serialize;
use serde_json::json;
use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use super::init::{
    ensure_project_directory, validate_generated_file, validate_project_directory_path,
    write_generated_file,
};
use super::types::{
    EVAL_DATASET_DIR, GENERATED_EVAL_DIR, HARBOR_ADAPTER_AGENT, HARBOR_ADAPTER_INIT,
    HARBOR_JOB_PLUGIN, HARBOR_LANGSMITH_PLUGIN, HARBOR_NODE_RUNNER, HARBOR_PYTHON_RUNNER,
};
use crate::deploy::helpers::REMOVED_WORKSPACE_ID_ENVS;
use crate::langsmith::{GATEWAY_API_KEY_ENV, REMOVED_API_KEY_ENVS};

const MDA_AGENT_IMPORT_PATH: &str = "mda_harbor.agent:ManagedDeepAgent";

fn harbor_adapter_paths(project_root: &Path) -> (PathBuf, PathBuf, [PathBuf; 6]) {
    let adapter_relative = Path::new(GENERATED_EVAL_DIR).join("harbor-adapter");
    let package_relative = adapter_relative.join("mda_harbor");
    let runners_relative = package_relative.join("runners");
    let root = project_root.join(&adapter_relative);
    let package = root.join("mda_harbor");
    let runners = package.join("runners");
    let files = [
        package.join("__init__.py"),
        package.join("agent.py"),
        package.join("job_plugin.py"),
        package.join("langsmith_plugin.py"),
        runners.join("node_runner.mjs"),
        runners.join("python_runner.py"),
    ];
    (root, runners_relative, files)
}

pub(crate) fn validate_harbor_adapter_paths(project_root: &Path) -> Result<()> {
    let (_, runners_relative, files) = harbor_adapter_paths(project_root);
    validate_project_directory_path(project_root, &runners_relative)?;
    for path in &files {
        validate_generated_file(path)?;
    }
    Ok(())
}

/// Stage the embedded MDA Harbor adapter under `.mda/evals/harbor-adapter`.
pub(crate) fn stage_harbor_adapter(project_root: &Path) -> Result<PathBuf> {
    validate_harbor_adapter_paths(project_root)?;
    let (root, runners_relative, files) = harbor_adapter_paths(project_root);

    ensure_project_directory(project_root, &runners_relative)?;
    let contents = [
        HARBOR_ADAPTER_INIT,
        HARBOR_ADAPTER_AGENT,
        HARBOR_JOB_PLUGIN,
        HARBOR_LANGSMITH_PLUGIN,
        HARBOR_NODE_RUNNER,
        HARBOR_PYTHON_RUNNER,
    ];
    for (path, contents) in files.iter().zip(contents) {
        write_generated_file(path, contents)?;
    }
    Ok(root)
}

#[derive(Serialize)]
struct InitialJobConfig<'a> {
    jobs_dir: &'a Path,
    n_attempts: u8,
    n_concurrent_trials: u8,
    quiet: bool,
    environment: InitialEnvironment,
    agents: [InitialAgent<'a>; 1],
    datasets: [InitialDataset<'a>; 1],
    verifier: InitialVerifier,
}

#[derive(Serialize)]
struct InitialEnvironment {
    r#type: &'static str,
}

#[derive(Serialize)]
struct InitialAgent<'a> {
    import_path: &'static str,
    kwargs: InitialAgentKwargs<'a>,
    env: serde_json::Value,
    include_logs: [&'static str; 1],
}

#[derive(Serialize)]
struct InitialAgentKwargs<'a> {
    artifact_path: &'a Path,
    runtime: &'static str,
    adapter_protocol_version: u8,
}

#[derive(Serialize)]
struct InitialDataset<'a> {
    path: &'a Path,
}

#[derive(Serialize)]
struct InitialVerifier {
    include_logs: [&'static str; 1],
}

#[derive(Clone, Copy)]
enum GeneratedJobConfigKind {
    CompileLegacy,
    AbsoluteInit,
}

fn legacy_task_moves(config_path: &Path) -> Result<Vec<(PathBuf, PathBuf)>> {
    let dataset_dir = config_path
        .parent()
        .context("legacy Harbor job config is missing its dataset directory")?;
    let legacy_tasks = dataset_dir.join("tasks");
    match fs::symlink_metadata(&legacy_tasks) {
        Ok(metadata) if metadata.file_type().is_symlink() => anyhow::bail!(
            "legacy Harbor task directory must not be a symlink: {}",
            legacy_tasks.display()
        ),
        Ok(metadata) if !metadata.is_dir() => anyhow::bail!(
            "legacy Harbor task path is not a directory: {}",
            legacy_tasks.display()
        ),
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => {
            return Err(error)
                .with_context(|| format!("failed to inspect {}", legacy_tasks.display()))
        }
    }

    let mut entries = fs::read_dir(&legacy_tasks)
        .with_context(|| format!("failed to read {}", legacy_tasks.display()))?
        .collect::<std::io::Result<Vec<_>>>()
        .with_context(|| format!("failed to enumerate {}", legacy_tasks.display()))?;
    entries.sort_by_key(fs::DirEntry::file_name);

    let mut moves = Vec::with_capacity(entries.len());
    for entry in entries {
        let source = entry.path();
        let file_type = entry
            .file_type()
            .with_context(|| format!("failed to inspect {}", source.display()))?;
        if file_type.is_symlink() || (!file_type.is_dir() && !file_type.is_file()) {
            anyhow::bail!(
                "legacy Harbor task entry must be a real file or directory: {}",
                source.display()
            );
        }
        let destination = dataset_dir.join(entry.file_name());
        match fs::symlink_metadata(&destination) {
            Ok(_) => anyhow::bail!(
                "cannot migrate legacy Harbor task {} because {} already exists",
                source.display(),
                destination.display()
            ),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("failed to inspect {}", destination.display()))
            }
        }
        moves.push((source, destination));
    }
    Ok(moves)
}

fn rollback_legacy_task_moves(moves: &[(PathBuf, PathBuf)]) -> Result<()> {
    for (source, destination) in moves.iter().rev() {
        fs::rename(destination, source).with_context(|| {
            format!(
                "failed to roll back legacy Harbor task {} to {}",
                destination.display(),
                source.display()
            )
        })?;
    }
    Ok(())
}

fn apply_legacy_task_moves(moves: &[(PathBuf, PathBuf)]) -> Result<Vec<(PathBuf, PathBuf)>> {
    let mut applied = Vec::with_capacity(moves.len());
    for (source, destination) in moves {
        if let Err(error) = fs::rename(source, destination) {
            let failure = format!(
                "failed to migrate legacy Harbor task {} to {}: {error}",
                source.display(),
                destination.display()
            );
            if let Err(rollback_error) = rollback_legacy_task_moves(&applied) {
                anyhow::bail!("{failure}; rollback also failed: {rollback_error:#}");
            }
            anyhow::bail!(failure);
        }
        applied.push((source.clone(), destination.clone()));
    }
    Ok(applied)
}

fn generated_job_config_kind(config: &serde_json::Value) -> Option<GeneratedJobConfigKind> {
    let agents = config.get("agents")?.as_array()?;
    let datasets = config.get("datasets")?.as_array()?;
    if agents.len() != 1 || datasets.len() != 1 {
        return None;
    }

    let agent = agents.first()?.as_object()?;
    let kwargs = agent.get("kwargs")?.as_object()?;
    if agent.get("import_path")?.as_str()? != MDA_AGENT_IMPORT_PATH
        || kwargs.get("runtime")?.as_str()? != "auto"
        || kwargs.get("adapter_protocol_version")?.as_u64()? != 1
    {
        return None;
    }

    let jobs_dir = Path::new(config.get("jobs_dir")?.as_str()?);
    let artifact_path = Path::new(kwargs.get("artifact_path")?.as_str()?);
    let dataset_path = Path::new(datasets.first()?.get("path")?.as_str()?);
    if !jobs_dir.is_absolute() || !artifact_path.is_absolute() || !dataset_path.is_absolute() {
        return None;
    }

    let absolute_init_root = dataset_path
        .parent()
        .filter(|_| dataset_path.ends_with(EVAL_DATASET_DIR));
    if absolute_init_root.is_some_and(|root| {
        artifact_path == root.join(GENERATED_EVAL_DIR).join("artifact")
            && jobs_dir == root.join(GENERATED_EVAL_DIR).join("jobs")
    }) {
        return Some(GeneratedJobConfigKind::AbsoluteInit);
    }

    let legacy_root = dataset_path
        .parent()
        .and_then(Path::parent)
        .filter(|_| dataset_path.ends_with(Path::new(EVAL_DATASET_DIR).join("tasks")));
    let run_id = jobs_dir.file_name().and_then(|value| value.to_str())?;
    let digest = kwargs.get("artifact_sha256")?.as_str()?;
    let graph = kwargs.get("graph")?.as_str()?;
    let model = agent.get("model_name")?.as_str()?;
    let is_legacy = legacy_root.is_some_and(|root| {
        artifact_path == root.join(EVAL_DATASET_DIR).join("artifact")
            && jobs_dir.parent() == Some(root.join(EVAL_DATASET_DIR).join("harbor-jobs").as_path())
    }) && config.get("job_name").and_then(|value| value.as_str())
        == Some("mda-eval")
        && !run_id.is_empty()
        && run_id.bytes().all(|byte| byte.is_ascii_digit())
        && digest.len() == 16
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        && !model.trim().is_empty()
        && crate::agent_config::validate_assistant_id(graph, "legacy Harbor job config").is_ok();

    is_legacy.then_some(GeneratedJobConfigKind::CompileLegacy)
}

fn migrate_generated_job_config(path: &Path) -> Result<()> {
    let contents =
        fs::read_to_string(path).with_context(|| format!("failed to read {}", path.display()))?;
    let Ok(mut config) = serde_json::from_str::<serde_json::Value>(&contents) else {
        return Ok(());
    };
    let Some(kind) = generated_job_config_kind(&config) else {
        return Ok(());
    };

    let legacy_moves = match kind {
        GeneratedJobConfigKind::CompileLegacy => {
            let Some(config_object) = config.as_object_mut() else {
                return Ok(());
            };
            config_object.remove("job_name");
            legacy_task_moves(path)?
        }
        GeneratedJobConfigKind::AbsoluteInit => Vec::new(),
    };

    let Some(jobs_dir) = config.get_mut("jobs_dir") else {
        return Ok(());
    };
    *jobs_dir = json!(Path::new(GENERATED_EVAL_DIR).join("jobs"));
    let Some(kwargs) = config
        .pointer_mut("/agents/0/kwargs")
        .and_then(serde_json::Value::as_object_mut)
    else {
        return Ok(());
    };
    kwargs.insert(
        "artifact_path".to_string(),
        json!(Path::new(GENERATED_EVAL_DIR).join("artifact")),
    );
    if matches!(kind, GeneratedJobConfigKind::CompileLegacy) {
        kwargs.remove("artifact_sha256");
        kwargs.remove("graph");
    }
    let Some(configured_dataset_path) = config.pointer_mut("/datasets/0/path") else {
        return Ok(());
    };
    *configured_dataset_path = json!(EVAL_DATASET_DIR);

    let upgraded = format!(
        "{}\n",
        serde_json::to_string_pretty(&config)
            .context("failed to serialize upgraded Harbor job config")?
    );
    let applied_moves = apply_legacy_task_moves(&legacy_moves)?;
    if let Err(error) = fs::write(path, upgraded) {
        let failure = format!(
            "failed to upgrade legacy Harbor job config {}: {error}",
            path.display()
        );
        if let Err(rollback_error) = rollback_legacy_task_moves(&applied_moves) {
            anyhow::bail!("{failure}; task rollback also failed: {rollback_error:#}");
        }
        anyhow::bail!(failure);
    }
    Ok(())
}

/// Create the user-owned Harbor job config once, preserving all later edits.
/// Recognized configs from older MDA generators receive a one-time path upgrade.
pub(crate) fn write_job_config_if_missing(
    project_root: &Path,
    dataset_dir: &Path,
) -> Result<PathBuf> {
    let path = dataset_dir.join("harbor-job.json");
    let artifact_dir = Path::new(GENERATED_EVAL_DIR).join("artifact");
    let jobs_dir = Path::new(GENERATED_EVAL_DIR).join("jobs");
    let dataset_path = Path::new(EVAL_DATASET_DIR);
    let env = provider_env_templates(&project_env_keys(project_root));
    let config = InitialJobConfig {
        jobs_dir: &jobs_dir,
        n_attempts: 1,
        n_concurrent_trials: 1,
        quiet: false,
        environment: InitialEnvironment { r#type: "docker" },
        agents: [InitialAgent {
            import_path: MDA_AGENT_IMPORT_PATH,
            kwargs: InitialAgentKwargs {
                artifact_path: &artifact_dir,
                runtime: "auto",
                adapter_protocol_version: 1,
            },
            env,
            include_logs: ["**/*"],
        }],
        datasets: [InitialDataset { path: dataset_path }],
        verifier: InitialVerifier {
            include_logs: ["**/*"],
        },
    };
    let contents = format!(
        "{}\n",
        serde_json::to_string_pretty(&config).context("failed to serialize Harbor job config")?
    );

    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => file
            .write_all(contents.as_bytes())
            .with_context(|| format!("failed to write {}", path.display()))?,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            let file_type = fs::symlink_metadata(&path)
                .with_context(|| format!("failed to inspect {}", path.display()))?
                .file_type();
            if file_type.is_file() && !file_type.is_symlink() {
                migrate_generated_job_config(&path)?;
                return Ok(path);
            }
            anyhow::bail!(
                "refusing to preserve non-regular Harbor job config {}",
                path.display()
            );
        }
        Err(error) => {
            return Err(error).with_context(|| format!("failed to create {}", path.display()))
        }
    }
    Ok(path)
}

/// Harbor agent `env` templates for the trial container.
///
/// Emits optional `${VAR:-}` templates (never secret values). Harbor resolves
/// them from the **host** environment at `harbor run` time.
///
/// Includes:
/// - all known provider API keys and provider overrides, so a shell
///   export at run time works without regenerating the write-once config
/// - every key from the project's `.env` that [`forwardable`] accepts. Deploy
///   ships the whole file, so trials must too: connector headers, MCP bearer
///   tokens, and tool API keys are indistinguishable from provider keys here,
///   and an allowlist silently drops them.
///
/// The empty default keeps unset providers from blocking unrelated models.
pub(crate) fn provider_env_templates(project_env_keys: &BTreeSet<String>) -> serde_json::Value {
    let mut env = serde_json::Map::new();

    for name in crate::dependencies::provider_api_key_envs()
        .into_iter()
        .chain(PROVIDER_OVERRIDE_ENVS.iter().copied())
        .filter(|name| forwardable(name))
    {
        insert_env_template(&mut env, name);
    }

    for name in project_env_keys.iter().filter(|name| forwardable(name)) {
        insert_env_template(&mut env, name);
    }
    serde_json::Value::Object(env)
}

/// Provider base URLs and related overrides that are not `api_key_env` on a
/// model provider but still change how a trial reaches the model.
const PROVIDER_OVERRIDE_ENVS: &[&str] = &[
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_GEMINI_BASE_URL",
    "LANGSMITH_WORKSPACE_ID",
    "LANGSMITH_API_KEY",
    "LANGSMITH_ENDPOINT",
    // Legacy `--gateway` scaffolds still read this name; keep it in Harbor's
    // host-env templates so shell-exported aliases reach trials.
    GATEWAY_API_KEY_ENV,
    "LANGSMITH_SANDBOX_API_KEY",
    "LANGSMITH_TRACING",
    "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
];

/// Names never forwarded from `.env`: they configure the trial container and
/// runner process (interpreter paths, cwd, adapter contract) rather than the
/// agent, so overriding them breaks module resolution inside Harbor.
const RESERVED_ENV_KEYS: &[&str] = &[
    "HOME",
    "HOSTNAME",
    "LANG",
    "NODE_PATH",
    "PATH",
    "PWD",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELL",
    "TERM",
    "USER",
    "VIRTUAL_ENV",
];

fn forwardable(name: &str) -> bool {
    is_shell_identifier(name)
        && !RESERVED_ENV_KEYS.contains(&name)
        && !retired(name)
        && !name.starts_with("MDA_EVAL_")
        && !name.starts_with("HARBOR_")
}

/// Whether `name` is one of the workspace / API-key names deploy now rejects.
/// A trial must not quietly accept what the rest of the CLI refuses.
fn retired(name: &str) -> bool {
    REMOVED_API_KEY_ENVS.contains(&name) || REMOVED_WORKSPACE_ID_ENVS.contains(&name)
}

/// Whether `name` can be assigned by a POSIX shell (`[A-Za-z_][A-Za-z0-9_]*`).
///
/// `dotenvy` accepts keys a shell cannot export, notably dotted names like
/// `Key.Value`. Templating one would leave Harbor unable to resolve the
/// `${Key.Value}` placeholder from the shell environment.
pub(crate) fn is_shell_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|char| char.is_ascii_alphanumeric() || char == '_')
}

/// Project `.env` keys that cannot be forwarded because a shell cannot export
/// them. Returned so compile can say which keys a trial will not see.
#[cfg(test)]
pub(crate) fn unexportable_env_keys(project_env_keys: &BTreeSet<String>) -> Vec<&str> {
    project_env_keys
        .iter()
        .map(String::as_str)
        .filter(|name| !is_shell_identifier(name))
        .collect()
}

pub(crate) fn insert_env_template(
    env: &mut serde_json::Map<String, serde_json::Value>,
    name: &str,
) {
    env.insert(name.to_string(), json!(format!("${{{name}:-}}")));
}

/// Non-empty keys from the project `.env` (values are not returned).
pub(crate) fn project_env_keys(project_root: &Path) -> BTreeSet<String> {
    crate::deploy::env_file::parse_env_file(&project_root.join(".env"))
        .into_iter()
        .filter(|(_, value)| !value.is_empty())
        .map(|(key, _)| key)
        .collect()
}
