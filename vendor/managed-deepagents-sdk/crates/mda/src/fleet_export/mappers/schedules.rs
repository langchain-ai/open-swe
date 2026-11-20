use crate::fleet_export::{Export, FleetAgent};

/// Stub: schedules aren't exported yet. MDAC-275 replaces this with a list of
/// schedules to recreate. Only the caller's schedules are visible: Fleet lists
/// schedules per user, so other users' schedules on a shared agent are unseen.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    if agent.schedules.is_empty() {
        return;
    }
    let crons: Vec<&str> = agent
        .schedules
        .iter()
        .map(|schedule| schedule.cron.as_str())
        .collect();
    out.report.skip(
        "schedules",
        format!("{}", crons.len()),
        format!("schedules aren't exported yet: {}", crons.join(", ")),
    );
}
