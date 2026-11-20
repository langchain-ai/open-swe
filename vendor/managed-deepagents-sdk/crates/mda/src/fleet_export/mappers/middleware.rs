use crate::fleet_export::{Export, FleetAgent};

/// Enabled middleware isn't exported: MDA has no equivalent in v0.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    if !agent.middleware_beta_confirmed {
        out.report.warn(
            "Middleware: Fleet didn't confirm it returned this agent's guardrails. Check the agent's middleware in Fleet.",
        );
    }
    let enabled: Vec<&str> = agent
        .middleware
        .iter()
        .filter(|middleware| middleware.is_enabled)
        .map(|middleware| middleware.id.as_str())
        .collect();
    if !enabled.is_empty() {
        out.report.skip(
            "middleware",
            format!("{}", enabled.len()),
            format!("MDA has no equivalent yet: {}", enabled.join(", ")),
        );
    }
}
