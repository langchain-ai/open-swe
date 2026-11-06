use std::collections::BTreeMap;
use std::fs;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use cliclack::{confirm, log};
use serde_json::Value;
use walkdir::WalkDir;

use crate::agent_config;
use crate::command::CommandCompletion;
use crate::context_layout::project_context_root;
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{agent_environment, agent_target, env_file, resolve_api_key};
use crate::install::Installation;
use crate::langsmith::AgentEnvironment;
use crate::naming::normalize_name;
use crate::path_util::to_unix_path;
use crate::project::{is_junk_dir, is_junk_file};
use crate::telemetry::update_facts;

#[derive(clap::Args)]
pub(crate) struct PullOptions {
    /// Path to the existing Deep Agent project.
    #[arg(default_value = ".")]
    pub path: PathBuf,
    /// Deployment name (defaults to the authored agent name).
    #[arg(short = 'n', long)]
    pub name: Option<String>,
    /// Agent environment to read from; the deployment name is used as the
    /// agent ID. Defaults to LANGSMITH_AGENT_ENVIRONMENT in .env.
    #[arg(long, value_enum)]
    pub agent_environment: Option<AgentEnvironment>,
    /// Workspace to read from (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    pub workspace_id: Option<String>,
    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    pub host_url: Option<String>,
    /// Read credentials from this file instead of the project .env.
    #[arg(long, value_name = "PATH")]
    pub env_file: Option<PathBuf>,
    /// Preview additions, overwrites, and deletions without changing files.
    #[arg(long)]
    pub dry_run: bool,
    /// Replace local instructions and skills without asking for confirmation.
    #[arg(short = 'y', long)]
    pub yes: bool,
}

pub(crate) fn run(opts: PullOptions) -> Result<CommandCompletion> {
    let installation = Installation::from_env()?;
    let project = installation.load_project(&opts.path)?;
    let base_name = match opts.name.as_deref() {
        Some(name) => normalize_name(name)?,
        None => agent_config::agent_assistant_id(&project)?,
    };
    let env_path = env_file::project_env_path(&opts.path, opts.env_file.as_deref());
    let env_vars = env_file::parse_env_file(&env_path);
    let name = target_name(base_name, opts.agent_environment, &env_vars)?;
    update_facts(|facts| facts.deployment.deployment_name = Some(name.clone()));
    let ctx = LangSmithContext::resolve_verified(
        &env_vars,
        opts.host_url.as_deref(),
        opts.workspace_id.as_deref(),
        WORKSPACE_FLAG_HINT,
        |url| resolve_api_key(&env_vars, &env_path, url),
    )?;
    let snapshot = ctx.context_hub_client()?.get_directory(&name)?;
    let root = project_context_root(&project);
    let changes = plan(&root, &snapshot.files)?;
    if changes.is_empty() {
        log::success("Local instructions and skills already match Context Hub.")?;
        return Ok(CommandCompletion::Succeeded);
    }
    for change in &changes {
        log::info(format!("{} {}", change.action(), change.path))?;
    }
    if opts.dry_run {
        return Ok(CommandCompletion::Succeeded);
    }
    if !opts.yes {
        if !std::io::stdin().is_terminal() {
            bail!("Pull would replace local context. Inspect with --dry-run, then pass --yes to apply.");
        }
        if !confirm("Replace local instructions and skills with these Hub files? Local-only skills will be deleted.")
            .initial_value(false)
            .interact()?
        {
            return Ok(CommandCompletion::Cancelled);
        }
    }
    apply(&root, &changes)?;
    log::success(format!("Pulled instructions and skills from '{name}'."))?;
    Ok(CommandCompletion::Succeeded)
}

/// The Context Hub repo `mda deploy` writes for this agent: the name, suffixed
/// for a non-production agent environment.
fn target_name(
    base_name: String,
    environment: Option<AgentEnvironment>,
    env_vars: &BTreeMap<String, String>,
) -> Result<String> {
    let environment = agent_environment(environment, env_vars)?;
    Ok(agent_target(base_name, environment)?.0)
}

#[derive(Debug)]
struct Change {
    path: String,
    before: Option<Vec<u8>>,
    after: Option<String>,
}

impl Change {
    fn action(&self) -> &str {
        match (&self.before, &self.after) {
            (None, _) => "Add",
            (_, None) => "Delete",
            _ => "Overwrite",
        }
    }
}

fn validate_path(path: &str) -> Result<()> {
    if path != "instructions.md" && !path.starts_with("skills/") {
        bail!("Not a pull-owned path: {path:?}");
    }
    if path.split('/').any(|part| {
        part.is_empty()
            || part == "."
            || part == ".."
            || part
                .chars()
                .any(|c| c.is_control() || matches!(c, '\\' | ':'))
    }) {
        bail!("Unsafe Context Hub path: {path:?}");
    }
    Ok(())
}

fn checked_path(root: &Path, path: &str) -> Result<PathBuf> {
    validate_path(path)?;
    let mut target = root.to_path_buf();
    let parts: Vec<_> = path.split('/').collect();
    for (index, part) in parts.iter().enumerate() {
        target.push(part);
        match fs::symlink_metadata(&target) {
            Ok(meta) => {
                if meta.is_symlink()
                    || (index + 1 < parts.len() && !meta.is_dir())
                    || (index + 1 == parts.len() && !meta.is_file())
                {
                    bail!(
                        "Refusing symlink or incompatible local path: {}",
                        target.display()
                    );
                }
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(err) => return Err(err).context("could not inspect local context path"),
        }
    }
    Ok(target)
}

fn read_local(root: &Path, path: &str) -> Result<Option<Vec<u8>>> {
    let target = checked_path(root, path)?;
    match fs::read(target) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err).context("could not read local context"),
    }
}

fn plan(root: &Path, remote: &BTreeMap<String, Value>) -> Result<Vec<Change>> {
    let mut desired = BTreeMap::new();
    for (path, entry) in remote {
        if path != "instructions.md" && !path.starts_with("skills/") {
            continue;
        }
        validate_path(path)?;
        if entry.get("type").and_then(Value::as_str) != Some("file") {
            bail!("Context Hub path is not a text file: {path:?}");
        }
        let text = entry
            .get("content")
            .and_then(Value::as_str)
            .with_context(|| format!("Context Hub file has no text content: {path:?}"))?;
        desired.insert(path.clone(), Some(text.to_string()));
    }
    if !desired.contains_key("instructions.md") {
        bail!("Remote instructions.md is missing; no local files were changed. Check the deployment name and workspace.");
    }
    let skills = root.join("skills");
    let skills_meta = match fs::symlink_metadata(&skills) {
        Ok(meta) => Some(meta),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => None,
        Err(err) => return Err(err).context("could not inspect local skills directory"),
    };
    if let Some(meta) = skills_meta {
        if !meta.is_dir() || meta.is_symlink() {
            bail!("Local skills must be a directory, not a symlink or file");
        }
        // Deploy never uploads OS junk or bytecode, so it is not "local-only".
        for entry in WalkDir::new(&skills)
            .follow_links(false)
            .into_iter()
            .filter_entry(|entry| !is_junk_dir(entry.path()))
        {
            let entry = entry.context("could not inspect local skills")?;
            if entry.file_type().is_dir() || is_junk_file(entry.path()) {
                continue;
            }
            let path = to_unix_path(entry.path().strip_prefix(root)?);
            checked_path(root, &path)?;
            desired.entry(path).or_insert(None);
        }
    }
    for path in desired.keys() {
        let mut ancestor = Path::new(path).parent();
        while let Some(parent) = ancestor {
            if desired
                .get(&to_unix_path(parent))
                .is_some_and(Option::is_some)
            {
                bail!("Conflicting remote file and directory: {path:?}");
            }
            ancestor = parent.parent();
        }
    }
    let mut changes = Vec::new();
    for (path, after) in desired {
        let before = read_local(root, &path)?;
        if before.as_deref() != after.as_ref().map(|text| text.as_bytes()) {
            changes.push(Change {
                path,
                before,
                after,
            });
        }
    }
    Ok(changes)
}

fn apply(root: &Path, changes: &[Change]) -> Result<()> {
    for change in changes {
        if read_local(root, &change.path)? != change.before {
            bail!("Local context changed during pull; rerun to review the new changes.");
        }
    }
    for change in changes {
        let target = checked_path(root, &change.path)?;
        match &change.after {
            Some(text) => {
                let parent = target.parent().context("context file has no parent")?;
                fs::create_dir_all(parent)?;
                let temp = parent.join(format!(".mda-pull-{}", uuid::Uuid::new_v4()));
                let result = (|| -> Result<()> {
                    use std::io::Write;
                    let mut file = fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(&temp)?;
                    file.write_all(text.as_bytes())?;
                    fs::rename(&temp, &target)?;
                    Ok(())
                })();
                if result.is_err() {
                    let _ = fs::remove_file(&temp);
                }
                result.with_context(|| format!("could not write {}", change.path))?;
            }
            None => fs::remove_file(target)?,
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::write_file;
    use serde_json::json;

    fn remote() -> BTreeMap<String, Value> {
        BTreeMap::from([
            (
                "instructions.md".into(),
                json!({"type":"file", "content":"remote prompt"}),
            ),
            (
                "skills/research/SKILL.md".into(),
                json!({"type":"file", "content":"remote skill"}),
            ),
            (
                "memories/agent/AGENTS.md".into(),
                json!({"type":"file", "content":"private"}),
            ),
            (
                "agent.py".into(),
                json!({"type":"file", "content":"not local code"}),
            ),
        ])
    }

    #[test]
    fn previews_then_mirrors_only_instructions_and_skills() {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), "instructions.md", "local prompt");
        write_file(dir.path(), "skills/obsolete/SKILL.md", "local skill");
        write_file(dir.path(), "agent.py", "local code");
        write_file(dir.path(), "memories/agent/AGENTS.md", "local memory");
        let changes = plan(dir.path(), &remote()).unwrap();
        assert_eq!(
            changes
                .iter()
                .map(|c| (c.action(), c.path.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("Overwrite", "instructions.md"),
                ("Delete", "skills/obsolete/SKILL.md"),
                ("Add", "skills/research/SKILL.md"),
            ]
        );
        assert_eq!(
            fs::read_to_string(dir.path().join("instructions.md")).unwrap(),
            "local prompt"
        );
        apply(dir.path(), &changes).unwrap();
        assert_eq!(
            fs::read_to_string(dir.path().join("instructions.md")).unwrap(),
            "remote prompt"
        );
        assert_eq!(
            fs::read_to_string(dir.path().join("skills/research/SKILL.md")).unwrap(),
            "remote skill"
        );
        assert!(!dir.path().join("skills/obsolete/SKILL.md").exists());
        assert_eq!(
            fs::read_to_string(dir.path().join("agent.py")).unwrap(),
            "local code"
        );
        assert_eq!(
            fs::read_to_string(dir.path().join("memories/agent/AGENTS.md")).unwrap(),
            "local memory"
        );
        assert!(plan(dir.path(), &remote()).unwrap().is_empty());
    }

    #[test]
    fn targets_the_agent_environment_repo_deploy_writes() {
        let env = BTreeMap::from([("LANGSMITH_AGENT_ENVIRONMENT".into(), "staging".into())]);
        assert_eq!(
            target_name("support".into(), None, &env).unwrap(),
            "support-staging"
        );
        let production = Some(AgentEnvironment::Production);
        assert_eq!(
            target_name("support".into(), production, &env).unwrap(),
            "support"
        );
        assert_eq!(
            target_name("support".into(), None, &BTreeMap::new()).unwrap(),
            "support"
        );
    }

    #[test]
    fn keeps_local_junk_files_deploy_never_uploads() {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), "instructions.md", "remote prompt");
        write_file(dir.path(), "skills/research/SKILL.md", "remote skill");
        write_file(dir.path(), "skills/.DS_Store", "junk");
        write_file(
            dir.path(),
            "skills/research/__pycache__/x.cpython-312.pyc",
            "junk",
        );
        write_file(dir.path(), "skills/research/helper.pyc", "junk");
        assert!(plan(dir.path(), &remote()).unwrap().is_empty());
    }

    #[test]
    fn refuses_missing_prompt_malformed_entries_and_unsafe_paths_before_writing() {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), "instructions.md", "local");
        assert!(plan(dir.path(), &BTreeMap::new()).is_err());
        for path in [
            "skills/../agent.py",
            "skills//file",
            "skills/./file",
            "skills/\\file",
            "skills/C:/file",
            "skills/bad\nfile",
            "skills/a/../../outside",
        ] {
            let mut files = remote();
            files.insert(path.into(), json!({"type":"file", "content":"bad"}));
            assert!(plan(dir.path(), &files).is_err(), "{path:?}");
        }
        for entry in [
            json!({"type":"file"}),
            json!({"type":"directory", "content":"bad"}),
        ] {
            let mut files = remote();
            files.insert("skills/bad".into(), entry);
            assert!(plan(dir.path(), &files).is_err());
        }
        let mut files = remote();
        files.insert(
            "skills/research".into(),
            json!({"type":"file", "content":"bad"}),
        );
        assert!(plan(dir.path(), &files).is_err());
        assert_eq!(
            fs::read_to_string(dir.path().join("instructions.md")).unwrap(),
            "local"
        );
    }

    #[test]
    fn refuses_changes_made_after_preview_before_applying_any_file() {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), "instructions.md", "local");
        let changes = plan(dir.path(), &remote()).unwrap();
        write_file(dir.path(), "skills/research/SKILL.md", "new local edit");
        assert!(apply(dir.path(), &changes).is_err());
        assert_eq!(
            fs::read_to_string(dir.path().join("instructions.md")).unwrap(),
            "local"
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_files_and_directories_without_touching_the_target() {
        use std::os::unix::fs::symlink;
        let outside = tempfile::tempdir().unwrap();
        write_file(outside.path(), "prompt", "untouched");
        for path in [
            "instructions.md",
            "skills",
            "skills/research",
            "skills/research/SKILL.md",
        ] {
            let dir = tempfile::tempdir().unwrap();
            let target = dir.path().join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            symlink(outside.path().join("prompt"), &target).unwrap();
            assert!(plan(dir.path(), &remote()).is_err(), "{path}");
        }
        assert_eq!(
            fs::read_to_string(outside.path().join("prompt")).unwrap(),
            "untouched"
        );
    }
}
