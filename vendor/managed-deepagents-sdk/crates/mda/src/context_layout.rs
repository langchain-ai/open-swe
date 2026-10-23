//! Shared Context Hub layout constants for deploy sync and local `mda dev` mock.

use std::path::PathBuf;

use crate::project::Project;

/// Deploy-owned system prompt file at the Hub root.
pub const INSTRUCTIONS_FILE: &str = "instructions.md";

/// Deploy-owned skills directory at the Hub root.
pub const SKILLS_DIR: &str = "skills";

/// Agent-owned memory tree at the Hub root (sliced per scope before remount).
pub const MEMORIES_DIR: &str = "memories";

/// Hot memory file for the agent-scoped slice (`memory: agent` / no identity).
pub const AGENT_MEMORY_FILE: &str = "memories/agent/AGENTS.md";

/// Legacy flat hot memory path (pre-slice layout). Preserved when already present.
pub const FLAT_MEMORY_FILE: &str = "memories/AGENTS.md";

/// Directory name under `.mda/` for the local Context Hub filesystem mock.
pub const LOCAL_CONTEXT_HUB_DIR: &str = "__contexthub__";

/// Seed content for a new agent-scoped hot memory file.
///
/// Empty on purpose. The instructions that used to live here — how to use memory,
/// which paths exist, what not to store — are stated by the runtime instead
/// (`packages/npm/src/memory/instructions-middleware.ts` and its Python mirror),
/// because prose committed into a Hub file is customer data: it renders as "agent
/// memory" in the Context Hub UI, and it goes stale in place with no way to correct
/// it except rewriting someone's file.
///
/// A zero-byte file rather than no file at all: it is prompt-identical (both render
/// as no memory loaded), but it keeps `edit_file` — the verb deepagents teaches —
/// working on the agent's first save, and keeps the `mda dev` mock structurally
/// identical to a deployment.
pub const DEFAULT_MEMORY_CONTENT: &str = "";

/// Content that should be written to [`AGENT_MEMORY_FILE`], if any.
///
/// Shared by the local Hub mock and deploy-time Context Hub sync so both apply
/// the same migration / refresh rules.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentMemorySeed {
    /// Existing agent-scoped memory is current — leave it alone.
    Keep,
    /// Write this content to [`AGENT_MEMORY_FILE`] (seed, migrate, or refresh).
    Write(String),
}

/// Decide what to do with agent-scoped hot memory given the current Hub state.
///
/// Deploy never rewrites memory. An existing [`AGENT_MEMORY_FILE`] is left exactly as
/// it is, whatever is in it; a legacy [`FLAT_MEMORY_FILE`] is copied across verbatim
/// when it is the only hot memory present, so the pre-slice layout still remounts.
/// Only a repo with neither gets the empty seed.
pub fn resolve_agent_memory_seed(
    agent_content: Option<&str>,
    flat_content: Option<&str>,
) -> AgentMemorySeed {
    if agent_content.is_some() {
        return AgentMemorySeed::Keep;
    }
    if let Some(flat) = flat_content {
        return AgentMemorySeed::Write(flat.to_string());
    }
    AgentMemorySeed::Write(DEFAULT_MEMORY_CONTENT.to_string())
}

/// Project directory that owns `instructions.md`, `skills/`, and `memories/`.
///
/// Usually the agent file's parent; falls back to the project root when the
/// agent sits at the root.
pub(crate) fn project_context_root(project: &Project) -> PathBuf {
    project
        .agent_file
        .parent()
        .map(|parent| project.root.join(parent))
        .unwrap_or_else(|| project.root.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_leaves_deployed_memory_alone() {
        // Deploy must never rewrite memory. That includes files an older CLI seeded
        // with its own instructions: they are the agent's file now, and the runtime
        // states the policy regardless of what the file happens to contain.
        for content in [
            "",
            "- favorite color: blue\n",
            "<!-- MANAGED-DEEPAGENTS MEMORY INSTRUCTIONS: DO NOT DELETE -->\n# Memory Instructions\n",
        ] {
            assert_eq!(
                resolve_agent_memory_seed(Some(content), None),
                AgentMemorySeed::Keep
            );
        }
    }

    #[test]
    fn resolve_seeds_an_empty_file_for_a_fresh_deploy() {
        // The instructions live in the runtime, so a new deployment starts with a
        // file that holds nothing but what its agent goes on to write.
        assert_eq!(
            resolve_agent_memory_seed(None, None),
            AgentMemorySeed::Write(String::new())
        );
        assert!(DEFAULT_MEMORY_CONTENT.is_empty());
    }

    #[test]
    fn resolve_migrates_legacy_flat_memory_verbatim() {
        let flat = "- from the flat layout\n";

        assert_eq!(
            resolve_agent_memory_seed(None, Some(flat)),
            AgentMemorySeed::Write(flat.to_string())
        );
    }
}
