//! `mda delete` — tear down the LangSmith resources a `mda deploy` created.
//!
//! Deploy leaves three kinds of remote state behind, and only the first is
//! obvious from the LangSmith deployments page:
//!
//! 1. the **LangGraph deployment** on the host backend;
//! 2. the **tracing project** LangSmith auto-creates with the same name — a
//!    separate resource that survives deployment deletion and then blocks the
//!    next deploy of that name with a conflict; and
//! 3. the **Context Hub agent repo** for deploy-owned context, plus any
//!    legacy per-user (`{name}--u--…`) or org (`{name}--t--…`) memory children
//!    left from older runtimes, which are keyed off the deploy name and are
//!    not touched by host teardown; and
//! 4. the **managed sandboxes** the runtime provisioned, which carry no
//!    deployment reference of their own and are therefore matched by the
//!    `{name}--` prefix the runtimes give them; and
//! 5. the **Agent Auth connections** the deployment's channels set up, e.g. a
//!    GitHub channel's App; what only the user can remove is linked instead.
//!
//! Managed crons live on the deployment's Agent Server, and they do go away
//! with it — but not promptly. Host teardown of the running container is
//! delayed by an hour after the deployment is marked deleted, and the Agent
//! Server keeps serving for that whole window, so a deleted agent would go on
//! firing its schedules and billing model calls long after this command
//! reports success. Delete therefore sweeps the crons itself while the
//! deployment is still reachable, before dropping the deployment.
//!
//! Every step is idempotent — a resource that is already gone counts as done —
//! so a partially torn-down deployment can be cleaned up by running the command
//! again.

use std::collections::{BTreeMap, BTreeSet};
use std::io::IsTerminal;
use std::path::{Path, PathBuf};

use anyhow::Result;
use cliclack::{confirm, log, note, outro, outro_cancel, spinner};

use crate::agent_config;
use crate::cli_output::show_wordmark_intro;
use crate::command::CommandCompletion;
use crate::deploy::channel_setup::{self, ChannelTeardown};
use crate::deploy::helpers::{
    bake_snapshot_list_prefix, deployment_runtime_url, is_deployment_bake_snapshot,
    is_deployment_sandbox, sandbox_name_prefixes, sandbox_registry_name,
};
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{
    agent_target, delete_managed_crons, ensure_project, env_file, find_target_deployment,
    resolve_api_key, step,
};
use crate::error::{categorized, category_of, is_cancellation, ErrorCategory};
use crate::install::Installation;
use crate::langsmith::{self, AgentEnvironment, ContextHubClient, DeploymentAgent};
use crate::naming::normalize_name;
use crate::project;
use crate::telemetry::{otel_logger, update_facts};

/// Docs page explaining managed sandbox lifetime and retention.
const SANDBOX_DOCS_URL: &str = "https://docs.langchain.com/langsmith/sandboxes";

/// User-facing options for `mda delete`.
pub struct DeleteOptions {
    /// Path to the Managed Deep Agent project.
    pub path: PathBuf,
    /// Deployment name override (defaults to the authored agent `name`).
    pub name: Option<String>,
    pub agent_environment: Option<AgentEnvironment>,
    /// Workspace ID override (takes precedence over `LANGSMITH_WORKSPACE_ID`).
    pub workspace_id: Option<String>,
    /// Host backend URL override.
    pub host_url: Option<String>,
    /// Skip the confirmation prompt.
    pub yes: bool,
}

/// What a delete is about to remove, resolved before anything is destroyed so
/// the user can be shown (and confirm) the full list up front.
#[derive(Debug, Default)]
struct Plan {
    /// Host deployment id, when a deployment with this name still exists.
    deployment_id: Option<String>,
    /// The deployment's Agent Server URL, used to clear its crons before the
    /// deployment is dropped. Absent when the payload carries no runtime URL,
    /// which is the case for a deployment that never finished building.
    deployment_url: Option<String>,
    /// Context Hub repo handles: the deploy repo plus its memory children.
    repos: Vec<String>,
    /// Managed sandbox names carrying this deployment's prefix.
    sandboxes: Vec<String>,
    /// Recipe snapshots (`{deployment}--setup-*`) and leftover temporary bake
    /// bases (`{deployment}--dockerbase-*` / `--sourcebase-*`) to delete.
    recipe_snapshots: Vec<(String, String)>,
    /// Deployment-owned private image registry, when present.
    registry: Option<String>,
    /// Set when sandboxes could not be enumerated, so teardown can say the
    /// deployment is gone without claiming its sandboxes were reclaimed.
    sandbox_lookup_error: Option<String>,
    /// Set when recipe snapshots could not be enumerated.
    recipe_lookup_error: Option<String>,
    /// Set when the deployment-owned registry could not be queried.
    registry_lookup_error: Option<String>,
    /// The Agent Auth connections the deployment's channels set up.
    channel_connections: Option<ChannelTeardown>,
    /// Set when Agent Auth could not be asked for them.
    channel_lookup_error: Option<String>,
}

impl Plan {
    fn is_empty(&self) -> bool {
        self.deployment_id.is_none()
            && self.repos.is_empty()
            && self.sandboxes.is_empty()
            && self.recipe_snapshots.is_empty()
            && self.registry.is_none()
            && self.channel_connections.is_none()
    }

    fn lookup_failed(&self) -> bool {
        self.sandbox_lookup_error.is_some()
            || self.recipe_lookup_error.is_some()
            || self.registry_lookup_error.is_some()
            || self.channel_lookup_error.is_some()
    }

    fn warn_lookup_errors(&self) -> Result<()> {
        if self.sandbox_lookup_error.is_some() {
            warn_sandboxes_not_deleted()?;
        }
        if self.recipe_lookup_error.is_some() {
            warn_recipes_not_deleted()?;
        }
        if self.registry_lookup_error.is_some() {
            warn_registry_not_deleted()?;
        }
        if self.channel_lookup_error.is_some() {
            warn_channel_connections_not_deleted()?;
        }
        Ok(())
    }
}

/// Run the delete command end-to-end.
///
/// Mirrors [`crate::deploy::run`]: pre-flight validation happens before the TUI
/// starts so those failures print plainly, and once `intro` is on screen any
/// error is rendered as a cancelled-session footer instead.
pub fn run(opts: DeleteOptions) -> Result<CommandCompletion> {
    ensure_project(&opts.path)?;
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    let root = project.root.clone();

    show_wordmark_intro()?;
    log::info(format!("Project: {}", root.display()))?;

    match run_session(&opts, &project, &root) {
        Ok(status) => Ok(status),
        Err(err) if is_cancellation(&err) => {
            outro_cancel("Cancelled")?;
            Ok(CommandCompletion::Cancelled)
        }
        Err(err) => {
            let category = category_of(&err);
            otel_logger::error(&err);
            outro_cancel(format!("{err:#}"))?;
            Ok(CommandCompletion::Failed(category))
        }
    }
}

/// The interactive portion of the delete, framed by `intro`/`outro`.
fn run_session(
    opts: &DeleteOptions,
    project: &project::Project,
    root: &Path,
) -> Result<CommandCompletion> {
    let env_path = root.join(".env");
    let env_vars = env_file::parse_env_file(&env_path);
    let ctx = LangSmithContext::resolve(
        &env_vars,
        opts.host_url.as_deref(),
        opts.workspace_id.as_deref(),
        WORKSPACE_FLAG_HINT,
        |platform_api_url| resolve_api_key(&env_vars, &env_path, platform_api_url),
    )?;

    let base_name = match &opts.name {
        Some(name) => normalize_name(name)?,
        None => agent_config::agent_assistant_id(project)?,
    };
    let (name, deployment_agent) = agent_target(base_name, opts.agent_environment)?;
    update_facts(|facts| facts.deployment.deployment_name = Some(name.clone()));

    let client = ctx.client()?;
    let context_client = ctx.context_hub_client()?;

    let mut plan = resolve_plan(&client, &context_client, &name, deployment_agent.as_ref())?;
    if let Some(deployment_id) = plan.deployment_id.clone() {
        resolve_channel_connections(&mut plan, &client, &env_vars, &deployment_id);
    }
    update_facts(|facts| facts.deployment.deployment_id = plan.deployment_id.clone());
    if plan.is_empty() {
        // Everything else is gone, but an unreadable sandbox list means we
        // cannot claim teardown is complete — sandboxes may still be running.
        plan.warn_lookup_errors()?;
        if plan.lookup_failed() {
            outro(format!("Nothing else to delete for '{name}'."))?;
        } else {
            outro(format!("Nothing to delete for '{name}'."))?;
        }
        return Ok(CommandCompletion::Succeeded);
    }

    // An agent environment owns its tracing project, and the host cannot deploy
    // into the environment again once that project is gone.
    let delete_tracing_project = deployment_agent.is_none();
    report_plan(&plan, delete_tracing_project)?;
    if !confirm_delete(&name, opts.yes)? {
        outro_cancel("Cancelled — nothing was deleted.")?;
        return Ok(CommandCompletion::Cancelled);
    }

    // Sandboxes first: they are the only resource that keeps costing while it
    // runs, and they are reachable independently of the deployment.
    delete_sandboxes(&client, &plan.sandboxes)?;
    delete_recipe_snapshots(&client, &plan.recipe_snapshots)?;
    if let Some(registry) = &plan.registry {
        delete_registry(&client, registry)?;
    }
    // Crons must go before the deployment: the sweep talks to the Agent Server,
    // which stays reachable after the deployment is marked deleted but is no
    // longer discoverable, so there would be no way back to it afterwards.
    if let Some(deployment_url) = &plan.deployment_url {
        delete_crons(&client, deployment_url);
    }
    // The connections are found through the deployment ID, so they go first:
    // a failure here leaves the deployment for a second run to find them by.
    if let Some(teardown) = &plan.channel_connections {
        delete_channel_connections(&client, &env_vars, teardown)?;
    }
    if let Some(deployment_id) = &plan.deployment_id {
        delete_deployment(&client, deployment_id, &name, delete_tracing_project)?;
    }
    delete_context_hub_repos(&context_client, &plan.repos)?;

    plan.warn_lookup_errors()?;
    for (title, message) in plan.channel_connections.iter().flat_map(|t| &t.notes) {
        note(title, message)?;
    }
    outro(format!("Deleted '{name}'."))?;
    Ok(CommandCompletion::Succeeded)
}

/// Tell the user sandboxes may still be running, since we could not enumerate
/// them to find out.
fn warn_sandboxes_not_deleted() -> Result<()> {
    note(
        "Sandboxes were not deleted",
        format!(
            "Listing managed sandboxes failed, so none were removed. LangSmith still \
             stops an idle sandbox and deletes it after its retention window; you can \
             also remove them by hand.\n{SANDBOX_DOCS_URL}"
        ),
    )?;
    Ok(())
}

fn warn_recipes_not_deleted() -> Result<()> {
    note(
        "Recipe snapshots were not deleted",
        format!(
            "Listing sandbox recipe snapshots failed, so none were removed. You can \
             remove leftover recipe snapshots by hand in LangSmith.\n{SANDBOX_DOCS_URL}"
        ),
    )?;
    Ok(())
}

fn warn_registry_not_deleted() -> Result<()> {
    note(
        "Sandbox registry was not deleted",
        format!(
            "Looking up the deployment-owned private image registry failed, so it was \
             not removed. You can remove it by hand in LangSmith.\n{SANDBOX_DOCS_URL}"
        ),
    )?;
    Ok(())
}

fn warn_channel_connections_not_deleted() -> Result<()> {
    note(
        "Channel connections were not deleted",
        "Looking up the deployment's channel connections in Agent Auth failed, so they \
         were not removed. You can remove them with `mda connections delete`.",
    )?;
    Ok(())
}

/// Find the Agent Auth connections the deployment's channels set up.
/// Best-effort, like sandboxes: an unreachable Agent Auth must not block
/// tearing down the deployment.
fn resolve_channel_connections(
    plan: &mut Plan,
    client: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
    deployment_id: &str,
) {
    let sp = spinner();
    sp.start("Looking up channel connections");
    match channel_setup::find_teardown(client, env_vars, deployment_id) {
        Ok(Some(teardown)) => {
            sp.stop(format!(
                "Found {} channel connection(s)",
                teardown.apps.len() + teardown.connections.len()
            ));
            plan.channel_connections = Some(teardown);
        }
        Ok(None) => sp.clear(),
        Err(error) => {
            sp.stop("Could not look up channel connections");
            plan.channel_lookup_error = Some(format!("{error:#}"));
        }
    }
}

/// Discover which of the deployment's resources still exist.
fn resolve_plan(
    client: &langsmith::Client,
    context_client: &ContextHubClient,
    name: &str,
    agent: Option<&DeploymentAgent>,
) -> Result<Plan> {
    let sp = spinner();
    sp.start(format!("Looking up deployment '{name}'"));
    let deployment = step(
        &sp,
        "Could not reach LangSmith",
        find_target_deployment(client, name, agent),
    )?;
    let deployment_id = deployment
        .as_ref()
        .and_then(|deployment| deployment.get("id")?.as_str())
        .map(str::to_string);
    let deployment_url = deployment
        .as_ref()
        .zip(deployment_id.as_deref())
        .and_then(|(deployment, id)| resolve_runtime_url(client, deployment, id));
    match &deployment_id {
        Some(id) => sp.stop(format!("Found deployment {id}")),
        None => sp.stop(format!("No deployment named '{name}'")),
    }

    let sp = spinner();
    sp.start("Looking up Context Hub repos");
    let handles = step(
        &sp,
        "Could not list Context Hub repos",
        context_client.list_agent_repo_handles(),
    )?;
    let repos = deployment_repos(&handles, name);
    if repos.is_empty() {
        sp.stop("No Context Hub repos to remove");
    } else {
        sp.stop(format!("Found {} Context Hub repo(s)", repos.len()));
    }

    // Sandbox enumeration is best-effort: the deployment and its context are the
    // resources that block a redeploy, so a workspace where the sandbox API is
    // unavailable should still be able to tear those down.
    let sp = spinner();
    sp.start("Looking up managed sandboxes");
    let listed = sandbox_name_prefixes(name).into_iter().try_fold(
        BTreeSet::new(),
        |mut owned, prefix| -> Result<BTreeSet<String>> {
            for sandbox in client.list_sandboxes_with_prefix(&prefix)? {
                if is_deployment_sandbox(&sandbox.name, name) {
                    owned.insert(sandbox.name);
                }
            }
            Ok(owned)
        },
    );
    let (sandboxes, sandbox_lookup_error) = match listed {
        Ok(found) => {
            let owned: Vec<String> = found.into_iter().collect();
            if owned.is_empty() {
                sp.stop("No managed sandboxes to remove");
            } else {
                sp.stop(format!("Found {} managed sandbox(es)", owned.len()));
            }
            (owned, None)
        }
        Err(error) => {
            let message = format!("{error:#}");
            sp.stop("Could not list managed sandboxes");
            (Vec::new(), Some(message))
        }
    };

    let sp = spinner();
    sp.start("Looking up recipe snapshots");
    let (recipe_snapshots, recipe_lookup_error) =
        match client.list_snapshots_with_prefix(&bake_snapshot_list_prefix(name)) {
            Ok(found) => {
                let owned: Vec<(String, String)> = found
                    .into_iter()
                    .filter(|snapshot| {
                        snapshot
                            .name
                            .as_deref()
                            .is_some_and(|n| is_deployment_bake_snapshot(n, name))
                    })
                    .filter_map(|snapshot| {
                        let name = snapshot.name?;
                        Some((snapshot.id, name))
                    })
                    .collect();
                if owned.is_empty() {
                    sp.stop("No recipe snapshots to remove");
                } else {
                    sp.stop(format!("Found {} recipe snapshot(s)", owned.len()));
                }
                (owned, None)
            }
            Err(error) => {
                let message = format!("{error:#}");
                sp.stop("Could not list recipe snapshots");
                (Vec::new(), Some(message))
            }
        };

    let registry_name = sandbox_registry_name(name);
    let sp = spinner();
    sp.start("Looking up sandbox registry");
    let (registry, registry_lookup_error) = match client.find_registry_by_name(&registry_name) {
        Ok(Some(_)) => {
            sp.stop(format!("Found sandbox registry '{registry_name}'"));
            (Some(registry_name), None)
        }
        Ok(None) => {
            sp.stop("No sandbox registry to remove");
            (None, None)
        }
        Err(error) => {
            let message = format!("{error:#}");
            sp.stop("Could not look up sandbox registry");
            (None, Some(message))
        }
    };

    Ok(Plan {
        deployment_id,
        deployment_url,
        repos,
        sandboxes,
        recipe_snapshots,
        registry,
        sandbox_lookup_error,
        recipe_lookup_error,
        registry_lookup_error,
        channel_connections: None,
        channel_lookup_error: None,
    })
}

/// Find the Agent Server URL to sweep crons on, preferring the list response
/// and falling back to the full deployment object.
///
/// List responses are trimmed, so a live deployment can appear here with an id
/// but no runtime URL. Missing the URL would silently skip the cron sweep and
/// leave the schedules firing for the whole teardown window, which is the case
/// this command exists to prevent, so it is worth a second request to rule out.
///
/// `None` means there is genuinely nothing to sweep — a deployment that never
/// finished building has no Agent Server — or that the lookup failed, which
/// [`delete_crons`] already treats as best-effort.
fn resolve_runtime_url(
    client: &langsmith::Client,
    listed_deployment: &serde_json::Value,
    deployment_id: &str,
) -> Option<String> {
    if let Some(url) = deployment_runtime_url(listed_deployment) {
        return Some(url);
    }
    let deployment = client.get_deployment(deployment_id).ok()?;
    deployment_runtime_url(&deployment)
}

/// Select the Context Hub repos owned by the deployment `name`, ordered so
/// children are deleted before their parent.
///
/// Deleting the deepest handles first means a failure on the parent repo still
/// leaves no orphaned user / legacy-org memory repos behind (those are only
/// discoverable through the deploy-name prefix).
fn deployment_repos(handles: &[String], name: &str) -> Vec<String> {
    let mut repos: Vec<String> = handles
        .iter()
        .filter(|handle| is_deployment_repo(handle, name))
        .cloned()
        .collect();
    repos.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
    repos
}

/// Whether `handle` is the deploy-owned Context Hub repo for `name`, a
/// per-user memory child (`--u--`), or a legacy org memory child (`--t--`).
fn is_deployment_repo(handle: &str, name: &str) -> bool {
    handle == name
        || handle.starts_with(&format!("{name}--u--"))
        || handle.starts_with(&format!("{name}--t--"))
}

/// List the resources about to be destroyed, so the confirmation is informed.
fn report_plan(plan: &Plan, delete_tracing_project: bool) -> Result<()> {
    if plan.deployment_id.is_some() {
        log::warning(format!(
            "Will delete the LangSmith deployment{}",
            tracing_project_suffix(delete_tracing_project)
        ))?;
    }
    for repo in &plan.repos {
        log::warning(format!("Will delete Context Hub repo '{repo}'"))?;
    }
    match (&plan.sandbox_lookup_error, plan.sandboxes.len()) {
        (Some(error), _) => log::warning(format!(
            "Could not list managed sandboxes, so none will be deleted: {error}"
        ))?,
        (None, 0) => {}
        (None, count) => log::warning(format!("Will delete {count} managed sandbox(es)"))?,
    }
    match (&plan.recipe_lookup_error, plan.recipe_snapshots.len()) {
        (Some(error), _) => log::warning(format!(
            "Could not list recipe snapshots, so none will be deleted: {error}"
        ))?,
        (None, 0) => {}
        (None, count) => log::warning(format!("Will delete {count} recipe snapshot(s)"))?,
    }
    match (&plan.registry_lookup_error, &plan.registry) {
        (Some(error), _) => log::warning(format!(
            "Could not look up the sandbox registry, so it will not be deleted: {error}"
        ))?,
        (None, Some(name)) => log::warning(format!("Will delete sandbox registry '{name}'"))?,
        (None, None) => {}
    }
    match (&plan.channel_lookup_error, &plan.channel_connections) {
        (Some(error), _) => log::warning(format!(
            "Could not look up channel connections, so none will be deleted: {error}"
        ))?,
        (None, Some(teardown)) => {
            for (_, slug) in &teardown.apps {
                log::warning(format!(
                    "Will delete the Agent Auth app behind connection '{slug}'"
                ))?;
            }
            for (_, slug) in &teardown.connections {
                log::warning(format!("Will delete Agent Auth connection '{slug}'"))?;
            }
        }
        (None, None) => {}
    }
    Ok(())
}

/// Ask for confirmation, unless `--yes` was passed.
///
/// Without a TTY there is nobody to ask, so require the explicit flag rather
/// than destroying resources on an unattended run.
fn confirm_delete(name: &str, yes: bool) -> Result<bool> {
    if yes {
        return Ok(true);
    }
    if !std::io::stdin().is_terminal() {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!("Refusing to delete '{name}' without confirmation. Re-run with --yes."),
        ));
    }
    Ok(confirm(format!(
        "Delete '{name}'? Agent memory, thread history, and recipe snapshots cannot be recovered."
    ))
    .initial_value(false)
    .interact()?)
}

/// Clear the deployment's schedules so it stops firing the moment delete runs,
/// rather than an hour later when host teardown removes the container.
///
/// Best-effort: an Agent Server that is already unreachable means the crons
/// cannot fire either, and a deployment that failed to build may never have had
/// any. Neither should block tearing down the resources that do exist.
fn delete_crons(client: &langsmith::Client, deployment_url: &str) {
    let sp = spinner();
    sp.start("Deleting schedules");
    match delete_managed_crons(client, deployment_url) {
        Ok(0) => sp.clear(),
        Ok(deleted) => sp.stop(format!("Deleted {deleted} schedule(s)")),
        Err(_) => sp.stop("Could not delete schedules; they stop when the deployment is torn down"),
    }
}

/// Drop the tracing project with a name-mode deployment: it is a separate
/// resource that would otherwise linger and block the next deploy of this name.
fn delete_deployment(
    client: &langsmith::Client,
    deployment_id: &str,
    name: &str,
    delete_tracing_project: bool,
) -> Result<()> {
    let sp = spinner();
    sp.start(format!("Deleting deployment '{name}'"));
    let existed = step(
        &sp,
        "Could not delete the deployment",
        client.delete_deployment(deployment_id, delete_tracing_project),
    )?;
    if existed {
        sp.stop(format!(
            "Deleted deployment '{name}'{}",
            tracing_project_suffix(delete_tracing_project)
        ));
    } else {
        sp.stop(format!("Deployment '{name}' was already gone"));
    }
    Ok(())
}

fn tracing_project_suffix(delete_tracing_project: bool) -> &'static str {
    if delete_tracing_project {
        " and its tracing project"
    } else {
        ""
    }
}

fn delete_sandboxes(client: &langsmith::Client, sandboxes: &[String]) -> Result<()> {
    if sandboxes.is_empty() {
        return Ok(());
    }

    let sp = spinner();
    sp.start(format!("Deleting {} managed sandbox(es)", sandboxes.len()));
    let outcome = step(
        &sp,
        "Could not delete managed sandboxes",
        client.batch_delete_sandboxes(sandboxes),
    )?;
    sp.stop(format!(
        "Deleted {} managed sandbox(es)",
        outcome.deleted.len()
    ));
    for skipped in &outcome.skipped {
        // A sandbox that disappeared between listing and deleting is normal
        // (idle TTL races teardown); report rather than fail.
        match &skipped.reason {
            Some(reason) => log::info(format!("Skipped sandbox '{}': {reason}", skipped.name))?,
            None => log::info(format!("Skipped sandbox '{}'", skipped.name))?,
        }
    }
    Ok(())
}

fn delete_recipe_snapshots(
    client: &langsmith::Client,
    snapshots: &[(String, String)],
) -> Result<()> {
    if snapshots.is_empty() {
        return Ok(());
    }

    let sp = spinner();
    sp.start(format!("Deleting {} recipe snapshot(s)", snapshots.len()));
    let mut deleted = 0usize;
    let mut in_use = Vec::new();
    for (id, name) in snapshots {
        sp.set_message(format!("Deleting recipe snapshot '{name}'"));
        match step(
            &sp,
            &format!("Could not delete recipe snapshot '{name}'"),
            client.delete_snapshot(id),
        )? {
            langsmith::SnapshotDelete::InUse => in_use.push(name.clone()),
            langsmith::SnapshotDelete::Deleted | langsmith::SnapshotDelete::Missing => {
                deleted += 1;
            }
        }
    }
    sp.stop(format!("Deleted {deleted} recipe snapshot(s)"));
    for name in in_use {
        log::info(format!(
            "Skipped recipe snapshot '{name}': still in use by one or more sandboxes"
        ))?;
    }
    Ok(())
}

fn delete_registry(client: &langsmith::Client, name: &str) -> Result<()> {
    let sp = spinner();
    sp.start(format!("Deleting sandbox registry '{name}'"));
    step(
        &sp,
        &format!("Could not delete sandbox registry '{name}'"),
        client.delete_registry(name),
    )?;
    sp.stop(format!("Deleted sandbox registry '{name}'"));
    Ok(())
}

fn delete_channel_connections(
    client: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
    teardown: &ChannelTeardown,
) -> Result<()> {
    let sp = spinner();
    sp.start("Deleting channel connections");
    let deleted = step(
        &sp,
        "Could not delete the channel connections",
        channel_setup::tear_down(client, env_vars, teardown),
    )?;
    sp.stop(format!("Deleted {deleted} channel connection(s)"));
    Ok(())
}

fn delete_context_hub_repos(context_client: &ContextHubClient, repos: &[String]) -> Result<()> {
    if repos.is_empty() {
        return Ok(());
    }

    let sp = spinner();
    sp.start("Deleting Context Hub repos");
    let mut deleted = 0usize;
    for repo in repos {
        sp.set_message(format!("Deleting Context Hub repo '{repo}'"));
        if step(
            &sp,
            &format!("Could not delete Context Hub repo '{repo}'"),
            context_client.delete_repo(repo),
        )? {
            deleted += 1;
        }
    }
    sp.stop(format!("Deleted {deleted} Context Hub repo(s)"));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::deploy::helpers::find_deployment_by_name;

    fn handles(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn selects_the_deploy_repo_and_its_memory_children() {
        let all = handles(&[
            "my-agent",
            "my-agent--u--user-1",
            "my-agent--t--tenant-1",
            "other-agent",
        ]);

        assert_eq!(
            deployment_repos(&all, "my-agent"),
            handles(&["my-agent--t--tenant-1", "my-agent--u--user-1", "my-agent",])
        );
    }

    #[test]
    fn does_not_select_repos_of_similarly_named_deployments() {
        // `my-agent-staging` is a different deployment, and `my-agentx--u--…`
        // does not use the `--u--` separator directly after the name.
        let all = handles(&[
            "my-agent-staging",
            "my-agent-staging--u--user-1",
            "my-agentx--u--user-1",
        ]);

        assert!(deployment_repos(&all, "my-agent").is_empty());
    }

    #[test]
    fn orders_children_before_their_parent() {
        let all = handles(&["my-agent", "my-agent--u--a", "my-agent--u--longer-actor"]);

        let repos = deployment_repos(&all, "my-agent");
        assert_eq!(repos.last().map(String::as_str), Some("my-agent"));
    }

    #[test]
    fn plan_is_empty_only_when_nothing_remains() {
        assert!(Plan::default().is_empty());
        assert!(!Plan {
            deployment_id: Some("dep-1".to_string()),
            ..Plan::default()
        }
        .is_empty());
        assert!(!Plan {
            repos: handles(&["my-agent"]),
            ..Plan::default()
        }
        .is_empty());
        assert!(!Plan {
            sandboxes: handles(&["my-agent--abc123"]),
            ..Plan::default()
        }
        .is_empty());
        assert!(!Plan {
            recipe_snapshots: vec![("id".into(), "my-agent--setup-abc".into())],
            ..Plan::default()
        }
        .is_empty());
        assert!(!Plan {
            registry: Some("my-agent--registry".into()),
            ..Plan::default()
        }
        .is_empty());
        assert!(!Plan {
            channel_connections: Some(ChannelTeardown {
                apps: vec![("a1".into(), "mda/github-app/dep-1".into())],
                connections: vec![],
                notes: vec![],
            }),
            ..Plan::default()
        }
        .is_empty());
        // A runtime URL is a route to resources, not a resource: on its own it
        // must not make the command claim there is something to delete.
        assert!(Plan {
            deployment_url: Some("https://agent.example.com".into()),
            ..Plan::default()
        }
        .is_empty());
    }

    #[test]
    fn plans_the_cron_sweep_from_the_deployment_runtime_url() {
        let listed = serde_json::json!({
            "resources": [{
                "id": "dep-1",
                "name": "my-agent",
                "deployment_url": "https://my-agent-abc.us.langgraph.app/",
            }],
        });

        assert_eq!(
            find_deployment_by_name(&listed, "my-agent")
                .and_then(deployment_runtime_url)
                .as_deref(),
            Some("https://my-agent-abc.us.langgraph.app")
        );
    }

    #[test]
    fn falls_back_to_the_full_deployment_when_the_listing_is_trimmed() {
        // List responses are trimmed, so a live deployment can appear with an
        // id and no runtime URL. Missing it would skip the cron sweep.
        let listed = serde_json::json!({
            "resources": [{ "id": "dep-1", "name": "my-agent" }],
        });

        assert!(find_deployment_by_name(&listed, "my-agent")
            .and_then(deployment_runtime_url)
            .is_none());
        assert_eq!(
            deployment_runtime_url(&serde_json::json!({
                "id": "dep-1",
                "name": "my-agent",
                "deployment_url": "https://my-agent-abc.us.langgraph.app",
            }))
            .as_deref(),
            Some("https://my-agent-abc.us.langgraph.app")
        );
    }

    #[test]
    fn skips_the_cron_sweep_when_the_deployment_has_no_runtime_url() {
        // A deployment that never finished building has no Agent Server to
        // sweep; delete must still tear down everything else.
        let deployment = serde_json::json!({ "id": "dep-1", "name": "my-agent" });

        assert!(deployment_runtime_url(&deployment).is_none());
    }

    #[test]
    fn a_failed_sandbox_lookup_is_reported_even_with_nothing_to_delete() {
        // Nothing is left to confirm, so the command must not prompt — but it
        // still has to warn rather than reporting a clean teardown, because
        // sandboxes it could not enumerate may still be running.
        let plan = Plan {
            sandbox_lookup_error: Some("platform API unavailable".to_string()),
            ..Plan::default()
        };
        assert!(plan.is_empty());
        assert!(plan.lookup_failed());

        let plan = Plan {
            channel_lookup_error: Some("Agent Auth unavailable".to_string()),
            ..Plan::default()
        };
        assert!(plan.is_empty());
        assert!(plan.lookup_failed());
    }

    #[test]
    fn confirmation_is_required_without_a_tty() {
        // `--yes` short-circuits the prompt; the non-TTY guard only applies
        // when confirmation was not already given.
        assert!(confirm_delete("my-agent", true).unwrap());
    }
}
