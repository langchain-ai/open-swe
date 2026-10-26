//! Injecting the managed dependency stack into the build manifest and
//! rewriting the `managed-deepagents` dependency to point at the vendored
//! runtime.

use std::collections::{HashSet, VecDeque};
use std::fs;
use std::path::Path;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use serde_json::json;

use crate::child;
use crate::dependencies::{self, Dependency};
use crate::error::child_failed;
use crate::project::{Language, Project};
use crate::toolchain;

use super::local_packages::{LocalPackage, LOCAL_PACKAGES_DIR};
use super::{RUNTIME_DIR, RUNTIME_PACKAGE};

/// Cap for `npm install --package-lock-only` so a hung registry fails the
/// compile (or live-npm unit test) instead of stalling CI for tens of minutes.
const NPM_LOCKFILE_TIMEOUT: Duration = Duration::from_secs(90);

#[cfg(test)]
thread_local! {
    /// When false (the default under `cargo test`), [`generate_lockfile`]
    /// writes a placeholder lockfile instead of calling the npm registry.
    /// Live-npm tests opt in with [`with_live_npm_lockfile`].
    static LIVE_NPM_LOCKFILE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Run `f` with real `npm install --package-lock-only` lockfile materialization.
///
/// Compile unit tests stub the lockfile by default so the suite does not depend
/// on registry latency. Tests that assert lockfile contents or peer resolution
/// wrap their body in this helper.
#[cfg(test)]
pub(crate) fn with_live_npm_lockfile<R>(f: impl FnOnce() -> R) -> R {
    struct Guard(bool);
    impl Drop for Guard {
        fn drop(&mut self) {
            LIVE_NPM_LOCKFILE.with(|cell| cell.set(self.0));
        }
    }
    let previous = LIVE_NPM_LOCKFILE.with(|cell| cell.replace(true));
    let _guard = Guard(previous);
    f()
}

#[cfg(test)]
fn stub_npm_lockfile() -> bool {
    !LIVE_NPM_LOCKFILE.with(std::cell::Cell::get)
}

#[derive(Default)]
pub(super) struct PythonVersionConfig {
    pub(super) requires_python: Option<String>,
    pub(super) python_version: Option<String>,
}

/// Read compatibility and deployment target from the authored pyproject.toml.
pub(super) fn read_python_version_config(project_root: &Path) -> Result<PythonVersionConfig> {
    let path = project_root.join("pyproject.toml");
    if !path.is_file() {
        return Ok(PythonVersionConfig::default());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let doc = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let read_string = |item: Option<&toml_edit::Item>, field: &str| -> Result<Option<String>> {
        item.map(|item| {
            item.as_str()
                .map(str::to_owned)
                .ok_or_else(|| anyhow!("{field} in {} must be a quoted string", path.display()))
        })
        .transpose()
    };
    let requires_python = doc
        .get("project")
        .and_then(|project| project.get("requires-python"));
    let python_version = doc
        .get("tool")
        .and_then(|tool| tool.get("mda"))
        .and_then(|mda| mda.get("python-version"));
    Ok(PythonVersionConfig {
        requires_python: read_string(requires_python, "[project] requires-python")?,
        python_version: read_string(python_version, "[tool.mda] python-version")?,
    })
}

/// Make a compiled Python build installable as a dependency-only project.
///
/// LangSmith installs the project with `pip install -e .`, which triggers
/// setuptools flat-layout auto-discovery — and that fails on the MDA folder
/// convention (`tools/`, `middleware/`, `skills/` look like competing top-level
/// packages). The agent itself runs from source on `sys.path` (the runtime
/// loads `_mda_entry.py` by path), so the install only needs to bring in
/// dependencies, not package the user's modules.
///
/// Project discovery allows `agent.py` without an authored `pyproject.toml`.
/// When the compile output has no manifest, synthesize a minimal one so
/// dependency injection and `pip install -e .` still work for eval/deploy.
pub(super) fn configure_python_packaging(out_dir: &Path) -> Result<()> {
    let path = out_dir.join("pyproject.toml");
    if !path.is_file() {
        fs::write(
            &path,
            "\
[project]
name = \"mda-agent\"
version = \"0.0.0\"
requires-python = \">=3.11\"
dependencies = []
",
        )
        .with_context(|| format!("failed to write {}", path.display()))?;
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut doc = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;

    // Respect an author-declared build system; only fix the default case.
    if doc.contains_key("build-system") {
        return Ok(());
    }

    let mut requires = toml_edit::Array::new();
    requires.push("setuptools>=61");
    let mut build_system = toml_edit::Table::new();
    build_system["requires"] = toml_edit::value(requires);
    build_system["build-backend"] = toml_edit::value("setuptools.build_meta");
    doc.insert("build-system", toml_edit::Item::Table(build_system));

    // Disable setuptools package auto-discovery: no user modules are installed,
    // only dependencies.
    let setuptools = doc
        .entry("tool")
        .or_insert_with(|| toml_edit::Item::Table(toml_edit::Table::new()))
        .as_table_mut()
        .ok_or_else(|| anyhow!("[tool] in {} is not a table", path.display()))?
        .entry("setuptools")
        .or_insert_with(|| toml_edit::Item::Table(toml_edit::Table::new()))
        .as_table_mut()
        .ok_or_else(|| anyhow!("[tool.setuptools] in {} is not a table", path.display()))?;
    setuptools["py-modules"] = toml_edit::value(toml_edit::Array::new());

    fs::write(&path, doc.to_string()).with_context(|| format!("failed to write {}", path.display()))
}

/// Inject the managed dependency stack into the build manifest.
///
/// Combines the always-required LangChain/LangGraph runtime dependencies with
/// the chat-model provider package inferred from the agent's `model`, then
/// merges them into the copied manifest. An author-declared version always
/// wins, so a power user can still pin or override any package by listing it
/// themselves.
pub(super) fn inject_managed_dependencies(
    project: &Project,
    out_dir: &Path,
    include_local_dev: bool,
    model: Option<&str>,
    model_override: Option<&str>,
    browser: bool,
) -> Result<()> {
    let mut deps: Vec<Dependency> = dependencies::managed_dependencies(project.language).to_vec();
    if browser {
        deps.extend_from_slice(dependencies::browser_dependencies(project.language));
    }
    if include_local_dev {
        deps.extend_from_slice(dependencies::local_dev_dependencies(project.language));
    }
    if let Some(selected_model) = model_override.or(model) {
        if let Some(provider) =
            dependencies::provider_dependency_for_model(selected_model, project.language)
        {
            deps.push(provider);
        }
    }

    match project.language {
        Language::TypeScript => merge_package_json_deps(out_dir, &deps),
        Language::Python => merge_pyproject_deps(out_dir, &deps),
    }
}

/// Where the vendored Python runtime dependency should resolve.
#[derive(Debug, Clone, Copy)]
pub(super) enum RuntimePathMode {
    /// Absolute `file://` URI rooted at the local build output (dev installs).
    AbsoluteDev,
    /// LangSmith deploy layout under `/deps/source`.
    Deploy,
    /// Portable relative URI for eval artifacts uploaded into trial containers.
    Relative,
}

/// Rewrite the `managed-deepagents` dependency for the build language.
///
/// TypeScript builds vendor the runtime under `__runtime__/` and expose it as
/// an npm workspace package instead of a `file:` dependency — hosted npm 11.x
/// has crashed with `Exit handler never called!` when installing `file:` deps.
/// Python builds still point at the vendored `file://` URI.
pub(super) fn rewrite_runtime_dependency(
    language: Language,
    out_dir: &Path,
    mode: RuntimePathMode,
) -> Result<()> {
    match language {
        Language::TypeScript => rewrite_ts_runtime_dep(out_dir),
        Language::Python => rewrite_py_runtime_dep(out_dir, mode),
    }
}

fn rewrite_ts_runtime_dep(out_dir: &Path) -> Result<()> {
    let path = out_dir.join("package.json");
    if !path.is_file() {
        return Ok(());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut manifest: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse {}", path.display()))?;

    let object = manifest
        .as_object_mut()
        .ok_or_else(|| anyhow!("{} root must be a JSON object", path.display()))?;

    // Drop any registry/`file:` entry; the workspace package supplies the name.
    if let Some(deps) = object
        .get_mut("dependencies")
        .and_then(|deps| deps.as_object_mut())
    {
        deps.remove(RUNTIME_PACKAGE);
    }

    ensure_workspace(object, RUNTIME_DIR);
    // Workspace roots are not published; keep npm from treating this as a
    // publishable package when it sees `workspaces`.
    object.insert("private".to_string(), json!(true));

    let mut out = serde_json::to_string_pretty(&manifest)
        .with_context(|| "failed to serialize package.json")?;
    out.push('\n');
    super::write_if_changed(&path, out.as_bytes())
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

/// Ensure `workspaces` lists `entry`.
fn ensure_workspace(object: &mut serde_json::Map<String, serde_json::Value>, entry: &str) {
    let listed = |packages: &[serde_json::Value]| {
        packages.iter().any(|value| {
            value
                .as_str()
                .is_some_and(|value| value == entry || value == format!("./{entry}"))
        })
    };
    match object.get_mut("workspaces") {
        Some(serde_json::Value::Array(packages)) => {
            if !listed(packages) {
                packages.push(json!(entry));
            }
        }
        Some(serde_json::Value::Object(workspaces)) => {
            let packages = workspaces.entry("packages").or_insert_with(|| json!([]));
            if let Some(packages) = packages.as_array_mut() {
                if !listed(packages) {
                    packages.push(json!(entry));
                }
            } else {
                *packages = json!([entry]);
            }
        }
        _ => {
            object.insert("workspaces".to_string(), json!([entry]));
        }
    }
}

fn rewrite_py_runtime_dep(out_dir: &Path, mode: RuntimePathMode) -> Result<()> {
    let path = out_dir.join("pyproject.toml");
    if !path.is_file() {
        return Ok(());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut doc = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;

    // A uv source for the runtime would win over the vendored copy, and its path
    // is relative to the project, not the build.
    if let Some(sources) = doc
        .get_mut("tool")
        .and_then(|tool| tool.get_mut("uv"))
        .and_then(|uv| uv.get_mut("sources"))
        .and_then(|sources| sources.as_table_like_mut())
    {
        let runtime_sources: Vec<String> = sources
            .iter()
            .map(|(name, _)| name.to_string())
            .filter(|name| canonical_requirement_name(name) == RUNTIME_PACKAGE)
            .collect();
        for name in runtime_sources {
            sources.remove(&name);
        }
    }

    let file_uri = match mode {
        RuntimePathMode::AbsoluteDev => {
            let abs = out_dir
                .canonicalize()
                .with_context(|| format!("failed to canonicalize {}", out_dir.display()))?;
            format!(
                "{RUNTIME_PACKAGE} @ file://{}",
                abs.join(RUNTIME_DIR).display()
            )
        }
        RuntimePathMode::Deploy => String::new(),
        RuntimePathMode::Relative => {
            // Absolute-in-container path used by Harbor/Docker eval trials.
            format!("{RUNTIME_PACKAGE} @ file:///app/{RUNTIME_DIR}")
        }
    };

    if let Some(deps) = doc
        .get_mut("project")
        .and_then(|p| p.get_mut("dependencies"))
        .and_then(|d| d.as_array_mut())
    {
        if matches!(mode, RuntimePathMode::Deploy) {
            let mut index = 0;
            while index < deps.len() {
                let is_runtime = deps
                    .get(index)
                    .and_then(toml_edit::Value::as_str)
                    .map(|value| canonical_requirement_name(value) == RUNTIME_PACKAGE)
                    .unwrap_or(false);
                if is_runtime {
                    deps.remove(index);
                } else {
                    index += 1;
                }
            }
            return fs::write(&path, doc.to_string())
                .with_context(|| format!("failed to write {}", path.display()));
        }

        // Replace any existing managed-deepagents entry (matching equivalent
        // PEP 503 spellings) with the vendored file:// URI, or add it if the
        // author never declared it.
        let mut found = false;
        for item in deps.iter_mut() {
            if item
                .as_str()
                .map(|s| canonical_requirement_name(s) == RUNTIME_PACKAGE)
                .unwrap_or(false)
            {
                *item = toml_edit::Value::from(&file_uri);
                found = true;
                break;
            }
        }
        if !found {
            deps.push(&file_uri);
        }
    }

    fs::write(&path, doc.to_string()).with_context(|| format!("failed to write {}", path.display()))
}

/// Point the build manifest at the local packages vendored under `__local__/`.
///
/// TypeScript links each copy as an npm workspace, like the runtime. Python
/// dev builds use an editable uv path source; eval builds use a container
/// `file://` URI; deploy builds drop the requirement because `langgraph.json`
/// installs the copy as its own local dependency.
pub(super) fn rewrite_local_package_dependencies(
    language: Language,
    out_dir: &Path,
    packages: &[LocalPackage],
    mode: RuntimePathMode,
) -> Result<()> {
    if packages.is_empty() {
        return Ok(());
    }
    match language {
        Language::TypeScript => rewrite_ts_local_deps(out_dir, packages),
        Language::Python => rewrite_py_local_deps(out_dir, packages, mode),
    }
}

fn rewrite_ts_local_deps(out_dir: &Path, packages: &[LocalPackage]) -> Result<()> {
    let path = out_dir.join("package.json");
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut manifest: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let object = manifest
        .as_object_mut()
        .ok_or_else(|| anyhow!("{} root must be a JSON object", path.display()))?;
    for package in packages {
        if let Some(deps) = object
            .get_mut("dependencies")
            .and_then(|deps| deps.as_object_mut())
        {
            deps.remove(&package.name);
        }
        ensure_workspace(object, &package.build_dir());
    }
    object.insert("private".to_string(), json!(true));

    let mut out = serde_json::to_string_pretty(&manifest)
        .with_context(|| "failed to serialize package.json")?;
    out.push('\n');
    super::write_if_changed(&path, out.as_bytes())
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

fn rewrite_py_local_deps(
    out_dir: &Path,
    packages: &[LocalPackage],
    mode: RuntimePathMode,
) -> Result<()> {
    let path = out_dir.join("pyproject.toml");
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let direct: Vec<&str> = packages
        .iter()
        .filter(|package| package.direct)
        .map(|package| package.name.as_str())
        .collect();
    let rewritten = point_py_requirements(&contents, &direct, mode, LOCAL_PACKAGES_DIR)
        .with_context(|| format!("failed to rewrite {}", path.display()))?;
    fs::write(&path, rewritten).with_context(|| format!("failed to write {}", path.display()))
}

/// Rewrite the manifest of a vendored local package so its own local packages
/// resolve to their sibling copies under `__local__/`.
pub(super) fn rewrite_vendored_manifest(
    language: Language,
    contents: &str,
    package: &LocalPackage,
    mode: RuntimePathMode,
) -> Result<String> {
    let names: Vec<&str> = package
        .local_dependencies
        .iter()
        .map(String::as_str)
        .collect();
    match language {
        Language::TypeScript => {
            let mut manifest: serde_json::Value = serde_json::from_str(contents)?;
            if let Some(deps) = manifest
                .get_mut("dependencies")
                .and_then(|deps| deps.as_object_mut())
            {
                for name in &names {
                    deps.remove(*name);
                }
            }
            let mut out = serde_json::to_string_pretty(&manifest)?;
            out.push('\n');
            Ok(out)
        }
        Language::Python => {
            let rewritten = point_py_requirements(contents, &names, mode, "..")?;
            match mode {
                RuntimePathMode::Deploy if !package.extras.is_empty() => {
                    fold_requested_extras(&rewritten, package)
                }
                _ => Ok(rewritten),
            }
        }
    }
}

/// Point the uv path sources named in `names` at their vendored copies.
///
/// Dev builds keep the requirement and use an editable uv source under
/// `dev_dir`. Eval builds use a container `file://` URI. Deploy builds drop the
/// requirement because `langgraph.json` installs each copy separately.
fn point_py_requirements(
    contents: &str,
    names: &[&str],
    mode: RuntimePathMode,
    dev_dir: &str,
) -> Result<String> {
    let mut doc = contents.parse::<toml_edit::DocumentMut>()?;
    for name in names {
        let sources = doc
            .get_mut("tool")
            .and_then(|tool| tool.get_mut("uv"))
            .and_then(|uv| uv.get_mut("sources"))
            .and_then(|sources| sources.as_table_like_mut())
            .ok_or_else(|| anyhow!("[tool.uv.sources] is not a table"))?;
        if let RuntimePathMode::AbsoluteDev = mode {
            let mut source = toml_edit::InlineTable::new();
            source.insert("path", format!("{dev_dir}/{name}").into());
            source.insert("editable", true.into());
            sources.insert(name, toml_edit::value(source));
            continue;
        }
        sources.remove(name);

        let Some(deps) = doc
            .get_mut("project")
            .and_then(|project| project.get_mut("dependencies"))
            .and_then(|deps| deps.as_array_mut())
        else {
            continue;
        };
        let canonical = canonical_requirement_name(name);
        let mut found = false;
        let mut index = 0;
        while index < deps.len() {
            let Some(requirement) = deps.get(index).and_then(|dep| dep.as_str()) else {
                index += 1;
                continue;
            };
            if canonical_requirement_name(requirement) != canonical {
                index += 1;
                continue;
            }
            found = true;
            let marker = requirement_marker(requirement);
            if let RuntimePathMode::Relative = mode {
                let extras = requirement_extras(requirement);
                let extras = if extras.is_empty() {
                    String::new()
                } else {
                    format!("[{}]", extras.join(","))
                };
                let marker = marker
                    .map(|marker| format!(" ; {marker}"))
                    .unwrap_or_default();
                deps.replace(
                    index,
                    format!("{name}{extras} @ file:///app/{LOCAL_PACKAGES_DIR}/{name}{marker}"),
                );
                index += 1;
                continue;
            }
            if let Some(marker) = marker {
                bail!(
                    "mda deploy always installs local package `{name}`, so it cannot honor the \
                     marker `{marker}`. Remove the marker from `{requirement}`."
                );
            }
            deps.remove(index);
        }
        if !found {
            if let RuntimePathMode::Relative = mode {
                deps.push(format!("{name} @ file:///app/{LOCAL_PACKAGES_DIR}/{name}"));
            }
        }
    }
    Ok(doc.to_string())
}

/// Add the requested extras of a vendored Python package to its own
/// dependencies. Deploy installs the copy as a bare `langgraph.json` path,
/// which cannot select extras.
fn fold_requested_extras(contents: &str, package: &LocalPackage) -> Result<String> {
    let mut doc = contents.parse::<toml_edit::DocumentMut>()?;
    let own = canonical_requirement_name(&package.name);
    let optional: Vec<(String, Vec<String>)> = doc
        .get("project")
        .and_then(|project| project.get("optional-dependencies"))
        .and_then(|optional| optional.as_table_like())
        .map(|optional| {
            optional
                .iter()
                .map(|(extra, requirements)| {
                    let requirements = requirements
                        .as_array()
                        .map(|array| {
                            array
                                .iter()
                                .filter_map(|value| value.as_str().map(str::to_string))
                                .collect()
                        })
                        .unwrap_or_default();
                    (
                        extra.to_ascii_lowercase().replace(['_', '.'], "-"),
                        requirements,
                    )
                })
                .collect()
        })
        .unwrap_or_default();

    let mut pending: VecDeque<String> = package.extras.iter().cloned().collect();
    let mut seen = HashSet::new();
    let mut added = Vec::new();
    while let Some(extra) = pending.pop_front() {
        if !seen.insert(extra.clone()) {
            continue;
        }
        let (_, requirements) = optional
            .iter()
            .find(|(name, _)| *name == extra)
            .ok_or_else(|| anyhow!("local package `{}` has no extra `{extra}`", package.name))?;
        for requirement in requirements {
            if canonical_requirement_name(requirement) == own {
                pending.extend(requirement_extras(requirement));
            } else {
                added.push(requirement.clone());
            }
        }
    }

    let deps = doc
        .entry("project")
        .or_insert_with(|| toml_edit::Item::Table(toml_edit::Table::new()))
        .as_table_like_mut()
        .ok_or_else(|| anyhow!("[project] is not a table"))?
        .entry("dependencies")
        .or_insert(toml_edit::value(toml_edit::Array::new()))
        .as_array_mut()
        .ok_or_else(|| anyhow!("[project].dependencies is not an array"))?;
    for requirement in added {
        if !deps
            .iter()
            .any(|dep| dep.as_str() == Some(requirement.as_str()))
        {
            deps.push(requirement);
        }
    }
    Ok(doc.to_string())
}

/// Merge `deps` into the build's `package.json`, adding only packages the
/// author has not already declared. The dependency map is rewritten in sorted
/// order for stable, diff-friendly output.
fn merge_package_json_deps(out_dir: &Path, deps: &[Dependency]) -> Result<()> {
    let path = out_dir.join("package.json");
    if !path.is_file() {
        // No manifest to augment. A TypeScript build needs one to install
        // against, but that is reported by the downstream langgraph build; the
        // compile step should not invent a manifest out of nothing here.
        return Ok(());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut manifest: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let deps_obj = manifest
        .as_object_mut()
        .ok_or_else(|| anyhow!("{} is not a JSON object", path.display()))?
        .entry("dependencies")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| anyhow!("\"dependencies\" in {} is not an object", path.display()))?;

    for dep in deps {
        deps_obj
            .entry(dep.name.to_string())
            .or_insert_with(|| json!(dep.version));
    }
    deps_obj.sort_keys();

    // `deepagents` still peers `langsmith@^0.7.1`. Mirror the build's
    // langsmith pin into `overrides` so `npm install --package-lock-only`
    // (and Host `npm ci`) can resolve until that peer range widens.
    ensure_langsmith_npm_override(&mut manifest)?;

    let mut out = serde_json::to_string_pretty(&manifest)
        .with_context(|| "failed to serialize package.json")?;
    out.push('\n');
    super::write_if_changed(&path, out.as_bytes())
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

/// Set `overrides.langsmith` to the same range as `dependencies.langsmith`.
///
/// No-op when the build has no langsmith dependency. Overwrites any prior
/// langsmith override so it cannot drift from the direct dependency (npm
/// rejects that with `EOVERRIDE`).
fn ensure_langsmith_npm_override(manifest: &mut serde_json::Value) -> Result<()> {
    let Some(langsmith) = manifest
        .pointer("/dependencies/langsmith")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
    else {
        return Ok(());
    };
    let object = manifest
        .as_object_mut()
        .ok_or_else(|| anyhow!("package.json root must be a JSON object"))?;
    let overrides = object
        .entry("overrides")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| anyhow!("\"overrides\" in package.json is not an object"))?;
    overrides.insert("langsmith".to_string(), json!(langsmith));
    Ok(())
}

/// Merge `deps` into the build's `pyproject.toml` `[project].dependencies`,
/// adding only packages the author has not already declared.
fn merge_pyproject_deps(out_dir: &Path, deps: &[Dependency]) -> Result<()> {
    let path = out_dir.join("pyproject.toml");
    if !path.is_file() {
        return Ok(());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut doc = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;
    // Get or create `[project].dependencies`: a freshly scaffolded project may
    // have a `[project]` table without a `dependencies` array yet.
    let project = doc
        .entry("project")
        .or_insert_with(|| toml_edit::Item::Table(toml_edit::Table::new()))
        .as_table_mut()
        .ok_or_else(|| anyhow!("[project] in {} is not a table", path.display()))?;
    let array = project
        .entry("dependencies")
        .or_insert_with(|| toml_edit::value(toml_edit::Array::new()))
        .as_array_mut()
        .ok_or_else(|| {
            anyhow!(
                "[project].dependencies in {} is not an array",
                path.display()
            )
        })?;

    // Whether the author's array spans multiple lines, so injected entries can
    // match that style instead of being crammed onto the closing-bracket line.
    let multiline = array.iter().count() == 0 || array.to_string().contains('\n');
    // The previous last element often carries the pre-`]` newline as its suffix
    // decor, which would otherwise push the separating comma onto its own line.
    if let Some(last) = array.len().checked_sub(1).and_then(|i| array.get_mut(i)) {
        last.decor_mut().set_suffix("");
    }
    let mut appended = false;
    for dep in deps {
        // Compare PEP 503-canonical names so an author-declared package is
        // matched regardless of `-`/`_`/`.` or case differences in its spelling.
        let dep_name = canonical_requirement_name(dep.name);
        let already_declared = array.iter().filter_map(|value| value.as_str()).any(|req| {
            canonical_requirement_name(req) == dep_name
                && requirement_extras(dep.name)
                    .iter()
                    .all(|extra| requirement_extras(req).contains(extra))
        });
        if already_declared {
            continue;
        }
        let mut value = toml_edit::Value::from(format!("{}{}", dep.name, dep.version));
        if multiline {
            value.decor_mut().set_prefix("\n    ");
        } else {
            value.decor_mut().set_prefix(" ");
        }
        array.push_formatted(value);
        appended = true;
    }
    if appended && multiline {
        // Drop the closing bracket onto its own line for a tidy array.
        array.set_trailing("\n");
        array.set_trailing_comma(false);
    }

    fs::write(&path, doc.to_string()).with_context(|| format!("failed to write {}", path.display()))
}

/// Extract the package name from a PEP 508 requirement string (the leading
/// run of name characters before any version specifier or extras).
fn requirement_name(requirement: &str) -> String {
    requirement
        .trim_start()
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        .collect()
}

/// PEP 503-style canonical package name used to compare requirement strings:
/// the leading name lowercased with `_` and `.` folded to `-`. This lets the
/// dependency dedup treat `langchain_openai`, `langchain-openai`, and
/// `Langchain.OpenAI` as the same package, so an author who declares a managed
/// package under any equivalent spelling never gets a duplicate injected.
pub(super) fn canonical_requirement_name(requirement: &str) -> String {
    requirement_name(requirement)
        .to_ascii_lowercase()
        .replace(['_', '.'], "-")
}

/// Environment marker after `;`, if any.
fn requirement_marker(requirement: &str) -> Option<&str> {
    requirement
        .split_once(';')
        .map(|(_, marker)| marker.trim())
        .filter(|marker| !marker.is_empty())
}

/// Extras are additive: a bare author pin does not supply managed runtime extras.
pub(super) fn requirement_extras(requirement: &str) -> Vec<String> {
    let name = requirement_name(requirement);
    let rest = requirement.trim_start()[name.len()..].trim_start();
    let Some(extras) = rest.strip_prefix('[').and_then(|s| s.split_once(']')) else {
        return Vec::new();
    };
    extras
        .0
        .split(',')
        .map(|extra| extra.trim().to_ascii_lowercase().replace(['_', '.'], "-"))
        .collect()
}

/// Drop author `devDependencies` from the deploy build's `package.json`.
///
/// Hosted builds run `npm ci` with no `--omit=dev`, so UI tooling (Vite,
/// wrangler, Tailwind) would otherwise be installed on Linux. Those packages
/// pull optional `@emnapi/*` peers that macOS lockfile generation often omits,
/// which then fails remote `npm ci`. The agent runtime does not need them.
pub(super) fn strip_package_json_dev_dependencies(out_dir: &Path) -> Result<()> {
    let path = out_dir.join("package.json");
    if !path.is_file() {
        return Ok(());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let mut manifest: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let Some(object) = manifest.as_object_mut() else {
        return Ok(());
    };
    if object.remove("devDependencies").is_none() {
        return Ok(());
    }
    let mut out = serde_json::to_string_pretty(&manifest)
        .with_context(|| "failed to serialize package.json")?;
    out.push('\n');
    fs::write(&path, out).with_context(|| format!("failed to write {}", path.display()))
}

/// Marker recording the input that produced the current lockfile.
///
/// TypeScript: the `package.json` bytes, plus vendored local package manifests,
/// that generated `package-lock.json`.
/// Watch rebuilds skip `npm install --package-lock-only` when this matches.
///
/// Python: the author's `uv.lock` bytes last copied into the build. Watch
/// rebuilds skip recopying that lock (which `uv sync` then rewrites) unless
/// the author edited it.
pub(super) const LOCK_INPUT_MARKER: &str = ".mda-lock-input";

/// Run `npm install --package-lock-only` in the build output to generate a
/// `package-lock.json` that matches the rewritten `package.json`. The remote
/// builder runs `npm ci`, which requires a lockfile.
///
/// Skips when `package-lock.json` already exists and was generated from the
/// same manifest bytes (see [`LOCK_INPUT_MARKER`]).
///
/// Under `cargo test`, writes a placeholder lockfile unless the caller opted
/// into live npm via [`with_live_npm_lockfile`]. Production builds always hit
/// the registry (with [`NPM_LOCKFILE_TIMEOUT`]).
pub(super) fn generate_lockfile(out_dir: &Path) -> Result<()> {
    let pkg_path = out_dir.join("package.json");
    if !pkg_path.is_file() {
        return Ok(());
    }
    // npm regenerates into an existing `npm-shrinkwrap.json`, so drop it and the
    // marker that may vouch for a lock npm never wrote.
    if remove_if_present(&out_dir.join("npm-shrinkwrap.json"))? {
        remove_if_present(&out_dir.join(LOCK_INPUT_MARKER))?;
    }
    let mut pkg =
        fs::read(&pkg_path).with_context(|| format!("failed to read {}", pkg_path.display()))?;
    for manifest in super::vendored_manifests(out_dir, "package.json") {
        let path = out_dir.join(&manifest);
        pkg.extend(manifest.to_string_lossy().as_bytes());
        pkg.extend(fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?);
    }
    let lock_path = out_dir.join("package-lock.json");
    let marker = out_dir.join(LOCK_INPUT_MARKER);
    if lock_path.is_file() && fs::read(&marker).ok().as_ref() == Some(&pkg) {
        return Ok(());
    }

    #[cfg(test)]
    if stub_npm_lockfile() {
        return write_stub_lockfile(out_dir, &pkg);
    }

    materialize_npm_lockfile(out_dir, &pkg)
}

/// Placeholder lockfile for unit tests that only need the file to exist.
#[cfg(test)]
fn write_stub_lockfile(out_dir: &Path, pkg: &[u8]) -> Result<()> {
    // Minimal lockfileVersion 3 document. Deliberately omits registry packages
    // so accidental assertions on resolved deps fail closed under the stub.
    const STUB: &str = "{\n  \"name\": \"mda-test-stub\",\n  \"lockfileVersion\": 3,\n  \"requires\": true,\n  \"packages\": {\n    \"\": {\n      \"name\": \"mda-test-stub\"\n    }\n  }\n}\n";
    let lock_path = out_dir.join("package-lock.json");
    let marker = out_dir.join(LOCK_INPUT_MARKER);
    fs::write(&lock_path, STUB)
        .with_context(|| format!("failed to write {}", lock_path.display()))?;
    fs::write(&marker, pkg).with_context(|| format!("failed to write {}", marker.display()))?;
    Ok(())
}

fn materialize_npm_lockfile(out_dir: &Path, pkg: &[u8]) -> Result<()> {
    const NPM_LOCK_ARGS: &[&str] = &["install", "--package-lock-only", "--include=optional"];
    let command = format!("{} {}", toolchain::npm_program(), NPM_LOCK_ARGS.join(" "));
    let mut npm = toolchain::npm();
    npm.args(NPM_LOCK_ARGS).current_dir(out_dir);
    let output = child::output_with_timeout(&mut npm, NPM_LOCKFILE_TIMEOUT)
        .with_context(|| format!("failed to run {command}"))?;

    if !output.status.success() {
        return Err(child_failed(&output, format!("{command} failed")));
    }
    let marker = out_dir.join(LOCK_INPUT_MARKER);
    fs::write(&marker, pkg).with_context(|| format!("failed to write {}", marker.display()))?;
    Ok(())
}

/// Delete `path` if it exists; `Ok(false)` when it was already absent.
fn remove_if_present(path: &Path) -> Result<bool> {
    match fs::remove_file(path) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error).with_context(|| format!("failed to remove {}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_managed_extras_are_not_suppressed_by_an_author_pin() {
        for (authored, injected) in [
            ("langchain==1.4.3", true),
            ("LangChain[other]==1.4.3", true),
            ("langchain[mcp]==1.4.3", false),
            ("LangChain[other, MCP]==1.4.3", false),
        ] {
            let root = tempfile::tempdir().unwrap();
            crate::test_support::write_file(
                root.path(),
                "pyproject.toml",
                &format!("[project]\ndependencies = [{authored:?}]\n"),
            );
            let dependency = Dependency {
                name: "langchain[mcp]",
                version: ">=1.4.3,<2",
            };
            merge_pyproject_deps(root.path(), &[dependency]).unwrap();
            // Repeated compilation neither rewrites the pin nor duplicates extras.
            merge_pyproject_deps(root.path(), &[dependency]).unwrap();
            let doc: toml_edit::DocumentMut =
                fs::read_to_string(root.path().join("pyproject.toml"))
                    .unwrap()
                    .parse()
                    .unwrap();
            let deps = doc["project"]["dependencies"].as_array().unwrap();
            assert_eq!(deps.get(0).unwrap().as_str(), Some(authored));
            assert_eq!(deps.len(), if injected { 2 } else { 1 });
            if injected {
                assert_eq!(
                    deps.get(1).unwrap().as_str(),
                    Some("langchain[mcp]>=1.4.3,<2")
                );
            }
        }
    }

    #[test]
    fn python_version_config_reads_optional_fields_and_rejects_non_strings() {
        let root = tempfile::tempdir().unwrap();
        let config = read_python_version_config(root.path()).unwrap();
        assert!(config.python_version.is_none());
        assert!(config.requires_python.is_none());

        for source in ["[project]\n", "[tool.mda]\nother = true\n"] {
            crate::test_support::write_file(root.path(), "pyproject.toml", source);
            let config = read_python_version_config(root.path()).unwrap();
            assert!(config.python_version.is_none());
            assert!(config.requires_python.is_none());
        }
        for (source, field) in [
            (
                "[project]\nrequires-python = 3.11\n",
                "[project] requires-python",
            ),
            (
                "[tool.mda]\npython-version = 3.14\n",
                "[tool.mda] python-version",
            ),
        ] {
            crate::test_support::write_file(root.path(), "pyproject.toml", source);
            let error = read_python_version_config(root.path())
                .err()
                .unwrap()
                .to_string();
            assert!(error.contains(field));
            assert!(error.contains("must be a quoted string"));
        }
    }

    #[test]
    fn generate_lockfile_reruns_when_a_local_package_manifest_changes() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path();
        crate::test_support::write_file(
            out_dir,
            "package.json",
            r#"{"name":"x","workspaces":["__local__/dep"]}"#,
        );
        crate::test_support::write_file(out_dir, "__local__/dep/package.json", r#"{"name":"dep"}"#);
        generate_lockfile(out_dir).unwrap();
        fs::write(out_dir.join("package-lock.json"), "sentinel").unwrap();

        generate_lockfile(out_dir).unwrap();
        assert_eq!(
            fs::read_to_string(out_dir.join("package-lock.json")).unwrap(),
            "sentinel"
        );

        crate::test_support::write_file(
            out_dir,
            "__local__/dep/package.json",
            r#"{"name":"dep","dependencies":{"zod":"^3.0.0"}}"#,
        );
        generate_lockfile(out_dir).unwrap();
        assert_ne!(
            fs::read_to_string(out_dir.join("package-lock.json")).unwrap(),
            "sentinel"
        );
    }

    #[test]
    fn generate_lockfile_skips_when_package_json_matches_marker() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path();
        let pkg = r#"{"name":"x","private":true,"dependencies":{}}"#;
        fs::write(out_dir.join("package.json"), pkg).unwrap();
        fs::write(
            out_dir.join("package-lock.json"),
            "{\"lockfileVersion\":3}\n",
        )
        .unwrap();
        fs::write(out_dir.join(LOCK_INPUT_MARKER), pkg).unwrap();
        let lock_before = fs::read(out_dir.join("package-lock.json")).unwrap();

        // Would fail if it tried to run npm against this incomplete manifest.
        generate_lockfile(out_dir).unwrap();

        assert_eq!(
            fs::read(out_dir.join("package-lock.json")).unwrap(),
            lock_before
        );
    }

    #[test]
    fn generate_lockfile_stubs_under_tests_without_live_npm() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path();
        let pkg = r#"{"name":"x","private":true,"dependencies":{"langchain":"^1.0.0"}}"#;
        fs::write(out_dir.join("package.json"), pkg).unwrap();

        generate_lockfile(out_dir).unwrap();

        let lock = fs::read_to_string(out_dir.join("package-lock.json")).unwrap();
        assert!(lock.contains("mda-test-stub"));
        assert!(!lock.contains("node_modules/langchain"));
        assert_eq!(
            fs::read(out_dir.join(LOCK_INPUT_MARKER)).unwrap(),
            pkg.as_bytes()
        );
    }

    // An older CLI let npm regenerate into a copied shrinkwrap and then recorded
    // the marker, so a matching marker can vouch for a stale package lock.
    #[test]
    fn generate_lockfile_regenerates_after_removing_a_stale_shrinkwrap() {
        let dir = tempfile::tempdir().unwrap();
        let out_dir = dir.path();
        let pkg = r#"{"name":"x","private":true,"dependencies":{}}"#;
        fs::write(out_dir.join("package.json"), pkg).unwrap();
        fs::write(out_dir.join("package-lock.json"), "stale\n").unwrap();
        fs::write(out_dir.join(LOCK_INPUT_MARKER), pkg).unwrap();
        fs::write(
            out_dir.join("npm-shrinkwrap.json"),
            "{\"lockfileVersion\":3}\n",
        )
        .unwrap();

        generate_lockfile(out_dir).unwrap();

        assert!(!out_dir.join("npm-shrinkwrap.json").exists());
        let lock = fs::read_to_string(out_dir.join("package-lock.json")).unwrap();
        assert!(lock.contains("mda-test-stub"));
        assert_eq!(
            fs::read(out_dir.join(LOCK_INPUT_MARKER)).unwrap(),
            pkg.as_bytes()
        );
    }

    // Empty dependencies keep npm off the registry, so this live run is cheap.
    #[test]
    fn generate_lockfile_writes_the_package_lock_with_live_npm_after_a_shrinkwrap() {
        if !toolchain::npm_available() {
            return;
        }
        const OLD_LOCK: &str = r#"{"name":"old","version":"1.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"old","version":"1.0.0"}}}"#;
        with_live_npm_lockfile(|| {
            for stale_lock in [Some(OLD_LOCK), None] {
                let dir = tempfile::tempdir().unwrap();
                let out_dir = dir.path();
                let pkg =
                    r#"{"name":"current","version":"2.0.0","private":true,"dependencies":{}}"#;
                fs::write(out_dir.join("package.json"), pkg).unwrap();
                fs::write(out_dir.join("npm-shrinkwrap.json"), OLD_LOCK).unwrap();
                fs::write(out_dir.join(LOCK_INPUT_MARKER), pkg).unwrap();
                if let Some(stale) = stale_lock {
                    fs::write(out_dir.join("package-lock.json"), stale).unwrap();
                }

                generate_lockfile(out_dir).unwrap();

                assert!(!out_dir.join("npm-shrinkwrap.json").exists());
                let lock: serde_json::Value = serde_json::from_str(
                    &fs::read_to_string(out_dir.join("package-lock.json")).unwrap(),
                )
                .unwrap();
                assert_eq!(lock["name"], "current", "stale lock: {stale_lock:?}");
                assert_eq!(lock["version"], "2.0.0", "stale lock: {stale_lock:?}");
            }
        });
    }

    #[test]
    fn ensure_workspace_adds_array_entry() {
        let mut object = serde_json::Map::new();
        ensure_workspace(&mut object, RUNTIME_DIR);
        assert_eq!(object.get("workspaces"), Some(&json!(["__runtime__"])));
    }

    #[test]
    fn ensure_workspace_merges_into_existing_array() {
        let mut object = serde_json::Map::new();
        object.insert("workspaces".to_string(), json!(["packages/*"]));
        ensure_workspace(&mut object, RUNTIME_DIR);
        assert_eq!(
            object.get("workspaces"),
            Some(&json!(["packages/*", "__runtime__"]))
        );
    }

    #[test]
    fn ensure_workspace_merges_into_packages_object() {
        let mut object = serde_json::Map::new();
        object.insert(
            "workspaces".to_string(),
            json!({ "packages": ["packages/*"] }),
        );
        ensure_workspace(&mut object, RUNTIME_DIR);
        assert_eq!(
            object.get("workspaces"),
            Some(&json!({ "packages": ["packages/*", "__runtime__"] }))
        );
    }

    #[test]
    fn langsmith_override_mirrors_the_direct_dependency() {
        let mut manifest = json!({
            "dependencies": { "langsmith": "^0.8.9", "zod": "^4.0.0" },
            "overrides": { "other": "1.0.0" }
        });
        ensure_langsmith_npm_override(&mut manifest).unwrap();
        assert_eq!(
            manifest.pointer("/overrides/langsmith"),
            Some(&json!("^0.8.9"))
        );
        assert_eq!(manifest.pointer("/overrides/other"), Some(&json!("1.0.0")));
    }

    #[test]
    fn langsmith_override_is_a_noop_without_the_dependency() {
        let mut manifest = json!({ "dependencies": { "zod": "^4.0.0" } });
        ensure_langsmith_npm_override(&mut manifest).unwrap();
        assert!(manifest.get("overrides").is_none());
    }

    #[test]
    fn langsmith_override_replaces_a_stale_override() {
        let mut manifest = json!({
            "dependencies": { "langsmith": "^0.8.9" },
            "overrides": { "langsmith": "^0.7.16" }
        });
        ensure_langsmith_npm_override(&mut manifest).unwrap();
        assert_eq!(
            manifest.pointer("/overrides/langsmith"),
            Some(&json!("^0.8.9"))
        );
    }
}
