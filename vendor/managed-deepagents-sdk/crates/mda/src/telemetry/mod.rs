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

pub use command::CommandInvocation;
pub use delivery::run_within;
pub use event::EventEnvelope;
pub use exit::{deliver_at_exit, hold_for_exit, report_panics};
pub use facts::{
    facts, init_facts, refresh_identity, update_facts, AuthInfo, DeploymentInfo, ReportableFacts,
};
pub use gates::is_telemetry_disabled;
pub use metadata::InvocationMetadata;
pub use otel_logger::{OtelLogger, MAX_STACKTRACE_BYTES};
pub use posthog::{posthog_transport, PosthogTransport};
pub use transport::TelemetryTransport;
