//! Command-line parsing and dispatch for the `mda` binary.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use crate::channel;
use crate::chat::{self, ChatOptions};
use crate::command::CommandCompletion;
use crate::connection::{self, ConnectionTarget};
use crate::delete::{self, DeleteOptions};
use crate::deploy::{self, AgentEnvironment, ContextStrategy, DeployOptions, DeploymentType};
use crate::dev::{self, DevOptions};
use crate::error::{categorized, category_of, is_cancellation, ErrorCategory};
use crate::eval::{self as eval_cmd, CompileOptions, InitOptions};
use crate::install::{Installation, INSTALL_CHANNEL_ENV};
use crate::langsmith::{self, LogLevel};
use crate::logs::{self, LogsOptions};
use crate::secret_input::ValueSource;
use crate::telemetry::{
    deliver_at_exit, hold_for_exit, init_facts, is_telemetry_disabled, otel_logger,
    posthog_transport, report_panics, OtelLogger, ReportableFacts,
};
use crate::{coding_agent, compile, init, styled_help_template, termination, version};
use anyhow::{Context, Result};
use clap::{Arg, ArgAction, Args, CommandFactory, FromArgMatches, Parser, Subcommand};

/// Examples prepended to the language-specific docs in root `--help`.
// Note: no `\` line continuation on the first line. That escape also eats the
// leading whitespace, which would drop the first line's indent.
const ROOT_HELP_EXAMPLES: &str = "  mda init research-assistant
  mda evals init
  mda dev
  mda chat
  mda deploy
  mda connections list
  mda channels init slack
  mda logs
  mda delete";

/// Survey of the connections subcommands. Create modes live on `create --help`.
const CONNECTIONS_HELP_EXAMPLES: &str = "  # OAuth services `--oauth` recognizes
  mda connections catalog

  # General OAuth from the catalog
  mda connections create github --oauth github --client-id CLIENT_ID

  # Connections this project's deployment uses
  mda connections list
  mda connections list --json

  # One connection's metadata
  mda connections get github

  # Delete a connection
  mda connections delete github
  mda connections delete github --yes";

const CONNECTION_CREATE_LONG_ABOUT: &str = "Create a connection in one of three modes:

  Opaque secret
    Store a fixed secret for the agent. Read it from an environment variable,
    a file, or stdin.

  General OAuth
    Register a BYOT OAuth app from the built-in service catalog, or provide
    custom authorization and token endpoints.

  MCP OAuth
    With only a slug, discover the MCP server URL from a unique user-owned
    connection in the project's connector declarations. Use --mcp to supply
    the server URL explicitly. Add --authorize to create an agent grant.";

const CONNECTION_CREATE_HELP_EXAMPLES: &str = "  # Opaque secret
  mda connections create acme-api --secret-from-env ACME_API_KEY
  printf '%s' \"$ACME_API_KEY\" | mda connections create acme-api

  # General OAuth from the catalog
  mda connections create google --oauth google --client-id CLIENT_ID

  # General OAuth with custom endpoints
  mda connections create acme --authorize-url https://auth.acme.com/authorize --token-url https://auth.acme.com/token --client-id CLIENT_ID --scope read

  # MCP OAuth discovered from this project's connector declarations
  mda connections create my-notion --authorize

  # MCP OAuth from an explicit server URL
  mda connections create my-notion --mcp https://mcp.notion.com/mcp --authorize

  # Authorize a deployed agent without a local project
  mda connections create my-notion --mcp https://mcp.notion.com/mcp --authorize --workspace-id WORKSPACE_ID --deployment-id DEPLOYMENT_ID";

const PYTHON_DOCS_AFTER_HELP: &str =
    "  https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart";

const JAVASCRIPT_DOCS_AFTER_HELP: &str =
    "  https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart";

const OPAQUE_SECRET_HEADING: &str = "Opaque secret";
const OAUTH_HEADING: &str = "General OAuth";
const MCP_OAUTH_HEADING: &str = "MCP OAuth";

/// Help footer when the install channel is unknown: both quickstarts.
const DOCS_AFTER_HELP: &str =
    "  https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart
  https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart";

#[derive(Parser)]
#[command(
    name = "mda",
    about = "Compile and deploy a code-first Deep Agent to a managed LangGraph runtime on LangSmith."
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Scaffold a new Deep Agent project.
    ///
    /// The install channel selects the project language: TypeScript (npm) or Python (PyPI).
    Init(InitArgs),
    /// Compile a Deep Agent project into a managed LangGraph app (no deploy).
    Build(BuildArgs),
    /// Compile a Deep Agent project and run it on the local LangGraph dev server.
    Dev(DevArgs),
    /// Chat with a Deep Agent from the terminal (starts a local dev server when none is up).
    Chat(ChatArgs),
    /// Build and deploy a Deep Agent project to LangSmith.
    Deploy(DeployArgs),
    /// Prepare a checked-out Deep Agent project for a LangSmith build job
    /// (Context Hub sync, compile, sandbox bake). Run by the build runner.
    #[command(hide = true)]
    Prepare(PrepareArgs),
    /// Manage workspace connections (opaque secrets and OAuth slots).
    #[command(name = "connections", alias = "connection")]
    Connection(ConnectionArgs),
    /// Initialize an external messaging channel in the current project.
    #[command(name = "channels", alias = "channel")]
    Channel(ChannelArgs),
    /// Tail the Agent Server logs of a deployed Deep Agent.
    Logs(LogsArgs),
    /// Delete a deployed Deep Agent and the LangSmith resources it created.
    #[command(visible_alias = "destroy")]
    Delete(DeleteArgs),
    /// Initialize evaluations for a Deep Agent project.
    #[command(name = "evals", alias = "eval")]
    Eval(EvalArgs),
}

#[derive(Args)]
struct InitArgs {
    /// Name of the new project directory. Prompts for the project name when omitted.
    name: Option<String>,

    /// Offer a coding-agent handoff after scaffolding.
    #[arg(short = 'i', long = "interactive")]
    coding_agent: bool,

    /// System prompt to write into instructions.md.
    #[arg(long, value_name = "TEXT", conflicts_with = "instructions_file")]
    instructions: Option<String>,

    /// Read the system prompt for instructions.md from a file, or from stdin
    /// when given "-".
    #[arg(long, value_name = "PATH")]
    instructions_file: Option<PathBuf>,

    /// Durable memory to declare. Omitted means no memory (the default).
    #[arg(long, value_enum, value_name = "SCOPE")]
    memory: Option<init::MemoryChoice>,

    /// Model the agent runs on, as provider:model (for example openai:gpt-5.6-terra).
    #[arg(long, value_name = "SPEC")]
    model: Option<String>,

    /// Leave out the managed sandbox declaration.
    #[arg(long)]
    no_sandbox: bool,

    /// Messaging channel to scaffold into the new project. Repeatable.
    #[arg(
        short = 'c',
        long = "channel",
        visible_alias = "channels",
        value_enum,
        value_name = "CHANNEL",
        num_args = 1,
        action = ArgAction::Append
    )]
    channels: Vec<init::InitChannel>,
}

#[derive(Args)]
struct BuildArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Output directory for the compiled app (defaults to <path>/.mda/build).
    ///
    /// The directory is emptied before the build, so it must be missing, empty,
    /// or a directory a previous build wrote.
    #[arg(long)]
    out: Option<PathBuf>,
}

#[derive(Args)]
struct DevArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Port the dev server binds to (defaults to the LangGraph CLI default).
    #[arg(short = 'p', long)]
    port: Option<u16>,

    /// Hostname the dev server binds to (defaults to the LangGraph CLI default).
    #[arg(long)]
    hostname: Option<String>,

    /// Prevent the dev server from opening Studio in a browser when it starts.
    #[arg(long)]
    no_browser: bool,

    /// Disable the dev server's hot reload.
    #[arg(long)]
    no_reload: bool,

    /// Expose the dev server through a public Cloudflare tunnel.
    ///
    /// Needed by browsers that block requests from the HTTPS Studio origin to
    /// localhost (Safari, Brave).
    #[arg(long)]
    tunnel: bool,

    /// Do not provision `schedules/` on the local server, so they never fire
    /// while you iterate.
    #[arg(long)]
    no_schedules: bool,
}

#[derive(Args)]
struct ChatArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Agent Server URL to talk to, instead of the default local one.
    ///
    /// Must be a local dev server, or a tunnel to one: chat sends no
    /// credentials, so a deployed agent rejects it. Without this flag, chat
    /// reuses a local dev server that is already up and otherwise starts one
    /// for the session.
    #[arg(long)]
    url: Option<String>,

    /// Assistant id (defaults to the one `mda dev` staged, then the agent `name`).
    #[arg(long)]
    assistant: Option<String>,

    /// Continue an existing thread instead of starting a new one.
    #[arg(short = 't', long)]
    thread: Option<String>,
}

#[derive(Args)]
struct DeployArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Deployment name (defaults to the static agent `name`, or `agent` for a dynamic entry).
    #[arg(short = 'n', long)]
    name: Option<String>,

    /// Agent environment to target; the deployment name is used as the agent ID
    /// (requires agent mode enabled for the workspace).
    #[arg(long, value_enum)]
    agent_environment: Option<AgentEnvironment>,

    /// Deployment type when creating.
    #[arg(long, value_enum, default_value_t = DeploymentType::Dev)]
    deployment_type: DeploymentType,

    /// Workspace ID to deploy into (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    workspace_id: Option<String>,

    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    host_url: Option<String>,

    /// Build, push, and deploy through a customer-managed container registry.
    #[arg(long, value_name = "REPOSITORY[:TAG]")]
    push_to: Option<String>,

    /// Trigger the build but do not wait for it to finish.
    #[arg(long)]
    no_wait: bool,

    /// Resolve instructions/skills edited in Context Hub without prompting.
    #[arg(long, value_enum, value_name = "STRATEGY")]
    context_strategy: Option<ContextStrategy>,

    /// Overwrite existing same-name resources on a first deploy without asking.
    ///
    /// Does not resolve Context Hub edits; use --context-strategy for those.
    #[arg(short = 'y', long)]
    yes: bool,

    /// Maximum seconds to wait for the deployment to finish.
    #[arg(long, default_value_t = 1800, value_parser = clap::value_parser!(u64).range(1..=3600))]
    wait_timeout_seconds: u64,
}

#[derive(Args)]
struct PrepareArgs {
    /// Path to the Deep Agent project inside the checkout.
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Output directory for the compiled app (defaults to <path>/.mda/build).
    #[arg(long)]
    out: Option<PathBuf>,

    /// Deployment this build belongs to.
    #[arg(long, value_name = "UUID")]
    deployment_id: String,

    /// Revision this build belongs to.
    #[arg(long, value_name = "UUID")]
    revision_id: String,

    /// JSON object file holding the deployment's secrets.
    #[arg(long, value_name = "PATH")]
    env_json: Option<PathBuf>,

    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    host_url: Option<String>,

    /// Prepare a preview deployment: never reconcile schedules or channels.
    #[arg(long)]
    preview: bool,

    /// Deployment name whose sandbox snapshots this build shares.
    #[arg(long, value_name = "NAME")]
    snapshot_owner: Option<String>,
}

#[derive(Args)]
struct ConnectionArgs {
    #[command(subcommand)]
    command: ConnectionCommand,
}

#[derive(Subcommand)]
enum ConnectionCommand {
    /// Create an opaque secret, general OAuth, or MCP OAuth connection.
    #[command(long_about = CONNECTION_CREATE_LONG_ABOUT)]
    Create(Box<ConnectionCreateArgs>),
    /// List the connections in the workspace.
    List(ConnectionListArgs),
    /// Show one connection's metadata.
    Get(ConnectionGetArgs),
    /// Delete a connection.
    Delete(ConnectionDeleteArgs),
    /// List the OAuth services `--oauth` recognizes.
    Catalog(ConnectionCatalogArgs),
}

/// Deliberately carries no workspace or host flags: the catalog is compiled in.
#[derive(Args)]
struct ConnectionCatalogArgs {
    /// Print JSON instead of a table.
    #[arg(long)]
    json: bool,
}

/// Flattened into each verb. The connection slug is the only positional here,
/// so the project is named with a flag alongside the workspace.
#[derive(Args)]
struct ConnectionCommonArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(long = "project", value_name = "PATH", default_value = ".")]
    path: PathBuf,

    /// Workspace ID (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    workspace_id: Option<String>,

    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    host_url: Option<String>,
}

#[derive(Args)]
struct ConnectionValueArgs {
    /// Name of an environment variable holding the value (shell or .env).
    #[arg(
        long,
        value_name = "VAR",
        conflicts_with = "secret_from_file",
        help_heading = OPAQUE_SECRET_HEADING
    )]
    secret_from_env: Option<String>,

    /// File holding the value. Redirect or pipe to read stdin instead.
    #[arg(long, value_name = "PATH", help_heading = OPAQUE_SECRET_HEADING)]
    secret_from_file: Option<PathBuf>,
}

#[derive(Args)]
struct ConnectionOAuthCreateArgs {
    /// Catalog OAuth service. See `mda connections catalog`.
    #[arg(long, value_name = "SERVICE", help_heading = OAUTH_HEADING)]
    oauth: Option<String>,

    /// OAuth grant type. Defaults to authorization_code.
    #[arg(
        long,
        value_name = "GRANT",
        value_parser = ["authorization_code", "client_credentials"],
        help_heading = OAUTH_HEADING
    )]
    grant_type: Option<String>,

    /// BYOT client id.
    #[arg(long, help_heading = OAUTH_HEADING)]
    client_id: Option<String>,

    /// How the client authenticates to the token endpoint. Defaults to the
    /// catalog service's method, or client_secret_basic.
    #[arg(
        long,
        value_name = "METHOD",
        value_parser = ["client_secret_basic", "client_secret_post", "none"],
        help_heading = OAUTH_HEADING
    )]
    auth_method: Option<String>,

    /// OAuth scope to request (repeatable). Replaces the catalog defaults.
    #[arg(long = "scope", value_name = "SCOPE", help_heading = OAUTH_HEADING)]
    scopes: Vec<String>,

    /// The most any authorization flow may request (repeatable). Defaults to
    /// the --scope values.
    #[arg(long = "allowed-scope", value_name = "SCOPE", help_heading = OAUTH_HEADING)]
    allowed_scopes: Vec<String>,

    /// Extra authorization query parameter, as KEY=VALUE (repeatable).
    #[arg(
        long = "authorization-param",
        value_name = "KEY=VALUE",
        help_heading = OAUTH_HEADING
    )]
    authorization_params: Vec<String>,

    /// Extra token request parameter, as KEY=VALUE (repeatable).
    #[arg(
        long = "token-param",
        value_name = "KEY=VALUE",
        help_heading = OAUTH_HEADING
    )]
    token_params: Vec<String>,

    /// Extra token request header, as KEY=VALUE (repeatable). Sent to the
    /// token and revocation endpoints; values are stored encrypted and never
    /// returned.
    #[arg(
        long = "token-request-header",
        value_name = "KEY=VALUE",
        help_heading = OAUTH_HEADING
    )]
    token_request_headers: Vec<String>,

    /// OAuth authorization endpoint URL (manual provider registration).
    #[arg(long, value_name = "URL", help_heading = OAUTH_HEADING)]
    authorize_url: Option<String>,

    /// OAuth token endpoint URL (manual provider registration).
    #[arg(long, value_name = "URL", help_heading = OAUTH_HEADING)]
    token_url: Option<String>,
}

#[derive(Args)]
struct ConnectionCreateArgs {
    /// Name for the new connection.
    slug: String,

    #[command(flatten)]
    common: ConnectionCommonArgs,

    #[command(flatten)]
    value: ConnectionValueArgs,

    #[command(flatten)]
    oauth: ConnectionOAuthCreateArgs,

    /// Sign in to the account that the deployed agent will use for this connection.
    #[arg(long, help_heading = OAUTH_HEADING)]
    authorize: bool,

    /// Deployment ID for agent-owned secrets or OAuth grants, without a local project.
    #[arg(long, value_name = "ID")]
    deployment_id: Option<String>,

    /// MCP server URL. Discovers OAuth and registers a client automatically.
    #[arg(
        long,
        value_name = "URL",
        conflicts_with_all = [
            "oauth",
            "grant_type",
            "client_id",
            "auth_method",
            "scopes",
            "allowed_scopes",
            "authorization_params",
            "token_params",
            "token_request_headers",
            "authorize_url",
            "token_url",
            "secret_from_env",
            "secret_from_file",
        ],
        help_heading = MCP_OAUTH_HEADING
    )]
    mcp: Option<String>,
}

#[derive(Args)]
struct ConnectionListArgs {
    #[command(flatten)]
    common: ConnectionCommonArgs,

    /// Print JSON instead of a table.
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
struct ConnectionGetArgs {
    /// Name of the connection.
    slug: String,

    #[command(flatten)]
    common: ConnectionCommonArgs,

    /// Print JSON instead of a table.
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
struct ConnectionDeleteArgs {
    /// Name of the connection.
    slug: String,

    #[command(flatten)]
    common: ConnectionCommonArgs,

    /// Delete without asking for confirmation.
    #[arg(short = 'y', long)]
    yes: bool,
}

#[derive(Args)]
struct ChannelArgs {
    #[command(subcommand)]
    command: ChannelCommand,
}

#[derive(Subcommand)]
enum ChannelCommand {
    /// Initialize a channel in the current project.
    Init(ChannelInitArgs),
}

#[derive(Args)]
struct ChannelInitArgs {
    #[command(subcommand)]
    channel: ChannelInitCommand,
}

#[derive(Subcommand)]
enum ChannelInitCommand {
    /// Create a Slack channel definition.
    Slack,
    /// Create a GitHub channel definition.
    #[command(name = "github")]
    GitHub,
    /// Create a Linear channel definition.
    Linear,
}

#[derive(Args)]
struct LogsArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Deployment name (defaults to the agent `name` from the project).
    #[arg(short = 'n', long)]
    name: Option<String>,

    /// How many recent log lines to fetch.
    #[arg(short = 'l', long, default_value_t = 1000, value_parser = clap::value_parser!(u32).range(1..=10_000))]
    lines: u32,

    /// Only show entries at or above this severity.
    #[arg(long, value_enum)]
    level: Option<LogLevel>,

    /// Keep streaming new logs. This is the default in an interactive terminal.
    #[arg(short = 'f', long)]
    follow: bool,

    /// Print the recent logs and exit. This is the default when output is piped.
    #[arg(long, conflicts_with = "follow")]
    no_follow: bool,

    /// Workspace ID to read from (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    workspace_id: Option<String>,

    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    host_url: Option<String>,
}

#[derive(Args)]
struct DeleteArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Deployment name (defaults to the agent `name` in the agent file).
    #[arg(short = 'n', long)]
    name: Option<String>,

    /// Agent environment to target; the deployment name is used as the agent ID
    /// (requires agent mode enabled for the workspace).
    #[arg(long, value_enum)]
    agent_environment: Option<AgentEnvironment>,

    /// Workspace ID the deployment lives in (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    workspace_id: Option<String>,

    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    host_url: Option<String>,

    /// Delete without asking for confirmation.
    #[arg(short = 'y', long)]
    yes: bool,
}

#[derive(Args)]
struct EvalArgs {
    #[command(subcommand)]
    command: EvalCommand,
}

#[derive(Subcommand)]
enum EvalCommand {
    /// Initialize evaluations for a Deep Agent project.
    Init(EvalInitArgs),
    /// Internal entrypoint used by the MDA Harbor JobPlugin.
    #[command(hide = true)]
    Compile(EvalCompileArgs),
}

#[derive(Args)]
struct EvalInitArgs {
    /// Offer a coding-agent handoff after initializing the eval workspace.
    #[arg(short = 'i', long = "interactive")]
    coding_agent: bool,
}

#[derive(Args)]
struct EvalCompileArgs {
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(default_value = ".")]
    path: PathBuf,

    /// Model override for this eval artifact.
    #[arg(long = "model")]
    model: Option<String>,

    /// Compile into this job-scoped artifact UUID (Harbor plugin use only).
    #[arg(long, hide = true)]
    artifact_id: Option<String>,
}

fn docs_after_help(install_channel: Option<&str>) -> &'static str {
    match install_channel {
        Some("npm") => JAVASCRIPT_DOCS_AFTER_HELP,
        Some("pypi") => PYTHON_DOCS_AFTER_HELP,
        _ => DOCS_AFTER_HELP,
    }
}

fn cli_command(install_channel: Option<&str>) -> clap::Command {
    let docs = docs_after_help(install_channel);
    Cli::command()
        .help_template(styled_help_template(Some(ROOT_HELP_EXAMPLES), docs))
        .mut_subcommands(|command| {
            let examples =
                (command.get_name() == "connections").then_some(CONNECTIONS_HELP_EXAMPLES);
            let command = command.help_template(styled_help_template(examples, docs));
            if command.get_name() == "connections" {
                command.mut_subcommands(|subcommand| {
                    if subcommand.get_name() == "create" {
                        subcommand.help_template(styled_help_template(
                            Some(CONNECTION_CREATE_HELP_EXAMPLES),
                            docs,
                        ))
                    } else {
                        subcommand
                    }
                })
            } else {
                command
            }
        })
}

#[expect(clippy::print_stderr, reason = "top-level command error boundary")]
pub fn run() -> ExitCode {
    // Capture the runner-only credential while startup is still single-threaded.
    // Project code, package installers, and compiler subprocesses must not inherit it.
    let build_artifacts_token = std::env::var_os(langsmith::BUILD_ARTIFACTS_TOKEN_ENV);
    std::env::remove_var(langsmith::BUILD_ARTIFACTS_TOKEN_ENV);

    // Before any child is spawned, so a SIGTERM or SIGHUP never strands one.
    if let Err(err) = termination::install() {
        eprintln!("warning: {err:#}");
    }

    // Report the bundled runtime version (see `crate::version`) rather than the
    // crate version clap would use by default. The version is resolved at
    // runtime, so it is set on the built command instead of via the derive
    // `version` attribute. Leaking is fine: it lives for the whole process and
    // clap wants a `'static` string.
    //
    // Replace clap's default `-V` with `-v` (keeping `-V` as a visible alias)
    // so `mda -v` matches common user muscle memory for version.
    let version: &'static str = Box::leak(version::version().into_boxed_str());
    let install_channel = std::env::var_os(INSTALL_CHANNEL_ENV);
    let command = cli_command(install_channel.as_deref().and_then(std::ffi::OsStr::to_str))
        .version(version)
        .disable_version_flag(true)
        .arg(
            Arg::new("version")
                .short('v')
                .visible_short_alias('V')
                .long("version")
                .action(ArgAction::Version)
                .help("Print version"),
        );
    let matches = command.clone().get_matches();
    let cli = match Cli::from_arg_matches(&matches) {
        Ok(cli) => cli,
        Err(err) => err.exit(),
    };

    let telemetry_enabled = !is_telemetry_disabled(|key| std::env::var(key).ok());
    let posthog = telemetry_enabled.then(posthog_transport).flatten();
    let otel_logger = telemetry_enabled.then(OtelLogger::install).flatten();
    if telemetry_enabled {
        hold_for_exit(otel_logger, posthog);
        report_panics();
        let (facts, client) = ReportableFacts::detect(&command, &matches);
        init_facts(facts);
        if let Some(client) = &client {
            otel_logger::authenticate(client);
        }
    }
    let result = match cli.command {
        Command::Init(args) => run_init(args),
        Command::Build(args) => run_build(args),
        Command::Dev(args) => run_dev(args),
        Command::Chat(args) => run_chat(args),
        Command::Deploy(args) => run_deploy(args),
        Command::Prepare(args) => run_prepare(args, build_artifacts_token),
        Command::Connection(args) => run_connection(args),
        Command::Channel(args) => run_channel(args),
        Command::Logs(args) => run_logs(args),
        Command::Delete(args) => run_delete(args),
        Command::Eval(args) => run_eval(args),
    };
    termination::hand_over_if_terminating();
    let completion = match result {
        Ok(completion) => completion,
        Err(err) if is_cancellation(&err) => CommandCompletion::Cancelled,
        // A command that did not categorize its own failure lands here, and
        // reports `unclassified` unless the cause is one every command shares.
        Err(err) => {
            eprintln!("error: {err:#}");
            otel_logger::error(&err);
            CommandCompletion::Failed(category_of(&err))
        }
    };
    deliver_at_exit(completion);
    match completion {
        CommandCompletion::Failed(_) => ExitCode::FAILURE,
        CommandCompletion::Succeeded | CommandCompletion::Cancelled => ExitCode::SUCCESS,
    }
}

fn run_init(args: InitArgs) -> Result<CommandCompletion> {
    let installation = Installation::from_env()?;
    let cwd = std::env::current_dir()?;
    let language = installation.language();
    let coding_agent_handoff = args.coding_agent;
    let prompts_for_name = args.name.is_none();
    if let Some(name) = args.name.as_deref() {
        init::validate_project_target(&cwd, name)?;
    }
    if coding_agent_handoff {
        coding_agent::require_terminal()?;
    }
    if prompts_for_name && !coding_agent::has_terminal() {
        return Err(categorized(
            ErrorCategory::UserInput,
            "project name is required without a terminal; run `mda init <name>` instead",
        ));
    }
    let name = match args.name {
        Some(name) => name,
        None => {
            init::begin()?;
            let Some(name) = init::prompt_project_name(&cwd)? else {
                return Ok(CommandCompletion::Cancelled);
            };
            name
        }
    };
    let blueprint = init::Blueprint {
        instructions: instructions(args.instructions, args.instructions_file.as_deref())?,
        memory: args.memory,
        model: args.model,
        sandbox: !args.no_sandbox,
        channels: args.channels,
        ..init::Blueprint::new(name, language)
    };
    if !prompts_for_name {
        init::begin()?;
    }
    let summary = init::init(&cwd, &blueprint)?;
    if coding_agent_handoff {
        init::show_interactive_created(&summary.root)?;
        let outcome = init::offer_handoff(&summary.root, init::coding_agent_prompt())?;
        if matches!(outcome, init::HandoffOutcome::PromptShown) {
            init::show_handoff_continuation()?;
        }
        if should_show_deploy_steps(outcome) {
            init::show_deploy_steps(&summary.root, language)?;
        }
    } else {
        init::show_non_interactive_next_steps(&summary.root, language)?;
    }
    Ok(CommandCompletion::Succeeded)
}

fn should_show_deploy_steps(outcome: init::HandoffOutcome) -> bool {
    matches!(
        outcome,
        init::HandoffOutcome::PromptShown | init::HandoffOutcome::Cancelled
    )
}

/// The system prompt for a new project, whether it was typed at the shell, read
/// out of a file, or piped in.
///
/// A prompt long enough to be worth writing is long enough to be awkward to
/// quote, so the file form exists for anything past a sentence, and `-` reads
/// stdin so it can come from a heredoc or another command.
fn instructions(text: Option<String>, file: Option<&Path>) -> Result<Option<String>> {
    if text.is_some() {
        return Ok(text);
    }
    let Some(file) = file else {
        return Ok(None);
    };
    if file == Path::new("-") {
        let mut piped = String::new();
        std::io::stdin()
            .read_to_string(&mut piped)
            .context("failed to read instructions from stdin")?;
        return Ok(Some(piped));
    }
    std::fs::read_to_string(file)
        .map(Some)
        .with_context(|| format!("failed to read instructions from {}", file.display()))
}

fn run_build(args: BuildArgs) -> Result<CommandCompletion> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(&args.path)?;
    let out_dir = args
        .out
        .unwrap_or_else(|| project.root.join(".mda").join("build"));

    let summary = compile::compile(&project, &out_dir, installation.runtime_source())
        .map_err(compile::compilation)?;
    compile::show_build_summary(&summary, &args.path)?;

    Ok(CommandCompletion::Succeeded)
}

fn run_dev(args: DevArgs) -> Result<CommandCompletion> {
    dev::run(DevOptions {
        path: args.path,
        port: args.port,
        hostname: args.hostname,
        no_browser: args.no_browser,
        no_reload: args.no_reload,
        tunnel: args.tunnel,
        no_schedules: args.no_schedules,
    })
}

fn run_chat(args: ChatArgs) -> Result<CommandCompletion> {
    chat::run(ChatOptions {
        path: args.path,
        url: args.url,
        assistant: args.assistant,
        thread: args.thread,
    })
}

fn run_deploy(args: DeployArgs) -> Result<CommandCompletion> {
    deploy::run(DeployOptions {
        path: args.path,
        name: args.name,
        agent_environment: args.agent_environment,
        deployment_type: args.deployment_type,
        workspace_id: args.workspace_id,
        host_url: args.host_url,
        push_to: args.push_to,
        wait: !args.no_wait,
        context_strategy: args.context_strategy,
        yes: args.yes,
        wait_timeout: std::time::Duration::from_secs(args.wait_timeout_seconds),
    })
}

fn run_prepare(
    args: PrepareArgs,
    build_artifacts_token: Option<std::ffi::OsString>,
) -> Result<CommandCompletion> {
    deploy::prepare::run(deploy::prepare::PrepareOptions {
        build_artifacts_token,
        path: args.path,
        out: args.out,
        deployment_id: args.deployment_id,
        revision_id: args.revision_id,
        env_json: args.env_json,
        host_url: args.host_url,
        is_preview: args.preview,
        snapshot_owner: args.snapshot_owner,
    })
}

impl From<ConnectionCommonArgs> for ConnectionTarget {
    fn from(args: ConnectionCommonArgs) -> Self {
        Self {
            path: args.path,
            workspace_id: args.workspace_id,
            host_url: args.host_url,
        }
    }
}

impl ConnectionValueArgs {
    fn source(self) -> ValueSource {
        match (self.secret_from_env, self.secret_from_file) {
            (Some(name), _) => ValueSource::FromEnv(name),
            (None, Some(path)) => ValueSource::FromFile(path),
            (None, None) => ValueSource::Unspecified,
        }
    }
}

fn run_connection(args: ConnectionArgs) -> Result<CommandCompletion> {
    match args.command {
        ConnectionCommand::Create(args) => connection::create(connection::CreateOptions {
            target: args.common.into(),
            slug: args.slug,
            value: args.value.source(),
            authorize: args.authorize,
            deployment_id: args.deployment_id,
            mcp: args.mcp,
            oauth: connection::oauth_create_options(connection::OAuthCreateOptions {
                service: args.oauth.oauth,
                grant_type: args.oauth.grant_type,
                client_id: args.oauth.client_id,
                scopes: args.oauth.scopes,
                allowed_scopes: args.oauth.allowed_scopes,
                authorization_params: args.oauth.authorization_params,
                token_params: args.oauth.token_params,
                token_request_headers: args.oauth.token_request_headers,
                auth_method: args.oauth.auth_method,
                authorize_url: args.oauth.authorize_url,
                token_url: args.oauth.token_url,
            }),
        }),
        ConnectionCommand::List(args) => connection::list(connection::ListOptions {
            target: args.common.into(),
            json: args.json,
        }),
        ConnectionCommand::Get(args) => connection::get(connection::GetOptions {
            target: args.common.into(),
            slug: args.slug,
            json: args.json,
        }),
        ConnectionCommand::Delete(args) => connection::delete(connection::DeleteOptions {
            target: args.common.into(),
            slug: args.slug,
            yes: args.yes,
        }),
        ConnectionCommand::Catalog(args) => {
            connection::catalog::show(connection::catalog::ShowOptions { json: args.json })
        }
    }
}

fn run_channel(args: ChannelArgs) -> Result<CommandCompletion> {
    match args.command {
        ChannelCommand::Init(ChannelInitArgs { channel }) => {
            let kind = match channel {
                ChannelInitCommand::Slack => channel::ChannelKind::Slack,
                ChannelInitCommand::GitHub => channel::ChannelKind::GitHub,
                ChannelInitCommand::Linear => channel::ChannelKind::Linear,
            };
            let path = channel::init_channel(Path::new("."), kind)?;
            channel::show_added(kind, &path)?;
        }
    }
    Ok(CommandCompletion::Succeeded)
}

fn run_logs(args: LogsArgs) -> Result<CommandCompletion> {
    // Neither flag: follow only when a human is watching (see `logs::run`).
    let follow = match (args.follow, args.no_follow) {
        (true, _) => Some(true),
        (_, true) => Some(false),
        _ => None,
    };
    logs::run(LogsOptions {
        path: args.path,
        name: args.name,
        lines: args.lines,
        level: args.level,
        follow,
        workspace_id: args.workspace_id,
        host_url: args.host_url,
    })
}

fn run_delete(args: DeleteArgs) -> Result<CommandCompletion> {
    delete::run(DeleteOptions {
        path: args.path,
        name: args.name,
        agent_environment: args.agent_environment,
        workspace_id: args.workspace_id,
        host_url: args.host_url,
        yes: args.yes,
    })
}

fn run_eval(args: EvalArgs) -> Result<CommandCompletion> {
    match args.command {
        EvalCommand::Init(EvalInitArgs { coding_agent }) => {
            if coding_agent {
                coding_agent::require_terminal()?;
            }
            let summary = eval_cmd::init(InitOptions {
                path: std::env::current_dir()?,
            })?;
            if coding_agent {
                eval_cmd::show_initialized(&summary)?;
                let prompt = eval_cmd::coding_agent_prompt(&summary)?;
                let outcome = coding_agent::offer_handoff(&summary.project_root, &prompt, true)?;
                if matches!(outcome, coding_agent::HandoffOutcome::PromptShown) {
                    console::Term::stderr()
                        .write_line("")
                        .context("failed to finish the coding-agent handoff")?;
                }
            } else {
                eval_cmd::show_non_interactive_next_steps(&summary)?;
            }
        }
        EvalCommand::Compile(args) => {
            eval_cmd::compile(CompileOptions {
                path: args.path,
                model: args.model,
                artifact_id: args.artifact_id,
            })?;
        }
    }
    Ok(CommandCompletion::Succeeded)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn analytics_for(args: &[&str]) -> crate::telemetry::CommandInvocation {
        let command = Cli::command();
        let matches = command.clone().try_get_matches_from(args).unwrap();
        crate::telemetry::CommandInvocation::from_matches(&command, &matches).unwrap()
    }

    #[test]
    fn destroy_is_reported_as_delete() {
        assert_eq!(analytics_for(&["mda", "destroy"]).command_path, "delete");
    }

    #[test]
    fn eval_is_reported_as_evals() {
        assert_eq!(
            analytics_for(&["mda", "eval", "init"]).command_path,
            "evals init"
        );
    }

    #[test]
    fn deploy_analytics_contains_only_command_and_flag_names() {
        let analytics = analytics_for(&[
            "mda",
            "deploy",
            "/private/project",
            "-n",
            "private-agent",
            "--deployment-type",
            "dev",
            "--wait-timeout-seconds",
            "1800",
            "--workspace-id",
            "workspace-secret",
            "--host-url",
            "https://private.example.com",
        ]);

        assert_eq!(
            serde_json::to_value(analytics).unwrap(),
            json!({
                "command_path": "deploy",
                "flags_present": [
                    "deployment-type",
                    "host-url",
                    "name",
                    "wait-timeout-seconds",
                    "workspace-id"
                ]
            })
        );
    }

    fn render_help(mut command: clap::Command) -> String {
        let mut help = Vec::new();
        command.write_help(&mut help).unwrap();
        String::from_utf8(help).unwrap()
    }

    fn render_subcommand_help(command: clap::Command, name: &str) -> String {
        let subcommand = command.find_subcommand(name).unwrap().clone();
        render_help(subcommand)
    }

    fn render_nested_subcommand_help(command: clap::Command, parent: &str, name: &str) -> String {
        let mut subcommand = command
            .find_subcommand(parent)
            .unwrap()
            .find_subcommand(name)
            .unwrap()
            .clone();
        let mut help = Vec::new();
        subcommand.write_long_help(&mut help).unwrap();
        String::from_utf8(help).unwrap()
    }

    #[test]
    fn npm_help_includes_only_the_javascript_quickstart() {
        let help = render_help(cli_command(Some("npm")));

        assert!(help.contains(
            "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart"
        ));
        assert!(!help.contains(
            "https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"
        ));
        assert!(!help.contains("TypeScript:"));
    }

    #[test]
    fn top_level_help_never_lists_prepare() {
        for channel in [Some("npm"), Some("pypi"), None] {
            let help = render_help(cli_command(channel));
            assert!(
                !help.contains("prepare"),
                "`prepare` leaked into top-level help for channel {channel:?}:\n{help}"
            );
        }

        let parsed = cli_command(Some("npm"))
            .try_get_matches_from([
                "mda",
                "prepare",
                "--deployment-id",
                "d",
                "--revision-id",
                "r",
            ])
            .unwrap();
        assert_eq!(parsed.subcommand_name(), Some("prepare"));
    }

    #[test]
    fn pypi_subcommand_help_includes_only_the_python_quickstart() {
        let help = render_subcommand_help(cli_command(Some("pypi")), "deploy");

        assert!(help.contains(
            "https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"
        ));
        assert!(!help.contains(
            "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart"
        ));
        assert!(!help.contains("Python:"));
    }

    #[test]
    fn unknown_install_channel_help_includes_both_quickstarts_without_labels() {
        let help = render_help(cli_command(Some("unknown")));

        assert!(help.contains(
            "https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"
        ));
        assert!(help.contains(
            "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-quickstart"
        ));
        assert!(!help.contains("Python:"));
        assert!(!help.contains("TypeScript:"));
    }

    #[test]
    fn root_help_includes_examples_and_docs() {
        let help = render_help(cli_command(None));

        assert!(help.contains("mda init research-assistant"));
        assert!(help.contains("mda evals init"));
        assert!(!help.contains("mda evals compile"));
        assert!(help.contains("mda dev\n"));
        assert!(help.contains("mda deploy\n"));
        assert!(help.contains("mda connections list\n"));
        assert!(!help.contains("mda dev ."));
        assert!(!help.contains("mda deploy ."));
        assert!(!help.contains("mda logs ."));
        assert!(!help.contains("mda delete ."));
        assert!(help.contains("managed-deep-agents-quickstart"));
        assert!(
            !help.contains("mda connect\n"),
            "unrelated connect subcommand should not appear in root help"
        );
    }

    #[test]
    fn connections_help_includes_create_list_and_delete_examples() {
        let help = render_subcommand_help(cli_command(None), "connections");

        assert!(
            help.contains(
                "Examples:\n  # OAuth services `--oauth` recognizes\n  mda connections catalog"
            ),
            "{help}"
        );
        assert!(
            help.contains("\n  # General OAuth from the catalog\n"),
            "{help}"
        );
        assert!(
            help.contains("\n  # Connections this project's deployment uses\n"),
            "{help}"
        );
        assert!(help.contains("\n  # One connection's metadata\n"), "{help}");
        assert!(help.contains("\n  # Delete a connection\n"), "{help}");
        assert!(
            help.contains("Docs:\n  https://docs.langchain.com/langsmith/python/managed-deep-agents-quickstart"),
            "{help}"
        );
        assert!(
            help.contains("mda connections create github --oauth github --client-id CLIENT_ID"),
            "{help}"
        );
        assert!(
            !help.contains("--authorize-url"),
            "create-mode variants belong on create --help: {help}"
        );
        assert!(
            !help.contains("--mcp"),
            "create-mode variants belong on create --help: {help}"
        );
        assert!(help.contains("mda connections list\n"), "{help}");
        assert!(help.contains("mda connections list --json"), "{help}");
        assert!(help.contains("mda connections get github"), "{help}");
        assert!(help.contains("mda connections delete github\n"), "{help}");
        assert!(
            help.contains("mda connections delete github --yes"),
            "{help}"
        );
        assert!(help.contains("managed-deep-agents-quickstart"), "{help}");

        let alias = render_subcommand_help(cli_command(None), "connection");
        assert!(
            alias.contains("mda connections create github --oauth github --client-id CLIENT_ID"),
            "{alias}"
        );
    }

    #[test]
    fn connection_create_help_explains_the_three_connection_modes() {
        let help = render_nested_subcommand_help(cli_command(None), "connections", "create");

        let opaque = help.find("Opaque secret:").unwrap();
        let oauth = help.find("General OAuth:").unwrap();
        let mcp = help.find("MCP OAuth:").unwrap();
        assert!(opaque < oauth && oauth < mcp, "{help}");
        assert!(
            help.contains("With only a slug, discover the MCP server URL"),
            "{help}"
        );
        assert!(
            help.contains("mda connections create my-notion --authorize\n"),
            "{help}"
        );
        assert!(
            help.contains("mda connections create google --oauth google --client-id CLIENT_ID"),
            "{help}"
        );
        assert!(
            help.contains("mda connections create acme-api --secret-from-env ACME_API_KEY"),
            "{help}"
        );
        assert!(
            help.contains(
                "Sign in to the account that the deployed agent will use for this connection"
            ),
            "{help}"
        );
    }

    #[test]
    fn init_accepts_long_and_short_interactive_flags() {
        for flag in ["--interactive", "-i"] {
            let cli = Cli::try_parse_from(["mda", "init", "my-agent", flag]).unwrap();
            let Command::Init(args) = cli.command else {
                unreachable!();
            };
            assert!(args.coding_agent, "{flag}");
        }

        let cli = Cli::command();
        let init = cli.find_subcommand("init").expect("init subcommand");
        let mut help = Vec::new();
        init.clone().write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();
        assert!(help.contains("-i, --interactive"));
        assert!(help.contains("Prompts for the project name when omitted"));
        assert!(help.contains("Offer a coding-agent handoff after scaffolding"));
    }

    #[test]
    fn init_without_a_name_keeps_the_coding_agent_handoff_disabled() {
        let cli = Cli::try_parse_from(["mda", "init"]).unwrap();
        let Command::Init(args) = cli.command else {
            unreachable!();
        };
        assert!(args.name.is_none());
        assert!(!args.coding_agent);
    }

    #[test]
    fn init_scaffolds_a_starter_project() {
        let cli = Cli::try_parse_from(["mda", "init", "my-agent"]).unwrap();
        match cli.command {
            Command::Init(args) => {
                assert_eq!(args.name.as_deref(), Some("my-agent"));
                assert!(!args.coding_agent);
                assert!(!args.no_sandbox);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn eval_init_does_not_require_task_guidance() {
        let cli = Cli::try_parse_from(["mda", "evals", "init"]).unwrap();
        let Command::Eval(EvalArgs {
            command: EvalCommand::Init(args),
        }) = cli.command
        else {
            unreachable!();
        };
        assert!(!args.coding_agent);

        assert!(Cli::try_parse_from(["mda", "evals", "init", "--codex"]).is_err());
    }

    #[test]
    fn eval_init_rejects_the_removed_task_name() {
        assert!(Cli::try_parse_from(["mda", "evals", "init", "smoke"]).is_err());
    }

    #[test]
    fn eval_init_accepts_long_and_short_interactive_flags() {
        for flag in ["--interactive", "-i"] {
            let cli = Cli::try_parse_from(["mda", "evals", "init", flag]).unwrap();
            let Command::Eval(EvalArgs {
                command: EvalCommand::Init(args),
            }) = cli.command
            else {
                unreachable!();
            };
            assert!(args.coding_agent, "{flag}");
        }
    }

    #[test]
    fn eval_help_hides_the_job_plugin_compile_entrypoint() {
        let command = Cli::command();
        let evals = command.find_subcommand("evals").expect("evals subcommand");
        let mut help = Vec::new();

        evals.clone().write_long_help(&mut help).unwrap();

        let help = String::from_utf8(help).unwrap();
        assert!(help.contains("init"));
        assert!(!help.contains("compile"), "unexpected help: {help}");
    }

    #[test]
    fn evals_is_canonical_and_eval_remains_an_alias() {
        let command = Cli::command();
        let evals = command.find_subcommand("evals").expect("evals subcommand");
        assert_eq!(evals.get_name(), "evals");
        assert!(Cli::try_parse_from(["mda", "eval", "init"]).is_ok());

        let help = Cli::command()
            .try_get_matches_from(["mda", "evals", "init", "--help"])
            .unwrap_err();
        assert_eq!(help.kind(), clap::error::ErrorKind::DisplayHelp);
        let rendered = help.to_string();
        assert!(
            rendered.contains("Usage: mda evals init [OPTIONS]\n"),
            "unexpected help: {rendered}"
        );
        assert!(!rendered.contains("Usage: mda eval init [OPTIONS] [PATH]"));
    }

    fn connection_command(args: &[&str]) -> ConnectionCommand {
        let cli = Cli::try_parse_from(args).unwrap();
        let Command::Connection(args) = cli.command else {
            unreachable!("expected a connection command");
        };
        args.command
    }

    #[test]
    fn connection_takes_the_slug_positionally_and_the_project_by_flag() {
        let ConnectionCommand::Get(args) =
            connection_command(&["mda", "connection", "get", "my-key"])
        else {
            unreachable!("expected get");
        };
        assert_eq!(args.slug, "my-key");
        assert_eq!(args.common.path, PathBuf::from("."));
        assert!(!args.json);

        let ConnectionCommand::Get(args) = connection_command(&[
            "mda",
            "connection",
            "get",
            "my-key",
            "--project",
            "../other",
        ]) else {
            unreachable!("expected get");
        };
        assert_eq!(args.slug, "my-key");
        assert_eq!(args.common.path, PathBuf::from("../other"));
    }

    #[test]
    fn connection_create_accepts_mcp_urls_with_or_without_a_scheme() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "foobar",
            "--mcp",
            "https://mcp.acme.com/mcp",
        ]) else {
            unreachable!("expected create");
        };
        assert_eq!(args.mcp.as_deref(), Some("https://mcp.acme.com/mcp"));

        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "foobar",
            "--mcp",
            "mcp.acme.com/mcp",
        ]) else {
            unreachable!("expected create");
        };
        assert_eq!(args.mcp.as_deref(), Some("mcp.acme.com/mcp"));
    }

    #[test]
    fn connection_create_accepts_explicit_deployment_without_project() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connections",
            "create",
            "doordash",
            "--client-id",
            "client",
            "--auth-method",
            "none",
            "--authorize-url",
            "https://auth.example.com/authorize",
            "--token-url",
            "https://auth.example.com/token",
            "--authorize",
            "--workspace-id",
            "workspace",
            "--deployment-id",
            "deployment",
        ]) else {
            unreachable!("expected create");
        };
        assert!(args.authorize);
        assert_eq!(args.deployment_id.as_deref(), Some("deployment"));
        assert_eq!(args.common.workspace_id.as_deref(), Some("workspace"));
    }

    #[test]
    fn connection_create_accepts_mcp_with_authorize() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connections",
            "create",
            "notion",
            "--mcp",
            "mcp.notion.com",
            "--authorize",
        ]) else {
            unreachable!("expected create");
        };

        assert!(args.authorize);
    }

    #[test]
    fn connection_create_rejects_mcp_with_oauth_or_secret_flags() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "foobar",
            "--mcp",
            "mcp.acme.com/mcp",
            "--oauth",
            "github",
        ])
        .is_err());
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "foobar",
            "--mcp",
            "mcp.acme.com/mcp",
            "--secret-from-env",
            "TOKEN",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_restricts_the_auth_method() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "gh",
            "--oauth",
            "github",
            "--auth-method",
            "client_secret_post",
        ]) else {
            unreachable!("expected create");
        };
        assert_eq!(
            args.oauth.auth_method.as_deref(),
            Some("client_secret_post")
        );

        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "gh",
            "--auth-method",
            "client_secret_jwt",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_accepts_oauth_grant_types_and_token_params() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "spotify",
            "--oauth",
            "spotify",
            "--grant-type",
            "client_credentials",
            "--token-param",
            "audience=music-api",
            "--token-param",
            "resource=library",
            "--token-request-header",
            "X-Provider-Version=2024-01",
        ]) else {
            unreachable!("expected create");
        };

        assert_eq!(args.oauth.grant_type.as_deref(), Some("client_credentials"));
        assert_eq!(
            args.oauth.token_params,
            ["audience=music-api", "resource=library"]
        );
        assert_eq!(
            args.oauth.token_request_headers,
            ["X-Provider-Version=2024-01"]
        );

        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "github",
            "--oauth",
            "github",
            "--grant-type",
            "authorization_code",
        ]) else {
            unreachable!("expected create");
        };
        assert_eq!(args.oauth.grant_type.as_deref(), Some("authorization_code"));
    }

    #[test]
    fn connection_create_rejects_invalid_grant_type() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "spotify",
            "--grant-type",
            "password",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_rejects_mcp_with_client_credentials_flags() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "spotify",
            "--mcp",
            "https://mcp.example.com",
            "--grant-type",
            "client_credentials",
        ])
        .is_err());
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "spotify",
            "--mcp",
            "https://mcp.example.com",
            "--token-param",
            "audience=api",
        ])
        .is_err());
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "spotify",
            "--mcp",
            "https://mcp.example.com",
            "--token-request-header",
            "Authorization=Bearer token",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_accepts_authorize_with_client_credentials() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connection",
            "create",
            "spotify",
            "--grant-type",
            "client_credentials",
            "--authorize",
        ]) else {
            unreachable!("expected create");
        };

        assert!(args.authorize);
    }

    #[test]
    fn connection_oauth_create_accepts_authorize() {
        let ConnectionCommand::Create(args) = connection_command(&[
            "mda",
            "connections",
            "create",
            "github",
            "--oauth",
            "github",
            "--authorize",
        ]) else {
            unreachable!("expected create");
        };

        assert!(args.authorize);
    }

    #[test]
    fn connection_catalog_needs_no_workspace() {
        let ConnectionCommand::Catalog(args) =
            connection_command(&["mda", "connection", "catalog"])
        else {
            unreachable!("expected catalog");
        };
        assert!(!args.json);

        let ConnectionCommand::Catalog(args) =
            connection_command(&["mda", "connection", "catalog", "--json"])
        else {
            unreachable!("expected catalog");
        };
        assert!(args.json);

        assert!(
            Cli::try_parse_from(["mda", "connection", "catalog", "--workspace-id", "ws"]).is_err()
        );
    }

    /// The whole point of `--project`: a trailing path would otherwise be read
    /// as the slug on four of the five verbs, and as the project on `list`.
    #[test]
    fn connection_rejects_a_trailing_path_positional() {
        assert!(Cli::try_parse_from(["mda", "connection", "get", "my-key", "../other"]).is_err());
        assert!(Cli::try_parse_from(["mda", "connection", "list", "../other"]).is_err());
    }

    #[test]
    fn connection_create_rejects_two_value_sources() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "my-key",
            "--secret-from-env",
            "MY_KEY",
            "--secret-from-file",
            "/tmp/k",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_rejects_the_removed_client_secret_env_flag() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "my-key",
            "--oauth",
            "github",
            "--client-secret-env",
            "MY_KEY",
        ])
        .is_err());
    }

    #[test]
    fn connection_create_rejects_the_removed_app_flag() {
        assert!(Cli::try_parse_from([
            "mda",
            "connection",
            "create",
            "my-key",
            "--oauth",
            "github",
            "--app",
            "my-app",
        ])
        .is_err());
    }

    #[test]
    fn connection_help_hides_host_url() {
        let command = Cli::command();
        let connection = command
            .find_subcommand("connections")
            .expect("connections subcommand");
        let mut create = connection
            .find_subcommand("create")
            .expect("create subcommand")
            .clone();
        let mut help = Vec::new();
        create.write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();

        assert!(help.contains("--secret-from-env <VAR>"), "{help}");
        assert!(help.contains("--secret-from-file <PATH>"), "{help}");
        assert!(help.contains("--project <PATH>"), "{help}");
        assert!(help.contains("--mcp <URL>"), "{help}");
        assert!(help.contains("--grant-type <GRANT>"), "{help}");
        assert!(help.contains("--token-param <KEY=VALUE>"), "{help}");
        assert!(
            help.contains("--token-request-header <KEY=VALUE>"),
            "{help}"
        );
        assert!(
            !help.contains("skips project ownership inference"),
            "{help}"
        );
        assert!(!help.contains("--owner"), "{help}");
        assert!(!help.contains("--host-url"), "{help}");
        assert!(!help.contains("--client-secret-env"), "{help}");
        assert!(!help.contains("--app"), "{help}");
    }

    #[test]
    fn connection_analytics_reports_no_slug_and_no_value() {
        let analytics = analytics_for(&[
            "mda",
            "connection",
            "create",
            "private-slug",
            "--project",
            "/private/project",
            "--secret-from-env",
            "PRIVATE_VAR",
            "--workspace-id",
            "workspace-secret",
        ]);

        assert_eq!(
            serde_json::to_value(analytics).unwrap(),
            json!({
                "command_path": "connections create",
                "flags_present": ["project", "secret-from-env", "workspace-id"]
            })
        );
    }

    #[test]
    fn init_rejects_the_removed_identity_flag() {
        assert!(Cli::try_parse_from(["mda", "init", "acme", "--identity"]).is_err());
    }

    #[test]
    fn init_rejects_the_removed_scope_flag() {
        assert!(Cli::try_parse_from(["mda", "init", "acme", "--scope", "user"]).is_err());
    }

    #[test]
    fn init_accepts_channel_short_and_long_flags() {
        for args in [
            ["mda", "init", "my-agent", "-c", "slack"].as_slice(),
            ["mda", "init", "my-agent", "--channel", "slack"].as_slice(),
            ["mda", "init", "my-agent", "--channels", "slack"].as_slice(),
            ["mda", "init", "-c", "slack", "my-agent"].as_slice(),
        ] {
            let cli = Cli::try_parse_from(args).unwrap();
            let Command::Init(init) = cli.command else {
                unreachable!();
            };
            assert_eq!(init.name.as_deref(), Some("my-agent"), "{args:?}");
            assert_eq!(init.channels, vec![init::InitChannel::Slack], "{args:?}");
        }
    }

    #[test]
    fn init_parses_github_channel() {
        let cli = Cli::try_parse_from(["mda", "init", "my-agent", "-c", "github", "-c", "slack"])
            .unwrap();
        let Command::Init(init) = cli.command else {
            unreachable!();
        };
        assert_eq!(
            init.channels,
            vec![init::InitChannel::GitHub, init::InitChannel::Slack]
        );
    }

    #[test]
    fn init_rejects_an_unknown_channel() {
        let err = match Cli::try_parse_from(["mda", "init", "my-agent", "--channel", "discord"]) {
            Err(err) => err,
            Ok(_) => panic!("expected an unknown channel to be rejected"),
        };
        let message = err.to_string();
        assert!(
            message.contains("invalid value 'discord'")
                && message.contains("slack")
                && message.contains("github"),
            "{message}"
        );
    }

    #[test]
    fn init_help_documents_channel_aliases() {
        let cli = Cli::command();
        let init = cli.find_subcommand("init").expect("init subcommand");
        let mut help = Vec::new();
        init.clone().write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();
        assert!(help.contains("-c, --channel"));
        assert!(help.contains("[alias: --channels]"));
        assert!(help.contains("slack"));
    }

    #[test]
    fn deploy_steps_follow_every_non_agent_outcome() {
        let cases = [
            (coding_agent::HandoffOutcome::Started, false),
            (coding_agent::HandoffOutcome::InstalledNeedsRestart, false),
            (coding_agent::HandoffOutcome::PromptShown, true),
            (coding_agent::HandoffOutcome::Cancelled, true),
        ];

        for (outcome, expected) in cases {
            assert_eq!(should_show_deploy_steps(outcome), expected, "{outcome:?}");
        }
    }

    #[test]
    fn init_reads_instructions_from_a_file() {
        let file = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(file.path(), "You answer questions about our docs.\n").unwrap();

        let prompt = instructions(None, Some(file.path())).unwrap();

        assert_eq!(
            prompt.as_deref(),
            Some("You answer questions about our docs.\n")
        );
    }

    #[test]
    fn deploy_help_lists_deployment_type_values_and_hides_host_url() {
        let cli = Cli::command();
        let deploy = cli.find_subcommand("deploy").expect("deploy subcommand");
        let mut help = Vec::new();
        deploy.clone().write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();

        assert!(help.contains("dev"));
        assert!(help.contains("prod"));
        assert!(help.contains("static agent `name`, or `agent` for a dynamic entry"));
        assert!(
            !help.contains("--host-url"),
            "advanced --host-url should be hidden from default help"
        );
    }

    #[test]
    fn deploy_rejects_invalid_deployment_type_at_parse_time() {
        let err = match Cli::try_parse_from(["mda", "deploy", ".", "--deployment-type", "staging"])
        {
            Err(err) => err,
            Ok(_) => panic!("expected parse failure for invalid deployment type"),
        };
        let rendered = err.to_string();
        assert!(
            rendered.contains("invalid value") || rendered.contains("possible values"),
            "unexpected error: {rendered}"
        );
    }

    #[test]
    fn deploy_accepts_push_to() {
        let parsed = Cli::try_parse_from(["mda", "deploy", "--push-to", "registry.example/agent"]);

        assert!(parsed.is_ok());
    }

    #[test]
    fn deploy_rejects_image_uri() {
        let parsed = Cli::try_parse_from([
            "mda",
            "deploy",
            "--image-uri",
            "registry.example/agent@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ]);

        assert!(parsed.is_err());
    }

    #[test]
    fn deploy_and_delete_accept_agent_environment_with_name() {
        for command in ["deploy", "delete"] {
            let argv = [
                "mda",
                command,
                "-n",
                "support",
                "--agent-environment",
                "staging",
            ];
            assert!(Cli::try_parse_from(argv).is_ok(), "{command}");

            let argv = ["mda", command, "--agent-environment", "dev"];
            assert!(Cli::try_parse_from(argv).is_err(), "{command}");

            let argv = ["mda", command, "--agent-id", "support"];
            assert!(Cli::try_parse_from(argv).is_err(), "{command}");
        }
    }

    #[test]
    fn deploy_accepts_short_name_flag() {
        let cli = Cli::try_parse_from(["mda", "deploy", ".", "-n", "my-agent"]).unwrap();
        match cli.command {
            Command::Deploy(args) => assert_eq!(args.name.as_deref(), Some("my-agent")),
            _ => unreachable!(),
        }
    }

    #[test]
    fn deploy_accepts_long_and_short_yes_flags() {
        for flag in ["--yes", "-y"] {
            let cli = Cli::try_parse_from(["mda", "deploy", ".", flag]).unwrap();
            let Command::Deploy(args) = cli.command else {
                unreachable!();
            };
            assert!(args.yes, "{flag}");
        }

        let cli = Cli::try_parse_from(["mda", "deploy"]).unwrap();
        let Command::Deploy(args) = cli.command else {
            unreachable!();
        };
        assert!(!args.yes);
    }

    #[test]
    fn channel_init_slack_and_channels_alias_parse() {
        for command in ["channel", "channels"] {
            let cli = Cli::try_parse_from(["mda", command, "init", "slack"]).unwrap();
            assert!(matches!(
                cli.command,
                Command::Channel(ChannelArgs {
                    command: ChannelCommand::Init(ChannelInitArgs {
                        channel: ChannelInitCommand::Slack,
                    }),
                })
            ));
        }
    }

    #[test]
    fn channel_init_github_parses() {
        for command in ["channel", "channels"] {
            let cli = Cli::try_parse_from(["mda", command, "init", "github"]).unwrap();
            assert!(matches!(
                cli.command,
                Command::Channel(ChannelArgs {
                    command: ChannelCommand::Init(ChannelInitArgs {
                        channel: ChannelInitCommand::GitHub,
                    }),
                })
            ));
        }
    }

    #[test]
    fn channel_add_slack_is_removed() {
        assert!(Cli::try_parse_from(["mda", "channel", "add", "slack"]).is_err());
    }

    #[test]
    fn deploy_rejects_removed_configure_slack_flag() {
        assert!(Cli::try_parse_from(["mda", "deploy", ".", "--configure-slack"]).is_err());
    }

    #[test]
    fn connect_command_is_not_supported() {
        assert!(
            Cli::try_parse_from(["mda", "connect", "gmail"]).is_err(),
            "connect is not a supported subcommand in this release"
        );
        assert!(
            Cli::command().find_subcommand("connect").is_none(),
            "connect must not be registered as a subcommand"
        );
    }

    #[test]
    fn logs_defaults_to_one_thousand_lines_and_auto_follow() {
        let cli = Cli::try_parse_from(["mda", "logs", "."]).unwrap();
        match cli.command {
            Command::Logs(args) => {
                assert_eq!(args.lines, 1000);
                assert!(!args.follow);
                assert!(!args.no_follow);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn logs_accepts_line_count_and_level() {
        let cli =
            Cli::try_parse_from(["mda", "logs", ".", "--lines", "50", "--level", "error"]).unwrap();
        match cli.command {
            Command::Logs(args) => {
                assert_eq!(args.lines, 50);
                assert_eq!(args.level, Some(LogLevel::Error));
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn logs_rejects_conflicting_follow_flags() {
        let err = match Cli::try_parse_from(["mda", "logs", ".", "-f", "--no-follow"]) {
            Err(err) => err,
            Ok(_) => panic!("expected --follow and --no-follow to conflict"),
        };
        assert!(err.to_string().contains("cannot be used with"));
    }

    #[test]
    fn logs_rejects_a_zero_line_count() {
        assert!(Cli::try_parse_from(["mda", "logs", ".", "--lines", "0"]).is_err());
    }

    #[test]
    fn delete_defaults_to_the_current_directory_and_asks_for_confirmation() {
        let cli = Cli::try_parse_from(["mda", "delete"]).unwrap();
        match cli.command {
            Command::Delete(args) => {
                assert_eq!(args.path, PathBuf::from("."));
                assert!(!args.yes, "delete must confirm unless --yes is passed");
                assert_eq!(args.name, None);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn delete_accepts_short_name_and_yes_flags() {
        let cli = Cli::try_parse_from(["mda", "delete", ".", "-n", "my-agent", "-y"]).unwrap();
        match cli.command {
            Command::Delete(args) => {
                assert_eq!(args.name.as_deref(), Some("my-agent"));
                assert!(args.yes);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn destroy_is_an_alias_for_delete() {
        let cli = Cli::try_parse_from(["mda", "destroy", "."]).unwrap();
        assert!(matches!(cli.command, Command::Delete(_)));
    }

    #[test]
    fn delete_help_hides_host_url() {
        let cli = Cli::command();
        let delete = cli.find_subcommand("delete").expect("delete subcommand");
        let mut help = Vec::new();
        delete.clone().write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap();

        assert!(help.contains("--yes"));
        assert!(
            !help.contains("--host-url"),
            "advanced --host-url should be hidden from default help"
        );
    }

    #[test]
    fn init_rejects_language_flag() {
        let err = match Cli::try_parse_from(["mda", "init", "demo", "--language", "python"]) {
            Err(err) => err,
            Ok(_) => panic!("expected --language to be rejected"),
        };
        assert!(err.to_string().contains("unexpected argument"));
    }

    #[test]
    fn chat_defaults_to_the_local_dev_server() {
        let cli = Cli::try_parse_from(["mda", "chat"]).unwrap();
        match cli.command {
            Command::Chat(args) => {
                assert_eq!(args.path, PathBuf::from("."));
                assert!(args.url.is_none());
                assert!(args.thread.is_none());
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn chat_accepts_a_url_and_thread() {
        let cli =
            Cli::try_parse_from(["mda", "chat", "--url", "http://localhost:3000", "-t", "abc"])
                .unwrap();
        match cli.command {
            Command::Chat(args) => {
                assert_eq!(args.url.as_deref(), Some("http://localhost:3000"));
                assert_eq!(args.thread.as_deref(), Some("abc"));
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_accepts_short_port_flag() {
        let cli = Cli::try_parse_from(["mda", "dev", ".", "-p", "8123"]).unwrap();
        match cli.command {
            Command::Dev(args) => assert_eq!(args.port, Some(8123)),
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_opens_browser_by_default() {
        let cli = Cli::try_parse_from(["mda", "dev", "."]).unwrap();

        match cli.command {
            Command::Dev(args) => assert!(!args.no_browser),
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_accepts_no_browser_flag() {
        let cli = Cli::try_parse_from(["mda", "dev", ".", "--no-browser"]).unwrap();

        match cli.command {
            Command::Dev(args) => assert!(args.no_browser),
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_accepts_tunnel_flag() {
        let cli = Cli::try_parse_from(["mda", "dev", ".", "--tunnel"]).unwrap();

        match cli.command {
            Command::Dev(args) => assert!(args.tunnel),
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_provisions_schedules_unless_told_not_to() {
        for (argv, expected) in [
            (vec!["mda", "dev", "."], false),
            (vec!["mda", "dev", ".", "--no-schedules"], true),
        ] {
            match Cli::try_parse_from(argv).unwrap().command {
                Command::Dev(args) => assert_eq!(args.no_schedules, expected),
                _ => unreachable!(),
            }
        }
    }

    #[test]
    fn dev_omits_tunnel_by_default() {
        let cli = Cli::try_parse_from(["mda", "dev", "."]).unwrap();

        match cli.command {
            Command::Dev(args) => assert!(!args.tunnel),
            _ => unreachable!(),
        }
    }

    #[test]
    fn dev_rejects_the_removed_browser_flag() {
        assert!(Cli::try_parse_from(["mda", "dev", ".", "--browser"]).is_err());
    }

    #[test]
    fn eval_compile_accepts_project_path_and_model() {
        let cli = Cli::try_parse_from([
            "mda",
            "evals",
            "compile",
            "./myAgent",
            "--model",
            "openai:gpt-4o-mini",
        ])
        .unwrap();
        match cli.command {
            Command::Eval(args) => match args.command {
                EvalCommand::Compile(compile) => {
                    assert_eq!(compile.path, PathBuf::from("./myAgent"));
                    assert_eq!(compile.model.as_deref(), Some("openai:gpt-4o-mini"));
                }
                _ => unreachable!(),
            },
            _ => unreachable!(),
        }

        assert!(Cli::try_parse_from(["mda", "evals", "compile", "--task", "smoke"]).is_err());
    }

    #[test]
    fn eval_compile_rejects_repeated_model_overrides() {
        assert!(Cli::try_parse_from([
            "mda",
            "evals",
            "compile",
            "--model",
            "openai:gpt-4o-mini",
            "--model",
            "anthropic:claude-sonnet-4-5",
        ])
        .is_err());
    }

    #[test]
    fn eval_compile_accepts_hidden_artifact_id() {
        let artifact_id = "11111111-1111-4111-8111-111111111111";
        let cli =
            Cli::try_parse_from(["mda", "evals", "compile", ".", "--artifact-id", artifact_id])
                .unwrap();

        match cli.command {
            Command::Eval(args) => match args.command {
                EvalCommand::Compile(compile) => {
                    assert_eq!(compile.artifact_id.as_deref(), Some(artifact_id));
                }
                _ => unreachable!(),
            },
            _ => unreachable!(),
        }
    }

    #[test]
    fn eval_run_is_removed() {
        match Cli::try_parse_from(["mda", "evals", "run", "."]) {
            Ok(_) => panic!("expected `evals run` to be rejected"),
            Err(err) => {
                let rendered = err.to_string();
                assert!(
                    rendered.contains("unrecognized subcommand")
                        || rendered.contains("invalid")
                        || rendered.contains("error"),
                    "unexpected error: {rendered}"
                );
            }
        }
    }
}
