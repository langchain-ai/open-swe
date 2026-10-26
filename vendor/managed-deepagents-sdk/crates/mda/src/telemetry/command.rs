use clap::parser::ValueSource;
use clap::{ArgMatches, Command};
use serde::Serialize;

/// The command and flag names that are safe to report.
#[derive(Clone, Default, Serialize)]
pub struct CommandInvocation {
    pub(crate) command_path: String,
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

        assert_eq!(analytics.command_path, "one two three four");
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
            serde_json::to_value(&analytics.flags_present).unwrap(),
            json!(["model"])
        );
    }
}
