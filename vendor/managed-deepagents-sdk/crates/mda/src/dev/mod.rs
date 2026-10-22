//! `mda dev` — compile a Managed Deep Agent project and run it locally on the
//! LangGraph in-memory dev server.
//!
//! The dev server is language-bound: a TypeScript graph must be hosted by the
//! Node CLI (`@langchain/langgraph-cli`) and a Python graph by the Python CLI
//! (`langgraph-cli[inmem]`). So `mda dev` mirrors `mda deploy`: it compiles the
//! project into `.mda/dev` and then hands that build to the language-matching
//! dev server.
//!
//! Authors only declare `managed-deepagents` (plus their own extras) in the
//! project manifest. The managed LangGraph stack is injected into the build
//! manifest at compile time. For TypeScript, `mda dev` installs that build
//! into `.mda/dev/node_modules` (same surface the remote deploy builder
//! installs). For Python, `uv run` resolves the compiled build's injected
//! dependencies (plus `langgraph-cli[inmem]`) on startup, and watch rebuilds
//! re-run `uv sync` into `.mda/dev/.venv` when `pyproject.toml` / `uv.lock`
//! change so newly added packages are importable without restarting `mda dev`.
//!
//! With reload enabled (the default), `mda` also watches the project root and
//! classifies edits: source/manifest changes trigger an incremental recompile
//! into `.mda/dev` (preserving `node_modules` / `.venv`); `instructions.md` /
//! `skills/**` sync into the local Context Hub mock; `.env` is restaged.
//! LangGraph's own watcher then picks up build/env changes. `--no-reload`
//! disables both the mda watch loop and LangGraph reload.
//!
//! One wrinkle for TypeScript: if `managed-deepagents` itself is symlinked into
//! the project (local SDK development or `npm link`), Node would canonicalize
//! the managed runtime to the SDK's real path and resolve its `deepagents` peer
//! from there instead of the project. To keep local file:-link workflows
//! working, the Node dev server is launched with `--preserve-symlinks`.

mod schedules;
mod watch;

use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use cliclack::{log, outro, spinner};
use std::io::IsTerminal;

use crate::agent_config;
use crate::child::{Outcome, OwnedChild};
use crate::cli_output::show_wordmark_intro;
use crate::command::CommandCompletion;
use crate::compile::{self, CompileSummary, IdentitySummary, MemoryDecision};
use crate::dependencies;
use crate::deploy::bake::bake_recipe;
use crate::deploy::helpers::{
    resolve_langsmith_api_bases, resolve_platform_api_url, sandbox_api_endpoint,
    LANGSMITH_ENDPOINT_ENV, MDA_ASSISTANT_ID_ENV, MDA_DEPLOYMENT_NAME_ENV, MDA_PUBLIC_API_URL_ENV,
    MDA_SANDBOX_API_ENDPOINT_ENV, MDA_SANDBOX_RECIPE_SNAPSHOT_ENV, RESERVED_ENV_VARS,
    WORKSPACE_ID_ENV,
};
use crate::deploy::{self, resolve_https_public_api_url, PublicApiUrl};
use crate::error::{categorized, ErrorCategory};
use crate::install::Installation;
use crate::interrupt::Interrupt;
use crate::langsmith::{Credential, API_KEY_ENV};
use crate::project::{Language, Project};
use crate::toolchain;
use schedules::ScheduleSync;
use watch::{
    batch_includes_env_path, classify_watch_paths, hub_inputs_fingerprint, poke_reload_signal,
    ProjectWatcher, WatchAction, WatchClassifyContext, WatchPoll,
};

/// Stamp file recording the last successful managed-deps install fingerprint
/// (TypeScript `npm ci` or Python `uv sync` into `.mda/dev`).
const DEPS_STAMP: &str = ".mda-deps-stamp";

/// User-facing options for `mda dev`.
pub struct DevOptions {
    /// Path to the Managed Deep Agent project.
    pub path: PathBuf,
    /// Port the dev server binds to (defaults to the LangGraph CLI default).
    pub port: Option<u16>,
    /// Hostname the dev server binds to (defaults to the LangGraph CLI default).
    pub hostname: Option<String>,
    /// Skip automatically opening the browser when the server starts.
    pub no_browser: bool,
    /// Disable the dev server's hot reload.
    pub no_reload: bool,
    /// Expose the dev server through a public Cloudflare tunnel, for browsers
    /// that block localhost requests from the HTTPS Studio origin.
    pub tunnel: bool,
    /// Leave `schedules/` off the local server instead of provisioning them.
    pub no_schedules: bool,
}

/// A resolved dev-server invocation: the program to launch and its arguments.
///
/// Kept separate from the spawn so the language-specific command construction
/// can be unit-tested without touching the filesystem or a real server.
#[derive(Debug, PartialEq, Eq)]
pub struct DevCommand {
    pub program: String,
    pub args: Vec<String>,
    /// Local origin derived from the same host and port as the server arguments.
    pub local_api_url: String,
}

/// Whether preparation narrates its summary.
///
/// `mda dev` owns the terminal and reports every step. `mda chat` drops the
/// summary — compile counts, feature lines, injected env notices — and keeps
/// what the slow steps print for themselves: installing dependencies and
/// baking a sandbox recipe run their own spinners under either setting, since
/// minutes of silence would read as a hang.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Narration {
    Full,
    Silent,
}

impl Narration {
    fn is_full(self) -> bool {
        self == Narration::Full
    }
}

/// What an existing `.mda/dev` is worth keeping across a compile.
///
/// `mda dev` clears it on startup: the author runs that command once and the
/// server lives for the session, so paying a full rebuild buys a clean slate
/// cheaply. Chat starts a server per invocation, and a Full compile wipes
/// `node_modules` / `.venv` and `.langgraph_api` — the deps would reinstall
/// every run, and the thread the closing resume command names would already
/// be gone by the time it is pasted.
#[derive(Clone, Copy, PartialEq, Eq)]
enum BuildReuse {
    Clear,
    Keep,
}

/// What preparation produced that its caller still needs.
struct Prepared {
    summary: CompileSummary,
    needs_developer_principal: bool,
}

/// Everything that has to happen before the dev server can start: compile the
/// project into `.mda/dev`, stage its `.env`, and install the managed
/// dependencies the local server resolves imports from.
fn prepare_dev(
    installation: &Installation,
    project: &Project,
    out_dir: &Path,
    opts: &DevOptions,
    narration: Narration,
    reuse: BuildReuse,
) -> Result<Prepared> {
    let sp = narration.is_full().then(spinner);
    if let Some(sp) = &sp {
        sp.start("Compiling project");
    }
    let compiled = match reuse {
        BuildReuse::Clear => compile::compile(project, out_dir, installation.runtime_source()),
        BuildReuse::Keep => {
            compile::compile_refresh(project, out_dir, installation.runtime_source())
        }
    };
    let summary = match compiled {
        Ok(summary) => {
            if let Some(sp) = &sp {
                sp.stop(format!(
                    "Compiled {} file(s) → .mda/dev",
                    summary.copied.len()
                ));
            }
            summary
        }
        Err(err) => {
            if let Some(sp) = &sp {
                sp.error("Could not compile the project");
            }
            return Err(compile::compilation(err));
        }
    };
    if narration.is_full() {
        report_compile_warnings(&summary);
    }

    stage_dev_env_file(
        &project.root,
        out_dir,
        &summary.assistant_id,
        &local_dev_public_api_url(opts),
    )?;
    let connector_requirements =
        compile::emit_connector_requirements_for_entries(project, out_dir, &summary.connectors)?;
    let needs_developer_principal = connectors_need_developer_principal(&connector_requirements)
        || summary.memory.user_enabled();
    ensure_dev_langsmith_env(
        &project.root,
        out_dir,
        summary.sandbox || needs_developer_principal,
        DevLoginPrompt::Allowed,
    )?;
    ensure_dev_developer_principal(out_dir, needs_developer_principal)?;
    ensure_dev_workspace_connections(out_dir, &connector_requirements)?;
    ensure_dev_tracing_project(out_dir, &summary.assistant_id)?;
    ensure_dev_sandbox_recipe(
        project,
        out_dir,
        &summary.assistant_id,
        DevLoginPrompt::Allowed,
    )?;
    ensure_dev_handoff_secret(out_dir)?;
    let injected_ingress = ensure_dev_ingress_secret(project, out_dir)?;
    let injected_public_api = ensure_dev_public_api_url(out_dir, opts)?;
    ensure_dev_assistant_id(project, out_dir)?;

    if narration.is_full() {
        report_project_features(project, &summary, !opts.no_schedules)?;

        if injected_ingress {
            log::info(format!(
                "Injected local {} (override in .env)",
                compile::INGRESS_SECRET_ENV
            ))?;
        }
        if injected_public_api {
            let url = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"))
                .get(MDA_PUBLIC_API_URL_ENV)
                .cloned()
                .unwrap_or_default();
            log::info(format!(
                "Injected local {MDA_PUBLIC_API_URL_ENV}={url} (override in .env)"
            ))?;
        }
    }

    // Init scaffolds only declare `managed-deepagents` at the project root.
    // Managed LangGraph deps live in the compiled build manifest (same as
    // deploy). Install/sync them into `.mda/dev` so the local server can
    // resolve imports without requiring authors to mirror the managed stack.
    // Python also stamps here so the first watch rebuild can skip a no-op
    // `uv sync` (startup `uv run` would sync again, but without a stamp).
    let deps_install = match project.language {
        Language::TypeScript => install_typescript_build_deps(out_dir)?,
        Language::Python => install_python_build_deps(out_dir)?,
    };
    match deps_install {
        DepsInstall::SkippedNoPackage | DepsInstall::Installed => {}
        DepsInstall::UpToDate => {
            if narration.is_full() {
                log::info("Managed dependencies in .mda/dev are up to date")?;
            }
        }
    }

    Ok(Prepared {
        summary,
        needs_developer_principal,
    })
}

/// Directory `mda dev` compiles into and serves from.
///
/// Separate from `.mda/build` so a full compile by `mda build`, `mda deploy`,
/// or `mda connections create` cannot wipe a running dev server's install.
pub(crate) fn dev_out_dir(root: &Path) -> PathBuf {
    root.join(".mda").join("dev")
}

/// Run the dev command end-to-end: compile, then launch the dev server.
pub fn run(opts: DevOptions) -> Result<CommandCompletion> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    let out_dir = dev_out_dir(&project.root);

    show_wordmark_intro()?;
    log::info(format!(
        "Project: {} ({})",
        project.root.display(),
        project.language.label()
    ))?;

    let Prepared {
        summary,
        needs_developer_principal,
    } = prepare_dev(
        &installation,
        &project,
        &out_dir,
        &opts,
        Narration::Full,
        BuildReuse::Clear,
    )?;

    let command = dev_command(project.language, &opts)?;
    if opts.no_reload {
        outro(format!(
            "Starting LangGraph dev server (reload disabled)…\n  {} {}",
            command.program,
            command.args.join(" ")
        ))?;
    } else {
        outro(format!(
            "Starting LangGraph dev server…\n  {} {}\n\
             Watching project root — code rebuilds on save; \
             skills/instructions sync live.",
            command.program,
            command.args.join(" ")
        ))?;
    }

    let interrupt = Interrupt::install()?;
    let mut child = spawn_langgraph_dev(&command, &project, &out_dir)?;
    let mut schedules = (!opts.no_schedules).then(|| {
        ScheduleSync::new(
            &command.local_api_url,
            &out_dir,
            summary.schedule_crons.clone(),
        )
    });

    if opts.no_reload {
        // Nothing rebuilds, so schedules only need provisioning once.
        while let Some(sync) = schedules.as_mut().filter(|sync| sync.is_pending()) {
            if let Some(outcome) = child
                .poll(&interrupt)
                .context("failed to poll langgraph dev process")?
            {
                return classify_langgraph_outcome(outcome);
            }
            sync.tick()?;
            interrupt.sleep_until_interrupted(DEV_PROBE_INTERVAL);
        }
        return classify_langgraph_outcome(
            child
                .wait(&interrupt)
                .context("failed while waiting for langgraph dev")?,
        );
    }

    let mut watched_local_roots = local_package_roots(&project)?;
    let mut watcher = ProjectWatcher::start(&project.root, &watched_local_roots)?;
    let mut features = DevWatchFeatures {
        has_skills: summary.has_skills,
        needs_developer_principal,
        schedule_crons: summary.schedule_crons,
        graphs_changed: false,
    };
    let mut classify_ctx = WatchClassifyContext::from_agent_file(&project.agent_file);
    let mut hub_fingerprint = hub_inputs_fingerprint(&project.root, &classify_ctx);

    loop {
        if let Some(outcome) = child
            .poll(&interrupt)
            .context("failed to poll langgraph dev process")?
        {
            return classify_langgraph_outcome(outcome);
        }
        if let Some(sync) = schedules.as_mut() {
            sync.tick()?;
        }

        match watcher.recv_timeout(Duration::from_millis(250)) {
            WatchPoll::Timeout => continue,
            WatchPoll::Disconnected => break,
            WatchPoll::Paths(paths) => {
                let action = classify_watch_paths(&paths, &classify_ctx);
                let also_env = batch_includes_env_path(&paths);
                if action == WatchAction::Ignore && !also_env {
                    continue;
                }
                let mut restaged_env = false;
                let needed_developer_principal = features.needs_developer_principal;
                if action != WatchAction::Ignore {
                    match apply_watch_action(
                        action,
                        &opts,
                        &installation,
                        &out_dir,
                        &mut features,
                        &mut classify_ctx,
                        &mut hub_fingerprint,
                    ) {
                        Ok(()) => {
                            restaged_env = action == WatchAction::EnvRestage;
                            if let Some(sync) = schedules.as_mut() {
                                sync.rebuilt(&features.schedule_crons);
                            }
                            // A manifest edit can add or remove a local package.
                            if let Ok(roots) = local_package_roots(&project) {
                                if roots != watched_local_roots {
                                    watcher = ProjectWatcher::start(&project.root, &roots)?;
                                    watched_local_roots = roots;
                                }
                            }
                        }
                        Err(err) => {
                            // Keep serving the previous successful build.
                            let _ = log::error(format!(
                                "{err:#}\nCompile/sync failed — server still running previous build"
                            ));
                        }
                    }
                }
                // Env restage is orthogonal to hub/code actions; apply it even
                // when a stronger action won the coalesce (or when it is alone).
                if also_env && action != WatchAction::EnvRestage {
                    match apply_watch_action(
                        WatchAction::EnvRestage,
                        &opts,
                        &installation,
                        &out_dir,
                        &mut features,
                        &mut classify_ctx,
                        &mut hub_fingerprint,
                    ) {
                        Ok(()) => restaged_env = true,
                        Err(err) => {
                            let _ = log::error(format!(
                                "{err:#}\nEnv restage failed — server still running previous build"
                            ));
                        }
                    }
                }
                // The running server needs the newly staged developer principal.
                let gained_developer_principal =
                    !needed_developer_principal && features.needs_developer_principal;
                let graphs_changed = std::mem::take(&mut features.graphs_changed);
                if gained_developer_principal || restaged_env {
                    // Both CLIs inherit the loopback URL from the parent process.
                    // Node's dotenv loader preserves that value across worker
                    // reloads, so an env restage must replace the parent too.
                    let _ = log::info("Restarting LangGraph server for updated .env");
                    restart_langgraph_dev(&mut child, &command, &project, &out_dir)?;
                    if let Some(sync) = schedules.as_mut() {
                        sync.restarted();
                    }
                } else if graphs_changed {
                    let _ = log::info("Restarting LangGraph server for updated graphs");
                    restart_langgraph_dev(&mut child, &command, &project, &out_dir)?;
                    if let Some(sync) = schedules.as_mut() {
                        sync.restarted();
                    }
                }
            }
        }
    }

    // Ctrl-C or watcher disconnect: stop the child tree if it is still running.
    let _ = child.stop();
    Ok(CommandCompletion::Succeeded)
}

/// Where an auto-started dev server's output goes, relative to `.mda`.
const DEV_SERVER_LOG: &str = "dev-server.log";

/// How long an auto-started dev server gets to answer before we give up on it.
///
/// A project's first run also downloads the language's LangGraph CLI, so this
/// is a startup budget rather than a request timeout.
const DEV_START_TIMEOUT: Duration = Duration::from_secs(180);

/// How often a starting dev server is probed.
const DEV_PROBE_INTERVAL: Duration = Duration::from_millis(250);

/// How much of the log an error quotes when the server never came up.
const DEV_LOG_TAIL_LINES: usize = 20;

/// A dev server another command started and owns.
///
/// Its output goes to a log file instead of the terminal, so the owning
/// command keeps its transcript and a failure is still readable afterwards.
/// Dropping it takes the server down.
pub(crate) struct DevServer {
    child: OwnedChild,
    /// Origin the server listens on.
    pub(crate) api_url: String,
    /// File its stdout and stderr were redirected to.
    pub(crate) log_path: PathBuf,
}

impl Drop for DevServer {
    fn drop(&mut self) {
        let _ = self.child.stop();
    }
}

/// Whether a LangGraph dev server already answers at `api_url`.
///
/// `/ok` is the server's liveness probe. `/info` would also identify one, but
/// `http.disable_meta` turns that route off, and a server configured that way
/// still serves runs.
pub(crate) fn dev_server_is_up(api_url: &str) -> bool {
    let Ok(http) = crate::http::client_builder()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(5))
        .build()
    else {
        return false;
    };
    http.get(format!("{}/ok", api_url.trim_end_matches('/')))
        .send()
        .is_ok_and(|response| response.status().is_success())
}

/// A compiled project whose dev server has not started yet.
///
/// Preparing and launching are separate calls because preparing can stop to
/// ask for a LangSmith login or a missing workspace connection. A caller that
/// wants a spinner over the launch must not have one running over those.
pub(crate) struct PendingDevServer {
    project: Project,
    out_dir: PathBuf,
    command: DevCommand,
    log_path: PathBuf,
}

impl PendingDevServer {
    /// Where the server will listen once it is launched.
    pub(crate) fn api_url(&self) -> &str {
        &self.command.local_api_url
    }
}

/// Compile the project at `path` and stage everything its dev server needs.
///
/// This is the half that can prompt, so it runs with the terminal to itself.
pub(crate) fn prepare_background(path: &Path) -> Result<PendingDevServer> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(path)?;
    let out_dir = dev_out_dir(&project.root);
    let opts = DevOptions {
        path: project.root.clone(),
        port: None,
        hostname: None,
        no_browser: true,
        no_reload: false,
        tunnel: false,
        // The server lives as long as one chat; its schedules would not run.
        no_schedules: true,
    };

    prepare_dev(
        &installation,
        &project,
        &out_dir,
        &opts,
        Narration::Silent,
        BuildReuse::Keep,
    )?;

    let command = dev_command(project.language, &opts)?;
    let log_path = project.root.join(".mda").join(DEV_SERVER_LOG);
    Ok(PendingDevServer {
        project,
        out_dir,
        command,
        log_path,
    })
}

/// Start the prepared dev server and return once it answers. `None` means the
/// user interrupted the startup.
///
/// The mda watch loop is not part of this: the command that started the server
/// is holding the terminal, and reporting rebuilds into its transcript would
/// compete with it. Authors who want that run `mda dev` themselves.
pub(crate) fn launch_background(
    pending: PendingDevServer,
    interrupt: &Interrupt,
) -> Result<Option<DevServer>> {
    let PendingDevServer {
        project,
        out_dir,
        command,
        log_path,
    } = pending;

    let mut server = langgraph_dev_process(&command, &project, &out_dir)?;
    redirect_to_log(&mut server, &log_path)?;
    let mut child = OwnedChild::spawn(&mut server)
        .map_err(|err| dev_spawn_error(project.language, &command.program, err))?;

    // Bound before the match so the borrows of `child`, `command` and
    // `log_path` are over by the time the ready arm moves them.
    let serving = wait_until_serving(&mut child, &command.local_api_url, &log_path, interrupt)?;
    match serving {
        Serving::Yes => Ok(Some(DevServer {
            child,
            api_url: command.local_api_url,
            log_path,
        })),
        Serving::Interrupted => Ok(None),
    }
}

/// Send a dev server's stdout and stderr to `log_path`, truncating what a
/// previous run left there.
///
/// The log lives inside the project, so the repo controls that path: a clone
/// can ship `.mda/dev-server.log`, or `.mda` itself, as a symlink. Creating
/// the file would follow it and truncate whatever it points at — a shell rc
/// file, an authorized_keys — and fill it with server output the project can
/// influence. Refuse a symlink at either level instead of writing through one.
///
/// Checking and then creating leaves a window a local attacker could race.
/// Closing it needs `O_NOFOLLOW`, which is a new dependency for one call; the
/// check matches what the rest of the CLI does with untrusted project paths.
fn redirect_to_log(server: &mut Command, log_path: &Path) -> Result<()> {
    if let Some(parent) = log_path.parent() {
        // Before the create: a symlinked directory already exists, so
        // `create_dir_all` would accept it and say nothing.
        reject_symlink(parent)?;
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    reject_symlink(log_path)?;
    let log = fs::File::create(log_path)
        .with_context(|| format!("failed to create {}", log_path.display()))?;
    let errors = log
        .try_clone()
        .with_context(|| format!("failed to open {} for stderr", log_path.display()))?;
    server.stdout(Stdio::from(log)).stderr(Stdio::from(errors));
    Ok(())
}

/// Refuse a path the project supplied as a symlink.
///
/// A path that does not exist yet is fine: it is about to be created, and
/// nothing is being written through.
fn reject_symlink(path: &Path) -> Result<()> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(());
    };
    if !metadata.file_type().is_symlink() {
        return Ok(());
    }
    Err(categorized(
        ErrorCategory::Project,
        format!(
            "refusing to write the dev server log through the symlink at {}. \
             Writing it would truncate whatever the link points at. Remove the \
             link, then run `mda chat` again.",
            path.display()
        ),
    ))
}

enum Serving {
    Yes,
    Interrupted,
}

/// Poll a starting dev server until it answers, it dies, or the budget runs
/// out. The log is quoted in either failure, because nothing was on screen.
///
/// Every exit leaves the child stopped or owned by the caller: `poll` takes
/// the tree down on an interrupt or an exit, and the timeout does so here.
/// Otherwise a server that never came up would keep holding the port.
fn wait_until_serving(
    child: &mut OwnedChild,
    api_url: &str,
    log_path: &Path,
    interrupt: &Interrupt,
) -> Result<Serving> {
    let deadline = Instant::now() + DEV_START_TIMEOUT;
    loop {
        if let Some(outcome) = child
            .poll(interrupt)
            .context("failed to poll the dev server")?
        {
            return match outcome {
                Outcome::Stopped => Ok(Serving::Interrupted),
                Outcome::Exited(status) => Err(categorized(
                    ErrorCategory::Toolchain,
                    format!(
                        "The local dev server exited during startup ({status}).\n{}",
                        log_reference(log_path)
                    ),
                )),
            };
        }
        if dev_server_is_up(api_url) {
            return Ok(Serving::Yes);
        }
        if Instant::now() >= deadline {
            let _ = child.stop();
            return Err(categorized(
                ErrorCategory::Toolchain,
                format!(
                    "The local dev server did not answer at {api_url} within {}s.\n{}",
                    DEV_START_TIMEOUT.as_secs(),
                    log_reference(log_path)
                ),
            ));
        }
        interrupt.sleep_until_interrupted(DEV_PROBE_INTERVAL);
    }
}

/// Point at the log and quote its tail, so the failure is actionable without
/// a second run.
fn log_reference(log_path: &Path) -> String {
    let tail = log_tail(log_path, DEV_LOG_TAIL_LINES);
    if tail.is_empty() {
        return format!("Its output is in {}.", log_path.display());
    }
    format!("Last lines of {}:\n{tail}", log_path.display())
}

/// The last `lines` lines of a log file, or an empty string when it cannot be
/// read. Only the tail is kept, because a server log can be large.
fn log_tail(log_path: &Path, lines: usize) -> String {
    let Ok(mut file) = fs::File::open(log_path) else {
        return String::new();
    };
    let mut text = String::new();
    if file.read_to_string(&mut text).is_err() {
        return String::new();
    }
    let tail: Vec<&str> = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .rev()
        .take(lines)
        .collect();
    tail.into_iter().rev().collect::<Vec<_>>().join("\n")
}

/// Spawn the language-matching LangGraph CLI against `.mda/dev`.
fn spawn_langgraph_dev(
    command: &DevCommand,
    project: &Project,
    out_dir: &Path,
) -> Result<OwnedChild> {
    let mut server = langgraph_dev_process(command, project, out_dir)?;
    // Its own process group also isolates the supervisors and workers a reloading
    // server spawns, so a restart cannot leave one serving stale env.
    OwnedChild::spawn(&mut server)
        .map_err(|err| dev_spawn_error(project.language, &command.program, err))
}

fn langgraph_dev_process(
    command: &DevCommand,
    project: &Project,
    out_dir: &Path,
) -> Result<Command> {
    // LangGraph also loads staged `.env` via `langgraph.json` `"env": ".env"`,
    // but TypeScript workers have been observed without those vars — mirror
    // the LangSmith API key and endpoint as a belt-and-suspenders.
    let mut server = Command::new(&command.program);
    server.args(&command.args).current_dir(out_dir);
    let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
    if let Some(jobs) = dev_jobs_per_worker(&staged, std::env::var("N_JOBS_PER_WORKER").ok())? {
        server.arg("--n-jobs-per-worker").arg(jobs.to_string());
    }
    apply_staged_langsmith_env(&mut server, out_dir);
    apply_staged_public_api_url(&mut server, out_dir);
    server.env(
        MDA_CHANNEL_API_URL_ENV,
        ensure_dev_loopback_api_url(out_dir, &command.local_api_url)?,
    );

    // Opt the managed auth runtime into local-dev identity: Studio and bare API
    // clients can exercise `trusted_backend` without injecting ingress headers.
    // Deploy never sets this.
    server.env("MDA_LOCAL_DEV", "1");

    // For TypeScript projects, run Node with `--preserve-symlinks` so a
    // symlinked / `file:`-linked `managed-deepagents` (local SDK development or
    // `npm link`) resolves its `deepagents` peer from the *project's*
    // `node_modules`. Without it, Node canonicalizes the runtime to the SDK's
    // real path (outside the project) and fails to find `deepagents`. A normally
    // installed, hoisted `managed-deepagents` is unaffected by this flag.
    if project.language == Language::TypeScript {
        server.env(
            "NODE_OPTIONS",
            node_options_with_preserve_symlinks(std::env::var("NODE_OPTIONS").ok()),
        );
    }

    Ok(server)
}

fn dev_jobs_per_worker(
    staged: &std::collections::BTreeMap<String, String>,
    process_value: Option<String>,
) -> Result<Option<usize>> {
    process_value
        .filter(|value| !value.trim().is_empty())
        .or_else(|| staged.get("N_JOBS_PER_WORKER").cloned())
        .filter(|value| !value.trim().is_empty())
        .map(|value| {
            value
                .trim()
                .parse()
                .context("N_JOBS_PER_WORKER must be a non-negative integer")
        })
        .transpose()
}

fn restart_langgraph_dev(
    child: &mut OwnedChild,
    command: &DevCommand,
    project: &Project,
    out_dir: &Path,
) -> Result<()> {
    // The respawn is the response either way, so its parting status is moot.
    let _ = child.stop().context("failed to stop langgraph dev")?;
    *child = spawn_langgraph_dev(command, project, out_dir)?;
    Ok(())
}

/// A stop we sent is the user's Ctrl-C. Any status that arrives instead is the
/// server's own verdict, reported as it stands.
fn classify_langgraph_outcome(outcome: Outcome) -> Result<CommandCompletion> {
    let status = match outcome {
        Outcome::Stopped => return Ok(CommandCompletion::Succeeded),
        Outcome::Exited(status) => status,
    };
    if status.success() {
        return Ok(CommandCompletion::Succeeded);
    }
    let reason = match status.code() {
        Some(code) => format!("langgraph dev exited with status {code}"),
        None => "langgraph dev was terminated by a signal".to_string(),
    };
    Err(categorized(ErrorCategory::Toolchain, reason))
}

/// Apply a classified watch action. Compile failures propagate so the caller
/// can keep the previous build running.
struct DevWatchFeatures {
    has_skills: bool,
    needs_developer_principal: bool,
    /// What the last successful compile produced from `schedules/`.
    schedule_crons: compile::schedules::ScheduleCrons,
    /// A rebuild added or removed a graph, which `langgraph dev` only reads at
    /// startup.
    graphs_changed: bool,
}

/// Graph keys in a `langgraph.json` manifest.
fn langgraph_graph_keys(manifest: Option<&[u8]>) -> Vec<String> {
    manifest
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(bytes).ok())
        .and_then(|value| {
            value
                .get("graphs")?
                .as_object()
                .map(|graphs| graphs.keys().cloned().collect())
        })
        .unwrap_or_default()
}

fn apply_watch_action(
    action: WatchAction,
    opts: &DevOptions,
    installation: &Installation,
    out_dir: &Path,
    features: &mut DevWatchFeatures,
    classify_ctx: &mut WatchClassifyContext,
    hub_fingerprint: &mut String,
) -> Result<()> {
    match action {
        WatchAction::Ignore => Ok(()),
        WatchAction::EnvRestage => {
            let project = installation.load_project(&opts.path)?;
            let assistant_id = agent_config::agent_assistant_id(&project)?;
            stage_dev_env_file(
                &project.root,
                out_dir,
                &assistant_id,
                &local_dev_public_api_url(opts),
            )?;
            let needs_api_key =
                compile::project_uses_sandbox(&project)? || features.needs_developer_principal;
            ensure_dev_langsmith_env(
                &project.root,
                out_dir,
                needs_api_key,
                DevLoginPrompt::Suppressed,
            )?;
            warn_if_dev_langsmith_api_key_missing(out_dir, needs_api_key)?;
            ensure_dev_developer_principal(out_dir, features.needs_developer_principal)?;
            ensure_dev_tracing_project(out_dir, &assistant_id)?;
            ensure_dev_sandbox_recipe(
                &project,
                out_dir,
                &assistant_id,
                DevLoginPrompt::Suppressed,
            )?;
            let _ = log::info("Restaged .env → .mda/dev");
            Ok(())
        }
        WatchAction::HubSync | WatchAction::HubSyncReload => {
            let project = installation.load_project(&opts.path)?;
            *classify_ctx = WatchClassifyContext::from_agent_file(&project.agent_file);
            let next_fingerprint = hub_inputs_fingerprint(&project.root, classify_ctx);
            if next_fingerprint == *hub_fingerprint {
                // Spurious watcher noise with unchanged hub inputs — do nothing.
                return Ok(());
            }
            let memory = compile::resolve_memory(&project)?;
            let hub = crate::local_context_hub::ensure_local_context_hub(
                &project,
                memory.agent_enabled(),
            )?;
            if hub.has_skills != features.has_skills {
                // `hasSkills` is baked into the generated entry — escalate.
                features.has_skills = hub.has_skills;
                return apply_watch_action(
                    WatchAction::Recompile,
                    opts,
                    installation,
                    out_dir,
                    features,
                    classify_ctx,
                    hub_fingerprint,
                );
            }
            features.has_skills = hub.has_skills;
            *hub_fingerprint = next_fingerprint;
            let _ = log::info("Synced instructions/skills → .mda/__contexthub__");
            if action == WatchAction::HubSyncReload {
                poke_reload_signal(out_dir, project.language)?;
            }
            Ok(())
        }
        WatchAction::Recompile => {
            let _ = log::info("Rebuilding…");
            let project = installation.load_project(&opts.path)?;
            *classify_ctx = WatchClassifyContext::from_agent_file(&project.agent_file);
            // Capture pre-refresh langgraph.json so TypeScript can suppress a
            // chokidar restart while npm install mutates node_modules.
            let langgraph_path = out_dir.join("langgraph.json");
            let prior_langgraph = fs::read(&langgraph_path).ok();
            let prior_langgraph_mtime = fs::metadata(&langgraph_path)
                .and_then(|meta| meta.modified())
                .ok();
            let summary =
                compile::compile_refresh(&project, out_dir, installation.runtime_source())
                    .map_err(compile::compilation)?;
            report_compile_warnings(&summary);
            features.has_skills = summary.has_skills;
            features.schedule_crons = summary.schedule_crons.clone();
            *hub_fingerprint = hub_inputs_fingerprint(&project.root, classify_ctx);
            // Only Python builds async subagent graphs, so only Python graph sets change.
            features.graphs_changed = project.language == Language::Python
                && langgraph_graph_keys(prior_langgraph.as_deref())
                    != langgraph_graph_keys(fs::read(&langgraph_path).ok().as_deref());

            // TypeScript: if deps are stale, stash the freshly written
            // langgraph.json and restore the previous one immediately so
            // chokidar does not restart into a mutating node_modules tree.
            let mut pending_langgraph: Option<Vec<u8>> = None;
            if project.language == Language::TypeScript && typescript_deps_stale(out_dir)? {
                pending_langgraph = fs::read(&langgraph_path).ok();
                restore_langgraph_json(
                    &langgraph_path,
                    prior_langgraph.as_deref(),
                    prior_langgraph_mtime,
                )?;
            }

            stage_dev_env_file(
                &project.root,
                out_dir,
                &summary.assistant_id,
                &local_dev_public_api_url(opts),
            )?;
            let connector_requirements = compile::emit_connector_requirements_for_entries(
                &project,
                out_dir,
                &summary.connectors,
            )?;
            features.needs_developer_principal = summary.memory.user_enabled()
                || connectors_need_developer_principal(&connector_requirements);
            let needs_api_key = summary.sandbox || features.needs_developer_principal;
            ensure_dev_langsmith_env(
                &project.root,
                out_dir,
                needs_api_key,
                DevLoginPrompt::Suppressed,
            )?;
            warn_if_dev_langsmith_api_key_missing(out_dir, needs_api_key)?;
            ensure_dev_developer_principal(out_dir, features.needs_developer_principal)?;
            ensure_dev_workspace_connections(out_dir, &connector_requirements)?;
            ensure_dev_tracing_project(out_dir, &summary.assistant_id)?;
            ensure_dev_sandbox_recipe(
                &project,
                out_dir,
                &summary.assistant_id,
                DevLoginPrompt::Suppressed,
            )?;
            ensure_dev_handoff_secret(out_dir)?;
            ensure_dev_ingress_secret(&project, out_dir)?;
            ensure_dev_public_api_url(out_dir, opts)?;
            ensure_dev_assistant_id(&project, out_dir)?;
            match project.language {
                Language::TypeScript => {
                    let installed = install_typescript_build_deps(out_dir)?;
                    if let Some(bytes) = pending_langgraph.as_deref() {
                        fs::write(&langgraph_path, bytes).with_context(|| {
                            format!("failed to write {}", langgraph_path.display())
                        })?;
                    }
                    // Poke after install so a premature tsx restart (from
                    // source copies) re-imports once deps are ready.
                    if matches!(installed, DepsInstall::Installed) || pending_langgraph.is_some() {
                        poke_reload_signal(out_dir, Language::TypeScript)?;
                    }
                }
                Language::Python => {
                    // Sync before poke: watchfiles may reload during refresh
                    // while site-packages is still stale.
                    if matches!(install_python_build_deps(out_dir)?, DepsInstall::Installed) {
                        poke_reload_signal(out_dir, Language::Python)?;
                    }
                }
            }
            let _ = log::info(format!(
                "Rebuilt {} file(s) → .mda/dev",
                summary.copied.len()
            ));
            Ok(())
        }
    }
}

/// Outcome of installing managed TypeScript deps into `.mda/dev`.
enum DepsInstall {
    SkippedNoPackage,
    UpToDate,
    Installed,
}

/// Surface compile advisories once the compile spinner has stopped, so they do
/// not race its steady tick.
fn report_compile_warnings(summary: &CompileSummary) {
    for warning in &summary.warnings {
        let _ = log::warning(warning);
    }
}

/// Print the omit-when-absent feature summary for a compiled project.
fn report_project_features(
    project: &Project,
    summary: &CompileSummary,
    provision_schedules: bool,
) -> Result<()> {
    let connector_labels = connector_summary_labels(project, &summary.connectors)?;
    let needs_public_url = compile::project_needs_events_public_url(project)?;
    let public_url = read_dev_public_api_url(project);
    for line in feature_summary_lines(
        project,
        summary,
        &connector_labels,
        Some(public_url),
        needs_public_url,
        provision_schedules,
    ) {
        log::info(line)?;
    }
    Ok(())
}

/// Format the feature lines shown after a successful compile (pure; unit-tested).
fn feature_summary_lines(
    project: &Project,
    summary: &CompileSummary,
    connector_labels: &[String],
    public_url: Option<PublicApiUrl>,
    needs_public_url: bool,
    provision_schedules: bool,
) -> Vec<String> {
    let mut lines = Vec::new();
    lines.push(format!(
        "Agent          {} ({})",
        summary.assistant_id,
        project.agent_file.display()
    ));
    lines.extend(format_identity_lines(summary.identity.as_ref()));
    lines.push(format_memory_line(&summary.memory));
    if let Some(connectors) = format_connectors_line(connector_labels) {
        lines.push(connectors);
    }
    if let Some(schedules) = format_schedules_line(&summary.schedule_crons, provision_schedules) {
        lines.push(schedules);
    }
    if summary.sandbox {
        lines.push("Sandbox        LangSmith (provisioned on first use)".to_string());
    }
    if summary.has_skills {
        lines.push("Skills         synced from skills/".to_string());
    }
    lines.extend(crate::deploy::channel_setup::dev_notes(
        &summary.channel_providers,
    ));
    if let Some(line) = format_public_url_line(public_url.as_ref(), needs_public_url) {
        lines.push(line);
    }
    lines.push("Context        .mda/__contexthub__ (local mock)".to_string());
    lines
}

/// Lines naming the declared memory scope and file. Never memory contents:
/// hot memory is model-visible customer content and has no place in CLI output.
fn format_memory_line(memory: &MemoryDecision) -> String {
    match memory {
        MemoryDecision::Declared(summary) => {
            let scope = match (summary.scope.agent, summary.scope.user) {
                (true, true) => "agent, user",
                (true, false) => "agent",
                (false, true) => "user",
                (false, false) => "none",
            };
            format!(
                "Memory         {scope}\n│                 {}",
                summary.path.display()
            )
        }
        MemoryDecision::None => "Memory         off — no memory declaration".to_string(),
    }
}

fn format_identity_lines(identity: Option<&IdentitySummary>) -> Vec<String> {
    let Some(identity) = identity else {
        return vec!["Identity       none — no managed auth".to_string()];
    };
    let mut lines = vec![format!("Identity       {}", identity.path.display())];
    if let Some(auth) = identity.auth {
        lines.push(format!("│                 auth={}", auth.label()));
    }
    if let Some(scope) = &identity.scope {
        lines.push(format!(
            "│                 credentials={}",
            scope.credentials
        ));
    }
    // One cliclack log entry so the Studio hint does not get its own bullet.
    lines.push("│                 local Studio uses MDA_LOCAL_DEV".to_string());
    vec![lines.join("\n")]
}

/// Label each connector by instance stem, annotating when the factory differs
/// (e.g. `notion.ts` exporting `defineMcp()` → `notion (mcp)`).
fn connector_summary_labels(project: &Project, connectors: &[PathBuf]) -> Result<Vec<String>> {
    let mut labels = Vec::with_capacity(connectors.len());
    for path in connectors {
        let stem = path
            .file_stem()
            .and_then(|stem| stem.to_str())
            .unwrap_or("connector");
        let label = match compile::read_connector_factory(project, path)? {
            Some(factory) if !factory.eq_ignore_ascii_case(stem) => {
                format!("{stem} ({factory})")
            }
            _ => stem.to_string(),
        };
        labels.push(label);
    }
    labels.sort_unstable();
    Ok(labels)
}

fn format_connectors_line(labels: &[String]) -> Option<String> {
    if labels.is_empty() {
        return None;
    }
    Some(format!("Connectors     {}", labels.join(", ")))
}

fn format_public_url_line(url: Option<&PublicApiUrl>, needs_public_url: bool) -> Option<String> {
    match url {
        Some(PublicApiUrl::Https(url)) => Some(format!("Public URL     {url}")),
        Some(PublicApiUrl::NotHttps(url)) if needs_public_url => Some(format!(
            "Public URL     {url} (must be https:// for Slack/GitHub Events)"
        )),
        Some(PublicApiUrl::Missing) | None if needs_public_url => Some(format!(
            "Public URL     not set — Events connectors need {MDA_PUBLIC_API_URL_ENV}=https://…"
        )),
        _ => None,
    }
}

/// Resolve `MDA_PUBLIC_API_URL` from the project env the same way Slack soft-skip does.
fn read_dev_public_api_url(project: &Project) -> PublicApiUrl {
    let mut env_vars = deploy::env_file::parse_env_file(&project.root.join(".env"));
    if env_vars.is_empty() {
        env_vars = deploy::env_file::parse_env_file(&project.root.join(".env.local"));
    }
    if let Ok(value) = std::env::var(MDA_PUBLIC_API_URL_ENV) {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            env_vars.insert(MDA_PUBLIC_API_URL_ENV.to_string(), trimmed.to_string());
        }
    }
    resolve_https_public_api_url(&env_vars)
}

/// Name each compiled schedule with its cron expression, and say when they
/// will not run locally.
fn format_schedules_line(
    crons: &compile::schedules::ScheduleCrons,
    provisioned: bool,
) -> Option<String> {
    if crons.is_empty() {
        return None;
    }
    let parts: Vec<String> = crons
        .iter()
        .map(|(name, value)| {
            let cron = value
                .get("schedule")
                .and_then(|v| v.as_str())
                .unwrap_or("?");
            format!("{name} ({cron})")
        })
        .collect();
    let state = if provisioned {
        ""
    } else {
        "\n│                 not provisioned locally (--no-schedules)"
    };
    Some(format!("Schedules      {}{state}", parts.join(", ")))
}

/// Whether the TypeScript build's install stamp is behind the current manifests.
fn typescript_deps_stale(out_dir: &Path) -> Result<bool> {
    if !out_dir.join("package.json").is_file() {
        return Ok(false);
    }
    let fingerprint = typescript_deps_fingerprint(out_dir)?;
    let stamp_path = out_dir.join(DEPS_STAMP);
    let node_modules = out_dir.join("node_modules");
    Ok(!(node_modules.is_dir()
        && fs::read_to_string(&stamp_path)
            .ok()
            .is_some_and(|stamp| stamp.trim() == fingerprint)))
}

fn restore_langgraph_json(
    path: &Path,
    prior: Option<&[u8]>,
    prior_mtime: Option<std::time::SystemTime>,
) -> Result<()> {
    let Some(bytes) = prior else {
        return Ok(());
    };
    fs::write(path, bytes).with_context(|| format!("failed to restore {}", path.display()))?;
    if let Some(mtime) = prior_mtime {
        let file = fs::File::options()
            .write(true)
            .open(path)
            .with_context(|| format!("failed to open {}", path.display()))?;
        file.set_modified(mtime)
            .with_context(|| format!("failed to restore mtime for {}", path.display()))?;
    }
    Ok(())
}

/// Install the compiled TypeScript build's dependency stack into `.mda/dev`.
///
/// Authors only declare `managed-deepagents` (+ their extras) at the project
/// root. `mda compile` injects the managed LangGraph stack into the build
/// `package.json` and materializes a lockfile — the same artifacts the remote
/// deploy builder installs with `npm ci`. Local `mda dev` installs them here
/// so Node resolves `@langchain/langgraph` and peers from `.mda/dev` without
/// requiring authors to mirror the managed stack in their project manifest.
///
/// Cold start (no `node_modules`) uses `npm ci` to match deploy. Watch rebuilds
/// with an existing tree use `npm install` so a live LangGraph worker is not
/// racing an emptied `node_modules` (`npm ci` deletes the tree first).
///
/// Skips reinstall when `node_modules` already matches the current
/// `package.json` + `package-lock.json` fingerprint.
fn install_typescript_build_deps(out_dir: &Path) -> Result<DepsInstall> {
    if !out_dir.join("package.json").is_file() {
        return Ok(DepsInstall::SkippedNoPackage);
    }

    let fingerprint = typescript_deps_fingerprint(out_dir)?;
    let stamp_path = out_dir.join(DEPS_STAMP);
    let node_modules = out_dir.join("node_modules");
    let has_modules = node_modules.is_dir();
    if has_modules
        && fs::read_to_string(&stamp_path)
            .ok()
            .is_some_and(|stamp| stamp.trim() == fingerprint)
    {
        return Ok(DepsInstall::UpToDate);
    }

    let sp = spinner();
    sp.start("Installing managed dependencies into .mda/dev");

    // Prefer non-destructive install when a tree already exists (watch reload).
    if has_modules {
        let output = toolchain::npm()
            .args(["install", "--omit=dev"])
            .current_dir(out_dir)
            .output()
            .context("failed to run npm install in .mda/dev")?;
        if output.status.success() {
            let fingerprint = typescript_deps_fingerprint(out_dir)?;
            write_deps_stamp(out_dir, &fingerprint)?;
            sp.stop("Installed managed dependencies into .mda/dev");
            let _ = log::info("Installed managed dependencies into .mda/dev");
            return Ok(DepsInstall::Installed);
        }
        sp.error("Could not install managed dependencies");
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!("failed to install managed dependencies in .mda/dev\nnpm install:\n{stderr}");
    }

    let output = toolchain::npm()
        .args(["ci", "--omit=dev"])
        .current_dir(out_dir)
        .output()
        .context("failed to run npm ci in .mda/dev")?;

    if output.status.success() {
        write_deps_stamp(out_dir, &fingerprint)?;
        sp.stop("Installed managed dependencies into .mda/dev");
        // Spinner frames are easy to miss when stdout is piped (tests/CI);
        // keep a durable log line for watch assertions.
        let _ = log::info("Installed managed dependencies into .mda/dev");
        return Ok(DepsInstall::Installed);
    }

    // Fall back when the lockfile is unusable (rare locally after a partial
    // compile). Prefer `npm ci` above so cold start matches the deploy path.
    let ci_stderr = String::from_utf8_lossy(&output.stderr);
    let fallback = toolchain::npm()
        .args(["install", "--omit=dev"])
        .current_dir(out_dir)
        .output()
        .context("failed to run npm install in .mda/dev")?;
    if fallback.status.success() {
        // Regenerated lockfile — stamp the post-install fingerprint.
        let fingerprint = typescript_deps_fingerprint(out_dir)?;
        write_deps_stamp(out_dir, &fingerprint)?;
        sp.stop("Installed managed dependencies into .mda/dev");
        let _ = log::info("Installed managed dependencies into .mda/dev");
        return Ok(DepsInstall::Installed);
    }

    sp.error("Could not install managed dependencies");
    let install_stderr = String::from_utf8_lossy(&fallback.stderr);
    bail!(
        "failed to install managed dependencies in .mda/dev\n\
         npm ci:\n{ci_stderr}\n\
         npm install:\n{install_stderr}"
    );
}

/// Sync the compiled Python build's dependency stack into `.mda/dev/.venv`.
///
/// Startup uses `uv run`, which syncs once before launching LangGraph. Watch
/// rebuilds must sync explicitly — the long-lived `uv run` process does not
/// re-resolve when `pyproject.toml` changes under it.
///
/// Skips when `.venv` already matches the current `pyproject.toml` + `uv.lock`
/// fingerprint.
fn install_python_build_deps(out_dir: &Path) -> Result<DepsInstall> {
    if !out_dir.join("pyproject.toml").is_file() {
        return Ok(DepsInstall::SkippedNoPackage);
    }

    let fingerprint = python_deps_fingerprint(out_dir)?;
    let stamp_path = out_dir.join(DEPS_STAMP);
    let venv = out_dir.join(".venv");
    if venv.is_dir()
        && fs::read_to_string(&stamp_path)
            .ok()
            .is_some_and(|stamp| stamp.trim() == fingerprint)
    {
        return Ok(DepsInstall::UpToDate);
    }

    let sp = spinner();
    sp.start("Syncing Python dependencies into .mda/dev");
    let output = Command::new("uv")
        .args(["sync"])
        .current_dir(out_dir)
        .output()
        .context("failed to run uv sync in .mda/dev")?;

    if output.status.success() {
        // Lockfile may be created/updated by sync — stamp the post-sync state.
        let fingerprint = python_deps_fingerprint(out_dir)?;
        write_deps_stamp(out_dir, &fingerprint)?;
        sp.stop("Synced Python dependencies into .mda/dev");
        // Spinner frames are easy to miss when stdout is piped (tests/CI);
        // keep a durable log line for watch assertions.
        let _ = log::info("Synced Python dependencies into .mda/dev");
        return Ok(DepsInstall::Installed);
    }

    sp.error("Could not sync Python dependencies");
    let stderr = String::from_utf8_lossy(&output.stderr);
    bail!("failed to sync Python dependencies in .mda/dev\n{stderr}");
}

/// Fingerprint of the TypeScript build manifests that drive `npm ci`.
fn typescript_deps_fingerprint(out_dir: &Path) -> Result<String> {
    hash_dep_manifests(out_dir, &["package.json", "package-lock.json"])
}

/// Source directories of the local packages `mda dev` watches besides the root.
fn local_package_roots(project: &Project) -> Result<Vec<PathBuf>> {
    Ok(compile::find_local_packages(project)?
        .into_iter()
        .map(|package| package.source)
        .collect())
}

/// Fingerprint of the Python build manifests that drive `uv sync`.
fn python_deps_fingerprint(out_dir: &Path) -> Result<String> {
    let mut names = vec![PathBuf::from("pyproject.toml"), PathBuf::from("uv.lock")];
    names.extend(compile::vendored_manifests(out_dir, "pyproject.toml"));
    hash_dep_manifests(out_dir, &names)
}

fn hash_dep_manifests(out_dir: &Path, names: &[impl AsRef<Path>]) -> Result<String> {
    let mut hasher = DefaultHasher::new();
    for name in names {
        let name = name.as_ref();
        let path = out_dir.join(name);
        let contents = if path.is_file() {
            fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?
        } else {
            Vec::new()
        };
        name.hash(&mut hasher);
        contents.hash(&mut hasher);
    }
    Ok(format!("{:x}", hasher.finish()))
}

fn write_deps_stamp(out_dir: &Path, fingerprint: &str) -> Result<()> {
    fs::write(out_dir.join(DEPS_STAMP), format!("{fingerprint}\n"))
        .with_context(|| format!("failed to write {} in {}", DEPS_STAMP, out_dir.display()))
}

/// Stage the project root env file where `langgraph dev` expects it.
///
/// Prefers `.env`, then falls back to `.env.local` so Vite-first example projects
/// (which often keep secrets in `.env.local`) still work with `mda dev`.
/// Uses `assistant_id` to preserve the generated tracing project while removing
/// a custom tracing project deleted from the source env. Resolve the final local
/// URL before merging so source-only rebuilds do not rewrite the watched file.
fn stage_dev_env_file(
    root: &Path,
    out_dir: &Path,
    assistant_id: &str,
    local_api_url: &str,
) -> Result<()> {
    let dst = out_dir.join(".env");
    let src = [".env", ".env.local"]
        .into_iter()
        .map(|name| root.join(name))
        .find(|path| path.is_file());

    let mut incoming = src
        .as_deref()
        .map(crate::deploy::env_file::parse_env_file)
        .unwrap_or_default();
    // Resolve from source settings before merging. A removed override must not
    // be recovered from the previous build, and an unchanged URL must not be
    // removed and re-added while LangGraph watches the staged env file.
    let loopback_url =
        dev_loopback_api_url(local_api_url, &incoming, |key| std::env::var(key).ok());
    incoming.insert(MDA_CHANNEL_API_URL_ENV.to_string(), loopback_url.clone());
    if let Some(src) = src.as_ref().filter(|_| !dst.is_file()) {
        fs::copy(src, &dst)
            .with_context(|| format!("failed to copy {} to {}", src.display(), dst.display()))?;
        return crate::deploy::env_file::set_env_key(&dst, MDA_CHANNEL_API_URL_ENV, &loopback_url);
    }
    let staged = crate::deploy::env_file::parse_env_file(&dst);
    let source_selects_tracing_project = [LANGSMITH_PROJECT_ENV, LANGCHAIN_PROJECT_ENV]
        .into_iter()
        .any(|key| {
            incoming
                .get(key)
                .map(|value| value.trim())
                .is_some_and(|value| !value.is_empty())
        });
    let preserve_generated_tracing_project = !source_selects_tracing_project
        && staged
            .get(LANGSMITH_PROJECT_ENV)
            .map(|value| value.trim())
            .is_some_and(|value| value == format!("{assistant_id}-local"));
    for (key, value) in &staged {
        // Keep injected values while a source env exists. If it disappears,
        // remove all old settings except the newly resolved loopback URL.
        if incoming.contains_key(key)
            || (src.is_some()
                && (key.starts_with("MDA_")
                    || (key == LANGSMITH_PROJECT_ENV && preserve_generated_tracing_project)
                    || crate::deploy::is_cli_session_env_entry(root, key, value)))
        {
            continue;
        }
        crate::deploy::env_file::remove_env_key(&dst, key)?;
    }
    for (key, value) in incoming {
        if key == LANGSMITH_PROJECT_ENV
            && value.trim().is_empty()
            && preserve_generated_tracing_project
        {
            continue;
        }
        // A blank project-side declaration must not clobber a session
        // credential the CLI staged for this run.
        if value.trim().is_empty()
            && staged.get(&key).is_some_and(|staged_value| {
                crate::deploy::is_cli_session_env_entry(root, &key, staged_value)
            })
        {
            continue;
        }
        crate::deploy::env_file::set_env_key(&dst, &key, &value)?;
    }

    Ok(())
}

const LANGSMITH_PROJECT_ENV: &str = "LANGSMITH_PROJECT";
const LANGCHAIN_PROJECT_ENV: &str = "LANGCHAIN_PROJECT";
const MDA_AGENT_AUTH_PRINCIPAL_ID_ENV: &str = "MDA_AGENT_AUTH_PRINCIPAL_ID";

/// Keys mirrored from the process environment into the staged `.env`.
const DEV_LANGSMITH_STAGED_ENV_KEYS: &[&str] = &[
    API_KEY_ENV,
    LANGSMITH_ENDPOINT_ENV,
    WORKSPACE_ID_ENV,
    LANGSMITH_PROJECT_ENV,
    LANGCHAIN_PROJECT_ENV,
];

/// Keys injected into the long-lived `langgraph dev` server process.
const DEV_LANGSMITH_SERVER_ENV_KEYS: &[&str] = &[
    API_KEY_ENV,
    LANGSMITH_ENDPOINT_ENV,
    WORKSPACE_ID_ENV,
    MDA_AGENT_AUTH_PRINCIPAL_ID_ENV,
];

/// Whether the current `mda dev` phase is allowed to prompt for LangSmith login.
///
/// Only startup may prompt. A watch rebuild runs while the server is already
/// serving, so a prompt there would render into the build-log stream and block
/// the watch loop on stdin the developer never asked for.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum DevLoginPrompt {
    Allowed,
    Suppressed,
}

/// Whether `mda dev` may prompt for LangSmith login.
///
/// Both stdin and stderr must be TTYs so redirected runs (for example
/// `mda dev 2>dev.log`) fail with a missing-key error instead of blocking on
/// an invisible prompt.
fn dev_langsmith_login_is_interactive(needs_api_key: bool, prompt: DevLoginPrompt) -> bool {
    needs_api_key
        && prompt == DevLoginPrompt::Allowed
        && std::io::stdin().is_terminal()
        && std::io::stderr().is_terminal()
}

/// Fill omitted LangSmith settings in the staged env from available local sources.
///
/// Order: mirror a set process env value (the shell wins over the project
/// `.env`, as everywhere else in the CLI); else keep the staged value. When the
/// project declares a sandbox and the CLI is interactive, resolve or prompt for
/// `LANGSMITH_API_KEY` the same way as deploy (env → cache → sign-in). Non-TTY
/// runs and watch rebuilds stay soft: mirror env/cache when present, and only
/// fail closed later if a sandbox recipe needs baking.
fn ensure_dev_langsmith_env(
    project_root: &Path,
    out_dir: &Path,
    needs_api_key: bool,
    prompt: DevLoginPrompt,
) -> Result<()> {
    let staged = out_dir.join(".env");
    // Compare against the project file, not the staged copy: the staged one
    // also holds values the CLI wrote itself (e.g. `LANGSMITH_PROJECT`).
    let project_env = crate::deploy::env_file::parse_env_file(
        &crate::deploy::env_file::project_env_persist_path(project_root),
    );
    for key in DEV_LANGSMITH_STAGED_ENV_KEYS {
        // The API key has its own resolver below (session PATs need care).
        if key == &API_KEY_ENV {
            continue;
        }
        // `set_env_key` no-ops on an unchanged value, so restages do not wake
        // LangGraph's `.env` watcher.
        if let Ok(value) = std::env::var(key) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                crate::langsmith::warn_if_shell_shadows_env_file(key, trimmed, &project_env);
                crate::deploy::env_file::set_env_key(&staged, key, trimmed)?;
            }
        }
    }
    deploy::ensure_project_langsmith_api_key(
        project_root,
        Some(out_dir),
        dev_langsmith_login_is_interactive(needs_api_key, prompt),
    )
}

/// Whether the dev server will see a `LANGSMITH_API_KEY`, either staged in
/// `.mda/dev/.env` or inherited from the `mda` process environment.
fn dev_langsmith_api_key_available(out_dir: &Path) -> bool {
    let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
    staged
        .get(API_KEY_ENV)
        .is_some_and(|value| !value.trim().is_empty())
        || std::env::var(API_KEY_ENV).is_ok_and(|value| !value.trim().is_empty())
}

/// Watch rebuilds cannot prompt for LangSmith login, so a project that newly
/// needs a key (for example a just-added sandbox) would otherwise reload into
/// a server whose sandbox calls hang without credentials. Say so instead.
fn warn_if_dev_langsmith_api_key_missing(out_dir: &Path, needs_api_key: bool) -> Result<()> {
    if !needs_api_key || dev_langsmith_api_key_available(out_dir) {
        return Ok(());
    }
    log::warning(format!(
        "This project now needs {API_KEY_ENV} (sandbox or user-scoped features), but none is \
         available, so sandbox requests will fail. Add {API_KEY_ENV} to .env (the server \
         restarts automatically) or restart `mda dev` to sign in with your browser."
    ))?;
    Ok(())
}

/// Verify declared connector connections exist in the workspace before dev starts.
fn ensure_dev_workspace_connections(
    out_dir: &Path,
    connector_requirements: &compile::ConnectorRequirements,
) -> Result<()> {
    use crate::deploy::helpers::{REMOVED_WORKSPACE_ID_ENVS, WORKSPACE_ID_ENV};
    use crate::langsmith::resolve_preferred_env;

    if compile::referenced_connection_slugs(connector_requirements).is_empty() {
        return Ok(());
    }

    let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
    let api_key = env
        .get(API_KEY_ENV)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            std::env::var(API_KEY_ENV)
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        });
    let Some(api_key) = api_key else {
        bail!(
            "This project declares connector connections, so `mda dev` needs {API_KEY_ENV} to \
             verify they exist in the workspace. Add the key to .env (or export it), then run \
             `mda dev` again."
        );
    };

    let (host_url, _) = resolve_langsmith_api_bases(&env);
    let platform = resolve_platform_api_url(&host_url, &env);
    let workspace_id = resolve_preferred_env(
        WORKSPACE_ID_ENV,
        REMOVED_WORKSPACE_ID_ENVS,
        None,
        &env,
        |key| std::env::var(key).ok(),
        ". Set LANGSMITH_WORKSPACE_ID in .env (or export it)",
    )?;
    let client = crate::langsmith::Client::with_credential_and_platform(
        &host_url,
        Some(platform.as_str()),
        Credential::ApiKey(api_key),
        workspace_id.clone(),
    )?;

    deploy::ensure_workspace_connections(
        &client,
        workspace_id.as_deref(),
        connector_requirements,
        &[],
    )
}

fn connectors_need_developer_principal(requirements: &compile::ConnectorRequirements) -> bool {
    !compile::user_connections_from_connector_requirements(requirements).is_empty()
        || requirements.values().any(|manifest| {
            manifest
                .get("needsDeveloperPrincipal")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
        })
}

fn ensure_dev_developer_principal(out_dir: &Path, needed: bool) -> Result<()> {
    if !needed {
        return Ok(());
    }

    let staged = out_dir.join(".env");
    let env = crate::deploy::env_file::parse_env_file(&staged);
    let api_key = env
        .get(API_KEY_ENV)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .context(
            "user-owned connections, managed tools, and user memory require LANGSMITH_API_KEY under `mda dev`",
        )?;
    let (host_url, _) = resolve_langsmith_api_bases(&env);
    let platform = resolve_platform_api_url(&host_url, &env);
    let workspace_id = env
        .get(WORKSPACE_ID_ENV)
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let client = crate::langsmith::Client::with_credential_and_platform(
        &host_url,
        Some(platform.as_str()),
        Credential::ApiKey(api_key.to_string()),
        workspace_id,
    )?;
    let developer = client.resolve_developer_principal()?;
    crate::deploy::env_file::set_env_key(&staged, WORKSPACE_ID_ENV, &developer.workspace_id)?;
    crate::deploy::env_file::set_env_key(
        &staged,
        MDA_AGENT_AUTH_PRINCIPAL_ID_ENV,
        &developer.principal_id,
    )
}

/// Keep local traces separate from the deployment's same-name tracing project.
fn ensure_dev_tracing_project(out_dir: &Path, assistant_id: &str) -> Result<()> {
    let staged = out_dir.join(".env");
    let env = crate::deploy::env_file::parse_env_file(&staged);
    let project_is_set = [LANGSMITH_PROJECT_ENV, LANGCHAIN_PROJECT_ENV]
        .into_iter()
        .any(|key| {
            env.get(key)
                .map(|value| value.trim())
                .is_some_and(|value| !value.is_empty())
        });
    if project_is_set {
        return Ok(());
    }

    crate::deploy::env_file::set_env_key(
        &staged,
        LANGSMITH_PROJECT_ENV,
        &format!("{assistant_id}-local"),
    )
}

/// Inject the staged LangSmith API key and endpoint into `langgraph dev` when
/// the MDA process does not already define them.
///
/// Project selection stays in staged `.env` so LangGraph can apply edits when
/// it restarts workers without an immutable process value overriding them.
fn apply_staged_langsmith_env(server: &mut Command, out_dir: &Path) {
    let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
    for key in DEV_LANGSMITH_SERVER_ENV_KEYS {
        let process_set = std::env::var(key)
            .ok()
            .is_some_and(|value| !value.trim().is_empty());
        if process_set && key != &MDA_AGENT_AUTH_PRINCIPAL_ID_ENV {
            continue;
        }
        if let Some(value) = staged.get(*key).map(|v| v.trim()).filter(|v| !v.is_empty()) {
            server.env(*key, value);
        }
    }
}

/// Mirror staged `MDA_PUBLIC_API_URL` into the child when the process omits it.
fn apply_staged_public_api_url(server: &mut Command, out_dir: &Path) {
    let process_set = std::env::var(MDA_PUBLIC_API_URL_ENV)
        .ok()
        .is_some_and(|value| !value.trim().is_empty());
    if process_set {
        return;
    }
    let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
    if let Some(value) = staged
        .get(MDA_PUBLIC_API_URL_ENV)
        .map(|v| v.trim())
        .filter(|v| !v.is_empty())
    {
        server.env(MDA_PUBLIC_API_URL_ENV, value);
    }
}

const MDA_CHANNEL_API_URL_ENV: &str = "MDA_CHANNEL_API_URL";

/// Keep trusted ingress on this server even when the public URL is a tunnel.
/// Python loads staged values over the child environment, so set both copies.
fn ensure_dev_loopback_api_url(out_dir: &Path, local_api_url: &str) -> Result<String> {
    let staged_path = out_dir.join(".env");
    let staged = crate::deploy::env_file::parse_env_file(&staged_path);
    let url = dev_loopback_api_url(local_api_url, &staged, |key| std::env::var(key).ok());
    crate::deploy::env_file::set_env_key(&staged_path, MDA_CHANNEL_API_URL_ENV, &url)?;
    Ok(url)
}

fn dev_loopback_api_url(
    local_api_url: &str,
    staged: &std::collections::BTreeMap<String, String>,
    process_env: impl Fn(&str) -> Option<String>,
) -> String {
    [MDA_CHANNEL_API_URL_ENV, "LANGGRAPH_API_URL"]
        .into_iter()
        .find_map(|key| {
            process_env(key)
                .filter(|value| !value.trim().is_empty())
                .or_else(|| {
                    staged
                        .get(key)
                        .filter(|value| !value.trim().is_empty())
                        .cloned()
                })
        })
        .map(|value| value.trim().to_string())
        .unwrap_or_else(|| local_api_url.to_string())
}

/// LangGraph CLI default bind port when `mda dev` does not pass `--port`.
const DEFAULT_DEV_PORT: u16 = 2024;

/// Local origin for channel/connector HTTP under `mda dev`.
///
/// Uses `localhost` (not `127.0.0.1`) when the server binds a wildcard or the
/// default host: on macOS the LangGraph Node CLI often listens on `[::1]` while
/// another local stack already owns `127.0.0.1:2024`, so an IPv4 URL can hit
/// the wrong process. Explicit `--host` values are kept as-is; bare IPv6
/// literals are bracketed so `http://[::1]:2024` is a valid origin.
fn local_dev_public_api_url(opts: &DevOptions) -> String {
    let port = opts.port.unwrap_or(DEFAULT_DEV_PORT);
    let host = match opts.hostname.as_deref().map(str::trim) {
        Some("0.0.0.0") | Some("::") | Some("") | None => "localhost",
        Some(hostname) => hostname,
    };
    format_dev_http_origin(host, port)
}

/// Build `http://host:port`, bracketing bare IPv6 literals.
fn format_dev_http_origin(host: &str, port: u16) -> String {
    if host.contains(':') && !host.starts_with('[') {
        format!("http://[{host}]:{port}")
    } else {
        format!("http://{host}:{port}")
    }
}

/// Ensure the staged build `.env` has `MDA_PUBLIC_API_URL` for local HTTP routes.
///
/// Deploy injects the real Agent Server URL; locally the LangGraph dev server
/// is the mount point for `/channels/*` and `/connectors/*`. When unset, infer
/// `http://localhost:<port>` from `--port` / `--host` (default port 2024).
/// Written only to the staged `.env` — not the project file — so deploy does
/// not pick up a loopback URL. Returns `true` when a value was injected.
fn ensure_dev_public_api_url(out_dir: &Path, opts: &DevOptions) -> Result<bool> {
    let staged = out_dir.join(".env");
    let env = crate::deploy::env_file::parse_env_file(&staged);
    let present = env
        .get(MDA_PUBLIC_API_URL_ENV)
        .map(|value| value.trim())
        .is_some_and(|value| !value.is_empty())
        || std::env::var(MDA_PUBLIC_API_URL_ENV)
            .ok()
            .is_some_and(|value| !value.trim().is_empty());
    if present {
        if let Ok(value) = std::env::var(MDA_PUBLIC_API_URL_ENV) {
            let trimmed = value.trim();
            if !trimmed.is_empty()
                && env.get(MDA_PUBLIC_API_URL_ENV).map(String::as_str) != Some(trimmed)
            {
                crate::deploy::env_file::set_env_key(&staged, MDA_PUBLIC_API_URL_ENV, trimmed)?;
            }
        }
        return Ok(false);
    }

    let url = local_dev_public_api_url(opts);
    crate::deploy::env_file::set_env_key(&staged, MDA_PUBLIC_API_URL_ENV, &url)?;
    Ok(true)
}

/// Bake (or reuse) the sandbox recipe for local LangSmith sandboxes.
///
/// Only projects with `setup.sh` or an explicit bake base need a recipe.
/// Without a key, fail closed — there is no Docker fallback on this path.
fn ensure_dev_sandbox_recipe(
    project: &Project,
    out_dir: &Path,
    assistant_id: &str,
    prompt: DevLoginPrompt,
) -> Result<()> {
    let staged = out_dir.join(".env");
    let spec = deploy::recipe_spec_from_project(project, out_dir)?;
    if spec.is_none() && !compile::project_uses_sandbox(project)? {
        crate::deploy::env_file::remove_env_key(&staged, MDA_SANDBOX_API_ENDPOINT_ENV)?;
        crate::deploy::env_file::remove_env_key(&staged, MDA_SANDBOX_RECIPE_SNAPSHOT_ENV)?;
        return Ok(());
    }

    let mut env = crate::deploy::env_file::parse_env_file(&staged);
    let (host_url, _) = resolve_langsmith_api_bases(&env);
    let platform = resolve_platform_api_url(&host_url, &env);
    let sandbox_endpoint =
        sandbox_api_endpoint(&platform).context("resolved LangSmith platform API URL is empty")?;
    crate::deploy::env_file::set_env_key(&staged, MDA_SANDBOX_API_ENDPOINT_ENV, &sandbox_endpoint)?;

    let Some(spec) = spec else {
        crate::deploy::env_file::remove_env_key(&staged, MDA_SANDBOX_RECIPE_SNAPSHOT_ENV)?;
        return Ok(());
    };

    let mut api_key = env
        .get(API_KEY_ENV)
        .map(|v| v.trim())
        .filter(|v| !v.is_empty())
        .map(str::to_string)
        .or_else(|| {
            std::env::var(API_KEY_ENV)
                .ok()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
        });
    if api_key.is_none() {
        // Recipe bake needs a key; offer the same login path as deploy before failing.
        deploy::ensure_project_langsmith_api_key(
            &project.root,
            Some(out_dir),
            dev_langsmith_login_is_interactive(true, prompt),
        )?;
        env = crate::deploy::env_file::parse_env_file(&staged);
        api_key = env
            .get(API_KEY_ENV)
            .map(|v| v.trim())
            .filter(|v| !v.is_empty())
            .map(str::to_string)
            .or_else(|| {
                std::env::var(API_KEY_ENV)
                    .ok()
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
            });
    }
    let Some(api_key) = api_key else {
        bail!(
            "This project declares a sandbox recipe, so `mda dev` needs {API_KEY_ENV} to bake \
             it. Add the key to .env (or export it), then run `mda dev` again."
        );
    };

    let deployment = dev_deployment_name(&api_key, project, assistant_id);
    let workspace_id = env
        .get("LANGSMITH_WORKSPACE_ID")
        .map(|v| v.trim())
        .filter(|v| !v.is_empty())
        .map(str::to_string);
    let client = crate::langsmith::Client::with_credential_and_platform(
        &host_url,
        Some(platform.as_str()),
        Credential::ApiKey(api_key),
        workspace_id,
    )?;

    let bake_env: std::collections::BTreeMap<String, String> = env
        .iter()
        .filter(|(name, value)| !value.is_empty() && !RESERVED_ENV_VARS.contains(&name.as_str()))
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect();

    let sp = spinner();
    sp.start(deploy::BAKE_SPINNER_TITLE);
    let outcome = match bake_recipe(
        &client,
        &deployment,
        &spec,
        &bake_env,
        &deploy::bake_progress(&sp),
    ) {
        Ok(outcome) => outcome,
        Err(err) => {
            sp.error("Could not bake the sandbox recipe");
            return Err(err);
        }
    };
    if outcome.skipped {
        sp.stop(format!(
            "Reusing ready recipe snapshot '{}'",
            outcome.recipe_name
        ));
    } else {
        sp.stop(format!("Baked recipe snapshot '{}'", outcome.recipe_name));
    }

    crate::deploy::env_file::set_env_key(&staged, MDA_DEPLOYMENT_NAME_ENV, &deployment)?;
    crate::deploy::env_file::set_env_key(
        &staged,
        MDA_SANDBOX_RECIPE_SNAPSHOT_ENV,
        &outcome.recipe_reference,
    )?;
    match deploy::bake::delete_stale_recipe_snapshots(
        &client,
        &deployment,
        Some(&outcome.recipe_name),
    ) {
        Ok(cleanup) => {
            if let Some(note) = cleanup.in_use_note() {
                log::info(note)?;
            }
        }
        Err(err) => log::warning(format!(
            "Sandbox recipe is ready, but stale mda dev snapshots could not be cleaned up: {err}"
        ))?,
    }
    // Registry credentials are bake-only; the local Agent Server does not need
    // the pull token after Host has stored it.
    if let Some(registry) = &spec.registry {
        crate::deploy::env_file::remove_env_key(&staged, &registry.password_env)?;
    }
    Ok(())
}

/// Stable, private namespace for one LangSmith credential and local deployment.
///
/// The credential belongs in the hash because Host scopes snapshot listing to the
/// workspace while cleanup deletes every *other* snapshot under this prefix: two
/// developers sharing a workspace and a project path would otherwise delete each
/// other's baked recipe on every `mda dev`. A workspace-level identity cannot
/// separate them. The trade-off is that rotating the key (or renaming the
/// assistant) leaves the previous namespace's one snapshot behind, which is
/// cheaper than an identity round trip on this path.
fn dev_deployment_name(api_key: &str, project: &Project, assistant_id: &str) -> String {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    hasher.update(api_key.as_bytes());
    hasher.update([0]);
    hasher.update(project.root.to_string_lossy().as_bytes());
    hasher.update([0]);
    hasher.update(assistant_id.as_bytes());
    let digest = hasher.finalize();
    let short: String = digest[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    format!("dev-{short}")
}

/// Cryptographically random secret for a single staged `mda dev` environment.
///
/// Written only into `.mda/dev/.env` when the project has not set the env var.
/// Must not be a repository constant — `mda dev` can expose the server through
/// `MDA_PUBLIC_API_URL`, so forgeable defaults would let anyone impersonate
/// trusted-backend callers.
fn random_local_dev_secret() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().as_simple(),
        uuid::Uuid::new_v4().as_simple()
    )
}

/// Ensure a build-local credential for managed loopback handoffs.
///
/// Generates once per staged `.env` and leaves an existing value alone — rotating
/// on every watch rebuild would rewrite `.env` and force extra LangGraph restarts.
fn ensure_dev_handoff_secret(out_dir: &Path) -> Result<()> {
    let staged = out_dir.join(".env");
    let env = crate::deploy::env_file::parse_env_file(&staged);
    let present = env
        .get(crate::deploy::MDA_HANDOFF_SECRET_ENV)
        .map(|value| value.trim())
        .is_some_and(|value| !value.is_empty());
    if present {
        return Ok(());
    }
    crate::deploy::env_file::set_env_key(
        &staged,
        crate::deploy::MDA_HANDOFF_SECRET_ENV,
        &random_local_dev_secret(),
    )
}

/// Ensure the staged build has a trusted credential for managed loopback calls.
///
/// Prefer the server-only LangSmith API key already staged for API-key auth.
/// When it is absent, inject the legacy ingress secret so backend auth and
/// credential-free local projects remain compatible.
/// Returns `true` when a fresh local secret was injected into the staged `.env`.
fn ensure_dev_ingress_secret(_project: &crate::project::Project, out_dir: &Path) -> Result<bool> {
    let process_ingress_secret = std::env::var(compile::INGRESS_SECRET_ENV).ok();
    let process_api_key = std::env::var(crate::langsmith::API_KEY_ENV).ok();
    ensure_dev_ingress_secret_with_process_env(
        out_dir,
        process_ingress_secret.as_deref(),
        process_api_key.as_deref(),
    )
}

fn ensure_dev_ingress_secret_with_process_env(
    out_dir: &Path,
    process_ingress_secret: Option<&str>,
    process_api_key: Option<&str>,
) -> Result<bool> {
    let staged = out_dir.join(".env");
    let env = crate::deploy::env_file::parse_env_file(&staged);
    let present = env
        .get(compile::INGRESS_SECRET_ENV)
        .map(|value| value.trim())
        .is_some_and(|value| !value.is_empty())
        || process_ingress_secret.is_some_and(|value| !value.trim().is_empty());
    if present {
        // Process env wins for this shell; mirror it into the staged file so the
        // LangGraph worker (which loads `.mda/dev/.env`) sees the same value.
        if let Some(value) = process_ingress_secret {
            let trimmed = value.trim();
            if !trimmed.is_empty()
                && env.get(compile::INGRESS_SECRET_ENV).map(String::as_str) != Some(trimmed)
            {
                crate::deploy::env_file::set_env_key(
                    &staged,
                    compile::INGRESS_SECRET_ENV,
                    trimmed,
                )?;
            }
        }
        return Ok(false);
    }

    let server_api_key_present = env
        .get(crate::langsmith::API_KEY_ENV)
        .map(|value| value.trim())
        .is_some_and(|value| !value.is_empty())
        || process_api_key.is_some_and(|value| !value.trim().is_empty());
    if server_api_key_present {
        return Ok(false);
    }

    let secret = random_local_dev_secret();
    crate::deploy::env_file::set_env_key(&staged, compile::INGRESS_SECRET_ENV, &secret)?;
    Ok(true)
}

/// Write [`MDA_ASSISTANT_ID_ENV`] into the staged `.env` so Slack/GitHub
/// channel ingress invokes the compiled graph id (not the `"agent"` fallback).
fn ensure_dev_assistant_id(project: &crate::project::Project, out_dir: &Path) -> Result<()> {
    let assistant_id = agent_config::agent_assistant_id(project)?;
    let staged = out_dir.join(".env");
    crate::deploy::env_file::set_env_key(&staged, MDA_ASSISTANT_ID_ENV, &assistant_id)?;
    Ok(())
}

/// Build the language-specific dev-server invocation, threading through the
/// shared `--port` / `--host` / `--no-browser` / `--no-reload` / `--tunnel`
/// options.
///
/// TypeScript graphs run on `npx @langchain/langgraph-cli@<pin> dev`; Python graphs
/// run on `uv run --with langgraph-cli[inmem] langgraph dev`, pinned to the same
/// `langgraph-api` release deploy uses so crons and runs behave alike. Both read
/// `langgraph.json` from the working directory (the compiled `.mda/dev`) and
/// accept the same flag set.
pub fn dev_command(language: Language, opts: &DevOptions) -> Result<DevCommand> {
    let (program, mut args) = match language {
        Language::TypeScript => (
            "npx".to_string(),
            vec![
                "--yes".to_string(),
                typescript_dev_server_package(),
                "dev".to_string(),
            ],
        ),
        Language::Python => ("uv".to_string(), python_dev_server_args()?),
    };

    if let Some(port) = opts.port {
        args.push("--port".to_string());
        args.push(port.to_string());
    }
    if let Some(hostname) = &opts.hostname {
        args.push("--host".to_string());
        args.push(hostname.clone());
    }
    if opts.no_browser {
        args.push("--no-browser".to_string());
    }
    if opts.no_reload {
        args.push("--no-reload".to_string());
    }
    if opts.tunnel {
        args.push("--tunnel".to_string());
    }

    Ok(DevCommand {
        program,
        args,
        local_api_url: local_dev_public_api_url(opts),
    })
}

fn typescript_dev_server_package() -> String {
    let dep = dependencies::TYPESCRIPT_DEV_SERVER_CLI;
    format!("{}@{}", dep.name, dep.version)
}

fn python_dev_server_requirement() -> String {
    let dep = dependencies::PYTHON_DEV_SERVER_CLI;
    format!("{}{}", dep.name, dep.version)
}

/// Pin the Agent Server to the deploy release. Explicit pins keep the staged
/// project resolution and `uv.lock` untouched, even for pre-releases.
fn python_dev_server_args() -> Result<Vec<String>> {
    let version = crate::compile::resolved_langgraph_api_version()?;
    Ok(vec![
        "run".to_string(),
        "--with".to_string(),
        python_dev_server_requirement(),
        "--with".to_string(),
        format!("langgraph-api=={version}"),
        "--with".to_string(),
        format!(
            "langgraph-runtime-inmem=={}",
            crate::compile::LANGGRAPH_RUNTIME_INMEM_VERSION
        ),
        "langgraph".to_string(),
        "dev".to_string(),
    ])
}

/// Append `--preserve-symlinks` to any existing `NODE_OPTIONS`, deduping so the
/// flag is never added twice. Returns the value to set on the dev server's env.
fn node_options_with_preserve_symlinks(existing: Option<String>) -> String {
    const FLAG: &str = "--preserve-symlinks";
    match existing {
        Some(value) if value.split_whitespace().any(|token| token == FLAG) => value,
        Some(value) if !value.trim().is_empty() => format!("{value} {FLAG}"),
        _ => FLAG.to_string(),
    }
}

/// Turn a failure to spawn the dev server into an actionable error, pointing the
/// developer at the install step for their language's LangGraph CLI.
fn dev_spawn_error(language: Language, program: &str, err: std::io::Error) -> anyhow::Error {
    if err.kind() == std::io::ErrorKind::NotFound {
        let hint = match language {
            Language::TypeScript => {
                "could not find `npx`. Install Node.js (which bundles npx) to run the LangGraph dev server"
            }
            Language::Python => {
                "could not find `uv`. Install uv to let `mda dev` resolve the Python LangGraph dev server (`langgraph-cli[inmem]`) from the compiled build"
            }
        };
        return anyhow::anyhow!("{hint}");
    }
    anyhow::Error::new(err).context(format!("failed to launch `{program}`"))
}

#[cfg(test)]
mod tests {
    #[cfg(unix)]
    use std::process::ExitStatus;
    #[cfg(unix)]
    use std::time::Instant;

    use super::*;
    use crate::error::category_of;

    #[test]
    fn langgraph_graph_keys_reads_the_graphs_map() {
        let manifest = br#"{"graphs": {"lead": "a", "lead--researcher": "b"}}"#;
        assert_eq!(
            langgraph_graph_keys(Some(manifest)),
            ["lead", "lead--researcher"]
        );
        assert!(langgraph_graph_keys(None).is_empty());
        assert!(langgraph_graph_keys(Some(b"not json")).is_empty());
    }

    const TEST_ASSISTANT_ID: &str = "test-agent";

    /// The whole log is too much for an error message, and the blank lines a
    /// server log is padded with would crowd out the part that explains it.
    #[test]
    fn log_tail_keeps_the_last_lines_without_blanks() {
        let dir = tempfile::tempdir().unwrap();
        let log = dir.path().join("dev-server.log");
        fs::write(&log, "one\n\ntwo\nthree\n\n").unwrap();

        assert_eq!(log_tail(&log, 2), "two\nthree");
        assert_eq!(log_tail(&log, 10), "one\ntwo\nthree");
    }

    /// A server can fail before it writes anything. The path still has to
    /// reach the user, because nothing was on screen either.
    #[test]
    fn log_reference_points_at_the_file_when_it_has_no_lines() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("dev-server.log");

        let reference = log_reference(&missing);

        assert!(reference.contains(&missing.display().to_string()));
        assert!(!reference.contains("Last lines"), "{reference}");
    }

    /// A clone controls everything under the project root, including
    /// `.mda/dev-server.log`. Creating that file follows a symlink and
    /// truncates its target, so the link has to be refused, not followed.
    #[cfg(unix)]
    #[test]
    fn a_symlinked_log_path_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let victim = dir.path().join("bashrc");
        fs::write(&victim, "# important user config\n").unwrap();
        let mda = dir.path().join(".mda");
        fs::create_dir_all(&mda).unwrap();
        let log = mda.join(DEV_SERVER_LOG);
        std::os::unix::fs::symlink(&victim, &log).unwrap();

        let mut server = Command::new("true");
        let error = redirect_to_log(&mut server, &log).unwrap_err();

        assert_eq!(crate::error::category_of(&error), ErrorCategory::Project);
        assert_eq!(
            fs::read_to_string(&victim).unwrap(),
            "# important user config\n",
            "the victim file must be untouched"
        );
    }

    /// The same trick one level up: `.mda` itself as a link. `create_dir_all`
    /// accepts an existing symlinked directory without complaint.
    #[cfg(unix)]
    #[test]
    fn a_symlinked_log_directory_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let elsewhere = dir.path().join("elsewhere");
        fs::create_dir_all(&elsewhere).unwrap();
        let mda = dir.path().join(".mda");
        std::os::unix::fs::symlink(&elsewhere, &mda).unwrap();

        let mut server = Command::new("true");
        let error = redirect_to_log(&mut server, &mda.join(DEV_SERVER_LOG)).unwrap_err();

        assert_eq!(crate::error::category_of(&error), ErrorCategory::Project);
        assert!(!elsewhere.join(DEV_SERVER_LOG).exists());
    }

    #[test]
    fn an_ordinary_log_path_is_created() {
        let dir = tempfile::tempdir().unwrap();
        let log = dir.path().join(".mda").join(DEV_SERVER_LOG);

        let mut server = Command::new("true");
        redirect_to_log(&mut server, &log).unwrap();

        assert!(log.is_file());
    }

    #[test]
    fn a_closed_port_is_not_a_running_dev_server() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        drop(listener);

        assert!(!dev_server_is_up(&url));
    }

    /// `mda chat` reuses a server that answers `/ok`, so this is what tells
    /// it not to start a second one that could not bind the same port.
    #[test]
    fn a_server_answering_ok_is_a_running_dev_server() {
        use std::io::{BufRead, Write};
        use std::net::TcpListener;
        use std::thread;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = String::new();
            {
                let mut reader = std::io::BufReader::new(&mut stream);
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" || line.is_empty() {
                        break;
                    }
                    request.push_str(&line);
                }
            }
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                .unwrap();
            request
        });

        assert!(dev_server_is_up(&url));

        let request = handle.join().unwrap();
        assert!(request.starts_with("GET /ok HTTP/1.1"), "{request}");
    }

    #[cfg(unix)]
    fn exit_status(raw: i32) -> ExitStatus {
        use std::os::unix::process::ExitStatusExt;

        ExitStatus::from_raw(raw)
    }

    #[cfg(unix)]
    fn exited(code: i32) -> ExitStatus {
        exit_status(code << 8)
    }

    #[cfg(unix)]
    fn signalled(signal: i32) -> ExitStatus {
        exit_status(signal)
    }

    #[cfg(unix)]
    #[test]
    fn a_clean_exit_succeeds() {
        let outcome = classify_langgraph_outcome(Outcome::Exited(exited(0))).unwrap();

        assert_eq!(outcome, CommandCompletion::Succeeded);
    }

    #[test]
    fn a_stop_we_sent_succeeds() {
        assert_eq!(
            classify_langgraph_outcome(Outcome::Stopped).unwrap(),
            CommandCompletion::Succeeded
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_server_error_is_a_failure() {
        for outcome in [Outcome::Exited(exited(1)), Outcome::Exited(signalled(9))] {
            let err = classify_langgraph_outcome(outcome).unwrap_err();

            assert_eq!(category_of(&err), ErrorCategory::Toolchain);
        }
    }

    #[cfg(unix)]
    #[test]
    fn an_interrupt_stops_a_server_that_is_still_running() {
        let mut child = OwnedChild::spawn(Command::new("sleep").arg("30"))
            .expect("failed to spawn the test server");
        let started = Instant::now();

        let outcome =
            classify_langgraph_outcome(child.wait(&Interrupt::requested()).unwrap()).unwrap();

        assert_eq!(outcome, CommandCompletion::Succeeded);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    /// Even though `uv run` reports a Ctrl-C the same way a crash exits.
    #[cfg(unix)]
    #[test]
    fn a_nonzero_exit_without_an_interrupt_stays_a_failure() {
        let mut child = OwnedChild::spawn(Command::new("sh").args(["-c", "exit 1"]))
            .expect("failed to spawn the test server");

        let classified = classify_langgraph_outcome(child.wait(&Interrupt::pending()).unwrap());

        assert!(classified.is_err());
    }

    fn opts() -> DevOptions {
        DevOptions {
            path: PathBuf::from("."),
            port: None,
            hostname: None,
            no_browser: false,
            no_reload: false,
            tunnel: false,
            no_schedules: false,
        }
    }

    #[test]
    fn plain_sandbox_stages_platform_endpoint_without_recipe_or_langsmith_key() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            format!(
                "{LANGSMITH_ENDPOINT_ENV}=https://self-hosted.example/api/v1\n\
                 {MDA_SANDBOX_API_ENDPOINT_ENV}=https://stale.example/v2/sandboxes\n\
                 {MDA_SANDBOX_RECIPE_SNAPSHOT_ENV}=stale-recipe\n"
            ),
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("sandbox/index.ts")],
        };

        ensure_dev_sandbox_recipe(
            &project,
            &out_dir,
            TEST_ASSISTANT_ID,
            DevLoginPrompt::Allowed,
        )
        .unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_SANDBOX_API_ENDPOINT_ENV).map(String::as_str),
            Some("https://self-hosted.example/v2/sandboxes")
        );
        assert!(!env.contains_key(MDA_SANDBOX_RECIPE_SNAPSHOT_ENV));
    }

    #[test]
    fn project_without_sandbox_removes_stale_sandbox_runtime_env() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            format!(
                "{MDA_SANDBOX_API_ENDPOINT_ENV}=https://stale.example/v2/sandboxes\n\
                 {MDA_SANDBOX_RECIPE_SNAPSHOT_ENV}=stale-recipe\n"
            ),
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![],
        };

        ensure_dev_sandbox_recipe(
            &project,
            &out_dir,
            TEST_ASSISTANT_ID,
            DevLoginPrompt::Allowed,
        )
        .unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert!(!env.contains_key(MDA_SANDBOX_API_ENDPOINT_ENV));
        assert!(!env.contains_key(MDA_SANDBOX_RECIPE_SNAPSHOT_ENV));
    }

    #[test]
    fn dev_deployment_name_is_scoped_to_credential_and_assistant() {
        let project = Project {
            root: PathBuf::from("/projects/agent"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![],
        };
        let first = dev_deployment_name("key-a", &project, "agent");
        let other_credential = dev_deployment_name("key-b", &project, "agent");
        let other_assistant = dev_deployment_name("key-a", &project, "other-agent");

        assert_ne!(first, other_credential);
        assert_ne!(first, other_assistant);
    }

    #[test]
    fn dev_deployment_name_is_scoped_to_project() {
        let project = Project {
            root: PathBuf::from("/projects/agent-a"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![],
        };
        let other_project = Project {
            root: PathBuf::from("/projects/agent-b"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![],
        };

        assert_ne!(
            dev_deployment_name("key", &project, "agent"),
            dev_deployment_name("key", &other_project, "agent")
        );
    }

    #[test]
    fn dev_deployment_name_does_not_expose_identity_inputs() {
        let project = Project {
            root: PathBuf::from("/projects/private-project"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![],
        };
        let name = dev_deployment_name("secret-key", &project, "private-agent");

        assert!(name.starts_with("dev-"));
        assert!(!name.contains("secret-key"));
        assert!(!name.contains("private-agent"));
        assert!(!name.contains("private-project"));
    }

    fn serve_json(responses: Vec<(u16, String)>) -> (String, std::sync::mpsc::Receiver<String>) {
        use std::io::Write;
        use std::net::TcpListener;
        use std::sync::mpsc;
        use std::thread;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for (status, body) in responses {
                let (mut stream, _) = listener.accept().unwrap();
                tx.send(read_http_request(&mut stream)).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });
        (base_url, rx)
    }

    fn read_http_request(stream: &mut std::net::TcpStream) -> String {
        use std::io::Read;
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            let text = String::from_utf8_lossy(&data);
            if text.contains("\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    #[test]
    fn managed_tools_developer_principal_is_staged_from_post_principals() {
        let principal_id = "11111111-1111-4111-8111-111111111111";
        let (url, requests) = serve_json(vec![
            (
                200,
                serde_json::json!({
                    "ls_user_id": "22222222-2222-4222-8222-222222222222",
                    "tenant_id": "workspace-resolved",
                })
                .to_string(),
            ),
            (
                201,
                serde_json::json!({ "principal": { "id": principal_id } }).to_string(),
            ),
        ]);
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            format!(
                "LANGSMITH_API_KEY=personal-key\nLANGSMITH_ENDPOINT={url}\n\
                 LANGSMITH_WORKSPACE_ID=workspace-requested\n"
            ),
        )
        .unwrap();

        let requirements = [(
            "mcp".to_string(),
            serde_json::json!({ "needsDeveloperPrincipal": true }),
        )]
        .into_iter()
        .collect();
        ensure_dev_developer_principal(
            &out_dir,
            connectors_need_developer_principal(&requirements),
        )
        .unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_AGENT_AUTH_PRINCIPAL_ID_ENV).map(String::as_str),
            Some(principal_id)
        );
        assert_eq!(
            env.get(WORKSPACE_ID_ENV).map(String::as_str),
            Some("workspace-resolved")
        );
        assert!(requests.recv().unwrap().starts_with("GET /api/v1/auth "));
        assert!(requests
            .recv()
            .unwrap()
            .starts_with("POST /api/v1/agent-auth/principals "));
    }

    #[test]
    fn managed_tools_require_developer_principal_without_connection_slugs() {
        let requirements = [(
            "mcp".to_string(),
            serde_json::json!({
                "needsDeveloperPrincipal": true,
                "userConnections": [],
            }),
        )]
        .into_iter()
        .collect();

        assert!(connectors_need_developer_principal(&requirements));
        assert!(compile::referenced_connection_slugs(&requirements).is_empty());
    }

    #[test]
    fn user_connections_require_developer_principal_without_managed_tools() {
        let requirements = [(
            "mcp".to_string(),
            serde_json::json!({ "userConnections": ["notion"] }),
        )]
        .into_iter()
        .collect();

        assert!(connectors_need_developer_principal(&requirements));
    }

    #[test]
    fn ordinary_mcp_servers_do_not_require_developer_principal() {
        let requirements = [(
            "mcp".to_string(),
            serde_json::json!({
                "needsDeveloperPrincipal": false,
                "agentConnections": ["search"],
                "userConnections": [],
            }),
        )]
        .into_iter()
        .collect();

        assert!(!connectors_need_developer_principal(&requirements));
        assert!(!connectors_need_developer_principal(
            &compile::ConnectorRequirements::new()
        ));
    }

    #[test]
    fn developer_principal_refresh_replaces_existing_stamp() {
        let principal_id = "33333333-3333-4333-8333-333333333333";
        let (url, requests) = serve_json(vec![
            (
                200,
                serde_json::json!({
                    "ls_user_id": "44444444-4444-4444-8444-444444444444",
                    "tenant_id": "workspace-new",
                })
                .to_string(),
            ),
            (
                201,
                serde_json::json!({ "principal": { "id": principal_id } }).to_string(),
            ),
        ]);
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            format!(
                "LANGSMITH_API_KEY=updated-key\nLANGSMITH_ENDPOINT={url}\n\
                 LANGSMITH_WORKSPACE_ID=workspace-old\n\
                 MDA_AGENT_AUTH_PRINCIPAL_ID=old-principal\n"
            ),
        )
        .unwrap();

        ensure_dev_developer_principal(&out_dir, true).unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            (
                env.get(MDA_AGENT_AUTH_PRINCIPAL_ID_ENV).map(String::as_str),
                env.get(WORKSPACE_ID_ENV).map(String::as_str),
            ),
            (Some(principal_id), Some("workspace-new"))
        );
        assert_eq!(requests.iter().count(), 2);
    }

    fn stage_recipe_dev_project() -> (tempfile::TempDir, PathBuf, String, String) {
        use crate::deploy::helpers::{recipe_digest, recipe_snapshot_name};

        let root = tempfile::tempdir().unwrap();
        let setup_rel = PathBuf::from("sandbox").join("setup.sh");
        fs::create_dir_all(root.path().join("sandbox")).unwrap();
        let setup = "#!/bin/sh\necho hi\n";
        fs::write(root.path().join(&setup_rel), setup).unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join("sandbox-recipe.json"),
            r#"{"hasSetupScript":true,"digest":"unused"}"#,
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![setup_rel],
        };
        let deployment = dev_deployment_name("test-key", &project, TEST_ASSISTANT_ID);
        let current = recipe_snapshot_name(
            &deployment,
            &recipe_digest(setup.as_bytes(), None, None, None, None),
        );
        (root, out_dir, deployment, current)
    }

    fn recipe_dev_project(root: &std::path::Path) -> Project {
        Project {
            root: root.to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("sandbox").join("setup.sh")],
        }
    }

    #[test]
    fn ensure_dev_sandbox_recipe_deletes_stale_snapshots_after_staging() {
        use serde_json::json;

        let (root, out_dir, deployment, current) = stage_recipe_dev_project();
        let stale = format!("{deployment}--setup-aaaaaaaaaaaaaaaa");
        let list_ready = json!({
            "snapshots": [
                { "id": "current-id", "name": current, "status": "ready" }
            ]
        })
        .to_string();
        let list_all = json!({
            "snapshots": [
                { "id": "current-id", "name": current, "status": "ready" },
                { "id": "stale-id", "name": stale, "status": "ready" }
            ]
        })
        .to_string();
        let (url, rx) = serve_json(vec![
            (200, list_ready),
            (200, list_all),
            (204, String::new()),
        ]);
        fs::write(
            out_dir.join(".env"),
            format!("{API_KEY_ENV}=test-key\n{LANGSMITH_ENDPOINT_ENV}={url}\n"),
        )
        .unwrap();

        ensure_dev_sandbox_recipe(
            &recipe_dev_project(root.path()),
            &out_dir,
            TEST_ASSISTANT_ID,
            DevLoginPrompt::Allowed,
        )
        .unwrap();

        let _ = rx.recv().unwrap();
        let _ = rx.recv().unwrap();
        let delete = rx.recv().unwrap();
        assert!(delete.contains("DELETE /v2/sandboxes/snapshots/stale-id"));
        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_SANDBOX_RECIPE_SNAPSHOT_ENV).map(String::as_str),
            Some(current.as_str())
        );
        drop(root);
    }

    #[test]
    fn ensure_dev_sandbox_recipe_warns_when_stale_delete_fails() {
        use serde_json::json;

        let (root, out_dir, deployment, current) = stage_recipe_dev_project();
        let stale = format!("{deployment}--setup-aaaaaaaaaaaaaaaa");
        let list_ready = json!({
            "snapshots": [
                { "id": "current-id", "name": current, "status": "ready" }
            ]
        })
        .to_string();
        let list_all = json!({
            "snapshots": [
                { "id": "stale-id", "name": stale, "status": "ready" }
            ]
        })
        .to_string();
        let (url, rx) = serve_json(vec![
            (200, list_ready),
            (200, list_all),
            (500, r#"{"detail":"delete failed"}"#.to_string()),
        ]);
        fs::write(
            out_dir.join(".env"),
            format!("{API_KEY_ENV}=test-key\n{LANGSMITH_ENDPOINT_ENV}={url}\n"),
        )
        .unwrap();

        ensure_dev_sandbox_recipe(
            &recipe_dev_project(root.path()),
            &out_dir,
            TEST_ASSISTANT_ID,
            DevLoginPrompt::Allowed,
        )
        .unwrap();

        let _ = rx.recv().unwrap();
        let _ = rx.recv().unwrap();
        let _ = rx.recv().unwrap();
        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_SANDBOX_RECIPE_SNAPSHOT_ENV).map(String::as_str),
            Some(current.as_str())
        );
        drop(root);
    }

    #[test]
    fn stage_dev_env_file_copies_root_env_into_build() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(out_dir.join(".env")).unwrap(),
            "OPENAI_API_KEY=test\nMDA_CHANNEL_API_URL=http://localhost:2024\n"
        );
    }

    #[test]
    fn stage_dev_env_file_skips_identical_copy() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        let body = "OPENAI_API_KEY=test\n";
        fs::write(root.path().join(".env"), body).unwrap();
        fs::write(
            out_dir.join(".env"),
            format!("{body}MDA_CHANNEL_API_URL=http://localhost:2024\n"),
        )
        .unwrap();
        let mtime_before = fs::metadata(out_dir.join(".env"))
            .unwrap()
            .modified()
            .unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        assert_eq!(
            fs::metadata(out_dir.join(".env"))
                .unwrap()
                .modified()
                .unwrap(),
            mtime_before
        );
    }

    #[test]
    fn stage_dev_env_file_preserves_injected_values_on_restage() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=test\n").unwrap();
        let handoff = crate::deploy::MDA_HANDOFF_SECRET_ENV;
        fs::write(
            out_dir.join(".env"),
            format!(
                "OPENAI_API_KEY=test\n{handoff}=keep-me\nMDA_PUBLIC_API_URL=http://localhost:2024\nMDA_CHANNEL_API_URL=http://localhost:2024\nLANGSMITH_PROJECT={TEST_ASSISTANT_ID}-local\n"
            ),
        )
        .unwrap();
        let mtime_before = fs::metadata(out_dir.join(".env"))
            .unwrap()
            .modified()
            .unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        let staged = fs::read_to_string(out_dir.join(".env")).unwrap();
        assert!(staged.contains(&format!("{handoff}=keep-me")));
        assert!(staged.contains("MDA_PUBLIC_API_URL=http://localhost:2024"));
        assert!(staged.contains(&format!("LANGSMITH_PROJECT={TEST_ASSISTANT_ID}-local")));
        assert_eq!(
            fs::metadata(out_dir.join(".env"))
                .unwrap()
                .modified()
                .unwrap(),
            mtime_before,
            "unchanged project keys must not rewrite staged .env"
        );
    }

    #[test]
    fn stage_dev_env_file_merges_updated_project_keys() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=new\n").unwrap();
        let handoff = crate::deploy::MDA_HANDOFF_SECRET_ENV;
        fs::write(
            out_dir.join(".env"),
            format!("OPENAI_API_KEY=old\n{handoff}=keep-me\n"),
        )
        .unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(env.get("OPENAI_API_KEY").map(String::as_str), Some("new"));
        assert_eq!(env.get(handoff).map(String::as_str), Some("keep-me"));
    }

    #[test]
    fn stage_dev_env_file_removes_deleted_project_keys_but_keeps_mda_injections() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=keep\n").unwrap();
        let handoff = crate::deploy::MDA_HANDOFF_SECRET_ENV;
        fs::write(
            out_dir.join(".env"),
            format!(
                "OPENAI_API_KEY=keep\nSTALE_SECRET=gone\nLANGSMITH_PROJECT=custom-project\n{handoff}=keep-me\nMDA_PUBLIC_API_URL=http://localhost:2024\n"
            ),
        )
        .unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(env.get("OPENAI_API_KEY").map(String::as_str), Some("keep"));
        assert!(!env.contains_key("STALE_SECRET"));
        assert!(!env.contains_key(LANGSMITH_PROJECT_ENV));
        assert_eq!(env.get(handoff).map(String::as_str), Some("keep-me"));
        assert_eq!(
            env.get("MDA_PUBLIC_API_URL").map(String::as_str),
            Some("http://localhost:2024")
        );
    }

    /// A session PAT lives only in the staged copy, so restage must leave it
    /// alone. Rewriting it (delete + immediate re-add) bumps the mtime of the
    /// file LangGraph watches and costs an extra restart per rebuild.
    #[test]
    fn stage_dev_env_file_keeps_cli_session_langsmith_keys_untouched() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();

        let state = tempfile::tempdir().unwrap();
        let previous_state = std::env::var(crate::state::STATE_DIR_ENV).ok();
        std::env::set_var(crate::state::STATE_DIR_ENV, state.path());
        fs::write(
            state.path().join("cli-api-keys.json"),
            format!(
                "{{\n  \"{}\": {{\n    \"key\": \"lsv2_pt_session\",\n    \"expires_at\": 4102444800\n  }}\n}}\n",
                crate::langsmith::login::DEFAULT_API_URL
            ),
        )
        .unwrap();

        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        // Legacy gateway scaffold: the project declares the alias but leaves it blank.
        fs::write(
            root.path().join(".env"),
            "OPENAI_API_KEY=keep\nLANGSMITH_GATEWAY_API_KEY=\n",
        )
        .unwrap();
        let staged_path = out_dir.join(".env");
        fs::write(
            &staged_path,
            "OPENAI_API_KEY=keep\nLANGSMITH_API_KEY=lsv2_pt_session\nLANGSMITH_GATEWAY_API_KEY=lsv2_pt_session\nMDA_CHANNEL_API_URL=http://localhost:2024\n",
        )
        .unwrap();
        let before = fs::read(&staged_path).unwrap();

        let result = std::panic::catch_unwind(|| {
            stage_dev_env_file(
                root.path(),
                &out_dir,
                TEST_ASSISTANT_ID,
                "http://localhost:2024",
            )
            .unwrap();
        });

        match previous_state {
            Some(value) => std::env::set_var(crate::state::STATE_DIR_ENV, value),
            None => std::env::remove_var(crate::state::STATE_DIR_ENV),
        }
        crate::langsmith::login::clear_session_api_keys_for_test();
        result.expect("stage_dev_env_file panicked");

        // Byte-identical: no write at all, so no watcher wake-up.
        assert_eq!(fs::read(&staged_path).unwrap(), before);
    }

    /// A watch rebuild must never enter the login prompt: it would render into
    /// the build-log stream and block the watch loop while the server runs.
    #[test]
    fn dev_langsmith_login_is_suppressed_outside_startup() {
        assert!(!dev_langsmith_login_is_interactive(
            true,
            DevLoginPrompt::Suppressed
        ));
        assert!(!dev_langsmith_login_is_interactive(
            false,
            DevLoginPrompt::Allowed
        ));
    }

    /// A watch rebuild that adds a sandbox cannot prompt, so it must notice a
    /// missing key from the staged env rather than reload silently.
    #[test]
    fn dev_langsmith_api_key_available_reads_staged_env() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        let previous = std::env::var(API_KEY_ENV).ok();
        std::env::remove_var(API_KEY_ENV);
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();

        let missing = dev_langsmith_api_key_available(&out_dir);
        fs::write(out_dir.join(".env"), "LANGSMITH_API_KEY=  \n").unwrap();
        let blank = dev_langsmith_api_key_available(&out_dir);
        fs::write(out_dir.join(".env"), "LANGSMITH_API_KEY=lsv2_pt_test\n").unwrap();
        let staged = dev_langsmith_api_key_available(&out_dir);

        if let Some(value) = previous {
            std::env::set_var(API_KEY_ENV, value);
        }
        assert!(!missing);
        assert!(!blank);
        assert!(staged);
    }

    #[test]
    fn ensure_dev_handoff_secret_does_not_rotate_existing_value() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        let key = crate::deploy::MDA_HANDOFF_SECRET_ENV;
        fs::write(out_dir.join(".env"), format!("{key}=keep-me\n")).unwrap();

        ensure_dev_handoff_secret(&out_dir).unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(env.get(key).map(String::as_str), Some("keep-me"));
    }

    #[test]
    fn ensure_dev_langsmith_env_mirrors_process_key_when_staged_omits_it() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=test\n").unwrap();
        fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_from_shell");
        let result = std::panic::catch_unwind(|| {
            ensure_dev_langsmith_env(root.path(), &out_dir, true, DevLoginPrompt::Allowed).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_dev_langsmith_env panicked");

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
        assert_eq!(env.get("OPENAI_API_KEY").map(String::as_str), Some("test"));
        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(
            project.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
    }

    #[test]
    fn ensure_dev_langsmith_env_prefers_shell_key_over_staged() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            "LANGSMITH_API_KEY=lsv2_pt_from_file\n",
        )
        .unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_from_shell");
        let result = std::panic::catch_unwind(|| {
            ensure_dev_langsmith_env(root.path(), &out_dir, true, DevLoginPrompt::Allowed).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_dev_langsmith_env panicked");

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
    }

    #[test]
    fn ensure_dev_langsmith_env_writes_env_local_when_env_absent() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env.local"), "OPENAI_API_KEY=test\n").unwrap();
        fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_from_shell");
        let result = std::panic::catch_unwind(|| {
            ensure_dev_langsmith_env(root.path(), &out_dir, true, DevLoginPrompt::Allowed).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_dev_langsmith_env panicked");

        assert!(!root.path().join(".env").exists());
        let local = crate::deploy::env_file::parse_env_file(&root.path().join(".env.local"));
        assert_eq!(
            local.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
        assert_eq!(
            local.get("OPENAI_API_KEY").map(String::as_str),
            Some("test")
        );
    }

    #[test]
    fn ensure_dev_tracing_project_only_defaults_when_project_is_unset() {
        let cases = [
            ("OPENAI_API_KEY=test\n", Some("research-agent-local"), None),
            (
                "LANGSMITH_PROJECT=custom-project\n",
                Some("custom-project"),
                None,
            ),
            (
                "LANGCHAIN_PROJECT=legacy-project\n",
                None,
                Some("legacy-project"),
            ),
        ];

        for (source_env, expected_langsmith, expected_langchain) in cases {
            let root = tempfile::tempdir().unwrap();
            let out_dir = root.path().join(".mda").join("build");
            fs::create_dir_all(&out_dir).unwrap();
            fs::write(root.path().join(".env"), source_env).unwrap();
            fs::write(out_dir.join(".env"), source_env).unwrap();

            ensure_dev_tracing_project(&out_dir, "research-agent").unwrap();

            let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
            assert_eq!(
                staged.get(LANGSMITH_PROJECT_ENV).map(String::as_str),
                expected_langsmith
            );
            assert_eq!(
                staged.get(LANGCHAIN_PROJECT_ENV).map(String::as_str),
                expected_langchain
            );
            assert_eq!(
                fs::read_to_string(root.path().join(".env")).unwrap(),
                source_env
            );
        }
    }

    #[test]
    fn apply_staged_langsmith_env_injects_runtime_auth_context() {
        // Clears `LANGSMITH_API_KEY` from the process env — must not race the
        // other tests that resolve it.
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            "LANGSMITH_API_KEY=test-key\nLANGSMITH_ENDPOINT=https://example.test\nLANGSMITH_WORKSPACE_ID=workspace-1\nMDA_AGENT_AUTH_PRINCIPAL_ID=principal-1\nLANGSMITH_PROJECT=research-agent\nLANGCHAIN_PROJECT=legacy-agent\n",
        )
        .unwrap();

        let previous = DEV_LANGSMITH_SERVER_ENV_KEYS
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in DEV_LANGSMITH_SERVER_ENV_KEYS {
            std::env::remove_var(key);
        }
        let result = std::panic::catch_unwind(|| {
            let mut command = Command::new("unused");
            apply_staged_langsmith_env(&mut command, &out_dir);
            let child_env = command
                .get_envs()
                .filter_map(|(key, value)| Some((key.to_str()?, value?.to_str()?)))
                .collect::<std::collections::HashMap<_, _>>();
            assert_eq!(child_env.get(API_KEY_ENV), Some(&"test-key"));
            assert_eq!(
                child_env.get(LANGSMITH_ENDPOINT_ENV),
                Some(&"https://example.test")
            );
            assert_eq!(child_env.get(WORKSPACE_ID_ENV), Some(&"workspace-1"));
            assert_eq!(
                child_env.get(MDA_AGENT_AUTH_PRINCIPAL_ID_ENV),
                Some(&"principal-1")
            );
            assert!(!child_env.contains_key(LANGSMITH_PROJECT_ENV));
            assert!(!child_env.contains_key(LANGCHAIN_PROJECT_ENV));
        });
        for (key, value) in previous {
            match value {
                Some(value) => std::env::set_var(key, value),
                None => std::env::remove_var(key),
            }
        }
        result.expect("apply_staged_langsmith_env panicked");
    }

    #[test]
    fn dev_jobs_per_worker_resolves_shell_before_staged_env() {
        for (process, staged, expected) in [
            (None, None, None),
            (None, Some("  "), None),
            (Some(" "), None, None),
            (None, Some(" 20 "), Some(20)),
            (Some(" "), Some("20"), Some(20)),
            (Some(" 12 "), None, Some(12)),
            (Some("12"), Some("20"), Some(12)),
            (Some("12"), Some("abc"), Some(12)),
            (Some("0"), Some("20"), Some(0)),
        ] {
            let staged = staged
                .map(|value| ("N_JOBS_PER_WORKER".to_string(), value.to_string()))
                .into_iter()
                .collect();
            assert_eq!(
                dev_jobs_per_worker(&staged, process.map(str::to_string)).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn dev_jobs_per_worker_rejects_invalid_values() {
        for value in ["abc", "-1", "1.5", "999999999999999999999999999999"] {
            for process in [false, true] {
                let staged = std::collections::BTreeMap::from([(
                    "N_JOBS_PER_WORKER".to_string(),
                    value.to_string(),
                )]);
                let err =
                    dev_jobs_per_worker(&staged, process.then(|| value.to_string())).unwrap_err();
                assert_eq!(
                    err.to_string(),
                    "N_JOBS_PER_WORKER must be a non-negative integer"
                );
            }
        }
    }

    #[test]
    fn dev_child_forwards_jobs_per_worker_to_both_languages() {
        let expected = std::env::var("N_JOBS_PER_WORKER")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "20".to_string());
        let expected = expected.trim().parse::<usize>().unwrap().to_string();
        for language in [Language::TypeScript, Language::Python] {
            let root = tempfile::tempdir().unwrap();
            fs::write(root.path().join(".env"), "N_JOBS_PER_WORKER=20\n").unwrap();
            let project = Project {
                root: root.path().to_path_buf(),
                language,
                agent_file: PathBuf::from("agent"),
                files: vec![],
            };
            let command = dev_command(language, &opts()).unwrap();
            let child = langgraph_dev_process(&command, &project, root.path()).unwrap();
            let args: Vec<_> = child.get_args().filter_map(|arg| arg.to_str()).collect();
            assert!(args
                .windows(2)
                .any(|pair| pair == ["--n-jobs-per-worker", expected.as_str()]));
        }
    }

    #[test]
    fn dev_child_loopback_url_matches_server_for_both_languages() {
        for language in [Language::TypeScript, Language::Python] {
            for (port, expected) in [
                (None, "http://localhost:2024"),
                (Some(4567), "http://localhost:4567"),
            ] {
                let root = tempfile::tempdir().unwrap();
                let project = Project {
                    root: root.path().to_path_buf(),
                    language,
                    agent_file: PathBuf::from("agent"),
                    files: vec![],
                };
                let mut options = opts();
                options.port = port;
                options.tunnel = true;
                fs::write(
                    root.path().join(".env"),
                    "MDA_PUBLIC_API_URL=https://tunnel.example.com\nMDA_CHANNEL_API_URL=   \nLANGGRAPH_API_URL=\n",
                )
                .unwrap();
                let command = dev_command(language, &options).unwrap();
                let child = langgraph_dev_process(&command, &project, root.path()).unwrap();
                assert_eq!(
                    child
                        .get_envs()
                        .find(|(key, _)| *key == "MDA_CHANNEL_API_URL")
                        .and_then(|(_, value)| value)
                        .and_then(|value| value.to_str()),
                    Some(expected)
                );
                let staged = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
                assert_eq!(
                    staged.get(MDA_CHANNEL_API_URL_ENV).map(String::as_str),
                    Some(expected)
                );
                assert_eq!(
                    staged.get(MDA_PUBLIC_API_URL_ENV).map(String::as_str),
                    Some("https://tunnel.example.com")
                );
            }
        }
    }

    #[test]
    fn dev_loopback_url_keeps_explicit_overrides_and_ignores_blanks() {
        let cases = [
            ([None, None], [None, None], "http://localhost:2024"),
            (
                [Some(" "), Some("")],
                [Some(""), Some("  ")],
                "http://localhost:2024",
            ),
            (
                [
                    Some(" http://localhost:3001 "),
                    Some("http://localhost:3002"),
                ],
                [Some("http://localhost:3003"), Some("http://localhost:3004")],
                "http://localhost:3001",
            ),
            (
                [None, Some("http://localhost:3002")],
                [Some("http://localhost:3003"), None],
                "http://localhost:3003",
            ),
            (
                [None, Some("http://localhost:3002")],
                [None, Some("http://localhost:3004")],
                "http://localhost:3002",
            ),
            (
                [Some(" "), Some("")],
                [Some(""), Some(" http://localhost:3004 ")],
                "http://localhost:3004",
            ),
        ];
        for (process, staged, expected) in cases {
            let keys = [MDA_CHANNEL_API_URL_ENV, "LANGGRAPH_API_URL"];
            let staged = keys
                .into_iter()
                .zip(staged)
                .filter_map(|(key, value)| value.map(|value| (key.to_string(), value.to_string())))
                .collect();
            assert_eq!(
                dev_loopback_api_url("http://localhost:2024", &staged, |key| keys
                    .iter()
                    .position(|candidate| *candidate == key)
                    .and_then(|index| process[index])
                    .map(str::to_string)),
                expected
            );
        }
    }

    #[test]
    fn source_restage_keeps_unchanged_loopback_env_bytes_and_mtime() {
        for (source, local_url) in [
            (Some("OPENAI_API_KEY=test\n"), "http://localhost:2024"),
            (None, "http://localhost:2024"),
            (
                Some("MDA_CHANNEL_API_URL=   \nLANGGRAPH_API_URL=\n"),
                "http://localhost:4567",
            ),
            (
                Some("LANGGRAPH_API_URL=http://localhost:3002\n"),
                "http://localhost:2024",
            ),
        ] {
            let root = tempfile::tempdir().unwrap();
            let out_dir = root.path().join("build");
            fs::create_dir_all(&out_dir).unwrap();
            if let Some(source) = source {
                fs::write(root.path().join(".env"), source).unwrap();
            }
            stage_dev_env_file(root.path(), &out_dir, TEST_ASSISTANT_ID, local_url).unwrap();
            ensure_dev_loopback_api_url(&out_dir, local_url).unwrap();
            let staged = out_dir.join(".env");
            let before = fs::read(&staged).unwrap();
            let modified = fs::metadata(&staged).unwrap().modified().unwrap();
            std::thread::sleep(Duration::from_millis(20));
            stage_dev_env_file(root.path(), &out_dir, TEST_ASSISTANT_ID, local_url).unwrap();
            ensure_dev_loopback_api_url(&out_dir, local_url).unwrap();
            assert_eq!(fs::read(&staged).unwrap(), before);
            assert_eq!(
                fs::metadata(&staged).unwrap().modified().unwrap(),
                modified,
                "source-only restage must not wake the env watcher"
            );
        }
    }

    #[test]
    fn restaging_updates_child_loopback_url_after_override_changes_and_removal() {
        for language in [Language::TypeScript, Language::Python] {
            let root = tempfile::tempdir().unwrap();
            let out_dir = root.path().join("build");
            fs::create_dir_all(&out_dir).unwrap();
            let project = Project {
                root: root.path().to_path_buf(),
                language,
                agent_file: PathBuf::from("agent"),
                files: vec![],
            };
            let command = dev_command(language, &opts()).unwrap();
            for (source, expected) in [
                (
                    "MDA_CHANNEL_API_URL=http://localhost:3001\n",
                    "http://localhost:3001",
                ),
                (
                    "LANGGRAPH_API_URL=http://localhost:3002\n",
                    "http://localhost:3002",
                ),
                ("", "http://localhost:2024"),
            ] {
                fs::write(root.path().join(".env"), source).unwrap();
                stage_dev_env_file(
                    root.path(),
                    &out_dir,
                    TEST_ASSISTANT_ID,
                    "http://localhost:2024",
                )
                .unwrap();
                let child = langgraph_dev_process(&command, &project, &out_dir).unwrap();
                assert_eq!(
                    child
                        .get_envs()
                        .find(|(key, _)| *key == MDA_CHANNEL_API_URL_ENV)
                        .and_then(|(_, value)| value)
                        .and_then(|value| value.to_str()),
                    Some(expected)
                );
                assert_eq!(
                    fs::read_to_string(root.path().join(".env")).unwrap(),
                    source
                );
            }
        }
    }

    #[test]
    fn local_dev_public_api_url_defaults_to_localhost_2024() {
        assert_eq!(local_dev_public_api_url(&opts()), "http://localhost:2024");
    }

    #[test]
    fn local_dev_public_api_url_honors_port_and_rewrites_wildcard_bind() {
        let mut custom = opts();
        custom.port = Some(4000);
        custom.hostname = Some("0.0.0.0".into());
        assert_eq!(local_dev_public_api_url(&custom), "http://localhost:4000");

        custom.hostname = Some("127.0.0.1".into());
        assert_eq!(local_dev_public_api_url(&custom), "http://127.0.0.1:4000");
    }

    #[test]
    fn local_dev_public_api_url_brackets_ipv6_literals() {
        let mut custom = opts();
        custom.port = Some(2024);
        custom.hostname = Some("::1".into());
        assert_eq!(local_dev_public_api_url(&custom), "http://[::1]:2024");

        custom.hostname = Some("[::1]".into());
        assert_eq!(local_dev_public_api_url(&custom), "http://[::1]:2024");
    }

    #[test]
    fn ensure_dev_public_api_url_injects_when_missing() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var(MDA_PUBLIC_API_URL_ENV).ok();
        std::env::remove_var(MDA_PUBLIC_API_URL_ENV);
        let mut custom = opts();
        custom.port = Some(3456);
        let result = std::panic::catch_unwind(|| {
            assert!(ensure_dev_public_api_url(&out_dir, &custom).unwrap());
        });
        match previous {
            Some(value) => std::env::set_var(MDA_PUBLIC_API_URL_ENV, value),
            None => std::env::remove_var(MDA_PUBLIC_API_URL_ENV),
        }
        result.expect("ensure_dev_public_api_url panicked");

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_PUBLIC_API_URL_ENV).map(String::as_str),
            Some("http://localhost:3456")
        );
        // Staged-only — do not pollute the project .env with a loopback URL.
        assert!(!root.path().join(".env").exists());
    }

    #[test]
    fn ensure_dev_public_api_url_keeps_existing() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(
            out_dir.join(".env"),
            "MDA_PUBLIC_API_URL=https://tunnel.example.com\n",
        )
        .unwrap();

        let previous = std::env::var(MDA_PUBLIC_API_URL_ENV).ok();
        std::env::remove_var(MDA_PUBLIC_API_URL_ENV);
        let result = std::panic::catch_unwind(|| {
            assert!(!ensure_dev_public_api_url(&out_dir, &opts()).unwrap());
        });
        match previous {
            Some(value) => std::env::set_var(MDA_PUBLIC_API_URL_ENV, value),
            None => std::env::remove_var(MDA_PUBLIC_API_URL_ENV),
        }
        result.expect("ensure_dev_public_api_url panicked");

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            env.get(MDA_PUBLIC_API_URL_ENV).map(String::as_str),
            Some("https://tunnel.example.com")
        );
    }

    #[test]
    fn ensure_dev_ingress_secret_injects_local_default_when_missing() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        assert!(ensure_dev_ingress_secret_with_process_env(&out_dir, None, None).unwrap());

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        let secret = env
            .get(compile::INGRESS_SECRET_ENV)
            .map(String::as_str)
            .expect("ingress secret");
        assert!(secret.len() >= 32);
        assert_eq!(env.get("OPENAI_API_KEY").map(String::as_str), Some("test"));
    }

    #[test]
    fn ensure_dev_handoff_secret_injects_fresh_build_local_value() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        ensure_dev_handoff_secret(&out_dir).unwrap();

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        let secret = env
            .get(crate::deploy::MDA_HANDOFF_SECRET_ENV)
            .expect("handoff secret");
        assert!(secret.len() >= 32);
    }

    #[test]
    fn ensure_dev_ingress_secret_prefers_staged_server_api_key() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(out_dir.join(".env"), "LANGSMITH_API_KEY=server-key\n").unwrap();
        let project = crate::project::Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: Vec::new(),
        };

        assert!(!ensure_dev_ingress_secret(&project, &out_dir).unwrap());

        let env = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert!(!env.contains_key(compile::INGRESS_SECRET_ENV));
    }

    /// Compile `schedules/` the way `mda dev` does, so the banner is tested
    /// against the shape the compiler actually writes.
    fn compiled_schedule_crons(
        schedules: &[(&str, &str)],
    ) -> crate::compile::schedules::ScheduleCrons {
        use crate::test_support::write_file;

        let root = tempfile::tempdir().unwrap();
        crate::compile::test_support::setup_mock_ts_runtime(root.path());
        write_file(
            root.path(),
            "agent.ts",
            "export const agent = defineDeepAgent({ name: \"demo\", model: \"openai:gpt-5\" });\n",
        );
        write_file(
            root.path(),
            "identity.ts",
            "import { defineIdentity } from \"managed-deepagents\";\nexport const identity = defineIdentity({ auth: \"backend\" });\n",
        );
        let mut files = vec![PathBuf::from("agent.ts"), PathBuf::from("identity.ts")];
        for (name, cron) in schedules {
            let path = format!("schedules/{name}.ts");
            write_file(
                root.path(),
                &path,
                &format!(
                    "import {{ defineSchedule }} from \"managed-deepagents\";\n\
                     export const schedule = defineSchedule({{ cron: \"{cron}\", prompt: \"Go\" }});\n"
                ),
            );
            files.push(PathBuf::from(path));
        }
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files,
        };
        let runtime = root.path().join("node_modules/managed-deepagents/dist");
        compile::compile(&project, &root.path().join(".mda/build"), &runtime)
            .unwrap()
            .schedule_crons
    }

    fn summary_fixture() -> (Project, CompileSummary) {
        use crate::compile::{
            IdentityAuthMode, IdentityScopeAxes, IdentitySummary, MemoryDecision, MemoryLayers,
            MemorySummary,
        };

        let project = Project {
            root: PathBuf::from("/tmp/demo"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("agent.ts")],
        };
        let schedule_crons = compiled_schedule_crons(&[
            ("daily-check-in", "0 9 * * *"),
            ("weekly-digest", "0 10 * * 1"),
        ]);
        let summary = CompileSummary {
            out_dir: PathBuf::from("/tmp/demo/.mda/build"),
            entry_file: PathBuf::from("_mda_entry.ts"),
            copied: vec![PathBuf::from("agent.ts")],
            langgraph_graph: "./_mda_entry.ts:agent".into(),
            assistant_id: "demo".into(),
            connectors: vec![
                PathBuf::from("connectors/gmail.ts"),
                PathBuf::from("connectors/notion.ts"),
            ],
            channels: vec![],
            channel_providers: Default::default(),
            identity: Some(IdentitySummary {
                path: PathBuf::from("identity.ts"),
                auth: Some(IdentityAuthMode::Backend),
                auth_omitted: false,
                scope: Some(IdentityScopeAxes {
                    credentials: "agent".into(),
                }),
                declares_memory: false,
            }),
            memory: MemoryDecision::Declared(MemorySummary {
                path: PathBuf::from("memory.ts"),
                scope: MemoryLayers {
                    agent: true,
                    user: false,
                },
            }),
            sandbox: true,
            has_skills: false,
            schedule_crons,
            warnings: Vec::new(),
        };
        (project, summary)
    }

    #[test]
    fn feature_summary_lists_schedules_and_omits_empty_skills() {
        let (project, summary) = summary_fixture();
        let labels = vec!["gmail".into(), "notion (word)".into()];
        let lines = feature_summary_lines(
            &project,
            &summary,
            &labels,
            Some(PublicApiUrl::Missing),
            true,
            true,
        );
        let joined = lines.join("\n");
        assert!(
            joined.contains("Agent          demo (agent.ts)"),
            "{joined}"
        );
        assert!(
            joined.contains(
                "Identity       identity.ts\n│                 auth=backend\n│                 credentials=agent"
            ),
            "{joined}"
        );
        // Memory reports its own line: scope + file, never contents.
        assert!(
            joined.contains("Memory         agent\n│                 memory.ts"),
            "{joined}"
        );
        assert!(
            joined.contains("local Studio uses MDA_LOCAL_DEV"),
            "{joined}"
        );
        assert!(
            joined.contains("Connectors     gmail, notion (word)"),
            "{joined}"
        );
        assert!(
            joined
                .contains("Schedules      daily-check-in (0 9 * * *), weekly-digest (0 10 * * 1)"),
            "{joined}"
        );
        assert!(!joined.contains("not provisioned"), "{joined}");
        assert!(joined.contains("Sandbox        LangSmith"), "{joined}");
        assert!(
            joined.contains("Public URL     not set — Events connectors need"),
            "{joined}"
        );
        assert!(!joined.contains("Skills"), "{joined}");
        assert!(
            joined.contains("Context        .mda/__contexthub__"),
            "{joined}"
        );
    }

    #[test]
    fn feature_summary_says_when_schedules_stay_off_the_local_server() {
        let (project, summary) = summary_fixture();
        let lines = feature_summary_lines(&project, &summary, &[], None, false, false);
        let joined = lines.join("\n");
        assert!(
            joined.contains(
                "Schedules      daily-check-in (0 9 * * *), weekly-digest (0 10 * * 1)\n│                 not provisioned locally (--no-schedules)"
            ),
            "{joined}"
        );
    }

    #[test]
    fn feature_summary_notes_github_channel_is_not_available_in_dev() {
        let (project, mut summary) = summary_fixture();
        summary.channels = vec![PathBuf::from("channels/github.ts")];
        summary.channel_providers.insert("github".to_string());
        let lines = feature_summary_lines(&project, &summary, &[], None, false, true);
        let joined = lines.join("\n");
        assert!(
            joined
                .contains("GitHub         not available in dev · deploy to receive GitHub events"),
            "{joined}"
        );
        assert!(!joined.contains("Public URL"), "{joined}");
    }

    #[test]
    fn feature_summary_omits_connectors_schedules_sandbox_when_absent() {
        let (project, mut summary) = summary_fixture();
        summary.connectors.clear();
        summary.schedule_crons.clear();
        summary.sandbox = false;
        summary.identity = None;
        let lines = feature_summary_lines(&project, &summary, &[], None, false, true);
        let joined = lines.join("\n");
        assert!(
            joined.contains("Identity       none — no managed auth"),
            "{joined}"
        );
        assert!(!joined.contains("Connectors"), "{joined}");
        assert!(!joined.contains("Schedules"), "{joined}");
        assert!(!joined.contains("Sandbox"), "{joined}");
        assert!(!joined.contains("Public URL"), "{joined}");
        assert!(!joined.contains("GitHub"), "{joined}");
    }

    #[test]
    fn public_url_https_is_shown_when_set() {
        assert_eq!(
            format_public_url_line(
                Some(&PublicApiUrl::Https("https://tunnel.example".into())),
                false
            ),
            Some("Public URL     https://tunnel.example".into())
        );
    }

    #[test]
    fn connector_label_annotates_mismatched_factory() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("notion.ts"),
            "import { defineMcp } from \"managed-deepagents\";\n\
             export const connector = defineMcp({ servers: {} });\n",
        )
        .unwrap();
        // read_connector_factory expects path relative to project.root
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("notion.ts")],
        };
        // Place file under connectors/ as real projects do.
        fs::create_dir_all(root.path().join("connectors")).unwrap();
        fs::rename(
            root.path().join("notion.ts"),
            root.path().join("connectors/notion.ts"),
        )
        .unwrap();
        let labels =
            connector_summary_labels(&project, &[PathBuf::from("connectors/notion.ts")]).unwrap();
        assert_eq!(labels, vec!["notion (mcp)".to_string()]);
    }

    #[test]
    fn connector_label_annotates_mismatched_python_factory() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("connectors")).unwrap();
        fs::write(
            root.path().join("connectors/notion.py"),
            "from managed_deepagents import define_mcp\n\
             connector = define_mcp(servers={\"docs\": {\"transport\": \"http\", \"url\": \"https://docs.example.com/mcp\"}})\n",
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("connectors/notion.py")],
        };
        let labels =
            connector_summary_labels(&project, &[PathBuf::from("connectors/notion.py")]).unwrap();
        assert_eq!(labels, vec!["notion (mcp)".to_string()]);
    }

    #[test]
    fn connector_label_omits_matching_mcp_factory() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("connectors")).unwrap();
        fs::write(
            root.path().join("connectors/mcp.ts"),
            "import { defineMcp } from \"managed-deepagents\";\n\
             export const connector = defineMcp({ servers: {} });\n",
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("connectors/mcp.ts")],
        };
        let labels =
            connector_summary_labels(&project, &[PathBuf::from("connectors/mcp.ts")]).unwrap();
        assert_eq!(labels, vec!["mcp".to_string()]);
    }

    #[test]
    fn connector_label_still_resolves_deprecated_connectors_mcp() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("connectors")).unwrap();
        fs::write(
            root.path().join("connectors/notion.ts"),
            "import { connectors } from \"managed-deepagents\";\n\
             export const connector = connectors.mcp({ servers: {} });\n",
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("connectors/notion.ts")],
        };
        let labels =
            connector_summary_labels(&project, &[PathBuf::from("connectors/notion.ts")]).unwrap();
        assert_eq!(labels, vec!["notion (mcp)".to_string()]);
    }

    #[test]
    fn stage_dev_env_file_removes_stale_project_keys_when_root_env_is_absent() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        let staged = out_dir.join(".env");
        fs::write(
            &staged,
            "STALE_SECRET=1\nMDA_STALE_VALUE=1\nLANGSMITH_PROJECT=custom\n",
        )
        .unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(&staged).unwrap(),
            "MDA_CHANNEL_API_URL=http://localhost:2024\n"
        );
    }

    #[test]
    fn stage_dev_env_file_falls_back_to_env_local_when_env_is_absent() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env.local"), "MDA_INGRESS_SECRET=test\n").unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(out_dir.join(".env")).unwrap(),
            "MDA_INGRESS_SECRET=test\nMDA_CHANNEL_API_URL=http://localhost:2024\n"
        );
        assert!(!out_dir.join(".env.local").exists());
    }

    #[test]
    fn stage_dev_env_file_prefers_env_over_env_local() {
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        fs::create_dir_all(&out_dir).unwrap();
        fs::write(root.path().join(".env"), "OPENAI_API_KEY=from-env\n").unwrap();
        fs::write(
            root.path().join(".env.local"),
            "OPENAI_API_KEY=from-local\n",
        )
        .unwrap();

        stage_dev_env_file(
            root.path(),
            &out_dir,
            TEST_ASSISTANT_ID,
            "http://localhost:2024",
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(out_dir.join(".env")).unwrap(),
            "OPENAI_API_KEY=from-env\nMDA_CHANNEL_API_URL=http://localhost:2024\n"
        );
    }

    #[test]
    fn typescript_uses_the_node_langgraph_cli() {
        let command = dev_command(Language::TypeScript, &opts()).unwrap();
        assert_eq!(command.program, "npx");
        assert_eq!(
            command.args,
            vec!["--yes", "@langchain/langgraph-cli@^1.5.1", "dev"]
        );
    }

    #[test]
    fn python_uses_the_python_langgraph_cli() {
        let command = dev_command(Language::Python, &opts()).unwrap();
        assert_eq!(command.program, "uv");
        assert_eq!(
            command.args,
            vec![
                "run",
                "--with",
                "langgraph-cli[inmem]>=0.4.36",
                "--with",
                &format!(
                    "langgraph-api=={}",
                    crate::compile::resolved_langgraph_api_version().unwrap()
                ),
                "--with",
                &format!(
                    "langgraph-runtime-inmem=={}",
                    crate::compile::LANGGRAPH_RUNTIME_INMEM_VERSION
                ),
                "langgraph",
                "dev"
            ]
        );
    }

    #[test]
    fn forwards_port_and_hostname_as_langgraph_flags() {
        let command = dev_command(
            Language::TypeScript,
            &DevOptions {
                port: Some(4000),
                hostname: Some("0.0.0.0".to_string()),
                ..opts()
            },
        )
        .unwrap();
        assert!(command
            .args
            .windows(2)
            .any(|pair| pair == ["--port", "4000"]));
        assert!(command
            .args
            .windows(2)
            .any(|pair| pair == ["--host", "0.0.0.0"]));
    }

    #[test]
    fn forwards_boolean_flags_when_set() {
        let command = dev_command(
            Language::Python,
            &DevOptions {
                no_browser: true,
                no_reload: true,
                tunnel: true,
                ..opts()
            },
        )
        .unwrap();
        assert!(command.args.contains(&"--no-browser".to_string()));
        assert!(command.args.contains(&"--no-reload".to_string()));
        assert!(command.args.contains(&"--tunnel".to_string()));
    }

    #[test]
    fn forwards_tunnel_to_both_language_clis() {
        for language in [Language::TypeScript, Language::Python] {
            let command = dev_command(
                language,
                &DevOptions {
                    tunnel: true,
                    ..opts()
                },
            )
            .unwrap();
            assert!(
                command.args.contains(&"--tunnel".to_string()),
                "{language:?} dropped --tunnel: {:?}",
                command.args
            );
        }
    }

    #[test]
    fn omits_optional_flags_by_default() {
        let command = dev_command(Language::TypeScript, &opts()).unwrap();
        assert!(!command.args.contains(&"--port".to_string()));
        assert!(!command.args.contains(&"--host".to_string()));
        assert!(!command.args.contains(&"--no-browser".to_string()));
        assert!(!command.args.contains(&"--no-reload".to_string()));
        assert!(!command.args.contains(&"--tunnel".to_string()));
    }

    #[test]
    fn spawn_error_points_python_users_at_the_inmem_extra() {
        let err = dev_spawn_error(
            Language::Python,
            "uv",
            std::io::Error::from(std::io::ErrorKind::NotFound),
        );
        assert!(err.to_string().contains("uv"));
        assert!(err.to_string().contains("langgraph-cli[inmem]"));
    }

    #[test]
    fn spawn_error_points_typescript_users_at_node() {
        let err = dev_spawn_error(
            Language::TypeScript,
            "npx",
            std::io::Error::from(std::io::ErrorKind::NotFound),
        );
        assert!(err.to_string().contains("npx"));
    }

    #[test]
    fn preserve_symlinks_is_set_when_node_options_is_unset() {
        assert_eq!(
            node_options_with_preserve_symlinks(None),
            "--preserve-symlinks"
        );
    }

    #[test]
    fn preserve_symlinks_is_set_when_node_options_is_empty() {
        assert_eq!(
            node_options_with_preserve_symlinks(Some("   ".to_string())),
            "--preserve-symlinks"
        );
    }

    #[test]
    fn preserve_symlinks_is_appended_to_existing_node_options() {
        assert_eq!(
            node_options_with_preserve_symlinks(Some("--max-old-space-size=4096".to_string())),
            "--max-old-space-size=4096 --preserve-symlinks"
        );
    }

    #[test]
    fn preserve_symlinks_is_not_duplicated() {
        assert_eq!(
            node_options_with_preserve_symlinks(Some(
                "--preserve-symlinks --enable-source-maps".to_string()
            )),
            "--preserve-symlinks --enable-source-maps"
        );
    }

    #[test]
    fn typescript_deps_fingerprint_changes_when_package_json_changes() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("package.json"), "{\"name\":\"a\"}\n").unwrap();
        fs::write(
            dir.path().join("package-lock.json"),
            "{\"lockfileVersion\":3}\n",
        )
        .unwrap();
        let first = typescript_deps_fingerprint(dir.path()).unwrap();

        fs::write(dir.path().join("package.json"), "{\"name\":\"b\"}\n").unwrap();
        let second = typescript_deps_fingerprint(dir.path()).unwrap();
        assert_ne!(first, second);
    }

    #[test]
    fn python_deps_fingerprint_changes_when_a_local_package_manifest_changes() {
        let dir = tempfile::tempdir().unwrap();
        crate::test_support::write_file(dir.path(), "pyproject.toml", "[project]\nname = \"a\"\n");
        crate::test_support::write_file(
            dir.path(),
            "__local__/my-dep/pyproject.toml",
            "[project]\nname = \"my-dep\"\ndependencies = []\n",
        );
        let first = python_deps_fingerprint(dir.path()).unwrap();

        crate::test_support::write_file(
            dir.path(),
            "__local__/my-dep/pyproject.toml",
            "[project]\nname = \"my-dep\"\ndependencies = [\"httpx\"]\n",
        );
        let second = python_deps_fingerprint(dir.path()).unwrap();
        assert_ne!(first, second);
    }

    #[test]
    fn python_deps_fingerprint_changes_when_pyproject_changes() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[project]\nname = \"a\"\ndependencies = []\n",
        )
        .unwrap();
        let first = python_deps_fingerprint(dir.path()).unwrap();

        fs::write(
            dir.path().join("pyproject.toml"),
            "[project]\nname = \"a\"\ndependencies = [\"httpx\"]\n",
        )
        .unwrap();
        let second = python_deps_fingerprint(dir.path()).unwrap();
        assert_ne!(first, second);
    }

    #[test]
    fn install_typescript_build_deps_skips_when_stamp_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("package.json"),
            "{\"name\":\"smoke\",\"private\":true}\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("package-lock.json"),
            "{\"lockfileVersion\":3}\n",
        )
        .unwrap();
        fs::create_dir_all(dir.path().join("node_modules")).unwrap();
        let fingerprint = typescript_deps_fingerprint(dir.path()).unwrap();
        write_deps_stamp(dir.path(), &fingerprint).unwrap();

        // Would fail if it tried to run npm ci against this incomplete manifest.
        install_typescript_build_deps(dir.path()).unwrap();
    }

    #[test]
    fn install_python_build_deps_skips_when_stamp_matches() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("pyproject.toml"),
            "[project]\nname = \"smoke\"\nversion = \"0.0.0\"\ndependencies = []\n",
        )
        .unwrap();
        fs::create_dir_all(dir.path().join(".venv")).unwrap();
        let fingerprint = python_deps_fingerprint(dir.path()).unwrap();
        write_deps_stamp(dir.path(), &fingerprint).unwrap();

        // Would fail if it tried to run uv sync against this incomplete project.
        assert!(matches!(
            install_python_build_deps(dir.path()).unwrap(),
            DepsInstall::UpToDate
        ));
    }
}
