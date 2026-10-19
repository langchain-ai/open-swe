use crate::deploy::agent_target;
use crate::langsmith::{AgentEnvironment, DeploymentAgent};
use clap::parser::ValueSource;
use clap::{ArgMatches, Command};
use serde::Serialize;
use std::path::PathBuf;

/// The command and flag names that are safe to report.
#[derive(Serialize)]
pub struct CommandInvocation {
    pub(super) command_path: String,
    pub(super) flags_present: Vec<String>,
}

impl CommandInvocation {
    /// Read the command and flag names from Clap's parsed arguments.
    ///
    /// Returns `None` when there is no command to run.
    pub fn from_matches(command: &Command, matches: &ArgMatches) -> Option<Self> {
        let mut command_names = Vec::new();
        let mut flag_names = Vec::new();
        collect_command_path_and_flags(command, matches, &mut command_names, &mut flag_names);
        if command_names.is_empty() {
            return None;
        }

        flag_names.sort_unstable();
        flag_names.dedup();
        Some(Self {
            command_path: command_names.join(" "),
            flags_present: flag_names,
        })
    }

    /// The complete command below `mda`, such as `channel init slack`.
    pub fn command_path(&self) -> &str {
        &self.command_path
    }

    /// The flag names written by the caller, without their values.
    pub fn flags_present(&self) -> &[String] {
        &self.flags_present
    }
}

fn collect_command_path_and_flags(
    command: &Command,
    matches: &ArgMatches,
    command_names: &mut Vec<String>,
    flag_names: &mut Vec<String>,
) {
    for id in matches.ids() {
        if matches.value_source(id.as_str()) != Some(ValueSource::CommandLine) {
            continue;
        }
        let Some(argument) = command
            .get_arguments()
            .find(|argument| argument.get_id().as_str() == id.as_str())
        else {
            continue;
        };
        if argument.is_positional() {
            continue;
        }
        flag_names.push(
            argument
                .get_long()
                .unwrap_or_else(|| argument.get_id().as_str())
                .to_string(),
        );
    }

    if let Some((name, child_matches)) = matches.subcommand() {
        let child_command = command
            .find_subcommand(name)
            .expect("Clap returned a command outside its command definition");
        command_names.push(child_command.get_name().to_string());
        collect_command_path_and_flags(child_command, child_matches, command_names, flag_names);
    }
}

/// Immutable inputs held by the dispatcher, never passed to a command handler.
#[derive(Default)]
pub struct CommandContext {
    /// Project .env file, or .env.local for dev/chat when .env is absent.
    pub(super) env_path: PathBuf,
    pub(super) host_url: Option<String>,
    pub(super) workspace_id: Option<String>,
    /// Command target name, retained even after deletion.
    pub(super) deployment_name: Option<String>,
    /// Agent/environment selector for deployments whose names are server-owned.
    pub(super) deployment_agent: Option<DeploymentAgent>,
    pub(super) deployment_id: Option<String>,
}

impl CommandContext {
    pub fn capture(matches: &ArgMatches) -> Self {
        let mut project_dir = PathBuf::from(".");
        let mut context = Self::default();
        let mut args = matches;
        loop {
            project_dir = arg(args, "path").unwrap_or(project_dir);
            context.host_url = arg(args, "host_url").or(context.host_url);
            context.workspace_id = arg(args, "workspace_id").or(context.workspace_id);
            let Some((_, child)) = args.subcommand() else {
                break;
            };
            args = child;
        }
        if matches
            .subcommand_name()
            .is_some_and(|name| matches!(name, "deploy" | "delete" | "logs"))
        {
            if let Ok((name, agent)) = (|| -> anyhow::Result<_> {
                let name = match arg::<String>(args, "name") {
                    Some(name) => crate::naming::normalize_name(&name)?,
                    None => crate::agent_config::agent_assistant_id(&crate::project::load(
                        &project_dir,
                    )?)?,
                };
                agent_target(name, arg::<AgentEnvironment>(args, "agent_environment"))
            })() {
                context.deployment_name = Some(name);
                context.deployment_agent = agent;
            }
        }
        context.deployment_id = arg(args, "deployment_id");
        let local_env = matches
            .subcommand_name()
            .is_some_and(|name| matches!(name, "dev" | "chat"));
        context.env_path = project_dir.join(if local_env && !project_dir.join(".env").is_file() {
            ".env.local"
        } else {
            ".env"
        });
        context
    }
}

fn arg<T: Clone + Send + Sync + 'static>(args: &ArgMatches, name: &str) -> Option<T> {
    args.try_get_one::<T>(name).ok().flatten().cloned()
}

#[cfg(test)]
mod tests {
    use clap::{Arg, ArgAction};
    use serde_json::json;

    use super::*;

    #[test]
    fn four_command_levels_are_joined_with_spaces() {
        let command = Command::new("mda").subcommand(Command::new("one").subcommand(
            Command::new("two").subcommand(Command::new("three").subcommand(Command::new("four"))),
        ));
        let matches = command
            .clone()
            .try_get_matches_from(["mda", "one", "two", "three", "four"])
            .unwrap();

        let analytics = CommandInvocation::from_matches(&command, &matches).unwrap();

        assert_eq!(analytics.command_path(), "one two three four");
    }

    #[test]
    fn only_written_flag_names_are_reported() {
        let command = Command::new("mda").subcommand(
            Command::new("build")
                .arg(Arg::new("path").index(1).default_value("."))
                .arg(Arg::new("out").short('o').long("out").num_args(1))
                .arg(
                    Arg::new("mode")
                        .long("mode")
                        .default_value("safe")
                        .num_args(1),
                ),
        );
        let matches = command
            .clone()
            .try_get_matches_from(["mda", "build", "/private/project", "-o", "/private/output"])
            .unwrap();

        let analytics = CommandInvocation::from_matches(&command, &matches).unwrap();

        assert_eq!(
            serde_json::to_value(analytics).unwrap(),
            json!({
                "command_path": "build",
                "flags_present": ["out"]
            })
        );
    }

    #[test]
    fn repeating_model_twice_reports_model_once() {
        let command = Command::new("mda").subcommand(
            Command::new("eval").arg(
                Arg::new("model")
                    .long("model")
                    .action(ArgAction::Append)
                    .num_args(1),
            ),
        );
        let matches = command
            .clone()
            .try_get_matches_from(["mda", "eval", "--model", "one", "--model", "two"])
            .unwrap();

        let analytics = CommandInvocation::from_matches(&command, &matches).unwrap();

        assert_eq!(
            serde_json::to_value(analytics.flags_present()).unwrap(),
            json!(["model"])
        );
    }
    #[test]
    fn delete_name_is_captured_before_project_removal() {
        for explicit in [false, true] {
            let root = tempfile::tempdir().unwrap();
            crate::test_support::write_file(
                root.path(),
                "agent.ts",
                "export const agent = defineDeepAgent({ name: 'authored' });",
            );
            let command = Command::new("mda").subcommand(
                Command::new("delete")
                    .arg(Arg::new("path").value_parser(clap::value_parser!(PathBuf)))
                    .arg(Arg::new("name").long("name"))
                    .arg(
                        Arg::new("agent_environment")
                            .long("environment")
                            .value_parser(clap::value_parser!(AgentEnvironment)),
                    ),
            );
            let mut args = vec![
                "mda",
                "delete",
                root.path().to_str().unwrap(),
                "--environment",
                "staging",
            ];
            if explicit {
                args.extend(["--name", "My Agent"]);
            }
            let context = CommandContext::capture(&command.try_get_matches_from(args).unwrap());
            std::fs::remove_file(root.path().join("agent.ts")).unwrap();
            assert_eq!(
                context.deployment_name.as_deref(),
                Some(if explicit {
                    "my-agent-staging"
                } else {
                    "authored-staging"
                })
            );
        }
    }
}
