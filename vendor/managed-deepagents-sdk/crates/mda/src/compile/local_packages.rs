//! Local packages that live outside the project root.
//!
//! Authors declare them the usual way: `npm install ../dep` writes
//! `"dep": "file:../dep"` and `uv add ../dep` writes a `[tool.uv.sources]`
//! path. The build only carries files under the project root, so each package
//! is copied into `__local__/<name>/` and the build manifests point at the copy.
//! Local packages of local packages are copied the same way.

use std::collections::{HashSet, VecDeque};
use std::fs;
use std::path::{Component, Path, PathBuf};

use anyhow::{anyhow, bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::Statement;
use walkdir::WalkDir;

use crate::project::{self, Language, Project};
use crate::typescript::parse_typescript_recovered;

use super::manifest::{
    canonical_requirement_name, requirement_extras, rewrite_vendored_manifest, RuntimePathMode,
};
use super::runtime::copy_file_if_changed;
use super::{write_if_changed, RUNTIME_PACKAGE};

pub(super) const LOCAL_PACKAGES_DIR: &str = "__local__";

const SKIPPED_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    ".mda",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct LocalPackage {
    pub(crate) name: String,
    pub(crate) source: PathBuf,
    /// Declared by the project manifest, not only by another local package.
    pub(super) direct: bool,
    /// Names of the local packages this package declares.
    pub(super) local_dependencies: Vec<String>,
    /// Python extras requested by the manifests that declare this package.
    pub(super) extras: Vec<String>,
}

impl LocalPackage {
    pub(super) fn build_dir(&self) -> String {
        format!("{LOCAL_PACKAGES_DIR}/{}", self.name)
    }
}

/// Local packages declared in the project manifest that resolve outside the
/// project root, followed by the local packages they declare. Packages inside
/// the root are already part of the build.
pub(crate) fn find_local_packages(project: &Project) -> Result<Vec<LocalPackage>> {
    let root = project
        .root
        .canonicalize()
        .with_context(|| format!("failed to resolve {}", project.root.display()))?;
    let mut pending: VecDeque<(PathBuf, String, String, bool)> =
        declared_local_paths(project.language, &project.root)?
            .into_iter()
            .map(|(name, path)| (project.root.clone(), name, path, true))
            .collect();
    let mut packages: Vec<LocalPackage> = Vec::new();
    while let Some((declared_in, name, path, direct)) = pending.pop_front() {
        let source = declared_in.join(&path).canonicalize().map_err(|_| {
            anyhow!("local package `{name}` points to `{path}`, which does not exist")
        })?;
        if direct && source.starts_with(&root) {
            // Already part of the build, so its manifest is copied as written
            // and a path outside the project would still point outside the build.
            let inside = source.strip_prefix(&root).unwrap_or(&source).to_path_buf();
            for (nested, nested_path) in declared_local_paths(project.language, &source)? {
                let outside = source
                    .join(&nested_path)
                    .canonicalize()
                    .is_ok_and(|nested| !nested.starts_with(&root));
                if outside {
                    bail!(
                        "local package `{nested}` at `{nested_path}` is declared by `{}`, \
                         which is inside the project, but resolves outside it; move `{nested}` \
                         into the project",
                        inside.display()
                    );
                }
            }
            continue;
        }
        if !source.is_dir() {
            bail!("local package `{name}` points to `{path}`, which is not a directory");
        }
        if !Path::new(&name)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
        {
            bail!("local package name `{name}` is not a valid package name");
        }
        let manifest = project.language.manifest_name();
        if !source.join(manifest).is_file() {
            bail!("local package `{name}` at `{path}` has no {manifest}");
        }
        // The copy is linked under its own name, so an alias would not resolve.
        let actual = manifest_package_name(project.language, &source)?;
        let same_name = match project.language {
            Language::TypeScript => actual == name,
            Language::Python => {
                canonical_requirement_name(&actual) == canonical_requirement_name(&name)
            }
        };
        if !same_name {
            bail!(
                "local package `{name}` at `{path}` is named `{actual}` in its {manifest}; \
                 declare it as `{actual}`"
            );
        }
        let extras = requested_extras(project.language, &declared_in, &name)?;
        if let Some(existing) = packages.iter_mut().find(|package| package.source == source) {
            for extra in extras {
                if !existing.extras.contains(&extra) {
                    existing.extras.push(extra);
                }
            }
            continue;
        }
        if let Some(other) = packages.iter().find(|package| package.name == name) {
            bail!(
                "two different local packages are named `{name}`: {} and {}",
                other.source.display(),
                source.display()
            );
        }
        let nested = declared_local_paths(project.language, &source)?;
        let local_dependencies = nested.iter().map(|(name, _)| name.clone()).collect();
        pending.extend(
            nested
                .into_iter()
                .map(|(name, path)| (source.clone(), name, path, false)),
        );
        packages.push(LocalPackage {
            name,
            source,
            direct,
            local_dependencies,
            extras,
        });
    }
    Ok(packages)
}

/// Copy each local package into the build and record the copied files in `keep`.
///
/// A package that declares local packages gets its manifest rewritten to
/// point at their copies.
pub(super) fn vendor_local_packages(
    language: Language,
    packages: &[LocalPackage],
    out_dir: &Path,
    mode: RuntimePathMode,
    keep: &mut HashSet<PathBuf>,
) -> Result<()> {
    for package in packages {
        let build_dir = PathBuf::from(package.build_dir());
        let manifest = Path::new(language.manifest_name());
        for entry in WalkDir::new(&package.source)
            .into_iter()
            .filter_entry(|entry| entry.depth() == 0 || !is_skipped_dir(entry.path()))
        {
            let entry = entry.with_context(|| {
                format!("failed to read local package {}", package.source.display())
            })?;
            if !entry.file_type().is_file() {
                continue;
            }
            let rel = entry.path().strip_prefix(&package.source)?;
            if project::is_ignored_file(rel) {
                continue;
            }
            let dst = out_dir.join(&build_dir).join(rel);
            if let Some(parent) = dst.parent() {
                fs::create_dir_all(parent)
                    .with_context(|| format!("failed to create {}", parent.display()))?;
            }
            if rel == manifest
                && !(package.local_dependencies.is_empty() && package.extras.is_empty())
            {
                let contents = fs::read_to_string(entry.path())
                    .with_context(|| format!("failed to read {}", entry.path().display()))?;
                let rewritten = rewrite_vendored_manifest(language, &contents, package, mode)
                    .with_context(|| format!("failed to rewrite {}", entry.path().display()))?;
                write_if_changed(&dst, rewritten.as_bytes())
                    .with_context(|| format!("failed to write {}", dst.display()))?;
            } else {
                copy_file_if_changed(entry.path(), &dst)?;
            }
            keep.insert(build_dir.join(rel));
        }
    }
    Ok(())
}

/// Manifests named `file_name` of the packages vendored into `out_dir`,
/// relative to `out_dir`.
pub(crate) fn vendored_manifests(out_dir: &Path, file_name: &str) -> Vec<PathBuf> {
    let mut manifests: Vec<PathBuf> = WalkDir::new(out_dir.join(LOCAL_PACKAGES_DIR))
        .min_depth(2)
        .max_depth(3)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file() && entry.file_name() == file_name)
        .filter_map(|entry| {
            entry
                .path()
                .strip_prefix(out_dir)
                .ok()
                .map(Path::to_path_buf)
        })
        .collect();
    manifests.sort();
    manifests
}

/// Whether a change at `rel` inside a local package affects the build.
pub(crate) fn is_vendored_path(rel: &Path) -> bool {
    !rel.components().any(|component| match component {
        Component::Normal(name) => is_skipped_dir(Path::new(name)),
        _ => false,
    }) && !project::is_ignored_file(rel)
}

/// Relative import specifiers in a TypeScript file that resolve outside the
/// project root. Type-only imports are skipped because they are erased.
pub(super) fn typescript_imports_outside_root(source: &str, rel: &Path) -> Vec<String> {
    let allocator = Allocator::default();
    let file_name = rel.to_string_lossy();
    let parsed = parse_typescript_recovered(&allocator, source, &file_name);
    let depth = rel
        .parent()
        .map(|parent| parent.components().count())
        .unwrap_or(0);
    let mut outside = Vec::new();
    for statement in &parsed.program.body {
        let specifier = match statement {
            Statement::ImportDeclaration(import) if !import.import_kind.is_type() => {
                import.source.value.as_str()
            }
            Statement::ExportFromDeclaration(export) if !export.export_kind.is_type() => {
                export.source.value.as_str()
            }
            Statement::ExportAllDeclaration(export) if !export.export_kind.is_type() => {
                export.source.value.as_str()
            }
            _ => continue,
        };
        if escapes_root(depth, specifier) {
            outside.push(specifier.to_string());
        }
    }
    outside
}

fn escapes_root(depth: usize, specifier: &str) -> bool {
    if !specifier.starts_with("../") {
        return false;
    }
    let mut depth = depth as isize;
    for component in Path::new(specifier).components() {
        match component {
            Component::ParentDir => depth -= 1,
            Component::Normal(_) => depth += 1,
            _ => {}
        }
        if depth < 0 {
            return true;
        }
    }
    false
}

/// The runtime dependency is always replaced by the vendored `__runtime__`.
fn is_runtime_package(name: &str) -> bool {
    canonical_requirement_name(name) == RUNTIME_PACKAGE
}

fn is_skipped_dir(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| SKIPPED_DIRS.contains(&name))
}

/// The name a package gives itself in its manifest.
fn manifest_package_name(language: Language, dir: &Path) -> Result<String> {
    let path = dir.join(language.manifest_name());
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let name = match language {
        Language::TypeScript => serde_json::from_str::<serde_json::Value>(&contents)
            .with_context(|| format!("failed to parse {}", path.display()))?
            .get("name")
            .and_then(|name| name.as_str())
            .map(str::to_string),
        Language::Python => contents
            .parse::<toml_edit::DocumentMut>()
            .with_context(|| format!("failed to parse {}", path.display()))?
            .get("project")
            .and_then(|project| project.get("name"))
            .and_then(|name| name.as_str())
            .map(str::to_string),
    };
    name.ok_or_else(|| anyhow!("{} has no package name", path.display()))
}

fn declared_local_paths(language: Language, dir: &Path) -> Result<Vec<(String, String)>> {
    let path = dir.join(language.manifest_name());
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let declared = match language {
        Language::TypeScript => npm_file_dependencies(&contents),
        Language::Python => uv_path_sources(&contents),
    }
    .with_context(|| format!("failed to parse {}", path.display()))?;
    Ok(declared
        .into_iter()
        .filter(|(name, _)| !is_runtime_package(name))
        .collect())
}

/// Extras that the Python manifest in `dir` requests for `name`.
fn requested_extras(language: Language, dir: &Path, name: &str) -> Result<Vec<String>> {
    if language != Language::Python {
        return Ok(Vec::new());
    }
    let path = dir.join("pyproject.toml");
    let contents =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let doc = contents
        .parse::<toml_edit::DocumentMut>()
        .with_context(|| format!("failed to parse {}", path.display()))?;
    let canonical = canonical_requirement_name(name);
    let mut extras: Vec<String> = Vec::new();
    for requirement in doc
        .get("project")
        .and_then(|project| project.get("dependencies"))
        .and_then(|deps| deps.as_array())
        .into_iter()
        .flatten()
        .filter_map(|dep| dep.as_str())
        .filter(|dep| canonical_requirement_name(dep) == canonical)
    {
        for extra in requirement_extras(requirement) {
            if !extras.contains(&extra) {
                extras.push(extra);
            }
        }
    }
    Ok(extras)
}

/// `file:` specs, which `npm install ../dep` writes, and `link:` specs, which
/// `pnpm add ../dep` writes.
fn npm_file_dependencies(contents: &str) -> Result<Vec<(String, String)>> {
    let manifest: serde_json::Value = serde_json::from_str(contents)?;
    let Some(dependencies) = manifest
        .get("dependencies")
        .and_then(|deps| deps.as_object())
    else {
        return Ok(Vec::new());
    };
    Ok(dependencies
        .iter()
        .filter_map(|(name, spec)| {
            let spec = spec.as_str()?;
            let path = spec
                .strip_prefix("file:")
                .or_else(|| spec.strip_prefix("link:"))?;
            Some((name.clone(), path.to_string()))
        })
        .collect())
}

/// Path sources of `[project.dependencies]`. A source that only serves a
/// dependency group is not installed, so it is not vendored.
fn uv_path_sources(contents: &str) -> Result<Vec<(String, String)>> {
    let doc = contents.parse::<toml_edit::DocumentMut>()?;
    let required: Vec<String> = doc
        .get("project")
        .and_then(|project| project.get("dependencies"))
        .and_then(|deps| deps.as_array())
        .into_iter()
        .flatten()
        .filter_map(|dep| dep.as_str())
        .map(canonical_requirement_name)
        .collect();
    let Some(sources) = doc
        .get("tool")
        .and_then(|tool| tool.get("uv"))
        .and_then(|uv| uv.get("sources"))
        .and_then(|sources| sources.as_table_like())
    else {
        return Ok(Vec::new());
    };
    Ok(sources
        .iter()
        .filter(|(name, _)| required.contains(&canonical_requirement_name(name)))
        .filter_map(|(name, source)| {
            let path = source.get("path")?.as_str()?;
            Some((name.to_string(), path.to_string()))
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_root_counts_parent_segments_from_the_importing_file() {
        assert!(escapes_root(0, "../dep/helper"));
        assert!(!escapes_root(1, "../helper"));
        assert!(escapes_root(1, "../../dep"));
        assert!(!escapes_root(0, "./local"));
        assert!(!escapes_root(0, "zod"));
    }

    #[test]
    fn typescript_imports_outside_root_skips_type_only_imports() {
        let source = concat!(
            "import { a } from \"../../shared/a\";\n",
            "import type { B } from \"../../shared/b\";\n",
            "export * from \"../../shared/c\";\n",
            "export type { D } from \"../../shared/d\";\n",
            "import { e } from \"../e\";\n",
        );
        assert_eq!(
            typescript_imports_outside_root(source, Path::new("tools/search.ts")),
            vec!["../../shared/a", "../../shared/c"]
        );
    }

    #[test]
    fn is_vendored_path_skips_installs_caches_and_env_files() {
        assert!(is_vendored_path(Path::new("src/index.ts")));
        assert!(is_vendored_path(Path::new("dist/index.js")));
        assert!(!is_vendored_path(Path::new("node_modules/zod/index.js")));
        assert!(!is_vendored_path(Path::new("pkg/__pycache__/a.pyc")));
        assert!(!is_vendored_path(Path::new(".env")));
    }

    #[test]
    fn uv_path_sources_skips_sources_outside_project_dependencies() {
        let sources = uv_path_sources(concat!(
            "[project]\n",
            "dependencies = [\"My_Dep[fast]>=1\"]\n",
            "\n",
            "[dependency-groups]\n",
            "dev = [\"dev-tool\"]\n",
            "\n",
            "[tool.uv.sources]\n",
            "my-dep = { path = \"../dep\" }\n",
            "dev-tool = { path = \"../dev-tool\" }\n",
        ))
        .unwrap();
        assert_eq!(sources, vec![("my-dep".to_string(), "../dep".to_string())]);
    }

    #[test]
    fn uv_path_sources_reads_only_path_sources() {
        let sources = uv_path_sources(concat!(
            "[project]\n",
            "dependencies = [\"my-dep\", \"other\"]\n",
            "\n",
            "[tool.uv.sources]\n",
            "my-dep = { path = \"../dep\", editable = true }\n",
            "other = { git = \"https://example.com/other\" }\n",
        ))
        .unwrap();
        assert_eq!(sources, vec![("my-dep".to_string(), "../dep".to_string())]);
    }
}
