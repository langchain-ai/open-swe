//! Export a Fleet agent into a Managed Deep Agent project (`mda init --from`).
//!
//! The agent is read into a [`FleetAgent`], every feature's mapper fills in its
//! part of an [`init::Blueprint`](crate::init::Blueprint) or records the
//! feature as not exported, and `mda init` renders the blueprint. Export is
//! best effort: a feature it can't carry over yet is listed in the report, so
//! nothing is dropped silently.

mod mappers;
mod report;
mod snapshot;

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, Context, Result};
use cliclack::log;
use serde_json::Value;

use crate::cli_output::{continue_sequence, SequenceItem};
use crate::command::CommandCompletion;
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{env_file, resolve_api_key, resolve_api_key_unsaved};
use crate::error::{categorized, ErrorCategory};
use crate::init::{self, Blueprint};
use crate::project::Language;

pub(crate) use report::ExportReport;
pub(crate) use snapshot::{FleetAgent, Snapshot};
use snapshot::{TriggerRegistration, WorkspaceConnection};

/// Maps one Fleet feature onto the export, or records it in the report.
pub(crate) type Mapper = fn(&FleetAgent, &mut Export);

/// A Fleet agent carried over as far as export supports.
#[derive(Debug)]
pub(crate) struct Export {
    pub blueprint: Blueprint,
    /// Files written into the project after `init`, keyed by path relative to
    /// the project root. Fleet-only, so they stay off the shared blueprint.
    pub files: BTreeMap<String, String>,
    /// Fleet file paths a mapper has read. The files mapper copies the rest.
    pub claimed_files: BTreeSet<String>,
    pub report: ExportReport,
}

/// Where `--from` saves each fetched agent, next to the projects it creates.
const DATA_DIR: &str = ".data";

/// Set to `1` to save the fetched Fleet responses under `.data/<agent-id>/`,
/// for re-running the export offline with `--from-snapshot`.
const SNAPSHOT_ENV: &str = "MDA_FLEET_EXPORT_SNAPSHOT";

/// Report written into every exported project.
const REPORT_FILE: &str = "FLEET_EXPORT.md";

/// Fleet files the project doesn't use directly: memory to upload after the
/// first deploy, and files no mapper reads. Kept out of git, since memory holds
/// whatever users told the agent.
pub(crate) const EXPORT_FILES_DIR: &str = "fleet_export_files";

/// Where the Fleet agent comes from.
pub(crate) enum Source {
    /// Fetch it, saving the responses under `.data/<agent-id>/` when
    /// `MDA_FLEET_EXPORT_SNAPSHOT=1`.
    Fleet {
        agent_id: String,
        workspace_id: Option<String>,
        host_url: Option<String>,
        env_file: Option<PathBuf>,
    },
    /// A directory an earlier fetch saved.
    Snapshot(PathBuf),
}

/// Create the project `name` in `cwd` from a Fleet agent.
pub(crate) fn run(
    cwd: &Path,
    name: &str,
    language: Language,
    source: Source,
) -> Result<CommandCompletion> {
    init::validate_project_target(cwd, name)?;
    let (data_dir, snapshot) = match source {
        Source::Fleet {
            agent_id,
            workspace_id,
            host_url,
            env_file,
        } => fetch(
            cwd,
            &agent_id,
            workspace_id.as_deref(),
            host_url.as_deref(),
            env_file.as_deref(),
        )?,
        Source::Snapshot(dir) => {
            let path = dir.join("agent.json");
            let raw = fs::read_to_string(&path)
                .with_context(|| format!("failed to read {}", path.display()))?;
            let snapshot = serde_json::from_str(&raw)
                .with_context(|| format!("{} is not a saved Fleet agent", path.display()))?;
            (Some(dir), snapshot)
        }
    };
    let agent = FleetAgent::from_snapshot(&snapshot)?;
    let done = export(&agent, Blueprint::new(name, language));

    let summary = init::init(cwd, &done.blueprint)?;
    let report_path = summary.root.join(REPORT_FILE);
    let written = write_files(&summary.root, &done.files)
        .and_then(|()| ignore_export_files(&summary.root, &done.files))
        .and_then(|()| match &data_dir {
            Some(dir) => write_json(&dir.join("blueprint.json"), &done.blueprint),
            None => Ok(()),
        })
        .and_then(|()| {
            fs::write(&report_path, done.report.markdown(&agent.name, language))
                .with_context(|| format!("failed to write {}", report_path.display()))
        });
    if let Err(err) = written {
        // A project without its export report reads as a complete export.
        let _ = fs::remove_dir_all(&summary.root);
        return Err(err);
    }

    if let Some(summary) = done.report.not_exported_summary() {
        log::warning(summary)?;
    }
    for warning in &done.report.warnings {
        log::warning(warning)?;
    }
    for item in &done.report.sandbox {
        log::warning(format!("Sandbox: {item}"))?;
    }
    if let Some(first) = done.report.skills.first() {
        log::info(format!("Skills: {first}"))?;
    }
    if !done.report.memory_files.is_empty() {
        log::warning(format!(
            "Memory: after your first deploy, run `mda memory upload {EXPORT_FILES_DIR}/memories` to upload the Fleet agent's {} memory file(s).",
            done.report.memory_files.len()
        ))?;
    }
    for note in &done.report.notes {
        log::info(note)?;
    }
    if !done.report.left_behind.is_empty() {
        let items: Vec<String> = done
            .report
            .left_behind
            .iter()
            .map(|item| format!("- {item}"))
            .collect();
        log::info(format!("Left behind in Fleet:\n{}", items.join("\n")))?;
    }
    log::info(format!("Details: {name}/{REPORT_FILE}"))?;
    show_next_steps(
        &summary.root,
        name,
        language,
        &done.report.after_first_deploy,
    )?;
    Ok(CommandCompletion::Succeeded)
}

/// The steps after an export. `mda init`'s own steps point at building a new
/// agent, which an exported one already is.
fn show_next_steps(
    root: &Path,
    name: &str,
    language: Language,
    after_first_deploy: &[String],
) -> Result<()> {
    init::show_interactive_created(root)?;
    let mut items = vec![
        SequenceItem::section(
            "Check the export",
            [format!(
                "{name}/{REPORT_FILE} lists the steps you need to take before your first deploy"
            )],
        ),
        SequenceItem::section(
            "Run locally",
            [
                format!("cd {name}"),
                init::install_command(language).to_string(),
                "mda dev".to_string(),
            ],
        ),
        SequenceItem::section("Deploy when ready", ["mda deploy"]),
    ];
    if !after_first_deploy.is_empty() {
        items.push(SequenceItem::section(
            "After your first deploy",
            after_first_deploy.iter().cloned(),
        ));
    }
    continue_sequence(&items)
}

/// Fetch the agent, its schedules and Trigger Server registrations, its
/// sandbox's access profiles, the workspace's MCP servers and connections, and
/// which Managed Tools ask before running. With `MDA_FLEET_EXPORT_SNAPSHOT=1`,
/// they are saved under `.data/<agent-id>/`, which is returned.
fn fetch(
    cwd: &Path,
    agent_id: &str,
    workspace_id: Option<&str>,
    host_url: Option<&str>,
    env_file: Option<&Path>,
) -> Result<(Option<PathBuf>, Snapshot)> {
    // The id names a directory and a URL path segment.
    if agent_id.is_empty()
        || !agent_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!("{agent_id:?} is not a Fleet agent id"),
        ));
    }
    let env_path = env_file::project_env_path(cwd, env_file);
    let env_vars = env_file::parse_env_file(&env_path);
    let ctx = LangSmithContext::resolve_verified(
        &env_vars,
        host_url,
        workspace_id,
        WORKSPACE_FLAG_HINT,
        |url| match env_file {
            Some(_) => resolve_api_key(&env_vars, &env_path, url),
            // `cwd/.env` is the parent directory's, not the new project's.
            None => resolve_api_key_unsaved(&env_vars, &env_path, url),
        },
    )?;
    let client = ctx.client()?;
    let (agent, middleware_beta_confirmed) = client.fleet_agent(agent_id)?;
    let parsed = FleetAgent::parse(&agent)?;
    // A subagent's tools are its own, so a lead that only delegates still needs these.
    let has_tools = std::iter::once(parsed.tools.as_ref())
        .chain(parsed.subagents.iter().map(|spec| spec.tools.as_ref()))
        .any(|config| config.is_some_and(|config| !config.tools.is_empty()));
    // Only a sandbox backend's own policies are mapped; a computer
    // environment isn't exported.
    let profile_ids = match &parsed.backend {
        Some(backend) if backend.kind == "sandbox" => backend
            .sandbox_config
            .as_ref()
            .map(|config| config.policy_ids.clone())
            .unwrap_or_default(),
        _ => Vec::new(),
    };
    // One the caller can't read is left out, and the mapper says so.
    let access_profiles: Vec<Value> = profile_ids
        .iter()
        .map(|id| client.fleet_access_profile(id))
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .flatten()
        .collect();
    // Fetched only when a mapper reads them, so an agent without tools or
    // policies doesn't depend on the caller being allowed to list them.
    let mcp_servers = if has_tools {
        serde_json::from_value(Value::Array(client.fleet_mcp_servers()?))
            .context("Fleet MCP servers response is not in the expected shape")?
    } else {
        Vec::new()
    };
    let connections = if has_tools || !access_profiles.is_empty() {
        client
            .list_connections_raw()?
            .into_iter()
            .map(|connection| WorkspaceConnection {
                id: connection.connection_id,
                slug: connection.slug,
                kind: connection.kind,
            })
            .collect()
    } else {
        Vec::new()
    };
    let mut snapshot = Snapshot {
        agent,
        schedules: client.fleet_agent_schedules(agent_id)?,
        mcp_servers,
        connections,
        managed_tools_ask_first: BTreeMap::new(),
        access_profiles,
        middleware_beta_confirmed,
        triggers: client
            .agent_triggers(agent_id)?
            .map(|items| {
                items
                    .into_iter()
                    .map(serde_json::from_value)
                    .collect::<Result<Vec<TriggerRegistration>, _>>()
            })
            .transpose()
            .context("Trigger Server triggers response is not in the expected shape")?,
    };
    // A failed lookup only loses those defaults; the approvals mapper warns.
    for url in mappers::managed_tools_urls(&FleetAgent::from_snapshot(&snapshot)?) {
        let ask_first = client.managed_tools_ask_first(&url).ok();
        snapshot.managed_tools_ask_first.insert(url, ask_first);
    }
    if std::env::var(SNAPSHOT_ENV).as_deref() != Ok("1") {
        return Ok((None, snapshot));
    }
    let data = cwd.join(DATA_DIR);
    let dir = data.join(agent_id);
    fs::create_dir_all(&dir).with_context(|| format!("failed to create {}", dir.display()))?;
    // Snapshots hold the agent's prompt and files; keep them out of any repo.
    let ignore = data.join(".gitignore");
    if !ignore.exists() {
        fs::write(&ignore, "*\n")
            .with_context(|| format!("failed to write {}", ignore.display()))?;
    }
    write_json(&dir.join("agent.json"), &snapshot)?;
    log::info(format!(
        "Saved the Fleet responses to {DATA_DIR}/{agent_id}/"
    ))?;
    Ok((Some(dir), snapshot))
}

/// Write `files` under `root`, refusing any path that would land outside it.
fn write_files(root: &Path, files: &BTreeMap<String, String>) -> Result<()> {
    for (relative, contents) in files {
        if !Path::new(relative)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
        {
            bail!("refusing to write {relative:?} outside the project");
        }
        let path = root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        fs::write(&path, contents)
            .with_context(|| format!("failed to write {}", path.display()))?;
    }
    Ok(())
}

/// Add [`EXPORT_FILES_DIR`] to the project's `.gitignore` when export wrote it.
fn ignore_export_files(root: &Path, files: &BTreeMap<String, String>) -> Result<()> {
    let prefix = format!("{EXPORT_FILES_DIR}/");
    if !files.keys().any(|path| path.starts_with(&prefix)) {
        return Ok(());
    }
    let path = root.join(".gitignore");
    let mut text = fs::read_to_string(&path).unwrap_or_default();
    if !text.is_empty() && !text.ends_with('\n') {
        text.push('\n');
    }
    text.push_str(&format!(
        "\n# Fleet memory and leftover files; see {REPORT_FILE}.\n{prefix}\n"
    ));
    fs::write(&path, text).with_context(|| format!("failed to write {}", path.display()))
}

fn write_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let text = format!("{}\n", serde_json::to_string_pretty(value)?);
    fs::write(path, text).with_context(|| format!("failed to write {}", path.display()))
}

/// Run every mapper over `agent`, starting from `base`.
pub(crate) fn export(agent: &FleetAgent, base: Blueprint) -> Export {
    let mut out = Export {
        blueprint: base,
        files: BTreeMap::new(),
        claimed_files: BTreeSet::new(),
        report: ExportReport::default(),
    };
    for map in mappers::ALL {
        map(agent, &mut out);
    }
    out
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::{Path, PathBuf};

    use super::*;
    use crate::project::Language;

    /// Each case holds an `agent.json` snapshot and the expected
    /// `blueprint.json` and `report.json`, plus `files.json` when the export
    /// copies files.
    /// `MDA_UPDATE_FIXTURES=1` rewrites the expected files.
    fn testdata() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/fleet_export/testdata")
    }

    fn check(case: &Path, file: &str, actual: String) {
        let path = case.join(file);
        if std::env::var_os("MDA_UPDATE_FIXTURES").is_some() {
            fs::write(&path, &actual).unwrap();
            return;
        }
        let expected = fs::read_to_string(&path).unwrap_or_else(|_| {
            panic!("missing {}; run with MDA_UPDATE_FIXTURES=1", path.display())
        });
        assert_eq!(actual, expected, "{} differs", path.display());
    }

    fn pretty(value: &impl serde::Serialize) -> String {
        format!("{}\n", serde_json::to_string_pretty(value).unwrap())
    }

    #[test]
    fn fixtures_match() {
        let mut cases: Vec<_> = fs::read_dir(testdata())
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        cases.sort();
        assert!(!cases.is_empty());
        for case in cases {
            let raw = fs::read_to_string(case.join("agent.json")).unwrap();
            let snapshot: Snapshot = serde_json::from_str(&raw).unwrap();
            let agent = FleetAgent::from_snapshot(&snapshot).unwrap();
            let done = export(&agent, Blueprint::new("demo", Language::Python));
            check(&case, "blueprint.json", pretty(&done.blueprint));
            check(&case, "report.json", pretty(&done.report));
            if done.files.is_empty() {
                assert!(!case.join("files.json").exists());
            } else {
                check(&case, "files.json", pretty(&done.files));
            }
        }
    }

    #[test]
    fn export_is_deterministic() {
        for case in fs::read_dir(testdata()).unwrap() {
            let raw = fs::read_to_string(case.unwrap().path().join("agent.json")).unwrap();
            let snapshot: Snapshot = serde_json::from_str(&raw).unwrap();
            let agent = FleetAgent::from_snapshot(&snapshot).unwrap();
            let run = || {
                let done = export(&agent, Blueprint::new("demo", Language::Python));
                pretty(&(done.blueprint, done.files, done.report))
            };
            assert_eq!(run(), run());
        }
    }
}
