//! Discovery of a Managed Deep Agent project on disk.
//!
//! This scans the project root, collects every file that should be carried into
//! the build, and locates the agent entry (`agent.ts`/`agent.tsx`/`agent.py`)
//! that exports the named `agent` defined via `defineDeepAgent` /
//! `define_deep_agent`.

use anyhow::{bail, Context, Result};
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use crate::dependencies::USER_RUNTIME_PACKAGE;
use crate::error::{categorized, ErrorCategory};

/// Tooling / OS cache directories that are never meaningful source or skill content.
const JUNK_DIRS: &[&str] = &["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"];

/// OS junk files that are often binary (and abort UTF-8 Context Hub sync).
const JUNK_FILES: &[&str] = &[".DS_Store", "Thumbs.db", "desktop.ini"];

/// Directories never carried into the compiled output.
///
/// Includes project-layout names (`build`, `dist`, legacy `mda_evals`, `evals`, …) that must not be
/// applied when walking `skills/**` — use [`is_junk_dir`] there instead.
const IGNORED_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    ".mda",
    "mda_evals",
    "evals",
    ".deepagents",
    ".agents",
    ".claude",
    ".cursor",
    ".factory",
    ".gemini",
    ".opencode",
    "memories",
    "dist",
    "build",
    // Python virtual environments: platform-specific and reinstalled from the
    // generated dependency manifest on deploy. Carrying them bloats the build and
    // lets vendored packages (e.g. a nested `connectors/`) get misdiscovered.
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
];

/// Files never carried into the compiled output.
///
/// Other lockfiles would override the installer and the lock generated for
/// the rewritten `package.json`.
const IGNORED_FILES: &[&str] = &[
    ".env",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "npm-shrinkwrap.json",
];

/// Candidate agent entry files paired with their language, in priority order.
const AGENT_ENTRYPOINTS: &[(&str, Language)] = &[
    ("agent.ts", Language::TypeScript),
    ("agent.tsx", Language::TypeScript),
    ("agent.py", Language::Python),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Language {
    TypeScript,
    Python,
}

impl Language {
    /// Human-readable label for CLI messaging.
    pub fn label(self) -> &'static str {
        match self {
            Language::TypeScript => "TypeScript",
            Language::Python => "Python",
        }
    }

    /// Source file extension (no dot) for authored modules.
    pub(crate) fn extension(self) -> &'static str {
        match self {
            Language::TypeScript => "ts",
            Language::Python => "py",
        }
    }

    /// Package manifest file name for projects in this language.
    pub(crate) fn manifest_name(self) -> &'static str {
        match self {
            Language::TypeScript => "package.json",
            Language::Python => "pyproject.toml",
        }
    }

    /// The spelling an author uses for an API name in this language.
    ///
    /// CLI messages quote authored code, so a Python project must never be told
    /// to edit `defineDeepAgent` (and a TypeScript one never `define_deep_agent`).
    pub(crate) fn ident(self, typescript: &'static str, python: &'static str) -> &'static str {
        match self {
            Language::TypeScript => typescript,
            Language::Python => python,
        }
    }

    /// A string argument written the way this language passes it
    /// (`name: "research-assistant"` / `name="research-assistant"`).
    pub(crate) fn string_argument(self, key: &str, value: &str) -> String {
        match self {
            Language::TypeScript => format!("{key}: \"{value}\""),
            Language::Python => format!("{key}=\"{value}\""),
        }
    }
}

/// A loaded Managed Deep Agent project.
#[derive(Debug)]
pub struct Project {
    /// Absolute path to the project root.
    pub root: PathBuf,
    /// Detected project language.
    pub language: Language,
    /// Agent entry file, relative to `root`.
    pub agent_file: PathBuf,
    /// All files to carry into the build, relative to `root`.
    pub files: Vec<PathBuf>,
}

/// Load and validate the project rooted at `root`.
pub fn load(root: &Path) -> Result<Project> {
    ensure_project_dir(root)?;
    let root = root.canonicalize().map_err(|err| {
        categorized(
            ErrorCategory::Project,
            format!("failed to resolve project root {}: {err}", root.display()),
        )
    })?;

    let mut files = Vec::new();
    for entry in WalkDir::new(&root)
        .into_iter()
        .filter_entry(|entry| !is_ignored_dir(entry.path()))
    {
        let entry = entry?;
        if entry.file_type().is_file() {
            let rel = entry.path().strip_prefix(&root)?.to_path_buf();
            if is_ignored_file(&rel) {
                continue;
            }
            files.push(rel);
        }
    }
    files.sort();

    let has_typescript_entry = AGENT_ENTRYPOINTS
        .iter()
        .filter(|(_, language)| *language == Language::TypeScript)
        .any(|(name, _)| files.contains(&PathBuf::from(name)));
    let has_python_entry = files.contains(&PathBuf::from("agent.py"));
    if has_typescript_entry && has_python_entry {
        return Err(categorized(
            ErrorCategory::Project,
            format!(
                "ambiguous project in {}: found both TypeScript and Python agent entry files",
                root.display()
            ),
        ));
    }

    let (agent_file, language) = AGENT_ENTRYPOINTS
        .iter()
        .map(|(name, lang)| (PathBuf::from(name), *lang))
        .find(|(candidate, _)| files.contains(candidate))
        .ok_or_else(|| not_a_project(&root))?;

    Ok(Project {
        root,
        language,
        agent_file,
        files,
    })
}

/// Reject `path` unless it is a directory holding an agent entry file.
///
/// Every project command runs this before any other work, so a wrong working
/// directory gets the same message, with the likeliest fix, from all of them.
pub(crate) fn ensure_project_dir(path: &Path) -> Result<()> {
    if !path.is_dir() {
        return Err(categorized(
            ErrorCategory::Project,
            format!("project root {} is not a directory", path.display()),
        ));
    }
    if !has_agent_entry(path) {
        return Err(not_a_project(path));
    }
    Ok(())
}

/// Whether `dir` directly holds an agent entry file.
fn has_agent_entry(dir: &Path) -> bool {
    AGENT_ENTRYPOINTS
        .iter()
        .any(|(name, _)| dir.join(name).is_file())
}

/// The error for a directory without an agent entry.
fn not_a_project(dir: &Path) -> anyhow::Error {
    let dir = dir.canonicalize().unwrap_or_else(|_| dir.to_path_buf());
    let cwd = std::env::current_dir()
        .and_then(|cwd| cwd.canonicalize())
        .ok();
    categorized(
        ErrorCategory::Project,
        not_a_project_message(&dir, cwd.as_deref()),
    )
}

/// Explain that `dir` is not a project and name the likeliest way to reach one.
///
/// `cwd` decides how the suggested `cd` is spelled: relative when `dir` is the
/// working directory, absolute otherwise.
fn not_a_project_message(dir: &Path, cwd: Option<&Path>) -> String {
    let expected = match AGENT_ENTRYPOINTS {
        [init @ .., (last, _)] => {
            let init = init.iter().map(|(name, _)| *name).collect::<Vec<_>>();
            format!("{}, or {last}", init.join(", "))
        }
        [] => String::new(),
    };
    let in_cwd = cwd == Some(dir);
    let hint = match nearby_project(dir) {
        Some(NearbyProject::Child(child)) => {
            let target = if in_cwd {
                child.file_name().map(PathBuf::from).unwrap_or(child)
            } else {
                child
            };
            format!(
                "Found a project in {}. Run `cd {}` first.",
                target.display(),
                shell_word(&target)
            )
        }
        Some(NearbyProject::Ancestor { root, depth }) => {
            let target = if in_cwd {
                std::iter::repeat_n("..", depth).collect::<PathBuf>()
            } else {
                root.clone()
            };
            format!(
                "This directory is inside the project at {}. Run `cd {}` first.",
                root.display(),
                shell_word(&target)
            )
        }
        None => {
            "Run `mda init <name>` to create one, or `cd` into an existing project.".to_string()
        }
    };
    format!(
        "{} is not a Managed Deep Agent project (no {expected} found).\n{hint}",
        dir.display()
    )
}

/// A project next to the directory a command was pointed at.
#[derive(Debug, PartialEq, Eq)]
enum NearbyProject {
    /// The only immediate subdirectory holding an agent entry: the usual
    /// result of forgetting to `cd` after `mda init`.
    Child(PathBuf),
    /// The closest enclosing project, `depth` levels up (for example when
    /// working in its `tools/` directory).
    Ancestor { root: PathBuf, depth: usize },
}

fn nearby_project(dir: &Path) -> Option<NearbyProject> {
    let mut children = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| {
            path.is_dir()
                && !is_ignored_dir(path)
                && !path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with('.'))
                && has_agent_entry(path)
        });
    if let (Some(child), None) = (children.next(), children.next()) {
        return Some(NearbyProject::Child(child));
    }
    dir.ancestors()
        .enumerate()
        .skip(1)
        .find(|(_, ancestor)| has_agent_entry(ancestor))
        .map(|(depth, root)| NearbyProject::Ancestor {
            root: root.to_path_buf(),
            depth,
        })
}

/// `path` as a single shell word, quoted only when it needs to be.
fn shell_word(path: &Path) -> String {
    let text = path.to_string_lossy();
    if text
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '.' | '_' | '-'))
    {
        text.into_owned()
    } else if cfg!(windows) {
        format!("'{}'", text.replace('\'', "''"))
    } else {
        format!("'{}'", text.replace('\'', "'\\''"))
    }
}

/// Reject an exact Authoring Package version that differs from the bundled runtime.
///
/// Ranges and non-registry sources are intentionally left to their package
/// managers; this check only handles the exact pins generated by `mda init`.
pub fn validate_authoring_package_version(project: &Project, expected: &str) -> Result<()> {
    let declared = match project.language {
        Language::TypeScript => declared_typescript_authoring_version(&project.root)?,
        Language::Python => declared_python_authoring_version(&project.root)?,
    };
    let Some(declared) = declared else {
        return Ok(());
    };
    if declared == expected {
        return Ok(());
    }

    let (uninstall, run) = match project.language {
        Language::TypeScript => ("npm uninstall -g managed-deepagents", "npx mda <command>"),
        Language::Python => (
            "uv tool uninstall managed-deepagents",
            "uv run mda <command>",
        ),
    };

    bail!(
        "managed-deepagents version mismatch: project declares {declared}, but this mda CLI \
         bundles {expected}.\n\
         \n\
         A globally installed `mda` is likely shadowing the project CLI. Uninstall the global \
         CLI and run the project-local one instead:\n\
         \n\
           {uninstall}\n\
           {run}"
    )
}

fn declared_typescript_authoring_version(root: &Path) -> Result<Option<String>> {
    let path = root.join("package.json");
    if !path.is_file() {
        return Ok(None);
    }
    let contents = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let manifest: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse {}", path.display()))?;

    for section in ["dependencies", "devDependencies"] {
        let Some(specifier) = manifest
            .get(section)
            .and_then(|dependencies| dependencies.get(USER_RUNTIME_PACKAGE))
            .and_then(|value| value.as_str())
        else {
            continue;
        };
        return Ok(exact_registry_version(specifier).map(str::to_string));
    }
    Ok(None)
}

fn declared_python_authoring_version(root: &Path) -> Result<Option<String>> {
    let path = root.join("pyproject.toml");
    if !path.is_file() {
        return Ok(None);
    }
    let contents = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let manifest = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let Some(dependencies) = manifest
        .get("project")
        .and_then(|project| project.get("dependencies"))
        .and_then(|dependencies| dependencies.as_array())
    else {
        return Ok(None);
    };

    for dependency in dependencies.iter().filter_map(|item| item.as_str()) {
        let Some((name, version)) = dependency.split_once("==") else {
            continue;
        };
        if canonical_package_name(name.trim()) != USER_RUNTIME_PACKAGE {
            continue;
        }
        return Ok(exact_registry_version(version).map(str::to_string));
    }
    Ok(None)
}

fn exact_registry_version(specifier: &str) -> Option<&str> {
    let version = specifier.trim();
    (!version.is_empty()
        && version.starts_with(|character: char| character.is_ascii_digit())
        && version
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || ".+-".contains(character)))
    .then_some(version)
}

fn canonical_package_name(name: &str) -> String {
    name.to_ascii_lowercase().replace(['_', '.'], "-")
}

/// Whether `path`'s final component is a directory that should be skipped when
/// scanning a project for compile/deploy inputs.
pub(crate) fn is_ignored_dir(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| IGNORED_DIRS.contains(&name))
        .unwrap_or(false)
}

/// Whether `path`'s final component is a file that should be skipped when
/// scanning a project for compile/deploy inputs.
pub(crate) fn is_ignored_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| {
            IGNORED_FILES.contains(&name)
                || name.starts_with(".env.")
                || is_bytecode_file_name(name)
        })
        .unwrap_or(false)
}

/// Whether `path`'s final component is a tooling/OS cache directory.
///
/// Safe to apply under `skills/**`, unlike [`is_ignored_dir`], which also skips
/// ordinary names such as `build` / `dist` / `evals` / `memories`.
pub(crate) fn is_junk_dir(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| JUNK_DIRS.contains(&name))
        .unwrap_or(false)
}

/// Whether `path`'s final component is OS junk or Python bytecode.
///
/// Safe to apply under `skills/**` (does not treat project secrets/layout rules
/// as skill denylist entries beyond binary OS junk).
pub(crate) fn is_junk_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| JUNK_FILES.contains(&name) || is_bytecode_file_name(name))
        .unwrap_or(false)
}

fn is_bytecode_file_name(name: &str) -> bool {
    name.ends_with(".pyc") || name.ends_with(".pyo")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::write_stub_file;
    use std::fs;

    #[test]
    fn is_ignored_dir_matches_known_directories() {
        assert!(is_ignored_dir(Path::new("/proj/node_modules")));
        assert!(is_ignored_dir(Path::new("/proj/.git")));
        assert!(is_ignored_dir(Path::new("/proj/dist")));
        assert!(is_ignored_dir(Path::new("/proj/.venv")));
        assert!(is_ignored_dir(Path::new("/proj/venv")));
        assert!(is_ignored_dir(Path::new("/proj/__pycache__")));
        assert!(is_ignored_dir(Path::new("/proj/.pytest_cache")));
        for harness_dir in [
            ".agents",
            ".claude",
            ".cursor",
            ".factory",
            ".gemini",
            ".opencode",
        ] {
            assert!(is_ignored_dir(
                Path::new("/proj").join(harness_dir).as_path()
            ));
        }
        assert!(!is_ignored_dir(Path::new("/proj/src")));
        assert!(!is_ignored_dir(Path::new("/proj/tools")));
    }

    #[test]
    fn is_ignored_file_matches_secret_env_and_os_junk() {
        assert!(is_ignored_file(Path::new("/proj/.env")));
        assert!(is_ignored_file(Path::new("/proj/.env.local")));
        assert!(is_ignored_file(Path::new("/proj/.env.production")));
        assert!(is_ignored_file(Path::new("/proj/.DS_Store")));
        assert!(is_ignored_file(Path::new("/proj/skills/foo/.DS_Store")));
        assert!(is_ignored_file(Path::new("/proj/Thumbs.db")));
        assert!(is_ignored_file(Path::new("/proj/desktop.ini")));
        assert!(is_ignored_file(Path::new("/proj/__pycache__/mod.pyc")));
        assert!(!is_ignored_file(Path::new("/proj/env.example")));
        assert!(!is_ignored_file(Path::new("/proj/package.json")));
    }

    #[test]
    fn is_junk_path_skips_os_caches_but_not_skill_layout_names() {
        assert!(is_junk_dir(Path::new("/proj/skills/foo/__pycache__")));
        assert!(is_junk_dir(Path::new("/proj/skills/foo/.pytest_cache")));
        assert!(!is_junk_dir(Path::new("/proj/skills/release/build")));
        assert!(!is_junk_dir(Path::new("/proj/skills/release/dist")));
        assert!(!is_junk_dir(Path::new("/proj/skills/release/evals")));
        assert!(!is_junk_dir(Path::new("/proj/skills/release/memories")));

        assert!(is_junk_file(Path::new("/proj/skills/foo/.DS_Store")));
        assert!(is_junk_file(Path::new("/proj/skills/foo/mod.pyc")));
        assert!(!is_junk_file(Path::new("/proj/skills/foo/.env")));
        assert!(!is_junk_file(Path::new(
            "/proj/skills/release/build/steps.md"
        )));
    }

    #[test]
    fn load_discovers_agent_entry_and_files() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "tools/query-db.ts");
        write_stub_file(root.path(), ".env");
        write_stub_file(root.path(), ".env.local");
        write_stub_file(root.path(), ".DS_Store");
        write_stub_file(root.path(), "Thumbs.db");

        let project = load(root.path()).unwrap();

        assert_eq!(project.language, Language::TypeScript);
        assert_eq!(project.agent_file, PathBuf::from("agent.ts"));
        assert!(project.files.contains(&PathBuf::from("agent.ts")));
        assert!(project.files.contains(&PathBuf::from("tools/query-db.ts")));
        assert!(!project.files.contains(&PathBuf::from(".env")));
        assert!(!project.files.contains(&PathBuf::from(".env.local")));
        assert!(!project.files.contains(&PathBuf::from(".DS_Store")));
        assert!(!project.files.contains(&PathBuf::from("Thumbs.db")));
    }

    #[test]
    fn load_skips_other_package_manager_lockfiles() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "package-lock.json");
        let lockfiles = [
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
            "npm-shrinkwrap.json",
        ];
        for name in lockfiles {
            write_stub_file(root.path(), name);
        }

        let project = load(root.path()).unwrap();

        assert!(project.files.contains(&PathBuf::from("package-lock.json")));
        let kept: Vec<_> = lockfiles
            .into_iter()
            .filter(|name| project.files.contains(&PathBuf::from(name)))
            .collect();
        assert!(kept.is_empty(), "discovered foreign lockfiles: {kept:?}");
    }

    #[test]
    fn load_returns_files_sorted() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "z.ts");
        write_stub_file(root.path(), "a.ts");

        let project = load(root.path()).unwrap();

        let mut sorted = project.files.clone();
        sorted.sort();
        assert_eq!(project.files, sorted);
    }

    #[test]
    fn load_skips_ignored_directories() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "node_modules/dep/index.ts");
        write_stub_file(root.path(), "dist/old.ts");
        write_stub_file(root.path(), "mda_evals/smoke/task/tests/answer.test.ts");
        write_stub_file(root.path(), "evals/smoke/task/tests/answer.test.ts");
        write_stub_file(root.path(), "memories/AGENTS.md");

        let project = load(root.path()).unwrap();

        assert!(!project.files.iter().any(|f| f.starts_with("node_modules")));
        assert!(!project.files.iter().any(|f| f.starts_with("dist")));
        assert!(!project.files.iter().any(|f| f.starts_with("mda_evals")));
        assert!(!project.files.iter().any(|f| f.starts_with("evals")));
        assert!(!project.files.iter().any(|f| f.starts_with("memories")));
    }

    #[test]
    fn load_detects_python_projects() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.py");
        write_stub_file(root.path(), "tools/query_db.py");

        let project = load(root.path()).unwrap();

        assert_eq!(project.language, Language::Python);
        assert_eq!(project.agent_file, PathBuf::from("agent.py"));
    }

    #[test]
    fn load_prefers_agent_ts_over_agent_tsx() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "agent.tsx");

        let project = load(root.path()).unwrap();

        assert_eq!(project.agent_file, PathBuf::from("agent.ts"));
    }

    #[test]
    fn load_rejects_entries_for_both_languages() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        write_stub_file(root.path(), "agent.py");

        let err = load(root.path()).unwrap_err();

        assert!(err.to_string().contains("ambiguous project"));
    }

    #[test]
    fn validate_authoring_package_version_rejects_typescript_mismatch() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        fs::write(
            root.path().join("package.json"),
            r#"{"dependencies":{"managed-deepagents":"0.3.0"}}"#,
        )
        .unwrap();
        let project = load(root.path()).unwrap();

        let err = validate_authoring_package_version(&project, "0.4.0")
            .unwrap_err()
            .to_string();

        assert!(err.contains("project declares 0.3.0"));
        assert!(err.contains("bundles 0.4.0"));
        assert!(err.contains("npm uninstall -g managed-deepagents"));
        assert!(err.contains("npx mda <command>"));
    }

    #[test]
    fn validate_authoring_package_version_rejects_python_mismatch() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.py");
        fs::write(
            root.path().join("pyproject.toml"),
            "[project]\ndependencies = [\"managed-deepagents==0.3.0\"]\n",
        )
        .unwrap();
        let project = load(root.path()).unwrap();

        let err = validate_authoring_package_version(&project, "0.4.0")
            .unwrap_err()
            .to_string();

        assert!(err.contains("project declares 0.3.0"));
        assert!(err.contains("bundles 0.4.0"));
        assert!(err.contains("uv tool uninstall managed-deepagents"));
        assert!(err.contains("uv run mda <command>"));
    }

    #[test]
    fn validate_authoring_package_version_ignores_ranges() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "agent.ts");
        fs::write(
            root.path().join("package.json"),
            r#"{"dependencies":{"managed-deepagents":"^0.3.0"}}"#,
        )
        .unwrap();
        let project = load(root.path()).unwrap();

        assert!(validate_authoring_package_version(&project, "0.4.0").is_ok());
    }

    #[test]
    fn load_errors_when_no_agent_entry_exists() {
        let root = tempfile::tempdir().unwrap();
        write_stub_file(root.path(), "index.ts");

        let err = load(root.path()).unwrap_err();
        assert!(
            err.to_string().contains(
                "is not a Managed Deep Agent project (no agent.ts, agent.tsx, or agent.py found)"
            ),
            "unexpected error: {err}"
        );
    }

    /// A canonical temp root, so expected paths match what the message prints.
    fn canonical_tempdir() -> (tempfile::TempDir, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().canonicalize().unwrap();
        (root, path)
    }

    #[test]
    fn missing_project_suggests_cd_into_the_only_child_project() {
        let (_root, dir) = canonical_tempdir();
        write_stub_file(&dir, "my-agent/agent.ts");
        write_stub_file(&dir, "notes/readme.md");

        let message = not_a_project_message(&dir, Some(&dir));

        assert!(
            message.ends_with("\nFound a project in my-agent. Run `cd my-agent` first."),
            "{message}"
        );
    }

    #[test]
    fn missing_project_spells_the_child_absolutely_outside_the_working_directory() {
        let (_root, dir) = canonical_tempdir();
        write_stub_file(&dir, "my-agent/agent.py");

        let message = not_a_project_message(&dir, None);

        let child = dir.join("my-agent");
        assert!(
            message.contains(&format!("Run `cd {}` first.", shell_word(&child))),
            "{message}"
        );
    }

    #[test]
    fn missing_project_does_not_guess_between_several_child_projects() {
        let (_root, dir) = canonical_tempdir();
        write_stub_file(&dir, "first/agent.ts");
        write_stub_file(&dir, "second/agent.py");

        let message = not_a_project_message(&dir, Some(&dir));

        assert!(
            message.ends_with(
                "\nRun `mda init <name>` to create one, or `cd` into an existing project."
            ),
            "{message}"
        );
    }

    #[test]
    fn missing_project_ignores_entries_under_dependency_and_hidden_dirs() {
        let (_root, dir) = canonical_tempdir();
        write_stub_file(&dir, "node_modules/pkg/agent.ts");
        write_stub_file(&dir, ".cache/agent.py");

        assert_eq!(nearby_project(&dir), None);
    }

    #[test]
    fn missing_project_points_back_up_to_the_enclosing_project() {
        let (_root, root) = canonical_tempdir();
        write_stub_file(&root, "agent.ts");
        write_stub_file(&root, "tools/search/index.ts");
        let dir = root.join("tools").join("search");

        let message = not_a_project_message(&dir, Some(&dir));

        assert!(
            message.ends_with(&format!(
                "\nThis directory is inside the project at {}. Run `cd ../..` first.",
                root.display()
            )),
            "{message}"
        );
    }

    #[test]
    fn deploy_and_load_report_a_missing_project_identically() {
        let (_root, dir) = canonical_tempdir();
        write_stub_file(&dir, "my-agent/agent.ts");

        let from_load = load(&dir).unwrap_err();
        let from_deploy = crate::deploy::ensure_project(&dir).unwrap_err();

        assert_eq!(format!("{from_load:#}"), format!("{from_deploy:#}"));
    }

    #[test]
    fn load_errors_when_root_is_not_a_directory() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("not-a-dir.ts");
        fs::write(&file, "// stub\n").unwrap();

        let err = load(&file).unwrap_err();
        assert!(
            err.to_string().contains("is not a directory"),
            "unexpected error: {err}"
        );
    }
}
