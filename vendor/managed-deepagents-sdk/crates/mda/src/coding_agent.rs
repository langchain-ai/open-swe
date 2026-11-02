//! Coding-agent discovery and handoff for MDA authoring workflows.

use std::ffi::{OsStr, OsString};
use std::fs;
use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use anyhow::{bail, Context, Result};
use cliclack::{note, reset_theme, select, set_theme, Theme, ThemeState};

use crate::error::{categorized, ErrorCategory};
use crate::interrupt::Interrupt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CodingAgent {
    DeepAgentsCode,
    Claude,
    Codex,
    Cursor,
    Droid,
    Gemini,
    OpenCode,
    Pi,
}

const CODING_AGENTS: [CodingAgent; 8] = [
    CodingAgent::DeepAgentsCode,
    CodingAgent::Claude,
    CodingAgent::Codex,
    CodingAgent::Cursor,
    CodingAgent::Droid,
    CodingAgent::Gemini,
    CodingAgent::OpenCode,
    CodingAgent::Pi,
];

const WINDOWS_EXECUTABLE_EXTENSIONS: [&str; 4] = [".COM", ".EXE", ".BAT", ".CMD"];
const DEEP_AGENTS_CODE_INSTALL_COMMAND: &str = "curl -LsSf https://langch.in/dcode | bash";
const DEEP_AGENTS_CODE_WINDOWS_GUIDANCE: &str =
    "Deep Agents Code requires WSL on Windows. Run the install command above inside WSL.";
const HANDOFF_HEADING: &str = "Handoff to a coding agent";
const HANDOFF_EXIT_LABEL: &str = "Exit";

impl CodingAgent {
    const fn command(self) -> &'static str {
        match self {
            Self::DeepAgentsCode => "dcode",
            Self::Claude => "claude",
            Self::Codex => "codex",
            Self::Cursor => "cursor-agent",
            Self::Droid => "droid",
            Self::Gemini => "gemini",
            Self::OpenCode => "opencode",
            Self::Pi => "pi",
        }
    }

    const fn label(self) -> &'static str {
        match self {
            Self::DeepAgentsCode => "Deep Agents Code",
            Self::Claude => "Claude Code",
            Self::Codex => "Codex",
            Self::Cursor => "Cursor",
            Self::Droid => "Droid",
            Self::Gemini => "Gemini CLI",
            Self::OpenCode => "opencode",
            Self::Pi => "Pi",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AgentOption {
    agent: CodingAgent,
    installed: bool,
}

impl AgentOption {
    fn label(self) -> String {
        self.agent.label().to_string()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum HandoffChoice {
    Agent(AgentOption),
    ViewRawPrompt,
    Exit,
}

struct DefaultTheme;

impl Theme for DefaultTheme {}

struct HandoffTheme {
    complete: bool,
}

impl Theme for HandoffTheme {
    fn format_select_item(
        &self,
        state: &ThemeState,
        selected: bool,
        label: &str,
        hint: &str,
    ) -> String {
        if label == HANDOFF_EXIT_LABEL
            && (matches!(state, ThemeState::Active)
                || (self.complete && matches!(state, ThemeState::Submit) && selected))
        {
            let trailing_line = if matches!(state, ThemeState::Submit) {
                "\n"
            } else {
                ""
            };
            return format!(
                "{}  {}\n{trailing_line}",
                self.bar_color(state).apply_to("└"),
                self.radio_item(state, selected, label, hint)
            );
        }
        let item = DefaultTheme.format_select_item(state, selected, label, hint);
        if matches!(state, ThemeState::Submit) && selected {
            return format!(
                "{item}{}",
                DefaultTheme.format_footer_with_message(state, "")
            );
        }
        item
    }

    fn format_footer_with_message(&self, state: &ThemeState, message: &str) -> String {
        if matches!(state, ThemeState::Active) {
            return "\n".to_string();
        }
        if matches!(state, ThemeState::Submit) {
            return String::new();
        }
        DefaultTheme.format_footer_with_message(state, message)
    }
}

pub(crate) struct ResetTheme;

impl Drop for ResetTheme {
    fn drop(&mut self) {
        reset_theme();
    }
}

impl HandoffChoice {
    fn label(self) -> String {
        match self {
            Self::Agent(option) => option.label(),
            Self::ViewRawPrompt => "View raw prompt".to_string(),
            Self::Exit => HANDOFF_EXIT_LABEL.to_string(),
        }
    }
}

fn handoff_choices(options: impl IntoIterator<Item = AgentOption>) -> Vec<HandoffChoice> {
    options
        .into_iter()
        .map(HandoffChoice::Agent)
        .chain([HandoffChoice::ViewRawPrompt, HandoffChoice::Exit])
        .collect()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum InstallChoice {
    Install,
    GoBack,
}

const DEEP_AGENTS_CODE_INSTALL_CHOICES: [InstallChoice; 2] =
    [InstallChoice::Install, InstallChoice::GoBack];

impl InstallChoice {
    const fn label(self) -> &'static str {
        match self {
            Self::Install => "Install",
            Self::GoBack => "Go back",
        }
    }

    const fn hint(self) -> &'static str {
        match self {
            Self::Install => "Esc to cancel",
            Self::GoBack => "",
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
struct CommandPlan {
    program: PathBuf,
    args: Vec<OsString>,
}

#[derive(Debug, Eq, PartialEq)]
enum InstallerAction {
    Run(CommandPlan),
    RequiresWsl { guidance: &'static str },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum InstallFlowOutcome {
    ReturnToSelector,
    RequiresWsl { guidance: &'static str },
    Started,
    InstalledNeedsRestart,
}

fn installer_action(native_windows: bool) -> InstallerAction {
    if native_windows {
        return InstallerAction::RequiresWsl {
            guidance: DEEP_AGENTS_CODE_WINDOWS_GUIDANCE,
        };
    }

    InstallerAction::Run(CommandPlan {
        program: PathBuf::from("/bin/sh"),
        args: vec![
            OsString::from("-c"),
            OsString::from(DEEP_AGENTS_CODE_INSTALL_COMMAND),
        ],
    })
}

fn normalize_install_choice(
    choice: std::io::Result<InstallChoice>,
) -> std::io::Result<Option<InstallChoice>> {
    match choice {
        Ok(choice) => Ok(Some(choice)),
        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => Ok(None),
        Err(error) => Err(error),
    }
}

fn executable_names(
    command: &str,
    native_windows: bool,
    path_ext: Option<&OsStr>,
) -> Vec<OsString> {
    if !native_windows {
        return vec![OsString::from(command)];
    }

    let extensions = path_ext
        .map(|value| {
            value
                .to_string_lossy()
                .split(';')
                .filter(|extension| !extension.is_empty())
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .filter(|extensions| !extensions.is_empty())
        .unwrap_or_else(|| {
            WINDOWS_EXECUTABLE_EXTENSIONS
                .into_iter()
                .map(str::to_owned)
                .collect()
        });
    extensions
        .into_iter()
        .map(|extension| OsString::from(format!("{command}{extension}")))
        .collect()
}

fn is_windows_command_shim(executable: &Path, native_windows: bool) -> bool {
    native_windows
        && executable
            .extension()
            .and_then(OsStr::to_str)
            .is_some_and(|extension| {
                extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat")
            })
}

fn is_executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn resolve_agent_in_for_platform(
    agent: CodingAgent,
    path: Option<&OsStr>,
    native_windows: bool,
    path_ext: Option<&OsStr>,
) -> Option<PathBuf> {
    let path = path.filter(|value| !value.is_empty())?;
    for directory in std::env::split_paths(path) {
        if directory.as_os_str().is_empty() || !directory.is_absolute() {
            continue;
        }
        for executable in executable_names(agent.command(), native_windows, path_ext) {
            let candidate = directory.join(executable);
            if agent == CodingAgent::DeepAgentsCode
                && is_windows_command_shim(&candidate, native_windows)
            {
                continue;
            }
            if is_executable(&candidate) {
                return fs::canonicalize(candidate).ok();
            }
        }
    }
    None
}

fn resolve_agent_in(agent: CodingAgent, path: Option<&OsStr>) -> Option<PathBuf> {
    resolve_agent_in_for_platform(
        agent,
        path,
        cfg!(windows),
        std::env::var_os("PATHEXT").as_deref(),
    )
}

fn agent_options_in_for_platform(
    path: Option<&OsStr>,
    native_windows: bool,
    path_ext: Option<&OsStr>,
) -> Vec<AgentOption> {
    CODING_AGENTS
        .into_iter()
        .filter_map(|agent| {
            let installed =
                resolve_agent_in_for_platform(agent, path, native_windows, path_ext).is_some();
            (agent == CodingAgent::DeepAgentsCode || installed)
                .then_some(AgentOption { agent, installed })
        })
        .collect()
}

fn agent_options_in(path: Option<&OsStr>) -> Vec<AgentOption> {
    agent_options_in_for_platform(path, cfg!(windows), std::env::var_os("PATHEXT").as_deref())
}

fn prompt_args(agent: CodingAgent, prompt: &str) -> Vec<OsString> {
    let prompt = OsString::from(prompt);
    match agent {
        CodingAgent::DeepAgentsCode => vec![OsString::from("-m"), prompt],
        CodingAgent::Gemini => vec![OsString::from("-i"), prompt],
        CodingAgent::OpenCode => vec![OsString::from("--prompt"), prompt],
        _ => vec![prompt],
    }
}

/// Why an interactive coding-agent handoff did or did not start.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HandoffOutcome {
    /// The selected coding agent ran and exited successfully.
    Started,
    /// Deep Agents Code was installed, but the current `PATH` needs a shell restart.
    InstalledNeedsRestart,
    /// The exact onboarding prompt was printed to standard output.
    PromptShown,
    /// The user selected Exit or cancelled the selection prompt.
    Cancelled,
}

#[derive(Debug, Eq, PartialEq)]
struct SpawnPlan {
    command: CommandPlan,
    prompt_seeded: bool,
}

fn spawn_plan(
    agent: CodingAgent,
    executable: &Path,
    prompt: &str,
    windows: bool,
) -> Result<SpawnPlan> {
    let is_command_shim = is_windows_command_shim(executable, windows);
    if is_command_shim {
        if agent == CodingAgent::DeepAgentsCode {
            bail!("Deep Agents Code requires WSL on Windows");
        }
        let quoted_executable = format!(r#""{}""#, executable.display());
        return Ok(SpawnPlan {
            command: CommandPlan {
                program: PathBuf::from("cmd.exe"),
                args: ["/D", "/S", "/C"]
                    .into_iter()
                    .map(OsString::from)
                    .chain([OsString::from(quoted_executable)])
                    .collect(),
            },
            prompt_seeded: false,
        });
    }

    Ok(SpawnPlan {
        command: CommandPlan {
            program: executable.to_path_buf(),
            args: prompt_args(agent, prompt),
        },
        prompt_seeded: true,
    })
}

// The child process owns the terminal, so fallback handoff text must stay plain.
#[expect(clippy::print_stderr, reason = "plain fallback for a child process")]
fn spawn_coding_agent_at(
    agent: CodingAgent,
    executable: &Path,
    cwd: &Path,
    prompt: &str,
) -> Result<()> {
    let plan = spawn_plan(agent, executable, prompt, cfg!(windows))?;
    if !plan.prompt_seeded {
        eprintln!(
            "Could not seed {} automatically. Paste this prompt after it opens:\n\n{prompt}",
            agent.label()
        );
    }

    // Installed so a Ctrl-C the agent lets through returns here instead of
    // killing mda mid-wait, which would skip the caller's reporting.
    let interrupt = Interrupt::install()?;
    let child = Command::new(&plan.command.program)
        .args(&plan.command.args)
        .current_dir(cwd)
        .spawn()
        .with_context(|| format!("failed to open {}", agent.label()))?;

    wait_for_coding_agent(child, agent, cwd, &interrupt)
}

/// Wait for an agent that owns the terminal.
///
/// Deliberately not an [`OwnedChild`](crate::child::OwnedChild): an interactive
/// agent needs the foreground process group to read the TTY, and killing one
/// leaves the terminal in raw mode. A nonzero status after the user quit is the
/// agent shutting down, not a failure of its own.
fn wait_for_coding_agent(
    mut child: Child,
    agent: CodingAgent,
    cwd: &Path,
    interrupt: &Interrupt,
) -> Result<()> {
    let status = child
        .wait()
        .with_context(|| format!("failed while waiting for {}", agent.label()))?;
    if status.success() || interrupt.is_requested() {
        return Ok(());
    }
    bail!(
        "{} exited unsuccessfully in {}",
        agent.label(),
        cwd.display()
    )
}

fn spawn_coding_agent(agent: CodingAgent, cwd: &Path, prompt: &str) -> Result<()> {
    let executable = resolve_agent_in(agent, std::env::var_os("PATH").as_deref())
        .with_context(|| format!("{} is no longer available on PATH", agent.label()))?;
    spawn_coding_agent_at(agent, &executable, cwd, prompt)
}

fn choose_deep_agents_code_install() -> Result<Option<InstallChoice>> {
    note("Install Deep Agents Code", DEEP_AGENTS_CODE_INSTALL_COMMAND)?;
    let mut choice = select("Continue with installation?");
    for item in DEEP_AGENTS_CODE_INSTALL_CHOICES {
        choice = choice.item(item, item.label(), item.hint());
    }

    normalize_install_choice(choice.interact())
        .context("failed to choose whether to install Deep Agents Code")
}

fn run_installer(plan: &CommandPlan) -> Result<()> {
    let status = Command::new(&plan.program)
        .args(&plan.args)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .context("failed to start the Deep Agents Code installer")?;
    if !status.success() {
        return Err(categorized(
            ErrorCategory::Toolchain,
            format!("Deep Agents Code installer exited unsuccessfully ({status})"),
        ));
    }
    Ok(())
}

fn run_install_flow(
    choice: Option<InstallChoice>,
    action: InstallerAction,
    mut runner: impl FnMut(&CommandPlan) -> Result<()>,
    mut resolver: impl FnMut() -> Option<PathBuf>,
    mut launcher: impl FnMut(&Path) -> Result<()>,
) -> Result<InstallFlowOutcome> {
    if choice != Some(InstallChoice::Install) {
        return Ok(InstallFlowOutcome::ReturnToSelector);
    }

    let plan = match action {
        InstallerAction::Run(plan) => plan,
        InstallerAction::RequiresWsl { guidance } => {
            return Ok(InstallFlowOutcome::RequiresWsl { guidance });
        }
    };
    runner(&plan)?;

    let Some(executable) = resolver() else {
        return Ok(InstallFlowOutcome::InstalledNeedsRestart);
    };
    launcher(&executable)?;
    Ok(InstallFlowOutcome::Started)
}

fn installed_needs_restart_note(cwd: &Path, prompt: &str) -> (&'static str, String) {
    (
        "Deep Agents Code installed",
        format!(
            "Restart your shell, then continue in the project directory:\n\n{}\n\n\
             Run `dcode`. When it opens, paste these instructions:\n\n{prompt}",
            cwd.display()
        ),
    )
}

fn write_raw_prompt(mut writer: impl Write, prompt: &str) -> std::io::Result<()> {
    writer.write_all(prompt.as_bytes())?;
    if !prompt.ends_with('\n') {
        writer.write_all(b"\n")?;
    }
    writer.flush()
}

/// Whether standard input and error are both terminals, as the handoff
/// picker needs.
pub(crate) fn has_terminal() -> bool {
    std::io::stdin().is_terminal() && std::io::stderr().is_terminal()
}

/// Reject `-i` without a terminal, before any work starts.
pub(crate) fn require_terminal() -> Result<()> {
    if has_terminal() {
        return Ok(());
    }
    Err(categorized(
        ErrorCategory::UserInput,
        "coding-agent handoff requires a terminal; omit `-i` to disable it",
    ))
}

/// Offer coding agents, or print the raw onboarding prompt instead.
///
/// Callers check [`require_terminal`] first, before doing any work.
pub fn offer_handoff(cwd: &Path, prompt: &str, complete: bool) -> Result<HandoffOutcome> {
    loop {
        let mut choice = select(HANDOFF_HEADING);
        for item in handoff_choices(agent_options_in(std::env::var_os("PATH").as_deref())) {
            choice = choice.item(item, item.label(), "");
        }
        let interaction = {
            set_theme(HandoffTheme { complete });
            let _theme_reset = ResetTheme;
            choice.interact()
        };
        let selected = match interaction {
            Ok(selected) => selected,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
                return Ok(HandoffOutcome::Cancelled);
            }
            Err(error) => return Err(error).context("failed to select a coding agent"),
        };
        let selected = match selected {
            HandoffChoice::Agent(option) => option,
            HandoffChoice::ViewRawPrompt => {
                write_raw_prompt(std::io::stdout().lock(), prompt)
                    .context("failed to display the raw onboarding prompt")?;
                return Ok(HandoffOutcome::PromptShown);
            }
            HandoffChoice::Exit => return Ok(HandoffOutcome::Cancelled),
        };

        if selected.installed {
            spawn_coding_agent(selected.agent, cwd, prompt)?;
            return Ok(HandoffOutcome::Started);
        }
        debug_assert_eq!(selected.agent, CodingAgent::DeepAgentsCode);

        let install_flow = run_install_flow(
            choose_deep_agents_code_install()?,
            installer_action(cfg!(windows)),
            run_installer,
            || {
                resolve_agent_in(
                    CodingAgent::DeepAgentsCode,
                    std::env::var_os("PATH").as_deref(),
                )
            },
            |executable| {
                spawn_coding_agent_at(CodingAgent::DeepAgentsCode, executable, cwd, prompt)
            },
        )?;
        match install_flow {
            InstallFlowOutcome::ReturnToSelector => continue,
            InstallFlowOutcome::RequiresWsl { guidance } => {
                note("WSL required", guidance)?;
                continue;
            }
            InstallFlowOutcome::Started => return Ok(HandoffOutcome::Started),
            InstallFlowOutcome::InstalledNeedsRestart => {
                let (title, body) = installed_needs_restart_note(cwd, prompt);
                note(title, body)?;
                return Ok(HandoffOutcome::InstalledNeedsRestart);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::fs;

    use super::*;

    fn inert_installer_action() -> InstallerAction {
        InstallerAction::Run(CommandPlan {
            program: PathBuf::from("not-run"),
            args: Vec::new(),
        })
    }

    #[test]
    fn deep_agents_code_is_first() {
        assert_eq!(CODING_AGENTS[0], CodingAgent::DeepAgentsCode);
    }

    #[test]
    fn coding_agent_allowlist_uses_the_supported_display_order() {
        let commands: Vec<&str> = CODING_AGENTS
            .into_iter()
            .map(CodingAgent::command)
            .collect();

        assert_eq!(
            commands,
            [
                "dcode",
                "claude",
                "codex",
                "cursor-agent",
                "droid",
                "gemini",
                "opencode",
                "pi",
            ]
        );
    }

    #[test]
    fn agent_options_without_path_only_include_unavailable_deep_agents_code() {
        assert_eq!(
            agent_options_in(None),
            vec![AgentOption {
                agent: CodingAgent::DeepAgentsCode,
                installed: false,
            }]
        );
    }

    #[test]
    fn unavailable_deep_agents_code_label_is_unchanged() {
        let option = AgentOption {
            agent: CodingAgent::DeepAgentsCode,
            installed: false,
        };

        assert_eq!(option.label(), "Deep Agents Code");
    }

    #[test]
    fn installed_deep_agents_code_label_is_unchanged() {
        let option = AgentOption {
            agent: CodingAgent::DeepAgentsCode,
            installed: true,
        };

        assert_eq!(option.label(), "Deep Agents Code");
    }

    #[test]
    fn unavailable_other_agent_label_is_unchanged() {
        let option = AgentOption {
            agent: CodingAgent::Claude,
            installed: false,
        };

        assert_eq!(option.label(), "Claude Code");
    }

    #[cfg(unix)]
    #[test]
    fn agent_options_include_only_executable_allowlisted_files() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let dcode = root.path().join("dcode");
        let claude = root.path().join("claude");
        let codex = root.path().join("codex");
        let unlisted = root.path().join("other-agent");
        fs::write(&dcode, "#!/bin/sh\n").unwrap();
        fs::write(&claude, "#!/bin/sh\n").unwrap();
        fs::write(&codex, "#!/bin/sh\n").unwrap();
        fs::write(&unlisted, "#!/bin/sh\n").unwrap();
        fs::set_permissions(&dcode, fs::Permissions::from_mode(0o755)).unwrap();
        fs::set_permissions(&claude, fs::Permissions::from_mode(0o755)).unwrap();
        fs::set_permissions(&codex, fs::Permissions::from_mode(0o644)).unwrap();
        fs::set_permissions(&unlisted, fs::Permissions::from_mode(0o755)).unwrap();

        let options = agent_options_in(Some(root.path().as_os_str()));

        assert_eq!(
            options,
            vec![
                AgentOption {
                    agent: CodingAgent::DeepAgentsCode,
                    installed: true,
                },
                AgentOption {
                    agent: CodingAgent::Claude,
                    installed: true,
                },
            ]
        );
    }

    #[cfg(unix)]
    #[test]
    fn agent_options_skip_empty_path_entries() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let claude = root.path().join("claude");
        fs::write(&claude, "#!/bin/sh\n").unwrap();
        fs::set_permissions(&claude, fs::Permissions::from_mode(0o755)).unwrap();
        let path = std::env::join_paths([OsStr::new(""), root.path().as_os_str()]).unwrap();

        let options = agent_options_in(Some(&path));

        assert_eq!(
            options,
            vec![
                AgentOption {
                    agent: CodingAgent::DeepAgentsCode,
                    installed: false,
                },
                AgentOption {
                    agent: CodingAgent::Claude,
                    installed: true,
                },
            ]
        );
    }

    #[test]
    fn agent_options_reject_empty_path() {
        assert_eq!(
            agent_options_in(Some(OsStr::new(""))),
            vec![AgentOption {
                agent: CodingAgent::DeepAgentsCode,
                installed: false,
            }]
        );
    }

    #[test]
    fn agent_options_reject_relative_path_entries() {
        assert_eq!(
            agent_options_in(Some(OsStr::new("relative/bin"))),
            vec![AgentOption {
                agent: CodingAgent::DeepAgentsCode,
                installed: false,
            }]
        );
    }

    #[cfg(unix)]
    #[test]
    fn native_windows_discovery_treats_dcode_command_shim_as_unavailable() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let shim = root.path().join("dcode.CMD");
        fs::write(&shim, "not executed").unwrap();
        fs::set_permissions(&shim, fs::Permissions::from_mode(0o755)).unwrap();

        let options = agent_options_in_for_platform(
            Some(root.path().as_os_str()),
            true,
            Some(OsStr::new(".CMD")),
        );

        assert_eq!(
            options,
            vec![AgentOption {
                agent: CodingAgent::DeepAgentsCode,
                installed: false,
            }]
        );
    }

    #[cfg(unix)]
    #[test]
    fn native_windows_discovery_finds_dcode_executable_after_command_shim() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        for name in ["dcode.CMD", "dcode.EXE"] {
            let executable = root.path().join(name);
            fs::write(&executable, "not executed").unwrap();
            fs::set_permissions(&executable, fs::Permissions::from_mode(0o755)).unwrap();
        }

        let options = agent_options_in_for_platform(
            Some(root.path().as_os_str()),
            true,
            Some(OsStr::new(".CMD;.EXE")),
        );

        assert_eq!(
            options,
            vec![AgentOption {
                agent: CodingAgent::DeepAgentsCode,
                installed: true,
            }]
        );
    }

    #[cfg(unix)]
    #[test]
    fn native_windows_discovery_keeps_other_agent_command_shims() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let shim = root.path().join("claude.CMD");
        fs::write(&shim, "not executed").unwrap();
        fs::set_permissions(&shim, fs::Permissions::from_mode(0o755)).unwrap();

        let options = agent_options_in_for_platform(
            Some(root.path().as_os_str()),
            true,
            Some(OsStr::new(".CMD")),
        );

        assert_eq!(
            options,
            vec![
                AgentOption {
                    agent: CodingAgent::DeepAgentsCode,
                    installed: false,
                },
                AgentOption {
                    agent: CodingAgent::Claude,
                    installed: true,
                },
            ]
        );
    }

    #[test]
    fn handoff_choices_put_raw_prompt_after_agents_and_before_exit() {
        let dcode = AgentOption {
            agent: CodingAgent::DeepAgentsCode,
            installed: true,
        };

        assert_eq!(
            handoff_choices([dcode]),
            vec![
                HandoffChoice::Agent(dcode),
                HandoffChoice::ViewRawPrompt,
                HandoffChoice::Exit,
            ]
        );
    }

    #[test]
    fn active_handoff_menu_closes_on_exit_and_leaves_a_blank_line() {
        let theme = HandoffTheme { complete: false };
        let state = ThemeState::Active;
        let output = format!(
            "{}{}",
            theme.format_select_item(&state, false, HANDOFF_EXIT_LABEL, ""),
            theme.format_footer_with_message(&state, "")
        );

        assert_eq!(console::strip_ansi_codes(&output), "└  ○ Exit \n\n");
    }

    #[test]
    fn submitted_exit_closes_the_handoff_and_leaves_a_blank_line() {
        let theme = HandoffTheme { complete: true };
        theme.format_select_item(&ThemeState::Active, true, HANDOFF_EXIT_LABEL, "");

        // cliclack computes the submitted footer before rendering the submitted item.
        let footer = theme.format_footer_with_message(&ThemeState::Submit, "");
        let item = theme.format_select_item(&ThemeState::Submit, true, HANDOFF_EXIT_LABEL, "");
        let output = format!("{item}{footer}");

        assert_eq!(console::strip_ansi_codes(&output), "└  Exit \n\n");
    }

    #[test]
    fn submitted_exit_keeps_an_incomplete_handoff_open() {
        let theme = HandoffTheme { complete: false };

        // cliclack computes the submitted footer before rendering the submitted item.
        let footer = theme.format_footer_with_message(&ThemeState::Submit, "");
        let item = theme.format_select_item(&ThemeState::Submit, true, HANDOFF_EXIT_LABEL, "");
        let output = format!("{item}{footer}");

        assert_eq!(console::strip_ansi_codes(&output), "│  Exit \n│\n");
    }

    #[test]
    fn raw_prompt_choice_uses_a_literal_label() {
        assert_eq!(HandoffChoice::ViewRawPrompt.label(), "View raw prompt");
    }

    #[test]
    fn interactive_handoff_uses_the_shared_heading() {
        assert_eq!(HANDOFF_HEADING, "Handoff to a coding agent");
    }

    #[test]
    fn raw_prompt_output_ends_with_one_newline() {
        let mut output = Vec::new();
        let prompt = "Use `code` literally.";

        write_raw_prompt(&mut output, prompt).unwrap();

        assert_eq!(output, b"Use `code` literally.\n");
    }

    #[test]
    fn raw_prompt_output_preserves_one_trailing_newline() {
        let mut output = Vec::new();

        write_raw_prompt(&mut output, "Use `code` literally.\n").unwrap();

        assert_eq!(output, b"Use `code` literally.\n");
    }

    #[test]
    fn named_install_restart_note_continues_in_the_scaffolded_project() {
        let prompt = "Build this project with the user.";
        let (title, body) = installed_needs_restart_note(Path::new("/tmp/my-agent"), prompt);

        assert_eq!(title, "Deep Agents Code installed");
        for expected in ["/tmp/my-agent", "`dcode`", prompt] {
            assert!(body.contains(expected), "missing {expected:?} from {body}");
        }
        assert!(!body.contains("mda init"), "{body}");
    }

    #[test]
    fn prompt_arguments_match_each_agents_interactive_mode() {
        let prompt = "Build this agent.\nKeep the prompt literal & safe.";

        assert_eq!(
            prompt_args(CodingAgent::DeepAgentsCode, prompt),
            ["-m", prompt]
        );
        assert_eq!(prompt_args(CodingAgent::Claude, prompt), [prompt]);
        assert_eq!(prompt_args(CodingAgent::Codex, prompt), [prompt]);
        assert_eq!(prompt_args(CodingAgent::Cursor, prompt), [prompt]);
        assert_eq!(prompt_args(CodingAgent::Droid, prompt), [prompt]);
        assert_eq!(prompt_args(CodingAgent::Gemini, prompt), ["-i", prompt]);
        assert_eq!(
            prompt_args(CodingAgent::OpenCode, prompt),
            ["--prompt", prompt]
        );
        assert_eq!(prompt_args(CodingAgent::Pi, prompt), [prompt]);
    }

    #[test]
    fn deep_agents_code_prompt_uses_message_argument_without_a_shell() {
        assert_eq!(
            prompt_args(CodingAgent::DeepAgentsCode, "Build it"),
            ["-m", "Build it"]
        );
    }

    #[cfg(unix)]
    #[test]
    fn deep_agents_code_unix_installer_uses_the_fixed_official_command() {
        assert_eq!(
            installer_action(false),
            InstallerAction::Run(CommandPlan {
                program: PathBuf::from("/bin/sh"),
                args: vec![
                    OsString::from("-c"),
                    OsString::from("curl -LsSf https://langch.in/dcode | bash"),
                ],
            })
        );
    }

    #[test]
    fn deep_agents_code_install_choices_put_install_before_go_back() {
        let items: Vec<_> = DEEP_AGENTS_CODE_INSTALL_CHOICES
            .into_iter()
            .map(|choice| (choice.label(), choice.hint()))
            .collect();

        assert_eq!(items, [("Install", "Esc to cancel"), ("Go back", "")]);
    }

    #[test]
    fn interrupted_install_choice_returns_to_the_agent_selector() {
        let interrupted = std::io::Error::from(std::io::ErrorKind::Interrupted);

        let choice = normalize_install_choice(Err(interrupted)).unwrap();

        assert_eq!(choice, None);
    }

    #[test]
    fn non_interrupted_install_choice_error_is_preserved() {
        let other = std::io::Error::other("terminal unavailable");

        let error = normalize_install_choice(Err(other)).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::Other);
    }

    #[test]
    fn native_windows_install_requires_wsl_without_a_shell_plan() {
        assert_eq!(
            installer_action(true),
            InstallerAction::RequiresWsl {
                guidance: "Deep Agents Code requires WSL on Windows. Run the install command above inside WSL.",
            }
        );
    }

    #[test]
    fn install_flow_interruption_returns_to_selector_without_side_effects() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);

        let outcome = run_install_flow(
            None,
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                None
            },
            |_: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            (
                outcome,
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
            ),
            (InstallFlowOutcome::ReturnToSelector, 0, 0, 0)
        );
    }

    #[test]
    fn install_flow_go_back_returns_to_selector_without_side_effects() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);

        let outcome = run_install_flow(
            Some(InstallChoice::GoBack),
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                None
            },
            |_: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            (
                outcome,
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
            ),
            (InstallFlowOutcome::ReturnToSelector, 0, 0, 0)
        );
    }

    #[test]
    fn install_flow_confirmation_calls_runner_once() {
        let runner_calls = Cell::new(0);

        run_install_flow(
            Some(InstallChoice::Install),
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || None,
            |_: &Path| -> Result<()> { Ok(()) },
        )
        .unwrap();

        assert_eq!(runner_calls.get(), 1);
    }

    #[test]
    fn install_flow_runner_error_skips_resolve_and_launch() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);

        let error = run_install_flow(
            Some(InstallChoice::Install),
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Err(anyhow::anyhow!("installer failed"))
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                None
            },
            |_: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                Ok(())
            },
        )
        .unwrap_err();

        assert_eq!(
            (
                error.to_string(),
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
            ),
            ("installer failed".to_string(), 1, 0, 0)
        );
    }

    #[test]
    fn install_flow_success_with_resolved_dcode_launches_once() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);
        let launched_path = RefCell::new(None);
        let dcode = PathBuf::from("/resolved/dcode");

        let outcome = run_install_flow(
            Some(InstallChoice::Install),
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                Some(dcode.clone())
            },
            |path: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                launched_path.replace(Some(path.to_path_buf()));
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            (
                outcome,
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
                launched_path.into_inner(),
            ),
            (
                InstallFlowOutcome::Started,
                1,
                1,
                1,
                Some(PathBuf::from("/resolved/dcode")),
            )
        );
    }

    #[test]
    fn install_flow_success_without_resolved_dcode_needs_restart() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);

        let outcome = run_install_flow(
            Some(InstallChoice::Install),
            inert_installer_action(),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                None
            },
            |_: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            (
                outcome,
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
            ),
            (InstallFlowOutcome::InstalledNeedsRestart, 1, 1, 0)
        );
    }

    #[test]
    fn install_flow_windows_guidance_skips_runner_and_shell() {
        let runner_calls = Cell::new(0);
        let resolver_calls = Cell::new(0);
        let launcher_calls = Cell::new(0);

        let outcome = run_install_flow(
            Some(InstallChoice::Install),
            installer_action(true),
            |_: &CommandPlan| -> Result<()> {
                runner_calls.set(runner_calls.get() + 1);
                Ok(())
            },
            || {
                resolver_calls.set(resolver_calls.get() + 1);
                None
            },
            |_: &Path| -> Result<()> {
                launcher_calls.set(launcher_calls.get() + 1);
                Ok(())
            },
        )
        .unwrap();

        assert_eq!(
            (
                outcome,
                runner_calls.get(),
                resolver_calls.get(),
                launcher_calls.get(),
            ),
            (
                InstallFlowOutcome::RequiresWsl {
                    guidance: "Deep Agents Code requires WSL on Windows. Run the install command above inside WSL.",
                },
                0,
                0,
                0,
            )
        );
    }

    #[cfg(unix)]
    #[test]
    fn run_installer_reports_nonzero_exit_without_a_shell() {
        let plan = CommandPlan {
            program: PathBuf::from("/usr/bin/false"),
            args: Vec::new(),
        };

        let error = run_installer(&plan).unwrap_err();

        assert!(error
            .to_string()
            .starts_with("Deep Agents Code installer exited unsuccessfully"));
    }

    #[cfg(unix)]
    #[test]
    fn coding_agent_starts_inside_the_project_directory() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let project = root.path().join("my-agent");
        fs::create_dir(&project).unwrap();
        let executable = root.path().join("record-working-directory");
        fs::write(&executable, "#!/bin/sh\npwd > \"$1\"\n").unwrap();
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o755)).unwrap();
        let recorded = root.path().join("working-directory");

        spawn_coding_agent_at(
            CodingAgent::Codex,
            &executable,
            &project,
            recorded.to_str().unwrap(),
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(recorded).unwrap().trim(),
            fs::canonicalize(project).unwrap().display().to_string()
        );
    }

    #[cfg(unix)]
    #[test]
    fn coding_agent_error_without_an_interrupt_stays_a_failure() {
        let child = Command::new("false")
            .spawn()
            .expect("failed to spawn the test agent");

        let result = wait_for_coding_agent(
            child,
            CodingAgent::Codex,
            Path::new("."),
            &Interrupt::pending(),
        );

        assert!(result.is_err());
    }

    /// An agent that exits nonzero after the interrupt exited because of it, so
    /// quitting the agent with Ctrl-C is not reported as an agent failure.
    #[cfg(unix)]
    #[test]
    fn coding_agent_error_after_an_interrupt_succeeds() {
        let child = Command::new("sh")
            .args(["-c", "sleep 0.1; exit 1"])
            .spawn()
            .expect("failed to spawn the test agent");

        wait_for_coding_agent(
            child,
            CodingAgent::Codex,
            Path::new("."),
            &Interrupt::requested(),
        )
        .unwrap();
    }

    #[test]
    fn direct_spawn_plan_passes_the_prompt_without_a_shell() {
        let prompt = "line one\nline two (literal) & safe";
        let executable = Path::new("/usr/local/bin/codex");

        let plan = spawn_plan(CodingAgent::Codex, executable, prompt, false).unwrap();

        assert_eq!(
            plan,
            SpawnPlan {
                command: CommandPlan {
                    program: executable.to_path_buf(),
                    args: vec![OsString::from(prompt)],
                },
                prompt_seeded: true,
            }
        );
    }

    #[test]
    fn deep_agents_code_windows_command_shim_requires_wsl() {
        let executable = Path::new(r"C:\Tools\dcode.cmd");

        let error = spawn_plan(
            CodingAgent::DeepAgentsCode,
            executable,
            "literal prompt & safe",
            true,
        )
        .unwrap_err();

        assert_eq!(
            error.to_string(),
            "Deep Agents Code requires WSL on Windows"
        );
    }

    #[test]
    fn windows_command_shim_never_receives_the_prompt() {
        let prompt = "line one\nline two (literal) & safe";
        let executable = Path::new(r"C:\Tools\claude.CMD");

        let plan = spawn_plan(CodingAgent::Claude, executable, prompt, true).unwrap();

        assert!(!plan.command.args.iter().any(|arg| arg == prompt));
        assert!(!plan.prompt_seeded);
        assert_eq!(plan.command.program, PathBuf::from("cmd.exe"));
    }
}
