use crate::fleet_export::{Export, FleetAgent, EXPORT_FILES_DIR};

/// Copies every file no other mapper claimed into `fleet_export_files/`. The
/// exported agent cannot read them at runtime, so each one is listed. Runs
/// after every mapper that claims files.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    let mut copied = Vec::new();
    for (path, entry) in &agent.files {
        if out.claimed_files.contains(path) {
            continue;
        }
        out.files
            .insert(format!("{EXPORT_FILES_DIR}/{path}"), entry.content.clone());
        copied.push(path.as_str());
    }
    if !copied.is_empty() {
        out.report.warn(format!(
            "Files: copied to {EXPORT_FILES_DIR}/, but the agent can't read them: {}",
            copied.join(", ")
        ));
    }
}
