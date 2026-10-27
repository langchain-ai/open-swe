use std::env;
use std::io::{self, IsTerminal};

use serde::Serialize;
use uuid::Uuid;

use crate::install::INSTALL_CHANNEL_ENV;
use crate::version;

use super::gates::is_ci_environment;
use super::identity::resolve_identity;

#[derive(Clone, Copy, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum InstallChannel {
    Npm,
    Pypi,
    #[default]
    Unknown,
}

#[derive(Clone, Copy, Default, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum InvokerDetectionMethod {
    CiEnvironment,
    InteractiveTerminal,
    #[default]
    NonInteractiveTerminal,
}

/// Values shared by every event created during one CLI invocation.
#[derive(Clone, Default, Serialize)]
pub struct InvocationMetadata {
    pub(super) install_id: Option<String>,
    pub(super) invocation_id: String,
    pub(super) cli_version: String,
    pub(super) os: &'static str,
    pub(super) install_channel: InstallChannel,
    pub(super) invoker_detection_method: InvokerDetectionMethod,
}

impl InvocationMetadata {
    /// Read safe, bounded values for the current CLI process.
    pub fn detect() -> Self {
        let install_id = resolve_identity();
        let install_channel = detect_install_channel(env::var(INSTALL_CHANNEL_ENV).ok().as_deref());
        let is_ci = is_ci_environment(|name| env::var(name).ok());
        let has_interactive_terminal = io::stdin().is_terminal() && io::stderr().is_terminal();

        Self {
            install_id,
            invocation_id: Uuid::new_v4().to_string(),
            cli_version: version::package_version(),
            os: env::consts::OS,
            install_channel,
            invoker_detection_method: detect_invoker(is_ci, has_interactive_terminal),
        }
    }
}

fn detect_install_channel(value: Option<&str>) -> InstallChannel {
    match value.map(str::trim) {
        Some("npm") => InstallChannel::Npm,
        Some("pypi") => InstallChannel::Pypi,
        _ => InstallChannel::Unknown,
    }
}

fn detect_invoker(is_ci: bool, has_interactive_terminal: bool) -> InvokerDetectionMethod {
    if is_ci {
        return InvokerDetectionMethod::CiEnvironment;
    }
    if has_interactive_terminal {
        return InvokerDetectionMethod::InteractiveTerminal;
    }
    InvokerDetectionMethod::NonInteractiveTerminal
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn ci_is_reported_even_when_a_terminal_is_present() {
        let is_ci = true;
        let has_terminal = true;

        let reported = detect_invoker(is_ci, has_terminal);

        assert_eq!(
            serde_json::to_value(reported).unwrap(),
            json!("ci_environment")
        );
    }

    #[test]
    fn an_unknown_install_channel_is_reported_as_unknown() {
        let reported = detect_install_channel(Some("private-channel"));

        assert_eq!(serde_json::to_value(reported).unwrap(), json!("unknown"));
    }
}
