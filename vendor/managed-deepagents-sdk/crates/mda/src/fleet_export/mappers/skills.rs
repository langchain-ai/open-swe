use std::collections::BTreeSet;

use crate::fleet_export::{Export, FleetAgent};

/// Fleet's built-in skills come with its runtime, not the agent's repo, so
/// export can't see which ones an agent used.
const BUILT_IN: &str = "Fleet's built-in skills (presentation-builder, svg-builder, mermaid-diagram-builder, generate-skill, fleet-guide and others) ship with Fleet's runtime. The exported agent doesn't have them.";

/// Fleet skills become `skills/<name>/`. The agent's own skills are files
/// under `skills/<name>/`. A linked workspace skill is a `skill` entry at
/// `skills/<repo_handle>`, and Fleet returns its files under that path at the
/// commit the agent selected, so both kinds are copied the same way.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    let mut linked = BTreeSet::new();
    for (path, entry) in &agent.files {
        let Some(rest) = path.strip_prefix("skills/") else {
            continue;
        };
        if entry.kind == "skill" {
            out.claimed_files.insert(path.clone());
            linked.insert(rest.to_string());
            continue;
        }
        // A file directly under `skills/` isn't a skill; it's memory.
        let Some((name, file)) = rest.split_once('/') else {
            continue;
        };
        out.claimed_files.insert(path.clone());
        out.blueprint
            .skills
            .entry(name.to_string())
            .or_default()
            .insert(file.to_string(), entry.content.clone());
    }
    out.report
        .skip("skills", "Fleet's built-in skills", BUILT_IN);

    // A linked skill Fleet returned no files for has nothing to copy.
    let (copied, empty): (Vec<String>, Vec<String>) = linked
        .into_iter()
        .partition(|name| out.blueprint.skills.contains_key(name));
    if !empty.is_empty() {
        out.report.skip(
            "linked skills",
            format!("{}", empty.len()),
            format!(
                "Fleet returned no files for these linked workspace skills: {}",
                empty.join(", ")
            ),
        );
    }
    if out.blueprint.skills.is_empty() {
        return;
    }
    let names: Vec<&str> = out.blueprint.skills.keys().map(String::as_str).collect();
    out.report.skill(format!(
        "`mda deploy` uploads these skills from `skills/`: {}.",
        names.join(", ")
    ));
    out.report.skill(
        "The agent can't edit its skills anymore. In Fleet it could save what it learned into them; it now saves to `memories/agent/` instead.",
    );
    if !copied.is_empty() {
        out.report.skill(format!(
            "Linked workspace skills are copies taken at export and won't receive updates: {}.",
            copied.join(", ")
        ));
    }
}
