use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use anyhow::{Context, Result};
use walkdir::WalkDir;

use crate::context_layout::{project_context_root, INSTRUCTIONS_FILE, MEMORIES_DIR};
use crate::project::{is_junk_dir, is_junk_file, Project};

use super::{MemoryDecision, RuntimeContext};

pub(super) fn project_warnings(
    project: &Project,
    memory: &MemoryDecision,
    context: RuntimeContext<'_>,
    is_factory: bool,
) -> Result<Vec<String>> {
    let mut warnings = Vec::new();
    let instructions = project_context_root(project).join(INSTRUCTIONS_FILE);
    if !is_factory && !instructions.is_file() {
        warnings.push(
            "instructions.md is missing. Add it next to the agent file to set the system prompt."
                .to_string(),
        );
    }
    let RuntimeContext::Local { context_root, .. } = context else {
        return Ok(warnings);
    };
    let MemoryDecision::Declared(declaration) = memory else {
        return Ok(warnings);
    };
    if declaration.scope.agent && !has_agent_memory(Path::new(context_root))? {
        let memory_root = Path::new(context_root).join(MEMORIES_DIR).join("agent");
        let location = memory_root
            .strip_prefix(&project.root)
            .unwrap_or(&memory_root);
        warnings.push(format!(
            "{} enables agent memory, but {}/ is empty. Add initial memory there if needed.",
            declaration.path.display(),
            location.display(),
        ));
    }
    Ok(warnings)
}

// Do not read memory contents; they can contain private data.
fn has_agent_memory(context_root: &Path) -> Result<bool> {
    let memories = context_root.join(MEMORIES_DIR);
    let agent = memories.join("agent");
    for path in [context_root, memories.as_path(), agent.as_path()] {
        match fs::symlink_metadata(path) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => return Ok(false),
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error).context("could not inspect local agent memory"),
        }
    }
    for entry in WalkDir::new(&agent)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| !is_junk_dir(entry.path()) && !is_junk_file(entry.path()))
    {
        let entry = entry.context("could not inspect local agent memory")?;
        if entry.file_type().is_file()
            && entry
                .metadata()
                .context("could not inspect local agent memory")?
                .len()
                > 0
        {
            return Ok(true);
        }
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use super::*;
    use crate::compile::memory::{MemoryLayers, MemorySummary};
    use crate::project::Language;
    use crate::test_support::write_file;

    fn project(root: &Path, language: Language) -> Project {
        Project {
            root: root.to_path_buf(),
            language,
            agent_file: PathBuf::from(format!("agent.{}", language.extension())),
            files: Vec::new(),
        }
    }

    fn memory(language: Language, agent: bool) -> MemoryDecision {
        MemoryDecision::Declared(MemorySummary {
            path: PathBuf::from(format!("memory.{}", language.extension())),
            scope: MemoryLayers { agent, user: true },
        })
    }

    fn local(root: &Path) -> RuntimeContext<'_> {
        RuntimeContext::Local {
            context_root: root.to_str().unwrap(),
            has_skills: false,
        }
    }

    #[test]
    fn missing_instructions_warns_in_both_languages() {
        let root = tempfile::tempdir().unwrap();
        for language in [Language::TypeScript, Language::Python] {
            let warnings = project_warnings(
                &project(root.path(), language),
                &MemoryDecision::None,
                local(root.path()),
                false,
            )
            .unwrap();
            assert_eq!(warnings, ["instructions.md is missing. Add it next to the agent file to set the system prompt."]);
        }
    }

    #[test]
    fn dynamic_entries_do_not_require_a_static_prompt_in_both_languages() {
        for language in [Language::TypeScript, Language::Python] {
            let root = tempfile::tempdir().unwrap();
            assert!(project_warnings(
                &project(root.path(), language),
                &MemoryDecision::None,
                local(root.path()),
                true,
            )
            .unwrap()
            .is_empty());
        }
    }

    #[test]
    fn dynamic_entries_still_warn_about_empty_agent_memory() {
        let root = tempfile::tempdir().unwrap();
        for language in [Language::TypeScript, Language::Python] {
            let warnings = project_warnings(
                &project(root.path(), language),
                &memory(language, true),
                local(root.path()),
                true,
            )
            .unwrap();
            assert_eq!(warnings.len(), 1);
            assert!(warnings[0].contains("enables agent memory"));
        }
    }

    #[test]
    fn instructions_are_checked_next_to_the_agent() {
        let root = tempfile::tempdir().unwrap();
        let mut project = project(root.path(), Language::TypeScript);
        project.agent_file = PathBuf::from("src/agent.ts");
        write_file(root.path(), "instructions.md", "root instructions");
        assert_eq!(
            project_warnings(&project, &MemoryDecision::None, local(root.path()), false)
                .unwrap()
                .len(),
            1
        );
        write_file(root.path(), "src/instructions.md", "");
        assert!(
            project_warnings(&project, &MemoryDecision::None, local(root.path()), false)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn absent_or_user_only_memory_does_not_warn() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "instructions.md", "instructions");
        for memory in [MemoryDecision::None, memory(Language::Python, false)] {
            assert!(project_warnings(
                &project(root.path(), Language::Python),
                &memory,
                local(root.path()),
                false,
            )
            .unwrap()
            .is_empty());
        }
    }

    #[test]
    fn empty_agent_memory_warns_without_changing_files() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "instructions.md", "instructions");
        write_file(root.path(), "memories/agent/AGENTS.md", "");
        for language in [Language::TypeScript, Language::Python] {
            let warnings = project_warnings(
                &project(root.path(), language),
                &memory(language, true),
                local(root.path()),
                false,
            )
            .unwrap();
            assert_eq!(warnings.len(), 1);
            assert!(warnings[0].contains(&format!("memory.{}", language.extension())));
            assert!(warnings[0].contains("memories/agent/"));
        }
        assert_eq!(
            std::fs::metadata(root.path().join("memories/agent/AGENTS.md"))
                .unwrap()
                .len(),
            0
        );
    }

    #[test]
    fn nonempty_agent_memory_does_not_warn() {
        for file in ["memories/agent/AGENTS.md", "memories/agent/nested/notes.md"] {
            let root = tempfile::tempdir().unwrap();
            write_file(root.path(), "instructions.md", "instructions");
            write_file(root.path(), "memories/agent/AGENTS.md", "");
            write_file(root.path(), file, "fixture data");
            assert!(project_warnings(
                &project(root.path(), Language::Python),
                &memory(Language::Python, true),
                local(root.path()),
                false,
            )
            .unwrap()
            .is_empty());
        }
    }

    #[test]
    fn remote_and_eval_memory_do_not_warn() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "instructions.md", "instructions");
        for context in [
            RuntimeContext::ContextHub {
                repo: "test",
                has_skills: false,
            },
            RuntimeContext::Eval {
                system_prompt: None,
                model_override: None,
                has_skills: false,
            },
        ] {
            assert!(project_warnings(
                &project(root.path(), Language::Python),
                &memory(Language::Python, true),
                context,
                false,
            )
            .unwrap()
            .is_empty());
        }
    }

    #[test]
    fn junk_files_do_not_count_as_memory() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "instructions.md", "instructions");
        write_file(root.path(), "memories/agent/.DS_Store", "junk");
        write_file(root.path(), "memories/agent/__pycache__/memory.pyc", "junk");
        assert_eq!(
            project_warnings(
                &project(root.path(), Language::Python),
                &memory(Language::Python, true),
                local(root.path()),
                false,
            )
            .unwrap()
            .len(),
            1
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_do_not_count_as_memory() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "instructions.md", "instructions");
        write_file(root.path(), "outside/notes.md", "fixture data");
        write_file(root.path(), "memories/agent/AGENTS.md", "");
        std::os::unix::fs::symlink(
            root.path().join("outside"),
            root.path().join("memories/agent/linked"),
        )
        .unwrap();
        std::os::unix::fs::symlink(
            root.path().join("outside/notes.md"),
            root.path().join("memories/agent/notes.md"),
        )
        .unwrap();
        assert_eq!(
            project_warnings(
                &project(root.path(), Language::Python),
                &memory(Language::Python, true),
                local(root.path()),
                false,
            )
            .unwrap()
            .len(),
            1
        );
    }
}
