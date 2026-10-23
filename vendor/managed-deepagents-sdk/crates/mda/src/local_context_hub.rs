//! Local Context Hub mock for `mda build` / `mda dev`.
//!
//! Mirrors the deploy-time Hub layout on disk under `.mda/__contexthub__` so the
//! managed runtime mounts instructions, skills, and agent memory the same way it
//! does against a remote Context Hub repo. Local user memory lives separately
//! under `__users__/`, where the runtime opens it by opaque user-repo handle.
//!
//! The agent tree is seeded only when the project declares the agent slice.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use walkdir::WalkDir;

use crate::context_layout::{
    project_context_root, resolve_agent_memory_seed, AgentMemorySeed, AGENT_MEMORY_FILE,
    FLAT_MEMORY_FILE, INSTRUCTIONS_FILE, LOCAL_CONTEXT_HUB_DIR, SKILLS_DIR,
};
use crate::project::Project;

/// Resolved local Context Hub mock used by compile/dev entries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalContextHub {
    /// Absolute path to `.mda/__contexthub__`.
    pub root: PathBuf,
    /// Whether the mock contains deploy-owned skills.
    pub has_skills: bool,
}

/// Ensure `.mda/__contexthub__` exists, sync harness files, and seed memory.
///
/// - `instructions.md` and `skills/**` are refreshed from the project each call.
/// - When `agent_memory_enabled`, `memories/agent/**` is seeded once and then
///   preserved (same as deploy: never overwrite existing hot memory).
/// - Otherwise existing memory is left untouched. Runtime layer selection
///   controls whether preserved memory is mounted.
pub fn ensure_local_context_hub(
    project: &Project,
    agent_memory_enabled: bool,
) -> Result<LocalContextHub> {
    let root = project.root.join(".mda").join(LOCAL_CONTEXT_HUB_DIR);
    fs::create_dir_all(&root).with_context(|| format!("failed to create {}", root.display()))?;

    let source_root = project_context_root(project);
    sync_instructions(&source_root, &root)?;
    let has_skills = sync_skills(&source_root, &root)?;
    if agent_memory_enabled {
        seed_agent_memory(&root)?;
    }

    let absolute = root
        .canonicalize()
        .with_context(|| format!("failed to resolve {}", root.display()))?;

    Ok(LocalContextHub {
        root: absolute,
        has_skills,
    })
}

fn sync_instructions(source_root: &Path, hub_root: &Path) -> Result<()> {
    let src = source_root.join(INSTRUCTIONS_FILE);
    let dst = hub_root.join(INSTRUCTIONS_FILE);
    if src.is_file() {
        let contents = fs::read_to_string(&src)
            .with_context(|| format!("failed to read {}", src.display()))?;
        fs::write(&dst, contents).with_context(|| format!("failed to write {}", dst.display()))?;
    } else if dst.is_file() {
        fs::remove_file(&dst)
            .with_context(|| format!("failed to remove stale {}", dst.display()))?;
    }
    Ok(())
}

fn sync_skills(source_root: &Path, hub_root: &Path) -> Result<bool> {
    let src_skills = source_root.join(SKILLS_DIR);
    let dst_skills = hub_root.join(SKILLS_DIR);

    if dst_skills.is_dir() {
        fs::remove_dir_all(&dst_skills)
            .with_context(|| format!("failed to clear {}", dst_skills.display()))?;
    }

    if !src_skills.is_dir() {
        return Ok(false);
    }

    let mut has_skills = false;
    for entry in WalkDir::new(&src_skills)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !crate::project::is_junk_dir(entry.path()))
    {
        let entry = entry.with_context(|| format!("failed to walk {}", src_skills.display()))?;
        if !entry.file_type().is_file() || crate::project::is_junk_file(entry.path()) {
            continue;
        }
        let rel = entry
            .path()
            .strip_prefix(&src_skills)
            .with_context(|| format!("failed to relativize {}", entry.path().display()))?;
        let dst = dst_skills.join(rel);
        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        fs::copy(entry.path(), &dst).with_context(|| {
            format!(
                "failed to copy {} -> {}",
                entry.path().display(),
                dst.display()
            )
        })?;
        has_skills = true;
    }
    Ok(has_skills)
}

fn seed_agent_memory(hub_root: &Path) -> Result<()> {
    let agent = hub_root.join(AGENT_MEMORY_FILE);
    let agent_content = if agent.is_file() {
        Some(
            fs::read_to_string(&agent)
                .with_context(|| format!("failed to read {}", agent.display()))?,
        )
    } else {
        None
    };
    let flat = hub_root.join(FLAT_MEMORY_FILE);
    let flat_content = if agent_content.is_none() && flat.is_file() {
        Some(
            fs::read_to_string(&flat)
                .with_context(|| format!("failed to read {}", flat.display()))?,
        )
    } else {
        None
    };
    let AgentMemorySeed::Write(content) =
        resolve_agent_memory_seed(agent_content.as_deref(), flat_content.as_deref())
    else {
        return Ok(());
    };
    if let Some(parent) = agent.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    fs::write(&agent, content).with_context(|| format!("failed to seed {}", agent.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context_layout::MEMORIES_DIR;
    use crate::project::{Language, Project};
    use crate::test_support::write_file;
    use std::path::PathBuf;

    fn project(root: &Path, agent: &str) -> Project {
        Project {
            root: root.to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from(agent),
            files: vec![PathBuf::from(agent)],
        }
    }

    #[test]
    fn ensure_local_context_hub_seeds_agent_memory() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");
        write_file(tmp.path(), "instructions.md", "Be helpful.\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();

        assert!(hub
            .root
            .ends_with(Path::new(".mda").join(LOCAL_CONTEXT_HUB_DIR)));
        assert_eq!(
            fs::read_to_string(hub.root.join(INSTRUCTIONS_FILE)).unwrap(),
            "Be helpful.\n"
        );
        assert!(hub.root.join(AGENT_MEMORY_FILE).is_file());
        assert!(!hub.has_skills);
    }

    #[test]
    fn ensure_local_context_hub_preserves_existing_memory() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");
        write_file(tmp.path(), "instructions.md", "v1\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();
        let memory = hub.root.join(AGENT_MEMORY_FILE);
        fs::write(&memory, "custom preference\n").unwrap();

        write_file(tmp.path(), "instructions.md", "v2\n");
        let hub2 = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();

        assert_eq!(
            fs::read_to_string(hub2.root.join(INSTRUCTIONS_FILE)).unwrap(),
            "v2\n"
        );
        assert_eq!(fs::read_to_string(memory).unwrap(), "custom preference\n");
    }

    #[test]
    fn ensure_local_context_hub_seeds_an_empty_hot_file() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();

        let hot = hub.root.join(AGENT_MEMORY_FILE);
        assert!(
            hot.is_file(),
            "the file exists so edit_file works on turn one"
        );
        assert_eq!(fs::read_to_string(hot).unwrap(), "");
    }

    #[test]
    fn ensure_local_context_hub_skips_the_seed_when_memory_is_off() {
        // A fresh project that declares no memory gets no local hot file. A
        // dynamic run can create memory later if it selects the agent layer.
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), false).unwrap();

        assert!(!hub.root.join(AGENT_MEMORY_FILE).exists());
        assert!(!hub.root.join(FLAT_MEMORY_FILE).exists());
        assert!(!hub.root.join(MEMORIES_DIR).exists());
    }

    #[test]
    fn ensure_local_context_hub_preserves_memory_when_disabled_after_use() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();
        let memory = hub.root.join(AGENT_MEMORY_FILE);
        fs::write(&memory, "remember this\n").unwrap();
        write_file(&hub.root, "memories/agent/notes.md", "extra\n");
        write_file(&hub.root, "__users__/opaque/AGENTS.md", "user memory\n");

        let hub2 = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), false).unwrap();

        assert_eq!(fs::read_to_string(memory).unwrap(), "remember this\n");
        assert_eq!(
            fs::read_to_string(hub2.root.join("memories/agent/notes.md")).unwrap(),
            "extra\n"
        );
        assert_eq!(
            fs::read_to_string(hub2.root.join("__users__/opaque/AGENTS.md")).unwrap(),
            "user memory\n"
        );
    }

    #[test]
    fn ensure_local_context_hub_syncs_skills_next_to_agent() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "src/agent.ts", "export const agent = {};\n");
        write_file(tmp.path(), "src/skills/research/SKILL.md", "Research.\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "src/agent.ts"), true).unwrap();

        assert!(hub.has_skills);
        assert_eq!(
            fs::read_to_string(hub.root.join("skills/research/SKILL.md")).unwrap(),
            "Research.\n"
        );
    }

    #[test]
    fn ensure_local_context_hub_migrates_flat_memory_into_agent_slice() {
        let tmp = tempfile::tempdir().unwrap();
        write_file(tmp.path(), "agent.ts", "export const agent = {};\n");
        let hub_root = tmp.path().join(".mda").join(LOCAL_CONTEXT_HUB_DIR);
        write_file(&hub_root, FLAT_MEMORY_FILE, "legacy hot\n");

        let hub = ensure_local_context_hub(&project(tmp.path(), "agent.ts"), true).unwrap();

        assert_eq!(
            fs::read_to_string(hub.root.join(AGENT_MEMORY_FILE)).unwrap(),
            "legacy hot\n"
        );
        assert_eq!(
            fs::read_to_string(hub.root.join(FLAT_MEMORY_FILE)).unwrap(),
            "legacy hot\n"
        );
    }
}
