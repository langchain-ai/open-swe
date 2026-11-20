use crate::fleet_export::{Export, FleetAgent};

/// `AGENTS.md` becomes `instructions.md`.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    out.claimed_files.insert("AGENTS.md".to_string());
    match &agent.system_prompt {
        Some(prompt) if !prompt.trim().is_empty() => {
            out.blueprint.instructions = Some(prompt.clone())
        }
        _ => out.report.warn(
            "Instructions: the Fleet agent has none, so instructions.md has the starter prompt.",
        ),
    }
}
