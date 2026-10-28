//! What the `mda` CLI reports about each invocation: the facts, the
//! `command_completed` analytics event and its transports, and the OTel logger.

mod command;
mod delivery;
mod event;
mod exit;
mod facts;
mod gates;
mod identity;
mod metadata;
pub mod otel_logger;
mod posthog;
pub mod stage;
mod transport;

#[cfg(test)]
pub use command::CommandInvocation;
pub use exit::{deliver_at_exit, hold_for_exit, report_panics};
pub use facts::{init_facts, refresh_identity, update_facts, DeploymentInfo, ReportableFacts};
pub use gates::is_telemetry_disabled;
pub use otel_logger::{OtelLogger, MAX_STACKTRACE_BYTES};
pub use posthog::posthog_transport;
