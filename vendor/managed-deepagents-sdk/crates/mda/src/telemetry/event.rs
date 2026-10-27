use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Serialize, Serializer};
use uuid::Uuid;

use crate::command::CommandCompletion;
use crate::error::ErrorCategory;
use crate::logs::rfc3339_from_epoch_millis;

use super::facts::ReportableFacts;
#[cfg(test)]
use super::{
    command::CommandInvocation,
    metadata::{InstallChannel, InvocationMetadata, InvokerDetectionMethod},
};

const SCHEMA_VERSION: &str = "2026-08-20";

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum CommandOutcome {
    Success,
    Failure,
    Cancelled,
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum ErrorCode {
    Unclassified,
}

pub(super) enum EventType {
    CommandCompleted,
}

impl EventType {
    pub(super) const fn as_str(&self) -> &'static str {
        match self {
            Self::CommandCompleted => "command_completed",
        }
    }
}

impl Serialize for EventType {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        serializer.serialize_str(self.as_str())
    }
}

/// How the command ended, as reported by `command_completed` and the
/// completion log record.
#[derive(Serialize)]
pub(super) struct CommandCompleted {
    pub(super) outcome: CommandOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) error_code: Option<ErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) error_category: Option<ErrorCategory>,
}

impl From<CommandCompletion> for CommandCompleted {
    fn from(completion: CommandCompletion) -> Self {
        let (outcome, error_code, error_category) = match completion {
            CommandCompletion::Succeeded => (CommandOutcome::Success, None, None),
            CommandCompletion::Cancelled => (CommandOutcome::Cancelled, None, None),
            CommandCompletion::Failed(category) => (
                CommandOutcome::Failure,
                Some(ErrorCode::Unclassified),
                Some(category),
            ),
        };
        Self {
            outcome,
            error_code,
            error_category,
        }
    }
}

/// A serialized analytics event: its own identity, how the command ended, and
/// the invocation's facts.
#[derive(Serialize)]
pub struct EventEnvelope {
    pub(super) event_id: String,
    pub(super) event_type: EventType,
    pub(super) schema_version: &'static str,
    pub(super) created_at: String,
    #[serde(flatten)]
    pub(super) command_completed: CommandCompleted,
    #[serde(flatten)]
    facts: ReportableFacts,
    /// Copied out of `facts` because an event cannot exist without one.
    #[serde(skip)]
    pub(super) install_id: String,
}

impl EventEnvelope {
    /// Build a completion event without retaining command errors or their
    /// messages. `None` when there is no installation id to attribute it to.
    pub fn command_completed(
        facts: ReportableFacts,
        completion: CommandCompletion,
    ) -> Option<Self> {
        Self::command_completed_at(
            facts,
            completion,
            Uuid::new_v4().to_string(),
            SystemTime::now(),
        )
    }

    fn command_completed_at(
        facts: ReportableFacts,
        completion: CommandCompletion,
        event_id: String,
        created_at: SystemTime,
    ) -> Option<Self> {
        Some(Self {
            install_id: facts.run.install_id.clone()?,
            event_id,
            event_type: EventType::CommandCompleted,
            schema_version: SCHEMA_VERSION,
            created_at: format_timestamp(created_at),
            command_completed: completion.into(),
            facts,
        })
    }
}

#[cfg(test)]
pub(super) fn fixture_facts() -> ReportableFacts {
    ReportableFacts {
        run: InvocationMetadata {
            install_id: Some("33333333-3333-4333-8333-333333333333".to_string()),
            invocation_id: "11111111-1111-4111-8111-111111111111".to_string(),
            cli_version: "0.5.3".to_string(),
            os: "linux",
            install_channel: InstallChannel::Npm,
            invoker_detection_method: InvokerDetectionMethod::InteractiveTerminal,
        },
        command: CommandInvocation {
            command_path: "channel init slack".to_string(),
            flags_present: vec!["name".to_string()],
        },
        ..ReportableFacts::default()
    }
}

/// A fully determined `command_completed` event for tests that need stable JSON.
#[cfg(test)]
pub(super) fn fixture_event() -> EventEnvelope {
    EventEnvelope::command_completed_at(
        fixture_facts(),
        CommandCompletion::Succeeded,
        "22222222-2222-4222-8222-222222222222".to_string(),
        UNIX_EPOCH + std::time::Duration::from_millis(1234),
    )
    .unwrap()
}

fn format_timestamp(time: SystemTime) -> String {
    let millis = time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;
    rfc3339_from_epoch_millis(millis)
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, UNIX_EPOCH};

    use serde_json::json;

    use super::*;

    #[test]
    fn successful_command_has_the_expected_json() {
        let event = EventEnvelope::command_completed_at(
            fixture_facts(),
            CommandCompletion::Succeeded,
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH + Duration::from_millis(1234),
        )
        .unwrap();

        assert_eq!(
            serde_json::to_value(event).unwrap(),
            json!({
                "event_id": "22222222-2222-4222-8222-222222222222",
                "user_id": null, "org_id": null, "workspace_id": null,
                "deployment_id": null, "deployment_name": null, "langsmith_host_url": null,
                "install_id": "33333333-3333-4333-8333-333333333333",
                "invocation_id": "11111111-1111-4111-8111-111111111111",
                "event_type": "command_completed",
                "schema_version": "2026-08-20",
                "created_at": "1970-01-01T00:00:01.234Z",
                "cli_version": "0.5.3",
                "os": "linux",
                "install_channel": "npm",
                "invoker_detection_method": "interactive_terminal",
                "command_path": "channel init slack",
                "flags_present": ["name"],
                "outcome": "success"
            })
        );
    }

    #[test]
    fn cancelled_command_has_no_error() {
        let event = EventEnvelope::command_completed_at(
            fixture_facts(),
            CommandCompletion::Cancelled,
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        )
        .unwrap();

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "outcome": "cancelled"
            })
        );
    }

    #[test]
    fn a_failure_reports_its_category() {
        let event = EventEnvelope::command_completed_at(
            fixture_facts(),
            CommandCompletion::Failed(ErrorCategory::Deployment),
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        )
        .unwrap();

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "outcome": "failure",
                "error_code": "unclassified",
                "error_category": "deployment"
            })
        );
    }

    #[test]
    fn an_unrecognized_failure_reports_unclassified() {
        let event = EventEnvelope::command_completed_at(
            fixture_facts(),
            CommandCompletion::Failed(ErrorCategory::Unclassified),
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        )
        .unwrap();

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "outcome": "failure",
                "error_code": "unclassified",
                "error_category": "unclassified"
            })
        );
    }

    #[test]
    fn the_current_stage_is_not_reported() {
        use crate::telemetry::stage::{CurrentStage, Stage};

        let mut facts = fixture_facts();
        facts.stage = Some(CurrentStage {
            stage: Stage::Compile,
            started: std::time::Instant::now(),
        });

        let event = EventEnvelope::command_completed(facts, CommandCompletion::Succeeded).unwrap();

        assert!(serde_json::to_value(event).unwrap().get("stage").is_none());
    }

    #[test]
    fn an_invocation_without_an_installation_id_reports_nothing() {
        let mut facts = fixture_facts();
        facts.run.install_id = None;

        assert!(EventEnvelope::command_completed(facts, CommandCompletion::Succeeded).is_none());
    }
}
