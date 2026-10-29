//! Compile a loaded project into a deployable managed LangGraph app.
//!
//! The output directory is a self-contained build artifact. Every user file is
//! carried over verbatim — the agent file is **never** rewritten (aside from
//! TypeScript text-module inlining). Instead, the CLI generates a small managed
//! entry module that consumes the user's `defineDeepAgent(...)` definition and
//! plugs it into an `agent(...)` graph factory, with the platform-owned
//! backend, store, and checkpointer injected by the managed runtime.
//! `langgraph.json` points at that generated entry.

use anyhow::{bail, Context, Result};
use std::collections::{BTreeSet, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use uv_pep440::{Version, VersionSpecifiers};
use walkdir::WalkDir;

use crate::agent_config;
use crate::cli_output::{show_sequence, SequenceItem};
use crate::error::{categorize_unclassified, ErrorCategory};
use crate::path_util::to_unix_path;
use crate::project::{Language, Project};
use crate::text_module;

mod diagnostics;
mod discovery;
mod entry;
mod exports;
mod identity;
mod local_packages;
mod manifest;
mod memory;
mod model;
pub(crate) mod requirements;
mod runtime;
mod sandbox_recipe;
pub(crate) mod schedules;
mod static_extract;

#[cfg(test)]
pub(crate) mod test_support;
#[cfg(test)]
#[path = "tests.rs"]
mod tests;

pub(crate) use discovery::{find_sandbox_entry, read_setup_script};
pub use identity::IdentitySummary;
pub(crate) use identity::{
    identity_requires_ingress_secret, read_identity_summary, INGRESS_SECRET_ENV,
};
#[cfg(test)]
pub(crate) use identity::{IdentityAuthMode, IdentityScopeAxes};
pub(crate) use local_packages::{find_local_packages, is_vendored_path, vendored_manifests};
pub use memory::MemoryDecision;
pub(crate) use memory::{declare_memory_snippet, resolve_memory};
#[cfg(test)]
pub(crate) use memory::{MemoryLayers, MemorySummary};
pub(crate) use model::read_agent_model;
pub(crate) use requirements::{
    agent_connections_from_connector_requirements, channel_provided_connections,
    connection_owner_policy_for_slug, emit_channel_requirements_for_entries,
    emit_connector_requirements_for_entries, emit_mcp_connection_entries_for_entries,
    referenced_connection_slugs, required_env_from_connector_requirements,
    reserved_user_connection_error, unprovided_reserved_connections,
    user_connections_from_connector_requirements, ChannelRequirements, ConnectorRequirements,
    McpConnectionEntry,
};
pub(crate) use sandbox_recipe::{
    load_sandbox_recipe_manifest, project_uses_sandbox, SandboxRegistryManifest,
};

use discovery::{
    agent_import_specifier, entry_stem_name, find_browser_entry, find_identity_entry,
    find_memory_entry, misspelled_channel_directory_warning, project_has_skills, read_instructions,
    sandbox_import_specifier,
};
use entry::{
    generate_auth_module, generate_channels_module, generate_connectors_module, generate_entry,
    generate_langgraph_json, generate_managed_http_module, EntryInputs, LanggraphManifestInputs,
    RuntimeContext,
};
pub(crate) use exports::project_needs_events_public_url;
use exports::{scan_exported_channels, scan_exported_connectors};
use local_packages::vendor_local_packages;
use manifest::{
    configure_python_packaging, generate_lockfile, inject_managed_dependencies,
    rewrite_local_package_dependencies, rewrite_runtime_dependency,
    strip_package_json_dev_dependencies, RuntimePathMode, LOCK_INPUT_MARKER,
};
use runtime::vendor_runtime;
use sandbox_recipe::{reject_removed_sandbox_scope, write_sandbox_recipe_manifest};
use schedules::{read_schedule_crons, ScheduleReadContext};

/// The LangGraph Node.js runtime version targeted by compiled TypeScript apps.
///
/// LangGraph's `langgraph.json` schema (enforced by both the local dev server
/// and the remote build) only accepts `"20"` or `"22"`, so this must stay on a
/// supported LTS line — `"24"` is rejected with a Zod validation error before
/// the server starts.
const NODE_VERSION: &str = "22";

/// Python runtimes langgraph-api publishes images for, **lowest first**.
///
/// The low end mirrors `MIN_PYTHON_VERSION` in `langgraph_cli/config.py`, which
/// validates `python_version` against a 3.11 floor. The high end is what the
/// registry actually carries for [`LANGGRAPH_API_VERSION`] — every entry must
/// have a published `{stem}-py{version}` tag, so extend this list when
/// langgraph-api starts shipping a newer series. Resolution picks the newest
/// entry the project allows unless an explicit deployment target is set.
const SUPPORTED_PYTHON_VERSIONS: &[(u32, u32)] = &[(3, 11), (3, 12), (3, 13), (3, 14)];

/// Runtime built when a project declares no `requires-python`.
const DEFAULT_PYTHON_VERSION: (u32, u32) = (3, 14);

/// Environment variable that overrides the Python runtime for one build.
///
/// Overrides `[tool.mda].python-version` without editing project configuration.
pub(crate) const PYTHON_VERSION_ENV: &str = "MDA_PYTHON_VERSION";

/// Environment variable that opts into a non-default langgraph-api image stem.
///
/// Value is a version stem only (e.g. `0.12.0rc9`); compile appends
/// `-node{N}` / `-py{V}` when writing `_INTERNAL_docker_tag`.
pub(crate) const LANGGRAPH_API_VERSION_ENV: &str = "MDA_LANGGRAPH_API_VERSION";

/// Default langgraph-api version stem forced into compiled `langgraph.json`
/// image pins. Override with [`LANGGRAPH_API_VERSION_ENV`].
pub(crate) const LANGGRAPH_API_VERSION: &str = "0.18.0.dev12";

/// In-memory runtime `mda dev` pairs with [`LANGGRAPH_API_VERSION`]. A pre-release
/// Agent Server pulls a pre-release runtime, which uv only accepts when pinned
/// explicitly, so this moves together with the version above.
pub(crate) const LANGGRAPH_RUNTIME_INMEM_VERSION: &str = "0.38.0.dev12";

/// Resolve the langgraph-api version stem for `_INTERNAL_docker_tag`.
///
/// Prefers a non-empty `override_stem` (typically from
/// [`LANGGRAPH_API_VERSION_ENV`]); otherwise the compile-time default.
fn resolve_langgraph_api_version(override_stem: Option<&str>) -> Result<String> {
    let Some(raw) = override_stem.map(str::trim).filter(|stem| !stem.is_empty()) else {
        return Ok(LANGGRAPH_API_VERSION.to_string());
    };
    validate_langgraph_api_version_stem(raw)
}

pub(crate) fn resolved_langgraph_api_version() -> Result<String> {
    resolve_langgraph_api_version(std::env::var(LANGGRAPH_API_VERSION_ENV).ok().as_deref())
}

/// Reject empty, whitespace-containing, or fully-qualified tag values.
fn validate_langgraph_api_version_stem(stem: &str) -> Result<String> {
    if stem.chars().any(char::is_whitespace) {
        bail!("{LANGGRAPH_API_VERSION_ENV} must not contain whitespace (got {stem:?})");
    }
    if stem.contains('/') || stem.contains("-node") || stem.contains("-py") {
        bail!(
            "{LANGGRAPH_API_VERSION_ENV} must be a version stem only (e.g. 0.12.0rc9), \
             not a full docker tag (got {stem:?})"
        );
    }
    Ok(stem.to_string())
}

/// Resolve the deployment target from the environment, `[tool.mda]`, or the
/// newest supported series allowed by `requires-python`, in that order.
pub(crate) fn resolved_python_version(project_root: &Path) -> Result<ResolvedPython> {
    let config = manifest::read_python_version_config(project_root)?;
    resolve_python_version(
        config.requires_python.as_deref(),
        config.python_version.as_deref(),
        std::env::var(PYTHON_VERSION_ENV).ok().as_deref(),
    )
}

/// A resolved runtime, plus anything the author should know about how it was
/// chosen.
#[derive(Debug)]
pub(crate) struct ResolvedPython {
    /// `major.minor`, as written into `langgraph.json`.
    pub(crate) version: String,
    /// Set when the specifier asked for precision an image tag cannot carry.
    /// Returned rather than printed so resolution stays pure and testable.
    pub(crate) warning: Option<String>,
}

impl ResolvedPython {
    fn new(version: (u32, u32)) -> Self {
        Self {
            version: format_python_version(version),
            warning: None,
        }
    }
}

fn resolve_python_version(
    requires_python: Option<&str>,
    configured_version: Option<&str>,
    env_override: Option<&str>,
) -> Result<ResolvedPython> {
    let requires_python = requires_python
        .map(str::trim)
        .filter(|spec| !spec.is_empty());
    let specifiers = match requires_python {
        Some(spec) => Some(VersionSpecifiers::from_str(spec).with_context(|| {
            format!("failed to parse requires-python {spec:?} in pyproject.toml")
        })?),
        None => None,
    };

    let explicit = env_override
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(|value| (value, PYTHON_VERSION_ENV))
        .or_else(|| configured_version.map(|value| (value.trim(), "[tool.mda] python-version")));
    let selected = if let Some((raw, source)) = explicit {
        let selected = parse_python_version(raw, source)?;
        if !SUPPORTED_PYTHON_VERSIONS.contains(&selected) {
            bail!("{}", unsupported_message(selected, source));
        }
        if let Some((specifiers, spec)) = specifiers.as_ref().zip(requires_python) {
            if !series_can_satisfy(specifiers, selected) {
                bail!(
                    "{source}={raw} contradicts requires-python {spec:?} in pyproject.toml; \
                     `pip install -e .` would fail in the built image"
                );
            }
        }
        selected
    } else if let Some(specifiers) = &specifiers {
        SUPPORTED_PYTHON_VERSIONS
            .iter()
            .rev()
            .copied()
            .find(|candidate| series_can_satisfy(specifiers, *candidate))
            .with_context(|| {
                format!(
                    "requires-python {:?} in pyproject.toml allows no Python that \
                     LangGraph ships an image for ({})",
                    requires_python.unwrap_or_default(),
                    supported_versions_list()
                )
            })?
    } else {
        DEFAULT_PYTHON_VERSION
    };
    let Some(specifiers) = specifiers else {
        return Ok(ResolvedPython::new(selected));
    };
    let spec = requires_python.unwrap_or_default();
    let warning = (!series_always_satisfies(&specifiers, selected)).then(|| {
        format!(
            "requires-python {spec:?} constrains the patch level, which \
             LangGraph images do not pin; building Python {}, whose patch comes \
             from the image and may not satisfy it",
            format_python_version(selected)
        )
    });
    Ok(ResolvedPython {
        version: format_python_version(selected),
        warning,
    })
}

/// Whether any stable patch in the series could satisfy the specifier.
fn series_can_satisfy(specifiers: &VersionSpecifiers, series: (u32, u32)) -> bool {
    series_patch_boundaries(specifiers, series)
        .iter()
        .any(|probe| specifiers.contains(probe))
}

/// Whether every stable patch in the series satisfies the specifier.
fn series_always_satisfies(specifiers: &VersionSpecifiers, series: (u32, u32)) -> bool {
    series_patch_boundaries(specifiers, series)
        .iter()
        .all(|probe| specifiers.contains(probe))
}

/// For stable X.Y.patch versions, a constraint can change membership only at
/// its patch boundary or the next patch. Checking those points and patch zero
/// covers each interval, including exact pins, exclusions, and bounded ranges.
/// uv-pep440 owns all comparison semantics; no image patch is assumed here.
fn series_patch_boundaries(specifiers: &VersionSpecifiers, series: (u32, u32)) -> Vec<Version> {
    let mut patches = vec![0];
    for specifier in specifiers.iter() {
        let release = specifier.version().release();
        if release.first() == Some(&u64::from(series.0))
            && release.get(1) == Some(&u64::from(series.1))
        {
            let patch = release.get(2).copied().unwrap_or(0);
            patches.push(patch);
            if let Some(next) = patch.checked_add(1) {
                patches.push(next);
            }
        }
    }
    patches.sort_unstable();
    patches.dedup();
    patches
        .into_iter()
        .map(|patch| version_at_patch(series, patch))
        .collect()
}

fn version_at_patch((major, minor): (u32, u32), patch: u64) -> Version {
    Version::new([u64::from(major), u64::from(minor), patch])
}

fn format_python_version((major, minor): (u32, u32)) -> String {
    format!("{major}.{minor}")
}

fn supported_versions_list() -> String {
    SUPPORTED_PYTHON_VERSIONS
        .iter()
        .copied()
        .map(format_python_version)
        .collect::<Vec<_>>()
        .join(", ")
}

/// Parse a bare `major.minor` runtime version from an explicit override.
///
/// Rebuilding the output from the parsed integers (see
/// [`format_python_version`]) is what keeps the value safe to interpolate into
/// `_INTERNAL_docker_tag`: registry syntax (`/`, `:`, `@`), whitespace, and tag
/// suffixes like `-wolfi` cannot survive it, so an override can never redirect
/// the image reference somewhere else.
fn parse_python_version(raw: &str, source: &str) -> Result<(u32, u32)> {
    let mut parts = raw.split('.');
    let (Some(major), Some(minor), None) = (parts.next(), parts.next(), parts.next()) else {
        bail!(
            "invalid Python version {raw:?} from {source}: use bare `major.minor` \
             (e.g. \"3.14\"), without a patch version or image suffix"
        );
    };
    let (Ok(major), Ok(minor)) = (major.parse::<u32>(), minor.parse::<u32>()) else {
        bail!(
            "invalid Python version {raw:?} from {source}: `major.minor` components \
             must be numbers (e.g. \"3.14\")"
        );
    };
    Ok((major, minor))
}

fn unsupported_message((major, minor): (u32, u32), source: &str) -> String {
    format!(
        "Python {major}.{minor} from {source} is not supported; LangGraph ships \
         images for {}",
        supported_versions_list()
    )
}

/// Basename (without extension) of the generated managed entry module.
const ENTRY_STEM: &str = "_mda_entry";
const EVAL_ENTRY_STEM: &str = "_mda_eval_entry";

/// Directory holding the managed sandbox declaration and provisioning script.
const SANDBOX_DIR: &str = "sandbox";

/// Provisioning script synced and run once per newly created sandbox.
const SANDBOX_SETUP_FILE: &str = "setup.sh";

/// Basename (without extension) of the generated connectors aggregator module.
const CONNECTORS_STEM: &str = "_mda_connectors";
/// Basename (without extension) of the generated channels aggregator module.
const CHANNELS_STEM: &str = "_mda_channels";

/// Named export the root `identity.{ts,py}` declaration exposes.
const IDENTITY_EXPORT: &str = "identity";

/// Canonical export name for the root memory declaration, in both languages.
const MEMORY_EXPORT: &str = "memory";
const BROWSER_EXPORT: &str = "browser";
/// Graph key of the browser handoff watcher, whose run waits out a handoff; the
/// runtime's `HANDOFF_WATCHER_GRAPH` names it too.
const BROWSER_HANDOFF_GRAPH: &str = "mda-browser-handoff";
/// The generated entry's export of that graph, in both languages.
const BROWSER_HANDOFF_EXPORT: &str = "browser_handoff_watcher";

/// Basename (without extension) of the generated managed custom-auth module.
const AUTH_STEM: &str = "_mda_auth";
const MANAGED_HTTP_STEM: &str = "_mda_http";

/// Filename prefix shared by every module the compiler generates.
const GENERATED_PREFIX: &str = "_mda_";

/// Stamp marking a directory as build output this CLI owns and may delete.
const BUILD_MARKER: &str = ".mda-build";

const BUILD_MARKER_CONTENTS: &str = "\
# Written by `mda`. This directory is generated build output: everything in it
# is deleted and rewritten on the next build. Do not keep sources here.
";

/// Top-level entries never deleted by a [`CompileMode::Refresh`] prune so
/// `mda dev` can rebuild without reinstalling managed deps or dropping
/// LangGraph/runtime state.
const REFRESH_PRESERVE: &[&str] = &[
    "node_modules",
    ".mda-deps-stamp",
    ".mda-lock-input",
    ".env",
    ".env.local",
    ".langgraph_api",
    ".venv",
    requirements::PYTHON_EMIT_SITE_DIR,
];

/// Top-level dependency caches a [`CompileMode::Full`] clear keeps, so a
/// deploy does not reinstall the connector/channel emit tree from scratch.
///
/// Only install trees the CLI itself fingerprints (see
/// `requirements::install_typescript_build_deps` / `install_python_emit_deps`),
/// so a stale tree is reconciled rather than trusted. Never `.env` files: a
/// full build must not carry staged secrets forward. The deploy archive omits
/// these directories.
const FULL_PRESERVE: &[&str] = &["node_modules", requirements::PYTHON_EMIT_SITE_DIR];

/// How aggressively to clear `out_dir` before writing a build.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompileMode {
    /// Wipe the managed build directory, then rewrite everything.
    Full,
    /// Overwrite sources/generated artifacts in place (no pre-clear), then prune
    /// stale paths. Preserves install/env/runtime state and avoids briefly
    /// deleting modules a live LangGraph worker still has open.
    Refresh,
}

pub(crate) fn has_current_build_marker(out_dir: &Path) -> Result<bool> {
    let marker = out_dir.join(BUILD_MARKER);
    match fs::read(&marker) {
        Ok(contents) => Ok(contents == BUILD_MARKER_CONTENTS.as_bytes()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => {
            Err(error).with_context(|| format!("failed to read build marker {}", marker.display()))
        }
    }
}

pub(crate) fn eval_graph_export(language: Language) -> String {
    format!("./{EVAL_ENTRY_STEM}.{}:agent", language.extension())
}

/// Subdirectory of the build that holds the vendored runtime.
const RUNTIME_DIR: &str = "__runtime__";

/// Package name of the managed runtime dependency.
const RUNTIME_PACKAGE: &str = crate::dependencies::USER_RUNTIME_PACKAGE;

/// Summary of a successful compilation.
#[derive(Debug)]
pub struct CompileSummary {
    pub out_dir: PathBuf,
    /// User files carried into the build verbatim, relative to the root.
    pub copied: Vec<PathBuf>,
    /// LangGraph `graphs` key / assistant id from the authored name.
    pub assistant_id: String,
    /// Connector modules (the `tools/mcp.{ts,py}` declaration), relative to the root.
    /// Their HTTP routes are enumerated at runtime, not here.
    pub connectors: Vec<PathBuf>,
    /// Channel modules discovered under `channels/`, relative to the root.
    pub channels: Vec<PathBuf>,
    /// First-party ingress channel providers (`slack`, `github`) those modules declare.
    pub channel_providers: BTreeSet<String>,
    /// Root `identity.{ts,py}` declaration, when present (with static scope when known).
    pub identity: Option<IdentitySummary>,
    /// What durable memory this project declares. Reports scope and file only —
    /// never memory contents.
    pub memory: MemoryDecision,
    /// Whether the project uses a managed sandbox: a declaration, or a
    /// factory `setup.sh` recipe.
    pub sandbox: bool,
    /// Whether deploy-owned skills were synced into the Context Hub mock / repo.
    pub has_skills: bool,
    /// Managed schedules discovered under `schedules/`, keyed by file-derived name.
    pub(crate) schedule_crons: schedules::ScheduleCrons,
    /// Non-fatal advisories about the project's shape, for the caller to render
    /// in its own idiom. `compile` never prints them itself: it runs inside a
    /// `cliclack` spinner under `mda dev` / `mda deploy`, and `mda build` has no
    /// clack framing at all.
    pub warnings: Vec<String>,
}

fn build_result_items(
    out_dir: &Path,
    project_path: &Path,
    warnings: &[String],
) -> Vec<SequenceItem> {
    let command = |verb: &str| {
        if project_path == Path::new(".") {
            format!("mda {verb}")
        } else {
            format!("mda {verb} {}", project_path.display())
        }
    };
    let mut items = vec![SequenceItem::section(
        "Managed app compiled",
        [out_dir.display().to_string()],
    )];
    if !warnings.is_empty() {
        items.push(SequenceItem::section("Build warnings", warnings));
    }
    items.extend([
        SequenceItem::section("Run locally", [command("dev")]),
        SequenceItem::section("Deploy when ready", [command("deploy")]),
    ]);
    items
}

/// Show the compiled app and the same action headings used by `mda init`.
pub fn show_build_summary(summary: &CompileSummary, project_path: &Path) -> Result<()> {
    show_sequence(&build_result_items(
        &summary.out_dir,
        project_path,
        &summary.warnings,
    ))
}

/// Context Hub metadata the generated deploy entry passes to the managed
/// runtime.
#[derive(Debug, Clone)]
pub struct DeployContext {
    /// Context Hub repo handle mounted by `ContextHubBackend`.
    pub repo: String,
    /// Whether the synced repo contains any deploy-owned skills.
    pub has_skills: bool,
}

/// Compile `project` into `out_dir`, replacing any previous build output.
///
/// Used by `mda build` and `mda dev` (both local operations). The Python
/// `file://` dependency points at the canonicalized `out_dir`.
///
/// `runtime_source` is the bundled managed-deepagents package path (typically
/// from a validated [`crate::install::Installation`]). Launcher env validation
/// stays in the CLI layer.
pub fn compile(project: &Project, out_dir: &Path, runtime_source: &Path) -> Result<CompileSummary> {
    compile_local(project, out_dir, runtime_source, CompileMode::Full)
}

/// Report an unclassified compile failure as `compilation`, keeping its causes
/// so a child's stderr still reaches the error record. A cancellation passes
/// through unchanged.
pub fn compilation(err: anyhow::Error) -> anyhow::Error {
    categorize_unclassified(ErrorCategory::Compilation, err)
}

/// Like [`compile`], but keeps `node_modules`, staged env, and LangGraph state
/// across the rebuild so `mda dev` watch loops stay fast.
pub fn compile_refresh(
    project: &Project,
    out_dir: &Path,
    runtime_source: &Path,
) -> Result<CompileSummary> {
    compile_local(project, out_dir, runtime_source, CompileMode::Refresh)
}

fn compile_local(
    project: &Project,
    out_dir: &Path,
    runtime_source: &Path,
    mode: CompileMode,
) -> Result<CompileSummary> {
    // One decision, read before the hub is touched: it gates the local hot-file
    // seed *and* what the generated entry mounts, so `mda dev` shows exactly what
    // a deploy would.
    let memory = resolve_memory(project)?;
    let hub = crate::local_context_hub::ensure_local_context_hub(project, memory.agent_enabled())?;
    let context_root = hub
        .root
        .to_str()
        .with_context(|| format!("non-UTF-8 local Context Hub path {}", hub.root.display()))?
        .to_string();
    let api_version = resolved_langgraph_api_version()?;
    compile_inner(
        project,
        out_dir,
        RuntimeContext::Local {
            context_root: &context_root,
            has_skills: hub.has_skills,
        },
        runtime_source,
        memory,
        mode,
        &api_version,
    )
}

/// Compile `project` for deployment, replacing local embedded context with
/// Context Hub metadata consumed by the deployed runtime.
///
/// `runtime_source` is the bundled managed-deepagents package path (typically
/// from a validated [`crate::install::Installation`]). Launcher env validation
/// stays in the CLI layer.
pub fn compile_for_deploy(
    project: &Project,
    out_dir: &Path,
    context: &DeployContext,
    runtime_source: &Path,
) -> Result<CompileSummary> {
    let memory = resolve_memory(project)?;
    let api_version = resolved_langgraph_api_version()?;
    compile_inner(
        project,
        out_dir,
        RuntimeContext::ContextHub {
            repo: &context.repo,
            has_skills: context.has_skills,
        },
        runtime_source,
        memory,
        CompileMode::Full,
        &api_version,
    )
}

/// Compile `project` for an eval trial artifact.
///
/// Eval artifacts keep the authored definition intact but point `langgraph.json`
/// at an eval-specific entry that suppresses nested sandbox provisioning and
/// allows the runner to apply an explicit model override.
pub(crate) fn compile_for_eval_with_api_version(
    project: &Project,
    out_dir: &Path,
    runtime_source: &Path,
    api_version: &str,
    model_override: Option<&str>,
) -> Result<CompileSummary> {
    let system_prompt = read_instructions(project)?;
    // Resolved even though eval never mounts memory: the retired-knob checks live
    // there, and an eval build should fail on the same config a deploy would.
    let memory = resolve_memory(project)?;
    compile_inner(
        project,
        out_dir,
        RuntimeContext::Eval {
            system_prompt: system_prompt.as_deref(),
            model_override,
            has_skills: project_has_skills(project),
        },
        runtime_source,
        memory,
        CompileMode::Full,
        api_version,
    )
}

/// Emit connector requirements for deploy and connection checks.
///
/// Runs a full local compile first so connector modules and the vendored
/// runtime are materialized in `out_dir` before requirements are emitted.
pub fn connector_requirements_for_project(
    project: &Project,
    out_dir: &Path,
    runtime_source: &Path,
) -> Result<ConnectorRequirements> {
    let summary = compile(project, out_dir, runtime_source)?;
    emit_connector_requirements_for_entries(project, out_dir, &summary.connectors)
}

pub(crate) fn mcp_connection_entries_for_project(
    project: &Project,
    out_dir: &Path,
    runtime_source: &Path,
) -> Result<Vec<McpConnectionEntry>> {
    let summary = compile(project, out_dir, runtime_source)?;
    emit_mcp_connection_entries_for_entries(project, out_dir, &summary.connectors)
}

// An explicit runtime override is diagnostic output from the compile pipeline.
#[expect(clippy::print_stderr, reason = "diagnostic for an explicit override")]
fn compile_inner(
    project: &Project,
    out_dir: &Path,
    context: RuntimeContext<'_>,
    runtime_source: &Path,
    memory: MemoryDecision,
    mode: CompileMode,
    api_version: &str,
) -> Result<CompileSummary> {
    // Validate first: prepare_out_dir clears existing files during a full build.
    let agent = agent_config::read_agent_static_config(project)?;
    if matches!(context, RuntimeContext::Eval { .. }) && !agent.async_subagents.is_empty() {
        bail!(
            "async subagents are not supported in eval: there is no Agent Server to launch them on"
        );
    }
    // Served agents also get an async general-purpose subagent; eval has no
    // Agent Server to launch it on.
    let async_subagents = if matches!(context, RuntimeContext::Eval { .. }) {
        Vec::new()
    } else {
        agent.served_async_subagents()
    };
    let local_packages = find_local_packages(project)?;
    prepare_out_dir(&project.root, out_dir, mode)?;

    let mut keep: HashSet<PathBuf> = HashSet::new();
    keep.insert(PathBuf::from(BUILD_MARKER));

    let mut copied = Vec::new();
    let mut outside_imports = Vec::new();
    for rel in &project.files {
        if is_build_owned_lockfile(rel, project, mode, out_dir) {
            continue;
        }
        let src = project.root.join(rel);
        let dst = out_dir.join(rel);
        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }

        if is_typescript_source(rel) {
            // Resolve `with { type: "text" }` imports into inline constants so
            // the build does not depend on runtime text-module support.
            let source = fs::read_to_string(&src)
                .with_context(|| format!("failed to read {}", src.display()))?;
            let resolve_dir = src.parent().unwrap_or(&project.root);
            let transformed =
                text_module::inline_text_modules(&source, &rel.to_string_lossy(), resolve_dir)?;
            for specifier in local_packages::typescript_imports_outside_root(&transformed, rel) {
                outside_imports.push(format!(
                    "{} imports \"{specifier}\" from outside the project, so the build does not \
                     include it. Make it a local package (`npm install <path>`) or move it into \
                     the project.",
                    to_unix_path(rel)
                ));
            }
            write_if_changed(&dst, transformed.as_bytes())
                .with_context(|| format!("failed to write {}", dst.display()))?;
        } else {
            let bytes =
                fs::read(&src).with_context(|| format!("failed to read {}", src.display()))?;
            write_if_changed(&dst, &bytes)
                .with_context(|| format!("failed to write {}", dst.display()))?;
        }
        // `write_if_changed` does not carry mode bits the way `fs::copy` did —
        // sync them after every write (and when bytes were already unchanged)
        // so executable helpers under the project tree stay executable.
        sync_file_permissions(&src, &dst)?;
        copied.push(rel.clone());
        keep.insert(rel.clone());
    }

    record_python_lock_input(project, out_dir)?;

    let entry_stem = if matches!(context, RuntimeContext::Eval { .. }) {
        EVAL_ENTRY_STEM
    } else {
        ENTRY_STEM
    };
    let entry_file = PathBuf::from(format!("{entry_stem}.{}", project.language.extension()));
    let agent_import = agent_import_specifier(&project.agent_file, project.language);
    let sandbox_entry = find_sandbox_entry(project);
    let sandbox_import = sandbox_entry
        .as_ref()
        .map(|entry| sandbox_import_specifier(entry, project.language));

    // Discover connectors name-agnostically (skipping test filenames and
    // modules without the required export shape) and, when any remain,
    // generate an aggregator that validates brands at runtime. Export gate and
    // ingress heuristic share one parse per connector file. This release's
    // public authoring surface is MCP-only (`defineMcp` / `define_mcp`); product
    // toolserver / marketplace factories are not exported.
    let connector_entries = scan_exported_connectors(project)?;
    let connectors_import = if connector_entries.is_empty() {
        None
    } else {
        let connectors_file = PathBuf::from(format!(
            "{CONNECTORS_STEM}.{}",
            project.language.extension()
        ));
        let modules: Vec<(String, String)> = connector_entries
            .iter()
            .map(|entry| {
                (
                    to_unix_path(entry),
                    agent_import_specifier(entry, project.language),
                )
            })
            .collect();
        let connectors_source = generate_connectors_module(project.language, &modules);
        write_if_changed(
            &out_dir.join(&connectors_file),
            connectors_source.as_bytes(),
        )
        .with_context(|| "failed to write managed connectors module")?;
        keep.insert(connectors_file.clone());
        Some(agent_import_specifier(&connectors_file, project.language))
    };

    let mut warnings: Vec<String> = misspelled_channel_directory_warning(project)
        .map(str::to_string)
        .into_iter()
        .collect();
    warnings.extend(outside_imports);
    warnings.extend(diagnostics::project_warnings(
        project,
        &memory,
        context,
        agent.is_factory,
    )?);
    if agent.unreadable_subagents > 0 {
        let rule = project.language.ident(
            "An async subagent (`async: true`) must be an object literal, or a name \
             bound to one, in `subagents: [...]`.",
            "An async subagent (`\"async\": True`) must be a dict literal, or a name \
             bound to one, in `subagents=[...]`. Annotate a name spread there that holds \
             only sync subagents as `list[SubAgent]`.",
        );
        warnings.push(format!(
            "{} subagent entr{} in {} could not be read without running it. {rule}",
            agent.unreadable_subagents,
            if agent.unreadable_subagents == 1 {
                "y"
            } else {
                "ies"
            },
            project.agent_file.display()
        ));
    }
    reject_removed_sandbox_scope(project)?;

    let channel_scan = scan_exported_channels(project)?;
    let channel_entries = channel_scan.entries;
    let channel_providers = channel_scan.providers;
    let channels_import = if channel_entries.is_empty() {
        None
    } else {
        let channels_file =
            PathBuf::from(format!("{CHANNELS_STEM}.{}", project.language.extension()));
        let modules: Vec<(String, String)> = channel_entries
            .iter()
            .map(|entry| {
                (
                    to_unix_path(entry),
                    agent_import_specifier(entry, project.language),
                )
            })
            .collect();
        let channels_source = generate_channels_module(project.language, &modules);
        write_if_changed(&out_dir.join(&channels_file), channels_source.as_bytes())
            .with_context(|| "failed to write managed channels module")?;
        keep.insert(channels_file.clone());
        Some(agent_import_specifier(&channels_file, project.language))
    };

    let identity_entry = find_identity_entry(project);
    let identity_import = identity_entry
        .as_ref()
        .map(|entry| agent_import_specifier(entry, project.language));
    let identity = read_identity_summary(project)?;
    if let Some(identity) = identity.as_ref().filter(|identity| identity.auth_omitted) {
        anyhow::bail!(
            "{} must select authentication explicitly; use auth.langsmithApiKey() / auth.langsmith_api_key() for LangSmith workspace API keys",
            identity.path.display()
        );
    }

    // Durable memory is opt-in and independent of identity. Eval trials never
    // mount it, so the declaration is only threaded into local/deploy entries.
    let memory_import = match context {
        RuntimeContext::Eval { .. } => None,
        RuntimeContext::Local { .. } | RuntimeContext::ContextHub { .. } => {
            find_memory_entry(project).map(|entry| agent_import_specifier(&entry, project.language))
        }
    };

    // Browser use is opt-in. Eval trials never get a browser.
    let browser_import = match context {
        RuntimeContext::Eval { .. } => None,
        RuntimeContext::Local { .. } | RuntimeContext::ContextHub { .. } => {
            find_browser_entry(project)
                .map(|entry| agent_import_specifier(&entry, project.language))
        }
    };

    // Ingress channels (`channels.slack(...)`, `channels.github(...)`) cannot fall back
    // to a deployment service principal: require a root identity declaration.
    if identity_import.is_none() && channel_scan.requires_identity {
        anyhow::bail!(
            "channels/ with an ingress trigger (e.g. Slack or GitHub channels) \
             require a root identity.{{ts,py}} declaration; ingress triggers cannot \
             fall back to a deployment service principal"
        );
    }

    let assistant_id = agent.assistant_id.clone();

    let recipe = write_sandbox_recipe_manifest(project, out_dir, agent.is_factory)?;
    if recipe.is_some() {
        keep.insert(PathBuf::from(sandbox_recipe::SANDBOX_RECIPE_FILE));
    }
    // Only a factory can select a sandbox whose base the recipe was not baked on.
    let sandbox_setup = agent.is_factory
        && !matches!(context, RuntimeContext::Eval { .. })
        && recipe
            .as_ref()
            .is_some_and(|recipe| recipe.has_setup_script);

    let entry_source = generate_entry(
        project.language,
        &EntryInputs {
            agent_import: &agent_import,
            factory: agent.is_factory,
            context,
            sandbox_import: sandbox_import.as_deref(),
            sandbox_setup,
            connectors_import: connectors_import.as_deref(),
            channels_import: channels_import.as_deref(),
            identity_import: identity_import.as_deref(),
            memory_import: memory_import.as_deref(),
            async_subagents: &async_subagents,
            browser_import: browser_import.as_deref(),
        },
    );
    write_if_changed(&out_dir.join(&entry_file), entry_source.as_bytes())
        .with_context(|| "failed to write managed entry module")?;
    keep.insert(entry_file.clone());

    // When the project declares identity, generate the thin custom-auth module
    // and point `langgraph.json` at it. Identity is opt-in: with no
    // `identity.{ts,py}` there is no auth block and behavior is unchanged.
    let auth_path = if let Some(identity_import) = identity_import.as_deref() {
        let auth_file = PathBuf::from(format!("{AUTH_STEM}.{}", project.language.extension()));
        let auth_source = generate_auth_module(
            project.language,
            identity_import,
            connectors_import.as_deref(),
            channels_import.as_deref(),
        );
        write_if_changed(&out_dir.join(&auth_file), auth_source.as_bytes())
            .with_context(|| "failed to write managed auth module")?;
        keep.insert(auth_file.clone());
        Some(format!("./{}:auth", to_unix_path(&auth_file)))
    } else {
        None
    };

    // The single managed HTTP app mounted via `langgraph.json` `http.app` serves
    // connector routes and channel Events routes. Routes use live plugin
    // definitions (no compile-time JSON embed).
    let managed_http_path =
        if identity_import.is_some() || connectors_import.is_some() || channels_import.is_some() {
            let http_file = PathBuf::from(format!(
                "{MANAGED_HTTP_STEM}.{}",
                project.language.extension()
            ));
            let http_source = generate_managed_http_module(
                project.language,
                identity_import.as_deref(),
                connectors_import.as_deref(),
                channels_import.as_deref(),
            );
            write_if_changed(&out_dir.join(&http_file), http_source.as_bytes())
                .with_context(|| "failed to write managed HTTP module")?;
            keep.insert(http_file.clone());
            Some(format!("./{}:app", to_unix_path(&http_file)))
        } else {
            None
        };

    let graph = format!("./{}:agent", to_unix_path(&entry_file));
    let mut extra_graphs: Vec<(String, String)> = async_subagents
        .iter()
        .map(|subagent| {
            let export = &subagent.export;
            (
                subagent.graph_id.clone(),
                format!("./{}:{export}", to_unix_path(&entry_file)),
            )
        })
        .collect();
    // `browser_handoff` starts a run of this graph to wait for the user to hand
    // the browser back, then wake the agent.
    if browser_import.is_some() {
        if assistant_id == BROWSER_HANDOFF_GRAPH {
            bail!("agent name `{BROWSER_HANDOFF_GRAPH}` is reserved for browser use");
        }
        extra_graphs.push((
            BROWSER_HANDOFF_GRAPH.to_string(),
            format!("./{}:{BROWSER_HANDOFF_EXPORT}", to_unix_path(&entry_file)),
        ));
    }
    let channel_names = channel_entries
        .iter()
        .map(|rel| entry_stem_name(rel, "channel"))
        .collect::<Result<Vec<_>>>()?;
    let schedule_crons = read_schedule_crons(
        project,
        ScheduleReadContext {
            source_root: out_dir,
            assistant_id: &assistant_id,
            channels: &channel_names,
        },
    )?;
    if identity_import.is_none() && !schedule_crons.is_empty() {
        bail!(
            "schedules require a root identity.ts or identity.py declaration for managed execution"
        );
    }
    if api_version != LANGGRAPH_API_VERSION {
        eprintln!(
            "warning: using langgraph-api version `{api_version}` from \
             {LANGGRAPH_API_VERSION_ENV} (default is `{LANGGRAPH_API_VERSION}`)"
        );
    }
    // TypeScript keys the runtime on `node_version`, so Python configuration is
    // neither read nor validated for those builds: an exported
    // `MDA_PYTHON_VERSION` or an unrelated `pyproject.toml` at the project root
    // must not fail a build that ignores both.
    let python_version = if project.language == Language::Python {
        let python = resolved_python_version(&project.root)?;
        if let Some(warning) = python.warning {
            warnings.push(warning);
        }
        python.version
    } else {
        format_python_version(DEFAULT_PYTHON_VERSION)
    };
    let deploy_local_packages: Vec<String> = match context {
        RuntimeContext::ContextHub { .. } => local_packages
            .iter()
            .map(|package| package.build_dir())
            .collect(),
        RuntimeContext::Local { .. } | RuntimeContext::Eval { .. } => Vec::new(),
    };
    let langgraph = generate_langgraph_json(&LanggraphManifestInputs {
        graph: &graph,
        language: project.language,
        api_version,
        python_version: &python_version,
        auth_path: auth_path.as_deref(),
        managed_http_path: managed_http_path.as_deref(),
        assistant_id: &assistant_id,
        include_vendored_runtime: matches!(context, RuntimeContext::ContextHub { .. }),
        local_packages: &deploy_local_packages,
        // Local/eval builds turn off the Studio auth scheme: the dev server
        // hands out Studio identity on `x-auth-scheme: langsmith` alone, with no
        // credential check, and `MDA_LOCAL_DEV` already unscopes local Studio.
        disable_studio_auth: matches!(
            context,
            RuntimeContext::Local { .. } | RuntimeContext::Eval { .. }
        ),
        extra_graphs: &extra_graphs,
    })?;
    // Avoid bumping mtime when the manifest is unchanged so `mda dev` watch
    // does not restart LangGraph on every source-only refresh.
    write_if_changed(&out_dir.join("langgraph.json"), langgraph.as_bytes())
        .with_context(|| "failed to write langgraph.json")?;
    keep.insert(PathBuf::from("langgraph.json"));

    if project.language == Language::Python {
        configure_python_packaging(out_dir)?;
    }

    let is_dev = matches!(
        context,
        RuntimeContext::Local { .. } | RuntimeContext::Eval { .. }
    );
    let runtime_path_mode = match context {
        RuntimeContext::Local { .. } => RuntimePathMode::AbsoluteDev,
        RuntimeContext::Eval { .. } => RuntimePathMode::Relative,
        RuntimeContext::ContextHub { .. } => RuntimePathMode::Deploy,
    };

    // Inject the inferred dependency stack (the LangChain/LangGraph runtime plus
    // the chat-model provider implied by the agent's `model`) into the build
    // manifest. Authors only declare `managed-deepagents` and `deepagents`;
    // everything else is managed and never written into their source.
    let eval_model_override = match context {
        RuntimeContext::Eval { model_override, .. } => model_override,
        RuntimeContext::Local { .. } | RuntimeContext::ContextHub { .. } => None,
    };
    inject_managed_dependencies(
        project,
        out_dir,
        is_dev,
        agent.model.as_deref(),
        eval_model_override,
        browser_import.is_some(),
    )?;

    vendor_runtime(project.language, runtime_source, out_dir)?;
    rewrite_runtime_dependency(project.language, out_dir, runtime_path_mode)?;
    vendor_local_packages(
        project.language,
        &local_packages,
        out_dir,
        runtime_path_mode,
        &mut keep,
    )?;
    rewrite_local_package_dependencies(
        project.language,
        out_dir,
        &local_packages,
        runtime_path_mode,
    )?;

    if project.language == Language::TypeScript {
        // Deploy installs every lockfile entry (including author UI tooling).
        // Drop devDependencies before materializing the lockfile so remote
        // `npm ci` does not pull platform-skewed optional peers (e.g. @emnapi/*).
        if !is_dev {
            strip_package_json_dev_dependencies(out_dir)?;
            for package in &local_packages {
                strip_package_json_dev_dependencies(&out_dir.join(package.build_dir()))?;
            }
        }
        generate_lockfile(out_dir)?;
    }

    // Manifest / runtime artifacts produced above (exact set varies by language).
    for name in [
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        RUNTIME_DIR,
    ] {
        if out_dir.join(name).exists() {
            keep.insert(PathBuf::from(name));
        }
    }
    // Vendored runtime tree: keep the whole directory on refresh prune.
    if out_dir.join(RUNTIME_DIR).is_dir() {
        for entry in WalkDir::new(out_dir.join(RUNTIME_DIR))
            .into_iter()
            .filter_map(Result::ok)
        {
            if entry.file_type().is_file() {
                if let Ok(rel) = entry.path().strip_prefix(out_dir) {
                    keep.insert(rel.to_path_buf());
                }
            }
        }
    }

    if mode == CompileMode::Refresh {
        prune_stale_refresh_files(out_dir, &keep)?;
    }

    let has_skills = match context {
        RuntimeContext::Local { has_skills, .. }
        | RuntimeContext::Eval { has_skills, .. }
        | RuntimeContext::ContextHub { has_skills, .. } => has_skills,
    };

    Ok(CompileSummary {
        out_dir: out_dir.to_path_buf(),
        copied,
        assistant_id,
        connectors: connector_entries,
        channels: channel_entries,
        channel_providers,
        identity,
        memory,
        sandbox: sandbox_entry.is_some() || recipe.is_some(),
        has_skills,
        schedule_crons,
        warnings,
    })
}

/// Empty `out_dir` so it can receive a fresh build.
///
/// `out_dir` comes from `mda build --out`, so the wipe is gated: the target has
/// to be missing, empty, or a directory a previous build wrote, and it may
/// never be the project directory or an ancestor of it. Without those checks
/// `--out .` deletes the user's sources, `.env` included.
///
/// [`CompileMode::Full`] keeps only the dependency caches in [`FULL_PRESERVE`].
/// [`CompileMode::Refresh`] leaves existing artifacts in place so a live
/// LangGraph worker is not racing a wiped directory; stale paths are pruned
/// after the rewrite via [`prune_stale_refresh_files`].
fn prepare_out_dir(project_root: &Path, out_dir: &Path, mode: CompileMode) -> Result<()> {
    if out_dir.exists() {
        if !out_dir.is_dir() {
            bail!(
                "build output path {} exists and is not a directory",
                out_dir.display()
            );
        }
        reject_project_ancestor(project_root, out_dir)?;
        if !is_empty_dir(out_dir)? {
            if !is_managed_build_dir(out_dir)? {
                bail!(
                    "refusing to clean build directory {}: it is not empty and was not written \
                     by `mda`. Point --out at an empty or previously built directory, or remove \
                     this one yourself.",
                    out_dir.display()
                );
            }
            // Clear the contents rather than the directory itself: removing
            // `out_dir` fails with EINVAL when it is the working directory
            // (`--out .`), and only after the removal has already run.
            if mode == CompileMode::Full {
                clear_dir_preserving(out_dir, FULL_PRESERVE)?;
            }
        }
    }
    fs::create_dir_all(out_dir)
        .with_context(|| format!("failed to create build directory {}", out_dir.display()))?;
    // Written before any artifact so a build that fails partway still leaves a
    // directory the next build recognizes as its own.
    fs::write(out_dir.join(BUILD_MARKER), BUILD_MARKER_CONTENTS)
        .with_context(|| format!("failed to stamp build directory {}", out_dir.display()))?;
    Ok(())
}

/// After an in-place refresh, delete build files that are no longer produced
/// (e.g. a removed tool module) while keeping install/env/runtime state.
fn prune_stale_refresh_files(out_dir: &Path, keep: &HashSet<PathBuf>) -> Result<()> {
    let mut files = Vec::new();
    for entry in WalkDir::new(out_dir)
        .into_iter()
        .filter_entry(|entry| {
            if entry.depth() == 0 {
                return true;
            }
            let name = entry.file_name();
            !REFRESH_PRESERVE
                .iter()
                .any(|keep_name| name == std::ffi::OsStr::new(keep_name))
        })
        .filter_map(Result::ok)
    {
        if entry.file_type().is_file() {
            files.push(entry.into_path());
        }
    }

    for path in files {
        let Ok(rel) = path.strip_prefix(out_dir) else {
            continue;
        };
        if keep.contains(rel) {
            continue;
        }
        // Also keep files under a kept directory prefix (e.g. `__runtime__/…`
        // when only the directory name was inserted).
        if keep.iter().any(|kept| {
            rel.starts_with(kept) && kept.components().count() == 1 && out_dir.join(kept).is_dir()
        }) {
            continue;
        }
        fs::remove_file(&path)
            .with_context(|| format!("failed to prune stale build file {}", path.display()))?;
    }

    // Drop empty directories left behind (but never preserved top-level dirs).
    let mut dirs: Vec<PathBuf> = WalkDir::new(out_dir)
        .into_iter()
        .filter_entry(|entry| {
            if entry.depth() == 0 {
                return true;
            }
            let name = entry.file_name();
            !REFRESH_PRESERVE
                .iter()
                .any(|keep_name| name == std::ffi::OsStr::new(keep_name))
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_dir())
        .map(|entry| entry.into_path())
        .collect();
    dirs.sort_by_key(|dir| std::cmp::Reverse(dir.components().count()));
    for dir in dirs {
        if dir == out_dir {
            continue;
        }
        let _ = fs::remove_dir(&dir);
    }
    Ok(())
}

/// Reject an `out_dir` that is the project directory or contains it.
///
/// A non-existent `out_dir` cannot be an ancestor of the (existing) project
/// root, so this only has to consider directories that are already there.
fn reject_project_ancestor(project_root: &Path, out_dir: &Path) -> Result<()> {
    let out = fs::canonicalize(out_dir)
        .with_context(|| format!("failed to resolve {}", out_dir.display()))?;
    let root = fs::canonicalize(project_root)
        .with_context(|| format!("failed to resolve {}", project_root.display()))?;
    if root == out {
        bail!(
            "refusing to build into the project directory {}: the build directory is emptied \
             first, which would delete the project. Pass --out a separate directory.",
            out_dir.display()
        );
    }
    if root.starts_with(&out) {
        bail!(
            "refusing to build into {}: it contains the project at {}, and the build directory \
             is emptied first. Pass --out a separate directory.",
            out_dir.display(),
            project_root.display()
        );
    }
    Ok(())
}

/// Whether `dir` holds build output this CLI wrote.
///
/// Current builds are stamped with [`BUILD_MARKER`]. Builds from CLI versions
/// that predate the stamp are recognized by their generated artifacts, so
/// upgrading does not force users to delete an existing build directory.
fn is_managed_build_dir(dir: &Path) -> Result<bool> {
    if dir.join(BUILD_MARKER).exists() {
        return Ok(true);
    }
    if !dir.join("langgraph.json").is_file() {
        return Ok(false);
    }
    for entry in read_dir(dir)? {
        let entry = entry.with_context(|| format!("failed to read {}", dir.display()))?;
        if entry
            .file_name()
            .to_string_lossy()
            .starts_with(GENERATED_PREFIX)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn is_empty_dir(dir: &Path) -> Result<bool> {
    let mut entries = read_dir(dir)?;
    match entries.next() {
        None => Ok(true),
        Some(entry) => {
            entry.with_context(|| format!("failed to read {}", dir.display()))?;
            Ok(false)
        }
    }
}

/// Remove everything inside `dir` except top-level names in `preserve`,
/// leaving `dir` itself in place.
fn clear_dir_preserving(dir: &Path, preserve: &[&str]) -> Result<()> {
    for entry in read_dir(dir)? {
        let entry = entry.with_context(|| format!("failed to read {}", dir.display()))?;
        let name = entry.file_name();
        if preserve
            .iter()
            .any(|keep| name == std::ffi::OsStr::new(keep))
        {
            continue;
        }
        let path = entry.path();
        let removed = if entry
            .file_type()
            .with_context(|| format!("failed to inspect {}", path.display()))?
            .is_dir()
        {
            fs::remove_dir_all(&path)
        } else {
            fs::remove_file(&path)
        };
        removed.with_context(|| format!("failed to clean build directory {}", path.display()))?;
    }
    Ok(())
}

/// Write `contents` to `path` only when the on-disk bytes differ.
///
/// Returns `true` when a write occurred. Used for watch-sensitive artifacts
/// (`langgraph.json`, manifests) so identical refreshes do not bump mtime.
pub(super) fn write_if_changed(path: &Path, contents: &[u8]) -> Result<bool> {
    if path.is_file() {
        if let Ok(existing) = fs::read(path) {
            if existing == contents {
                return Ok(false);
            }
        }
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    fs::write(path, contents).with_context(|| format!("failed to write {}", path.display()))?;
    Ok(true)
}

/// Copy permission bits from `src` onto `dst`, including when content was left
/// unchanged by [`write_if_changed`] (mode-only source edits).
pub(super) fn sync_file_permissions(src: &Path, dst: &Path) -> Result<()> {
    let src_permissions = fs::metadata(src)
        .with_context(|| format!("failed to read permissions for {}", src.display()))?
        .permissions();
    let dst_permissions = fs::metadata(dst)
        .with_context(|| format!("failed to read permissions for {}", dst.display()))?
        .permissions();
    if src_permissions == dst_permissions {
        return Ok(());
    }
    fs::set_permissions(dst, src_permissions)
        .with_context(|| format!("failed to set permissions on {}", dst.display()))?;
    Ok(())
}

/// Whether the build owns `rel`, so the author's copy must not overwrite it.
///
/// `package-lock.json` is compile-managed (`generate_lockfile`). Copying the
/// author's stale lock over a prior build makes the deps stamp look dirty while
/// lock regeneration is skipped (the `package.json` marker still matches).
///
/// `uv.lock` is author-owned on a full build (compile does not regenerate it),
/// but `mda dev` runs `uv sync` in `.mda/dev`, and uv rewrites the build lock
/// to cover the injected managed stack. Copying the author's lock back over it
/// on every source-only refresh dirties the deps stamp, so each source edit
/// re-runs `uv sync`, churns `.mda/dev/.venv`, and bounces the LangGraph
/// worker several times before it serves the edit.
///
/// The author's last-copied lock is recorded in [`LOCK_INPUT_MARKER`]. A
/// refresh copies `uv.lock` again only when that marker no longer matches, so
/// an intentional author lock edit still reaches `uv sync`.
fn is_build_owned_lockfile(
    rel: &Path,
    project: &Project,
    mode: CompileMode,
    out_dir: &Path,
) -> bool {
    match project.language {
        Language::TypeScript => rel == Path::new("package-lock.json"),
        Language::Python => {
            if mode != CompileMode::Refresh || rel != Path::new("uv.lock") {
                return false;
            }
            if !out_dir.join(rel).is_file() {
                return false;
            }
            python_author_lock_unchanged(&project.root, out_dir)
        }
    }
}

/// Whether the project `uv.lock` still matches the last copy recorded in the
/// build. Missing marker (upgraded CLI over an older build) is treated as
/// unchanged so the first refresh does not clobber a synced lock.
fn python_author_lock_unchanged(project_root: &Path, out_dir: &Path) -> bool {
    let Ok(author) = fs::read(project_root.join("uv.lock")) else {
        return true;
    };
    match fs::read(out_dir.join(LOCK_INPUT_MARKER)) {
        Ok(marker) => marker == author,
        Err(_) => true,
    }
}

/// Remember the author's `uv.lock` bytes so a later refresh can tell a
/// source-only rebuild from an intentional lock edit.
fn record_python_lock_input(project: &Project, out_dir: &Path) -> Result<()> {
    if project.language != Language::Python {
        return Ok(());
    }
    let author = project.root.join("uv.lock");
    if !author.is_file() {
        return Ok(());
    }
    let bytes =
        fs::read(&author).with_context(|| format!("failed to read {}", author.display()))?;
    write_if_changed(&out_dir.join(LOCK_INPUT_MARKER), &bytes)?;
    Ok(())
}

/// Touch the managed entry module so LangGraph's file watcher reloads workers
/// after a hub-only update (e.g. `instructions.md`) that does not rewrite the
/// entry contents.
pub fn poke_dev_reload_signal(out_dir: &Path, language: Language) -> Result<()> {
    let entry = out_dir.join(format!("{ENTRY_STEM}.{}", language.extension()));
    if !entry.is_file() {
        bail!(
            "cannot poke reload signal: missing managed entry {}",
            entry.display()
        );
    }
    let now = std::time::SystemTime::now();
    fs::File::options()
        .write(true)
        .open(&entry)
        .and_then(|file| file.set_modified(now))
        .with_context(|| format!("failed to touch {}", entry.display()))?;
    Ok(())
}

fn read_dir(dir: &Path) -> Result<fs::ReadDir> {
    fs::read_dir(dir).with_context(|| format!("failed to read {}", dir.display()))
}

/// Whether `rel` is a TypeScript source file that should be run through the
/// text-module inliner instead of copied verbatim.
fn is_typescript_source(rel: &Path) -> bool {
    matches!(
        rel.extension().and_then(|ext| ext.to_str()),
        Some("ts" | "tsx" | "mts" | "cts")
    )
}

#[cfg(test)]
mod api_version_tests {
    use super::*;

    #[test]
    fn default_is_0_18_0_dev12() {
        assert_eq!(LANGGRAPH_API_VERSION, "0.18.0.dev12");
    }

    #[test]
    fn resolve_falls_back_to_default_when_unset() {
        assert_eq!(
            resolve_langgraph_api_version(None).unwrap(),
            LANGGRAPH_API_VERSION
        );
    }

    #[test]
    fn resolve_falls_back_to_default_when_blank() {
        assert_eq!(
            resolve_langgraph_api_version(Some("   ")).unwrap(),
            LANGGRAPH_API_VERSION
        );
    }

    #[test]
    fn resolve_accepts_version_stem_override() {
        assert_eq!(
            resolve_langgraph_api_version(Some("0.13.0rc1")).unwrap(),
            "0.13.0rc1"
        );
    }

    #[test]
    fn resolve_trims_version_stem_override() {
        assert_eq!(
            resolve_langgraph_api_version(Some("  0.13.0rc1  ")).unwrap(),
            "0.13.0rc1"
        );
    }

    #[test]
    fn resolve_rejects_internal_whitespace() {
        let err = resolve_langgraph_api_version(Some("0.13.0 rc1")).unwrap_err();
        assert!(
            err.to_string().contains("must not contain whitespace"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn resolve_rejects_full_docker_tag() {
        let err = resolve_langgraph_api_version(Some("0.13.0rc1-node22")).unwrap_err();
        assert!(
            err.to_string().contains("version stem only"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn resolve_rejects_python_tag_suffix() {
        let err = resolve_langgraph_api_version(Some("0.13.0rc1-py3.11")).unwrap_err();
        assert!(
            err.to_string().contains("version stem only"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn resolve_rejects_registry_path() {
        let err =
            resolve_langgraph_api_version(Some("registry.example/langgraph-api:0.13")).unwrap_err();
        assert!(
            err.to_string().contains("version stem only"),
            "unexpected error: {err:#}"
        );
    }
}

#[cfg(test)]
mod python_version_tests {
    use super::*;

    #[test]
    fn missing_requirements_use_the_newest_supported_runtime() {
        for spec in [None, Some(" ")] {
            let resolved = resolve_python_version(spec, None, None).unwrap();
            assert_eq!(resolved.version, "3.14");
            assert!(resolved.warning.is_none());
        }
        assert_eq!(
            Some(DEFAULT_PYTHON_VERSION),
            SUPPORTED_PYTHON_VERSIONS.last().copied()
        );
    }

    #[test]
    fn requirements_select_the_newest_compatible_series_and_warn_only_for_patch_constraints() {
        for (spec, version, warns) in [
            (">=3.11", "3.14", false),
            (">=3.9", "3.14", false),
            (">=3.12,<3.13", "3.12", false),
            ("==3.13.*", "3.13", false),
            ("~=3.12.0", "3.12", false),
            (">=3.11,!=3.14.*", "3.13", false),
            (">=3.11,!=3.14.*,!=3.13.*", "3.12", false),
            (">=3.11.0", "3.14", false),
            (">=3.13.2", "3.14", false),
            (">3.12,<3.13", "3.12", true),
            (">=3.13.2,<3.14", "3.13", true),
            ("<=3.12.1", "3.12", true),
            (">=3.11,<3.11.100", "3.11", true),
            (">=3.11.1,<3.11.100", "3.11", true),
            (">=3.11.10000,<3.12", "3.11", true),
            ("==3.13", "3.13", true),
            ("==3.11.16", "3.11", true),
            (">=3.11,!=3.14.16", "3.14", true),
        ] {
            let resolved = resolve_python_version(Some(spec), None, None).unwrap();
            assert_eq!(resolved.version, version, "{spec}");
            assert_eq!(resolved.warning.is_some(), warns, "{spec}");
        }
    }

    #[test]
    fn environment_overrides_project_target_and_blank_environment_falls_back() {
        for (configured, environment, expected) in [
            (Some("3.12"), None, "3.12"),
            (Some("3.12"), Some("3.13"), "3.13"),
            (Some("3.12"), Some(" "), "3.12"),
            (None, Some("3.13"), "3.13"),
            (None, Some(" "), "3.14"),
        ] {
            let resolved = resolve_python_version(Some(">=3.11"), configured, environment).unwrap();
            assert_eq!(
                resolved.version, expected,
                "{configured:?}, {environment:?}"
            );
        }
    }

    #[test]
    fn explicit_targets_obey_requirements_and_preserve_patch_warnings() {
        for (configured, environment) in [(Some("3.13"), None), (None, Some("3.13"))] {
            let error =
                resolve_python_version(Some(">=3.14"), configured, environment).unwrap_err();
            assert!(error.to_string().contains("contradicts requires-python"));
            let resolved =
                resolve_python_version(Some(">=3.13.2"), configured, environment).unwrap();
            let warning = resolved.warning.unwrap();
            assert!(warning.contains("constrains the patch level"));
            assert!(warning.contains("building Python 3.13"));
        }
    }

    #[test]
    fn invalid_or_unsatisfiable_requirements_fail() {
        for (spec, message) in [
            ("not a specifier", "failed to parse requires-python"),
            (">=3.9,<3.11", "3.11, 3.12, 3.13, 3.14"),
            (">3.11.1,<3.11.2", "allows no Python"),
        ] {
            let error = resolve_python_version(Some(spec), None, None).unwrap_err();
            assert!(error.to_string().contains(message), "{spec}: {error}");
        }
    }

    #[test]
    fn explicit_targets_require_a_supported_minor_version() {
        for raw in [
            "3.10",
            "3.15",
            "3.14.1",
            ">=3.14",
            "3",
            "3.13-wolfi",
            "../3.13",
            "3.13:latest",
            "3.13@sha256:0000",
        ] {
            for (configured, environment, source) in [
                (Some(raw), None, "[tool.mda] python-version"),
                (None, Some(raw), PYTHON_VERSION_ENV),
            ] {
                let error = resolve_python_version(None, configured, environment).unwrap_err();
                assert!(error.to_string().contains(source), "{raw}: {error}");
            }
        }
        assert!(resolve_python_version(None, Some(""), None).is_err());
    }
}

#[cfg(test)]
mod output_tests {
    use super::*;
    use crate::cli_output::SequenceItem;

    #[test]
    fn build_result_uses_init_action_vocabulary_and_hides_internal_details() {
        assert_eq!(
            build_result_items(Path::new(".mda/build"), Path::new("."), &[]),
            [
                SequenceItem::section("Managed app compiled", [".mda/build"]),
                SequenceItem::section("Run locally", ["mda dev"]),
                SequenceItem::section("Deploy when ready", ["mda deploy"]),
            ]
        );
    }

    #[test]
    fn build_result_qualifies_commands_for_an_explicit_project_path() {
        assert_eq!(
            build_result_items(
                Path::new("agents/research/.mda/build"),
                Path::new("agents/research"),
                &[],
            ),
            [
                SequenceItem::section("Managed app compiled", ["agents/research/.mda/build"],),
                SequenceItem::section("Run locally", ["mda dev agents/research"]),
                SequenceItem::section("Deploy when ready", ["mda deploy agents/research"],),
            ]
        );
    }

    #[test]
    fn build_result_keeps_compiler_warnings_in_the_sequence() {
        assert_eq!(
            build_result_items(
                Path::new(".mda/build"),
                Path::new("."),
                &["Public HTTP route found".to_string()],
            ),
            [
                SequenceItem::section("Managed app compiled", [".mda/build"]),
                SequenceItem::section("Build warnings", ["Public HTTP route found"]),
                SequenceItem::section("Run locally", ["mda dev"]),
                SequenceItem::section("Deploy when ready", ["mda deploy"]),
            ]
        );
    }
}
