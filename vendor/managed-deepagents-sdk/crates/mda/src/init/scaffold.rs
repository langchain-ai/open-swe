//! Create the target directory and write starter project files.

use anyhow::{bail, Context, Result};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

use crate::agent_config;
use crate::project::Language;

use super::env::{env_file, sandbox_env_vars};
use super::manifests::manifest_contents;
use super::mcp;
use super::readme::readme;
use super::sandbox;
use super::subagents;
use super::{Blueprint, InitSummary};

/// Placeholder in agent templates replaced with the scaffold project name.
const AGENT_NAME_PLACEHOLDER: &str = "__MDA_AGENT_NAME__";

/// Placeholder in agent templates replaced with the model to run.
const MODEL_PLACEHOLDER: &str = "__MDA_MODEL__";

/// Placeholder in agent templates replaced with extra `define…Agent`
/// arguments (`interrupt_on`), or nothing.
const AGENT_OPTIONS_PLACEHOLDER: &str = "__MDA_AGENT_OPTIONS__";

/// Placeholder in agent templates replaced with the import of the `tools/mcp`
/// definitions subagents select, or nothing.
const AGENT_IMPORTS_PLACEHOLDER: &str = "__MDA_AGENT_IMPORTS__";

/// Placeholder in memory templates replaced with the chosen layer arguments.
pub(crate) const MEMORY_LAYERS_PLACEHOLDER: &str = "__MDA_MEMORY_LAYERS__";

/// Model a scaffolded agent runs on when the caller names none.
pub(crate) const DEFAULT_MODEL: &str = "openai:gpt-6-luna";

/// Minimal `agent.ts` scaffold.
///
/// The agent imports from the published `managed-deepagents` package so the
/// project runs as-is after `npm install`. `mda build`/`mda deploy` vendor the
/// runtime into `__runtime__/` and rewrite the dependency to embed it; the
/// agent file itself is never rewritten.
///
/// The system prompt is managed: the CLI embeds `instructions.md` at deploy
/// time, so the agent file never sets `systemPrompt` directly.
const AGENT_TS: &str = include_str!("templates/agent.ts");

/// Minimal `agent.py` scaffold.
///
/// The agent imports from the published `managed_deepagents` package so the
/// project runs as-is after `uv sync`. `mda build`/`mda deploy` vendor
/// the runtime into `__runtime__/` and rewrite the dependency to embed it; the
/// agent file itself is never rewritten.
///
/// The system prompt is managed: the CLI embeds `instructions.md` at deploy
/// time, so the agent file never sets `system_prompt` directly.
const AGENT_PY: &str = include_str!("templates/agent.py");

/// Minimal `instructions.md` system prompt.
const INSTRUCTIONS_MD: &str = include_str!("templates/instructions.md");

/// `identity.ts` declaring managed user identity.
const IDENTITY_TS: &str = include_str!("templates/identity.ts");

/// `identity.py` declaring managed user identity.
const IDENTITY_PY: &str = include_str!("templates/identity.py");

/// `memory.ts` declaring the project's durable memory.
pub(crate) const MEMORY_TS: &str = include_str!("templates/memory.ts");

/// `memory.py` declaring the project's durable memory.
pub(crate) const MEMORY_PY: &str = include_str!("templates/memory.py");

/// Default TypeScript compiler configuration.
///
/// The starter uses the modern ESM/bundler module settings expected by the
/// managed runtime and validates author code without emitting build artifacts.
const TSCONFIG_JSON: &str = include_str!("templates/tsconfig.json");

/// Default TypeScript LangSmith sandbox declaration.
///
/// Scaffolded by `mda init` so new projects get a managed execution environment
/// out of the box. Delete `sandbox/` to opt out (for example chat-only agents).
const SANDBOX_TS: &str = include_str!("templates/sandbox.ts");

/// Default Python LangSmith sandbox declaration.
///
/// Scaffolded by `mda init` so new projects get a managed execution environment
/// out of the box. Delete `sandbox/` to opt out (for example chat-only agents).
const SANDBOX_PY: &str = include_str!("templates/sandbox.py");

/// `AGENTS.md` for coding agents working in the project.
///
/// Coding agents load it as project instructions. It names the project's
/// commands and asks the agent to end each session by asking whether to keep
/// the agent local or deploy it, without running `mda deploy` on its own.
const AGENTS_MD: &str = include_str!("templates/AGENTS.md");

/// Placeholder in the `AGENTS.md` template replaced with the agent file name.
const AGENT_FILE_PLACEHOLDER: &str = "__MDA_AGENT_FILE__";

/// Placeholder in the `AGENTS.md` template replaced with the install command.
const INSTALL_COMMAND_PLACEHOLDER: &str = "__MDA_INSTALL_COMMAND__";

/// Placeholder in the `AGENTS.md` template replaced with the quickstart docs URL.
const DOCS_URL_PLACEHOLDER: &str = "__MDA_DOCS_URL__";

/// `.gitignore` keeping secrets (`.env`) and generated output out of version
/// control while preserving authored `evals/` tasks. Pairs with the
/// generated `.env`, which may hold real keys.
const GITIGNORE: &str = include_str!("templates/gitignore");

/// Reject a project name that cannot be used as an agent name.
///
/// The project name becomes the deployed agent's assistant id, so it has to
/// satisfy the same rules `mda deploy` enforces.
pub(super) fn validate_project_name(name: &str) -> Result<()> {
    if agent_config::is_valid_assistant_id(name) {
        return Ok(());
    }
    bail!(
        "project name {name:?} is not a valid agent name; use \
         [a-zA-Z][a-zA-Z0-9_-]* (e.g. my-agent)"
    )
}

/// Create the target directory and write the starter project files.
///
/// `langsmith_key`, when present, seeds the generated `.env` file's
/// `LANGSMITH_API_KEY` value.
pub(super) fn scaffold(
    dir: &Path,
    name: &str,
    blueprint: &Blueprint,
    langsmith_key: Option<&str>,
) -> Result<InitSummary> {
    validate_project_name(name)?;
    let language = blueprint.language;
    let model = model(blueprint.model.as_deref())?;

    fs::create_dir_all(dir)
        .with_context(|| format!("failed to create project directory {}", dir.display()))?;

    let (agent_file_name, sandbox_file, sandbox_contents) = match language {
        Language::TypeScript => ("agent.ts", "sandbox/index.ts", SANDBOX_TS),
        Language::Python => ("agent.py", "sandbox/__init__.py", SANDBOX_PY),
    };
    let agent_template = match language {
        Language::TypeScript => AGENT_TS,
        Language::Python => AGENT_PY,
    };
    let agent_contents = agent_template
        .replace(AGENT_NAME_PLACEHOLDER, name)
        .replace(MODEL_PLACEHOLDER, model)
        .replace(
            AGENT_IMPORTS_PLACEHOLDER,
            &subagents::imports(&blueprint.subagents, language),
        )
        .replace(
            AGENT_OPTIONS_PLACEHOLDER,
            &format!(
                "{}{}",
                mcp::agent_options(&blueprint.interrupt_on, language),
                subagents::option(&blueprint.subagents, language)
            ),
        );

    let mut files = vec![
        (agent_file_name, agent_contents),
        (
            "instructions.md",
            instructions(blueprint.instructions.as_deref()),
        ),
        (language.manifest_name(), manifest_contents(name, language)),
    ];
    if language == Language::TypeScript {
        files.push(("tsconfig.json", TSCONFIG_JSON.to_string()));
    }
    // Deployment requires an identity declaration, so every starter has one.
    files.push(match language {
        Language::TypeScript => ("identity.ts", IDENTITY_TS.to_string()),
        Language::Python => ("identity.py", IDENTITY_PY.to_string()),
    });
    // Memory is opt-in end to end: no `--memory` flag means no declaration, and
    // a project without one keeps nothing between runs.
    if let Some(choice) = blueprint.memory {
        let (file_name, template) = match language {
            Language::TypeScript => ("memory.ts", MEMORY_TS),
            Language::Python => ("memory.py", MEMORY_PY),
        };
        files.push((
            file_name,
            template.replace(MEMORY_LAYERS_PLACEHOLDER, &choice.layers_argument(language)),
        ));
    }
    if blueprint.sandbox {
        files.push((
            sandbox_file,
            sandbox::sandbox_module(
                sandbox_contents,
                blueprint.sandbox_options.as_ref(),
                language,
            ),
        ));
    }
    if let Some(module) = mcp::mcp_module(&blueprint.mcp_definitions, language) {
        let mcp_file = match language {
            Language::TypeScript => "tools/mcp.ts",
            Language::Python => "tools/mcp.py",
        };
        files.push((mcp_file, module));
    }
    files.extend([
        ("README.md", readme(name, blueprint)),
        ("AGENTS.md", agents_md(name, agent_file_name, language)),
        (".env", {
            let subagent_models: Vec<&str> = blueprint
                .subagents
                .iter()
                .filter_map(|subagent| subagent.model.as_deref())
                .collect();
            let mut env = env_file(langsmith_key, model, &subagent_models);
            if blueprint.sandbox {
                if let Some(options) = &blueprint.sandbox_options {
                    env.push_str(&sandbox_env_vars(&options.env_vars));
                }
            }
            env
        }),
        (".gitignore", GITIGNORE.to_string()),
    ]);

    let files: Vec<(PathBuf, String)> = files
        .into_iter()
        .map(|(file_name, contents)| (PathBuf::from(file_name), contents))
        .chain(skill_files(&blueprint.skills)?)
        .collect();
    let mut created = Vec::with_capacity(files.len());
    for (file_name, contents) in files {
        let path = dir.join(file_name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        fs::write(&path, contents)
            .with_context(|| format!("failed to write {}", path.display()))?;
        created.push(path);
    }
    Ok(InitSummary {
        root: dir.to_path_buf(),
        files: created,
    })
}

/// The `AGENTS.md` to write for this project and language.
fn agents_md(name: &str, agent_file: &str, language: Language) -> String {
    AGENTS_MD
        .replace(AGENT_NAME_PLACEHOLDER, name)
        .replace(AGENT_FILE_PLACEHOLDER, agent_file)
        .replace(
            INSTALL_COMMAND_PLACEHOLDER,
            super::install_command(language),
        )
        .replace(DOCS_URL_PLACEHOLDER, super::quickstart_docs_url(language))
}

/// Each skill file at `skills/<name>/<path>`, refusing a name or path that
/// would land outside its skill's directory.
fn skill_files(
    skills: &BTreeMap<String, BTreeMap<String, String>>,
) -> Result<Vec<(PathBuf, String)>> {
    let mut files = Vec::new();
    for (name, skill) in skills {
        for (path, contents) in skill {
            let relative = Path::new(name).join(path);
            if !relative
                .components()
                .all(|component| matches!(component, Component::Normal(_)))
            {
                bail!("refusing to write skill file {name}/{path} outside skills/{name}/");
            }
            files.push((Path::new("skills").join(relative), contents.clone()));
        }
    }
    Ok(files)
}

/// The system prompt to write, from the caller's own text or the starter.
///
/// A prompt handed over on a command line rarely ends in a newline, and one
/// read out of a file usually ends in several; `instructions.md` is a file the
/// developer will edit next, so it gets exactly one. Leading whitespace is
/// kept: it can be part of the prompt (an indented first line).
fn instructions(text: Option<&str>) -> String {
    match text
        .map(str::trim_end)
        .filter(|text| !text.trim_start().is_empty())
    {
        Some(text) => format!("{text}\n"),
        None => INSTRUCTIONS_MD.to_string(),
    }
}

/// The model to define the agent with, rejecting anything that would not
/// survive being written into a source file as a quoted string.
fn model(requested: Option<&str>) -> Result<&str> {
    let Some(model) = requested.map(str::trim).filter(|model| !model.is_empty()) else {
        return Ok(DEFAULT_MODEL);
    };
    if model.contains(['"', '\'', '\\', '\n', '\r']) {
        bail!("model {model:?} contains characters that cannot be written into an agent file");
    }
    Ok(model)
}
