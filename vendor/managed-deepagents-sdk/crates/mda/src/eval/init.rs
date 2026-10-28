//! Evaluation workspace initialization.

use std::fs;
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, Context, Result};
use cliclack::log;
use serde::Serialize;

use super::harbor::{
    reject_legacy_job_config, stage_harbor_adapter, validate_harbor_adapter_paths,
    write_job_config_if_missing,
};
use super::types::{InitOptions, InitSummary, EVAL_DATASET_DIR, GENERATED_EVAL_DIR};
use crate::cli_output::{heading_style, show_sequence, supporting_style, SequenceItem};
use crate::install::Installation;
use crate::project::Language;

#[derive(Serialize)]
struct RuntimeSettings<'a> {
    project_root: &'a Path,
    mda_executable: &'a Path,
    install_channel: &'static str,
    bundled_runtime_path: &'a Path,
    runtime_version: &'a str,
}

/// Initialize the authored and generated evaluation workspace for a project.
pub fn init(options: InitOptions) -> Result<InitSummary> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(&options.path)?;
    let project_root = project
        .root
        .canonicalize()
        .with_context(|| format!("failed to resolve project root {}", project.root.display()))?;
    let dataset_relative = Path::new(EVAL_DATASET_DIR);
    let generated_relative = Path::new(GENERATED_EVAL_DIR);
    validate_project_directory_path(&project_root, dataset_relative)?;
    validate_project_directory_path(&project_root, generated_relative)?;
    validate_generated_file(&project_root.join(GENERATED_EVAL_DIR).join("runtime.json"))?;
    validate_harbor_adapter_paths(&project_root)?;
    reject_legacy_job_config(&project_root)?;
    let dataset_dir = ensure_project_directory(&project_root, dataset_relative)?;
    let generated_dir = ensure_project_directory(&project_root, generated_relative)?;
    stage_harbor_adapter(&project_root)?;
    write_runtime_settings(&project_root, &generated_dir, &installation)?;
    let job_config_path = write_job_config_if_missing(&project_root, &dataset_dir)?;

    Ok(InitSummary {
        project_root,
        language: project.language,
        dataset_dir,
        job_config_path,
    })
}

pub(super) fn validate_project_directory_path(project_root: &Path, relative: &Path) -> Result<()> {
    if relative.is_absolute() {
        bail!("workspace path must be relative: {}", relative.display());
    }

    let mut current = project_root.to_path_buf();
    for component in relative.components() {
        let Component::Normal(component) = component else {
            bail!(
                "workspace path has an invalid component: {}",
                relative.display()
            );
        };
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                bail!(
                    "workspace path must not be a symlink: {}",
                    current.display()
                )
            }
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => bail!("workspace path is not a directory: {}", current.display()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("failed to inspect {}", current.display()))
            }
        }
    }
    Ok(())
}

pub(super) fn ensure_project_directory(project_root: &Path, relative: &Path) -> Result<PathBuf> {
    validate_project_directory_path(project_root, relative)?;

    let mut current = project_root.to_path_buf();
    for component in relative.components() {
        let Component::Normal(component) = component else {
            bail!(
                "workspace path has an invalid component: {}",
                relative.display()
            );
        };
        current.push(component);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Ok(_) => bail!("workspace path is not a directory: {}", current.display()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => fs::create_dir(&current)
                .with_context(|| format!("failed to create {}", current.display()))?,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("failed to inspect {}", current.display()))
            }
        }
    }
    Ok(current)
}

pub(super) fn validate_generated_file(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            bail!("generated file must not be a symlink: {}", path.display())
        }
        Ok(metadata) if metadata.is_file() => Ok(()),
        Ok(_) => bail!("generated file is not a regular file: {}", path.display()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error).with_context(|| format!("failed to inspect {}", path.display())),
    }
}

pub(super) fn write_generated_file(path: &Path, contents: impl AsRef<[u8]>) -> Result<()> {
    validate_generated_file(path)?;
    fs::write(path, contents).with_context(|| format!("failed to write {}", path.display()))
}

fn write_runtime_settings(
    project_root: &Path,
    generated_dir: &Path,
    installation: &Installation,
) -> Result<()> {
    let mda_executable = std::env::current_exe()
        .context("failed to locate the current mda executable")?
        .canonicalize()
        .context("failed to resolve the current mda executable")?;
    let bundled_runtime_path = installation
        .runtime_source()
        .canonicalize()
        .with_context(|| {
            format!(
                "failed to resolve bundled runtime {}",
                installation.runtime_source().display()
            )
        })?;
    let runtime_version = crate::version::package_version();
    let settings = RuntimeSettings {
        project_root,
        mda_executable: &mda_executable,
        install_channel: installation.channel_name(),
        bundled_runtime_path: &bundled_runtime_path,
        runtime_version: &runtime_version,
    };
    let contents = format!(
        "{}\n",
        serde_json::to_string_pretty(&settings)
            .context("failed to serialize eval runtime settings")?
    );
    write_generated_file(&generated_dir.join("runtime.json"), contents)
}

/// Skill prompt shared by noninteractive guidance and explicit handoff.
pub(super) fn coding_agent_handoff_prompt() -> &'static str {
    concat!(
        "Create Harbor eval tasks under evals/ using the eval-engineering skill:\n",
        "npx skills add langchain-ai/langchain-skills --skill eval-engineering --yes\n\n",
        "If npx isn't installed, use the skill directly from:\n",
        "https://github.com/langchain-ai/langchain-skills/blob/main/config/skills/",
        "eval-engineering/SKILL.md",
    )
}

/// Prompt passed to a coding agent or shown for the user to copy.
pub fn coding_agent_prompt(summary: &InitSummary) -> Result<String> {
    let run_command = harbor_run_command(summary)?;
    Ok(format!(
        concat!(
            "{handoff}\n\n",
            "This is a {language} agent. The skill's examples may be written in another ",
            "language; translate them to {language}. {guidance}\n",
            "Managed Deep Agents eval reference: {documentation}\n\n",
            "To run these evals from the project root:\n",
            "{run_command}\n",
        ),
        handoff = coding_agent_handoff_prompt(),
        language = summary.language.label(),
        guidance = language_guidance(summary.language),
        documentation = documentation_url(summary.language),
        run_command = run_command,
    ))
}

/// Task environment and verifier layout for the project's language, so an agent
/// following a Python-heavy skill does not scaffold either for the wrong runtime.
fn language_guidance(language: Language) -> &'static str {
    match language {
        Language::TypeScript => concat!(
            "Base each task's `environment/Dockerfile` on `node:24`, and write each verifier as ",
            "`evals/<task>/tests/*.test.ts` using Node's built-in test runner, invoked from ",
            "`evals/<task>/tests/test.sh` with ",
            "`node --experimental-strip-types --test /tests/*.test.ts`.",
        ),
        Language::Python => concat!(
            "Base each task's `environment/Dockerfile` on `python:3.12`, and write each verifier ",
            "as `evals/<task>/tests/test_*.py` using pytest, invoked from ",
            "`evals/<task>/tests/test.sh` with `python -m pytest /tests -q`.",
        ),
    }
}

fn posix_shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn powershell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[derive(Clone, Copy)]
pub(super) enum LaunchShell {
    Posix,
    PowerShell,
}

pub(super) fn harbor_run_command(summary: &InitSummary) -> Result<String> {
    let shell = if cfg!(windows) {
        LaunchShell::PowerShell
    } else {
        LaunchShell::Posix
    };
    harbor_run_command_for_shell(summary, shell)
}

pub(super) fn harbor_run_command_for_shell(
    summary: &InitSummary,
    shell: LaunchShell,
) -> Result<String> {
    let project_name = summary
        .project_root
        .file_name()
        .and_then(|name| name.to_str())
        .with_context(|| {
            format!(
                "failed to derive eval dataset name from project root {}",
                summary.project_root.display()
            )
        })?;
    let dataset = format!("mda-{project_name}-evals");
    let env_file = if summary.project_root.join(".env").is_file() {
        "--env-file .env "
    } else {
        ""
    };

    Ok(match shell {
        LaunchShell::Posix => format!(
            concat!(
                "HARBOR_LANGSMITH_DATASET={dataset} \\\n",
                "PYTHONPATH=.mda/evals/harbor-adapter \\\n",
                "uv run {env_file}--python 3.12 --with 'harbor[langsmith]==0.21.0' harbor run \\\n",
                "  --config evals/harbor-job.json --yes \\\n",
                "  --plugin mda_harbor.job_plugin:MDAJobPlugin \\\n",
                "  --plugin mda_harbor.langsmith_plugin:LangSmithPlugin",
            ),
            dataset = posix_shell_quote(&dataset),
            env_file = env_file,
        ),
        LaunchShell::PowerShell => format!(
            concat!(
                "$env:HARBOR_LANGSMITH_DATASET = {dataset}\n",
                "$env:PYTHONPATH = '.mda/evals/harbor-adapter'\n",
                "uv run {env_file}--python 3.12 --with 'harbor[langsmith]==0.21.0' harbor run `\n",
                "  --config evals/harbor-job.json --yes `\n",
                "  --plugin mda_harbor.job_plugin:MDAJobPlugin `\n",
                "  --plugin mda_harbor.langsmith_plugin:LangSmithPlugin",
            ),
            dataset = powershell_quote(&dataset),
            env_file = env_file,
        ),
    })
}

/// Show the user-owned eval paths initialized in the project.
pub fn show_initialized(summary: &InitSummary) -> Result<()> {
    writeln!(io::stderr().lock()).context("failed to start eval init summary")?;
    let colors = console::colors_enabled_stderr();
    let heading = heading_style(colors);
    let supporting = supporting_style(colors);
    let [dataset, config] = initialized_details(summary);
    log::step(format!(
        "{}\n{}\n{}",
        heading.apply_to("Eval workspace initialized"),
        supporting.apply_to(dataset),
        supporting.apply_to(config),
    ))?;
    Ok(())
}

fn initialized_details(summary: &InitSummary) -> [String; 2] {
    let relative = |path: &Path| {
        path.strip_prefix(&summary.project_root)
            .unwrap_or(path)
            .display()
            .to_string()
    };

    [
        format!("dataset:   {}", relative(&summary.dataset_dir)),
        format!("config:    {}", relative(&summary.job_config_path)),
    ]
}

pub(super) fn documentation_url(language: Language) -> &'static str {
    match language {
        Language::TypeScript => {
            "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-evals"
        }
        Language::Python => "https://docs.langchain.com/langsmith/python/managed-deep-agents-evals",
    }
}

/// Show eval documentation after non-interactive init.
pub fn show_non_interactive_next_steps(summary: &InitSummary) -> Result<()> {
    let items = [
        SequenceItem::section("Eval workspace initialized", initialized_details(summary)),
        SequenceItem::section(
            "Get started",
            [format!("Docs: {}", documentation_url(summary.language))],
        ),
        SequenceItem::agent_note(
            "Use a coding agent to create evals. Give it this prompt:",
            coding_agent_handoff_prompt(),
        ),
    ];
    show_sequence(&items)
}
