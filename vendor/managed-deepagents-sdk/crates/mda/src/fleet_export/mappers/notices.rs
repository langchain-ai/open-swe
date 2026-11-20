//! What every export tells the developer about Fleet state that never moves.
//!
//! For example, past conversations stay in Fleet. These are not features a
//! mapper carries over, so they come from here instead of a feature mapper.

use crate::fleet_export::snapshot::Permissions;
use crate::fleet_export::{Export, FleetAgent};

pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    out.report.leave_behind(
        "Conversations: past threads stay in Fleet. The deployment starts with none.",
    );
    if agent
        .permissions
        .as_ref()
        .is_some_and(Permissions::is_shared)
    {
        out.report.leave_behind(
            "Sharing: people you shared the agent with in Fleet don't get access to the deployment.",
        );
    }
    if agent
        .tools
        .as_ref()
        .is_some_and(|config| !config.tools.is_empty())
    {
        out.report.leave_behind(
            "Credentials: saved logins don't carry over. Each user signs in again the first time a tool needs it.",
        );
    }
    out.report.leave_behind(
        "Traces: the deployment traces to a new LangSmith project. Past traces stay in the Fleet agent's project.",
    );
}
