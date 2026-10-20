//! Project discovery helpers: locating the optional instructions, opt-in
//! sandbox, and connectors declarations, and building the import specifiers the
//! generated entry module uses to reach the author's declarations.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};

use crate::context_layout::{project_context_root, INSTRUCTIONS_FILE, SKILLS_DIR};
use crate::project::{Language, Project};

use super::{SANDBOX_DIR, SANDBOX_SETUP_FILE};

/// Read the `instructions.md` sitting next to the agent file, if present.
///
/// Eval entries still embed this prompt directly. Local/deploy prefer the
/// Context Hub mock / Hub repo (`instructions.md` synced under the hub root).
pub(super) fn read_instructions(project: &Project) -> Result<Option<String>> {
    let path = project_context_root(project).join(INSTRUCTIONS_FILE);
    if !path.is_file() {
        return Ok(None);
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    Ok(Some(contents))
}

/// Whether the project carries any `skills/` file into the build.
///
/// Eval trials have no Context Hub: the Harbor adapter copies the compiled
/// project into the trial workspace, so the runtime's `/skills/` mount reads the
/// project's own `skills/` tree. This tests the copied file list rather than the
/// disk so the answer matches what actually lands in the artifact — ignored
/// files (`.DS_Store`, editor junk) never count as a skill.
pub(super) fn project_has_skills(project: &Project) -> bool {
    let rel = match project.agent_file.parent() {
        Some(parent) => parent.join(SKILLS_DIR),
        None => PathBuf::from(SKILLS_DIR),
    };
    project.files.iter().any(|path| {
        path.strip_prefix(&rel)
            .is_ok_and(|nested| nested.components().next().is_some())
    })
}

/// Candidate `sandbox/` index modules for `language`, in priority order.
fn sandbox_entry_candidates(language: Language) -> &'static [&'static str] {
    match language {
        Language::TypeScript => &[
            "sandbox/index.ts",
            "sandbox/index.tsx",
            "sandbox/index.mts",
            "sandbox/index.cts",
        ],
        Language::Python => &["sandbox/__init__.py"],
    }
}

/// Locate the `sandbox/` index module that exports the named `sandbox`
/// declaration, if the project configures one.
pub(crate) fn find_sandbox_entry(project: &Project) -> Option<PathBuf> {
    sandbox_entry_candidates(project.language)
        .iter()
        .map(PathBuf::from)
        .find(|candidate| project.files.contains(candidate))
}

/// Module file extensions that can hold a connector or channel, per language.
fn plugin_module_extensions(language: Language) -> &'static [&'static str] {
    match language {
        Language::TypeScript => &["ts", "tsx", "mts", "cts"],
        Language::Python => &["py"],
    }
}

/// Whether `path` looks like a co-located unit-test module under a plugin dir.
///
/// These are never plugins even when they re-import a `connector` binding
/// (Python) or sit next to a real module (TypeScript `*.test.ts`).
fn is_co_located_test_module(path: &Path) -> bool {
    let Some(stem) = path.file_stem().and_then(|stem| stem.to_str()) else {
        return false;
    };
    let lower = stem.to_ascii_lowercase();
    lower.ends_with(".test")
        || lower.ends_with(".spec")
        || lower.starts_with("test_")
        || lower.ends_with("_test")
}

/// Whether `path` is a plugin module directly under the project's top-level
/// `directory`.
///
/// Discovery is name-agnostic for real connector filenames: any module directly
/// under the root `connectors/` with a supported extension is a candidate.
/// Python package initializers (`__init__.py`) and co-located unit-test modules
/// (`*.test.ts`, `test_*.py`, …) are excluded.
///
/// The parent must be exactly `connectors` (not merely *named* `connectors`), so
/// a nested `connectors/` directory anywhere else in the tree (for example a
/// vendored package under `.venv/.../site-packages/`) is never misdiscovered as
/// a project connector.
fn is_plugin_module(path: &Path, directory: &str, language: Language) -> bool {
    if path.parent() != Some(Path::new(directory)) {
        return false;
    }
    if language == Language::Python && path.file_name() == Some(std::ffi::OsStr::new("__init__.py"))
    {
        return false;
    }
    if is_co_located_test_module(path) {
        return false;
    }
    match path.extension().and_then(|ext| ext.to_str()) {
        Some(ext) => plugin_module_extensions(language).contains(&ext),
        None => false,
    }
}

/// Locate every connector *candidate* module directly under `connectors/`,
/// sorted by path for deterministic generated output.
///
/// Discovery is name-agnostic on filenames. Compile then keeps only modules
/// that expose the required named `connector` export; the
/// runtime collectors still check brand and hooks.
pub(super) fn find_connector_entries(project: &Project) -> Vec<PathBuf> {
    find_plugin_entries(project, "connectors")
}

/// Candidate MCP declaration modules under `tools/` for `language`, in priority order.
fn mcp_entry_candidates(language: Language) -> &'static [&'static str] {
    match language {
        Language::TypeScript => &[
            "tools/mcp.ts",
            "tools/mcp.tsx",
            "tools/mcp.mts",
            "tools/mcp.cts",
        ],
        Language::Python => &["tools/mcp.py"],
    }
}

/// Locate the canonical MCP declaration, if the project defines one.
pub(super) fn find_mcp_entry(project: &Project) -> Option<PathBuf> {
    mcp_entry_candidates(project.language)
        .iter()
        .map(PathBuf::from)
        .find(|candidate| project.files.contains(candidate))
}

/// Locate every channel *candidate* module directly under `channels/`, sorted
/// by path for deterministic generated output. Uses the same extension,
/// initializer, and co-located-test rules as connector discovery.
pub(super) fn find_channel_entries(project: &Project) -> Vec<PathBuf> {
    find_plugin_entries(project, "channels")
}

/// Plugin modules directly under the top-level `directory`, sorted by path.
fn find_plugin_entries(project: &Project, directory: &str) -> Vec<PathBuf> {
    let mut entries: Vec<PathBuf> = project
        .files
        .iter()
        .filter(|path| is_plugin_module(path, directory, project.language))
        .cloned()
        .collect();
    entries.sort();
    entries
}

fn has_files_under(project: &Project, directory: &str) -> bool {
    project.files.iter().any(|path| {
        path.components()
            .next()
            .is_some_and(|component| component.as_os_str() == directory)
    })
}

/// Advisory message for a project with a top-level `channel/` tree but no
/// `channels/`.
///
/// Channel deploys are discovered only from `channels/`, so a singular
/// `channel/` directory is silently ignored — most often a misspelling. Returned
/// rather than printed: `compile` runs inside a `cliclack` spinner under
/// `mda dev` / `mda deploy`, so writing to stderr here would race the spinner's
/// steady tick. Callers render it once their spinner has stopped.
pub(super) fn misspelled_channel_directory_warning(project: &Project) -> Option<&'static str> {
    if has_files_under(project, "channels") || !has_files_under(project, "channel") {
        return None;
    }
    Some("found `channel/`, but channel deploys are discovered from `channels/` — check the directory name")
}

/// Language source modules that are direct children of `dir` (e.g. `schedules/`).
/// Unlike connectors, Python `__init__.py` and co-located tests are included
/// when present — callers that need plugin filtering should use
/// [`find_connector_entries`] instead.
pub(super) fn language_dir_entries(project: &Project, dir: &str) -> Vec<PathBuf> {
    project
        .files
        .iter()
        .filter(|rel| is_language_dir_entry(rel, dir, project.language))
        .cloned()
        .collect()
}

fn is_language_dir_entry(rel: &Path, dir: &str, language: Language) -> bool {
    if rel.parent() != Some(Path::new(dir)) {
        return false;
    }
    rel.extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| plugin_module_extensions(language).contains(&ext))
}

/// File-stem name for a discovered schedule/connector entry.
pub(super) fn entry_stem_name(rel: &Path, kind: &str) -> Result<String> {
    rel.file_stem()
        .and_then(|stem| stem.to_str())
        .filter(|stem| !stem.is_empty())
        .map(str::to_string)
        .ok_or_else(|| anyhow!("invalid {kind} file name {}", rel.display()))
}

/// Candidate root `identity` modules for `language`, in priority order.
fn identity_entry_candidates(language: Language) -> &'static [&'static str] {
    match language {
        Language::TypeScript => &[
            "identity.ts",
            "identity.tsx",
            "identity.mts",
            "identity.cts",
        ],
        Language::Python => &["identity.py"],
    }
}

/// Locate the root `identity` module that exports the named `identity`
/// declaration, if the project opts into managed identity.
pub(in crate::compile) fn find_identity_entry(project: &Project) -> Option<PathBuf> {
    identity_entry_candidates(project.language)
        .iter()
        .map(PathBuf::from)
        .find(|candidate| project.files.contains(candidate))
}

/// Candidate root `memory` modules for `language`, in priority order.
///
/// Durable memory is opt-in: a project with none of these mounts nothing.
/// Package-dir forms (`memory/index.ts`, `memory/__init__.py`) are intentionally
/// unsupported, matching the identity entry candidates above.
fn memory_entry_candidates(language: Language) -> &'static [&'static str] {
    match language {
        Language::TypeScript => &["memory.ts", "memory.tsx", "memory.mts", "memory.cts"],
        Language::Python => &["memory.py"],
    }
}

/// Locate the root `memory` module that exports the named `memory` declaration,
/// if the project opts into durable memory.
pub(in crate::compile) fn find_memory_entry(project: &Project) -> Option<PathBuf> {
    memory_entry_candidates(project.language)
        .iter()
        .map(PathBuf::from)
        .find(|candidate| project.files.contains(candidate))
}

/// Locate the root `browser.py` that exports the named `browser` declaration,
/// if the project opts into browser use. Python only: the TypeScript SDK has
/// no browser tools.
pub(in crate::compile) fn find_browser_entry(project: &Project) -> Option<PathBuf> {
    let candidate = PathBuf::from("browser.py");
    (project.language == Language::Python && project.files.contains(&candidate))
        .then_some(candidate)
}

/// Read `sandbox/setup.sh`, if present.
pub(crate) fn read_setup_script(project: &Project) -> Result<Option<String>> {
    let rel = PathBuf::from(SANDBOX_DIR).join(SANDBOX_SETUP_FILE);
    if !project.files.contains(&rel) {
        return Ok(None);
    }
    let path = project.root.join(&rel);
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    Ok(Some(contents))
}

/// Build the import specifier the generated entry uses to reach the user's
/// `sandbox` declaration.
///
/// TypeScript imports are extensionless and module-relative
/// (`./sandbox/index`). Python imports use the dotted package path, collapsing
/// the `__init__` module to its package (`sandbox`).
pub(super) fn sandbox_import_specifier(sandbox_entry: &Path, language: Language) -> String {
    match language {
        Language::TypeScript => agent_import_specifier(sandbox_entry, language),
        Language::Python => {
            let stem_path = sandbox_entry.with_extension("");
            let parts: Vec<String> = stem_path
                .components()
                .filter_map(|component| component.as_os_str().to_str())
                .filter(|part| *part != "__init__")
                .map(|s| s.to_string())
                .collect();
            parts.join(".")
        }
    }
}

/// Build the import specifier the generated entry uses to reach the user's
/// agent definition.
///
/// TypeScript imports are extensionless and module-relative (`./agent`,
/// `./src/agent`). Python imports use the dotted module path rooted at the
/// build directory (`agent`, `src.agent`).
pub(super) fn agent_import_specifier(agent_file: &Path, language: Language) -> String {
    let stem_path = agent_file.with_extension("");
    let parts: Vec<String> = stem_path
        .components()
        .filter_map(|component| component.as_os_str().to_str())
        .map(|s| s.to_string())
        .collect();

    match language {
        Language::TypeScript => format!("./{}", parts.join("/")),
        Language::Python => parts.join("."),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn top_level_connector_modules_are_discovered() {
        assert!(is_plugin_module(
            Path::new("connectors/mcp.py"),
            "connectors",
            Language::Python
        ));
        assert!(is_plugin_module(
            Path::new("connectors/mcp.ts"),
            "connectors",
            Language::TypeScript
        ));
    }

    #[test]
    fn tools_mcp_module_is_discovered() {
        let project = project_with_files(vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("tools/mcp.ts"),
            PathBuf::from("tools/mcp.mts"),
        ]);

        assert_eq!(
            find_mcp_entry(&project),
            Some(PathBuf::from("tools/mcp.ts"))
        );
    }

    #[test]
    fn package_initializers_are_not_connectors() {
        assert!(!is_plugin_module(
            Path::new("connectors/__init__.py"),
            "connectors",
            Language::Python
        ));
    }

    #[test]
    fn nested_connectors_directories_are_not_discovered() {
        // A vendored package under a virtualenv also has a `connectors/` dir; it
        // must never be treated as one of the project's connectors.
        assert!(!is_plugin_module(
            Path::new(".venv/lib/python3.13/site-packages/managed_deepagents/connectors/mcp.py"),
            "connectors",
            Language::Python
        ));
        assert!(!is_plugin_module(
            Path::new("src/connectors/foo.py"),
            "connectors",
            Language::Python
        ));
    }

    #[test]
    fn non_module_extensions_are_ignored() {
        assert!(!is_plugin_module(
            Path::new("connectors/readme.md"),
            "connectors",
            Language::Python
        ));
    }

    #[test]
    fn co_located_test_modules_are_not_connectors() {
        assert!(!is_plugin_module(
            Path::new("connectors/mcp.test.ts"),
            "connectors",
            Language::TypeScript
        ));
        assert!(!is_plugin_module(
            Path::new("connectors/mcp.spec.ts"),
            "connectors",
            Language::TypeScript
        ));
        assert!(!is_plugin_module(
            Path::new("connectors/test_mcp.py"),
            "connectors",
            Language::Python
        ));
        assert!(!is_plugin_module(
            Path::new("connectors/mcp_test.py"),
            "connectors",
            Language::Python
        ));
        // Real plugins must still be discovered.
        assert!(is_plugin_module(
            Path::new("connectors/mcp.ts"),
            "connectors",
            Language::TypeScript
        ));
        assert!(is_plugin_module(
            Path::new("connectors/mcp.py"),
            "connectors",
            Language::Python
        ));
    }

    #[test]
    fn agent_import_specifier_is_module_relative_for_typescript() {
        assert_eq!(
            agent_import_specifier(&PathBuf::from("agent.ts"), Language::TypeScript),
            "./agent"
        );
        assert_eq!(
            agent_import_specifier(&PathBuf::from("src/agent.ts"), Language::TypeScript),
            "./src/agent"
        );
    }

    #[test]
    fn agent_import_specifier_is_dotted_for_python() {
        assert_eq!(
            agent_import_specifier(&PathBuf::from("agent.py"), Language::Python),
            "agent"
        );
        assert_eq!(
            agent_import_specifier(&PathBuf::from("src/agent.py"), Language::Python),
            "src.agent"
        );
    }

    #[test]
    fn sandbox_import_specifier_is_module_relative_for_typescript() {
        assert_eq!(
            sandbox_import_specifier(&PathBuf::from("sandbox/index.ts"), Language::TypeScript),
            "./sandbox/index"
        );
    }

    #[test]
    fn sandbox_import_specifier_collapses_python_package_init() {
        assert_eq!(
            sandbox_import_specifier(&PathBuf::from("sandbox/__init__.py"), Language::Python),
            "sandbox"
        );
    }

    #[test]
    fn plugin_entries_are_sorted_top_level_modules() {
        let project = project_with_files(vec![
            PathBuf::from("channels/slack.ts"),
            PathBuf::from("connectors/mcp.ts"),
            PathBuf::from("channels/discord.ts"),
            PathBuf::from("channels/discord.test.ts"),
            PathBuf::from("channels/nested/teams.ts"),
            PathBuf::from("connectors/readme.md"),
        ]);

        assert_eq!(
            find_channel_entries(&project),
            vec![
                PathBuf::from("channels/discord.ts"),
                PathBuf::from("channels/slack.ts")
            ]
        );
        assert_eq!(
            find_connector_entries(&project),
            vec![PathBuf::from("connectors/mcp.ts")]
        );
    }

    #[test]
    fn language_dir_entries_keep_only_language_modules() {
        let project = project_with_files(vec![
            PathBuf::from("schedules/a.ts"),
            PathBuf::from("schedules/b.tsx"),
            PathBuf::from("schedules/c.mts"),
            PathBuf::from("schedules/d.cts"),
            PathBuf::from("schedules/e.js"),
            PathBuf::from("schedules/notes.md"),
            PathBuf::from("schedules/nested/f.ts"),
        ]);

        assert_eq!(
            language_dir_entries(&project, "schedules"),
            vec![
                PathBuf::from("schedules/a.ts"),
                PathBuf::from("schedules/b.tsx"),
                PathBuf::from("schedules/c.mts"),
                PathBuf::from("schedules/d.cts"),
            ]
        );
    }

    #[test]
    fn instructions_are_read_next_to_a_nested_agent_file() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("src")).unwrap();
        fs::write(dir.path().join("src/instructions.md"), "be brief").unwrap();
        let project = Project {
            root: dir.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("src/agent.ts"),
            files: Vec::new(),
        };

        assert_eq!(
            read_instructions(&project).unwrap().as_deref(),
            Some("be brief")
        );
    }

    fn project_with_files(files: Vec<PathBuf>) -> Project {
        Project {
            root: PathBuf::from("/tmp"),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files,
        }
    }

    #[test]
    fn project_skills_are_detected_from_the_copied_file_list() {
        let project = project_with_files(vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("skills/research-brief/SKILL.md"),
        ]);

        assert!(project_has_skills(&project));
    }

    #[test]
    fn a_project_without_a_skills_directory_has_no_skills() {
        let project = project_with_files(vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("skills-notes.md"),
        ]);

        assert!(!project_has_skills(&project));
    }

    #[test]
    fn a_bare_skills_file_is_not_a_skills_directory() {
        // `skills` as a file would make the runtime mount a non-directory.
        let project = project_with_files(vec![PathBuf::from("agent.ts"), PathBuf::from("skills")]);

        assert!(!project_has_skills(&project));
    }

    #[test]
    fn misspelled_channel_directory_warning_when_channel_exists_without_channels() {
        let project = project_with_files(vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("channel/slack.ts"),
        ]);
        assert_eq!(
            misspelled_channel_directory_warning(&project),
            Some(
                "found `channel/`, but channel deploys are discovered from `channels/` — check the directory name"
            )
        );
    }

    #[test]
    fn misspelled_channel_directory_warning_is_absent_when_channels_exists() {
        let project = project_with_files(vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("channels/slack.ts"),
            PathBuf::from("channel/slack.ts"),
        ]);
        assert_eq!(misspelled_channel_directory_warning(&project), None);
    }

    #[test]
    fn misspelled_channel_directory_warning_is_absent_without_either_directory() {
        let project = project_with_files(vec![PathBuf::from("agent.ts")]);
        assert_eq!(misspelled_channel_directory_warning(&project), None);
    }
}
