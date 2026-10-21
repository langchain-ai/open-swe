//! Project-root file classification and debounced watching for `mda dev`.
//!
//! LangGraph only watches `.mda/dev`. This module watches the authoring tree,
//! classifies each change, and tells `dev` whether to recompile, sync the local
//! Context Hub mock, restage env, or ignore the event.

use std::collections::HashSet;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use walkdir::WalkDir;

use crate::compile;
use crate::context_layout::{INSTRUCTIONS_FILE, SKILLS_DIR};
use crate::project::{self, Language};

/// Debounce window for coalescing editor save storms.
pub const WATCH_DEBOUNCE: Duration = Duration::from_millis(200);

/// Action to take for one or more project-root file changes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum WatchAction {
    /// Nothing relevant changed.
    Ignore,
    /// Restage `.env` / `.env.local` into `.mda/dev`.
    EnvRestage,
    /// Sync `skills/**` into the local Context Hub mock (no worker reload).
    HubSync,
    /// Sync hub content that the running worker must re-read (`instructions.md`).
    HubSyncReload,
    /// Incremental compile into `.mda/dev` (LangGraph reload picks it up).
    Recompile,
}

impl WatchAction {
    /// Prefer the strongest action when several paths change in one burst.
    pub fn coalesce(self, other: Self) -> Self {
        self.max(other)
    }
}

/// Paths that map to hub sync rather than a graph rebuild.
#[derive(Debug, Clone)]
pub struct WatchClassifyContext {
    /// Deploy-owned instructions path relative to the project root
    /// (usually `instructions.md`, or next to a nested agent entry).
    pub instructions_rel: PathBuf,
    /// Deploy-owned skills directory relative to the project root.
    pub skills_rel: PathBuf,
}

impl WatchClassifyContext {
    /// Build classify context from the agent entry path.
    pub fn from_agent_file(agent_file: &Path) -> Self {
        let parent = agent_file.parent().unwrap_or_else(|| Path::new(""));
        let instructions_rel = if parent.as_os_str().is_empty() {
            PathBuf::from(INSTRUCTIONS_FILE)
        } else {
            parent.join(INSTRUCTIONS_FILE)
        };
        let skills_rel = if parent.as_os_str().is_empty() {
            PathBuf::from(SKILLS_DIR)
        } else {
            parent.join(SKILLS_DIR)
        };
        Self {
            instructions_rel,
            skills_rel,
        }
    }
}

/// Classify a path relative to the project root.
pub fn classify_watch_path(rel: &Path, ctx: &WatchClassifyContext) -> WatchAction {
    if rel.is_absolute() {
        return WatchAction::Recompile;
    }
    // Skills must be classified before the project denylist: names like
    // `build` / `dist` / `evals` / `memories` are valid under `skills/**`
    // (same rule as `sync_skills` / Context Hub collection).
    if is_skills_watch_path(rel, ctx) {
        if should_ignore_skill_watch_path(rel) {
            return WatchAction::Ignore;
        }
        return WatchAction::HubSync;
    }
    if should_ignore_watch_path(rel) {
        return WatchAction::Ignore;
    }
    if is_env_watch_path(rel) {
        return WatchAction::EnvRestage;
    }
    if rel == ctx.instructions_rel {
        return WatchAction::HubSyncReload;
    }
    WatchAction::Recompile
}

/// Reduce a batch of relative paths to a single action.
pub fn classify_watch_paths<I, P>(paths: I, ctx: &WatchClassifyContext) -> WatchAction
where
    I: IntoIterator<Item = P>,
    P: AsRef<Path>,
{
    paths
        .into_iter()
        .map(|path| classify_watch_path(path.as_ref(), ctx))
        .fold(WatchAction::Ignore, WatchAction::coalesce)
}

fn is_skills_watch_path(rel: &Path, ctx: &WatchClassifyContext) -> bool {
    rel == ctx.skills_rel || rel.starts_with(&ctx.skills_rel)
}

fn should_ignore_watch_path(rel: &Path) -> bool {
    if rel.as_os_str().is_empty() {
        return true;
    }
    for component in rel.components() {
        let Component::Normal(name) = component else {
            continue;
        };
        if project::is_ignored_dir(Path::new(name)) {
            return true;
        }
    }
    // `.env` / `.env.local` are ignored by project discovery but watched here.
    if is_env_watch_path(rel) {
        return false;
    }
    if project::is_ignored_file(rel) || project::is_junk_file(rel) {
        return true;
    }
    false
}

/// Junk-only filter for paths under `skills/**` (not the project denylist).
fn should_ignore_skill_watch_path(rel: &Path) -> bool {
    if rel.as_os_str().is_empty() {
        return true;
    }
    for component in rel.components() {
        let Component::Normal(name) = component else {
            continue;
        };
        if project::is_junk_dir(Path::new(name)) {
            return true;
        }
    }
    project::is_junk_file(rel)
}

/// Whether any path in the batch is a staged env file.
pub fn batch_includes_env_path<I, P>(paths: I) -> bool
where
    I: IntoIterator<Item = P>,
    P: AsRef<Path>,
{
    paths
        .into_iter()
        .any(|path| is_env_watch_path(path.as_ref()))
}

fn is_env_watch_path(rel: &Path) -> bool {
    matches!(rel.to_str(), Some(".env") | Some(".env.local"))
}

/// Fingerprint of deploy-owned hub inputs (instructions + skills tree).
///
/// Used to skip no-op hub syncs when the watcher reports spurious modifies
/// (or atime-like noise that still arrives as `Modify` on some platforms).
pub fn hub_inputs_fingerprint(project_root: &Path, ctx: &WatchClassifyContext) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let mut hasher = DefaultHasher::new();
    let instructions = project_root.join(&ctx.instructions_rel);
    hash_file_if_present(&mut hasher, &instructions);
    let skills = project_root.join(&ctx.skills_rel);
    if skills.is_dir() {
        let mut files = Vec::new();
        for entry in WalkDir::new(&skills)
            .into_iter()
            .filter_entry(|entry| !project::is_junk_dir(entry.path()))
            .filter_map(Result::ok)
        {
            if !entry.file_type().is_file() || project::is_junk_file(entry.path()) {
                continue;
            }
            if let Ok(rel) = entry.path().strip_prefix(&skills) {
                files.push(rel.to_path_buf());
            }
        }
        files.sort();
        for rel in files {
            rel.hash(&mut hasher);
            hash_file_if_present(&mut hasher, &skills.join(&rel));
        }
    }
    format!("{:x}", hasher.finish())
}

fn hash_file_if_present(hasher: &mut impl std::hash::Hasher, path: &Path) {
    use std::hash::Hash;
    path.hash(hasher);
    if let Ok(bytes) = fs::read(path) {
        bytes.hash(hasher);
    } else {
        0u8.hash(hasher);
    }
}

/// Touch the managed entry so LangGraph workers reload after hub-only updates.
pub fn poke_reload_signal(out_dir: &Path, language: Language) -> Result<()> {
    crate::compile::poke_dev_reload_signal(out_dir, language)
}

/// Relativize an absolute watch event path against the project root.
pub fn relativize_watch_path(project_root: &Path, path: &Path) -> Option<PathBuf> {
    let abs = if path.is_absolute() {
        path.to_path_buf()
    } else {
        project_root.join(path)
    };
    if let Some(rel) = pathdiff_from(project_root, &abs) {
        return Some(rel);
    }
    // notify may yield a non-canonical absolute path while `project::load`
    // canonicalizes the root — retry with both sides resolved.
    let root = fs::canonicalize(project_root).ok()?;
    let abs = fs::canonicalize(&abs).ok()?;
    abs.strip_prefix(root).ok().map(Path::to_path_buf)
}

/// Keep a change inside a local package root as an absolute path, so
/// [`classify_watch_path`] can tell it apart from project-relative paths.
fn local_package_watch_path(roots: &[PathBuf], path: &Path) -> Option<PathBuf> {
    roots.iter().find_map(|root| {
        let rel = path.strip_prefix(root).ok()?;
        compile::is_vendored_path(rel).then(|| path.to_path_buf())
    })
}

fn pathdiff_from(root: &Path, path: &Path) -> Option<PathBuf> {
    let root = dunce_simplify(root);
    let path = dunce_simplify(path);
    path.strip_prefix(&root).ok().map(Path::to_path_buf)
}

/// Best-effort normalize without requiring the path to exist.
fn dunce_simplify(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn is_content_change(kind: &EventKind) -> bool {
    // Ignore access/atime-only noise — reading skills during hub sync would
    // otherwise re-trigger HubSync forever on some platforms.
    !matches!(kind, EventKind::Access(_))
}

/// Debounced recursive watcher over `project_root`.
pub struct ProjectWatcher {
    _watcher: RecommendedWatcher,
    rx: Receiver<Vec<PathBuf>>,
}

impl ProjectWatcher {
    /// Start watching `project_root` and each local package root recursively.
    pub fn start(project_root: &Path, local_package_roots: &[PathBuf]) -> Result<Self> {
        let (raw_tx, raw_rx) = mpsc::channel::<Event>();
        let mut watcher = notify::recommended_watcher(move |result| {
            if let Ok(event) = result {
                let _ = raw_tx.send(event);
            }
        })
        .context("failed to start project file watcher")?;
        watcher
            .watch(project_root, RecursiveMode::Recursive)
            .with_context(|| format!("failed to watch {}", project_root.display()))?;
        for root in local_package_roots {
            watcher
                .watch(root, RecursiveMode::Recursive)
                .with_context(|| format!("failed to watch {}", root.display()))?;
        }
        let local_package_roots = local_package_roots.to_vec();

        let (batch_tx, batch_rx) = mpsc::channel::<Vec<PathBuf>>();
        let root = project_root.to_path_buf();
        let pending: Arc<Mutex<HashSet<PathBuf>>> = Arc::new(Mutex::new(HashSet::new()));
        let pending_flush = Arc::clone(&pending);

        thread::Builder::new()
            .name("mda-dev-watch".into())
            .spawn(move || {
                let mut deadline: Option<Instant> = None;
                loop {
                    let timeout = deadline.map(|at| {
                        at.saturating_duration_since(Instant::now())
                            .max(Duration::from_millis(1))
                    });
                    let recv = match timeout {
                        Some(duration) => raw_rx.recv_timeout(duration),
                        None => raw_rx
                            .recv()
                            .map_err(|_| mpsc::RecvTimeoutError::Disconnected),
                    };
                    match recv {
                        Ok(event) => {
                            if !is_content_change(&event.kind) {
                                continue;
                            }
                            let mut guard = pending_flush.lock().unwrap_or_else(|e| e.into_inner());
                            for path in event.paths {
                                if let Some(rel) = relativize_watch_path(&root, &path) {
                                    guard.insert(rel);
                                } else if let Some(path) =
                                    local_package_watch_path(&local_package_roots, &path)
                                {
                                    guard.insert(path);
                                }
                            }
                            deadline = Some(Instant::now() + WATCH_DEBOUNCE);
                        }
                        Err(mpsc::RecvTimeoutError::Timeout) => {
                            let batch = {
                                let mut guard =
                                    pending_flush.lock().unwrap_or_else(|e| e.into_inner());
                                std::mem::take(&mut *guard)
                            };
                            deadline = None;
                            if batch.is_empty() {
                                continue;
                            }
                            let mut paths: Vec<PathBuf> = batch.into_iter().collect();
                            paths.sort();
                            if batch_tx.send(paths).is_err() {
                                break;
                            }
                        }
                        Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    }
                }
            })
            .context("failed to spawn watch debounce thread")?;

        Ok(Self {
            _watcher: watcher,
            rx: batch_rx,
        })
    }

    /// Block until the next debounced batch, a timeout, or watcher disconnect.
    pub fn recv_timeout(&self, timeout: Duration) -> WatchPoll {
        match self.rx.recv_timeout(timeout) {
            Ok(paths) => WatchPoll::Paths(paths),
            Err(mpsc::RecvTimeoutError::Timeout) => WatchPoll::Timeout,
            Err(mpsc::RecvTimeoutError::Disconnected) => WatchPoll::Disconnected,
        }
    }
}

/// Result of polling the project watcher.
#[derive(Debug)]
pub enum WatchPoll {
    Paths(Vec<PathBuf>),
    Timeout,
    Disconnected,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root_ctx() -> WatchClassifyContext {
        WatchClassifyContext::from_agent_file(Path::new("agent.ts"))
    }

    #[test]
    fn classifies_local_package_paths_as_recompile() {
        assert_eq!(
            classify_watch_path(Path::new("/work/dep/src/index.ts"), &root_ctx()),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("/work/build/dep/index.ts"), &root_ctx()),
            WatchAction::Recompile
        );
    }

    #[test]
    fn local_package_watch_path_keeps_only_vendored_files() {
        let roots = vec![PathBuf::from("/work/dep")];
        assert_eq!(
            local_package_watch_path(&roots, Path::new("/work/dep/src/index.ts")),
            Some(PathBuf::from("/work/dep/src/index.ts"))
        );
        assert_eq!(
            local_package_watch_path(&roots, Path::new("/work/dep/node_modules/zod/index.js")),
            None
        );
        assert_eq!(
            local_package_watch_path(&roots, Path::new("/work/dep/.env")),
            None
        );
        assert_eq!(
            local_package_watch_path(&roots, Path::new("/work/other/a.ts")),
            None
        );
    }

    #[test]
    fn classifies_agent_source_as_recompile() {
        let ctx = root_ctx();
        assert_eq!(
            classify_watch_path(Path::new("agent.ts"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("tools/query.ts"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("connectors/mcp.ts"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("tools/mcp.ts"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("package.json"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("pyproject.toml"), &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_path(Path::new("uv.lock"), &ctx),
            WatchAction::Recompile
        );
    }

    #[test]
    fn ignores_other_package_manager_lockfiles() {
        let ctx = root_ctx();
        for path in [
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
            "npm-shrinkwrap.json",
            "packages/ui/pnpm-lock.yaml",
        ] {
            assert_eq!(
                classify_watch_path(Path::new(path), &ctx),
                WatchAction::Ignore,
                "{path}"
            );
        }
    }

    #[test]
    fn classifies_hub_and_env_paths() {
        let ctx = root_ctx();
        assert_eq!(
            classify_watch_path(Path::new("instructions.md"), &ctx),
            WatchAction::HubSyncReload
        );
        assert_eq!(
            classify_watch_path(Path::new("skills/research/SKILL.md"), &ctx),
            WatchAction::HubSync
        );
        assert_eq!(
            classify_watch_path(Path::new(".env"), &ctx),
            WatchAction::EnvRestage
        );
        assert_eq!(
            classify_watch_path(Path::new(".env.local"), &ctx),
            WatchAction::EnvRestage
        );
    }

    #[test]
    fn skill_nested_layout_names_are_hub_sync_not_ignored() {
        let ctx = root_ctx();
        // Project denylist names are valid skill content (matches Context Hub).
        for path in [
            "skills/release/build/template.md",
            "skills/release/dist/notes.md",
            "skills/release/evals/rubric.md",
            "skills/release/memories/hints.md",
        ] {
            assert_eq!(
                classify_watch_path(Path::new(path), &ctx),
                WatchAction::HubSync,
                "{path}"
            );
        }
        // Real junk under skills still ignored.
        assert_eq!(
            classify_watch_path(Path::new("skills/foo/__pycache__/x.pyc"), &ctx),
            WatchAction::Ignore
        );
        assert_eq!(
            classify_watch_path(Path::new("skills/foo/.DS_Store"), &ctx),
            WatchAction::Ignore
        );
    }

    #[test]
    fn ignores_build_output_memory_evals_and_junk() {
        let ctx = root_ctx();
        assert_eq!(
            classify_watch_path(Path::new(".mda/build/agent.ts"), &ctx),
            WatchAction::Ignore
        );
        assert_eq!(
            classify_watch_path(Path::new("memories/agent/AGENTS.md"), &ctx),
            WatchAction::Ignore
        );
        assert_eq!(
            classify_watch_path(Path::new("evals/scaffold/README.md"), &ctx),
            WatchAction::Ignore
        );
        assert_eq!(
            classify_watch_path(Path::new("node_modules/foo/index.js"), &ctx),
            WatchAction::Ignore
        );
        assert_eq!(
            classify_watch_path(Path::new(".DS_Store"), &ctx),
            WatchAction::Ignore
        );
    }

    #[test]
    fn coalesce_prefers_strongest_action() {
        let ctx = root_ctx();
        assert_eq!(
            classify_watch_paths([".env", "skills/a/SKILL.md", "agent.ts"], &ctx),
            WatchAction::Recompile
        );
        assert_eq!(
            classify_watch_paths([".env", "instructions.md"], &ctx),
            WatchAction::HubSyncReload
        );
        assert_eq!(
            classify_watch_paths(["skills/a/SKILL.md", ".env"], &ctx),
            WatchAction::HubSync
        );
    }

    #[test]
    fn nested_agent_uses_adjacent_instructions_and_skills() {
        let ctx = WatchClassifyContext::from_agent_file(Path::new("src/agent.ts"));
        assert_eq!(
            classify_watch_path(Path::new("src/instructions.md"), &ctx),
            WatchAction::HubSyncReload
        );
        assert_eq!(
            classify_watch_path(Path::new("src/skills/research/SKILL.md"), &ctx),
            WatchAction::HubSync
        );
        assert_eq!(
            classify_watch_path(Path::new("instructions.md"), &ctx),
            WatchAction::Recompile
        );
    }

    #[test]
    fn access_events_are_not_content_changes() {
        assert!(!is_content_change(&EventKind::Access(
            notify::event::AccessKind::Any
        )));
        assert!(is_content_change(&EventKind::Modify(
            notify::event::ModifyKind::Any
        )));
        assert!(is_content_change(&EventKind::Create(
            notify::event::CreateKind::Any
        )));
        assert!(is_content_change(&EventKind::Remove(
            notify::event::RemoveKind::Any
        )));
    }

    #[test]
    fn batch_includes_env_path_detects_dotenv() {
        assert!(batch_includes_env_path(["agent.ts", ".env"]));
        assert!(batch_includes_env_path([".env.local"]));
        assert!(!batch_includes_env_path(["agent.ts", "instructions.md"]));
    }
}
