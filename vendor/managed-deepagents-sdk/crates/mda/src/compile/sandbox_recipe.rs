//! Emit `.mda/build/sandbox-recipe.json` for deploy/dev bake.
//!
//! Digests cover `setup.sh` bytes plus optional author bake bases extracted from
//! `defineSandbox` / `define_sandbox` (string literals only).

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::agent_config::read_agent_static_config;
use crate::deploy::helpers::recipe_digest;
use crate::project::{Language, Project};

use super::discovery::{find_sandbox_entry, read_setup_script};
use super::static_extract::{
    extract_python_selected_keyword_config, extract_typescript_selected_object_config,
};
use super::write_if_changed;

/// Build artifact next to `langgraph.json`.
pub const SANDBOX_RECIPE_FILE: &str = "sandbox-recipe.json";

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
    /// Filesystem size for a snapshot built from `docker_image`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fs_capacity_bytes: Option<u64>,
    /// [`recipe_digest`] over setup bytes + bake bases.
    pub digest: String,
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
    let bases = match find_sandbox_entry(project) {
        Some(entry) => extract_sandbox_bases(project, &entry)?,
        None if factory && has_setup_script => BakeBases::default(),
        None => {
            if path.exists() {
                let _ = fs::remove_file(&path);
            }
            return Ok(None);
        }
    };
    if !has_setup_script && bases.is_empty() {
        if path.exists() {
            let _ = fs::remove_file(&path);
        }
        return Ok(None);
    }
    let registry_digest = bases
        .registry
        .as_ref()
        .map(SandboxRegistryManifest::digest_input);
    let digest = recipe_digest(
        setup.as_bytes(),
        bases.snapshot_name.as_deref(),
        bases.snapshot_id.as_deref(),
        bases.docker_image.as_deref(),
        registry_digest.as_deref(),
        bases.fs_capacity_bytes,
    );
    let setup_sha_preview = if !has_setup_script {
        None
    } else {
        Some(recipe_digest(
            setup.as_bytes(),
            None,
            None,
            None,
            None,
            None,
        ))
    };

    let manifest = SandboxRecipeManifest {
        has_setup_script,
        setup_sha_preview,
        snapshot_name: bases.snapshot_name,
        snapshot_id: bases.snapshot_id,
        docker_image: bases.docker_image,
        registry: bases.registry,
        fs_capacity_bytes: bases.fs_capacity_bytes,
        digest,
    };

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
    fs_capacity_bytes: Option<u64>,
}

impl BakeBases {
    fn is_empty(&self) -> bool {
        self.snapshot_name.is_none()
            && self.snapshot_id.is_none()
            && self.docker_image.is_none()
            && self.registry.is_none()
    }
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
    let mut bases = bases_from_config(&config, project.language)?;
    if bases.docker_image.is_some() {
        bases.fs_capacity_bytes =
            extract_docker_fs_capacity_bytes(&source, entry, project.language)?;
    }
    Ok(bases)
}

/// The declared size of the snapshot built from `docker_image`.
///
/// Read only for a `docker_image` base so other sandboxes may still compute it.
fn extract_docker_fs_capacity_bytes(
    source: &str,
    entry: &Path,
    language: Language,
) -> Result<Option<u64>> {
    let key = language.ident("fsCapacityBytes", "fs_capacity_bytes");
    let config = match language {
        Language::TypeScript => extract_typescript_selected_object_config(
            source,
            entry,
            "sandbox",
            "defineSandbox",
            &[key],
        ),
        Language::Python => extract_python_selected_keyword_config(
            source,
            entry,
            "sandbox",
            "define_sandbox",
            &[key],
        ),
    }
    .with_context(|| {
        format!(
            "sandbox {key} sizes the {} snapshot, so it must be an integer literal or a top-level constant",
            language.ident("dockerImage", "docker_image")
        )
    })?;
    fs_capacity_bytes(&config, language)
}

fn fs_capacity_bytes(config: &Value, language: Language) -> Result<Option<u64>> {
    let key = language.ident("fsCapacityBytes", "fs_capacity_bytes");
    let Some(value) = config.get(key).filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    // TypeScript numeric literals extract as f64.
    value
        .as_u64()
        .or_else(|| {
            value
                .as_f64()
                .filter(|bytes| bytes.fract() == 0.0 && *bytes > 0.0)
                .map(|bytes| bytes as u64)
        })
        .filter(|bytes| *bytes > 0)
        .map(Some)
        .with_context(|| format!("sandbox {key} must be a positive integer literal"))
}

/// Fail the build when the sandbox declaration still passes the removed `scope`
/// option, which `defineSandbox` / `define_sandbox` reject at startup.
pub(super) fn reject_removed_sandbox_scope(project: &Project) -> Result<()> {
    let Some(entry) = find_sandbox_entry(project) else {
        return Ok(());
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
    // A declaration compile cannot read statically is reported by recipe
    // extraction later in this compile, and the runtime still rejects a dynamic
    // `scope` at startup.
    let Ok(config) = config else {
        return Ok(());
    };
    if config.get("scope").is_some() {
        bail!(
            "{} passes the removed sandbox `scope` option. Sandbox reuse is always one box per thread: remove `scope` from the sandbox declaration.",
            entry.display()
        );
    }
    Ok(())
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
        fs_capacity_bytes: None,
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
    fn fs_capacity_bytes_from_literals() {
        let python = json!({ "fs_capacity_bytes": 2_147_483_648_u64 });
        let typescript = json!({ "fsCapacityBytes": 2_147_483_648.0 });
        for (config, language) in [
            (python, Language::Python),
            (typescript, Language::TypeScript),
        ] {
            assert_eq!(fs_capacity_bytes(&config, language).unwrap(), Some(2 << 30));
        }
        for bad in [json!(0), json!(1.5), json!("2GiB")] {
            let config = json!({ "fs_capacity_bytes": bad });
            assert!(fs_capacity_bytes(&config, Language::Python).is_err());
        }
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
    fn docker_sandbox_records_fs_capacity_bytes() {
        for (language, entry, source) in [
            (
                Language::Python,
                "sandbox/__init__.py",
                "sandbox = define_sandbox(docker_image=\"python:3.12\", fs_capacity_bytes=2_147_483_648)\n",
            ),
            (
                Language::TypeScript,
                "sandbox/index.ts",
                "export const sandbox = defineSandbox({ dockerImage: \"python:3.12\", fsCapacityBytes: 2_147_483_648 });\n",
            ),
        ] {
            let root = tempfile::tempdir().unwrap();
            write_sandbox(root.path(), entry, source);
            let project = sandbox_project(root.path(), language, entry);

            let manifest =
                write_sandbox_recipe_manifest(&project, &root.path().join("build"), false)
                    .unwrap()
                    .unwrap();

            assert_eq!(manifest.fs_capacity_bytes, Some(2 << 30));
        }
    }

    #[test]
    fn computed_fs_capacity_bytes_fails_only_with_a_docker_image() {
        let compile = |base: &str| {
            let root = tempfile::tempdir().unwrap();
            write_sandbox(
                root.path(),
                "sandbox/__init__.py",
                &format!("sandbox = define_sandbox({base}, fs_capacity_bytes=2 * 1024**3)\n"),
            );
            let project = sandbox_project(root.path(), Language::Python, "sandbox/__init__.py");
            write_sandbox_recipe_manifest(&project, &root.path().join("build"), false)
        };

        let manifest = compile("snapshot_name=\"base\"").unwrap().unwrap();
        assert_eq!(manifest.fs_capacity_bytes, None);
        let error = compile("docker_image=\"python:3.12\"").unwrap_err();
        assert!(error
            .to_string()
            .contains("must be an integer literal or a top-level constant"));
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
            recipe_digest(b"echo ready\n", None, None, None, None, None)
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
    fn rejects_typescript_sandbox_scope() {
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

        let error = reject_removed_sandbox_scope(&project)
            .unwrap_err()
            .to_string();
        assert!(error.contains("sandbox/index.ts"), "{error}");
        assert!(error.contains("removed sandbox `scope` option"), "{error}");
    }

    #[test]
    fn rejects_python_sandbox_scope() {
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

        let error = reject_removed_sandbox_scope(&project)
            .unwrap_err()
            .to_string();
        assert!(error.contains("sandbox/__init__.py"), "{error}");
        assert!(error.contains("removed sandbox `scope` option"), "{error}");
    }

    #[test]
    fn accepts_sandbox_without_scope() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox({ idleTtlSeconds: 600 });\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        reject_removed_sandbox_scope(&project).unwrap();
    }

    #[test]
    fn leaves_dynamic_sandbox_scope_to_the_runtime() {
        let root = tempfile::tempdir().unwrap();
        write_sandbox(
            root.path(),
            "sandbox/index.ts",
            "export const sandbox = defineSandbox({ scope: process.env.SCOPE });\n",
        );
        let project = sandbox_project(root.path(), Language::TypeScript, "sandbox/index.ts");

        reject_removed_sandbox_scope(&project).unwrap();
    }
}
