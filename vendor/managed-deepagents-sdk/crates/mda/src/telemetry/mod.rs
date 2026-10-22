//! Backend-neutral product analytics event contracts for the `mda` CLI.

mod command;
mod event;
mod facts;
mod gates;
mod identity;
mod metadata;
mod posthog;
mod transport;

pub use command::{CommandContext, CommandInvocation};
pub use event::EventEnvelope;
pub use facts::{AuthInfo, DeploymentInfo, ReportableFacts};
pub use gates::is_telemetry_disabled;
pub use metadata::InvocationMetadata;
pub use posthog::{posthog_transport, PosthogTransport};
pub use transport::TelemetryTransport;
