//! `mda channels setup`: the channel setup `mda deploy` runs, on its own, so
//! it holds no surprises for the next deploy. Setup needs the Agent Server
//! URL of a deployed agent, so the agent has to be deployed once first.
//! Without a terminal, or with `--json`, it only reports where each channel
//! stands: finishing setup may need a browser that a sandboxed agent does not
//! have. It exits non-zero unless every channel is set up, so it doubles as a
//! precheck.

use std::collections::BTreeMap;
use std::io::{self, Write};
use std::path::PathBuf;

use anyhow::{Context, Result};
use cliclack::{log, outro, outro_cancel, spinner};
use serde::Serialize;
use serde_json::Value;

use super::ChannelKind;
use crate::agent_config;
use crate::cli_output::show_wordmark_intro;
use crate::command::CommandCompletion;
use crate::compile;
use crate::deploy::channel_app::is_interactive;
use crate::deploy::channel_setup::{is_settled, ChannelSetup, ChannelStatus, SetupTarget};
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{
    agent_environment, agent_target, ensure_project, env_file, find_target_deployment,
    live_deployment, require_user_key, resolve_api_key, step, UserKeyCheckpoint,
};
use crate::error::{categorized, is_cancellation, ErrorCategory, Failure};
use crate::install::Installation;
use crate::langsmith::{self, AgentEnvironment, DeploymentAgent};
use crate::naming::normalize_name;
use crate::project;

/// User-facing options for `mda channels setup`.
pub struct SetupOptions {
    /// Path to the Managed Deep Agent project.
    pub path: PathBuf,
    /// The one channel to set up; every channel of the project when `None`.
    pub kind: Option<ChannelKind>,
    /// Deployment name override (defaults to the authored agent `name`).
    pub name: Option<String>,
    pub agent_environment: Option<AgentEnvironment>,
    /// Workspace ID override (takes precedence over `LANGSMITH_WORKSPACE_ID`).
    pub workspace_id: Option<String>,
    /// Host backend URL override.
    pub host_url: Option<String>,
    /// Env file to read instead of the project's `.env`.
    pub env_file: Option<PathBuf>,
    /// Print the status report as JSON on stdout and set nothing up.
    pub json: bool,
}

/// Each channel's status, in setup order.
type Statuses = Vec<(ChannelKind, Result<ChannelStatus>)>;

/// Run the setup command end-to-end. Like [`crate::deploy::run`], failures
/// before `intro` print plainly and later ones close the session. With
/// `--json` nothing but the report is printed, and failures are left to the
/// caller, which prints them on stderr.
pub fn run(opts: SetupOptions) -> Result<CommandCompletion> {
    ensure_project(&opts.path)?;
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    if opts.json {
        return run_session(&opts, &project, &installation);
    }

    show_wordmark_intro()?;
    log::info(format!("Project: {}", project.root.display()))?;

    match run_session(&opts, &project, &installation) {
        Ok(status) => Ok(status),
        Err(err) if is_cancellation(&err) => {
            outro_cancel("Cancelled")?;
            Ok(CommandCompletion::Cancelled)
        }
        Err(err) => {
            let failure = Failure::of(&err);
            outro_cancel(format!("{err:#}"))?;
            Ok(CommandCompletion::Failed(failure))
        }
    }
}

/// The deployment to set channels up on, resolved as `deploy` resolves it:
/// `--name`, else the assistant ID, in the `--agent-environment` or
/// `LANGSMITH_AGENT_ENVIRONMENT` agent environment.
fn setup_target(
    opts: &SetupOptions,
    assistant_id: &str,
    env_vars: &BTreeMap<String, String>,
) -> Result<(String, Option<DeploymentAgent>)> {
    let base_name = match &opts.name {
        Some(name) => normalize_name(name)?,
        None => assistant_id.to_string(),
    };
    agent_target(
        base_name,
        agent_environment(opts.agent_environment, env_vars)?,
    )
}

fn run_session(
    opts: &SetupOptions,
    project: &project::Project,
    installation: &Installation,
) -> Result<CommandCompletion> {
    let json = opts.json;
    let env_path = env_file::project_env_path(&project.root, opts.env_file.as_deref());
    let env_vars = env_file::parse_env_file(&env_path);
    let mut key_source = None;
    let ctx = LangSmithContext::resolve_verified(
        &env_vars,
        opts.host_url.as_deref(),
        opts.workspace_id.as_deref(),
        WORKSPACE_FLAG_HINT,
        |platform_api_url| {
            let key = resolve_api_key(&env_vars, &env_path, platform_api_url)?;
            key_source = Some(key.source.clone());
            Ok(key)
        },
    )?;
    // Channel ingress invokes runs by the agent's assistant ID, never a
    // `--name` override, as in deploy.
    let assistant_id = agent_config::agent_assistant_id(project)?;
    let (name, deployment_agent) = setup_target(opts, &assistant_id, &env_vars)?;
    let client = ctx.client()?;

    let mut setup = project_channels(project, installation, &assistant_id, json)?;
    let available = setup.kinds();
    if let Some(kind) = opts.kind {
        if !available.contains(&kind) {
            return Err(categorized(
                ErrorCategory::Project,
                format!(
                    "This project has no {} channel. Add one with `mda channels init {}`.",
                    kind.label(),
                    kind.command()
                ),
            ));
        }
        setup.retain(&[kind]);
    } else if available.is_empty() {
        if json {
            print_report(&StatusReport::new(&name, None, &[], None))?;
        } else {
            outro("This project has no channels to set up. Add one with `mda channels init`.")?;
        }
        return Ok(CommandCompletion::Succeeded);
    }
    if !json {
        super::warn_experimental(&setup.kinds())?;
    }

    // Trigger Server refuses a key with no user, for the status lookup below
    // as much as for setup, with a bare 401.
    if let (Some(channel), Some(source)) = (setup.trigger_label(), &key_source) {
        require_user_key(&client, source, channel, UserKeyCheckpoint::ChannelsSetup)?;
    }

    let (deployment_id, agent_server_url) =
        find_deployment(&client, &name, deployment_agent.as_ref(), json)?;

    let statuses = with_spinner(
        json,
        "Checking channel setup",
        "Could not check channel setup",
        || Ok(setup.statuses(&client, &deployment_id, &env_vars)),
        |_| "Checked channel setup".to_string(),
    )?;

    if json || !is_interactive() {
        // What would stop the next deploy, said only when something is left.
        let blocker = if all_settled(&statuses) {
            None
        } else {
            deploy_blocker(
                &setup,
                &client,
                &deployment_id,
                &agent_server_url,
                &env_vars,
            )
        };
        if json {
            print_report(&StatusReport::new(
                &name,
                Some(&deployment_id),
                &statuses,
                blocker,
            ))?;
            return Ok(completion(&statuses));
        }
        show_statuses(&statuses)?;
        if let Some(blocker) = blocker {
            log::warning(format!(
                "`mda deploy` without a terminal would stop before deploying: {blocker}"
            ))?;
        }
        return finish(
            &statuses,
            "Run `mda channels setup` in a terminal to finish setting the channels up.",
        );
    }

    show_statuses(&statuses)?;
    let selected = match opts.kind {
        Some(kind) => vec![kind],
        None => choose_channels(&statuses)?,
    };
    if selected.is_empty() {
        return finish(&statuses, "No channels selected.");
    }
    setup.retain(&selected);
    setup.preflight(
        &client,
        Some(&deployment_id),
        Some(&agent_server_url),
        &env_vars,
        true,
    )?;
    setup.set_up(&SetupTarget {
        client: &client,
        agent_server_url: &agent_server_url,
        deployment_id: &deployment_id,
        assistant_id: &assistant_id,
        project_root: &project.root,
        env_vars: &env_vars,
    })?;

    // Setup may have been skipped, so check again what it left.
    let after = with_spinner(
        false,
        "Checking channel setup",
        "Could not check channel setup",
        || Ok(setup.statuses(&client, &deployment_id, &env_vars)),
        |_| "Checked channel setup".to_string(),
    )?;
    for (kind, status) in after.iter().filter(|(_, status)| !is_settled(status)) {
        show_status(*kind, status)?;
    }
    finish(
        &merge_statuses(statuses, after),
        "Run `mda channels setup` again to finish setting the channels up.",
    )
}

/// Compile the project and read its channels, as deploy does.
fn project_channels(
    project: &project::Project,
    installation: &Installation,
    assistant_id: &str,
    json: bool,
) -> Result<ChannelSetup> {
    let out_dir = project.root.join(".mda").join("build");
    let requirements = with_spinner(
        json,
        "Reading the project's channels",
        "Could not read the project's channels",
        || {
            let summary = compile::compile(project, &out_dir, installation.runtime_source())
                .map_err(compile::compilation)?;
            compile::emit_channel_requirements_for_entries(project, &out_dir, &summary.channels)
        },
        |_| "Read the project's channels".to_string(),
    )?;
    ChannelSetup::prepare(
        &requirements,
        &project.root,
        &project.files,
        project.language,
        assistant_id,
    )
}

/// The deployment's ID and Agent Server URL. Fails when the agent was never
/// deployed: setup points each provider at that URL.
fn find_deployment(
    client: &langsmith::Client,
    name: &str,
    agent: Option<&langsmith::DeploymentAgent>,
    json: bool,
) -> Result<(String, String)> {
    let found = with_spinner(
        json,
        &format!("Looking up deployment '{name}'"),
        "Could not reach LangSmith",
        || {
            let Some(deployment) = find_target_deployment(client, name, agent)? else {
                return Ok(None);
            };
            let live = live_deployment(client, &deployment)?;
            Ok(Some((deployment, live)))
        },
        |found| match found {
            Some((deployment, _)) => format!(
                "Found deployment {}",
                deployment.get("id").and_then(Value::as_str).unwrap_or(name)
            ),
            None => format!("No deployment named '{name}'"),
        },
    )?;
    match found {
        Some((_, Some(live))) => Ok(live),
        Some((_, None)) => Err(categorized(
            ErrorCategory::Deployment,
            format!(
                "Channel setup needs an initial deployment of the agent, and deployment '{name}' has not finished its first build. Run `mda channels setup` again once it is live."
            ),
        )),
        None => Err(categorized(
            ErrorCategory::Deployment,
            format!(
                "Channel setup needs an initial deployment of the agent, and there is no deployment named '{name}' yet. Run `mda deploy` first; it sets the channels up once the deployment is live."
            ),
        )),
    }
}

/// Run `work` behind a spinner in a terminal; with `--json`, whose stdout is
/// the report, silently.
fn with_spinner<T>(
    json: bool,
    start: &str,
    failed: &str,
    work: impl FnOnce() -> Result<T>,
    done: impl FnOnce(&T) -> String,
) -> Result<T> {
    if json {
        return work();
    }
    let sp = spinner();
    sp.start(start);
    let value = step(&sp, failed, work())?;
    sp.stop(done(&value));
    Ok(value)
}

fn show_statuses(statuses: &Statuses) -> Result<()> {
    for (kind, status) in statuses {
        show_status(*kind, status)?;
    }
    Ok(())
}

fn show_status(kind: ChannelKind, status: &Result<ChannelStatus>) -> Result<()> {
    let label = kind.label();
    match status {
        Ok(ChannelStatus::Ready) => log::success(format!("{label} · set up")),
        Ok(ChannelStatus::NoApp) => {
            log::info(format!("{label} · handles no events, so it needs no app"))
        }
        Ok(ChannelStatus::NeedsSetup(reason)) => {
            log::warning(format!("{label} · not set up\n{reason}"))
        }
        Err(error) => log::error(format!("{label} · could not check: {error:#}")),
    }?;
    Ok(())
}

fn all_settled(statuses: &[(ChannelKind, Result<ChannelStatus>)]) -> bool {
    statuses.iter().all(|(_, status)| is_settled(status))
}

/// Succeeded only when every channel is set up, so the command works as a
/// precheck.
fn completion(statuses: &Statuses) -> CommandCompletion {
    if all_settled(statuses) {
        CommandCompletion::Succeeded
    } else {
        CommandCompletion::Failed(ErrorCategory::Deployment.into())
    }
}

/// Close the session, saying `next` when a channel is left to set up.
fn finish(statuses: &Statuses, next: &str) -> Result<CommandCompletion> {
    let completion = completion(statuses);
    if completion == CommandCompletion::Succeeded {
        outro("Every channel is set up.")?;
    } else {
        outro_cancel(next)?;
    }
    Ok(completion)
}

/// `statuses`, with the channels checked again after setup taken from `after`.
fn merge_statuses(mut statuses: Statuses, after: Statuses) -> Statuses {
    for (kind, status) in after {
        if let Some(slot) = statuses.iter_mut().find(|(checked, _)| *checked == kind) {
            slot.1 = status;
        }
    }
    statuses
}

/// What would stop `mda deploy` without a terminal, as in CI, before it
/// uploads anything: deploy checks the same, but only once it has started.
/// Checked as without a terminal even in one, so a check on a laptop says
/// what a deploy in CI would hit.
fn deploy_blocker(
    setup: &ChannelSetup,
    client: &langsmith::Client,
    deployment_id: &str,
    agent_server_url: &str,
    env_vars: &BTreeMap<String, String>,
) -> Option<String> {
    setup
        .preflight(
            client,
            Some(deployment_id),
            Some(agent_server_url),
            env_vars,
            false,
        )
        .err()
        .map(|error| format!("{error:#}"))
}

/// Ask which channels to set up, offering the unsettled ones first. With one
/// channel there is nothing to choose.
fn choose_channels(statuses: &Statuses) -> Result<Vec<ChannelKind>> {
    if let [(kind, _)] = statuses.as_slice() {
        return Ok(vec![*kind]);
    }
    let mut prompt = cliclack::multiselect("Which channels do you want to set up?").required(false);
    for (kind, status) in statuses {
        let hint = if is_settled(status) {
            "set up"
        } else {
            "not set up"
        };
        prompt = prompt.item(*kind, kind.label(), hint);
    }
    prompt = prompt.initial_values(initial_selection(statuses));
    prompt
        .interact()
        .context("failed to choose the channels to set up")
}

/// The channels the prompt starts with: the unsettled ones, or all of them
/// when every channel is set up.
fn initial_selection(statuses: &Statuses) -> Vec<ChannelKind> {
    let unsettled: Vec<ChannelKind> = statuses
        .iter()
        .filter(|(_, status)| !is_settled(status))
        .map(|(kind, _)| *kind)
        .collect();
    if unsettled.is_empty() {
        statuses.iter().map(|(kind, _)| *kind).collect()
    } else {
        unsettled
    }
}

/// What `mda channels setup --json` prints.
#[derive(Debug, Serialize, PartialEq, Eq)]
struct StatusReport {
    deployment: DeploymentReport,
    /// Whether every channel is set up; the command exits non-zero otherwise.
    ready: bool,
    channels: Vec<ChannelReport>,
    /// What would stop `mda deploy` without a terminal before it uploads
    /// anything, when a channel is not set up.
    deploy_blocker: Option<String>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
struct DeploymentReport {
    name: String,
    /// Absent when the project has no channels, so no deployment was looked up.
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<String>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
struct ChannelReport {
    /// `slack`, `github` or `linear`, as `mda channels setup <channel>` takes it.
    channel: &'static str,
    /// `ready`, `no_app` (it handles no events), `needs_setup`, or `error`
    /// when its setup could not be checked.
    status: &'static str,
    /// Whether the channel may still change in a minor release.
    experimental: bool,
    /// What is left to do, or why the check failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

impl StatusReport {
    fn new(
        name: &str,
        deployment_id: Option<&str>,
        statuses: &[(ChannelKind, Result<ChannelStatus>)],
        deploy_blocker: Option<String>,
    ) -> Self {
        let channels = statuses
            .iter()
            .map(|(kind, status)| {
                let (status, message) = match status {
                    Ok(ChannelStatus::Ready) => ("ready", None),
                    Ok(ChannelStatus::NoApp) => ("no_app", None),
                    Ok(ChannelStatus::NeedsSetup(reason)) => ("needs_setup", Some(reason.clone())),
                    Err(error) => ("error", Some(format!("{error:#}"))),
                };
                ChannelReport {
                    channel: kind.command(),
                    status,
                    experimental: kind.is_experimental(),
                    message,
                }
            })
            .collect();
        Self {
            deployment: DeploymentReport {
                name: name.to_string(),
                id: deployment_id.map(str::to_string),
            },
            ready: all_settled(statuses),
            channels,
            deploy_blocker,
        }
    }
}

fn print_report(report: &StatusReport) -> Result<()> {
    let mut stdout = io::stdout().lock();
    let written = serde_json::to_writer_pretty(&mut stdout, report)
        .map_err(io::Error::from)
        .and_then(|()| writeln!(stdout))
        .and_then(|()| stdout.flush());
    match written {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::BrokenPipe => Ok(()),
        Err(err) => Err(err).context("failed to write the channel status to stdout"),
    }
}

#[cfg(test)]
mod tests {
    use anyhow::anyhow;
    use serde_json::json;

    use super::*;

    fn setup_options(agent_environment: Option<AgentEnvironment>) -> SetupOptions {
        SetupOptions {
            path: PathBuf::from("."),
            kind: None,
            name: None,
            agent_environment,
            workspace_id: None,
            host_url: None,
            env_file: None,
            json: false,
        }
    }

    #[test]
    fn the_target_is_the_agent_environment_from_the_env_file_unless_the_flag_names_one() {
        let env = BTreeMap::from([(
            "LANGSMITH_AGENT_ENVIRONMENT".to_string(),
            "staging".to_string(),
        )]);

        let (_, agent) = setup_target(&setup_options(None), "support", &env).unwrap();
        let agent = agent.expect("an agent deployment");
        assert_eq!(agent.agent_id, "support");
        assert_eq!(agent.environment, AgentEnvironment::Staging);

        let flag = setup_options(Some(AgentEnvironment::Production));
        let (_, agent) = setup_target(&flag, "support", &env).unwrap();
        assert_eq!(agent.unwrap().environment, AgentEnvironment::Production);

        let (name, agent) =
            setup_target(&setup_options(None), "support", &BTreeMap::new()).unwrap();
        assert_eq!(name, "support");
        assert!(agent.is_none());
    }

    #[test]
    fn an_unknown_agent_environment_in_the_env_file_is_rejected() {
        let env = BTreeMap::from([(
            "LANGSMITH_AGENT_ENVIRONMENT".to_string(),
            "local".to_string(),
        )]);

        assert!(setup_target(&setup_options(None), "support", &env).is_err());
    }

    #[test]
    fn the_prompt_starts_with_the_unsettled_channels() {
        let statuses = vec![
            (ChannelKind::Slack, Ok(ChannelStatus::Ready)),
            (
                ChannelKind::GitHub,
                Ok(ChannelStatus::NeedsSetup("not installed".to_string())),
            ),
            (ChannelKind::Linear, Err(anyhow!("Agent Auth is down"))),
        ];

        assert_eq!(
            initial_selection(&statuses),
            vec![ChannelKind::GitHub, ChannelKind::Linear]
        );
    }

    #[test]
    fn the_prompt_starts_with_every_channel_once_all_are_set_up() {
        let statuses = vec![
            (ChannelKind::Slack, Ok(ChannelStatus::Ready)),
            (ChannelKind::GitHub, Ok(ChannelStatus::NoApp)),
        ];

        assert_eq!(
            initial_selection(&statuses),
            vec![ChannelKind::Slack, ChannelKind::GitHub]
        );
    }

    #[test]
    fn the_command_fails_unless_every_channel_is_set_up() {
        let settled = vec![
            (ChannelKind::Slack, Ok(ChannelStatus::Ready)),
            (ChannelKind::GitHub, Ok(ChannelStatus::NoApp)),
        ];
        let unsettled = vec![
            (ChannelKind::Slack, Ok(ChannelStatus::Ready)),
            (ChannelKind::Linear, Err(anyhow!("Agent Auth is down"))),
        ];

        assert_eq!(completion(&settled), CommandCompletion::Succeeded);
        assert_eq!(
            completion(&unsettled),
            CommandCompletion::Failed(ErrorCategory::Deployment.into())
        );
    }

    #[test]
    fn a_recheck_replaces_only_the_channels_set_up() {
        let before = vec![
            (
                ChannelKind::Slack,
                Ok(ChannelStatus::NeedsSetup("no app".to_string())),
            ),
            (
                ChannelKind::GitHub,
                Ok(ChannelStatus::NeedsSetup("not installed".to_string())),
            ),
        ];
        let after = vec![(ChannelKind::GitHub, Ok(ChannelStatus::Ready))];

        let merged = merge_statuses(before, after);

        assert_eq!(
            merged
                .iter()
                .map(|(kind, status)| (*kind, is_settled(status)))
                .collect::<Vec<_>>(),
            vec![(ChannelKind::Slack, false), (ChannelKind::GitHub, true)]
        );
    }

    #[test]
    fn the_json_report_names_each_channel_and_what_is_left() {
        let statuses = vec![
            (ChannelKind::Slack, Ok(ChannelStatus::Ready)),
            (
                ChannelKind::GitHub,
                Ok(ChannelStatus::NeedsSetup(
                    "The GitHub App is not installed yet.".into(),
                )),
            ),
            (ChannelKind::Linear, Ok(ChannelStatus::NoApp)),
        ];

        let report = StatusReport::new(
            "triage",
            Some("dep-1"),
            &statuses,
            Some("set MDA_GITHUB_APP_OWNER".into()),
        );

        assert_eq!(
            serde_json::to_value(&report).unwrap(),
            json!({
                "deployment": {"name": "triage", "id": "dep-1"},
                "ready": false,
                "channels": [
                    {"channel": "slack", "status": "ready", "experimental": false},
                    {
                        "channel": "github",
                        "status": "needs_setup",
                        "experimental": true,
                        "message": "The GitHub App is not installed yet."
                    },
                    {"channel": "linear", "status": "no_app", "experimental": true}
                ],
                "deploy_blocker": "set MDA_GITHUB_APP_OWNER"
            })
        );
    }

    #[test]
    fn the_json_report_of_a_project_without_channels_is_ready() {
        let report = StatusReport::new("triage", None, &[], None);

        assert_eq!(
            serde_json::to_value(&report).unwrap(),
            json!({
                "deployment": {"name": "triage"},
                "ready": true,
                "channels": [],
                "deploy_blocker": null
            })
        );
    }
}
