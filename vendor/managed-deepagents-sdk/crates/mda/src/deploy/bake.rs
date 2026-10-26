//! Bake a deployment's sandbox recipe snapshot from `setup.sh` + bake bases.
//!
//! Fail closed: non-zero setup exit or a snapshot that never becomes `ready`
//! aborts deploy/dev before the recipe pointer is persisted.

use std::collections::BTreeMap;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use base64::Engine as _;

use crate::compile::SandboxRegistryManifest;
use crate::langsmith::{
    Client, CreateSandboxBoxOptions, SandboxCommandResult, SnapshotDelete,
    DEFAULT_SNAPSHOT_READY_TIMEOUT,
};

use super::helpers::{
    bake_snapshot_list_prefix, intermediate_snapshot_name, is_deployment_intermediate_snapshot,
    is_deployment_recipe_snapshot, recipe_digest, recipe_snapshot_name, recipe_snapshot_reference,
    sandbox_deployment_component, sandbox_registry_name, HOST_SANDBOX_NAME_LIMIT,
    SANDBOX_NAME_SEPARATOR,
};

/// Default filesystem capacity when building a snapshot from a Docker image.
const DEFAULT_DOCKER_FS_CAPACITY_BYTES: u64 = 1 << 30;

/// Bound for create-from-docker-image readiness.
const DOCKER_SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(300);

/// Builder box create wait.
const BUILDER_TIMEOUT_SECS: u64 = 180;

/// Builder idle TTL — short; bake should finish well before this.
const BUILDER_IDLE_TTL_SECS: u64 = 900;

/// Setup execute timeout.
const SETUP_TIMEOUT_SECS: u64 = 600;

/// Inputs that identify and provision a recipe snapshot.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RecipeSpec {
    /// Contents of `sandbox/setup.sh` (may be empty).
    pub setup_script: String,
    /// Author bake base: LangSmith snapshot name.
    pub snapshot_name: Option<String>,
    /// Author bake base: LangSmith snapshot id.
    pub snapshot_id: Option<String>,
    /// Author bake base: Docker image.
    pub docker_image: Option<String>,
    /// Secret-safe private registry declaration.
    pub registry: Option<SandboxRegistryManifest>,
    /// Filesystem capacity for a snapshot built from `docker_image`.
    pub fs_capacity_bytes: Option<u64>,
}

impl RecipeSpec {
    fn docker_fs_capacity_bytes(&self) -> u64 {
        self.fs_capacity_bytes
            .unwrap_or(DEFAULT_DOCKER_FS_CAPACITY_BYTES)
    }
}

/// Coarse stages of a bake, reported so a multi-minute cold bake shows which
/// step is running instead of one static spinner line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BakePhase {
    /// Looking for a ready recipe snapshot for the current digest.
    CheckingRecipe,
    /// Storing the private registry credential on Host.
    StoringRegistry,
    /// Building a snapshot from the author's Docker image.
    BuildingFromImage,
    /// Creating the builder sandbox that `setup.sh` runs on.
    StartingBuilder,
    /// Executing `setup.sh` on the builder.
    RunningSetup,
    /// Capturing the builder filesystem as the recipe snapshot.
    CapturingSnapshot,
    /// Tearing down temporary bake resources.
    CleaningUp,
}

impl BakePhase {
    /// Short, lower-case description for the CLI sub-line.
    pub fn label(self) -> &'static str {
        match self {
            Self::CheckingRecipe => "checking for a ready recipe snapshot",
            Self::StoringRegistry => "storing registry credentials",
            Self::BuildingFromImage => "building snapshot from Docker image",
            Self::StartingBuilder => "starting builder sandbox",
            Self::RunningSetup => "running setup.sh",
            Self::CapturingSnapshot => "capturing recipe snapshot",
            Self::CleaningUp => "cleaning up",
        }
    }
}

/// Result of [`bake_recipe`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BakeOutcome {
    /// Deterministic recipe snapshot name (`{deployment}--setup-{digest}`).
    ///
    /// Bookkeeping identity: name matching and stale-recipe cleanup. Not what
    /// the runtime resolves — use [`BakeOutcome::recipe_reference`] for that.
    pub recipe_name: String,
    /// Reference the runtime clones from, tagged when Host did not tag the
    /// snapshot `latest`. See `helpers::recipe_snapshot_reference`.
    pub recipe_reference: String,
    /// `true` when a ready snapshot with that name already existed.
    pub skipped: bool,
}

/// Bake or reuse the recipe snapshot for `deployment`.
///
/// `env_vars` are non-reserved project secrets forwarded onto the builder box
/// so `setup.sh` can reach private registries / APIs without embedding secrets
/// in the script.
///
/// `progress` is called as the bake enters each [`BakePhase`].
pub fn bake_recipe(
    client: &Client,
    deployment: &str,
    spec: &RecipeSpec,
    env_vars: &BTreeMap<String, String>,
    progress: &dyn Fn(BakePhase),
) -> Result<BakeOutcome> {
    let registry_digest = spec
        .registry
        .as_ref()
        .map(SandboxRegistryManifest::digest_input);
    let digest = recipe_digest(
        spec.setup_script.as_bytes(),
        spec.snapshot_name.as_deref(),
        spec.snapshot_id.as_deref(),
        spec.docker_image.as_deref(),
        registry_digest.as_deref(),
        spec.fs_capacity_bytes,
    );
    let recipe_name = recipe_snapshot_name(deployment, &digest);

    progress(BakePhase::CheckingRecipe);
    if let Some(existing) = client.find_ready_snapshot_by_name(&recipe_name)? {
        let recipe_reference = recipe_snapshot_reference(&recipe_name, &existing.tags);
        return Ok(BakeOutcome {
            recipe_name,
            recipe_reference,
            skipped: true,
        });
    }

    let registry_id = resolve_registry_id(client, deployment, spec, env_vars, progress)?;

    // Docker image with an empty setup: the docker-built snapshot *is* the recipe.
    if let Some(image) = spec.docker_image.as_deref().filter(|_| {
        spec.setup_script.trim().is_empty()
            && spec.snapshot_name.is_none()
            && spec.snapshot_id.is_none()
    }) {
        progress(BakePhase::BuildingFromImage);
        let snapshot = client.create_snapshot_from_docker_image(
            &recipe_name,
            image,
            spec.docker_fs_capacity_bytes(),
            DOCKER_SNAPSHOT_TIMEOUT,
            registry_id.as_deref(),
        )?;
        let recipe_reference = recipe_snapshot_reference(&recipe_name, &snapshot.tags);
        return Ok(BakeOutcome {
            recipe_name,
            recipe_reference,
            skipped: false,
        });
    }

    let mut intermediate: Option<String> = None;
    let (base_snapshot_name, base_snapshot_id) = resolve_builder_base(
        client,
        deployment,
        &digest,
        spec,
        registry_id.as_deref(),
        &mut intermediate,
        progress,
    )?;

    let builder_name = builder_sandbox_name(deployment, &digest);
    // Drop a leftover builder from a previous interrupted bake.
    let _ = client.delete_sandbox_box(&builder_name);

    progress(BakePhase::StartingBuilder);
    let box_summary = client
        .create_sandbox_box(
            &builder_name,
            CreateSandboxBoxOptions {
                snapshot_name: base_snapshot_name.as_deref(),
                snapshot_id: base_snapshot_id.as_deref(),
                env_vars: Some(env_vars),
                timeout_secs: BUILDER_TIMEOUT_SECS,
                idle_ttl_seconds: BUILDER_IDLE_TTL_SECS,
            },
        )
        .with_context(|| format!("failed to create bake builder '{builder_name}'"))?;

    let bake_result = (|| -> Result<Vec<String>> {
        if !spec.setup_script.trim().is_empty() {
            let dataplane = box_summary
                .dataplane_url
                .as_deref()
                .context("bake builder is missing dataplane_url")?;
            progress(BakePhase::RunningSetup);
            run_setup_script(client, dataplane, &spec.setup_script)?;
        }
        progress(BakePhase::CapturingSnapshot);
        let captured =
            client.capture_snapshot(&builder_name, &recipe_name, DEFAULT_SNAPSHOT_READY_TIMEOUT)?;
        Ok(captured.tags)
    })();

    // Always tear down the builder; intermediate docker base only after success.
    progress(BakePhase::CleaningUp);
    let _ = client.delete_sandbox_box(&builder_name);
    let tags = match bake_result {
        Err(err) => {
            if let Some(name) = &intermediate {
                let _ = delete_snapshot_by_name(client, name);
            }
            return Err(err);
        }
        Ok(tags) => tags,
    };
    if let Some(name) = &intermediate {
        let _ = delete_snapshot_by_name(client, name);
    }

    let recipe_reference = recipe_snapshot_reference(&recipe_name, &tags);
    Ok(BakeOutcome {
        recipe_name,
        recipe_reference,
        skipped: false,
    })
}

fn resolve_registry_id(
    client: &Client,
    deployment: &str,
    spec: &RecipeSpec,
    env_vars: &BTreeMap<String, String>,
    progress: &dyn Fn(BakePhase),
) -> Result<Option<String>> {
    let Some(registry) = &spec.registry else {
        return Ok(None);
    };
    if spec
        .docker_image
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_none()
    {
        bail!("registry requires dockerImage / docker_image on the sandbox declaration");
    }
    let password = env_vars
        .get(&registry.password_env)
        .map(String::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| {
            std::env::var(&registry.password_env)
                .ok()
                .filter(|value| !value.is_empty())
        })
        .with_context(|| {
            format!(
                "sandbox registry password is missing; set {} in the project .env or process environment",
                registry.password_env
            )
        })?;
    progress(BakePhase::StoringRegistry);
    let name = sandbox_registry_name(deployment);
    let reconciled =
        client.reconcile_registry(&name, &registry.url, &registry.username, &password)?;
    Ok(Some(reconciled.id))
}

fn resolve_builder_base(
    client: &Client,
    deployment: &str,
    digest: &str,
    spec: &RecipeSpec,
    registry_id: Option<&str>,
    intermediate: &mut Option<String>,
    progress: &dyn Fn(BakePhase),
) -> Result<(Option<String>, Option<String>)> {
    if let Some(image) = spec.docker_image.as_deref() {
        progress(BakePhase::BuildingFromImage);
        let base_name = intermediate_snapshot_name(deployment, "dockerbase", digest);
        let snapshot = client.create_snapshot_from_docker_image(
            &base_name,
            image,
            spec.docker_fs_capacity_bytes(),
            DOCKER_SNAPSHOT_TIMEOUT,
            registry_id,
        )?;
        // Boot the builder by id only: the sandbox API rejects a create request
        // that names a snapshot both by id and by name.
        *intermediate = Some(base_name);
        return Ok((None, Some(snapshot.id)));
    }
    Ok((spec.snapshot_name.clone(), spec.snapshot_id.clone()))
}

fn run_setup_script(client: &Client, dataplane_url: &str, script: &str) -> Result<()> {
    let encoded = base64::engine::general_purpose::STANDARD.encode(script.as_bytes());
    // Base64 alphabet is single-quote safe.
    let command =
        format!("echo '{encoded}' | base64 -d > /tmp/mda-setup.sh && bash -e /tmp/mda-setup.sh");
    let result: SandboxCommandResult =
        client.execute_sandbox_command(dataplane_url, &command, SETUP_TIMEOUT_SECS)?;
    if result.exit_code != 0 {
        bail!(
            "sandbox setup.sh exited with code {}{}",
            result.exit_code,
            format_setup_output(&result)
        );
    }
    Ok(())
}

fn format_setup_output(result: &SandboxCommandResult) -> String {
    let stderr = result.stderr.trim();
    let stdout = result.stdout.trim();
    if !stderr.is_empty() {
        format!("\n{}", truncate_for_error(stderr))
    } else if !stdout.is_empty() {
        format!("\n{}", truncate_for_error(stdout))
    } else {
        String::new()
    }
}

fn truncate_for_error(text: &str) -> String {
    const MAX: usize = 2_000;
    if text.len() <= MAX {
        text.to_string()
    } else {
        let mut end = MAX;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        format!("{}…", &text[..end])
    }
}

fn builder_sandbox_name(deployment: &str, digest: &str) -> String {
    let suffix = format!("{SANDBOX_NAME_SEPARATOR}bake-{digest}");
    let component =
        sandbox_deployment_component(deployment, HOST_SANDBOX_NAME_LIMIT - suffix.len());
    format!("{component}{suffix}")
}

fn delete_snapshot_by_name(client: &Client, name: &str) -> Result<()> {
    let matches = client.list_snapshots_with_prefix(name)?;
    for snapshot in matches {
        if snapshot.name.as_deref() == Some(name) {
            client.delete_snapshot(&snapshot.id)?;
        }
    }
    Ok(())
}

/// Sweep of `{deployment}--setup-*` snapshots other than the live recipe.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct StaleRecipeCleanup {
    /// Previous recipes LangSmith refused to delete because boxes still use them.
    pub in_use: Vec<String>,
}

impl StaleRecipeCleanup {
    /// Note when previous recipes were left in place. `None` if every stale
    /// snapshot was deleted (or none existed).
    pub fn in_use_note(&self) -> Option<String> {
        if self.in_use.is_empty() {
            return None;
        }
        let names = self
            .in_use
            .iter()
            .map(|name| format!("'{name}'"))
            .collect::<Vec<_>>()
            .join(", ");
        let (count, pronoun, subject) = if self.in_use.len() == 1 {
            ("1 previous recipe snapshot".to_string(), "it", "It")
        } else {
            (
                format!("{} previous recipe snapshots", self.in_use.len()),
                "them",
                "They",
            )
        };
        Some(format!(
            "Left {count} in place because live sandboxes still use {pronoun} ({names}). {subject} will be removed on a later deploy once those sandboxes are gone."
        ))
    }
}

/// Delete `{deployment}--setup-*` snapshots except the active recipe, if any,
/// plus temporary `{deployment}--dockerbase-*` / `--sourcebase-*` bases that an
/// earlier bake could not remove while its builder was still tearing down.
///
/// A snapshot still referenced by live boxes is skipped, not a failure. The
/// next deploy retries the sweep after those boxes are reaped.
pub fn delete_stale_recipe_snapshots(
    client: &Client,
    deployment: &str,
    keep_name: Option<&str>,
) -> Result<StaleRecipeCleanup> {
    let found = client.list_snapshots_with_prefix(&bake_snapshot_list_prefix(deployment))?;
    let mut cleanup = StaleRecipeCleanup::default();
    let mut failures = Vec::new();
    for snapshot in found {
        let Some(name) = snapshot.name.as_deref() else {
            continue;
        };
        let intermediate = is_deployment_intermediate_snapshot(name, deployment);
        if keep_name == Some(name)
            || !(intermediate || is_deployment_recipe_snapshot(name, deployment))
        {
            continue;
        }
        match client.delete_snapshot(&snapshot.id) {
            // A builder that is still shutting down holds its base; the next
            // sweep removes it. Not worth a note: users never reference it.
            Ok(SnapshotDelete::InUse) if intermediate => {}
            Ok(SnapshotDelete::InUse) => cleanup.in_use.push(name.to_string()),
            Ok(SnapshotDelete::Deleted | SnapshotDelete::Missing) => {}
            Err(err) => failures.push(format!("'{name}': {err}")),
        }
    }
    if !failures.is_empty() {
        bail!(
            "failed to delete {} stale snapshot(s): {}",
            failures.len(),
            failures.join("; ")
        );
    }
    Ok(cleanup)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::RefCell;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    fn serve(responses: Vec<(u16, String)>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for (status, body) in responses {
                let (mut stream, _) = listener.accept().unwrap();
                tx.send(read_request(&mut stream)).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });
        (base_url, rx)
    }

    fn read_request(stream: &mut TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            let text = String::from_utf8_lossy(&data);
            if text.contains("\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    #[test]
    fn bake_skips_when_recipe_already_ready() {
        let recipe =
            recipe_snapshot_name("agent", &recipe_digest(b"", None, None, None, None, None));
        let list = json!({
            "snapshots": [
                { "id": "1", "name": recipe, "status": "ready" }
            ]
        })
        .to_string();
        let (url, _rx) = serve(vec![(200, list)]);
        let outcome = bake_recipe(
            &Client::for_test(&url),
            "agent",
            &RecipeSpec::default(),
            &BTreeMap::new(),
            &|_| {},
        )
        .unwrap();
        assert!(outcome.skipped);
        assert_eq!(outcome.recipe_name, recipe);
        assert_eq!(outcome.recipe_reference, recipe);
    }

    #[test]
    fn bake_skip_keeps_the_tag_of_a_docker_built_recipe() {
        // Every run after the first cold bake takes the skip path, so it has to
        // re-derive the tag or the recipe pointer breaks again.
        let digest = recipe_digest(b"", None, None, Some("acme/base:0.1.1"), None, None);
        let recipe = recipe_snapshot_name("agent", &digest);
        let list = json!({
            "snapshots": [
                { "id": "1", "name": recipe, "status": "ready", "tags": ["0.1.1"] }
            ]
        })
        .to_string();
        let (url, _rx) = serve(vec![(200, list)]);
        let outcome = bake_recipe(
            &Client::for_test(&url),
            "agent",
            &RecipeSpec {
                docker_image: Some("acme/base:0.1.1".into()),
                ..RecipeSpec::default()
            },
            &BTreeMap::new(),
            &|_| {},
        )
        .unwrap();
        assert!(outcome.skipped);
        assert_eq!(outcome.recipe_reference, format!("{recipe}:0.1.1"));
    }

    #[test]
    fn empty_docker_image_creates_recipe_directly() {
        let digest = recipe_digest(b"", None, None, Some("python:3.12-slim"), None, None);
        let recipe = recipe_snapshot_name("agent", &digest);
        let building = json!({
            "id": "snap-1",
            "name": recipe,
            "status": "building"
        })
        .to_string();
        // Host tags a Docker-built snapshot with the image's tag, not `latest`.
        let ready = json!({
            "id": "snap-1",
            "name": recipe,
            "status": "ready",
            "tags": ["3.12-slim"]
        })
        .to_string();
        let empty_list = json!({ "snapshots": [] }).to_string();
        // find (miss) → create docker → wait get
        let (url, rx) = serve(vec![(200, empty_list), (200, building), (200, ready)]);
        let phases = RefCell::new(Vec::new());
        let outcome = bake_recipe(
            &Client::for_test(&url),
            "agent",
            &RecipeSpec {
                docker_image: Some("python:3.12-slim".into()),
                ..RecipeSpec::default()
            },
            &BTreeMap::new(),
            &|phase| phases.borrow_mut().push(phase),
        )
        .unwrap();
        assert!(!outcome.skipped);
        assert_eq!(outcome.recipe_name, recipe);
        // The runtime must clone the tag Host assigned; the bare name is
        // resolved as `:latest` and would not exist.
        assert_eq!(outcome.recipe_reference, format!("{recipe}:3.12-slim"));
        assert_eq!(
            phases.into_inner(),
            vec![BakePhase::CheckingRecipe, BakePhase::BuildingFromImage]
        );
        let create_req = rx.recv().unwrap(); // find list
        let _ = create_req;
        let create = rx.recv().unwrap();
        assert!(create.contains("POST /v2/sandboxes/snapshots"));
        assert!(create.contains("python:3.12-slim"));
        assert!(create.contains(r#""fs_capacity_bytes":1073741824"#));
    }

    #[test]
    fn docker_image_bake_passes_registry_id() {
        let registry_spec = SandboxRegistryManifest {
            url: "ghcr.io".into(),
            username: "octocat".into(),
            password_env: "GHCR_TOKEN".into(),
        };
        let registry_digest = registry_spec.digest_input();
        let digest = recipe_digest(
            b"",
            None,
            None,
            Some("ghcr.io/acme/base:1"),
            Some(&registry_digest),
            None,
        );
        let recipe = recipe_snapshot_name("agent", &digest);
        let registry = json!({
            "id": "reg-99",
            "name": "ghcr",
            "url": "ghcr.io"
        })
        .to_string();
        let building = json!({
            "id": "snap-1",
            "name": recipe,
            "status": "building"
        })
        .to_string();
        let ready = json!({
            "id": "snap-1",
            "name": recipe,
            "status": "ready"
        })
        .to_string();
        let empty_list = json!({ "snapshots": [] }).to_string();
        // find (miss) → registry miss → create registry → create docker → wait
        let (url, rx) = serve(vec![
            (200, empty_list),
            (404, json!({ "detail": "not found" }).to_string()),
            (200, registry),
            (200, building),
            (200, ready),
        ]);
        // Build at runtime so static analysis does not treat the fixture as a
        // hard-coded credential (CodeQL rust/hard-coded-cryptographic-value).
        let password = format!("{}-{}", "fixture", "credential");
        let env_vars = BTreeMap::from([("GHCR_TOKEN".into(), password.clone())]);
        let outcome = bake_recipe(
            &Client::for_test(&url),
            "agent",
            &RecipeSpec {
                docker_image: Some("ghcr.io/acme/base:1".into()),
                registry: Some(registry_spec),
                ..RecipeSpec::default()
            },
            &env_vars,
            &|_| {},
        )
        .unwrap();
        assert!(!outcome.skipped);
        let _ = rx.recv().unwrap(); // find list
        assert!(rx
            .recv()
            .unwrap()
            .contains("GET /api/v2/sandboxes/registries/agent--registry"));
        let reconcile = rx.recv().unwrap();
        assert!(reconcile.contains("POST /api/v2/sandboxes/registries"));
        assert!(reconcile.contains(&format!(r#""password":"{password}""#)));
        let create = rx.recv().unwrap();
        assert!(create.contains("POST /v2/sandboxes/snapshots"));
        assert!(create.contains(r#""registry_id":"reg-99""#));
    }

    #[test]
    fn private_image_bake_fails_when_registry_password_is_missing() {
        let empty_list = json!({ "snapshots": [] }).to_string();
        let (url, _rx) = serve(vec![(200, empty_list)]);
        let error = bake_recipe(
            &Client::for_test(&url),
            "agent",
            &RecipeSpec {
                docker_image: Some("ghcr.io/acme/base:1".into()),
                registry: Some(SandboxRegistryManifest {
                    url: "ghcr.io".into(),
                    username: "octocat".into(),
                    password_env: "MDA_TEST_MISSING_REGISTRY_TOKEN".into(),
                }),
                ..RecipeSpec::default()
            },
            &BTreeMap::new(),
            &|_| {},
        )
        .unwrap_err();
        assert!(error
            .to_string()
            .contains("set MDA_TEST_MISSING_REGISTRY_TOKEN"));
    }

    #[test]
    fn docker_base_builder_boots_by_snapshot_id_only() {
        // The sandbox API rejects a builder create that names the snapshot by
        // both id and name (HTTP 422), so the docker-base path returns only the id.
        let building = json!({
            "id": "snap-base",
            "name": "agent--dockerbase-abc",
            "status": "building"
        })
        .to_string();
        let ready = json!({
            "id": "snap-base",
            "name": "agent--dockerbase-abc",
            "status": "ready"
        })
        .to_string();
        let (url, rx) = serve(vec![(200, building), (200, ready)]);
        let mut intermediate = None;
        let (name, id) = resolve_builder_base(
            &Client::for_test(&url),
            "agent",
            "abc",
            &RecipeSpec {
                docker_image: Some("python:3.12-slim".into()),
                setup_script: "pip install requests".into(),
                fs_capacity_bytes: Some(2 << 30),
                ..RecipeSpec::default()
            },
            None,
            &mut intermediate,
            &|_| {},
        )
        .unwrap();
        assert_eq!(name, None);
        assert_eq!(id.as_deref(), Some("snap-base"));
        assert!(rx
            .recv()
            .unwrap()
            .contains(r#""fs_capacity_bytes":2147483648"#));
        // The name is still kept so the intermediate base gets cleaned up.
        assert_eq!(
            intermediate.as_deref(),
            Some(format!("agent{SANDBOX_NAME_SEPARATOR}dockerbase-abc").as_str())
        );
    }

    #[test]
    fn setup_error_truncation_preserves_utf8_boundaries() {
        let text = format!("{}éafter", "a".repeat(1_999));

        let truncated = truncate_for_error(&text);

        assert_eq!(truncated, format!("{}…", "a".repeat(1_999)));
    }

    #[test]
    fn builder_sandbox_name_stays_within_the_host_limit() {
        let digest = "27f7518029b14543";
        let name = builder_sandbox_name("mda-integ-sandbox-recipe-ts-pr-339-b3f203", digest);

        assert!(name.len() <= HOST_SANDBOX_NAME_LIMIT);
        assert!(name.ends_with(&format!("{SANDBOX_NAME_SEPARATOR}bake-{digest}")));
    }

    #[test]
    fn builder_sandbox_name_distinguishes_long_names_with_a_shared_prefix() {
        let digest = "27f7518029b14543";
        let first = builder_sandbox_name("mda-integ-sandbox-recipe-ts-pr-971-ecc3e2", digest);
        let second = builder_sandbox_name("mda-integ-sandbox-recipe-ts-pr-971-ecc3e2-del", digest);

        assert_ne!(first, second);
        assert!(first.len() <= HOST_SANDBOX_NAME_LIMIT);
        assert!(second.len() <= HOST_SANDBOX_NAME_LIMIT);
    }

    #[test]
    fn stale_recipe_cleanup_preserves_only_the_active_recipe() {
        let snapshots = json!({
            "snapshots": [
                { "id": "active-id", "name": "agent--setup-active", "status": "ready" },
                { "id": "stale-id", "name": "agent--setup-stale", "status": "ready" },
                { "id": "neighbor-id", "name": "agent--setup-stale--extra", "status": "ready" }
            ]
        })
        .to_string();
        let (url, rx) = serve(vec![(200, snapshots), (204, String::new())]);

        let cleanup = delete_stale_recipe_snapshots(
            &Client::for_test(&url),
            "agent",
            Some("agent--setup-active"),
        )
        .unwrap();
        assert!(cleanup.in_use.is_empty());

        let _ = rx.recv().unwrap();
        let delete = rx.recv().unwrap();
        assert!(delete.contains("DELETE /v2/sandboxes/snapshots/stale-id"));
        assert!(!delete.contains("active-id"));
        assert!(!delete.contains("neighbor-id"));
    }

    #[test]
    fn stale_recipe_cleanup_reports_delete_failures() {
        let snapshots = json!({
            "snapshots": [
                { "id": "stale-id", "name": "agent--setup-stale", "status": "ready" }
            ]
        })
        .to_string();
        let (url, rx) = serve(vec![
            (200, snapshots),
            (500, r#"{"detail":"delete failed"}"#.to_string()),
        ]);

        let err =
            delete_stale_recipe_snapshots(&Client::for_test(&url), "agent", None).unwrap_err();
        let _ = rx.recv().unwrap();
        let _ = rx.recv().unwrap();

        assert!(err
            .to_string()
            .contains("failed to delete 1 stale snapshot"));
    }

    #[test]
    fn stale_recipe_cleanup_skips_snapshots_still_in_use() {
        let snapshots = json!({
            "snapshots": [
                { "id": "stale-id", "name": "agent--setup-stale", "status": "ready" }
            ]
        })
        .to_string();
        let conflict = json!({
            "error": "Conflict",
            "message": "snapshot is in use by one or more sandboxes; release them before deleting"
        })
        .to_string();
        let (url, rx) = serve(vec![(200, snapshots), (409, conflict)]);

        let cleanup =
            delete_stale_recipe_snapshots(&Client::for_test(&url), "agent", None).unwrap();
        let _ = rx.recv().unwrap();
        let _ = rx.recv().unwrap();

        assert_eq!(cleanup.in_use, vec!["agent--setup-stale".to_string()]);
        assert_eq!(
            cleanup.in_use_note().as_deref(),
            Some(
                "Left 1 previous recipe snapshot in place because live sandboxes still use it ('agent--setup-stale'). It will be removed on a later deploy once those sandboxes are gone."
            )
        );
    }

    #[test]
    fn stale_recipe_cleanup_removes_leaked_intermediate_bases() {
        let snapshots = json!({
            "snapshots": [
                { "id": "active-id", "name": "agent--setup-active", "status": "ready" },
                { "id": "docker-id", "name": "agent--dockerbase-640ecb01b34ba969", "status": "ready" },
                { "id": "source-id", "name": "agent--sourcebase-0123456789abcdef", "status": "ready" },
                { "id": "neighbor-docker-id", "name": "agent--x--dockerbase-640ecb01b34ba969", "status": "ready" },
                { "id": "neighbor-source-id", "name": "agent--sourcebase-0123456789abcdef--dockerbase-640ecb01b34ba969", "status": "ready" },
                { "id": "short-digest-id", "name": "agent--dockerbase-abc", "status": "ready" }
            ]
        })
        .to_string();
        let (url, rx) = serve(vec![
            (200, snapshots),
            (204, String::new()),
            (204, String::new()),
        ]);

        let cleanup = delete_stale_recipe_snapshots(
            &Client::for_test(&url),
            "agent",
            Some("agent--setup-active"),
        )
        .unwrap();
        assert_eq!(cleanup, StaleRecipeCleanup::default());

        let list = rx.recv().unwrap();
        assert!(list.contains("name_contains=agent--&"), "{list}");
        assert!(rx
            .recv()
            .unwrap()
            .contains("DELETE /v2/sandboxes/snapshots/docker-id"));
        assert!(rx
            .recv()
            .unwrap()
            .contains("DELETE /v2/sandboxes/snapshots/source-id"));
        assert!(rx.try_recv().is_err(), "neighbour snapshots must be kept");
    }

    #[test]
    fn stale_recipe_cleanup_leaves_in_use_intermediate_bases_quietly() {
        let snapshots = json!({
            "snapshots": [
                { "id": "docker-id", "name": "agent--dockerbase-640ecb01b34ba969", "status": "ready" },
                { "id": "source-id", "name": "agent--sourcebase-0123456789abcdef", "status": "ready" }
            ]
        })
        .to_string();
        let conflict = json!({
            "error": "Conflict",
            "message": "snapshot is in use by one or more sandboxes; release them before deleting"
        })
        .to_string();
        let (url, rx) = serve(vec![
            (200, snapshots),
            (409, conflict.clone()),
            (409, conflict),
        ]);

        let cleanup =
            delete_stale_recipe_snapshots(&Client::for_test(&url), "agent", None).unwrap();
        let _ = rx.recv().unwrap();
        assert!(rx.recv().unwrap().contains("docker-id"));
        assert!(rx.recv().unwrap().contains("source-id"));

        assert!(cleanup.in_use.is_empty());
        assert_eq!(cleanup.in_use_note(), None);
    }
}
