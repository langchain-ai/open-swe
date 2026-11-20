use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use cliclack::log;
use serde_json::{json, Value};
use walkdir::WalkDir;

use crate::command::CommandCompletion;
use crate::compile::{declare_memory_snippet, resolve_memory};
use crate::deploy::find_target_deployment;
use crate::hub_target::{HubTarget, HubTargetArgs};
use crate::install::Installation;
use crate::langsmith::RepoOwnership;
use crate::path_util::to_unix_path;
use crate::project::{is_junk_dir, is_junk_file, Project};

const AGENT_MEMORY_DIR: &str = "memories/agent";

#[derive(clap::Args)]
pub(crate) struct MemoryUploadArgs {
    /// Folder whose files become memories/agent/<path>.
    pub folder: PathBuf,
    /// Path to the Deep Agent project (defaults to the current directory).
    #[arg(long = "project", value_name = "PATH", default_value = ".")]
    pub path: PathBuf,
    #[command(flatten)]
    pub target: HubTargetArgs,
    /// List what would upload and what would be skipped, without writing.
    #[arg(long)]
    pub dry_run: bool,
}

pub(crate) fn upload(args: MemoryUploadArgs) -> Result<CommandCompletion> {
    let local = read_folder(&args.folder)?;
    let installation = Installation::from_env()?;
    let project = installation.load_project(&args.path)?;
    require_agent_memory(&project)?;
    let HubTarget { name, agent, ctx } = args.target.resolve(&args.path, &project)?;
    if find_target_deployment(&ctx.client()?, &name, agent.as_ref())?.is_none() {
        bail!("No Agent Server named '{name}' yet. Run `mda deploy` first.");
    }
    let hub = ctx.context_hub_client()?;
    require_deployed_repo(hub.repo_ownership(&name)?, &name)?;
    let snapshot = hub.get_directory(&name)?;
    let (files, skipped) = plan_upload(local, &snapshot.files);
    for path in files.keys() {
        log::info(format!("Upload {path}"))?;
    }
    for path in &skipped {
        log::info(format!("Skip {path} (memory already has it)"))?;
    }
    if files.is_empty() {
        log::success(format!("'{name}' already has every file in this folder."))?;
        return Ok(CommandCompletion::Succeeded);
    }
    if args.dry_run {
        return Ok(CommandCompletion::Succeeded);
    }
    let count = files.len();
    hub.create_directory_commit(
        &name,
        snapshot.commit_hash.as_deref(),
        files,
        "the upload",
        "mda memory upload",
    )?;
    log::success(format!("Uploaded {count} memory file(s) to '{name}'."))?;
    Ok(CommandCompletion::Succeeded)
}

/// The runtime mounts `memories/agent/` only when the project turns on agent
/// memory, so files uploaded without it would never reach the agent.
fn require_agent_memory(project: &Project) -> Result<()> {
    if resolve_memory(project)?.agent_enabled() {
        return Ok(());
    }
    let extension = project.language.extension();
    let declare = declare_memory_snippet(project.language);
    bail!(
        "This project doesn't turn on agent memory, so the agent would never read uploaded files. \
         Add memory.{extension} with `{declare}`, run `mda deploy`, then upload again."
    )
}

/// Writes only into a repo `mda deploy` created, as deploy does: a same-name repo
/// something else made is someone else's data.
fn require_deployed_repo(ownership: RepoOwnership, name: &str) -> Result<()> {
    match ownership {
        RepoOwnership::ManagedDeepAgent => Ok(()),
        RepoOwnership::Missing => {
            bail!("Agent Server '{name}' has no Context Hub repo yet. Run `mda deploy` first.")
        }
        RepoOwnership::Foreign => bail!(
            "Context Hub repo '{name}' was not created by Managed Deep Agents, so memory upload won't write to it."
        ),
    }
}

/// Text files under `folder`, keyed by their `/`-separated path inside it.
/// Skips what deploy never uploads (OS junk, bytecode).
fn read_folder(folder: &Path) -> Result<BTreeMap<String, String>> {
    let meta = fs::symlink_metadata(folder)
        .with_context(|| format!("could not read {}", folder.display()))?;
    if !meta.is_dir() {
        bail!(
            "{} must be a folder, not a file or symlink",
            folder.display()
        );
    }
    let mut files = BTreeMap::new();
    for entry in WalkDir::new(folder)
        .follow_links(false)
        .into_iter()
        .filter_entry(|entry| !is_junk_dir(entry.path()))
    {
        let entry = entry.with_context(|| format!("could not read {}", folder.display()))?;
        let file_type = entry.file_type();
        if file_type.is_symlink() {
            bail!("refusing to upload symlink {}", entry.path().display());
        }
        if file_type.is_dir() || is_junk_file(entry.path()) {
            continue;
        }
        let path = to_unix_path(entry.path().strip_prefix(folder)?);
        let content = fs::read_to_string(entry.path()).with_context(|| {
            format!(
                "could not read {} as UTF-8 text; memory holds text files only",
                entry.path().display()
            )
        })?;
        files.insert(path, content);
    }
    if files.is_empty() {
        bail!("{} has no files to upload", folder.display());
    }
    Ok(files)
}

/// The commit's files (`memories/agent/<path>`) and the paths skipped because
/// the repo already has them; the agent may have edited those since deploy.
fn plan_upload(
    local: BTreeMap<String, String>,
    remote: &BTreeMap<String, Value>,
) -> (BTreeMap<String, Value>, Vec<String>) {
    let mut files = BTreeMap::new();
    let mut skipped = Vec::new();
    for (path, content) in local {
        let path = format!("{AGENT_MEMORY_DIR}/{path}");
        if remote.contains_key(&path) {
            skipped.push(path);
        } else {
            files.insert(path, json!({ "type": "file", "content": content }));
        }
    }
    (files, skipped)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::Language;
    use crate::test_support::write_file;

    fn project(root: &Path) -> Project {
        Project {
            root: root.to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: ["agent.ts", "memory.ts"]
                .into_iter()
                .filter(|file| root.join(file).exists())
                .map(PathBuf::from)
                .collect(),
        }
    }

    #[test]
    fn requires_the_agent_memory_layer() {
        let dir = tempfile::tempdir().unwrap();
        write_file(
            dir.path(),
            "agent.ts",
            "export const agent = defineDeepAgent({ name: \"support\" });\n",
        );
        let none = require_agent_memory(&project(dir.path())).unwrap_err();
        assert!(none.to_string().contains("Add memory.ts"), "{none:#}");

        write_file(
            dir.path(),
            "memory.ts",
            "export const memory = defineMemory({ agent: memoryLayer() });\n",
        );
        require_agent_memory(&project(dir.path())).unwrap();
    }

    #[test]
    fn writes_only_to_a_repo_deploy_created() {
        require_deployed_repo(RepoOwnership::ManagedDeepAgent, "support").unwrap();
        let missing = require_deployed_repo(RepoOwnership::Missing, "support").unwrap_err();
        assert!(
            missing.to_string().contains("Run `mda deploy` first"),
            "{missing:#}"
        );
        let foreign = require_deployed_repo(RepoOwnership::Foreign, "support").unwrap_err();
        assert!(
            foreign.to_string().contains("not created by"),
            "{foreign:#}"
        );
    }

    #[test]
    fn plan_uploads_new_files_under_agent_memory_and_skips_existing_ones() {
        let local = BTreeMap::from([
            ("wiki/INDEX.md".to_string(), "index".to_string()),
            ("pricing.md".to_string(), "local".to_string()),
        ]);
        let remote = BTreeMap::from([(
            "memories/agent/pricing.md".to_string(),
            json!({"type": "file", "content": "learned since deploy"}),
        )]);

        let (files, skipped) = plan_upload(local, &remote);

        assert_eq!(
            files,
            BTreeMap::from([(
                "memories/agent/wiki/INDEX.md".to_string(),
                json!({"type": "file", "content": "index"}),
            )])
        );
        assert_eq!(skipped, ["memories/agent/pricing.md"]);
    }

    #[test]
    fn reads_nested_text_files_and_skips_junk() {
        let dir = tempfile::tempdir().unwrap();
        write_file(dir.path(), "wiki/INDEX.md", "index");
        write_file(dir.path(), "skills/README.md", "notes");
        write_file(dir.path(), ".DS_Store", "junk");
        write_file(dir.path(), "__pycache__/x.pyc", "junk");

        let files = read_folder(dir.path()).unwrap();

        assert_eq!(
            files,
            BTreeMap::from([
                ("skills/README.md".to_string(), "notes".to_string()),
                ("wiki/INDEX.md".to_string(), "index".to_string()),
            ])
        );
    }

    #[test]
    fn refuses_missing_empty_and_binary_folders() {
        let dir = tempfile::tempdir().unwrap();
        assert!(read_folder(&dir.path().join("missing")).is_err());

        let empty = read_folder(dir.path()).unwrap_err();
        assert!(
            empty.to_string().contains("no files to upload"),
            "{empty:#}"
        );

        fs::write(dir.path().join("image.png"), [0xff, 0xfe, 0x00]).unwrap();
        let binary = read_folder(dir.path()).unwrap_err();
        assert!(binary.to_string().contains("UTF-8"), "{binary:#}");
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        write_file(outside.path(), "secret.md", "secret");
        std::os::unix::fs::symlink(outside.path().join("secret.md"), dir.path().join("a.md"))
            .unwrap();

        let err = read_folder(dir.path()).unwrap_err();
        assert!(err.to_string().contains("symlink"), "{err:#}");
    }
}
