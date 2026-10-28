//! `mda deploy` — build a Managed Deep Agent project and ship it to LangSmith
//! Deployment.
//!
//! The flow mirrors the langgraph CLI's remote (internal-source) deploy path,
//! reimplemented in Rust with a [`cliclack`] TUI:
//!
//! 1. Validate the directory is a Managed Deep Agent project (`agent.ts` /
//!    `agent.py`).
//! 2. Resolve LangSmith credentials from the environment or `.env`. When none
//!    are set, prompt for an API key or (on Enter) run the device authorization
//!    flow.
//! 3. Verify the agent model's provider API key is available (in `.env` or as a
//!    LangSmith workspace secret) before doing any work, failing fast otherwise.
//!    When identity uses `trusted_backend` ingress, also require
//!    `MDA_INGRESS_SECRET` the same way — otherwise the agent would deploy but
//!    reject every request with 401.
//! 4. Confirm before a first Managed Deep Agent deploy overwrites a same-name
//!    Context Hub repo or non-MDA LangSmith deployment, then sync deploy-owned
//!    context into Context Hub and compile `.mda/build`.
//! 5. Look up (or create) a deployment named after the authored agent `name`
//!    (or `mda deploy -n`).
//! 6. Archive the build, request an upload URL, upload the source, and trigger
//!    a remote build.
//! 7. Poll the revision status with a live spinner until it settles.
//! 8. Reconcile Managed Deep Agent schedules through Agent Server cron APIs.

mod archive;
pub(crate) mod bake;
pub(crate) mod channel_app;
mod channel_icon;
pub(crate) mod channel_setup;
mod context_hub;
pub(crate) mod env_file;
pub(crate) mod github_channel;
pub(crate) mod helpers;
mod image;
pub(crate) mod langsmith_context;
pub(crate) mod linear_channel;
pub mod prepare;
pub(crate) mod slack_app;
mod trigger_channels;
mod trigger_icon;
mod upsell;

use std::collections::BTreeMap;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use cliclack::{confirm, log, outro, outro_cancel, password, spinner, ProgressBar};

use crate::agent_config;
use crate::cli_output::{note_links, note_links_with_section, show_wordmark_intro};
use crate::command::CommandCompletion;
use crate::error::{categorized, category_of, is_cancellation, ErrorCategory};
use crate::install::Installation;
use crate::interrupt::Interrupt;
use crate::langsmith::{
    self, is_cli_session_api_key, lookup_env_file, lookup_env_or_process, note_session_api_key,
    require_str, resolve_preferred_env, ApiKey, ConnectionSummary, ContextHubClient,
    DeploymentAgent, Login, RepoOwnership, GATEWAY_API_KEY_ENV, REMOVED_API_KEY_ENVS,
};
use crate::naming::{normalize_name, validate_agent_id};
use crate::telemetry::stage::Stage;
use crate::telemetry::{self, otel_logger, refresh_identity, update_facts, DeploymentInfo};
use crate::{compile, connection, dependencies, project};

pub use crate::langsmith::{AgentEnvironment, DeploymentType, API_KEY_ENV};
pub(crate) use helpers::{resolve_https_public_api_url, PublicApiUrl, MDA_HANDOFF_SECRET_ENV};

use bake::{bake_recipe, RecipeSpec};
use channel_setup::{ChannelSetup, SetupTarget};
use context_hub::{
    apply_context_sync, link_context_repo_to_agent, prepare_context_sync, ConflictResolution,
    ContextSection,
};
use helpers::{
    clear_recipe_snapshot_secret, deployment_is_managed_deep_agent,
    deployment_managed_deep_agent_marker, deployment_runtime_url, deployment_status_url,
    deployment_tenant_id, ensure_assistant_id_secret, ensure_cors_allow_origins,
    ensure_deployment_name_secret, ensure_langsmith_url_secret, ensure_public_api_url,
    ensure_recipe_snapshot_secret, ensure_sandbox_api_endpoint_secret, find_agent_deployment,
    find_deployment_by_name, find_deployment_id_by_name, is_failed_status, is_interrupted_status,
    is_request_timeout, is_terminal_status, secrets_from_env, set_mda_handoff_secret,
    set_workspace_id_secret, RESERVED_ENV_VARS, WORKSPACE_ID_ENV,
};
use langsmith_context::{resolve_workspace_id, LangSmithEndpoints, WORKSPACE_FLAG_HINT};

/// Delay between revision status polls.
const POLL_INTERVAL: Duration = Duration::from_secs(3);
/// Consecutive failed revision polls allowed before the deploy stops waiting.
const POLL_MAX_FAILURES: u32 = 5;

/// How an unattended deploy resolves Context Hub files edited since MDA last synced them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum ContextStrategy {
    /// Replace Hub-edited instructions or skills with their local versions.
    Overwrite,
    /// Preserve Hub-edited instructions or skills.
    KeepHub,
}

/// High-entropy, per-revision credential for internal loopback calls.
fn random_handoff_secret() -> String {
    format!(
        "{}{}",
        uuid::Uuid::new_v4().as_simple(),
        uuid::Uuid::new_v4().as_simple()
    )
}

/// User-facing options for `mda deploy`.
pub struct DeployOptions {
    /// Path to the Managed Deep Agent project.
    pub path: PathBuf,
    /// Deployment name override (defaults to the authored agent `name`).
    pub name: Option<String>,
    pub agent_environment: Option<AgentEnvironment>,
    /// Deployment type when creating.
    pub deployment_type: DeploymentType,
    /// Workspace ID override (takes precedence over `LANGSMITH_WORKSPACE_ID`).
    pub workspace_id: Option<String>,
    /// Host backend URL override.
    pub host_url: Option<String>,
    /// Customer-managed registry repository to build and push to.
    pub push_to: Option<String>,
    /// Wait for the deployment to finish (poll status) when `true`.
    pub wait: bool,
    /// How to resolve Hub-edited deploy-owned context without prompting.
    pub context_strategy: Option<ContextStrategy>,
    /// Overwrite same-name resources on a first deploy without prompting.
    pub yes: bool,
    /// Maximum time to wait for a deployment revision to reach a terminal status.
    pub wait_timeout: Duration,
}

/// Run the deploy command end-to-end.
///
/// Pre-flight validation runs before the TUI starts so those failures are
/// printed plainly by the caller. Once `intro` is on screen, any error is
/// rendered as a cancelled-session footer instead.
pub fn run(opts: DeployOptions) -> Result<CommandCompletion> {
    // Installed before anything else so Ctrl-C means the same thing in every
    // stage: it sets a flag that prompts, children, the stage checks and the
    // readiness wait each turn into a cancellation.
    let interrupt = Interrupt::install()?;
    ensure_project(&opts.path)?;
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    let root = project.root.as_path();

    show_wordmark_intro()?;
    log::info(format!("Project: {}", root.display()))?;

    let mut client = None;
    match run_session(
        &opts,
        &project,
        root,
        &installation,
        &mut client,
        &interrupt,
    ) {
        Ok(status) => Ok(status),
        Err(err) if is_cancellation(&err) => {
            outro_cancel("Cancelled")?;
            Ok(CommandCompletion::Cancelled)
        }
        Err(err) => {
            let category = category_of(&err);
            otel_logger::error(&err);
            report_session_failure(&err, client.as_ref())?;
            Ok(CommandCompletion::Failed(category))
        }
    }
}

/// Close a failed session, upgrading a plan-entitlement 403 into an upsell.
///
/// `client` is whatever `run_session` managed to build before failing: the
/// upsell's billing link is workspace-scoped, so without a client there is
/// nothing to link to and the message falls back to directions.
fn report_session_failure(err: &anyhow::Error, client: Option<&langsmith::Client>) -> Result<()> {
    if let Some(gate) = upsell::plan_gate(err) {
        let url = client.and_then(upsell::upgrade_url);
        return upsell::report(gate, url.as_deref());
    }
    outro_cancel(format!("{err:#}"))?;
    Ok(())
}

/// The interactive portion of the deploy, framed by `intro`/`outro`.
///
/// `client_slot` hands the LangSmith client back to [`run`] the moment it exists,
/// before any host-backend call can fail. That is what lets a plan-entitlement
/// 403 unwinding from anywhere in the session still be reported with a link to
/// the right organization's billing page.
fn run_session(
    opts: &DeployOptions,
    project: &project::Project,
    root: &Path,
    installation: &Installation,
    client_slot: &mut Option<langsmith::Client>,
    interrupt: &Interrupt,
) -> Result<CommandCompletion> {
    // -- 1. Target name, env + credentials ---------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Credentials);
    // One agent-file parse for deploy preflight (name / model / memory). The
    // name is resolved before credentials so a sign-in failure is still
    // reported against the deployment.
    let agent = agent_config::read_agent_static_config(project)?;
    let assistant_id = agent.assistant_id.clone();
    let base_name = match &opts.name {
        Some(n) => normalize_name(n)?,
        None => assistant_id.clone(),
    };
    let (name, deployment_agent) = agent_target(base_name, opts.agent_environment)?;
    update_facts(|facts| facts.deployment.deployment_name = Some(name.clone()));

    let env_path = root.join(".env");
    let env_vars = env_file::parse_env_file(&env_path);
    let endpoints = LangSmithEndpoints::resolve(&env_vars, opts.host_url.as_deref());
    let image_source = image::select_image_source(
        image::requires_external_image(&endpoints.host_url),
        opts.push_to.as_deref(),
    )?;
    let api_key = resolve_api_key(&env_vars, &env_path, &endpoints.platform_api_url)?;
    let workspace_id =
        resolve_workspace_id(&env_vars, opts.workspace_id.as_deref(), WORKSPACE_FLAG_HINT)?;
    let ctx = endpoints.with_auth(api_key, workspace_id);
    let platform_api_url = ctx.platform_api_url();
    let workspace_id = &ctx.workspace_id;

    let mut secrets = secrets_from_env(&env_vars);
    set_mda_handoff_secret(&mut secrets, &random_handoff_secret());
    // Scope the runtime to the same workspace the deploy clients use,
    // overriding any `LANGSMITH_WORKSPACE_ID` copied from `.env`.
    if let Some(workspace) = workspace_id {
        set_workspace_id_secret(&mut secrets, workspace);
    }
    // Let the runtime name its sandboxes after this deployment so `mda delete`
    // can find and remove them later.
    ensure_deployment_name_secret(&mut secrets, &name);
    // Channel ingress (Slack/GitHub) invokes runs by assistant / graph id —
    // always the langgraph.json graphs key, never a `--name` override.
    ensure_assistant_id_secret(&mut secrets, &assistant_id);
    // Browser CORS is owned by LangGraph. Derive `CORS_ALLOW_ORIGINS` from the
    // configured public app URL / extra origins so the browser UI can call the
    // Agent Server without a second ACAO from the managed HTTP app.
    ensure_cors_allow_origins(&mut secrets, &env_vars);

    let client: &langsmith::Client = client_slot.insert(ctx.client()?);
    let dashboard_url = client.dashboard_url().ok();
    if let Some(url) = &dashboard_url {
        ensure_langsmith_url_secret(&mut secrets, url);
    }
    // The credential may come from a login during this deploy.
    refresh_identity(client);
    otel_logger::authenticate(client);

    // -- 1b. Name where this deploy is going -------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Preflight);
    // Everything below writes remote state — Context Hub repos, secrets, the
    // deployment itself — into whichever organization and workspace the
    // credential resolves to. Say which before the first prompt that can
    // overwrite something.
    let target = client.describe_deploy_target();
    report_deploy_target(&target)?;

    // -- 2. Verify the model provider API key is available -----------------
    interrupt.check()?;
    // Direct models require a deployment key; Gateway models require a workspace
    // provider key. Check the source the model will actually use before building.
    // Fetch workspace secret keys once for model / ingress / connector checks.
    let mut workspace_keys = WorkspaceSecretKeys::Unloaded;
    ensure_model_api_key(
        agent.model.as_deref(),
        project,
        &env_vars,
        &secrets,
        client,
        dashboard_url.as_deref(),
        &mut workspace_keys,
    )?;

    // -- 2b. Verify trusted_backend ingress secret -------------------------
    interrupt.check()?;
    // Identity with `trusted_backend` HTTP ingress (every shipped preset's
    // default) rejects every request unless `MDA_INGRESS_SECRET` is set on the
    // deployment. Fail before compiling when it is absent — otherwise the
    // agent deploys but is unusable.
    ensure_ingress_secret(
        project,
        &env_vars,
        &secrets,
        client,
        dashboard_url.as_deref(),
        &mut workspace_keys,
    )?;

    // A sandbox declaration with no recipe never hits the sandbox API during
    // bake, so this has to run as a preflight. After Context Hub sync it would
    // leave remote state changed on an org that cannot run the agent.
    if compile::project_uses_sandbox(project)? {
        ensure_sandbox_api_endpoint_secret(&mut secrets, platform_api_url);
        client.ensure_sandbox_access().context(
            "project declares a managed sandbox, but sandbox access is unavailable for this organization",
        )?;
    }

    // One decision for the whole deploy: the same value the generated entry hands
    // the runtime also decides whether the Hub gets a hot-memory seed. Resolved
    // here, above the gate, because it is the last preflight that can still fail
    // on purely local input — asking to overwrite and then aborting on a bad
    // `memory` declaration would be a prompt for a deploy that never had a chance.
    let memory = compile::resolve_memory(project)?;

    let context_client = ctx.context_hub_client()?;

    // -- 2c. Confirm before overwriting same-name resources ----------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Confirm);
    // Last gate before remote state changes: a first Managed Deep Agent deploy
    // overwrites a same-name Context Hub repo or LangSmith deployment, so make
    // that explicit. Deliberately after the preflights above — a deploy that
    // cannot succeed should never ask the question.
    if !confirm_first_deploy_collisions(
        client,
        &context_client,
        &name,
        deployment_agent.as_ref(),
        &image_source,
        opts.yes,
    )? {
        outro_cancel("Cancelled — existing resources left unchanged.")?;
        return Ok(CommandCompletion::Cancelled);
    }

    // -- 3. Sync deploy-owned context to Context Hub -----------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::ContextHubSync);
    let context = sync_context_hub(
        &context_client,
        project,
        &name,
        memory.agent_enabled(),
        opts.context_strategy,
    )?;

    // -- 4. Compile --------------------------------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Compile);
    let out_dir = root.join(".mda").join("build");
    let summary = compile_for_deploy_step(project, &out_dir, context, installation)?;

    // -- 4a. Bake sandbox recipe snapshot -----------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Bake);
    // Fail closed before create/upload when setup exits non-zero or the
    // snapshot never becomes ready. A plain sandbox declaration uses Host's
    // default image and does not need a recipe snapshot.
    let recipe_spec = recipe_spec_from_project(project, &out_dir)?;
    let active_recipe_name = if let Some(spec) = recipe_spec.as_ref() {
        let (outcome, registry_password_env) =
            bake_sandbox_recipe_step(client, &name, &env_vars, spec)?;
        ensure_recipe_snapshot_secret(&mut secrets, &outcome.recipe_reference);
        // Registry credentials are bake-only. Never forward the pull token to
        // the deployed Agent Server after Host has stored it.
        if let Some(password_env) = registry_password_env {
            secrets.retain(|secret| secret.name != password_env);
        }
        Some(outcome.recipe_name)
    } else {
        clear_recipe_snapshot_secret(&mut secrets);
        None
    };

    // -- 4b. Verify connector provider secrets ------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::Deploy);
    // Manifests come from branded connector plugins via live-module emit
    // (`requirementsFromConnectors` / `requirements_from_connectors`), not
    // from a CLI helper-name allowlist. Reuse connector paths from compile so
    // we do not re-walk/re-parse `connectors/` for names.
    let EmittedRequirements {
        connectors: connector_requirements,
        mcp_connections,
        channels: channel_requirements,
    } = emit_requirements_step(project, &out_dir, &summary)?;
    ensure_workspace_connections(
        client,
        workspace_id.as_deref(),
        &connector_requirements,
        &channel_requirements,
        &mcp_connections,
    )?;
    warn_unprovided_reserved_connections(&connector_requirements, &channel_requirements)?;
    let channel_setup = ChannelSetup::prepare(
        &channel_requirements,
        opts.wait,
        &project.root,
        &project.files,
        project.language,
        &assistant_id,
    )?;
    channel_setup.preflight(
        client,
        || find_target_deployment(client, &name, deployment_agent.as_ref()),
        &env_vars,
    )?;

    ensure_connector_requirements_secrets(
        project,
        &env_vars,
        &secrets,
        client,
        dashboard_url.as_deref(),
        &connector_requirements,
        &mut workspace_keys,
    )?;
    ensure_channel_requirements_secrets(
        project,
        &env_vars,
        &secrets,
        client,
        dashboard_url.as_deref(),
        &channel_requirements,
        &mut workspace_keys,
    )?;

    let external_image = match image_source {
        image::ImageSource::InternalSource => None,
        image::ImageSource::CustomerRegistry(target) => {
            let sp = spinner();
            sp.start("Building and pushing deployment image");
            let image = step(
                &sp,
                "Could not build and push the deployment image",
                image::build_and_push(project.language, &out_dir, &target),
            )?;
            sp.stop("Built and pushed deployment image");
            Some(image)
        }
    };

    // -- 5. Resolve / create deployment ------------------------------------
    interrupt.check()?;
    let (deployment_id, created_external) = if let Some(image_uri) = external_image.as_deref() {
        resolve_or_create_external_deployment(
            client,
            &name,
            deployment_agent.as_ref(),
            image_uri,
            &secrets,
        )?
    } else {
        let deployment_id = match &deployment_agent {
            Some(agent) => {
                resolve_or_create_agent_deployment(client, agent, opts.deployment_type, &secrets)?
            }
            None => resolve_or_create_deployment(client, &name, opts.deployment_type, &secrets)?,
        };
        (deployment_id, None)
    };
    link_context_repo_to_agent(client, &context_client, &deployment_id, &name)?;
    // Slack OAuth redirect_uri needs the public Agent Server URL. Inject it
    // when the deployment already has a runtime URL (revises / existing deploys).
    let deployment = client.get_deployment(&deployment_id).ok();
    update_facts(|facts| {
        facts.deployment = DeploymentInfo {
            deployment_name: deployment
                .as_ref()
                .and_then(|deployment| deployment.get("name")?.as_str().map(str::to_string))
                .or_else(|| Some(name.clone())),
            deployment_id: Some(deployment_id.clone()),
        };
    });
    report_non_interactive_deployment_id(&deployment_id);
    let runtime_url = deployment.as_ref().and_then(deployment_runtime_url);
    if let Some(runtime_url) = &runtime_url {
        ensure_public_api_url(&mut secrets, runtime_url);
    }

    // The deployment exists now, so the dashboard can already show the build:
    // hand over the link here rather than making the developer wait out the
    // build for it. An unscoped credential resolved its workspace server-side,
    // and this object is the first thing to name it.
    let tenant_id = deployment.as_ref().and_then(deployment_tenant_id);
    if target.workspace.is_none() {
        if let Some(tenant_id) = tenant_id {
            log::info(format!("Workspace: {}", target.label_for(tenant_id)))?;
        }
    }
    let mut status_url = dashboard_url.as_deref().and_then(|dashboard_url| {
        deployment
            .as_ref()
            .and_then(|deployment| deployment_status_url(deployment, &deployment_id, dashboard_url))
    });
    if let Some(url) = &status_url {
        note_links("Deployment dashboard", &[("", url)])?;
    }

    // -- 6. Supply source or image and trigger the revision ----------------
    interrupt.check()?;
    let updated = match (external_image.as_deref(), created_external) {
        (Some(_), Some(created)) => created,
        (Some(image_uri), None) => {
            let sp = spinner();
            sp.start("Updating deployment image");
            let updated = step(
                &sp,
                "Could not update the deployment",
                client.update_deployment_external_image(&deployment_id, image_uri, &secrets),
            )?;
            sp.stop("Deployment image updated");
            updated
        }
        (None, _) => archive_upload_and_trigger(client, &out_dir, &deployment_id, &secrets)?,
    };
    // The build response names the tenant too. When the read above failed, this
    // is where the link first becomes available, so print it here: the reports
    // below assume a knowable link has already been handed over.
    if status_url.is_none() {
        status_url = dashboard_url.as_deref().and_then(|dashboard_url| {
            deployment_status_url(&updated, &deployment_id, dashboard_url)
        });
        if let Some(url) = &status_url {
            note_links("Deployment dashboard", &[("", url)])?;
        }
    }

    // -- 7. Wait + report --------------------------------------------------
    interrupt.check()?;
    telemetry::stage::enter(Stage::WaitReady);
    if !opts.wait {
        if !summary.schedule_crons.is_empty() {
            log::warning(
                "Schedules were discovered, but --no-wait skips cron reconciliation until deploy completes.",
            )?;
        }
        outro(format!(
            "Build triggered for '{name}'. Not waiting (--no-wait)."
        ))?;
        return Ok(CommandCompletion::Succeeded);
    }

    let agent_server_url = poll_and_report(
        client,
        &deployment_id,
        &name,
        status_url.as_deref(),
        &summary.schedule_crons,
        opts.wait_timeout,
        interrupt,
    )?;
    let agent_server_url =
        require_trigger_registration_url(agent_server_url, channel_setup.needs_live_url())?;
    let Some(agent_server_url) = agent_server_url else {
        return Ok(CommandCompletion::Succeeded);
    };
    let http_links = http_channel_links(&agent_server_url, &channel_requirements);
    let http_link_refs: Vec<_> = http_links
        .iter()
        .map(|(name, url)| (*name, url.as_str()))
        .collect();
    note_links_with_section(
        "Deployment",
        &deployment_links(&agent_server_url, status_url.as_deref()),
        "HTTP channels",
        &http_link_refs,
    )?;
    channel_setup.set_up(&SetupTarget {
        client,
        agent_server_url: &agent_server_url,
        deployment_id: &deployment_id,
        assistant_id: &assistant_id,
        project_root: &project.root,
        env_vars: &env_vars,
    })?;
    match bake::delete_stale_recipe_snapshots(client, &name, active_recipe_name.as_deref()) {
        Ok(cleanup) => {
            if let Some(note) = cleanup.in_use_note() {
                log::info(note)?;
            }
        }
        Err(err) => log::warning(format!(
            "Deployment is live, but stale sandbox recipe snapshots could not be cleaned up: {err}"
        ))?,
    }
    outro(format!("Deployed '{name}' to LangSmith."))?;
    Ok(CommandCompletion::Succeeded)
}

fn require_trigger_registration_url(
    agent_server_url: Option<String>,
    has_trigger_channel: bool,
) -> Result<Option<String>> {
    if has_trigger_channel && agent_server_url.is_none() {
        bail!(
            "Deployment started, but channel setup could not complete because no revision was available to track; retry `mda deploy` once the deployment is live."
        );
    }
    Ok(agent_server_url)
}

fn secret_is_available(
    key: &str,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> bool {
    claim_secret_from_local_sources(key, env_vars, secrets)
        || matches!(
            verify_workspace_secret(key, client, workspace_keys),
            WorkspaceSecretStatus::Present
        )
}

/// Run a fallible step under an active spinner, switching the spinner to an
/// error state (so the TUI line is closed cleanly) when the step fails, or to
/// a cancelled one when the user stopped it.
pub(crate) fn step<T>(sp: &ProgressBar, fail_message: &str, result: Result<T>) -> Result<T> {
    match &result {
        Err(err) if is_cancellation(err) => sp.cancel("Cancelled"),
        Err(_) => sp.error(fail_message),
        Ok(_) => {}
    }
    result
}

/// Spinner title of the sandbox bake step; phases render beneath it.
pub(crate) const BAKE_SPINNER_TITLE: &str = "Baking sandbox recipe snapshot";

/// Bake spinner text: the stable title plus a dim sub-line for the running
/// phase, aligned with the TUI's vertical bar.
fn bake_spinner_message(phase: bake::BakePhase) -> String {
    format!(
        "{BAKE_SPINNER_TITLE}\n{}  {}",
        console::style("│").dim().for_stderr(),
        console::style(phase.label()).dim().for_stderr()
    )
}

/// Progress callback for [`bake_recipe`] that rewrites the spinner sub-line in
/// place. Without a terminal the spinner draws nothing, so each phase is logged
/// once instead.
#[expect(clippy::print_stderr, reason = "non-interactive progress stream")]
pub(crate) fn bake_progress(sp: &ProgressBar) -> impl Fn(bake::BakePhase) + '_ {
    let interactive = std::io::stderr().is_terminal();
    move |phase| {
        sp.set_message(bake_spinner_message(phase));
        if !interactive {
            eprintln!("Sandbox bake: {}", phase.label());
        }
    }
}

#[derive(Debug, Default, PartialEq, Eq)]
struct FirstDeployCollisions {
    context_hub_repo: bool,
    langsmith_deployment: bool,
}

fn first_deploy_collisions(
    existing_deployment: Option<&serde_json::Value>,
    context_hub_repo_is_foreign: bool,
) -> FirstDeployCollisions {
    // A redeploy only ever overwrites resources this deployment already owns,
    // so there is nothing to confirm.
    if existing_deployment.is_some_and(deployment_is_managed_deep_agent) {
        return FirstDeployCollisions::default();
    }
    FirstDeployCollisions {
        context_hub_repo: context_hub_repo_is_foreign,
        langsmith_deployment: existing_deployment.is_some(),
    }
}

/// What to do about same-name resources a first deploy would overwrite.
#[derive(Debug, PartialEq, Eq)]
enum CollisionDecision {
    Proceed,
    Prompt,
}

/// `--yes` answers the overwrite question up front; without it, only a terminal
/// can, so an unattended run fails with the flag that would have answered it.
fn collision_decision(
    name: &str,
    collisions: &FirstDeployCollisions,
    yes: bool,
    is_interactive: bool,
) -> Result<CollisionDecision> {
    if *collisions == FirstDeployCollisions::default() || yes {
        return Ok(CollisionDecision::Proceed);
    }
    if is_interactive {
        return Ok(CollisionDecision::Prompt);
    }
    Err(categorized(
        ErrorCategory::UserInput,
        format!(
            "Resources for deployment '{name}' already exist and require confirmation \
             before they can be overwritten. Re-run with --yes to overwrite them, or pass \
             --name to deploy under a different name."
        ),
    ))
}

/// In agent mode the deployment name is the agent ID. Returns the name of the
/// deploy-owned resources (see [`DeploymentAgent::resource_name`]) and the agent.
pub(crate) fn agent_target(
    name: String,
    environment: Option<AgentEnvironment>,
) -> Result<(String, Option<DeploymentAgent>)> {
    let Some(environment) = environment else {
        return Ok((name, None));
    };
    validate_agent_id(&name)?;
    let agent = DeploymentAgent {
        agent_id: name,
        environment,
    };
    Ok((agent.resource_name()?, Some(agent)))
}

/// The deployment this command targets: the agent's deployment in agent mode
/// (agent deployments are server-named), otherwise the exact-name match.
pub(crate) fn find_target_deployment(
    client: &langsmith::Client,
    name: &str,
    agent: Option<&DeploymentAgent>,
) -> Result<Option<serde_json::Value>> {
    Ok(match agent {
        Some(agent) => find_agent_deployment(&client.list_agent_deployments(agent)?).cloned(),
        None => find_deployment_by_name(&client.list_deployments(name)?, name).cloned(),
    })
}

/// Prefer a deployment object that actually carries the Managed Deep Agent
/// marker.
///
/// List responses are trimmed, so a marker missing from the listing means
/// "unknown", not "not ours" — and reading unknown as not-ours would make every
/// redeploy look like a collision. Re-read the full object once before letting
/// the caller conclude anything. A failed or still-unmarked re-read keeps the
/// listed object, which errs toward asking rather than silently overwriting.
fn with_managed_marker(
    client: &langsmith::Client,
    deployment: serde_json::Value,
) -> serde_json::Value {
    if deployment_managed_deep_agent_marker(&deployment).is_some() {
        return deployment;
    }
    let Some(id) = deployment.get("id").and_then(serde_json::Value::as_str) else {
        return deployment;
    };
    match client.get_deployment(id) {
        Ok(full) if deployment_managed_deep_agent_marker(&full).is_some() => full,
        _ => deployment,
    }
}

fn confirm_first_deploy_collisions(
    client: &langsmith::Client,
    context_client: &ContextHubClient,
    name: &str,
    agent: Option<&DeploymentAgent>,
    image_source: &image::ImageSource,
    yes: bool,
) -> Result<bool> {
    let sp = spinner();
    sp.start(format!("Checking for existing resources named '{name}'"));
    let existing_deployment = step(
        &sp,
        "Could not check existing resources",
        find_target_deployment(client, name, agent),
    )?
    .map(|deployment| with_managed_marker(client, deployment));
    if let Some(id) = existing_deployment
        .as_ref()
        .and_then(|deployment| deployment.get("id")?.as_str())
    {
        update_facts(|facts| facts.deployment.deployment_id = Some(id.to_string()));
    }
    // Source types are immutable. Reject an incompatible image deploy before
    // inspecting or syncing Context Hub so a deploy that cannot succeed leaves
    // every remote resource unchanged.
    if let Some(existing) = existing_deployment.as_ref() {
        if let Err(err) = ensure_requested_deployment_source(name, existing, image_source) {
            sp.error("Existing deployment uses a different source type");
            return Err(err);
        }
    }
    // Short-circuit the redeploy path before paying for the Context Hub lookup;
    // `first_deploy_collisions` re-checks the same marker as the invariant.
    if existing_deployment
        .as_ref()
        .is_some_and(deployment_is_managed_deep_agent)
    {
        sp.stop("Existing Managed Deep Agent deployment found");
        return Ok(true);
    }
    // A repo `mda deploy` created is not a collision even on a first deploy —
    // most often it is the leftover of an earlier deploy that failed after the
    // Context Hub sync but before the deployment was created, and blocking that
    // retry would wedge the very workflow this gate exists to protect.
    let context_hub_repo = step(
        &sp,
        "Could not check existing resources",
        context_client.repo_ownership(name),
    )?;
    sp.stop("Checked for existing resources");

    let collisions = first_deploy_collisions(
        existing_deployment.as_ref(),
        context_hub_repo == RepoOwnership::Foreign,
    );
    if collisions == FirstDeployCollisions::default() {
        return Ok(true);
    }
    if collisions.context_hub_repo {
        log::warning(format!(
            "Context Hub repo '{name}' already exists and was not created by `mda deploy`. Deploying overwrites its instructions and deletes everything under skills/ that this project does not define (memories are preserved)."
        ))?;
    }
    if collisions.langsmith_deployment {
        let deployment_name = existing_deployment
            .as_ref()
            .and_then(|deployment| deployment.get("name")?.as_str())
            .unwrap_or(name);
        log::warning(format!(
            "First Managed Deep Agent deploy will overwrite the source and configuration of existing LangSmith deployment '{deployment_name}'."
        ))?;
    }
    match collision_decision(name, &collisions, yes, std::io::stdin().is_terminal())? {
        CollisionDecision::Proceed => return Ok(true),
        CollisionDecision::Prompt => {}
    }
    Ok(confirm(format!(
        "Overwrite existing resources for '{name}' and continue?"
    ))
    .initial_value(false)
    .interact()?)
}

fn sync_context_hub(
    context_client: &ContextHubClient,
    project: &project::Project,
    name: &str,
    memory_enabled: bool,
    strategy: Option<ContextStrategy>,
) -> Result<context_hub::SyncSummary> {
    let sp = spinner();
    sp.start(format!("Checking Context Hub context '{name}'"));
    let plan = step(
        &sp,
        "Could not inspect Context Hub context",
        prepare_context_sync(context_client, project, name, memory_enabled),
    )?;
    sp.stop("Checked Context Hub context");

    let conflicts = plan.conflicts();
    let resolution = resolve_context_conflicts(conflicts, strategy)?;
    let preserved = resolution == ConflictResolution::KeepHub && !conflicts.is_empty();

    let sp = spinner();
    sp.start(format!("Syncing Context Hub context '{name}'"));
    let context = step(
        &sp,
        "Could not sync Context Hub context",
        apply_context_sync(context_client, plan, resolution),
    )?;
    if context.changed {
        sp.stop(format!("Synced Context Hub context ({name})"));
    } else {
        sp.stop("Context Hub context already current");
    }
    if preserved {
        log::warning(
            "Preserved Context Hub edits; local context was not applied to those sections.",
        )?;
    }
    if context.had_local_memories {
        log::warning(
            "Local memories/ is not deploy-owned. Agent memory lives in Context Hub and will not be overwritten by deploy.",
        )?;
    }
    // Memory is opt-in, so a project that stops declaring it keeps whatever the
    // Hub already holds — untouched, but unmounted. Say so rather than letting
    // durable knowledge go quiet. Paths only; contents are never read here.
    if context.orphaned_hub_memory_count > 0 {
        let count = context.orphaned_hub_memory_count;
        let extension = project.language.extension();
        let declare = compile::declare_memory_snippet(project.language);
        log::warning(format!(
            "This project declares no memory, so {count} existing Context Hub \
             memory file(s) will not be mounted (they are preserved, not deleted). \
             Add memory.{extension} with `{declare}` to mount them again.",
        ))?;
    }
    Ok(context)
}

fn resolve_context_conflicts(
    conflicts: &[ContextSection],
    strategy: Option<ContextStrategy>,
) -> Result<ConflictResolution> {
    if conflicts.is_empty() {
        return Ok(ConflictResolution::Overwrite);
    }
    if let Some(strategy) = strategy {
        return Ok(match strategy {
            ContextStrategy::Overwrite => ConflictResolution::Overwrite,
            ContextStrategy::KeepHub => ConflictResolution::KeepHub,
        });
    }

    let changed = conflicts
        .iter()
        .map(|section| section.label())
        .collect::<Vec<_>>()
        .join(" and ");
    if !std::io::stdin().is_terminal() {
        bail!(
            "Context Hub {changed} changed since the last MDA sync. Re-run with \
             --context-strategy overwrite or --context-strategy keep-hub."
        );
    }

    log::warning(format!(
        "Context Hub {changed} changed since the last MDA sync."
    ))?;
    let overwrite = confirm(format!(
        "Overwrite Hub-edited {changed} with the local version?"
    ))
    .initial_value(false)
    .interact()?;
    Ok(if overwrite {
        ConflictResolution::Overwrite
    } else {
        ConflictResolution::KeepHub
    })
}

fn compile_for_deploy_step(
    project: &project::Project,
    out_dir: &Path,
    context: context_hub::SyncSummary,
    installation: &Installation,
) -> Result<compile::CompileSummary> {
    let deploy_context = compile::DeployContext {
        repo: context.repo,
        has_skills: context.has_skills,
    };
    let sp = spinner();
    sp.start("Compiling project");
    let summary = step(
        &sp,
        "Could not compile the project",
        compile::compile_for_deploy(
            project,
            out_dir,
            &deploy_context,
            installation.runtime_source(),
        )
        .map_err(compile::compilation),
    )?;
    sp.stop(format!(
        "Compiled {} file(s) into .mda/build",
        summary.copied.len()
    ));
    // After `stop`: writing during the spinner would race its steady tick.
    for warning in &summary.warnings {
        log::warning(warning)?;
    }
    Ok(summary)
}

/// Requirements emitted from the compiled connector and channel modules.
pub(crate) struct EmittedRequirements {
    pub connectors: compile::ConnectorRequirements,
    pub mcp_connections: Vec<compile::McpConnectionEntry>,
    pub channels: compile::ChannelRequirements,
}

/// Emit connector, MCP, and channel requirements and report progress.
///
/// Each emit imports the compiled modules in a Node or Python subprocess,
/// after installing the emit deps when they are missing, so this step takes
/// a noticeable moment with no other output. Without connectors or channels
/// the emits only clear stale manifests, so no spinner is shown.
pub(crate) fn emit_requirements_step(
    project: &project::Project,
    out_dir: &Path,
    summary: &compile::CompileSummary,
) -> Result<EmittedRequirements> {
    let emit = || -> Result<EmittedRequirements> {
        Ok(EmittedRequirements {
            connectors: compile::emit_connector_requirements_for_entries(
                project,
                out_dir,
                &summary.connectors,
            )?,
            mcp_connections: compile::emit_mcp_connection_entries_for_entries(
                project,
                out_dir,
                &summary.connectors,
            )?,
            channels: compile::emit_channel_requirements_for_entries(
                project,
                out_dir,
                &summary.channels,
            )?,
        })
    };
    let mut modules = Vec::new();
    if !summary.connectors.is_empty() {
        modules.push(format!("{} connector(s)", summary.connectors.len()));
    }
    if !summary.channels.is_empty() {
        modules.push(format!("{} channel(s)", summary.channels.len()));
    }
    if modules.is_empty() {
        return emit();
    }
    let sp = spinner();
    sp.start("Resolving connector and channel requirements");
    let emitted = step(
        &sp,
        "Could not resolve connector and channel requirements",
        emit(),
    )?;
    sp.stop(format!(
        "Resolved requirements for {}",
        modules.join(" and ")
    ));
    Ok(emitted)
}

/// Bake (or reuse) the sandbox recipe snapshot and report progress.
fn bake_sandbox_recipe_step(
    client: &langsmith::Client,
    deployment: &str,
    env_vars: &std::collections::BTreeMap<String, String>,
    spec: &RecipeSpec,
) -> Result<(bake::BakeOutcome, Option<String>)> {
    let registry_password_env = spec
        .registry
        .as_ref()
        .map(|registry| registry.password_env.clone());
    let bake_env = bake_env_vars(env_vars);
    let sp = spinner();
    sp.start(BAKE_SPINNER_TITLE);
    let outcome = step(
        &sp,
        "Could not bake the sandbox recipe",
        bake_recipe(client, deployment, spec, &bake_env, &bake_progress(&sp)),
    )?;
    if outcome.skipped {
        sp.stop(format!(
            "Reusing ready recipe snapshot '{}'",
            outcome.recipe_name
        ));
    } else {
        sp.stop(format!("Baked recipe snapshot '{}'", outcome.recipe_name));
    }
    Ok((outcome, registry_password_env))
}

/// Build a [`RecipeSpec`] from the project setup script and compile manifest.
pub(crate) fn recipe_spec_from_project(
    project: &project::Project,
    out_dir: &Path,
) -> Result<Option<RecipeSpec>> {
    let Some(manifest) = compile::load_sandbox_recipe_manifest(out_dir)? else {
        return Ok(None);
    };
    let setup = compile::read_setup_script(project)?.unwrap_or_default();
    Ok(Some(RecipeSpec {
        setup_script: setup,
        snapshot_name: manifest.snapshot_name,
        snapshot_id: manifest.snapshot_id,
        docker_image: manifest.docker_image,
        registry: manifest.registry,
        fs_capacity_bytes: manifest.fs_capacity_bytes,
    }))
}

/// Non-reserved env vars forwarded onto the bake builder box.
fn bake_env_vars(
    env_vars: &std::collections::BTreeMap<String, String>,
) -> std::collections::BTreeMap<String, String> {
    env_vars
        .iter()
        .filter(|(name, value)| !value.is_empty() && !RESERVED_ENV_VARS.contains(&name.as_str()))
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect()
}

/// Look up a LangSmith deployment by name, or create one when missing.
fn resolve_or_create_deployment(
    client: &langsmith::Client,
    name: &str,
    deployment_type: DeploymentType,
    secrets: &[langsmith::Secret],
) -> Result<String> {
    let sp = spinner();
    sp.start(format!("Looking up deployment '{name}'"));
    let listed = step(
        &sp,
        "Could not reach LangSmith",
        client.list_deployments(name),
    )?;
    match find_deployment_id_by_name(&listed, name) {
        Some(id) => {
            sp.stop(format!("Found existing deployment {id}"));
            Ok(id)
        }
        None => {
            sp.stop("No existing deployment — creating a new one");
            let sp = spinner();
            sp.start(format!("Creating deployment '{name}'"));
            match client.create_deployment(name, deployment_type, secrets) {
                Ok(created) => {
                    let id = step(
                        &sp,
                        "Could not create the deployment",
                        require_str(&created, "id", "create deployment"),
                    )?;
                    sp.stop(format!("Created deployment {id}"));
                    Ok(id)
                }
                Err(err) if is_request_timeout(&err) => {
                    // The host backend may still finish provisioning even when
                    // the client times out waiting for the response.
                    sp.stop("Create request timed out — checking whether the deployment exists");
                    let sp = spinner();
                    sp.start(format!("Looking up deployment '{name}'"));
                    let listed = step(
                        &sp,
                        "Could not reach LangSmith",
                        client.list_deployments(name),
                    )?;
                    if let Some(id) = find_deployment_id_by_name(&listed, name) {
                        sp.stop(format!("Found deployment {id} (created despite timeout)"));
                        Ok(id)
                    } else {
                        sp.error("Could not create the deployment");
                        Err(err)
                    }
                }
                Err(err) => {
                    sp.error("Could not create the deployment");
                    Err(err)
                }
            }
        }
    }
}

fn resolve_or_create_agent_deployment(
    client: &langsmith::Client,
    agent: &DeploymentAgent,
    deployment_type: DeploymentType,
    secrets: &[langsmith::Secret],
) -> Result<String> {
    let listed = client.list_agent_deployments(agent)?;
    let deployment = match find_agent_deployment(&listed) {
        Some(deployment) => deployment.clone(),
        None => client.create_agent_deployment(agent, deployment_type, secrets)?,
    };
    let name = require_str(&deployment, "name", "agent deployment")?;
    log::info(format!("Deployment: {name}"))?;
    require_str(&deployment, "id", "agent deployment")
}

fn resolve_or_create_external_deployment(
    client: &langsmith::Client,
    name: &str,
    agent: Option<&DeploymentAgent>,
    image_uri: &str,
    secrets: &[langsmith::Secret],
) -> Result<(String, Option<serde_json::Value>)> {
    let sp = spinner();
    sp.start("Checking for an existing deployment");
    let existing = match agent {
        Some(agent) => {
            let listed = step(
                &sp,
                "Could not check existing deployments",
                client.list_agent_deployments(agent),
            )?;
            find_agent_deployment(&listed).cloned()
        }
        None => {
            let listed = step(
                &sp,
                "Could not check existing deployments",
                client.list_deployments(name),
            )?;
            find_deployment_by_name(&listed, name).cloned()
        }
    };

    if let Some(existing) = existing {
        let id = step(
            &sp,
            "Existing deployment has no id",
            require_str(&existing, "id", "existing deployment"),
        )?;
        if let Err(err) = ensure_external_deployment_source(name, &existing) {
            sp.error("Existing deployment uses a different source type");
            return Err(err);
        }
        sp.stop(format!("Found deployment {id}"));
        return Ok((id, None));
    }

    sp.stop("No existing deployment — creating a new one");
    let sp = spinner();
    sp.start(format!("Creating deployment '{name}'"));
    let created = step(
        &sp,
        "Could not create the deployment",
        match agent {
            Some(agent) => client.create_external_agent_deployment(agent, image_uri, secrets),
            None => client.create_external_deployment(name, image_uri, secrets),
        },
    )?;
    let id = step(
        &sp,
        "Could not create the deployment",
        require_str(&created, "id", "create deployment"),
    )?;
    sp.stop(format!("Created deployment {id}"));
    Ok((id, Some(created)))
}

fn ensure_external_deployment_source(name: &str, deployment: &serde_json::Value) -> Result<()> {
    let source = deployment.get("source").and_then(serde_json::Value::as_str);
    if source != Some("external_docker") {
        bail!(
            "deployment '{name}' uses source '{}'; deployment source types cannot be changed, so delete it or use a different --name",
            source.unwrap_or("unknown")
        );
    }
    Ok(())
}

fn ensure_requested_deployment_source(
    name: &str,
    deployment: &serde_json::Value,
    image_source: &image::ImageSource,
) -> Result<()> {
    if matches!(image_source, image::ImageSource::CustomerRegistry(_)) {
        ensure_external_deployment_source(name, deployment)?;
    }
    Ok(())
}

fn archive_upload_and_trigger(
    client: &langsmith::Client,
    out_dir: &Path,
    deployment_id: &str,
    secrets: &[langsmith::Secret],
) -> Result<serde_json::Value> {
    let sp = spinner();
    sp.start("Creating source archive");
    let archive = step(
        &sp,
        "Could not archive the build",
        archive::create_archive(out_dir),
    )?;
    sp.stop(format!(
        "Archived build ({:.1} KB)",
        archive.bytes.len() as f64 / 1024.0
    ));

    let sp = spinner();
    sp.start("Requesting upload URL");
    let upload = step(
        &sp,
        "Could not request an upload URL",
        client.request_upload_url(deployment_id),
    )?;
    let signed_url = step(
        &sp,
        "Could not request an upload URL",
        require_str(&upload, "upload_url", "upload URL"),
    )?;
    let object_path = step(
        &sp,
        "Could not request an upload URL",
        require_str(&upload, "object_path", "upload URL"),
    )?;
    sp.stop("Received upload URL");

    let sp = spinner();
    sp.start("Uploading source");
    step(
        &sp,
        "Could not upload the source",
        upload_to_gcs(&signed_url, archive.bytes),
    )?;
    sp.stop("Uploaded source");

    let sp = spinner();
    sp.start("Triggering remote build");
    let updated = step(
        &sp,
        "Could not trigger the remote build",
        client.update_deployment_internal_source(
            deployment_id,
            &object_path,
            &archive.config_rel,
            secrets,
        ),
    )?;
    sp.stop("Remote build triggered");
    Ok(updated)
}

/// Confirm the directory looks like a Managed Deep Agent project before doing
/// any work, with a friendly message when it does not.
pub(crate) fn ensure_project(path: &Path) -> Result<()> {
    project::ensure_project_dir(path)
}

/// A durable key this process put into its own environment (see
/// [`export_langsmith_api_key`]). `mda dev` re-resolves on every watch rebuild,
/// so without this a key first read from `.env` would later pose as a shell
/// export and shadow an edit to `.env`.
static SELF_EXPORTED_API_KEY: Mutex<Option<String>> = Mutex::new(None);

/// A durable `LANGSMITH_API_KEY` the developer exported in the shell, as
/// opposed to a CLI session PAT or a key this process exported itself.
fn langsmith_api_key_from_shell(platform_api_url: &str) -> Option<String> {
    let key = lookup_env_or_process(API_KEY_ENV, &BTreeMap::new())?;
    let self_exported = SELF_EXPORTED_API_KEY
        .lock()
        .is_ok_and(|exported| exported.as_deref() == Some(key.as_str()));
    (!self_exported && !is_cli_session_api_key(&key, platform_api_url)).then_some(key)
}

/// Export `key` into this process's env so later steps and `mda dev` children
/// see it, remembering it so it is not later mistaken for a shell export.
fn export_langsmith_api_key(key: &str) {
    if lookup_env_or_process(API_KEY_ENV, &BTreeMap::new()).as_deref() == Some(key) {
        return;
    }
    if let Ok(mut exported) = SELF_EXPORTED_API_KEY.lock() {
        *exported = Some(key.to_string());
    }
    std::env::set_var(API_KEY_ENV, key);
}

/// Whether a staged build `.env` entry holds a CLI session credential rather
/// than a project-owned value.
///
/// Session PATs are deliberately kept out of the project `.env`, so they exist
/// only in the staged copy. Restaging must treat them as CLI-owned the same way
/// it does `MDA_*`: a sync that cannot find the key in the project file would
/// delete it, and the resolver would write it straight back — two mtime bumps
/// on the file LangGraph watches, so an extra server restart per rebuild.
pub(crate) fn is_cli_session_env_entry(project_root: &Path, key: &str, value: &str) -> bool {
    if key != API_KEY_ENV && key != GATEWAY_API_KEY_ENV {
        return false;
    }
    let value = value.trim();
    if value.is_empty() {
        return false;
    }
    let env_vars = env_file::parse_env_file(&env_file::project_env_persist_path(project_root));
    let platform = LangSmithEndpoints::resolve(&env_vars, None).platform_api_url;
    is_cli_session_api_key(value, &platform)
}

/// When `.env` still documents `LANGSMITH_GATEWAY_API_KEY` (legacy `--gateway`
/// scaffolds), fill a blank alias so constructed ChatOpenAI clients keep working.
/// New projects do not need this name; `LANGSMITH_API_KEY` stays reserved and never
/// reaches a deployment, so a blank legacy alias would otherwise 401 at runtime.
fn mirror_gateway_api_key(env_path: &Path, key: &str) -> Result<()> {
    let env = env_file::parse_env_file(env_path);
    if !env.contains_key(GATEWAY_API_KEY_ENV) {
        return Ok(());
    }
    if lookup_env_file(GATEWAY_API_KEY_ENV, &env).is_some() {
        return Ok(());
    }
    env_file::set_env_key(env_path, GATEWAY_API_KEY_ENV, key)
}

/// Clear a gateway alias that was filled with a leaked session PAT.
fn clear_gateway_session_key(env_path: &Path, session_key: &str) -> Result<()> {
    let env = env_file::parse_env_file(env_path);
    if env.get(GATEWAY_API_KEY_ENV).map(String::as_str) != Some(session_key) {
        return Ok(());
    }
    env_file::set_env_key(env_path, GATEWAY_API_KEY_ENV, "")
}

/// Apply a LangSmith API key to the staged build env and optionally the project.
///
/// Session keys from device login / the CLI PAT cache must not be written to
/// the project `.env` — only deliberately pasted (or shell-exported) keys are
/// durable there. Legacy projects that still declare a blank
/// `LANGSMITH_GATEWAY_API_KEY` get that alias filled when we touch the file.
///
/// Callers that apply a CLI session PAT must [`note_session_api_key`] so a later
/// process-env read does not treat it as a durable shell export.
fn apply_langsmith_api_key(
    key: &str,
    project_env: &Path,
    staged_env_dir: Option<&Path>,
    persist_to_project: bool,
) -> Result<()> {
    if persist_to_project {
        env_file::set_env_key(project_env, API_KEY_ENV, key)?;
        mirror_gateway_api_key(project_env, key)?;
    }
    if let Some(out_dir) = staged_env_dir {
        let staged = out_dir.join(".env");
        env_file::set_env_key(&staged, API_KEY_ENV, key)?;
        mirror_gateway_api_key(&staged, key)?;
    }
    export_langsmith_api_key(key);
    Ok(())
}

/// Ensure the project has a LangSmith API key for local sandbox work.
///
/// Resolution order matches [`resolve_api_key`]: a shell-exported key, then
/// staged / project `.env`, CLI sign-in cache, then (on a TTY when `required`)
/// paste or browser login.
///
/// Pasted keys are written to the project `.env`; a shell-exported key is too,
/// but only when the project has none, so it never overwrites the project's.
/// Device-login / cached PATs are session-scoped: process env and the staged
/// build `.env` only, so an expired PAT cannot stick in the project file.
pub(crate) fn ensure_project_langsmith_api_key(
    project_root: &Path,
    staged_env_dir: Option<&Path>,
    required: bool,
) -> Result<()> {
    let project_env = env_file::project_env_persist_path(project_root);
    let mut env_vars = env_file::parse_env_file(&project_env);
    let platform = LangSmithEndpoints::resolve(&env_vars, None).platform_api_url;

    // The shell wins over staged and project `.env` keys, like every other CLI
    // setting.
    if let Some(key) = langsmith_api_key_from_shell(&platform) {
        let project_vars = env_file::parse_env_file(&project_env);
        crate::langsmith::warn_if_shell_shadows_env_file(API_KEY_ENV, &key, &project_vars);
        let seed_project = lookup_env_file(API_KEY_ENV, &project_vars).is_none();
        apply_langsmith_api_key(&key, &project_env, staged_env_dir, seed_project)?;
        return Ok(());
    }

    if let Some(out_dir) = staged_env_dir {
        let staged_path = out_dir.join(".env");
        let staged_env = env_file::parse_env_file(&staged_path);
        if let Some(key) = lookup_env_file(API_KEY_ENV, &staged_env) {
            let is_session = is_cli_session_api_key(&key, &platform);
            let cache_live =
                Login::for_api_url(&platform).cached_api_key().as_deref() == Some(key.as_str());
            if is_session && !cache_live {
                // Stale session PAT left in staged env after expiry — refresh.
                env_vars.extend(staged_env);
            } else {
                // Session PATs may live in staged/.env for the run; never copy
                // them into the project gateway alias.
                if is_session {
                    note_session_api_key(&key);
                }
                mirror_gateway_api_key(&staged_path, &key)?;
                if !is_session
                    && lookup_env_file(API_KEY_ENV, &env_file::parse_env_file(&project_env))
                        .is_some()
                {
                    mirror_gateway_api_key(&project_env, &key)?;
                }
                return Ok(());
            }
        } else {
            env_vars.extend(staged_env);
        }
    }

    if let Some(key) = lookup_env_file(API_KEY_ENV, &env_file::parse_env_file(&project_env)) {
        if is_cli_session_api_key(&key, &platform) {
            // A prior restage wrote a session PAT into the project file. Strip it
            // (and a matching gateway alias) so cache/login can refresh.
            env_file::remove_env_key(&project_env, API_KEY_ENV)?;
            clear_gateway_session_key(&project_env, &key)?;
        } else {
            // Already durable in the project file — stage + fill gateway alias.
            apply_langsmith_api_key(&key, &project_env, staged_env_dir, false)?;
            mirror_gateway_api_key(&project_env, &key)?;
            return Ok(());
        }
    }

    // Durable shell keys returned above; what is left in the process env is a
    // session PAT or a key this process exported from a `.env` it no longer has.
    if let Some(key) = lookup_env_or_process(API_KEY_ENV, &BTreeMap::new()) {
        // Only reuse a process-exported session PAT while the cache still
        // considers it live. An expired PAT left in the environment must
        // not short-circuit re-login or get written to the project `.env`.
        if is_cli_session_api_key(&key, &platform)
            && Login::for_api_url(&platform).cached_api_key().as_deref() == Some(key.as_str())
        {
            note_session_api_key(&key);
            apply_langsmith_api_key(&key, &project_env, staged_env_dir, false)?;
            return Ok(());
        }
    }

    if let Some(key) = Login::for_api_url(&platform).cached_api_key() {
        note_session_api_key(&key);
        apply_langsmith_api_key(&key, &project_env, staged_env_dir, false)?;
        let where_loaded = staged_env_dir
            .map(|dir| format!("staged env under {}", dir.display()))
            .unwrap_or_else(|| "the process environment".to_string());
        log::info(format!(
            "Loaded {API_KEY_ENV} from the local sign-in cache into {where_loaded}"
        ))?;
        return Ok(());
    }

    if !required {
        return Ok(());
    }

    let api_key = resolve_api_key(&env_vars, &project_env, &platform)?;
    let key = api_key.as_str();
    // `resolve_api_key` already wrote pasted keys to the project `.env`. Device
    // login only caches a short-lived PAT — stage it, do not make it durable.
    let pasted_into_project =
        lookup_env_file(API_KEY_ENV, &env_file::parse_env_file(&project_env)).is_some();
    if pasted_into_project {
        mirror_gateway_api_key(&project_env, key)?;
        if let Some(out_dir) = staged_env_dir {
            let staged = out_dir.join(".env");
            env_file::set_env_key(&staged, API_KEY_ENV, key)?;
            mirror_gateway_api_key(&staged, key)?;
        }
    } else {
        note_session_api_key(key);
        apply_langsmith_api_key(key, &project_env, staged_env_dir, false)?;
    }
    Ok(())
}

/// Fails when nothing matches. `delete` calls [`find_deployment_id_by_name`]
/// directly instead, because a missing deployment is a normal teardown outcome
/// there rather than an error.
pub(crate) fn resolve_deployment_id(client: &langsmith::Client, name: &str) -> Result<String> {
    let listed = client
        .list_deployments(name)
        .context("failed to reach LangSmith")?;
    match find_deployment_id_by_name(&listed, name) {
        Some(id) => Ok(id),
        None => {
            let dashboard = client
                .dashboard_url()
                .map(|url| format!(" ({url})"))
                .unwrap_or_default();
            bail!(
                "No deployment named '{name}' exists in this workspace.\n\
                 Deploy it with `mda deploy` first, or pass `-n <name>` / `--workspace-id <id>` \
                 if it lives under a different name or workspace{dashboard}."
            )
        }
    }
}

/// Resolve LangSmith credentials for deploy / logs / delete / credential-backed `dev`.
///
/// Order:
/// 1. `LANGSMITH_API_KEY` from the project `.env` or process environment
///    (user-provided; treated as durable)
/// 2. An unexpired CLI-minted PAT from the machine state directory
/// 3. On a TTY: prompt to paste a key (saved to `.env`) or press Enter for
///    browser device login (mints a 2-hour PAT into the state directory)
///
/// `platform_api_url` is the LangSmith platform origin device login must use
/// (same installation as Fleet / workspace APIs). Resolve it with
/// [`resolve_platform_api_url`] before calling.
pub(crate) fn resolve_api_key(
    env_vars: &std::collections::BTreeMap<String, String>,
    env_path: &Path,
    platform_api_url: &str,
) -> Result<ApiKey> {
    if let Some(key) = resolve_preferred_env(
        API_KEY_ENV,
        REMOVED_API_KEY_ENVS,
        None,
        env_vars,
        |key| std::env::var(key).ok(),
        " instead (same value, renamed)",
    )? {
        return Ok(ApiKey::new(key));
    }

    let login = Login::for_api_url(platform_api_url);
    if let Some(key) = login.cached_api_key() {
        // So later steps and `mda dev` children inherit the session key.
        note_session_api_key(&key);
        std::env::set_var("LANGSMITH_API_KEY", &key);
        return Ok(ApiKey::new(key));
    }

    if !std::io::stdin().is_terminal() {
        return Err(categorized(
            ErrorCategory::Authentication,
            format!(
                "No LangSmith API key found. Set LANGSMITH_API_KEY in the environment \
                 or in {}, or run interactively to sign in with your browser",
                env_path.display()
            ),
        ));
    }

    log::warning(
        "No LangSmith API key found. Paste one from Settings → API Keys (saved to .env), \
         or press Enter to sign in with your browser (2-hour session on this machine).",
    )?;
    let key: String = password("LangSmith API key (or Enter to authorize)")
        .mask('•')
        .allow_empty()
        .interact()?;
    match prompt_api_key_choice(&key) {
        ApiKeyPromptChoice::Authorize => authorize_with_device_login(platform_api_url),
        ApiKeyPromptChoice::ApiKey(key) => persist_api_key(env_path, &key),
    }
}

/// A client for the key [`resolve_api_key`] would find without prompting:
/// the env key, else an unexpired cached sign-in. `None` when there is no key
/// or a setting is invalid. Makes no request.
pub(crate) fn client_without_prompt(
    env_vars: &std::collections::BTreeMap<String, String>,
    host_url: &str,
    platform_api_url: &str,
    workspace_id: Option<&str>,
) -> Option<langsmith::Client> {
    let key = env_api_key(env_vars)
        .ok()?
        .or_else(|| Login::for_api_url(platform_api_url).cached_api_key())?;
    let workspace_id = langsmith_context::resolve_workspace_id(env_vars, workspace_id, "").ok()?;
    langsmith_context::LangSmithEndpoints {
        host_url: host_url.to_string(),
        platform_api_url: platform_api_url.to_string(),
    }
    .with_auth(ApiKey::new(key), workspace_id)
    .client()
    .ok()
}

/// `LANGSMITH_API_KEY` from the env file, then the process env. Errors when
/// only a removed name for it is set.
fn env_api_key(env_vars: &std::collections::BTreeMap<String, String>) -> Result<Option<String>> {
    resolve_preferred_env(
        API_KEY_ENV,
        REMOVED_API_KEY_ENVS,
        None,
        env_vars,
        |key| std::env::var(key).ok(),
        " instead (same value, renamed)",
    )
}

/// Sign in with the device flow and mint a short-lived PAT into the state dir.
///
/// OAuth access tokens (~5 minutes) are not enough for Fleet or `mda dev`; the
/// CLI-minted `lsv2_pt_…` key (2 hours) is what those paths need.
fn authorize_with_device_login(platform_api_url: &str) -> Result<ApiKey> {
    let http = crate::http::client_builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .context("failed to build HTTP client for LangSmith API key creation")?;
    let login = Login::sign_in_at(platform_api_url)?;
    let key = login
        .mint_and_cache_api_key(&http)
        .context("signed in, but could not create a LangSmith API key")?;
    log::success("Signed in with a 2-hour LangSmith API key (cached on this machine)")?;
    log::info(
        "This session key expires in 2 hours and stays in the local sign-in cache \
         (not your project .env). `mda dev` loads it into the process for local runs; \
         for longer sessions, create an API key under Settings → API Keys with a longer \
         expiry and paste it when prompted. If this isn't your machine, revoke the key \
         under Settings → API Keys when you're done.",
    )?;
    Ok(ApiKey::new(key))
}

/// Write a user-pasted `LANGSMITH_API_KEY` to `.env` and the process environment.
fn persist_api_key(env_path: &Path, key: &str) -> Result<ApiKey> {
    env_file::set_env_key(env_path, "LANGSMITH_API_KEY", key)?;
    mirror_gateway_api_key(env_path, key)?;
    // So later resolve_api_key calls in this process see the key even when the
    // caller's in-memory env map was parsed before the write.
    export_langsmith_api_key(key);
    log::success(format!("Saved LANGSMITH_API_KEY to {}", env_path.display()))?;
    Ok(ApiKey::new(key))
}

/// How the interactive API-key prompt was answered.
#[derive(Debug, PartialEq, Eq)]
enum ApiKeyPromptChoice {
    /// Empty input — sign in with the device authorization flow.
    Authorize,
    /// A non-empty API key to persist and use.
    ApiKey(String),
}

/// Classify the interactive API-key prompt: blank means authorize in the browser.
fn prompt_api_key_choice(input: &str) -> ApiKeyPromptChoice {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        ApiKeyPromptChoice::Authorize
    } else {
        ApiKeyPromptChoice::ApiKey(trimmed.to_string())
    }
}

/// Ensure `MDA_INGRESS_SECRET` will be available when the project's identity
/// uses `trusted_backend` ingress, failing the deploy before any build work
/// otherwise.
///
/// A deployment without this secret accepts no authenticated traffic (auth
/// fails closed with 401), so shipping one is never useful. Unlike the
/// model-key check, a failed workspace lookup does not soft-pass — the secret
/// must be positively confirmed.
fn ensure_ingress_secret(
    project: &project::Project,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    dashboard_url: Option<&str>,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> Result<()> {
    // One identity parse (auth + scope share a walk); compile reuses the same helper.
    if !compile::identity_requires_ingress_secret(project)? {
        return Ok(());
    }

    ensure_required_secret(
        compile::INGRESS_SECRET_ENV,
        project,
        env_vars,
        secrets,
        client,
        dashboard_url,
        workspace_keys,
        "This project declares identity with trusted_backend ingress, which requires an ingress \
         secret (generate with `openssl rand -hex 32`; clients send it as `x-mda-ingress-secret`)",
    )
}

/// Ensure connector provider secrets from compiled requirements' `requiredEnv`.
fn ensure_connector_requirements_secrets(
    project: &project::Project,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    dashboard_url: Option<&str>,
    connector_requirements: &compile::ConnectorRequirements,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> Result<()> {
    if connector_requirements.is_empty() {
        return Ok(());
    }

    for key_env in compile::required_env_from_connector_requirements(connector_requirements) {
        ensure_required_secret(
            &key_env,
            project,
            env_vars,
            secrets,
            client,
            dashboard_url,
            workspace_keys,
            "This project declares connectors/ with requirements, which requires provider secrets from each connector's requirements",
        )?;
    }
    Ok(())
}

/// Create missing user-owned MCP connections through discovery + DCR, then
/// verify every remaining connector connection exists in the workspace.
pub(crate) fn ensure_workspace_connections(
    client: &langsmith::Client,
    workspace_id: Option<&str>,
    connector_requirements: &compile::ConnectorRequirements,
    channel_requirements: &compile::ChannelRequirements,
    mcp_connections: &[compile::McpConnectionEntry],
) -> Result<()> {
    let referenced = compile::referenced_connection_slugs(connector_requirements);
    if referenced.is_empty() {
        return Ok(());
    }
    // Workspace scope is carried on the client (tenant header). Require the ID
    // up front so deploy fails with a clear local error instead of a 401/403.
    workspace_id.ok_or_else(|| {
        anyhow::anyhow!(
            "connector connections are declared, but no workspace ID is configured. \
             Set {WORKSPACE_ID_ENV} or pass --workspace-id so deploy can verify connections."
        )
    })?;
    let mut existing: BTreeMap<String, ConnectionSummary> = client
        .list_workspace_connections()?
        .into_iter()
        .map(|connection| (connection.slug.clone(), connection))
        .collect();
    let provided = compile::channel_provided_connections(channel_requirements);
    for mcp in missing_user_owned_mcp_connections(
        connector_requirements,
        &provided,
        &existing,
        mcp_connections,
    )? {
        let created = connection::create_discovered_mcp_connection(client, &mcp.slug, &mcp.url)?;
        log::success(format!(
            "Created MCP connection '{}' through OAuth discovery",
            mcp.slug
        ))?;
        existing.insert(mcp.slug.clone(), created);
    }
    validate_workspace_connections_for_manifest(connector_requirements, &provided, &existing)
}

/// Warn about `mda/` agent connections the project reads that none of its
/// channels provides: deploy goes on, but reading one fails at runtime.
pub(crate) fn warn_unprovided_reserved_connections(
    connector_requirements: &compile::ConnectorRequirements,
    channel_requirements: &compile::ChannelRequirements,
) -> Result<()> {
    for slug in
        compile::unprovided_reserved_connections(connector_requirements, channel_requirements)
    {
        log::warning(format!(
            "'{slug}' is reserved for a channel's app, but no channel in this project provides \
             it, so reading it will fail. Add the channel that provides it, or use another \
             connection."
        ))?;
    }
    Ok(())
}

/// Select only missing, uniquely declared user-owned MCP connections.
///
/// Other missing connection kinds remain for normal validation, while an
/// ambiguous MCP slug fails before deploy creates any remote resources.
fn missing_user_owned_mcp_connections<'a>(
    requirements: &compile::ConnectorRequirements,
    provided: &BTreeMap<String, String>,
    existing: &BTreeMap<String, ConnectionSummary>,
    mcp_connections: &'a [compile::McpConnectionEntry],
) -> Result<Vec<&'a compile::McpConnectionEntry>> {
    validate_connection_ownership(requirements, provided)?;
    let mut missing = Vec::new();
    for slug in compile::user_connections_from_connector_requirements(requirements) {
        if existing.contains_key(&slug) {
            continue;
        }
        let matches: Vec<&compile::McpConnectionEntry> = mcp_connections
            .iter()
            .filter(|entry| {
                entry.slug == slug
                    && entry.owner_policy == langsmith::ConnectionOwnerPolicy::User.as_str()
            })
            .collect();
        match matches.as_slice() {
            [] => {}
            [entry] => missing.push(*entry),
            many => {
                bail!(
                    "connection '{slug}' is used by multiple user-owned MCP servers in this \
                     project: {}. Keep the slug unique per MCP server.",
                    many.iter()
                        .map(|entry| format!("{}::{}", entry.connector_name, entry.server_name))
                        .collect::<Vec<_>>()
                        .join(", ")
                );
            }
        }
    }
    Ok(missing)
}

/// Verify every connector connection slug declared in the manifest exists in the workspace.
///
/// Presence only. Ownership is not checked here: the connections API does not
/// report it on a listed row, and runtime ownership lives on credentials.
pub(crate) fn validate_workspace_connections_for_manifest(
    requirements: &compile::ConnectorRequirements,
    provided: &BTreeMap<String, String>,
    existing: &BTreeMap<String, ConnectionSummary>,
) -> Result<()> {
    validate_connection_ownership(requirements, provided)?;
    let agent_slugs = compile::agent_connections_from_connector_requirements(requirements);
    let user_slugs = compile::user_connections_from_connector_requirements(requirements);

    let mut missing: Vec<(&str, &str)> = Vec::new();
    for slug in &agent_slugs {
        if !existing.contains_key(slug.as_str()) {
            missing.push((slug.as_str(), "agent-owned"));
        }
    }
    for slug in &user_slugs {
        if !existing.contains_key(slug.as_str()) {
            missing.push((slug.as_str(), "user-owned"));
        }
    }
    if missing.is_empty() {
        return Ok(());
    }

    bail!(
        "connections declared by this project are not in the workspace\n\n{}\n\nCreate each \
         with `mda connections create`.",
        format_missing_connections(&missing)
    )
}

/// `provided` is what [`compile::channel_provided_connections`] returns.
fn validate_connection_ownership(
    requirements: &compile::ConnectorRequirements,
    provided: &BTreeMap<String, String>,
) -> Result<()> {
    let agent_slugs = compile::agent_connections_from_connector_requirements(requirements);
    let user_slugs = compile::user_connections_from_connector_requirements(requirements);
    if let Some(error) = user_slugs
        .iter()
        .find_map(|slug| compile::reserved_user_connection_error(slug, provided))
    {
        bail!(error);
    }

    if let Some(slug) = agent_slugs
        .iter()
        .find(|slug| user_slugs.iter().any(|other| other == *slug))
    {
        bail!(
            "connection '{slug}' is declared as both agent-owned and user-owned in this project. \
             Use one owner per slug in connectors."
        );
    }
    Ok(())
}

/// One indented `'slug'  owner` line per connection, slug column padded so the
/// owners line up.
fn format_missing_connections(missing: &[(&str, &str)]) -> String {
    let quoted: Vec<String> = missing
        .iter()
        .map(|(slug, _)| format!("'{slug}'"))
        .collect();
    let width = quoted.iter().map(String::len).max().unwrap_or(0);
    quoted
        .iter()
        .zip(missing)
        .map(|(slug, (_, owner))| format!("  {slug:<width$}  {owner}"))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Ensure channel provider secrets from compiled requirements' `requiredEnv`.
fn ensure_channel_requirements_secrets(
    project: &project::Project,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    dashboard_url: Option<&str>,
    channel_requirements: &compile::ChannelRequirements,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> Result<()> {
    let (required, optional_slack) = channel_secret_policy(channel_requirements);
    let mut slack_inactive = false;
    for key_env in optional_slack {
        if !secret_is_available(&key_env, env_vars, secrets, client, workspace_keys) {
            slack_inactive = true;
        }
    }

    for key_env in required {
        ensure_required_secret(
            &key_env,
            project,
            env_vars,
            secrets,
            client,
            dashboard_url,
            workspace_keys,
            "This project declares channels/ with requirements, which requires provider secrets from each channel's requirements",
        )?;
    }
    if slack_inactive {
        log::warning(
            "Slack is not configured yet. The agent will deploy with normal Slack events disabled.",
        )?;
    }
    Ok(())
}

fn channel_secret_policy(
    channel_requirements: &compile::ChannelRequirements,
) -> (Vec<String>, Vec<String>) {
    let mut required = std::collections::BTreeSet::new();
    let mut optional_slack = std::collections::BTreeSet::new();
    for manifest in channel_requirements.values() {
        let provider = manifest.get("provider").and_then(serde_json::Value::as_str);
        let Some(entries) = manifest
            .get("requiredEnv")
            .and_then(serde_json::Value::as_array)
        else {
            continue;
        };
        for entry in entries {
            let Some(key_env) = entry.as_str() else {
                continue;
            };
            if provider == Some("slack")
                && matches!(key_env, "SLACK_SIGNING_SECRET" | "SLACK_BOT_TOKEN")
            {
                optional_slack.insert(key_env.to_string());
            } else {
                required.insert(key_env.to_string());
            }
        }
    }
    (
        required.into_iter().collect(),
        optional_slack.into_iter().collect(),
    )
}

/// Require a named secret from `.env` or LangSmith workspace secrets.
#[expect(
    clippy::too_many_arguments,
    reason = "thin preflight helper shared by ingress and connector requirements secret checks"
)]
fn ensure_required_secret(
    key_env: &str,
    project: &project::Project,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    dashboard_url: Option<&str>,
    workspace_keys: &mut WorkspaceSecretKeys,
    context: &str,
) -> Result<()> {
    if claim_secret_from_local_sources(key_env, env_vars, secrets) {
        return Ok(());
    }

    let env = project.root.join(".env");
    let sp = spinner();
    sp.start(format!("Checking that {key_env} is configured"));

    match verify_workspace_secret(key_env, client, workspace_keys) {
        WorkspaceSecretStatus::Present => {
            sp.stop(format!("{key_env} found in LangSmith workspace secrets"));
            Ok(())
        }
        WorkspaceSecretStatus::Absent => {
            sp.error(format!("{key_env} is not configured"));
            let dashboard = dashboard_url
                .map(|url| format!(" ({url})"))
                .unwrap_or_default();
            bail!(
                "{context}. {key_env} is not set in {env} and not configured as a LangSmith \
                 workspace secret.\n\
                 Add `{key_env}=...` to {env}, or add it under Settings > Workspaces > Secrets \
                 in LangSmith{dashboard}, then deploy again. Values exported in your shell \
                 are not read.",
                env = env.display(),
            );
        }
        WorkspaceSecretStatus::LookupFailed(err) => {
            sp.error(format!("Could not verify {key_env} ({err:#})"));
            bail!(
                "{context}. {key_env} is not set in {env}, and LangSmith workspace secrets \
                 could not be checked ({err:#}).\n\
                 Add `{key_env}=...` to {env}, then deploy again. Values exported in your \
                 shell are not read.",
                env = env.display(),
            );
        }
    }
}

/// Resolve the provider secret required by deploy preflight.
///
/// Gateway models must either use an explicitly supported keyless identifier or
/// resolve to a known workspace Provider Secret.
fn model_api_key_requirement_for_deploy(
    model: &str,
) -> Result<dependencies::ModelApiKeyRequirement> {
    match dependencies::deploy_model_api_key_requirement(model) {
        dependencies::ModelApiKeyRequirement::UnresolvedLangsmithProvider => {
            bail!(
                "The agent model '{model}' uses LangSmith Gateway, but its provider is not \
                 supported. Use a supported `langsmith:<provider>/<model>` identifier, then \
                 configure that provider under Settings > Integrations > Provider Secrets in \
                 LangSmith before deploying."
            );
        }
        requirement => Ok(requirement),
    }
}

/// Ensure the agent's model provider API key will be available to the
/// deployment at runtime, failing the deploy before any build work otherwise.
///
/// Direct model keys may come from `.env`.
/// Gateway provider credentials must be positively confirmed in workspace
/// Provider Secrets because local deployment secrets never reach the Gateway.
fn ensure_model_api_key(
    model: Option<&str>,
    project: &project::Project,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
    client: &langsmith::Client,
    dashboard_url: Option<&str>,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> Result<()> {
    let Some(model) = model else {
        return Ok(());
    };
    let requirement = model_api_key_requirement_for_deploy(model)?;
    let key_env = match requirement {
        dependencies::ModelApiKeyRequirement::NoPreflight => return Ok(()),
        dependencies::ModelApiKeyRequirement::DeploymentSecret(key_env) => {
            if claim_secret_from_local_sources(key_env, env_vars, secrets) {
                return Ok(());
            }
            bail!(missing_model_api_key_message(
                model,
                key_env,
                &project.root.join(".env"),
                "then deploy again.",
            ));
        }
        dependencies::ModelApiKeyRequirement::WorkspaceProviderSecret(key_env) => key_env,
        dependencies::ModelApiKeyRequirement::UnresolvedLangsmithProvider => {
            unreachable!("unresolved Gateway providers fail above")
        }
    };

    let sp = spinner();
    sp.start(format!("Checking that {key_env} is configured"));

    match verify_workspace_secret(key_env, client, workspace_keys) {
        WorkspaceSecretStatus::Present => {
            sp.stop(format!(
                "{key_env} found in LangSmith workspace Provider Secrets"
            ));
            Ok(())
        }
        WorkspaceSecretStatus::Absent => {
            sp.error(format!("{key_env} is not configured"));
            let dashboard = dashboard_url
                .map(|url| format!(" ({url})"))
                .unwrap_or_default();
            bail!(
                "The agent model '{model}' uses LangSmith Gateway and requires {key_env} in \
                 LangSmith workspace Provider Secrets. Add `{key_env}` under Settings > \
                 Integrations > Provider Secrets in LangSmith{dashboard}, then deploy again."
            );
        }
        WorkspaceSecretStatus::LookupFailed(err) => {
            sp.error("Could not check LangSmith workspace Provider Secrets");
            bail!(
                "The agent model '{model}' uses LangSmith Gateway and requires {key_env} in \
                 LangSmith workspace Provider Secrets, but Provider Secrets could not be checked \
                 ({err:#})."
            );
        }
    }
}

/// The provider key a local run of `model` needs that neither `env_vars` nor
/// the process environment supplies, or `None` when nothing is missing.
///
/// This is deploy's direct-model preflight for `mda dev` and `mda chat`. A local
/// server inherits the shell, so unlike deploy an exported key counts. Gateway
/// models take their credentials from LangSmith and are left to deploy.
pub(crate) fn missing_local_model_api_key(
    model: &str,
    env_vars: &std::collections::BTreeMap<String, String>,
    process_env: impl Fn(&str) -> Option<String>,
) -> Option<&'static str> {
    let dependencies::ModelApiKeyRequirement::DeploymentSecret(key_env) =
        dependencies::deploy_model_api_key_requirement(model)
    else {
        return None;
    };
    let is_set = |value: &str| !value.trim().is_empty();
    let present = env_vars.get(key_env).is_some_and(|value| is_set(value))
        || process_env(key_env).is_some_and(|value| is_set(&value));
    (!present).then_some(key_env)
}

/// Tell the author `model` needs `key_env` in `env`; `next` ends the sentence
/// with what happens once they add it.
pub(crate) fn missing_model_api_key_message(
    model: &str,
    key_env: &str,
    env: &Path,
    next: &str,
) -> String {
    format!(
        "The agent model '{model}' requires {key_env} in {env}. \
         Add `{key_env}=...` to {env}, {next}",
        env = env.display(),
    )
}

/// Cached result of listing LangSmith workspace secret keys for one deploy.
enum WorkspaceSecretKeys {
    Unloaded,
    Keys(Vec<String>),
    LookupFailed(String),
}

/// Outcome of checking whether a named secret exists in the LangSmith workspace.
enum WorkspaceSecretStatus {
    Present,
    Absent,
    LookupFailed(anyhow::Error),
}

/// Return `true` when `key_env` is already staged for this deploy or set in the
/// project `.env`.
fn claim_secret_from_local_sources(
    key_env: &str,
    env_vars: &std::collections::BTreeMap<String, String>,
    secrets: &[langsmith::Secret],
) -> bool {
    secrets.iter().any(|secret| secret.name == key_env)
        || env_vars.get(key_env).is_some_and(|value| !value.is_empty())
}

/// Look up `key_env` in LangSmith workspace secrets (no spinner / messaging).
///
/// Loads and caches the workspace key list on first use so model / ingress /
/// connector requirements preflight share a single platform API call.
fn verify_workspace_secret(
    key_env: &str,
    client: &langsmith::Client,
    workspace_keys: &mut WorkspaceSecretKeys,
) -> WorkspaceSecretStatus {
    if matches!(workspace_keys, WorkspaceSecretKeys::Unloaded) {
        *workspace_keys = match client.workspace_secret_keys() {
            Ok(keys) => WorkspaceSecretKeys::Keys(keys),
            Err(err) => WorkspaceSecretKeys::LookupFailed(format!("{err:#}")),
        };
    }

    match workspace_keys {
        WorkspaceSecretKeys::Unloaded => unreachable!("workspace keys loaded above"),
        WorkspaceSecretKeys::Keys(keys) if keys.iter().any(|key| key == key_env) => {
            WorkspaceSecretStatus::Present
        }
        WorkspaceSecretKeys::Keys(_) => WorkspaceSecretStatus::Absent,
        WorkspaceSecretKeys::LookupFailed(err) => {
            WorkspaceSecretStatus::LookupFailed(anyhow::anyhow!("{err}"))
        }
    }
}

/// PUT the gzipped source archive to the signed GCS upload URL.
///
/// Takes ownership of `bytes` so the request body does not clone the archive.
fn upload_to_gcs(signed_url: &str, bytes: Vec<u8>) -> Result<()> {
    let client = crate::http::client_builder()
        .timeout(Duration::from_secs(300))
        .build()
        .context("failed to build upload client")?;
    let size = bytes.len();
    let response = client
        .put(signed_url)
        .header("Content-Type", "application/gzip")
        .header("Content-Length", size.to_string())
        .header("X-Goog-Content-Length-Range", "0,209715200")
        .body(bytes)
        .send()
        .context("source upload failed")?;
    if !response.status().is_success() {
        let status = response.status();
        let detail = response.text().unwrap_or_else(|_| String::new());
        bail!("source upload failed with status {status}: {detail}");
    }
    Ok(())
}

fn poll_failure_limit_reached(failures: &mut u32) -> bool {
    *failures += 1;
    *failures >= POLL_MAX_FAILURES
}

fn deployment_id_status_record(deployment_id: &str) -> Option<String> {
    let deployment_id = uuid::Uuid::parse_str(deployment_id).ok()?;
    Some(format!("Deployment ID: {}", deployment_id.hyphenated()))
}

#[expect(clippy::print_stderr, reason = "non-interactive deployment record")]
fn report_non_interactive_deployment_id(deployment_id: &str) {
    if std::io::stderr().is_terminal() {
        return;
    }
    if let Some(record) = deployment_id_status_record(deployment_id) {
        eprintln!("{record}");
    }
}

/// Poll the latest revision's status with a live spinner, then print a result.
#[expect(clippy::print_stderr, reason = "non-interactive status stream")]
fn poll_and_report(
    client: &langsmith::Client,
    deployment_id: &str,
    name: &str,
    status_url: Option<&str>,
    schedule_crons: &compile::schedules::ScheduleCrons,
    wait_timeout: Duration,
    interrupt: &Interrupt,
) -> Result<Option<String>> {
    let revisions = client
        .list_revisions(deployment_id, 1)
        .context("failed to list revisions")?;
    let revision_id = revisions
        .get("resources")
        .and_then(|r| r.as_array())
        .and_then(|r| r.first())
        .and_then(|rev| rev.get("id"))
        .and_then(|id| id.as_str())
        .map(str::to_string);

    let Some(revision_id) = revision_id else {
        // The dashboard link was handed over as soon as it was knowable.
        outro(format!(
            "Build triggered for '{name}'. No revision to track yet."
        ))?;
        return Ok(None);
    };

    let sp = spinner();
    sp.start("Waiting for deployment");
    let deadline = Instant::now() + wait_timeout;
    let mut last_status = String::new();
    let mut failures = 0_u32;
    let interactive = std::io::stderr().is_terminal();
    let final_status = loop {
        if interrupt.is_requested() {
            sp.cancel("Stopped waiting; the deployment continues");
            return Err(std::io::Error::from(std::io::ErrorKind::Interrupted).into());
        }
        if Instant::now() >= deadline {
            break last_status;
        }
        let (revision, recovered) = match client.get_revision(deployment_id, &revision_id) {
            Ok(revision) => {
                let recovered = failures > 0;
                failures = 0;
                (revision, recovered)
            }
            Err(err) => {
                if poll_failure_limit_reached(&mut failures) {
                    sp.error("Lost connection while polling status");
                    return Err(err).context("lost connection while polling deployment status");
                }
                otel_logger::warn("deployment status check failed", &err);
                if interactive {
                    sp.set_message("Could not check deployment status. Retrying…");
                } else {
                    eprintln!("warning: could not check deployment status ({err:#}); retrying");
                }
                interrupt.sleep_until_interrupted(POLL_INTERVAL);
                continue;
            }
        };
        let status = revision
            .get("status")
            .and_then(|s| s.as_str())
            .unwrap_or("UNKNOWN")
            .to_string();

        if status != last_status || recovered {
            last_status = status.clone();
            let display_status = display_revision_status(&status);
            sp.set_message(format!("{display_status}…"));
            if !interactive {
                eprintln!("Deployment status: {display_status}");
            }
        }
        if is_terminal_status(&status) {
            break status;
        }
        interrupt.sleep_until_interrupted(POLL_INTERVAL);
    };

    match final_status.as_str() {
        "DEPLOYED" => {
            sp.stop("Deployment live");
            let agent_server_url = fetch_agent_server_url(client, deployment_id)?;
            reconcile_schedules(client, &agent_server_url, schedule_crons)?;
            Ok(Some(agent_server_url))
        }
        status if is_interrupted_status(status) => {
            sp.error("Deployment interrupted from another interface");
            if let Some(url) = &status_url {
                note_links("Inspect the deployment", &[("", url)])?;
            }
            // The cancelled-session footer is rendered by `run`.
            Err(categorized(
                ErrorCategory::Deployment,
                format!("deployment of '{name}' was interrupted (status {status})"),
            ))
        }
        status if is_failed_status(status) => {
            sp.error(format!("Deployment failed ({status})"));
            if let Some(url) = &status_url {
                note_links("Inspect the failure", &[("", url)])?;
            }
            // The cancelled-session footer is rendered by `run`.
            Err(categorized(
                ErrorCategory::Deployment,
                format!("deployment of '{name}' failed with status {status}"),
            ))
        }
        "" => {
            // The deadline elapsed before any status was observed.
            sp.error("Timed out waiting for the deployment");
            if let Some(url) = &status_url {
                note_links("Inspect the deployment", &[("", url)])?;
            }
            Err(categorized(
                ErrorCategory::Deployment,
                format!("timed out waiting for '{name}' to deploy (no status reported)"),
            ))
        }
        status => {
            // Either the deadline elapsed while still mid-deploy, or a terminal
            // status that isn't DEPLOYED (e.g. SKIPPED). Either way, fail.
            sp.error(format!("Deployment did not reach DEPLOYED ({status})"));
            if let Some(url) = &status_url {
                note_links("Inspect the deployment", &[("", url)])?;
            }
            Err(categorized(
                ErrorCategory::Deployment,
                format!("deployment of '{name}' did not reach DEPLOYED (last status: {status})"),
            ))
        }
    }
}

/// Constrain Host-returned revision status before rendering it in terminal output.
fn display_revision_status(status: &str) -> &str {
    if !status.is_empty()
        && status.len() <= 64
        && status
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        status
    } else {
        "UNKNOWN"
    }
}

/// Look up the public Agent Server base URL of a live deployment.
///
/// This is the URL callers point an SDK or HTTP client at, and the base the CLI
/// uses for Agent Server control-plane calls (cron reconciliation).
fn fetch_agent_server_url(client: &langsmith::Client, deployment_id: &str) -> Result<String> {
    let deployment = client
        .get_deployment(deployment_id)
        .context("failed to look up the deployment's Agent Server URL")?;
    let Some(url) = deployment_runtime_url(&deployment) else {
        bail!(
            "deployment is live, but the host backend response did not include an Agent Server URL"
        );
    };
    Ok(url)
}

/// Print the organization and workspace a deploy is about to write into.
///
/// Both lines are best effort: a platform API that cannot be reached, or a
/// credential without organization read, prints one line or none rather than
/// failing a deploy that is otherwise fine.
fn report_deploy_target(target: &langsmith::DeployTarget) -> Result<()> {
    if let Some(organization) = &target.organization {
        log::info(format!("Organization: {organization}"))?;
    }
    if let Some(workspace) = &target.workspace {
        log::info(format!("Workspace: {}", workspace.label()))?;
    }
    Ok(())
}

/// The post-deploy links: where to call the agent, and where to inspect it.
fn deployment_links<'a>(
    agent_server_url: &'a str,
    dashboard_url: Option<&'a str>,
) -> Vec<(&'static str, &'a str)> {
    let mut links = vec![("Agent Server", agent_server_url)];
    if let Some(url) = dashboard_url {
        links.push(("Dashboard", url));
    }
    links
}

/// Only direct HTTP channels advertise an events path; sending capability is irrelevant.
fn http_channel_links<'a>(
    agent_server_url: &str,
    requirements: &'a compile::ChannelRequirements,
) -> Vec<(&'a str, String)> {
    requirements
        .iter()
        .filter(|(_, manifest)| channel_setup::lists_events_url(manifest))
        .filter_map(|(name, manifest)| {
            let path = manifest.get("eventsPath")?.as_str()?;
            Some((
                name.as_str(),
                format!("{}{path}", agent_server_url.trim_end_matches('/')),
            ))
        })
        .collect()
}

/// Reconcile MDA-owned schedules with LangSmith Agent Server crons.
///
/// The deployment is already live by this point, and the Agent Server it talks
/// to may still be cold, so a failure here degrades to a warning rather than
/// failing the deploy: the code is deployed either way, and rerunning
/// `mda deploy` reconciles again.
fn reconcile_schedules(
    client: &langsmith::Client,
    deployment_url: &str,
    schedule_crons: &compile::schedules::ScheduleCrons,
) -> Result<()> {
    let sp = spinner();
    sp.start("Reconciling schedules");
    let removed = match reconcile_schedules_at_url(client, deployment_url, schedule_crons) {
        Ok(removed) => removed,
        Err(err) => {
            sp.error("Could not reconcile schedules");
            otel_logger::warn("schedule reconcile failed", &err);
            log::warning(schedule_reconcile_warning(&err, schedule_crons.len()))?;
            return Ok(());
        }
    };

    match schedules_summary(schedule_crons.len(), removed) {
        Some(message) => sp.stop(message),
        None => sp.clear(),
    }
    Ok(())
}

/// Explain a failed reconcile: what still happened, what did not, and how to
/// finish the job.
fn schedule_reconcile_warning(err: &anyhow::Error, authored: usize) -> String {
    let consequence = if authored == 0 {
        "Any schedules from a previous deploy may still be running."
    } else {
        "The deployment is live, but its schedules may be missing or stale."
    };
    format!("{err:#}\n{consequence} Re-run `mda deploy` to reconcile them.")
}

/// The line to report after reconciling, or `None` when nothing changed and the
/// deploy log is better off without it.
pub(crate) fn schedules_summary(created: usize, removed: usize) -> Option<String> {
    match (created, removed) {
        (0, 0) => None,
        (0, removed) => Some(format!("Removed {removed} schedule(s)")),
        (created, _) => Some(format!("Reconciled {created} schedule(s)")),
    }
}

/// The Agent Server cron calls that reconciling schedules makes.
///
/// [`langsmith::Client`] makes them as the workspace, for deploy and delete.
/// `mda dev` makes them against its local server with its own client, since
/// local builds authenticate control-plane calls differently.
pub(crate) trait CronApi {
    fn search_managed_crons(&self, deployment_url: &str) -> Result<serde_json::Value>;
    fn create_cron(
        &self,
        deployment_url: &str,
        payload: serde_json::Value,
    ) -> Result<serde_json::Value>;
    fn create_thread_cron(
        &self,
        deployment_url: &str,
        thread_id: &str,
        payload: serde_json::Value,
    ) -> Result<serde_json::Value>;
    fn delete_cron(&self, deployment_url: &str, cron_id: &str) -> Result<serde_json::Value>;
}

impl CronApi for langsmith::Client {
    fn search_managed_crons(&self, deployment_url: &str) -> Result<serde_json::Value> {
        langsmith::Client::search_managed_crons(self, deployment_url)
    }

    fn create_cron(
        &self,
        deployment_url: &str,
        payload: serde_json::Value,
    ) -> Result<serde_json::Value> {
        langsmith::Client::create_cron(self, deployment_url, payload)
    }

    fn create_thread_cron(
        &self,
        deployment_url: &str,
        thread_id: &str,
        payload: serde_json::Value,
    ) -> Result<serde_json::Value> {
        langsmith::Client::create_thread_cron(self, deployment_url, thread_id, payload)
    }

    fn delete_cron(&self, deployment_url: &str, cron_id: &str) -> Result<serde_json::Value> {
        langsmith::Client::delete_cron(self, deployment_url, cron_id)
    }
}

/// Replace the deploy-managed crons on the Agent Server at `deployment_url`
/// with `schedule_crons`. `mda dev` runs the same reconcile against its local
/// server.
///
/// Returns how many previously managed crons were deleted.
pub(crate) fn reconcile_schedules_at_url(
    client: &impl CronApi,
    deployment_url: &str,
    schedule_crons: &compile::schedules::ScheduleCrons,
) -> Result<usize> {
    let stale = delete_schedule_crons(client, deployment_url, true)?;

    for (name, cron) in schedule_crons {
        create_schedule_cron(client, deployment_url, name, cron)
            .with_context(|| format!("failed to create cron for schedule '{name}'"))?;
    }

    Ok(stale)
}

/// Delete every MDA-owned cron on a deployment's Agent Server.
///
/// Host teardown is delayed, so remove both deploy and runtime schedules first.
///
/// Returns how many crons were deleted.
pub(crate) fn delete_managed_crons(
    client: &langsmith::Client,
    deployment_url: &str,
) -> Result<usize> {
    delete_schedule_crons(client, deployment_url, false)
}

fn delete_schedule_crons(
    client: &impl CronApi,
    deployment_url: &str,
    deploy_only: bool,
) -> Result<usize> {
    let existing = client
        .search_managed_crons(deployment_url)
        .context("failed to search existing managed crons")?;
    let stale = managed_cron_ids(&existing, deploy_only)
        .context("failed to parse managed cron search response")?;
    for cron_id in &stale {
        client
            .delete_cron(deployment_url, cron_id)
            .with_context(|| format!("failed to delete existing cron {cron_id}"))?;
    }

    Ok(stale.len())
}

fn managed_cron_ids(response: &serde_json::Value, deploy_only: bool) -> Result<Vec<String>> {
    // `CronApi::search_managed_crons` flattens every page into one array.
    let Some(items) = response.as_array() else {
        bail!("unexpected cron search response shape: expected a JSON array");
    };

    Ok(items
        .iter()
        .filter(|cron| {
            let metadata = &cron["metadata"];
            metadata["mda_managed"] == true
                && (!deploy_only
                    || metadata["mda_managed_by"] == "deploy"
                    || (metadata.get("mda_managed_by").is_none()
                        && metadata["mda_schedule"]
                            .as_str()
                            .is_some_and(|name| !name.is_empty())))
        })
        .filter_map(|cron| cron.get("cron_id").and_then(|id| id.as_str()))
        .map(str::to_string)
        .collect())
}

fn create_schedule_cron(
    client: &impl CronApi,
    deployment_url: &str,
    name: &str,
    cron: &serde_json::Value,
) -> Result<()> {
    let (thread_id, payload) = schedule_cron_request(name, cron)?;
    match thread_id {
        Some(thread_id) => {
            client.create_thread_cron(deployment_url, &thread_id, payload)?;
        }
        None => {
            client.create_cron(deployment_url, payload)?;
        }
    }
    Ok(())
}

fn schedule_cron_request(
    name: &str,
    cron: &serde_json::Value,
) -> Result<(Option<String>, serde_json::Value)> {
    let Some(mut payload) = cron.as_object().cloned() else {
        bail!("schedule '{name}' did not compile to an object");
    };
    let graph = payload
        .remove("graph")
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_else(|| "agent".to_string());
    let thread_id = payload
        .remove("thread_id")
        .and_then(|value| value.as_str().map(str::to_string));
    payload.insert("assistant_id".to_string(), serde_json::Value::String(graph));

    Ok((thread_id, serde_json::Value::Object(payload)))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::path::PathBuf;

    use serde_json::json;

    use super::bake::BakePhase;
    use super::image::ImageSource;
    use super::{
        agent_target, bake_spinner_message, channel_secret_policy, claim_secret_from_local_sources,
        collision_decision, deployment_id_status_record, deployment_links, display_revision_status,
        ensure_external_deployment_source, ensure_model_api_key,
        ensure_requested_deployment_source, first_deploy_collisions, http_channel_links,
        managed_cron_ids, missing_local_model_api_key, missing_user_owned_mcp_connections,
        model_api_key_requirement_for_deploy, poll_failure_limit_reached, prompt_api_key_choice,
        require_trigger_registration_url, schedule_cron_request, schedule_reconcile_warning,
        schedules_summary, validate_workspace_connections_for_manifest, ApiKeyPromptChoice,
        CollisionDecision, FirstDeployCollisions, WorkspaceSecretKeys, BAKE_SPINNER_TITLE,
    };
    use crate::compile;
    use crate::error::{category_of, ErrorCategory};
    use crate::langsmith::{AgentEnvironment, Client, ConnectionSummary, Secret};
    use crate::project::{Language, Project};

    fn test_project(root: PathBuf) -> Project {
        Project {
            root,
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("agent.py")],
        }
    }

    fn test_client() -> Client {
        Client::for_test("http://127.0.0.1:1")
    }

    #[test]
    fn deployment_status_poll_stops_after_five_failures() {
        let mut failures = 4;

        assert!(poll_failure_limit_reached(&mut failures));
    }

    #[test]
    fn deployment_status_poll_retries_the_first_four_failures() {
        let mut failures = 0;

        assert!(!(0..4).any(|_| poll_failure_limit_reached(&mut failures)));
    }

    #[test]
    fn external_image_update_rejects_an_internal_source_deployment() {
        let err = ensure_external_deployment_source("agent", &json!({"source": "internal_source"}))
            .unwrap_err();

        assert!(err.to_string().contains("source types cannot be changed"));
    }

    #[test]
    fn external_image_preflight_rejects_an_internal_source_deployment() {
        let image_source =
            super::image::select_image_source(true, Some("registry.example/agent")).unwrap();
        let err = ensure_requested_deployment_source(
            "agent",
            &json!({"source": "internal_source"}),
            &image_source,
        )
        .unwrap_err();

        assert!(err.to_string().contains("source types cannot be changed"));
    }

    #[test]
    fn external_image_preflight_accepts_an_external_source_deployment() {
        let image_source =
            super::image::select_image_source(true, Some("registry.example/agent")).unwrap();

        assert!(ensure_requested_deployment_source(
            "agent",
            &json!({"source": "external_docker"}),
            &image_source,
        )
        .is_ok());
    }

    #[test]
    fn internal_source_preflight_does_not_require_an_external_deployment() {
        assert!(ensure_requested_deployment_source(
            "agent",
            &json!({"source": "internal_source"}),
            &ImageSource::InternalSource,
        )
        .is_ok());
    }

    #[test]
    fn secret_is_claimed_from_the_project_env_file() {
        let mut env = BTreeMap::new();
        env.insert("OPENAI_API_KEY".to_string(), "sk-from-dotenv".to_string());
        let secrets = Vec::new();

        assert!(claim_secret_from_local_sources(
            "OPENAI_API_KEY",
            &env,
            &secrets
        ));
        assert!(secrets.is_empty());
    }

    #[test]
    fn secret_is_claimed_when_already_staged_for_this_deploy() {
        let env = BTreeMap::new();
        let secrets = vec![Secret {
            name: "OPENAI_API_KEY".to_string(),
            value: "sk-already-staged".to_string(),
        }];

        assert!(claim_secret_from_local_sources(
            "OPENAI_API_KEY",
            &env,
            &secrets
        ));
        assert_eq!(secrets.len(), 1);
    }

    #[test]
    fn secret_absent_from_env_file_is_not_claimed() {
        let env = BTreeMap::new();
        let secrets = Vec::new();

        assert!(!claim_secret_from_local_sources(
            "OPENAI_API_KEY",
            &env,
            &secrets
        ));
        assert!(secrets.is_empty());
    }

    #[test]
    fn secret_in_the_process_environment_is_ignored() {
        let env = BTreeMap::new();
        let secrets: Vec<Secret> = Vec::new();

        // Process env is global to the test binary: save, restore, and catch a
        // panic so a failure cannot leak the variable into other tests.
        let previous = std::env::var("OPENAI_API_KEY").ok();
        std::env::set_var("OPENAI_API_KEY", "sk-ambient-from-shell");
        let claimed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            claim_secret_from_local_sources("OPENAI_API_KEY", &env, &secrets)
        }));
        match previous {
            Some(value) => std::env::set_var("OPENAI_API_KEY", value),
            None => std::env::remove_var("OPENAI_API_KEY"),
        }

        assert!(
            !claimed.expect("claim_secret_from_local_sources panicked"),
            "a key present only in the process environment must not be claimed"
        );
        assert!(
            secrets.is_empty(),
            "a key present only in the process environment must not be staged as a secret"
        );
    }

    #[test]
    fn unresolved_gateway_provider_reports_generic_key_configuration_error() {
        let error = model_api_key_requirement_for_deploy("langsmith:unknown/model").unwrap_err();

        assert_eq!(
            error.to_string(),
            "The agent model 'langsmith:unknown/model' uses LangSmith Gateway, but its provider \
             is not supported. Use a supported `langsmith:<provider>/<model>` identifier, then \
             configure that provider under Settings > Integrations > Provider Secrets in \
             LangSmith before deploying."
        );
    }

    #[test]
    fn dynamic_entry_without_static_model_skips_credential_preflight() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let mut workspace_keys = WorkspaceSecretKeys::Unloaded;

        ensure_model_api_key(
            None,
            &project,
            &BTreeMap::new(),
            &[],
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap();

        assert!(matches!(workspace_keys, WorkspaceSecretKeys::Unloaded));
    }

    #[test]
    fn gateway_model_is_not_satisfied_by_a_project_provider_key() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let mut env = BTreeMap::new();
        env.insert(
            "OPENAI_API_KEY".to_string(),
            "test-provider-key".to_string(),
        );
        let mut workspace_keys = WorkspaceSecretKeys::Keys(Vec::new());

        let error = ensure_model_api_key(
            Some("langsmith:openai/gpt-5.5"),
            &project,
            &env,
            &[],
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap_err();

        assert!(error.to_string().contains("Provider Secrets"));
    }

    #[test]
    fn missing_gateway_key_guidance_omits_an_unavailable_dashboard_url() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let mut workspace_keys = WorkspaceSecretKeys::Keys(Vec::new());

        let error = ensure_model_api_key(
            Some("langsmith:openai/gpt-5.5"),
            &project,
            &BTreeMap::new(),
            &[],
            &test_client(),
            None,
            &mut workspace_keys,
        )
        .unwrap_err();

        assert!(!error.to_string().contains("https://"));
    }

    #[test]
    fn local_model_key_is_missing_when_env_value_is_blank() {
        let env = BTreeMap::from([("OPENAI_API_KEY".to_string(), " ".to_string())]);

        assert_eq!(
            missing_local_model_api_key("openai:gpt-5.6-terra", &env, |_| None),
            Some("OPENAI_API_KEY")
        );
    }

    #[test]
    fn local_model_key_accepts_a_key_exported_in_the_shell() {
        let shell = |key: &str| (key == "OPENAI_API_KEY").then(|| "sk-test".to_string());

        assert_eq!(
            missing_local_model_api_key("openai:gpt-5.6-terra", &BTreeMap::new(), shell),
            None
        );
    }

    #[test]
    fn local_model_key_leaves_gateway_models_to_deploy() {
        assert_eq!(
            missing_local_model_api_key("langsmith:openai/gpt-5.5", &BTreeMap::new(), |_| None),
            None
        );
    }

    #[test]
    fn gateway_model_is_satisfied_by_a_workspace_provider_secret() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let env = BTreeMap::new();
        let mut workspace_keys = WorkspaceSecretKeys::Keys(vec!["OPENAI_API_KEY".to_string()]);

        ensure_model_api_key(
            Some("langsmith:openai/gpt-5.5"),
            &project,
            &env,
            &[],
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap();
    }

    #[test]
    fn gateway_model_fails_when_workspace_provider_secrets_cannot_be_checked() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let env = BTreeMap::new();
        let mut workspace_keys =
            WorkspaceSecretKeys::LookupFailed("workspace lookup failed".to_string());

        let error = ensure_model_api_key(
            Some("langsmith:openai/gpt-5.5"),
            &project,
            &env,
            &[],
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap_err();

        assert!(error.to_string().contains("could not be checked"));
    }

    #[test]
    fn direct_model_is_satisfied_by_a_project_provider_key() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let mut env = BTreeMap::new();
        env.insert(
            "OPENAI_API_KEY".to_string(),
            "test-provider-key".to_string(),
        );
        let mut workspace_keys = WorkspaceSecretKeys::LookupFailed("not checked".to_string());

        ensure_model_api_key(
            Some("openai:gpt-5.5"),
            &project,
            &env,
            &[],
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap();
    }

    #[test]
    fn direct_model_accepts_staged_provider_key_without_workspace_lookup() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        let secrets = [Secret {
            name: "OPENAI_API_KEY".into(),
            value: "test-provider-key".into(),
        }];
        let mut workspace_keys = WorkspaceSecretKeys::Unloaded;
        ensure_model_api_key(
            Some("openai:gpt-5.5"),
            &project,
            &BTreeMap::new(),
            &secrets,
            &test_client(),
            Some("https://smith.langchain.com"),
            &mut workspace_keys,
        )
        .unwrap();
        assert!(matches!(workspace_keys, WorkspaceSecretKeys::Unloaded));
    }

    #[test]
    fn direct_model_rejects_workspace_only_provider_key() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        for (model, key) in [
            ("openai:gpt-5.5", "OPENAI_API_KEY"),
            ("gpt-5.5", "OPENAI_API_KEY"),
            ("anthropic:claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
            ("cohere:command-r", "COHERE_API_KEY"),
        ] {
            let mut workspace_keys = WorkspaceSecretKeys::Keys(vec![key.into()]);
            let env = BTreeMap::from([(key.into(), String::new())]);
            let error = ensure_model_api_key(
                Some(model),
                &project,
                &env,
                &[],
                &test_client(),
                Some("https://smith.langchain.com"),
                &mut workspace_keys,
            )
            .unwrap_err();
            assert!(error.to_string().contains(key));
            assert!(error.to_string().contains(".env"));
            assert!(error.to_string().contains(&format!("Add `{key}=...`")));
        }
    }

    #[test]
    fn direct_model_rejects_missing_key_without_workspace_lookup() {
        let root = tempfile::tempdir().unwrap();
        let project = test_project(root.path().to_path_buf());
        for mut workspace_keys in [
            WorkspaceSecretKeys::LookupFailed("unavailable".into()),
            WorkspaceSecretKeys::Unloaded,
        ] {
            let was_unloaded = matches!(workspace_keys, WorkspaceSecretKeys::Unloaded);
            let error = ensure_model_api_key(
                Some("openai:gpt-5.5"),
                &project,
                &BTreeMap::new(),
                &[],
                &test_client(),
                Some("https://smith.langchain.com"),
                &mut workspace_keys,
            )
            .unwrap_err();
            assert!(error.to_string().contains(".env"));
            if was_unloaded {
                assert!(matches!(workspace_keys, WorkspaceSecretKeys::Unloaded));
            }
        }
    }

    #[test]
    fn revision_status_display_rejects_terminal_control_characters() {
        assert_eq!(display_revision_status("BUILDING\nsecret"), "UNKNOWN");
    }

    #[test]
    fn revision_status_display_accepts_host_status_tokens() {
        assert_eq!(display_revision_status("DEPLOY_PENDING"), "DEPLOY_PENDING");
    }

    #[test]
    fn blank_api_key_prompt_means_browser_authorize() {
        assert_eq!(prompt_api_key_choice(""), ApiKeyPromptChoice::Authorize);
        assert_eq!(prompt_api_key_choice("  \t"), ApiKeyPromptChoice::Authorize);
    }

    #[test]
    fn non_empty_api_key_prompt_is_trimmed() {
        assert_eq!(
            prompt_api_key_choice("  lsv2_pt_x  "),
            ApiKeyPromptChoice::ApiKey("lsv2_pt_x".into())
        );
    }

    #[test]
    fn first_deploy_collisions_are_empty_when_resources_do_not_exist() {
        assert_eq!(
            first_deploy_collisions(None, false),
            FirstDeployCollisions::default()
        );
    }

    #[test]
    fn first_deploy_collisions_include_a_foreign_context_hub_repo() {
        assert_eq!(
            first_deploy_collisions(None, true),
            FirstDeployCollisions {
                context_hub_repo: true,
                langsmith_deployment: false,
            }
        );
    }

    #[test]
    fn first_deploy_collisions_ignore_a_deploy_owned_context_hub_repo() {
        // The retry after a deploy that failed between the Context Hub sync and
        // deployment creation: our own repo is left behind, and it must not
        // block the next attempt.
        assert_eq!(
            first_deploy_collisions(None, false),
            FirstDeployCollisions::default()
        );
    }

    #[test]
    fn first_deploy_collisions_include_existing_langsmith_deployment() {
        let deployment = json!({ "is_managed_deep_agent": false });

        assert_eq!(
            first_deploy_collisions(Some(&deployment), false),
            FirstDeployCollisions {
                context_hub_repo: false,
                langsmith_deployment: true,
            }
        );
    }

    #[test]
    fn first_deploy_collisions_include_both_existing_resources() {
        let deployment = json!({});

        assert_eq!(
            first_deploy_collisions(Some(&deployment), true),
            FirstDeployCollisions {
                context_hub_repo: true,
                langsmith_deployment: true,
            }
        );
    }

    #[test]
    fn agent_target_uses_the_name_as_agent_id_and_suffixes_non_production_resources() {
        let (name, agent) =
            agent_target("support".into(), Some(AgentEnvironment::Staging)).unwrap();
        assert_eq!(name, "support-staging");
        let agent = agent.unwrap();
        assert_eq!(agent.agent_id, "support");
        assert_eq!(agent.environment, AgentEnvironment::Staging);

        let (name, _) = agent_target("support".into(), Some(AgentEnvironment::Production)).unwrap();
        assert_eq!(name, "support");

        let (name, agent) = agent_target("support".into(), None).unwrap();
        assert_eq!(name, "support");
        assert!(agent.is_none());
    }

    #[test]
    fn agent_target_rejects_an_invalid_agent_id() {
        assert!(agent_target("2support".into(), Some(AgentEnvironment::Staging)).is_err());
    }

    #[test]
    fn first_deploy_collisions_are_empty_for_managed_redeploy() {
        let deployment = json!({ "is_managed_deep_agent": true });

        assert_eq!(
            first_deploy_collisions(Some(&deployment), true),
            FirstDeployCollisions::default()
        );
    }

    #[test]
    fn bake_spinner_message_puts_the_phase_on_a_second_bar_line() {
        let rendered = bake_spinner_message(BakePhase::RunningSetup);
        let plain = console::strip_ansi_codes(&rendered);
        let mut lines = plain.lines();
        assert_eq!(lines.next(), Some(BAKE_SPINNER_TITLE));
        assert_eq!(lines.next(), Some("│  running setup.sh"));
        assert_eq!(lines.next(), None);
    }

    #[test]
    fn collision_confirmation_is_not_required_when_resources_do_not_exist() {
        for (yes, interactive) in [(false, false), (false, true), (true, false)] {
            assert_eq!(
                collision_decision(
                    "my-agent",
                    &FirstDeployCollisions::default(),
                    yes,
                    interactive
                )
                .unwrap(),
                CollisionDecision::Proceed,
            );
        }
    }

    #[test]
    fn collision_confirmation_fails_without_interactive_terminal_or_yes() {
        let collisions = FirstDeployCollisions {
            context_hub_repo: false,
            langsmith_deployment: true,
        };

        let error = collision_decision("my-agent", &collisions, false, false).unwrap_err();

        assert_eq!(
            error.to_string(),
            "Resources for deployment 'my-agent' already exist and require confirmation before they can be overwritten. Re-run with --yes to overwrite them, or pass --name to deploy under a different name."
        );
        assert_eq!(category_of(&error), ErrorCategory::UserInput);
    }

    #[test]
    fn collision_confirmation_is_answered_by_yes_without_a_terminal() {
        let collisions = FirstDeployCollisions {
            context_hub_repo: true,
            langsmith_deployment: true,
        };

        assert_eq!(
            collision_decision("my-agent", &collisions, true, false).unwrap(),
            CollisionDecision::Proceed,
        );
    }

    #[test]
    fn collision_confirmation_skips_the_prompt_with_yes_in_a_terminal() {
        let collisions = FirstDeployCollisions {
            context_hub_repo: false,
            langsmith_deployment: true,
        };

        assert_eq!(
            collision_decision("my-agent", &collisions, true, true).unwrap(),
            CollisionDecision::Proceed,
        );
    }

    #[test]
    fn collision_confirmation_can_prompt_in_interactive_terminal() {
        let collisions = FirstDeployCollisions {
            context_hub_repo: true,
            langsmith_deployment: false,
        };

        assert_eq!(
            collision_decision("my-agent", &collisions, false, true).unwrap(),
            CollisionDecision::Prompt,
        );
    }

    #[test]
    fn schedules_summary_stays_silent_when_nothing_changed() {
        assert_eq!(schedules_summary(0, 0), None);
    }

    #[test]
    fn schedules_summary_reports_removals_and_reconciliations() {
        assert_eq!(
            schedules_summary(0, 2).as_deref(),
            Some("Removed 2 schedule(s)")
        );
        assert_eq!(
            schedules_summary(3, 0).as_deref(),
            Some("Reconciled 3 schedule(s)")
        );
    }

    #[test]
    fn schedule_reconcile_warning_keeps_the_cause_and_the_next_step() {
        let err = anyhow::anyhow!("operation timed out")
            .context("failed to search existing managed crons");

        let warning = schedule_reconcile_warning(&err, 2);

        assert!(warning.contains("failed to search existing managed crons"));
        assert!(warning.contains("operation timed out"));
        assert!(warning.contains("Re-run `mda deploy`"));
    }

    #[test]
    fn schedule_reconcile_warning_flags_stale_crons_when_nothing_is_authored() {
        let err = anyhow::anyhow!("operation timed out");

        assert!(schedule_reconcile_warning(&err, 0).contains("previous deploy"));
        assert!(schedule_reconcile_warning(&err, 1).contains("missing or stale"));
    }

    #[test]
    fn slack_credentials_are_optional_but_other_channel_secrets_remain_required() {
        let requirements = [
            (
                "slack".to_string(),
                json!({
                    "provider": "slack",
                    "requiredEnv": ["SLACK_SIGNING_SECRET", "SLACK_BOT_TOKEN"]
                }),
            ),
            (
                "other".to_string(),
                json!({"provider": "other", "requiredEnv": ["OTHER_TOKEN"]}),
            ),
        ]
        .into_iter()
        .collect();

        let (required, optional_slack) = channel_secret_policy(&requirements);
        assert_eq!(required, vec!["OTHER_TOKEN"]);
        assert_eq!(
            optional_slack,
            vec!["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"]
        );
    }

    fn connection_row(slug: &str, owner_policy: &str) -> ConnectionSummary {
        ConnectionSummary {
            connection_id: "77777777-7777-7777-7777-777777777777".into(),
            slug: slug.into(),
            display_name: slug.into(),
            kind: "secret".into(),
            oauth_provider_id: None,
            created_at: String::new(),
            updated_at: String::new(),
            oauth_app_slug: None,
            service: None,
            owner_policy: Some(owner_policy.into()),
        }
    }

    fn oauth_connection_row(slug: &str) -> ConnectionSummary {
        ConnectionSummary {
            connection_id: "77777777-7777-7777-7777-777777777777".into(),
            slug: slug.into(),
            display_name: slug.into(),
            kind: "oauth2".into(),
            oauth_provider_id: Some("11111111-1111-1111-1111-111111111111".into()),
            created_at: String::new(),
            updated_at: String::new(),
            oauth_app_slug: Some("notion".into()),
            service: None,
            owner_policy: None,
        }
    }

    fn mcp_connection(slug: &str, server_name: &str) -> compile::McpConnectionEntry {
        compile::McpConnectionEntry {
            connector_name: "mcp".into(),
            server_name: server_name.into(),
            url: format!("https://mcp.{server_name}.com/mcp"),
            slug: slug.into(),
            owner_policy: "user".into(),
        }
    }

    #[test]
    fn deploy_selects_a_unique_missing_user_owned_mcp_connection_for_dcr() {
        let requirements =
            BTreeMap::from([("mcp".to_string(), json!({ "userConnections": ["notion"] }))]);
        let entries = [mcp_connection("notion", "notion")];

        let selected = missing_user_owned_mcp_connections(
            &requirements,
            &BTreeMap::new(),
            &BTreeMap::new(),
            &entries,
        )
        .unwrap();

        assert_eq!(selected, vec![&entries[0]]);
    }

    #[test]
    fn deploy_leaves_an_existing_mcp_connection_unchanged() {
        let requirements =
            BTreeMap::from([("mcp".to_string(), json!({ "userConnections": ["notion"] }))]);
        let existing = BTreeMap::from([("notion".into(), oauth_connection_row("notion"))]);
        let entries = [mcp_connection("notion", "notion")];

        let selected = missing_user_owned_mcp_connections(
            &requirements,
            &BTreeMap::new(),
            &existing,
            &entries,
        )
        .unwrap();

        assert!(selected.is_empty());
    }

    #[test]
    fn deploy_does_not_guess_between_multiple_mcp_servers_for_one_slug() {
        let requirements =
            BTreeMap::from([("mcp".to_string(), json!({ "userConnections": ["shared"] }))]);
        let entries = [
            mcp_connection("shared", "notion"),
            mcp_connection("shared", "linear"),
        ];

        let error = missing_user_owned_mcp_connections(
            &requirements,
            &BTreeMap::new(),
            &BTreeMap::new(),
            &entries,
        )
        .unwrap_err()
        .to_string();

        assert!(error.contains("mcp::notion"), "{error}");
        assert!(error.contains("mcp::linear"), "{error}");
        assert!(error.contains("Keep the slug unique"), "{error}");
    }

    #[test]
    fn deploy_leaves_a_missing_non_mcp_connection_for_normal_validation() {
        let requirements =
            BTreeMap::from([("other".to_string(), json!({ "userConnections": ["acme"] }))]);

        let selected = missing_user_owned_mcp_connections(
            &requirements,
            &BTreeMap::new(),
            &BTreeMap::new(),
            &[],
        )
        .unwrap();

        assert!(selected.is_empty());
    }

    #[test]
    fn connection_validation_accepts_oauth_rows_without_owner_policy() {
        let requirements =
            BTreeMap::from([("mcp".to_string(), json!({ "userConnections": ["notion"] }))]);
        let existing = BTreeMap::from([("notion".into(), oauth_connection_row("notion"))]);

        assert!(validate_workspace_connections_for_manifest(
            &requirements,
            &BTreeMap::new(),
            &existing
        )
        .is_ok());
    }

    #[test]
    fn connection_validation_accepts_existing_workspace_rows() {
        let requirements = BTreeMap::from([(
            "mcp".to_string(),
            json!({
                "userConnections": ["notion"],
                "agentConnections": ["docs-token"],
            }),
        )]);
        let existing = BTreeMap::from([
            ("docs-token".into(), connection_row("docs-token", "agent")),
            ("notion".into(), connection_row("notion", "user")),
        ]);

        assert!(validate_workspace_connections_for_manifest(
            &requirements,
            &BTreeMap::new(),
            &existing
        )
        .is_ok());
    }

    #[test]
    fn connection_validation_rejects_missing_workspace_rows() {
        let requirements = BTreeMap::from([(
            "mcp".to_string(),
            json!({
                "userConnections": ["notion"],
                "agentConnections": ["tavily", "docs-token"],
            }),
        )]);
        let existing = BTreeMap::from([
            ("notion".into(), connection_row("notion", "user")),
            ("docs-token".into(), connection_row("docs-token", "agent")),
        ]);

        let error =
            validate_workspace_connections_for_manifest(&requirements, &BTreeMap::new(), &existing)
                .unwrap_err();

        assert!(error.to_string().contains("'tavily'"), "{error}");
        assert!(error.to_string().contains("agent-owned"), "{error}");
        assert!(
            error.to_string().contains("mda connections create"),
            "{error}"
        );
    }

    #[test]
    fn connection_validation_lists_each_missing_connection_with_its_declared_owner() {
        let requirements = BTreeMap::from([(
            "mcp".to_string(),
            json!({
                "userConnections": ["notion-docs"],
                "agentConnections": ["tavily"],
            }),
        )]);

        let error = validate_workspace_connections_for_manifest(
            &requirements,
            &BTreeMap::new(),
            &BTreeMap::new(),
        )
        .unwrap_err();

        assert_eq!(
            error.to_string(),
            [
                "connections declared by this project are not in the workspace",
                "",
                "  'tavily'       agent-owned",
                "  'notion-docs'  user-owned",
                "",
                "Create each with `mda connections create`.",
            ]
            .join("\n")
        );
    }

    #[test]
    fn connection_validation_names_the_channel_that_provides_a_reserved_connection() {
        let requirements = BTreeMap::from([(
            "mcp".to_string(),
            json!({ "userConnections": ["mda/github-token"] }),
        )]);
        let provided = BTreeMap::from([("mda/github-token".to_string(), "github".to_string())]);

        let error =
            validate_workspace_connections_for_manifest(&requirements, &provided, &BTreeMap::new())
                .unwrap_err();

        assert!(
            error.to_string().contains("the github channel's app"),
            "{error}"
        );
    }

    #[test]
    fn connection_validation_rejects_ambiguous_manifest_ownership() {
        let requirements = BTreeMap::from([(
            "mcp".to_string(),
            json!({
                "userConnections": ["shared"],
                "agentConnections": ["shared"],
            }),
        )]);
        let existing = BTreeMap::new();

        let error =
            validate_workspace_connections_for_manifest(&requirements, &BTreeMap::new(), &existing)
                .unwrap_err();

        assert!(error.to_string().contains("'shared'"), "{error}");
        assert!(
            error
                .to_string()
                .contains("both agent-owned and user-owned"),
            "{error}"
        );
    }

    #[test]
    fn http_channel_links_include_receive_only_channels_and_use_declared_paths() {
        let requirements = serde_json::from_value(json!({
            "orders": {"provider": "slack", "autoReply": false, "eventsPath": "/channels/orders/events"},
            "alerts": {"provider": "custom", "autoReply": true, "eventsPath": "/channels/alerts/events"},
            "slack": {"transport": "trigger_server"},
            "sender": {"provider": "custom", "autoReply": true}
        })).unwrap();
        assert_eq!(
            http_channel_links("https://agent.example/base/", &requirements),
            [
                (
                    "alerts",
                    "https://agent.example/base/channels/alerts/events".to_string()
                ),
                (
                    "orders",
                    "https://agent.example/base/channels/orders/events".to_string()
                ),
            ]
        );
    }

    #[test]
    fn http_channel_links_are_empty_without_http_channels() {
        for requirements in [
            json!({}),
            json!({"slack": {"transport": "trigger_server"}}),
            json!({"github": {"provider": "github", "eventsPath": "/channels/github/events"}}),
        ] {
            assert!(http_channel_links(
                "https://agent.example",
                &serde_json::from_value(requirements).unwrap()
            )
            .is_empty());
        }
    }

    #[test]
    fn channel_setup_requires_a_live_url() {
        let error = require_trigger_registration_url(None, true)
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("channel setup could not complete"),
            "{error}"
        );
        assert_eq!(
            require_trigger_registration_url(Some("https://agent.example".into()), true).unwrap(),
            Some("https://agent.example".into())
        );
    }

    #[test]
    fn deployment_links_lists_agent_server_and_dashboard() {
        assert_eq!(
            deployment_links(
                "https://agent.us.langgraph.app",
                Some("https://smith.langchain.com/o/t-1/host/deployments/dep-1")
            ),
            [
                ("Agent Server", "https://agent.us.langgraph.app"),
                (
                    "Dashboard",
                    "https://smith.langchain.com/o/t-1/host/deployments/dep-1"
                ),
            ]
        );
    }

    #[test]
    fn deployment_links_omits_dashboard_when_tenant_is_unknown() {
        assert_eq!(
            deployment_links("https://agent.us.langgraph.app", None),
            [("Agent Server", "https://agent.us.langgraph.app")]
        );
    }

    #[test]
    fn deployment_id_status_record_canonicalizes_a_uuid() {
        assert_eq!(
            deployment_id_status_record("7AD71A02-1C0E-4477-A0E4-D27E108E5AB6"),
            Some("Deployment ID: 7ad71a02-1c0e-4477-a0e4-d27e108e5ab6".to_string())
        );
    }

    #[test]
    fn deployment_id_status_record_rejects_untrusted_text() {
        assert_eq!(
            deployment_id_status_record("id\nDeployment status: DEPLOYED"),
            None
        );
    }

    #[test]
    fn missing_revision_remains_non_fatal_without_a_trigger_channel() {
        let result = require_trigger_registration_url(None, false).unwrap();

        assert_eq!(result, None);
    }

    #[test]
    fn missing_revision_blocks_trigger_registration_with_retry_guidance() {
        let error = require_trigger_registration_url(None, true).unwrap_err();

        assert_eq!(
            error.to_string(),
            "Deployment started, but channel setup could not complete because no revision was available to track; retry `mda deploy` once the deployment is live."
        );
    }

    #[test]
    fn schedule_cron_request_maps_graph_to_assistant_id() {
        let (thread_id, payload) = schedule_cron_request(
            "daily",
            &json!({
                "graph": "agent",
                "schedule": "0 9 * * *",
                "input": {"messages": [{"role": "user", "content": "Hi"}]},
                "metadata": {"mda_managed": true, "mda_schedule": "daily"},
                "on_run_completed": "delete"
            }),
        )
        .unwrap();

        assert_eq!(thread_id, None);
        assert_eq!(payload["assistant_id"], "agent");
        assert!(payload.get("graph").is_none());
        assert_eq!(payload["on_run_completed"], "delete");
    }

    #[test]
    fn schedule_cron_request_extracts_thread_id() {
        let (thread_id, payload) = schedule_cron_request(
            "nightly",
            &json!({
                "graph": "agent",
                "thread_id": "nightly-thread",
                "schedule": "30 2 * * *",
                "input": {"messages": [{"role": "user", "content": "Sweep"}]},
                "metadata": {"mda_managed": true, "mda_schedule": "nightly"}
            }),
        )
        .unwrap();

        assert_eq!(thread_id.as_deref(), Some("nightly-thread"));
        assert_eq!(payload["assistant_id"], "agent");
        assert!(payload.get("thread_id").is_none());
    }

    #[test]
    fn managed_cron_ids_reads_bare_search_array() {
        let response = json!([
            {"cron_id": "cron-1", "metadata": {"mda_managed": true, "mda_schedule": "daily"}},
            {"cron_id": "cron-2", "metadata": {"mda_managed": true, "mda_managed_by": "runtime"}},
            {"id": "not-a-cron-id"}
        ]);
        assert_eq!(
            managed_cron_ids(&response, false).unwrap(),
            vec!["cron-1", "cron-2"]
        );
    }

    #[test]
    fn managed_cron_ids_rejects_unexpected_search_shape() {
        let response = json!({ "crons": [{ "cron_id": "cron-1" }] });
        assert!(managed_cron_ids(&response, false).is_err());
    }

    #[test]
    fn managed_cron_ids_preserves_runtime_and_unmanaged_records_on_deploy() {
        let response = json!([
            {"cron_id": "legacy", "metadata": {"mda_managed": true, "mda_schedule": "daily"}},
            {"cron_id": "deploy", "metadata": {"mda_managed": true, "mda_managed_by": "deploy"}},
            {"cron_id": "runtime", "metadata": {"mda_managed": true, "mda_schedule": "daily", "mda_managed_by": "runtime"}},
            {"cron_id": "unknown", "metadata": {"mda_managed": true, "mda_managed_by": "unknown"}},
            {"cron_id": "unmarked", "metadata": {"mda_managed": true}},
            {"cron_id": "foreign", "metadata": {"mda_schedule": "daily"}}
        ]);
        assert_eq!(
            managed_cron_ids(&response, true).unwrap(),
            ["legacy", "deploy"]
        );
        assert_eq!(
            managed_cron_ids(&response, false).unwrap(),
            ["legacy", "deploy", "runtime", "unknown", "unmarked"]
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_mirrors_process_env_into_project_and_staged() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(root.path().join(".env"), "OPENAI_API_KEY=test\n").unwrap();
        std::fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_from_shell");
        let result = std::panic::catch_unwind(|| {
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_project_langsmith_api_key panicked");

        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(
            project.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_from_shell")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_prefers_shell_without_overwriting_project() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(
            root.path().join(".env"),
            "LANGSMITH_API_KEY=lsv2_pt_project_file\n",
        )
        .unwrap();
        std::fs::write(
            out_dir.join(".env"),
            "LANGSMITH_API_KEY=lsv2_pt_project_file\n",
        )
        .unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_shell_override");
        let result = std::panic::catch_unwind(|| {
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_project_langsmith_api_key panicked");

        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_shell_override")
        );
        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(
            project.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_project_file")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_follows_env_edit_after_exporting_it() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(
            root.path().join(".env"),
            "LANGSMITH_API_KEY=lsv2_pt_before_edit\n",
        )
        .unwrap();
        std::fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::remove_var("LANGSMITH_API_KEY");
        let result = std::panic::catch_unwind(|| {
            // Exports the `.env` key into this process, as a dev watch run does.
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap();
            std::fs::write(
                root.path().join(".env"),
                "LANGSMITH_API_KEY=lsv2_pt_after_edit\n",
            )
            .unwrap();
            std::fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap();
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_project_langsmith_api_key panicked");

        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_after_edit")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_follows_env_edit_after_pasted_key() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        let project_env = root.path().join(".env");
        std::fs::write(&project_env, "OPENAI_API_KEY=test\n").unwrap();
        std::fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::remove_var("LANGSMITH_API_KEY");
        let result = std::panic::catch_unwind(|| {
            // What the interactive prompt does with a pasted key.
            super::persist_api_key(&project_env, "lsv2_pt_pasted").unwrap();
            std::fs::write(
                &project_env,
                "LANGSMITH_API_KEY=lsv2_pt_pasted_then_edited\n",
            )
            .unwrap();
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap();
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_project_langsmith_api_key panicked");

        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_pasted_then_edited")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_fills_blank_gateway_alias() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(
            root.path().join(".env"),
            "LANGSMITH_API_KEY=\nLANGSMITH_GATEWAY_API_KEY=\n",
        )
        .unwrap();
        std::fs::write(
            out_dir.join(".env"),
            "LANGSMITH_API_KEY=\nLANGSMITH_GATEWAY_API_KEY=\n",
        )
        .unwrap();

        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::set_var("LANGSMITH_API_KEY", "lsv2_pt_gateway");
        let result = std::panic::catch_unwind(|| {
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap()
        });
        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        result.expect("ensure_project_langsmith_api_key panicked");

        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(
            project.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_gateway")
        );
        assert_eq!(
            project.get("LANGSMITH_GATEWAY_API_KEY").map(String::as_str),
            Some("lsv2_pt_gateway")
        );
        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_GATEWAY_API_KEY").map(String::as_str),
            Some("lsv2_pt_gateway")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_keeps_session_pat_out_of_project_on_restage() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();

        let state = tempfile::tempdir().unwrap();
        let previous_state = std::env::var(crate::state::STATE_DIR_ENV).ok();
        std::env::set_var(crate::state::STATE_DIR_ENV, state.path());
        std::fs::write(
            state.path().join("cli-api-keys.json"),
            format!(
                "{{\n  \"{}\": {{\n    \"key\": \"lsv2_pt_session\",\n    \"expires_at\": 4102444800\n  }}\n}}\n",
                crate::langsmith::login::DEFAULT_API_URL
            ),
        )
        .unwrap();

        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(
            root.path().join(".env"),
            "LANGSMITH_API_KEY=\nLANGSMITH_GATEWAY_API_KEY=\n",
        )
        .unwrap();
        std::fs::write(
            out_dir.join(".env"),
            "LANGSMITH_API_KEY=\nLANGSMITH_GATEWAY_API_KEY=\n",
        )
        .unwrap();

        let previous_key = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::remove_var("LANGSMITH_API_KEY");

        let result = std::panic::catch_unwind(|| {
            // First resolve loads the cache into process + staged.
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap();
            // Simulate watch restage wiping the staged session key (project has none).
            std::fs::write(
                out_dir.join(".env"),
                "LANGSMITH_API_KEY=\nLANGSMITH_GATEWAY_API_KEY=\n",
            )
            .unwrap();
            // Second resolve must not treat the process-exported PAT as durable.
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap();
        });

        match previous_key {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        match previous_state {
            Some(value) => std::env::set_var(crate::state::STATE_DIR_ENV, value),
            None => std::env::remove_var(crate::state::STATE_DIR_ENV),
        }
        crate::langsmith::login::clear_session_api_keys_for_test();
        result.expect("ensure_project_langsmith_api_key panicked");

        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(
            project.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("")
        );
        assert_eq!(
            project.get("LANGSMITH_GATEWAY_API_KEY").map(String::as_str),
            Some("")
        );
        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_session")
        );
        assert_eq!(
            staged.get("LANGSMITH_GATEWAY_API_KEY").map(String::as_str),
            Some("lsv2_pt_session")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_strips_leaked_session_pat_from_project() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();

        let state = tempfile::tempdir().unwrap();
        let previous_state = std::env::var(crate::state::STATE_DIR_ENV).ok();
        std::env::set_var(crate::state::STATE_DIR_ENV, state.path());
        std::fs::write(
            state.path().join("cli-api-keys.json"),
            format!(
                "{{\n  \"{}\": {{\n    \"key\": \"lsv2_pt_leaked\",\n    \"expires_at\": 4102444800\n  }}\n}}\n",
                crate::langsmith::login::DEFAULT_API_URL
            ),
        )
        .unwrap();

        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join(".mda").join("build");
        std::fs::create_dir_all(&out_dir).unwrap();
        std::fs::write(
            root.path().join(".env"),
            "LANGSMITH_API_KEY=lsv2_pt_leaked\nLANGSMITH_GATEWAY_API_KEY=lsv2_pt_leaked\n",
        )
        .unwrap();
        std::fs::write(out_dir.join(".env"), "OPENAI_API_KEY=test\n").unwrap();

        let previous_key = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::remove_var("LANGSMITH_API_KEY");

        let result = std::panic::catch_unwind(|| {
            super::ensure_project_langsmith_api_key(root.path(), Some(&out_dir), true).unwrap()
        });

        match previous_key {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
        match previous_state {
            Some(value) => std::env::set_var(crate::state::STATE_DIR_ENV, value),
            None => std::env::remove_var(crate::state::STATE_DIR_ENV),
        }
        crate::langsmith::login::clear_session_api_keys_for_test();
        result.expect("ensure_project_langsmith_api_key panicked");

        let project = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert!(
            project
                .get("LANGSMITH_API_KEY")
                .is_none_or(|value| value.trim().is_empty()),
            "leaked session PAT must be removed from the project .env"
        );
        assert_eq!(
            project.get("LANGSMITH_GATEWAY_API_KEY").map(String::as_str),
            Some("")
        );
        let staged = crate::deploy::env_file::parse_env_file(&out_dir.join(".env"));
        assert_eq!(
            staged.get("LANGSMITH_API_KEY").map(String::as_str),
            Some("lsv2_pt_leaked")
        );
    }

    #[test]
    fn ensure_project_langsmith_api_key_noops_when_not_required_and_unset() {
        let _lock = crate::test_support::LANGSMITH_API_KEY_ENV_LOCK
            .lock()
            .unwrap();
        crate::langsmith::login::clear_session_api_keys_for_test();
        let root = tempfile::tempdir().unwrap();
        let previous = std::env::var("LANGSMITH_API_KEY").ok();
        std::env::remove_var("LANGSMITH_API_KEY");
        std::fs::write(root.path().join(".env"), "LANGSMITH_API_KEY=\n").unwrap();

        super::ensure_project_langsmith_api_key(root.path(), None, false).unwrap();

        let env = crate::deploy::env_file::parse_env_file(&root.path().join(".env"));
        assert_eq!(env.get("LANGSMITH_API_KEY").map(String::as_str), Some(""));

        match previous {
            Some(value) => std::env::set_var("LANGSMITH_API_KEY", value),
            None => std::env::remove_var("LANGSMITH_API_KEY"),
        }
    }
}
