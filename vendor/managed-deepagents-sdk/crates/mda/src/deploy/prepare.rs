//! `mda prepare` — the headless, pre-build half of `mda deploy`, run inside a
//! LangSmith build job against a cloned GitHub repository.
//!
//! Host-backend owns the deployment (it already exists, with a revision in
//! BUILDING), so this command never creates, uploads, or waits. It does the
//! steps that need the project source and the LangSmith platform before
//! `langgraph build` can run:
//!
//! 1. Preflight (model key, ingress secret, sandbox entitlement) against the
//!    deployment's secrets and the workspace's secrets.
//! 2. Context Hub sync with the repository always winning.
//! 3. Compile into `.mda/build`.
//! 4. Bake the sandbox recipe snapshot.
//! 5. Emit connector / channel requirements and verify their secrets.
//! 6. Report build artifacts (snapshot reference, cron payloads, channel
//!    manifest) to host-backend for the deploy and reconcile steps.
//!
//! Nothing here prompts. Every decision `mda deploy` asks a human about is
//! either fixed by policy (repository wins) or a hard failure.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use reqwest::header::HeaderValue;
use serde_json::{json, Value};

use super::helpers::{
    deployment_is_managed_deep_agent, secrets_from_env, RESERVED_ENV_VARS, WORKSPACE_ID_ENV,
};
use super::langsmith_context::LangSmithContext;
use super::{
    bake_sandbox_recipe_step, compile_for_deploy_step, emit_requirements_step,
    ensure_channel_requirements_secrets, ensure_connector_requirements_secrets,
    ensure_ingress_secret, ensure_model_api_key, ensure_project, ensure_workspace_connections,
    link_context_repo_to_agent, recipe_spec_from_project, schedule_cron_request, sync_context_hub,
    trigger_channels, warn_unprovided_reserved_connections, ChannelSetup, ContextStrategy,
    EmittedRequirements, WorkspaceSecretKeys,
};
use crate::agent_config;
use crate::cli_output::{show_sequence, SequenceItem};
use crate::command::CommandCompletion;
use crate::error::{categorized, category_of, ErrorCategory};
use crate::install::Installation;
use crate::langsmith::{
    self, require_str, resolve_preferred_env, ApiKey, ContextHubClient, RepoOwnership, API_KEY_ENV,
    REMOVED_API_KEY_ENVS,
};
use crate::telemetry::{otel_logger, update_facts};
use crate::{compile, project};

/// Context Hub repo tag marking a deployment whose context is owned by a Git
/// repository rather than edited in the Hub.
pub(crate) const GITHUB_SOURCED_REPO_TAG: &str = "managed-deepagents:github";

/// Options for `mda prepare`.
pub struct PrepareOptions {
    /// Captured at CLI startup and removed from the inherited environment.
    pub build_artifacts_token: Option<OsString>,
    /// Path to the Managed Deep Agent project inside the checkout.
    pub path: PathBuf,
    /// Output directory for the compiled app (defaults to `<path>/.mda/build`).
    pub out: Option<PathBuf>,
    /// Deployment the build belongs to.
    pub deployment_id: String,
    /// Revision the build belongs to; artifacts are stored against it.
    pub revision_id: String,
    /// JSON object file holding the deployment's secrets (the repository has
    /// no `.env`). JSON, not dotenv: values pass through byte-for-byte with no
    /// quoting or `$VAR` interpolation rules to get wrong.
    pub env_json: Option<PathBuf>,
    /// Host backend URL override.
    pub host_url: Option<String>,
    /// Preview deployments prepare but never reconcile schedules or channels.
    pub is_preview: bool,
    /// Deployment name to bake sandbox snapshots under (a preview reuses its
    /// parent's content-addressed snapshots this way).
    pub snapshot_owner: Option<String>,
}

/// What a successful prepare leaves behind, for the closing summary.
struct Prepared {
    deployment: String,
    out_dir: PathBuf,
    compiled_files: usize,
}

/// Run `mda prepare` end-to-end.
pub fn run(opts: PrepareOptions) -> Result<CommandCompletion> {
    update_facts(|facts| facts.deployment.deployment_id = Some(opts.deployment_id.clone()));
    // Fail before compilation or Context Hub writes if the runner is incompatible.
    let build_token = build_artifacts_token(opts.build_artifacts_token.as_deref())?;
    ensure_project(&opts.path)?;
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;

    match run_inner(&opts, &project, &installation, &build_token) {
        Ok(prepared) => {
            show_sequence(&prepared_items(&project, &prepared))?;
            Ok(CommandCompletion::Succeeded)
        }
        Err(err) => {
            otel_logger::error(&err);
            show_sequence(&failed_items(&project, &err))?;
            Ok(CommandCompletion::Failed(category_of(&err)))
        }
    }
}

/// Validate without including the credential in diagnostics or header debug output.
fn build_artifacts_token(raw: Option<&std::ffi::OsStr>) -> Result<HeaderValue> {
    let name = langsmith::BUILD_ARTIFACTS_TOKEN_ENV;
    let value = raw
        .and_then(std::ffi::OsStr::to_str)
        .filter(|s| !s.trim().is_empty());
    let value = value.ok_or_else(|| {
        categorized(
            ErrorCategory::Authentication,
            format!(
                "{name} is required; the build runner must provide the revision build credential"
            ),
        )
    })?;
    let mut header = HeaderValue::from_str(value).map_err(|_| {
        categorized(
            ErrorCategory::Authentication,
            format!("{name} is not a valid HTTP header value"),
        )
    })?;
    header.set_sensitive(true);
    Ok(header)
}

fn prepared_items(project: &project::Project, prepared: &Prepared) -> Vec<SequenceItem> {
    vec![SequenceItem::section(
        format!("Prepared '{}' for build", prepared.deployment),
        [
            format!("Project: {}", project.root.display()),
            format!(
                "Compiled {} file(s) into {}",
                prepared.compiled_files,
                prepared.out_dir.display()
            ),
        ],
    )]
}

fn failed_items(project: &project::Project, err: &anyhow::Error) -> Vec<SequenceItem> {
    vec![SequenceItem::section(
        "Prepare failed",
        [
            format!("Project: {}", project.root.display()),
            format!("{err:#}"),
        ],
    )]
}

fn run_inner(
    opts: &PrepareOptions,
    project: &project::Project,
    installation: &Installation,
    build_token: &HeaderValue,
) -> Result<Prepared> {
    // -- 1. Credentials + deployment secrets --------------------------------
    // A checkout has no `.env`; host-backend hands the deployment's secrets to
    // the build job and the runner writes them to `--env-json`. Reserved keys
    // are dropped on read, so `LANGSMITH_ENDPOINT` / `LANGSMITH_API_KEY` come
    // only from `--host-url` and the runner's own process environment.
    let env_vars = match opts.env_json.as_deref() {
        Some(path) => read_env_json(path)?,
        None => BTreeMap::new(),
    };
    let ctx = LangSmithContext::resolve(&env_vars, opts.host_url.as_deref(), None, "", |_| {
        resolve_api_key_headless(&env_vars)
    })?;
    if ctx.workspace_id.is_none() {
        bail!("{WORKSPACE_ID_ENV} is required so platform calls are scoped to the deployment's workspace");
    }

    let client = ctx.client()?;
    let dashboard_url = client.dashboard_url().ok();

    let deployment = client
        .get_deployment(&opts.deployment_id)
        .context("failed to load the deployment this build belongs to")?;
    if !deployment_is_managed_deep_agent(&deployment) {
        bail!(
            "deployment {} is not a Managed Deep Agent deployment",
            opts.deployment_id
        );
    }
    let name = require_str(&deployment, "name", "deployment")?;
    update_facts(|facts| facts.deployment.deployment_name = Some(name.to_string()));

    let agent = agent_config::read_agent_static_config(project)?;
    let assistant_id = agent.assistant_id.clone();
    // The deployment's secrets stand in for `.env` in every preflight; the
    // host-backend-owned MDA_* values are already excluded by the reserved list.
    let secrets = secrets_from_env(&env_vars);

    // -- 2. Preflight -------------------------------------------------------
    let mut workspace_keys = WorkspaceSecretKeys::Unloaded;
    ensure_model_api_key(
        agent.model.as_deref(),
        project,
        &env_vars,
        &secrets,
        &client,
        dashboard_url.as_deref(),
        &mut workspace_keys,
    )?;
    ensure_ingress_secret(
        project,
        &env_vars,
        &secrets,
        &client,
        dashboard_url.as_deref(),
        &mut workspace_keys,
    )?;
    if compile::project_uses_sandbox(project)? {
        client.ensure_sandbox_access().context(
            "project declares a managed sandbox, but sandbox access is unavailable for this organization",
        )?;
    }
    let memory = compile::resolve_memory(project)?;

    // -- 3. Context Hub sync: the repository wins ---------------------------
    // The Hub UI reads this tag to make GitHub-owned instructions and skills
    // read-only there (ADR 0001): the repository wins on every build.
    let context_client = ctx
        .context_hub_client()?
        .with_extra_repo_tags([GITHUB_SOURCED_REPO_TAG.to_string()]);
    ensure_hub_repo_is_ours(&context_client, &name)?;
    let context = sync_context_hub(
        &context_client,
        project,
        &name,
        memory.agent_enabled(),
        Some(ContextStrategy::Overwrite),
    )?;
    link_context_repo_to_agent(&client, &context_client, &opts.deployment_id, &name)?;

    // -- 4. Compile ---------------------------------------------------------
    let out_dir = opts
        .out
        .clone()
        .unwrap_or_else(|| project.root.join(".mda").join("build"));
    let summary = compile_for_deploy_step(project, &out_dir, context, installation)?;

    // -- 5. Bake ------------------------------------------------------------
    let snapshot_owner = opts.snapshot_owner.as_deref().unwrap_or(&name);
    let mut bake_only_secrets = Vec::new();
    let sandbox_recipe_snapshot = match recipe_spec_from_project(project, &out_dir)? {
        Some(spec) => {
            let (outcome, registry_password_env) =
                bake_sandbox_recipe_step(&client, snapshot_owner, &env_vars, &spec)?;
            bake_only_secrets.extend(registry_password_env);
            Some(outcome.recipe_reference)
        }
        None => None,
    };

    // -- 6. Connector / channel requirements --------------------------------
    let EmittedRequirements {
        connectors: connector_requirements,
        mcp_connections,
        channels: channel_requirements,
    } = emit_requirements_step(project, &out_dir, &summary)?;
    ensure_workspace_connections(
        &client,
        ctx.workspace_id.as_deref(),
        &connector_requirements,
        &channel_requirements,
        &mcp_connections,
    )?;
    warn_unprovided_reserved_connections(&connector_requirements, &channel_requirements)?;
    // Validation only: setup needs the live deployment.
    let channel_setup = ChannelSetup::prepare(
        &channel_requirements,
        true,
        &project.root,
        &project.files,
        project.language,
        &assistant_id,
    )?;
    ensure_connector_requirements_secrets(
        project,
        &env_vars,
        &secrets,
        &client,
        dashboard_url.as_deref(),
        &connector_requirements,
        &mut workspace_keys,
    )?;
    ensure_channel_requirements_secrets(
        project,
        &env_vars,
        &secrets,
        &client,
        dashboard_url.as_deref(),
        &channel_requirements,
        &mut workspace_keys,
    )?;

    // -- 7. Report build artifacts -----------------------------------------
    let artifacts = build_artifacts(
        &assistant_id,
        sandbox_recipe_snapshot.as_deref(),
        &bake_only_secrets,
        &summary.schedule_crons,
        channel_setup.trigger(),
        !opts.is_preview,
    )?;
    client
        .put_managed_deep_agent_artifacts(
            &opts.deployment_id,
            &opts.revision_id,
            artifacts,
            build_token,
        )
        .context("failed to report build artifacts to LangSmith")?;

    Ok(Prepared {
        deployment: name,
        out_dir,
        compiled_files: summary.copied.len(),
    })
}

/// Deployment secrets as a flat string map. Non-string JSON values are
/// rejected rather than coerced: a secret is text or it is a mistake.
fn read_env_json(path: &Path) -> Result<BTreeMap<String, String>> {
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let value: Value = serde_json::from_str(&raw)
        .with_context(|| format!("{} is not valid JSON", path.display()))?;
    let Value::Object(entries) = value else {
        bail!(
            "{} must hold a JSON object of env var names to values",
            path.display()
        );
    };
    entries
        .into_iter()
        // Deployment owners set these secrets; never let them steer where the
        // runner's platform credential is sent.
        .filter(|(key, _)| !RESERVED_ENV_VARS.contains(&key.as_str()))
        .map(|(key, value)| match value {
            Value::String(text) => Ok((key, text)),
            _ => bail!("env var {key} must be a string"),
        })
        .collect()
}

/// The API key from the env file or process env, never a prompt.
fn resolve_api_key_headless(env_vars: &BTreeMap<String, String>) -> Result<ApiKey> {
    match resolve_preferred_env(
        API_KEY_ENV,
        REMOVED_API_KEY_ENVS,
        None,
        env_vars,
        |key| std::env::var(key).ok(),
        " instead (same value, renamed)",
    )? {
        Some(key) => Ok(ApiKey::new(key)),
        None => Err(categorized(
            ErrorCategory::Authentication,
            format!("{API_KEY_ENV} is required; `mda prepare` never prompts"),
        )),
    }
}

/// A same-name Context Hub repo that `mda` did not create is someone else's
/// data. Overwriting it is the one thing the repository-wins policy must not
/// do, so fail instead of adopting it.
fn ensure_hub_repo_is_ours(context_client: &ContextHubClient, name: &str) -> Result<()> {
    if context_client.repo_ownership(name)? == RepoOwnership::Foreign {
        bail!(
            "Context Hub repo '{name}' already exists and was not created by Managed Deep Agents. \
             Rename the deployment or remove that repo, then rebuild."
        );
    }
    Ok(())
}

/// Everything host-backend needs from the build after `mda prepare` exits.
///
/// Cron payloads are emitted in the exact shape the Agent Server accepts so
/// the reconcile step is HTTP only.
fn build_artifacts(
    assistant_id: &str,
    sandbox_recipe_snapshot: Option<&str>,
    bake_only_secrets: &[String],
    schedule_crons: &compile::schedules::ScheduleCrons,
    trigger_channel: Option<&trigger_channels::PreparedTriggerChannel>,
    reconcile: bool,
) -> Result<Value> {
    let schedules = schedule_crons
        .iter()
        .map(|(name, cron)| {
            let (thread_id, payload) = schedule_cron_request(name, cron)?;
            Ok(json!({ "name": name, "thread_id": thread_id, "payload": payload }))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(json!({
        "version": 1,
        "assistant_id": assistant_id,
        "sandbox_recipe_snapshot": sandbox_recipe_snapshot,
        "bake_only_secrets": bake_only_secrets,
        "schedules": schedules,
        "trigger_channel": trigger_channel.map(trigger_channels::artifact),
        "reconcile": reconcile,
    }))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use serde_json::json;

    use super::{build_artifacts, read_env_json};
    use crate::deploy::helpers::resolve_context_hub_base_url;

    #[test]
    fn build_token_is_required_and_never_in_error_or_debug_output() {
        for value in [
            None,
            Some(""),
            Some("  "),
            Some("private-token\r\nInjected: yes"),
        ] {
            let err = super::build_artifacts_token(value.map(std::ffi::OsStr::new)).unwrap_err();
            assert_eq!(
                crate::error::category_of(&err),
                crate::error::ErrorCategory::Authentication
            );
            assert!(err
                .to_string()
                .contains(crate::langsmith::BUILD_ARTIFACTS_TOKEN_ENV));
            assert!(!format!("{err:#}").contains("private-token"));
        }
        let token =
            super::build_artifacts_token(Some(std::ffi::OsStr::new("private-token"))).unwrap();
        assert_eq!(token.to_str().unwrap(), "private-token");
        assert!(token.is_sensitive());
        assert!(!format!("{token:?}").contains("private-token"));
    }

    #[test]
    fn deployment_secrets_cannot_supply_or_forward_the_build_token() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("env.json");
        std::fs::write(
            &path,
            r#"{"MDA_BUILD_ARTIFACTS_TOKEN":"untrusted-token","LANGSMITH_ENDPOINT":"https://untrusted.example","OPENAI_API_KEY":"app-key"}"#,
        )
        .unwrap();
        let env = read_env_json(&path).unwrap();
        assert_eq!(
            env,
            BTreeMap::from([("OPENAI_API_KEY".into(), "app-key".into())])
        );
        let raw = BTreeMap::from([(
            crate::langsmith::BUILD_ARTIFACTS_TOKEN_ENV.to_string(),
            "untrusted-token".into(),
        )]);
        assert!(crate::deploy::helpers::secrets_from_env(&raw).is_empty());
    }

    #[test]
    fn hub_url_preserves_known_mappings_and_uses_platform_for_custom_hosts() {
        for (host, platform, expected) in [
            (
                "https://pr-37428.api.review.smith.langchain.com",
                "https://pr-37428-api.review.smith.langchain.com/",
                "https://pr-37428-api.review.smith.langchain.com",
            ),
            (
                "https://host.example/",
                "https://platform.example/",
                "https://platform.example",
            ),
            (
                "https://api.host.langchain.com",
                "https://ignored.example",
                "https://api.smith.langchain.com",
            ),
            (
                "https://dev.api.host.langchain.com",
                "https://ignored.example",
                "https://dev.api.smith.langchain.com",
            ),
            (
                "http://localhost:8300",
                "https://ignored.example",
                "http://localhost:1980",
            ),
        ] {
            assert_eq!(resolve_context_hub_base_url(host, platform), expected);
        }
    }

    #[test]
    fn artifacts_carry_agent_server_shaped_cron_payloads() {
        let mut crons = BTreeMap::new();
        crons.insert(
            "daily".to_string(),
            json!({ "graph": "agent", "schedule": "0 9 * * *", "thread_id": "t-1" }),
        );
        crons.insert(
            "hourly".to_string(),
            json!({ "graph": "agent", "schedule": "0 * * * *" }),
        );

        let artifacts = build_artifacts(
            "agent",
            Some("snap:latest"),
            &["REG_PW".into()],
            &crons,
            None,
            true,
        )
        .unwrap();

        assert_eq!(artifacts["version"], 1);
        assert_eq!(artifacts["sandbox_recipe_snapshot"], "snap:latest");
        assert_eq!(artifacts["bake_only_secrets"], json!(["REG_PW"]));
        assert_eq!(artifacts["reconcile"], true);
        assert!(artifacts["trigger_channel"].is_null());
        let schedules = artifacts["schedules"].as_array().unwrap();
        assert_eq!(schedules.len(), 2);
        assert_eq!(schedules[0]["name"], "daily");
        assert_eq!(schedules[0]["thread_id"], "t-1");
        assert_eq!(schedules[0]["payload"]["assistant_id"], "agent");
        assert!(schedules[0]["payload"].get("graph").is_none());
        assert!(schedules[0]["payload"].get("thread_id").is_none());
        assert!(schedules[1]["thread_id"].is_null());
    }

    #[test]
    fn env_json_must_be_a_flat_string_object() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("env.json");
        std::fs::write(&path, r#"{"A":"x$y\n","B":"2"}"#).unwrap();
        let env = read_env_json(&path).unwrap();
        assert_eq!(env["A"], "x$y\n");
        assert_eq!(env["B"], "2");

        std::fs::write(&path, r#"{"A":1}"#).unwrap();
        assert!(read_env_json(&path).is_err());
        std::fs::write(
            &path,
            r#"{"A":"x","LANGSMITH_ENDPOINT":"https://evil.example","LANGSMITH_API_KEY":"k"}"#,
        )
        .unwrap();
        let env = read_env_json(&path).unwrap();
        assert_eq!(env.len(), 1);
        assert_eq!(env["A"], "x");
        std::fs::write(&path, r#"[]"#).unwrap();
        assert!(read_env_json(&path).is_err());
    }

    #[test]
    fn summary_and_failure_are_static_sections() {
        let project = crate::project::Project {
            root: std::path::PathBuf::from("/src/agent"),
            language: crate::project::Language::Python,
            agent_file: std::path::PathBuf::from("agent.py"),
            files: Vec::new(),
        };
        let prepared = super::Prepared {
            deployment: "my-agent".into(),
            out_dir: std::path::PathBuf::from("/src/agent/.mda/build"),
            compiled_files: 9,
        };
        let items = super::prepared_items(&project, &prepared);
        assert_eq!(items.len(), 1);
        let rendered = format!("{items:?}");
        assert!(rendered.contains("Prepared 'my-agent' for build"));
        assert!(rendered.contains("Compiled 9 file(s) into /src/agent/.mda/build"));

        let items = super::failed_items(&project, &anyhow::anyhow!("boom"));
        let rendered = format!("{items:?}");
        assert!(rendered.contains("Prepare failed"));
        assert!(rendered.contains("boom"));
    }

    #[test]
    fn preview_artifacts_never_ask_for_reconcile() {
        let artifacts = build_artifacts("agent", None, &[], &BTreeMap::new(), None, false).unwrap();
        assert_eq!(artifacts["reconcile"], false);
        assert!(artifacts["sandbox_recipe_snapshot"].is_null());
        assert_eq!(artifacts["schedules"], json!([]));
    }
}
