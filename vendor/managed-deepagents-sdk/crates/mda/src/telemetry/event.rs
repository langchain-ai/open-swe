use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Serialize, Serializer};
use uuid::Uuid;

use crate::command::CommandCompletion;
use crate::error::ErrorCategory;
use crate::logs::rfc3339_from_epoch_millis;

use super::command::CommandInvocation;
use super::facts::ReportableFacts;
use super::metadata::{InstallChannel, InvocationMetadata, InvokerDetectionMethod};

const SCHEMA_VERSION: &str = "2026-08-20";

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum CommandOutcome {
    Success,
    Failure,
    Cancelled,
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum ErrorCode {
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

#[derive(Serialize)]
pub(super) struct CommandCompleted {
    #[serde(flatten)]
    command: CommandInvocation,
    outcome: CommandOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<ErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_category: Option<ErrorCategory>,
}

/// A serialized analytics event with its shared invocation values.
#[derive(Serialize)]
pub struct EventEnvelope {
    pub(super) event_id: String,
    pub(super) install_id: String,
    pub(super) invocation_id: String,
    pub(super) event_type: EventType,
    pub(super) schema_version: &'static str,
    pub(super) created_at: String,
    pub(super) cli_version: String,
    pub(super) os: &'static str,
    pub(super) install_channel: InstallChannel,
    pub(super) invoker_detection_method: InvokerDetectionMethod,
    #[serde(flatten)]
    pub(super) command_completed: CommandCompleted,
    #[serde(flatten)]
    facts: ReportableFacts,
}

impl EventEnvelope {
    /// Build a completion event without retaining command errors or their messages.
    pub fn command_completed(
        metadata: &InvocationMetadata,
        facts: ReportableFacts,
        command: CommandInvocation,
        completion: CommandCompletion,
    ) -> Self {
        Self::command_completed_at(
            metadata,
            facts,
            command,
            completion,
            Uuid::new_v4().to_string(),
            SystemTime::now(),
        )
    }

    fn command_completed_at(
        metadata: &InvocationMetadata,
        facts: ReportableFacts,
        command: CommandInvocation,
        completion: CommandCompletion,
        event_id: String,
        created_at: SystemTime,
    ) -> Self {
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
            event_id,
            facts,
            install_id: metadata.install_id.clone(),
            invocation_id: metadata.invocation_id.clone(),
            event_type: EventType::CommandCompleted,
            schema_version: SCHEMA_VERSION,
            created_at: format_timestamp(created_at),
            cli_version: metadata.cli_version.clone(),
            os: metadata.os,
            install_channel: metadata.install_channel,
            invoker_detection_method: metadata.invoker_detection_method,
            command_completed: CommandCompleted {
                command,
                outcome,
                error_code,
                error_category,
            },
        }
    }
}

#[cfg(test)]
pub(super) fn fixture_metadata() -> InvocationMetadata {
    InvocationMetadata {
        install_id: "33333333-3333-4333-8333-333333333333".to_string(),
        invocation_id: "11111111-1111-4111-8111-111111111111".to_string(),
        cli_version: "0.5.3".to_string(),
        os: "linux",
        install_channel: InstallChannel::Npm,
        invoker_detection_method: InvokerDetectionMethod::InteractiveTerminal,
    }
}

#[cfg(test)]
pub(super) fn fixture_command() -> CommandInvocation {
    CommandInvocation {
        command_path: "channel init slack".to_string(),
        flags_present: vec!["name".to_string()],
    }
}

/// A fully determined `command_completed` event for tests that need stable JSON.
#[cfg(test)]
pub(super) fn fixture_event() -> EventEnvelope {
    EventEnvelope::command_completed_at(
        &fixture_metadata(),
        ReportableFacts::default(),
        fixture_command(),
        CommandCompletion::Succeeded,
        "22222222-2222-4222-8222-222222222222".to_string(),
        UNIX_EPOCH + std::time::Duration::from_millis(1234),
    )
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
        let metadata = fixture_metadata();
        let command = fixture_command();
        let event = EventEnvelope::command_completed_at(
            &metadata,
            ReportableFacts::default(),
            command,
            CommandCompletion::Succeeded,
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH + Duration::from_millis(1234),
        );

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
        let metadata = fixture_metadata();
        let command = fixture_command();
        let event = EventEnvelope::command_completed_at(
            &metadata,
            ReportableFacts::default(),
            command,
            CommandCompletion::Cancelled,
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        );

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "command_path": "channel init slack",
                "flags_present": ["name"],
                "outcome": "cancelled"
            })
        );
    }

    #[test]
    fn a_failure_reports_its_category() {
        let metadata = fixture_metadata();
        let command = fixture_command();
        let event = EventEnvelope::command_completed_at(
            &metadata,
            ReportableFacts::default(),
            command,
            CommandCompletion::Failed(ErrorCategory::Deployment),
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        );

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "command_path": "channel init slack",
                "flags_present": ["name"],
                "outcome": "failure",
                "error_code": "unclassified",
                "error_category": "deployment"
            })
        );
    }

    #[test]
    fn an_unrecognized_failure_reports_unclassified() {
        let metadata = fixture_metadata();
        let command = fixture_command();
        let event = EventEnvelope::command_completed_at(
            &metadata,
            ReportableFacts::default(),
            command,
            CommandCompletion::Failed(ErrorCategory::Unclassified),
            "22222222-2222-4222-8222-222222222222".to_string(),
            UNIX_EPOCH,
        );

        assert_eq!(
            serde_json::to_value(event.command_completed).unwrap(),
            json!({
                "command_path": "channel init slack",
                "flags_present": ["name"],
                "outcome": "failure",
                "error_code": "unclassified",
                "error_category": "unclassified"
            })
        );
    }
}
