//! Emit `.mda/build/sandbox-recipe.json` for deploy/dev bake.
//!
//! Digests cover `setup.sh` bytes plus optional author bake bases extracted from
//! `defineSandbox` / `define_sandbox` (string literals only), and, when a Python
//! `define_sandbox(prepare=...)` callback is declared, the files under `sandbox/`.

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::agent_config::read_agent_static_config;
use crate::deploy::bake::{RecipeSpec, SandboxPrepare};
use crate::deploy::helpers::recipe_digest;
use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};

use super::discovery::{find_sandbox_entry, read_setup_script, sandbox_import_specifier};
use super::static_extract::{
    extract_python_selected_keyword_config, extract_typescript_selected_object_config,
};
use super::write_if_changed;
use super::SANDBOX_DIR;

/// Build artifact next to `langgraph.json`.
pub const SANDBOX_RECIPE_FILE: &str = "sandbox-recipe.json";

const LEGACY_THREAD_SCOPE_WARNING: &str =
    "sandbox scope \"thread\" is deprecated and ignored. Remove it from the sandbox declaration. Support for this option will be removed in v0.7.0.";

/// Secret-safe private registry declaration compiled from the authoring API.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SandboxRegistryManifest {
    pub url: String,
    pub username: String,
    /// Environment variable read by `mda deploy` / `mda dev`.
    pub password_env: String,
}

impl SandboxRegistryManifest {
    /// Non-secret registry identity included in the recipe digest.
    pub fn digest_input(&self) -> String {
        format!("{}\0{}\0{}", self.url, self.username, self.password_env)
    }
}

/// A Python sandbox's deploy-time `prepare` callback, recorded at compile time.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SandboxPrepareManifest {
    /// Dotted module exporting `sandbox`.
    pub module: String,
    /// Digest of the files under `sandbox/`, where the callback and its inputs live.
    pub inputs_digest: String,
}

/// Author bake bases + setup metadata written at compile time.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SandboxRecipeManifest {
    pub has_setup_script: bool,
    /// First 16 hex chars of the setup SHA (preview only; full digest is below).
    pub setup_sha_preview: Option<String>,
    pub snapshot_name: Option<String>,
    pub snapshot_id: Option<String>,
    pub docker_image: Option<String>,
    /// Private registry declaration. Contains only an env-var name, not a secret.
    pub registry: Option<SandboxRegistryManifest>,
    /// Deploy-time `prepare` callback, when declared.
    #[serde(default)]
    pub prepare: Option<SandboxPrepareManifest>,
    /// [`RecipeSpec::digest`] over setup bytes, bake bases, and `prepare` inputs.
    pub digest: String,
}

impl SandboxRecipeManifest {
    /// The bake inputs this manifest describes, with `setup_script` as read now.
    pub fn recipe_spec(&self, project_root: &Path, setup_script: String) -> RecipeSpec {
        RecipeSpec {
            setup_script,
            snapshot_name: self.snapshot_name.clone(),
            snapshot_id: self.snapshot_id.clone(),
            docker_image: self.docker_image.clone(),
            registry: self.registry.clone(),
            prepare: self.prepare.as_ref().map(|prepare| SandboxPrepare {
                project_root: project_root.to_path_buf(),
                module: prepare.module.clone(),
                inputs_digest: prepare.inputs_digest.clone(),
            }),
        }
    }
}

/// Whether the project needs LangSmith sandboxes: a sandbox declaration, or a
/// factory whose `setup.sh` bakes a recipe without one.
///
/// Matches what [`write_sandbox_recipe_manifest`] bakes, so preflights that run
/// before compile see the same projects the bake does.
pub(crate) fn project_uses_sandbox(project: &Project) -> Result<bool> {
    if find_sandbox_entry(project).is_some() {
        return Ok(true);
    }
    let has_setup_script =
        read_setup_script(project)?.is_some_and(|setup| !setup.trim().is_empty());
    Ok(has_setup_script && read_agent_static_config(project)?.is_factory)
}

/// Probe the sandbox declaration and write `sandbox-recipe.json` when present.
///
/// A factory may select its sandbox per run without a static declaration, so
/// its `setup.sh` alone is a recipe, baked on Host's default image.
///
/// Returns `None` (and removes a stale file) when there is nothing to bake.
pub(super) fn write_sandbox_recipe_manifest(
    project: &Project,
    out_dir: &std::path::Path,
    factory: bool,
) -> Result<Option<SandboxRecipeManifest>> {
    let path = out_dir.join(SANDBOX_RECIPE_FILE);
    let setup = read_setup_script(project)?.unwrap_or_default();
    let has_setup_script = !setup.trim().is_empty();
    let (bases, prepare) = match find_sandbox_entry(project) {
        Some(entry) => (
            extract_sandbox_bases(project, &entry)?,
            extract_sandbox_prepare(project, &entry)?,
        ),
        None if factory && has_setup_script => (BakeBases::default(), None),
        None => {
            if path.exists() {
                let _ = fs::remove_file(&path);
            }
            return Ok(None);
        }
    };
    if !has_setup_script && bases.is_empty() && prepare.is_none() {
        if path.exists() {
            let _ = fs::remove_file(&path);
        }
        return Ok(None);
    }
    let mut manifest = SandboxRecipeManifest {
        has_setup_script,
        setup_sha_preview: has_setup_script
            .then(|| recipe_digest(setup.as_bytes(), None, None, None, None)),
        snapshot_name: bases.snapshot_name,
        snapshot_id: bases.snapshot_id,
        docker_image: bases.docker_image,
        registry: bases.registry,
        prepare,
        digest: String::new(),
    };
    manifest.digest = manifest.recipe_spec(&project.root, setup).digest();

    let json = serde_json::to_string_pretty(&manifest)
        .context("failed to serialize sandbox-recipe.json")?;
    write_if_changed(&path, format!("{json}\n").as_bytes())
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(Some(manifest))
}

/// Load a previously written recipe manifest from the build directory.
pub fn load_sandbox_recipe_manifest(
    out_dir: &std::path::Path,
) -> Result<Option<SandboxRecipeManifest>> {
    let path = out_dir.join(SANDBOX_RECIPE_FILE);
    if !path.is_file() {
        return Ok(None);
    }
    let text =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let manifest: SandboxRecipeManifest = serde_json::from_str(&text)
        .with_context(|| format!("failed to parse {}", path.display()))?;
    Ok(Some(manifest))
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct BakeBases {
    snapshot_name: Option<String>,
    snapshot_id: Option<String>,
    docker_image: Option<String>,
    registry: Option<SandboxRegistryManifest>,
}

impl BakeBases {
    fn is_empty(&self) -> bool {
        self.snapshot_name.is_none()
            && self.snapshot_id.is_none()
            && self.docker_image.is_none()
            && self.registry.is_none()
    }
}

/// Record a Python `sandbox = define_sandbox(prepare=...)` callback.
///
/// The callback is code, so it is detected rather than extracted; its digest
/// covers every project file under `sandbox/`. TypeScript has no `prepare` yet.
fn extract_sandbox_prepare(
    project: &Project,
    entry: &Path,
) -> Result<Option<SandboxPrepareManifest>> {
    if project.language != Language::Python {
        return Ok(None);
    }
    let path = project.root.join(entry);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let declared = run_python_extractor(
        scripts::SANDBOX_PREPARE,
        &source,
        &entry.display().to_string(),
        "the sandbox prepare callback",
    )?;
    if declared.is_none() {
        return Ok(None);
    }
    Ok(Some(SandboxPrepareManifest {
        module: sandbox_import_specifier(entry, project.language),
        inputs_digest: sandbox_inputs_digest(project)?,
    }))
}

fn sandbox_inputs_digest(project: &Project) -> Result<String> {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    for rel in project
        .files
        .iter()
        .filter(|rel| rel.starts_with(SANDBOX_DIR))
    {
        let path = project.root.join(rel);
        let bytes =
            fs::read(&path).with_context(|| format!("failed to read {}", path.display()))?;
        hasher.update(crate::path_util::to_unix_path(rel).as_bytes());
        hasher.update([0]);
        hasher.update((bytes.len() as u64).to_le_bytes());
        hasher.update(&bytes);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn extract_sandbox_bases(project: &Project, entry: &PathBuf) -> Result<BakeBases> {
    let path = project.root.join(entry);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let config = match project.language {
        Language::TypeScript => extract_typescript_selected_object_config(
            &source,
            entry,
            "sandbox",
            "defineSandbox",
            &["snapshotName", "snapshotId", "dockerImage", "registry"],
        )?,
        Language::Python => extract_python_selected_keyword_config(
            &source,
            entry,
            "sandbox",
            "define_sandbox",
            &["snapshot_name", "snapshot_id", "docker_image", "registry"],
        )?,
    };
    bases_from_config(&config, project.language)
}

/// Return the migration warning when the legacy thread scope is statically identifiable.
pub(super) fn legacy_thread_scope_warning(project: &Project) -> Result<Option<String>> {
    let Some(entry) = find_sandbox_entry(project) else {
        return Ok(None);
    };
    let path = project.root.join(&entry);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let config = match project.language {
        Language::TypeScript => extract_typescript_selected_object_config(
            &source,
            &entry,
            "sandbox",
            "defineSandbox",
            &["scope"],
        ),
        Language::Python => extract_python_selected_keyword_config(
            &source,
            &entry,
            "sandbox",
            "define_sandbox",
            &["scope"],
        ),
    };
    let Ok(config) = config else {
        // Warning detection is advisory. Recipe extraction later in this compile
        // validates the declaration, while runtime validation handles dynamic scope.
        return Ok(None);
    };

    Ok(
        (config.get("scope").and_then(Value::as_str) == Some("thread"))
            .then(|| LEGACY_THREAD_SCOPE_WARNING.to_string()),
    )
}

fn bases_from_config(config: &Value, language: Language) -> Result<BakeBases> {
    let string_field = |camel: &str, snake: &str| -> Option<String> {
        let key = match language {
            Language::TypeScript => camel,
            Language::Python => snake,
        };
        config
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
    };
    // Accept either spelling when present (defensive for mixed extracts).
    let pick = |camel: &str, snake: &str| {
        string_field(camel, snake)
            .or_else(|| {
                config
                    .get(camel)
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|v| !v.is_empty())
                    .map(str::to_string)
            })
            .or_else(|| {
                config
                    .get(snake)
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|v| !v.is_empty())
                    .map(str::to_string)
            })
    };
    let registry = config
        .get("registry")
        .map(|raw| -> Result<SandboxRegistryManifest> {
            let Some(value) = raw.as_object() else {
                bail!("sandbox registry must be an object");
            };
            let field = |camel: &str, snake: &str| {
                value
                    .get(match language {
                        Language::TypeScript => camel,
                        Language::Python => snake,
                    })
                    .or_else(|| value.get(camel))
                    .or_else(|| value.get(snake))
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_string)
            };
            let required = |value: Option<String>, field: &str| {
                value
                    .with_context(|| format!("sandbox registry.{field} must be a non-empty string"))
            };
            let password_env_key = language.ident("passwordEnv", "password_env");
            Ok(SandboxRegistryManifest {
                url: required(field("url", "url"), "url")?,
                username: required(field("username", "username"), "username")?,
                password_env: required(field("passwordEnv", "password_env"), password_env_key)?,
            })
        })
        .transpose()?;
    let bases = BakeBases {
        snapshot_name: pick("snapshotName", "snapshot_name"),
        snapshot_id: pick("snapshotId", "snapshot_id"),
        docker_image: pick("dockerImage", "docker_image"),
        registry,
    };
    if bases.registry.is_some() && bases.docker_image.is_none() {
        bail!(
            "sandbox registry requires {}",
            language.ident("dockerImage", "docker_image")
        );
    }
    Ok(bases)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::Path;

    fn write_sandbox(root: &Path, relative: &str, source: &str) {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, source).unwrap();
    }

    fn sandbox_project(root: &Path, language: Language, entry: &str) -> Project {
        let mut files = vec![PathBuf::from(entry)];
        if root.join("sandbox/setup.sh").is_file() {
            files.push(PathBuf::from("sandbox/setup.sh"));
        }
        Project {
            root: root.to_path_buf(),
            language,
            agent_file: PathBuf::from(match language {
                Language::TypeScript => "agent.ts",
                Language::Python => "agent.py",
            }),
            files,
        }
    }

    #[test]
    fn bases_from_typescript_literals() {
        let config = json!({
            "idleTtlSeconds": 600,
            "snapshotName": "base-snap",
        });
        let bases = bases_from_config(&config, Language::TypeScript).unwrap();
        assert_eq!(bases.snapshot_name.as_deref(), Some("base-snap"));
        assert!(bases.snapshot_id.is_none());
        assert!(bases.docker_image.is_none());
        assert!(bases.registry.is_none());
    }

    #[test]
    fn bases_from_python_kwargs() {
        let config = json!({
            "default_timeout": 600,
            "docker_image": "python:3.12-slim",
            "registry": {
                "url": "ghcr.io",
                "username": "octocat",
                "password_env": "GHCR_TOKEN"
            },
        });
        let bases = bases_from_config(&config, Language::Python).unwrap();
        assert_eq!(bases.docker_image.as_deref(), Some("python:3.12-slim"));
        assert_eq!(
            bases.registry,
            Some(SandboxRegistryManifest {
                url: "ghcr.io".into(),
                username: "octocat".into(),
                password_env: "GHCR_TOKEN".into(),
            })
        );
    }

    #[test]
    fn bases_from_typescript_registry_object() {
        let config = json!({
            "dockerImage": "ghcr.io/acme/base:1",
            "registry": {
                "url": "ghcr.io",
                "username": "octocat",
                "passwordEnv": "GHCR_TOKEN"
            },
        });
        let bases = bases_from_config(&config, Language::TypeScript).unwrap();
        assert_eq!(
            bases.registry,
            Some(SandboxRegistryManifest {
                url: "ghcr.io".into(),
                username: "octocat".into(),
                password_env: "GHCR_TOKEN".into(),
            })
        );
    }

    #[test]
    fn plain_typescript_sandbox_does_not_emit_a_recipe() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox();\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        let manifest =
            write_sandbox_recipe_manifest(&project, &root.path().join("build"), false).unwrap();

        assert!(manifest.is_none());
    }

    fn setup_only_project(root: &Path) -> Project {
        write_sandbox(root, "sandbox/setup.sh", "echo ready\n");
        Project {
            root: root.to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("sandbox/setup.sh")],
        }
    }

    #[test]
    fn factory_setup_script_without_declaration_bakes_on_the_default_image() {
        let root = tempfile::tempdir().unwrap();
        let project = setup_only_project(root.path());

        let manifest = write_sandbox_recipe_manifest(&project, &root.path().join("build"), true)
            .unwrap()
            .unwrap();

        assert!(manifest.has_setup_script);
        assert_eq!(manifest.snapshot_name, None);
        assert_eq!(manifest.snapshot_id, None);
        assert_eq!(manifest.docker_image, None);
        assert_eq!(
            manifest.digest,
            recipe_digest(b"echo ready\n", None, None, None, None)
        );
    }

    #[test]
    fn static_setup_script_without_declaration_does_not_emit_a_recipe() {
        let root = tempfile::tempdir().unwrap();
        let project = setup_only_project(root.path());

        let manifest =
            write_sandbox_recipe_manifest(&project, &root.path().join("build"), false).unwrap();

        assert!(manifest.is_none());
    }

    #[test]
    fn project_uses_sandbox_for_a_factory_setup_script_without_declaration() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(root.path(), "sandbox/setup.sh", "echo ready\n");
        let project = |agent: &str| {
            write_sandbox(root.path(), "agent.ts", agent);
            Project {
                root: root.path().to_path_buf(),
                language: Language::TypeScript,
                agent_file: PathBuf::from("agent.ts"),
                files: vec![PathBuf::from("agent.ts"), PathBuf::from("sandbox/setup.sh")],
            }
        };

        let factory = project("export const agent = () => defineDeepAgent({ name: \"a\" });\n");
        assert!(project_uses_sandbox(&factory).unwrap());

        let fixed = project("export const agent = defineDeepAgent({ name: \"a\" });\n");
        assert!(!project_uses_sandbox(&fixed).unwrap());
    }

    #[test]
    fn dynamic_runtime_only_options_do_not_block_recipe_extraction() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            concat!(
                "export const sandbox = defineSandbox({\n",
                "  defaultTimeout: Number(process.env.TIMEOUT),\n",
                "});\n",
            ),
        );
        write_sandbox(root.path(), "sandbox/setup.sh", "echo ready\n");
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        let manifest = write_sandbox_recipe_manifest(&project, &root.path().join("build"), false)
            .unwrap()
            .unwrap();

        assert!(manifest.has_setup_script);
    }

    #[test]
    fn dynamic_python_runtime_only_options_do_not_emit_a_recipe() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/__init__.py",
            "sandbox = define_sandbox(default_timeout=int(os.environ['TIMEOUT']))\n",
        );
        let project = sandbox_project(root.path(), Language::Python, "sandbox/__init__.py");

        let manifest =
            write_sandbox_recipe_manifest(&project, &root.path().join("build"), false).unwrap();

        assert!(manifest.is_none());
    }

    #[test]
    fn python_prepare_callback_is_a_recipe_keyed_by_the_sandbox_directory() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/__init__.py",
            "async def prepare(sandbox):\n    pass\n\nsandbox = define_sandbox(prepare=prepare)\n",
        );
        write_sandbox(root.path(), "sandbox/files/browser_cli.py", "v1\n");
        let mut project = sandbox_project(root.path(), Language::Python, "sandbox/__init__.py");
        project
            .files
            .push(PathBuf::from("sandbox/files/browser_cli.py"));
        let out = root.path().join("build");

        let manifest = write_sandbox_recipe_manifest(&project, &out, false)
            .unwrap()
            .unwrap();
        assert!(!manifest.has_setup_script);
        assert_eq!(manifest.prepare.as_ref().unwrap().module, "sandbox");
        assert_eq!(
            load_sandbox_recipe_manifest(&out).unwrap().as_ref(),
            Some(&manifest)
        );

        write_sandbox(root.path(), "sandbox/files/browser_cli.py", "v2\n");
        let changed = write_sandbox_recipe_manifest(&project, &out, false)
            .unwrap()
            .unwrap();
        assert_ne!(manifest.digest, changed.digest);
    }

    #[test]
    fn sandbox_without_prepare_keeps_its_recipe_digest() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/__init__.py",
            "sandbox = define_sandbox(snapshot_name=\"base\")\n",
        );
        let project = sandbox_project(root.path(), Language::Python, "sandbox/__init__.py");

        let manifest = write_sandbox_recipe_manifest(&project, &root.path().join("build"), false)
            .unwrap()
            .unwrap();

        assert_eq!(manifest.prepare, None);
        assert_eq!(
            manifest.digest,
            recipe_digest(b"", Some("base"), None, None, None)
        );
    }

    #[test]
    fn dynamic_bake_options_still_fail_static_extraction() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox({ dockerImage: process.env.IMAGE });\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        let error =
            write_sandbox_recipe_manifest(&project, &root.path().join("build"), false).unwrap_err();

        let error = format!("{error:#}");
        assert!(error.contains("dockerImage"));
        assert!(error.contains("not static"));
    }

    #[test]
    fn warns_when_typescript_declares_legacy_thread_scope() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            concat!(
                "export const sandbox = defineSandbox({\n",
                "  scope: \"thread\",\n",
                "  idleTtlSeconds: 600,\n",
                "});\n",
            ),
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        assert_eq!(
            legacy_thread_scope_warning(&project).unwrap().as_deref(),
            Some(LEGACY_THREAD_SCOPE_WARNING)
        );
    }

    #[test]
    fn warns_when_python_declares_legacy_thread_scope() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/__init__.py",
            concat!(
                "sandbox = define_sandbox(\n",
                "    scope=\"thread\",\n",
                "    idle_ttl_seconds=600,\n",
                ")\n",
            ),
        );
        let project = sandbox_project(root.path(), Language::Python, "sandbox/__init__.py");

        assert_eq!(
            legacy_thread_scope_warning(&project).unwrap().as_deref(),
            Some(LEGACY_THREAD_SCOPE_WARNING)
        );
    }

    #[test]
    fn does_not_warn_when_sandbox_omits_scope() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox({ idleTtlSeconds: 600 });\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        assert_eq!(legacy_thread_scope_warning(&project).unwrap(), None);
    }

    #[test]
    fn does_not_fail_compile_when_legacy_scope_is_dynamic() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox({ scope: process.env.SCOPE });\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        assert_eq!(legacy_thread_scope_warning(&project).unwrap(), None);
    }
}
