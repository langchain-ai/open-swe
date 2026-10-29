//! Stages of a command, recorded as one `stage completed` record each.
//!
//! A command calls [`enter`] at each stage boundary. That records the stage it
//! leaves and makes the new one current in the facts, so every later record
//! names it. The run's last stage is recorded at exit with how the run ended.
//! Does nothing while telemetry is off.

use std::time::Instant;

use crate::command::CommandCompletion;
use crate::error::ErrorCategory;

use super::facts::{facts, update_facts};
use super::otel_logger::serialized_name;

/// A step of a command. The command is on every record, so steps several
/// commands share (`compile`) have one name.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    Credentials,
    Preflight,
    Confirm,
    ContextHubSync,
    Compile,
    Bake,
    Deploy,
    WaitReady,
    EnvSetup,
    DepsInstall,
    ServerStart,
    Serving,
    Chatting,
}

impl Stage {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Credentials => "credentials",
            Self::Preflight => "preflight",
            Self::Confirm => "confirm",
            Self::ContextHubSync => "context_hub_sync",
            Self::Compile => "compile",
            Self::Bake => "bake",
            Self::Deploy => "deploy",
            Self::WaitReady => "wait_ready",
            Self::EnvSetup => "env_setup",
            Self::DepsInstall => "deps_install",
            Self::ServerStart => "server_start",
            Self::Serving => "serving",
            Self::Chatting => "chatting",
        }
    }
}

/// The stage running now and when it began.
#[derive(Clone, Copy, Debug)]
pub struct CurrentStage {
    pub stage: Stage,
    pub started: Instant,
}

/// How a stage ended.
#[derive(Clone, Copy)]
enum StageOutcome {
    Success,
    Failure(ErrorCategory),
    Cancelled,
}

/// The stage the run ended in ends the way the run did.
impl From<CommandCompletion> for StageOutcome {
    fn from(completion: CommandCompletion) -> Self {
        match completion {
            CommandCompletion::Succeeded => Self::Success,
            CommandCompletion::Failed(category) => Self::Failure(category),
            CommandCompletion::Cancelled => Self::Cancelled,
        }
    }
}

impl StageOutcome {
    fn name(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failure(_) => "failure",
            Self::Cancelled => "cancelled",
        }
    }

    fn error_category(self) -> Option<ErrorCategory> {
        match self {
            Self::Failure(category) => Some(category),
            Self::Success | Self::Cancelled => None,
        }
    }
}

/// Record the stage being left as a success and make `stage` current.
pub fn enter(stage: Stage) {
    record_open_stage(StageOutcome::Success);
    update_facts(|facts| {
        facts.stage = Some(CurrentStage {
            stage,
            started: Instant::now(),
        });
    });
}

/// Record the stage the run ended in, then clear it, so the completion record
/// that follows names no stage.
pub(super) fn close(completion: CommandCompletion) {
    record_open_stage(completion.into());
    update_facts(|facts| facts.stage = None);
}

/// Recorded while the stage is still current, so the record carries its name.
fn record_open_stage(outcome: StageOutcome) {
    if let Some(current) = facts().and_then(|facts| facts.stage) {
        record(current.started, outcome);
    }
}

fn record(started: Instant, outcome: StageOutcome) {
    tracing::info!(
        duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
        outcome = outcome.name(),
        error_category = outcome.error_category().as_ref().and_then(serialized_name),
        "stage completed"
    );
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::telemetry::otel_logger::tests::{attribute, capture, text};

    fn no_facts() -> Option<super::super::ReportableFacts> {
        None
    }

    #[test]
    fn a_stage_record_carries_its_duration_and_outcome() {
        let started = Instant::now() - Duration::from_millis(25);

        let logs = capture(no_facts, || {
            record(started, StageOutcome::Success);
            record(started, StageOutcome::Failure(ErrorCategory::Toolchain));
            record(started, StageOutcome::Cancelled);
        });

        assert_eq!(logs.len(), 3);
        assert!(matches!(
            attribute(&logs[0], "duration_ms"),
            Some(opentelemetry::logs::AnyValue::Int(ms)) if ms >= 25
        ));
        assert_eq!(attribute(&logs[0], "outcome"), text("success"));
        assert_eq!(attribute(&logs[0], "error_category"), None);
        assert_eq!(attribute(&logs[1], "outcome"), text("failure"));
        assert_eq!(attribute(&logs[1], "error_category"), text("toolchain"));
        assert_eq!(attribute(&logs[2], "outcome"), text("cancelled"));
    }

    // No test in this crate calls `init_facts`, so telemetry is off here.
    #[test]
    fn stages_record_nothing_while_telemetry_is_off() {
        let logs = capture(no_facts, || {
            enter(Stage::Compile);
            close(CommandCompletion::Succeeded);
        });

        assert!(logs.is_empty());
    }
}
