//! Creation of new Managed Deep Agent projects.
//!
//! Expands local starter templates into the authoring language selected by the
//! package install channel (npm creates TypeScript, PyPI creates Python), shaped
//! by whatever the [`Blueprint`] carries. See [`scaffold`].
//!
//! The reporting that closes the path uses [`cliclack`] to match the rest of
//! the CLI.

mod env;
mod manifests;
mod mcp;
mod readme;
mod sandbox;
pub(crate) mod scaffold;
pub(crate) mod source;
mod subagents;

use anyhow::{bail, Context, Result};
use clap::ValueEnum;
use cliclack::{input, log, set_theme, StringCursor, Theme, ThemeState};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::cli_output::{continue_sequence, show_wordmark_intro, supporting_style, SequenceItem};
pub use crate::coding_agent::HandoffOutcome;
use crate::coding_agent::ResetTheme;
use crate::langsmith::{API_KEY_ENV, REMOVED_API_KEY_ENVS};
use crate::project::Language;
pub(crate) use env::sandbox_env_key;

use scaffold::{scaffold, validate_project_name};

/// Prompt passed to a coding agent or shown for the user to copy.
pub fn coding_agent_prompt() -> &'static str {
    "Build out my agent using the managed-deep-agents skill:\nnpx skills add langchain-ai/langchain-skills --skill managed-deep-agents --yes\n\nIf npx isn't installed, use the skill directly from:\nhttps://github.com/langchain-ai/langchain-skills/blob/main/config/skills/managed-deep-agents/SKILL.md"
}

/// Offer the shared coding-agent handoff.
pub fn offer_handoff(cwd: &Path, prompt: &str) -> Result<HandoffOutcome> {
    crate::coding_agent::offer_handoff(cwd, prompt, false)
}

/// What to create, and how much of it the caller has already decided.
///
/// A starter project is a starting point, and the parts a developer would
/// change first — the prompt, who may call the agent, which model it runs —
/// are the parts they often already know. Naming them here writes them into
/// the project instead of leaving them to be edited afterwards, which is also
/// what makes `mda init` usable from a script.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Blueprint {
    /// Shape version of a serialized blueprint; bumped on breaking changes.
    pub version: u32,
    /// Directory to create, relative to the working directory.
    pub name: String,
    /// Authoring language, fixed by the package install channel.
    pub language: Language,
    /// System prompt for `instructions.md`. The starter prompt when absent.
    pub instructions: Option<String>,
    /// Model the agent is defined with. The starter model when absent.
    pub model: Option<String>,
    /// Write the managed sandbox declaration.
    pub sandbox: bool,
    /// Durable memory to declare in `memory.{ts,py}`, when the author asks for
    /// it. `None` writes no file — memory is opt-in.
    pub memory: Option<MemoryChoice>,
    /// Messaging channels to scaffold next to the agent. Empty leaves
    /// `channels/` uncreated; authors can still run `mda channels init` later.
    pub channels: Vec<InitChannel>,
    /// MCP definitions to export from `tools/mcp.{ts,py}`. The agent loads
    /// the one named [`AGENT_MCP`]; subagents select theirs by name. Empty
    /// writes no file.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub mcp_definitions: Vec<McpDefinition>,
    /// Tools that ask a person before they run, keyed by the tool's exposed
    /// `{server}_{tool}` name.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub interrupt_on: BTreeMap<String, InterruptRule>,
    /// Sync subagents in the agent file's `subagents`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub subagents: Vec<Subagent>,
    /// Skills written under `skills/<name>/`, keyed by skill name, then by
    /// path inside the skill.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub skills: BTreeMap<String, BTreeMap<String, String>>,
    /// Settings written into `sandbox/` in place of the starter's. Only read
    /// when `sandbox` is set.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sandbox_options: Option<SandboxOptions>,
}

/// The name of the MCP definition the agent loads.
pub const AGENT_MCP: &str = "mcp";

/// One exported `define_mcp` / `defineMcp` in `tools/mcp.{ts,py}`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpDefinition {
    /// snake_case export name, such as `mcp` or `web_researcher_subagent_mcp`.
    /// TypeScript writes it in camelCase.
    pub name: String,
    pub servers: Vec<McpServer>,
}

/// One remote MCP server in `tools/mcp.{ts,py}`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpServer {
    /// Logical server name; tools are exposed as `{key}_{tool}`.
    pub key: String,
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub connection: Option<Connection>,
    /// Signs in through LangSmith (`auth: "langsmith"`), as a Tool Gateway server does.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub langsmith_auth: bool,
    /// Raw upstream tool names to expose.
    pub include_tools: Vec<String>,
    /// When set, the server is written commented out under this note, for
    /// one that can't load until the developer does something first.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub commented_out: Option<String>,
}

/// A `connections.get(slug, { type })` reference.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Connection {
    pub slug: String,
    pub owner: ConnectionOwner,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConnectionOwner {
    User,
    Agent,
}

/// A declarative sync subagent in the agent file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Subagent {
    pub name: String,
    pub description: String,
    pub system_prompt: String,
    /// Unset inherits the agent's model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    /// Names of the [`McpDefinition`]s it selects; empty is `mcp: []`.
    pub mcp: Vec<String>,
    /// Keyed by `{server}_{tool}`, like the agent's.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub interrupt_on: BTreeMap<String, InterruptRule>,
}

/// `define_sandbox` / `defineSandbox` options beyond the starter's.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxOptions {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snapshot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub idle_ttl_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delete_after_stop_seconds: Option<u64>,
    /// Set for every command run in the sandbox (`run_config.env_vars`).
    /// Values go into `.env`; `sandbox/` reads them by name.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub env_vars: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_control: Option<AccessControl>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rules: Vec<ProxyRule>,
    /// AWS and GCP signing rules, passed to the sandbox proxy as written.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub provider_rules: Vec<serde_json::Value>,
}

/// The hosts the sandbox may reach (`proxy_config.access_control`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccessControl {
    AllowList(Vec<String>),
    DenyList(Vec<String>),
}

/// A sandbox proxy rule that adds headers to requests for `match_hosts`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProxyRule {
    pub name: String,
    pub match_hosts: Vec<String>,
    pub headers: Vec<ProxyHeader>,
    /// When set, the rule is written commented out under this note, for one
    /// that can't work until the developer does something first.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub commented_out: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProxyHeader {
    pub name: String,
    pub value: HeaderValue,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HeaderValue {
    /// A template the proxy fills from workspace secrets, such as
    /// `Bearer {ACME_KEY}`.
    WorkspaceSecret(String),
    /// A connection's credential, resolved before each command.
    Connection {
        connection: Connection,
        format: ConnectionFormat,
    },
}

/// How a connection's credential is written into a header.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnectionFormat {
    Raw,
    Bearer,
    Basic {
        username: String,
    },
    /// `credential_template` text with `{token}` where the credential goes.
    Template(String),
}

/// An `interrupt_on` entry. With neither field set it renders as `true`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct InterruptRule {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub allowed_decisions: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// Current [`Blueprint::version`].
// 2: a Slack channel carries its options (`{"slack": {...}}`, not `"slack"`).
pub const BLUEPRINT_VERSION: u32 = 2;

impl Blueprint {
    /// The project `mda init <name>` creates when told nothing else.
    pub fn new(name: impl Into<String>, language: Language) -> Self {
        Self {
            version: BLUEPRINT_VERSION,
            name: name.into(),
            language,
            instructions: None,
            model: None,
            sandbox: true,
            memory: None,
            channels: Vec::new(),
            mcp_definitions: Vec::new(),
            interrupt_on: BTreeMap::new(),
            subagents: Vec::new(),
            skills: BTreeMap::new(),
            sandbox_options: None,
        }
    }
}

pub use crate::channel::InitChannel;

/// The durable memory a scaffolded `memory.ts` / `memory.py` declares.
///
/// Memory is opt-in, so `mda init` writes the file only when asked. `None` still
/// writes one — an explicit "this agent keeps no durable memory" is a useful
/// thing for a scaffold to say out loud.
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MemoryChoice {
    /// Deployment-shared memory at `/memories/agent/`, readable and writable by
    /// every caller.
    Agent,
    /// No durable memory.
    None,
}

impl MemoryChoice {
    /// The layer arguments to write into the scaffolded declaration, in the
    /// authoring syntax of `language`.
    pub(crate) fn layers_argument(self, language: Language) -> String {
        match (self, language) {
            (Self::Agent, Language::Python) => "agent=MemoryLayer()".to_string(),
            (Self::Agent, Language::TypeScript) => "{ agent: memoryLayer() }".to_string(),
            (Self::None, Language::Python) => String::new(),
            (Self::None, Language::TypeScript) => "{}".to_string(),
        }
    }

    /// The layer name, for prose that has to name it.
    pub(crate) const fn layer_name(self) -> &'static str {
        match self {
            Self::Agent => "agent",
            Self::None => "none",
        }
    }
}

/// Outcome of creating a new project, returned for reporting.
#[derive(Debug)]
pub struct InitSummary {
    /// Directory the project was created in.
    pub root: PathBuf,
    /// Files created, in creation order.
    pub files: Vec<PathBuf>,
}

struct InlineNameTheme;

impl Theme for InlineNameTheme {
    fn format_header(&self, state: &ThemeState, prompt: &str) -> String {
        format!("{}  {prompt}: ", self.state_symbol(state))
    }

    fn format_input(&self, state: &ThemeState, cursor: &StringCursor) -> String {
        let style = self.input_style(state);
        match state {
            ThemeState::Active | ThemeState::Error(_) => self.cursor_with_style(cursor, &style),
            ThemeState::Cancel | ThemeState::Submit => cursor.to_string(),
        }
    }

    fn format_placeholder(&self, state: &ThemeState, cursor: &StringCursor) -> String {
        let style = self.placeholder_style(state);
        match state {
            ThemeState::Active | ThemeState::Error(_) => self.cursor_with_style(cursor, &style),
            ThemeState::Cancel => String::new(),
            ThemeState::Submit => cursor.to_string(),
        }
    }

    fn format_footer_with_message(&self, state: &ThemeState, _message: &str) -> String {
        let bar = self.bar_color(state);
        match state {
            ThemeState::Active | ThemeState::Submit => "\n".to_string(),
            ThemeState::Cancel => {
                format!("\n{}  Operation cancelled.\n", bar.apply_to("└"))
            }
            ThemeState::Error(error) => format!("\n{}  {error}\n", bar.apply_to("└")),
        }
    }
}

/// Start the init presentation before collecting or validating input.
pub fn begin() -> Result<()> {
    show_wordmark_intro()
}

/// Ask for the project name before scaffolding when `mda init` omits it.
pub fn prompt_project_name(cwd: &Path) -> Result<Option<String>> {
    let validation_root = cwd.to_path_buf();
    let mut prompt =
        input("Name your agent")
            .placeholder("my-agent")
            .validate(move |name: &String| {
                normalize_prompted_project_name(&validation_root, name).map(|_| ())
            });

    set_theme(InlineNameTheme);
    let _reset_theme = ResetTheme;

    match prompt.interact::<String>() {
        Ok(name) => normalize_prompted_project_name(cwd, &name).map(Some),
        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => Ok(None),
        Err(error) => Err(error).context("failed to read the agent name"),
    }
}

fn normalize_prompted_project_name(cwd: &Path, name: &str) -> Result<String> {
    let name = name.trim();
    validate_project_name(name)?;

    let dir = cwd.join(name);
    if dir.exists() {
        bail!(
            "destination {} already exists; choose another name",
            dir.display()
        );
    }
    Ok(name.to_string())
}

/// Validate the project target without creating files or rendering the init UI.
pub fn validate_project_target(cwd: &Path, name: &str) -> Result<()> {
    validated_project_target(cwd, name).map(|_| ())
}

fn validated_project_target(cwd: &Path, name: &str) -> Result<(PathBuf, String)> {
    let name = name.trim();
    if name.is_empty() {
        bail!("project name must not be empty");
    }
    let dir = cwd.join(name);
    if dir.exists() {
        bail!(
            "destination {} already exists; remove it or run `mda init` with another name",
            dir.display()
        );
    }
    let project_name = project_name_from_path(&dir, name)?;
    validate_project_name(&project_name)?;
    Ok((dir, project_name))
}

/// Initialise a new Managed Deep Agent project under `cwd`.
pub fn init(cwd: &Path, blueprint: &Blueprint) -> Result<InitSummary> {
    let (dir, project_name) = validated_project_target(cwd, &blueprint.name)?;

    // Prefer the current name, but migrate a retired alias into the scaffolded
    // `.env` so an old shell configuration does not block project creation.
    let langsmith_key = std::iter::once(API_KEY_ENV)
        .chain(REMOVED_API_KEY_ENVS.iter().copied())
        .find_map(|key| {
            std::env::var(key).ok().and_then(|value| {
                let value = value.trim();
                (!value.is_empty()).then(|| value.to_string())
            })
        });

    if langsmith_key.is_some() {
        log::info("Found a LangSmith API key in your environment, seeding .env with it.")?;
    }
    let mut summary = scaffold(&dir, &project_name, blueprint, langsmith_key.as_deref())?;
    add_requested_channels(&mut summary, blueprint)?;

    Ok(summary)
}

fn add_requested_channels(summary: &mut InitSummary, blueprint: &Blueprint) -> Result<()> {
    // One file per channel type; the first entry of a type wins.
    let mut added = Vec::new();
    for channel in &blueprint.channels {
        if added.contains(&channel.kind()) {
            continue;
        }
        let relative = crate::channel::init_channel(&summary.root, channel)?;
        summary.files.push(summary.root.join(&relative));
        added.push(channel.kind());
    }
    Ok(())
}

/// Show the Clack-style creation message used by interactive init.
pub fn show_interactive_created(project_root: &Path) -> Result<()> {
    let project_name = project_directory_name(project_root)?;
    console::Term::stderr()
        .write_line(&created_line(project_name))
        .context("failed to report the created agent")?;
    Ok(())
}

fn created_line(project_name: &str) -> String {
    created_line_with_color(project_name, console::colors_enabled_stderr())
}

fn created_line_with_color(project_name: &str, colors: bool) -> String {
    supporting_style(colors)
        .apply_to(format!("│  Created {project_name}/\n│"))
        .to_string()
}

fn handoff_continuation_line_with_color(colors: bool) -> String {
    format!("{}\n", supporting_style(colors).apply_to("│"))
}

/// Continue the Clack flow after displaying a raw coding-agent prompt.
pub fn show_handoff_continuation() -> Result<()> {
    console::Term::stderr()
        .write_str(&handoff_continuation_line_with_color(
            console::colors_enabled_stderr(),
        ))
        .context("failed to continue the coding-agent handoff")
}

/// Show deterministic next steps after non-interactive project creation.
pub fn show_non_interactive_next_steps(project_root: &Path, language: Language) -> Result<()> {
    let project_name = project_directory_name(project_root)?;
    let docs_url = quickstart_docs_url(language);

    console::Term::stderr()
        .write_line(&created_line(project_name))
        .context("failed to report the created agent")?;
    let items = [
        SequenceItem::section("Get started", [format!("Docs: {docs_url}")]),
        SequenceItem::section(
            "Run locally",
            [
                format!("cd {project_name}"),
                install_command(language).to_string(),
                "mda dev".to_string(),
            ],
        ),
        SequenceItem::agent_note(
            "Use a coding agent to build your agent. Give it this prompt:",
            coding_agent_prompt(),
        ),
        SequenceItem::section(
            "Deploy when ready",
            [
                format!("cd {project_name}"),
                install_command(language).to_string(),
                "mda deploy".to_string(),
            ],
        ),
    ];
    continue_sequence(&items)
}

/// Show the commands for deploying a scaffold after a non-agent handoff.
pub fn show_deploy_steps(project_root: &Path, language: Language) -> Result<()> {
    let project_name = project_directory_name(project_root)?;
    continue_sequence(&[SequenceItem::section(
        "Deploy when ready",
        [
            format!("cd {project_name}"),
            install_command(language).to_string(),
            "mda deploy".to_string(),
        ],
    )])
}

fn project_directory_name(project_root: &Path) -> Result<&str> {
    project_root
        .file_name()
        .and_then(|component| component.to_str())
        .context("created project path has no directory name")
}

/// Derive the package/project name written into manifests from the target
/// directory rather than the raw CLI argument. This keeps `mda init ./my-agent`
/// from writing invalid package names such as `"./my-agent"`.
fn project_name_from_path(dir: &Path, original: &str) -> Result<String> {
    dir.file_name()
        .and_then(|component| component.to_str())
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_string)
        .ok_or_else(|| anyhow::anyhow!("project name {original:?} must include a directory name"))
}

/// Quickstart docs for the project's language.
fn quickstart_docs_url(language: Language) -> &'static str {
    match language {
        Language::TypeScript => {
            "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart"
        }
        Language::Python => {
            "https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"
        }
    }
}

/// Command to install the project's dependencies.
pub(crate) fn install_command(language: Language) -> &'static str {
    match language {
        Language::TypeScript => "npm install",
        Language::Python => "uv sync",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::init::env::env_file;
    use crate::init::scaffold::scaffold;
    use std::fs;

    /// The project `mda init myAgent` asks for.
    fn blueprint(language: Language) -> Blueprint {
        Blueprint::new("myAgent", language)
    }

    #[test]
    fn coding_agent_prompt_installs_and_follows_the_skill_from_the_project_root() {
        assert_eq!(
            coding_agent_prompt(),
            "Build out my agent using the managed-deep-agents skill:\nnpx skills add langchain-ai/langchain-skills --skill managed-deep-agents --yes\n\nIf npx isn't installed, use the skill directly from:\nhttps://github.com/langchain-ai/langchain-skills/blob/main/config/skills/managed-deep-agents/SKILL.md"
        );
    }

    #[test]
    fn scaffold_creates_typescript_project() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::TypeScript), None).unwrap();

        let agent = fs::read_to_string(dir.join("agent.ts")).unwrap();
        assert!(agent.contains("defineDeepAgent"));
        assert!(agent.contains("name: \"myAgent\""));
        // Imports the published package, not the build-only vendored runtime
        // directory, so the scaffold runs as-is after `npm install`.
        assert!(agent.contains("from \"managed-deepagents\""));
        assert!(!agent.contains("__runtime__"));
        assert!(!agent.contains("__MDA_AGENT_NAME__"));
        // The system prompt is managed: the scaffold never wires it manually.
        assert!(!agent.contains("systemPrompt"));
        assert!(!agent.contains("instructions.md"));

        assert!(fs::read_to_string(dir.join("instructions.md"))
            .unwrap()
            .contains("You are a helpful assistant."));

        let sandbox = fs::read_to_string(dir.join("sandbox/index.ts")).unwrap();
        assert!(sandbox.contains("defineSandbox("));
        assert!(sandbox.contains("from \"managed-deepagents\""));
        assert!(sandbox.contains("sandbox/setup.sh"));
        assert!(!dir.join("sandbox/setup.sh").exists());

        let manifest = fs::read_to_string(dir.join("package.json")).unwrap();
        assert!(manifest.contains("\"name\": \"myAgent\""));
        let manifest: serde_json::Value = serde_json::from_str(&manifest).unwrap();
        assert_eq!(
            manifest["dependencies"]["managed-deepagents"],
            crate::version::package_version()
        );
        assert_eq!(manifest["devDependencies"]["@types/node"], "^26.1.1");

        let tsconfig: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(dir.join("tsconfig.json")).unwrap()).unwrap();
        assert_eq!(tsconfig["compilerOptions"]["strict"], true);
        assert_eq!(tsconfig["compilerOptions"]["moduleResolution"], "Bundler");
        assert_eq!(tsconfig["compilerOptions"]["noEmit"], true);
        assert_eq!(tsconfig["include"], serde_json::json!(["**/*.ts"]));

        let readme = fs::read_to_string(dir.join("README.md")).unwrap();
        assert!(readme.contains("# myAgent"));
        assert!(readme.contains("npm install"));
        assert!(readme.contains("mda deploy --name myAgent-dev --agent-server-type serverless"));
        assert!(!readme.contains("--deployment-type serverless"));
        assert!(readme.contains("delete `sandbox/` to opt out"));
        assert!(readme.contains("Add `sandbox/setup.sh`"));
        assert!(readme.contains("sandbox/index.ts"));
        assert!(readme.contains("mda evals init -i\n"));
        assert!(readme.contains("evals/<task>/"));
        assert!(readme.contains("evals/<task>/identity.json"));
        assert!(readme.contains("non-empty `user.id`"));
        assert!(readme.contains("every Harbor job start"));
        assert!(readme.contains("PowerShell on native Windows"));
        assert!(!readme.contains("missing or stale eval artifact"));
        assert!(readme.contains("preserves your edits"));
        assert!(readme.contains(
            "uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor view .mda/evals/jobs"
        ));
        assert!(!readme.contains("mda evals compile"));
        assert!(!dir.join("evals").exists());
    }

    #[test]
    fn scaffold_creates_python_project() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let agent = fs::read_to_string(dir.join("agent.py")).unwrap();
        assert!(agent.contains("define_deep_agent"));
        assert!(agent.contains("name=\"myAgent\""));
        // Imports the published package, not the build-only vendored runtime
        // directory, so the scaffold runs as-is after `uv sync`.
        assert!(agent.contains("from managed_deepagents import"));
        assert!(!agent.contains("__runtime__"));
        assert!(!agent.contains("__MDA_AGENT_NAME__"));
        // The system prompt is managed: the scaffold never wires it manually.
        assert!(!agent.contains("system_prompt"));
        assert!(!agent.contains("instructions.md"));

        let sandbox = fs::read_to_string(dir.join("sandbox/__init__.py")).unwrap();
        // The template must preserve a real Python module docstring.
        assert!(
            sandbox.starts_with("\"\"\""),
            "sandbox/__init__.py must start with a triple-quoted docstring"
        );
        assert!(sandbox.contains("define_sandbox("));
        assert!(sandbox.contains("from managed_deepagents import"));
        assert!(sandbox.contains("sandbox/setup.sh"));
        assert!(!dir.join("sandbox/setup.sh").exists());

        let manifest = fs::read_to_string(dir.join("pyproject.toml")).unwrap();
        let parsed = manifest.parse::<toml_edit::DocumentMut>().unwrap();
        assert_eq!(
            parsed["tool"]["mda"]["python-version"].as_str(),
            Some("3.14")
        );
        assert!(manifest.contains("name = \"myAgent\""));
        assert!(manifest.contains(&format!(
            "managed-deepagents=={}",
            crate::version::package_version()
        )));

        let readme = fs::read_to_string(dir.join("README.md")).unwrap();
        assert!(readme.contains("# myAgent"));
        assert!(readme.contains("uv sync"));
        assert!(readme.contains("mda deploy --name myAgent-dev --agent-server-type serverless"));
        assert!(!readme.contains("--deployment-type serverless"));
        assert!(readme.contains("delete `sandbox/` to opt out"));
        assert!(readme.contains("Add `sandbox/setup.sh`"));
        assert!(readme.contains("sandbox/__init__.py"));
        assert!(readme.contains("mda evals init -i\n"));
        assert!(readme.contains("evals/<task>/"));
        assert!(readme.contains("evals/<task>/identity.json"));
        assert!(readme.contains("non-empty `user.id`"));
        assert!(readme.contains("every Harbor job start"));
        assert!(readme.contains("PowerShell on native Windows"));
        assert!(!readme.contains("missing or stale eval artifact"));
        assert!(readme.contains("preserves your edits"));
        assert!(readme.contains(
            "uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor view .mda/evals/jobs"
        ));
        assert!(!readme.contains("mda evals compile"));
        assert!(!dir.join("evals").exists());
    }

    #[test]
    fn scaffold_writes_agents_md_for_a_python_project() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let agents = fs::read_to_string(dir.join("AGENTS.md")).unwrap();

        assert!(agents.starts_with("# myAgent\n"));
        assert!(agents.contains("`agent.py` defines the agent"));
        assert!(agents.contains("- `uv sync` installs dependencies."));
        assert!(agents.contains(
            "Docs: https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"
        ));
        assert!(!agents.contains("__MDA_"));
    }

    #[test]
    fn scaffold_writes_agents_md_for_a_typescript_project() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::TypeScript), None).unwrap();

        let agents = fs::read_to_string(dir.join("AGENTS.md")).unwrap();

        assert!(agents.contains("`agent.ts` defines the agent"));
        assert!(agents.contains("- `npm install` installs dependencies."));
        assert!(agents.contains(
            "Docs: https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart"
        ));
        assert!(!agents.contains("__MDA_"));
    }

    #[test]
    fn scaffold_agents_md_points_to_mda_help_for_other_commands() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let agents = fs::read_to_string(dir.join("AGENTS.md")).unwrap();

        assert!(agents.contains("- `mda --help` lists the other commands"));
    }

    #[test]
    fn scaffold_agents_md_asks_before_deploying() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let agents = fs::read_to_string(dir.join("AGENTS.md")).unwrap();

        assert!(agents.contains("Don't run `mda deploy` unless the user asks you to."));
    }

    #[test]
    fn scaffold_readme_lists_agents_md() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let readme = fs::read_to_string(dir.join("README.md")).unwrap();

        assert!(readme.contains("AGENTS.md"));
    }

    #[test]
    fn scaffold_writes_documented_env_file() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(&dir, "myAgent", &blueprint(Language::TypeScript), None).unwrap();

        let env = fs::read_to_string(dir.join(".env")).unwrap();
        // Required key present but left empty when none is supplied.
        assert!(env.contains("LANGSMITH_API_KEY=\n"));
        // The default model's key is the one line left to fill in.
        assert!(env.contains("\nOPENAI_API_KEY=\n"));
        assert!(!env.contains("# OPENAI_API_KEY="));
        assert!(env.contains("# ANTHROPIC_API_KEY="));
    }

    #[test]
    fn scaffold_seeds_env_with_resolved_langsmith_key() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        scaffold(
            &dir,
            "myAgent",
            &blueprint(Language::Python),
            Some("ls-secret"),
        )
        .unwrap();

        let env = fs::read_to_string(dir.join(".env")).unwrap();
        assert!(env.contains("LANGSMITH_API_KEY=ls-secret\n"));
    }

    #[test]
    fn env_file_leaves_langsmith_blank_without_key() {
        let contents = env_file(None, "openai:gpt-6-luna", &[]);
        assert!(contents.contains("LANGSMITH_API_KEY=\n"));
        assert!(!contents.contains("LANGSMITH_API_KEY=ls-"));
    }

    #[test]
    fn env_file_uncomments_only_the_models_provider_key() {
        let contents = env_file(Some("ls-key"), "anthropic:claude-sonnet-5-5", &[]);
        let uncommented: Vec<_> = super::env::PROVIDER_API_KEYS
            .iter()
            .filter(|(key, _)| contents.contains(&format!("\n{key}=\n")))
            .map(|(key, _)| *key)
            .collect();
        assert_eq!(uncommented, ["ANTHROPIC_API_KEY"]);
    }

    #[test]
    fn env_file_uncomments_each_subagent_models_provider_key_once() {
        let contents = env_file(
            None,
            "openai:gpt-5.5",
            &[
                "anthropic:claude-sonnet-5-5",
                "openai:gpt-4.1",
                "anthropic:claude-haiku-5-5",
            ],
        );
        let uncommented: Vec<_> = super::env::PROVIDER_API_KEYS
            .iter()
            .filter(|(key, _)| contents.contains(&format!("\n{key}=\n")))
            .map(|(key, _)| *key)
            .collect();
        assert_eq!(uncommented, ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]);
        assert_eq!(contents.matches("ANTHROPIC_API_KEY").count(), 1);
        assert!(contents.contains("subagent's model (anthropic:claude-sonnet-5-5)"));
    }

    #[test]
    fn env_file_writes_a_key_outside_the_documented_providers() {
        let contents = env_file(None, "together:meta-llama/Llama-3-70b", &[]);
        assert!(contents.contains("\nTOGETHER_AI_API_KEY=\n"));
    }

    #[test]
    fn env_file_keeps_provider_keys_commented_for_gateway_models() {
        let contents = env_file(Some("ls-key"), "langsmith:openai/gpt-5.5", &[]);
        for (key, _) in super::env::PROVIDER_API_KEYS {
            assert!(contents.contains(&format!("# {key}=")));
        }
    }

    #[test]
    fn created_message_is_plain_when_colors_are_disabled() {
        assert_eq!(
            created_line_with_color("my-agent", false),
            "│  Created my-agent/\n│"
        );
    }

    #[test]
    fn handoff_continuation_is_a_single_bar_line() {
        assert_eq!(handoff_continuation_line_with_color(false), "│\n");
    }

    #[test]
    fn prompted_project_name_trims_a_valid_name() {
        let root = tempfile::tempdir().unwrap();

        let name = normalize_prompted_project_name(root.path(), "  my-agent  ").unwrap();

        assert_eq!(name, "my-agent");
    }

    #[test]
    fn prompted_project_name_rejects_an_invalid_agent_name() {
        let root = tempfile::tempdir().unwrap();

        let error = normalize_prompted_project_name(root.path(), "not/a/name").unwrap_err();

        assert!(error.to_string().contains("is not a valid agent name"));
    }

    #[test]
    fn prompted_project_name_rejects_an_existing_destination() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("my-agent")).unwrap();

        let error = normalize_prompted_project_name(root.path(), "my-agent").unwrap_err();

        assert!(error.to_string().contains("destination"));
    }

    #[test]
    fn submitted_name_prompt_keeps_the_label_and_value_on_one_line() {
        let theme = InlineNameTheme;
        let mut value = cliclack::StringCursor::default();
        for character in "my-agent".chars() {
            value.insert(character);
        }
        let state = cliclack::ThemeState::Submit;

        let frame = format!(
            "{}{}{}",
            cliclack::Theme::format_header(&theme, &state, "Name your agent"),
            cliclack::Theme::format_input(&theme, &state, &value),
            cliclack::Theme::format_footer_with_message(&theme, &state, ""),
        );

        assert_eq!(
            console::strip_ansi_codes(&frame),
            "◇  Name your agent: my-agent\n"
        );
    }

    #[test]
    fn init_errors_when_destination_exists() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("myAgent")).unwrap();

        let err = init(root.path(), &blueprint(Language::TypeScript)).unwrap_err();
        assert!(err.to_string().contains("already exists"));
    }

    #[test]
    fn a_destination_that_exists_is_refused_with_a_way_forward() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        fs::create_dir(&dir).unwrap();
        fs::write(dir.join("agent.ts"), "export const agent = 1;").unwrap();

        let err = init(root.path(), &blueprint(Language::TypeScript)).unwrap_err();

        let message = err.to_string();
        assert!(
            message.contains("remove it") && message.contains("another name"),
            "the error must name a usable next action: {message}"
        );
    }

    #[test]
    fn init_errors_on_empty_name() {
        let root = tempfile::tempdir().unwrap();
        let err = init(root.path(), &Blueprint::new("  ", Language::Python)).unwrap_err();
        assert!(err.to_string().contains("must not be empty"));
    }

    #[test]
    fn init_rejects_invalid_agent_names_before_creating_anything() {
        let root = tempfile::tempdir().unwrap();

        let err = init(root.path(), &Blueprint::new("1bad", Language::TypeScript)).unwrap_err();

        assert!(
            err.to_string().contains("not a valid agent name"),
            "unexpected error: {err}"
        );
        assert!(
            !root.path().join("1bad").exists(),
            "a rejected name must not leave a directory behind"
        );
    }

    #[test]
    fn scaffold_rejects_invalid_agent_name() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("1bad");
        let err = scaffold(
            &dir,
            "1bad",
            &Blueprint::new("1bad", Language::TypeScript),
            None,
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("not a valid agent name"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn scaffold_writes_skills_and_refuses_paths_outside_them() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        let skill = |path: &str| {
            BTreeMap::from([(
                "triage".to_string(),
                BTreeMap::from([(path.to_string(), "# Triage".to_string())]),
            )])
        };

        let requested = Blueprint {
            skills: skill("refs/SKILL.md"),
            ..blueprint(Language::Python)
        };
        scaffold(&dir, "myAgent", &requested, None).unwrap();
        assert_eq!(
            fs::read_to_string(dir.join("skills/triage/refs/SKILL.md")).unwrap(),
            "# Triage"
        );

        let escaping = Blueprint {
            skills: skill("../../agent.py"),
            ..blueprint(Language::Python)
        };
        let other = root.path().join("other");
        assert!(scaffold(&other, "other", &escaping, None).is_err());
        assert!(!other.join("agent.py").exists());
    }

    #[test]
    fn scaffold_writes_the_requested_instructions() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        let requested = Blueprint {
            instructions: Some("  You triage inbound support tickets.  ".to_string()),
            ..blueprint(Language::TypeScript)
        };

        scaffold(&dir, "myAgent", &requested, None).unwrap();

        let instructions = fs::read_to_string(dir.join("instructions.md")).unwrap();
        assert_eq!(instructions, "  You triage inbound support tickets.\n");
    }

    #[test]
    fn identity_scaffolds_langsmith_api_key_auth_for_typescript() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");

        scaffold(&dir, "myAgent", &blueprint(Language::TypeScript), None).unwrap();

        let identity = fs::read_to_string(dir.join("identity.ts")).unwrap();
        assert!(identity.contains("auth.langsmithApiKey()"), "{identity}");
        assert!(!identity.contains("scope:"));
    }

    #[test]
    fn identity_scaffolds_langsmith_api_key_auth_for_python() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");

        scaffold(&dir, "myAgent", &blueprint(Language::Python), None).unwrap();

        let identity = fs::read_to_string(dir.join("identity.py")).unwrap();
        assert!(identity.contains("auth.langsmith_api_key()"), "{identity}");
        assert!(!identity.contains("scope="));
        assert!(fs::read_to_string(dir.join("README.md"))
            .unwrap()
            .contains("managed authentication"));
    }

    #[test]
    fn default_scaffolded_identity_does_not_need_the_ingress_secret() {
        for language in [Language::TypeScript, Language::Python] {
            let root = tempfile::tempdir().unwrap();
            let dir = root.path().join("myAgent");
            scaffold(&dir, "myAgent", &blueprint(language), None).unwrap();

            let entry = match language {
                Language::TypeScript => "identity.ts",
                Language::Python => "identity.py",
            };
            let project = crate::project::Project {
                root: dir,
                language,
                agent_file: PathBuf::from(entry),
                files: vec![PathBuf::from(entry)],
            };
            assert!(
                !crate::compile::identity_requires_ingress_secret(&project).unwrap(),
                "{language:?} identity should use LangSmith API-key auth"
            );
        }
    }

    #[test]
    fn scaffold_includes_identity_by_default() {
        for language in [Language::TypeScript, Language::Python] {
            let root = tempfile::tempdir().unwrap();
            let dir = root.path().join("myAgent");

            scaffold(&dir, "myAgent", &blueprint(language), None).unwrap();

            let identity = match language {
                Language::TypeScript => "identity.ts",
                Language::Python => "identity.py",
            };
            assert!(dir.join(identity).is_file(), "missing {identity}");
        }
    }

    #[test]
    fn scaffold_defines_the_agent_with_the_requested_model() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        let requested = Blueprint {
            model: Some("anthropic:claude-sonnet-4-5".to_string()),
            ..blueprint(Language::Python)
        };

        scaffold(&dir, "myAgent", &requested, None).unwrap();

        let agent = fs::read_to_string(dir.join("agent.py")).unwrap();
        assert!(agent.contains(r#"model="anthropic:claude-sonnet-4-5""#));
        assert!(!agent.contains("__MDA_MODEL__"));
    }

    #[test]
    fn scaffold_rejects_a_model_that_would_break_the_agent_file() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        let requested = Blueprint {
            model: Some(r#"openai:gpt", tools: []"#.to_string()),
            ..blueprint(Language::TypeScript)
        };

        let err = scaffold(&dir, "myAgent", &requested, None).unwrap_err();

        assert!(err.to_string().contains("cannot be written"));
        assert!(
            !dir.exists(),
            "a rejected model must not leave files behind"
        );
    }

    #[test]
    fn scaffold_leaves_out_the_sandbox_and_evals_by_default() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("myAgent");
        let requested = Blueprint {
            sandbox: false,
            ..blueprint(Language::TypeScript)
        };

        let summary = scaffold(&dir, "myAgent", &requested, None).unwrap();

        assert!(!dir.join("sandbox").exists());
        assert!(!dir.join("evals").exists());
        assert!(summary.files.iter().all(|file| {
            let file = file.strip_prefix(&dir).unwrap();
            !file.starts_with("sandbox") && !file.starts_with("evals")
        }));
        let readme = fs::read_to_string(dir.join("README.md")).unwrap();
        assert!(readme.contains("This project has no sandbox"));
        assert!(readme.contains("initialize the Harbor eval workspace"));
        assert!(!readme.contains("delete `sandbox/` to opt out"));
    }

    #[test]
    fn project_name_uses_final_path_component() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("./hahah");

        assert_eq!(project_name_from_path(&dir, "./hahah").unwrap(), "hahah");
    }

    #[test]
    fn init_without_channels_does_not_create_a_channels_directory() {
        let root = tempfile::tempdir().unwrap();
        let summary = init(root.path(), &blueprint(Language::TypeScript)).unwrap();

        assert!(!summary.root.join("channels").exists());
        assert!(summary
            .files
            .iter()
            .all(|file| !file.ends_with("channels/slack.ts")));
    }

    #[test]
    fn init_with_slack_channel_writes_the_python_channel_file() {
        let root = tempfile::tempdir().unwrap();
        let requested = Blueprint {
            channels: vec![InitChannel::Slack(Default::default())],
            ..blueprint(Language::Python)
        };

        let summary = init(root.path(), &requested).unwrap();
        let contents = fs::read_to_string(summary.root.join("channels/slack.py")).unwrap();

        assert!(summary
            .files
            .iter()
            .any(|file| file.ends_with("channels/slack.py")));
        assert!(contents.contains("channel = channels.slack()"));
        assert!(fs::read_to_string(summary.root.join("README.md"))
            .unwrap()
            .contains("channels/slack.py"));
    }

    #[test]
    fn init_with_slack_channel_writes_the_typescript_channel_file() {
        let root = tempfile::tempdir().unwrap();
        let requested = Blueprint {
            channels: vec![InitChannel::Slack(Default::default())],
            ..blueprint(Language::TypeScript)
        };

        let summary = init(root.path(), &requested).unwrap();
        let contents = fs::read_to_string(summary.root.join("channels/slack.ts")).unwrap();

        assert!(contents.contains("export const channel = channels.slack({});"));
    }

    #[test]
    fn init_with_github_channel_writes_the_channel_file_and_readme_entry() {
        let root = tempfile::tempdir().unwrap();
        let requested = Blueprint {
            channels: vec![InitChannel::GitHub],
            ..blueprint(Language::TypeScript)
        };

        let summary = init(root.path(), &requested).unwrap();
        let contents = fs::read_to_string(summary.root.join("channels/github.ts")).unwrap();
        let readme = fs::read_to_string(summary.root.join("README.md")).unwrap();

        assert!(contents.contains("export const channel = channels.github({"));
        assert!(summary
            .files
            .iter()
            .any(|file| file.ends_with("channels/github.ts")));
        assert!(readme.contains("channels/github.ts"), "{readme}");
    }

    #[test]
    fn init_dedupes_a_repeated_slack_channel() {
        let root = tempfile::tempdir().unwrap();
        let requested = Blueprint {
            channels: vec![
                InitChannel::Slack(Default::default()),
                InitChannel::Slack(Default::default()),
            ],
            ..blueprint(Language::Python)
        };

        let summary = init(root.path(), &requested).unwrap();

        assert_eq!(
            summary
                .files
                .iter()
                .filter(|file| file.ends_with("channels/slack.py"))
                .count(),
            1
        );
    }
}
