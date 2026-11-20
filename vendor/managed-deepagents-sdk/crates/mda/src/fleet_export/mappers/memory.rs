//! A Fleet agent's repo is also its memory: every file outside Fleet's own
//! configuration is something the agent wrote or a user uploaded. Export turns
//! on agent memory and copies those files for `mda memory upload` to commit
//! into the deployment's memory after the first deploy, since the deployment's
//! memory doesn't exist until then and `mda deploy` doesn't upload memory.

use crate::fleet_export::{Export, FleetAgent, EXPORT_FILES_DIR};
use crate::init::MemoryChoice;

/// Fleet's configuration, which other mappers carry over. Everything else in
/// the repo is memory, including a file directly under `skills/`: Fleet loads
/// only `skills/<name>/` folders as skills.
fn is_config(path: &str) -> bool {
    matches!(path, "AGENTS.md" | "tools.json")
        || path
            .strip_prefix("skills/")
            .is_some_and(|rest| rest.contains('/'))
        || path.starts_with("subagents/")
}

pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    out.blueprint.memory = Some(MemoryChoice::Agent);
    let extension = out.blueprint.language.extension();
    out.report
        .memory("Fleet's `AGENTS.md` is now `instructions.md`.");
    out.report.memory(format!(
        "`memory.{extension}` gives the agent its own memory in `memories/agent/`. It starts empty, and the agent adds to it as it learns."
    ));

    let paths: Vec<String> = agent
        .files
        .keys()
        .filter(|path| !is_config(path) && !out.claimed_files.contains(*path))
        .cloned()
        .collect();
    if paths.is_empty() {
        return;
    }
    for path in &paths {
        out.claimed_files.insert(path.clone());
        out.files.insert(
            format!("{EXPORT_FILES_DIR}/memories/{path}"),
            agent.files[path].content.clone(),
        );
    }
    out.report.memory_files = paths;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_files_outside_fleet_config_are_memory() {
        for (path, config) in [
            ("AGENTS.md", true),
            ("tools.json", true),
            ("skills/triage/SKILL.md", true),
            ("skills/README.md", false),
            ("subagents/researcher/AGENTS.md", true),
            ("wiki/AGENTS.md", false),
            ("notes/tools.json", false),
            ("skills.md", false),
            ("pricing.md", false),
        ] {
            assert_eq!(is_config(path), config, "{path}");
        }
    }
}
