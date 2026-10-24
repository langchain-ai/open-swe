//! Deploy-time Context Hub reconciliation.
//!
//! MDA deploys code, while Context Hub owns deployed context. This module syncs
//! local developer-authored context (`instructions.md`, `skills/**`) into a
//! Context Hub repo and deliberately preserves agent-authored memory under
//! `memories/**`.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use walkdir::WalkDir;

use crate::context_layout::{
    project_context_root, resolve_agent_memory_seed, AgentMemorySeed, AGENT_MEMORY_FILE,
    FLAT_MEMORY_FILE, INSTRUCTIONS_FILE, MEMORIES_DIR, SKILLS_DIR,
};
use crate::langsmith::{self, ContextHubClient};
use crate::path_util::to_unix_path;
use crate::project::{self, Project};

const DEPLOY_MANIFEST_FILE: &str = ".mda/deploy-manifest.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ContextSection {
    Instructions,
    Skills,
}

impl ContextSection {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Instructions => "instructions.md",
            Self::Skills => "skills/",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ConflictResolution {
    Overwrite,
    KeepHub,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize)]
struct DeployManifest {
    instructions: Option<String>,
    skills: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
struct DeployManifestFile {
    raw_content: String,
    parsed: DeployManifest,
}

#[derive(Debug, PartialEq, Eq)]
struct HubContextState {
    manifest: Option<DeployManifestFile>,
    deploy_owned_hashes: DeployManifest,
}

impl HubContextState {
    fn read(files: &BTreeMap<String, Value>) -> Result<Self> {
        Ok(Self {
            manifest: read_deploy_manifest(files)?,
            deploy_owned_hashes: hash_hub_context(files)?,
        })
    }

    fn conflicts_with_local(&self, local_hashes: &DeployManifest) -> Vec<ContextSection> {
        let Some(baseline) = &self.manifest else {
            return Vec::new();
        };
        detect_conflicts(&baseline.parsed, &self.deploy_owned_hashes, local_hashes)
    }
}

#[derive(Debug)]
pub(crate) struct SyncPlan {
    repo: String,
    local: LocalContext,
    parent_commit: Option<String>,
    existing: BTreeMap<String, Value>,
    hub_state: HubContextState,
    local_hashes: DeployManifest,
    conflicts: Vec<ContextSection>,
}

impl SyncPlan {
    pub(crate) fn conflicts(&self) -> &[ContextSection] {
        &self.conflicts
    }
}

/// Result of syncing local context into Context Hub.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncSummary {
    pub repo: String,
    pub has_skills: bool,
    pub changed: bool,
    pub had_local_memories: bool,
    /// How many Hub memory files exist but will not be mounted, because the project
    /// declares no memory. Zero when memory is enabled.
    ///
    /// A count rather than the paths: an agent chooses its own filenames, so a path
    /// like `memories/agent/archive/2026-08-05-jane-doe-leave.md` *is* memory
    /// content. Keeping paths out of the struct means no later caller can print them
    /// by accident.
    pub orphaned_hub_memory_count: usize,
}

/// Inspect local and Hub context without changing directory contents.
///
/// `memory_enabled` comes from the project's memory declaration
/// ([`crate::compile::resolve_memory`]) — the single answer build, deploy, and dev
/// all read, so the seeded tree always matches what the runtime mounts.
pub(crate) fn prepare_context_sync(
    client: &ContextHubClient,
    project: &Project,
    repo: &str,
    memory_enabled: bool,
) -> Result<SyncPlan> {
    client.ensure_repo(repo)?;

    let local = collect_local_context(project, memory_enabled)?;
    let snapshot = client.get_directory(repo)?;
    let local_hashes = hash_local_context(&local);
    let hub_state = HubContextState::read(&snapshot.files)?;
    let conflicts = hub_state.conflicts_with_local(&local_hashes);

    Ok(SyncPlan {
        repo: repo.to_string(),
        local,
        parent_commit: snapshot.commit_hash,
        existing: snapshot.files,
        hub_state,
        local_hashes,
        conflicts,
    })
}

/// Apply a prepared context sync while preserving all Hub-owned memory files.
pub(crate) fn apply_context_sync(
    client: &ContextHubClient,
    plan: SyncPlan,
    resolution: ConflictResolution,
) -> Result<SyncSummary> {
    let preserve_instructions = resolution == ConflictResolution::KeepHub
        && plan.conflicts.contains(&ContextSection::Instructions);
    let preserve_skills = resolution == ConflictResolution::KeepHub
        && plan.conflicts.contains(&ContextSection::Skills);
    let files = build_commit_files(
        &plan.existing,
        &plan.local,
        preserve_instructions,
        preserve_skills,
        plan.hub_state
            .manifest
            .as_ref()
            .map(|manifest| &manifest.parsed),
        &plan.local_hashes,
    )?;

    let changed = !files.is_empty();
    if changed {
        client.create_directory_commit(&plan.repo, plan.parent_commit.as_deref(), files)?;
    } else {
        let current = client.get_directory(&plan.repo)?;
        if HubContextState::read(&current.files)? != plan.hub_state {
            bail!(
                "Context Hub repo '{}' changed during deploy; rerun `mda deploy` to retry",
                plan.repo
            );
        }
    }

    Ok(SyncSummary {
        repo: plan.repo,
        has_skills: if preserve_skills {
            plan.existing.keys().any(|path| path.starts_with("skills/"))
        } else {
            plan.local.has_skills
        },
        changed,
        had_local_memories: plan.local.has_local_memories,
        orphaned_hub_memory_count: if plan.local.memory_enabled {
            0
        } else {
            count_hub_memory_files(&plan.existing)
        },
    })
}

/// Tag `repo` with the agent bound to `deployment_id`, so the repo shows in
/// that agent's Context Hub list. Call after the deployment exists. Failures
/// print a warning and do not stop the deploy.
pub(crate) fn link_context_repo_to_agent(
    client: &langsmith::Client,
    context_client: &ContextHubClient,
    deployment_id: &str,
    repo: &str,
) -> Result<()> {
    let result = client
        .deployment_agent_tag_value_id(deployment_id)
        .and_then(|tag| match tag {
            Some(tag) => context_client.tag_repo(repo, &tag),
            None => Ok(()),
        });
    if let Err(err) = result {
        cliclack::log::warning(format!(
            "Could not link Context Hub repo '{repo}' to its LangSmith agent: {err:#}. Deploy will continue."
        ))?;
    }
    Ok(())
}

/// How many `memories/**` files the Hub snapshot already has.
///
/// Deliberately returns a count, not the paths — see
/// [`SyncSummary::orphaned_hub_memory_count`].
fn count_hub_memory_files(files: &BTreeMap<String, Value>) -> usize {
    let prefix = format!("{MEMORIES_DIR}/");
    files
        .keys()
        .filter(|path| path.starts_with(&prefix))
        .count()
}

#[derive(Debug)]
struct LocalContext {
    files: BTreeMap<String, String>,
    has_skills: bool,
    has_local_memories: bool,
    memory_enabled: bool,
}

impl Default for LocalContext {
    fn default() -> Self {
        Self {
            files: BTreeMap::new(),
            has_skills: false,
            has_local_memories: false,
            memory_enabled: true,
        }
    }
}

fn collect_local_context(project: &Project, memory_enabled: bool) -> Result<LocalContext> {
    let root = project_context_root(project);
    let mut files = BTreeMap::new();

    let instructions = root.join(INSTRUCTIONS_FILE);
    if instructions.is_file() {
        files.insert(
            INSTRUCTIONS_FILE.to_string(),
            read_utf8_context_file(&instructions)?,
        );
    }

    let skills = root.join(SKILLS_DIR);
    if skills.is_dir() {
        collect_skill_files(&root, &skills, &mut files)?;
    }

    let has_skills = files.keys().any(|path| path.starts_with("skills/"));
    Ok(LocalContext {
        files,
        has_skills,
        has_local_memories: root.join(MEMORIES_DIR).is_dir(),
        memory_enabled,
    })
}

fn collect_skill_files(
    context_root: &Path,
    skills_dir: &Path,
    files: &mut BTreeMap<String, String>,
) -> Result<()> {
    for entry in WalkDir::new(skills_dir)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !project::is_junk_dir(entry.path()))
    {
        let entry = entry?;
        if !entry.file_type().is_file() || project::is_junk_file(entry.path()) {
            continue;
        }
        let rel = entry.path().strip_prefix(context_root)?;
        let path = to_unix_path(rel);
        files.insert(path, read_utf8_context_file(entry.path())?);
    }
    Ok(())
}

fn read_utf8_context_file(path: &Path) -> Result<String> {
    fs::read_to_string(path).with_context(|| {
        format!(
            "Context Hub only accepts UTF-8 text files; failed to read {}",
            path.display()
        )
    })
}

fn read_deploy_manifest(existing: &BTreeMap<String, Value>) -> Result<Option<DeployManifestFile>> {
    let Some(entry) = existing.get(DEPLOY_MANIFEST_FILE) else {
        return Ok(None);
    };
    let Some(content) = entry_content(entry) else {
        bail!("Context Hub deploy manifest is not a text file");
    };
    let parsed = serde_json::from_str(content).context("Context Hub deploy manifest is invalid")?;
    Ok(Some(DeployManifestFile {
        raw_content: content.to_string(),
        parsed,
    }))
}

fn hash_local_context(local: &LocalContext) -> DeployManifest {
    DeployManifest {
        instructions: local
            .files
            .get(INSTRUCTIONS_FILE)
            .map(|content| hash_files([(INSTRUCTIONS_FILE, content.as_str())])),
        skills: hash_files_if_any(
            local
                .files
                .iter()
                .filter(|(path, _)| path.starts_with("skills/"))
                .map(|(path, content)| (path.as_str(), content.as_str())),
        ),
    }
}

fn hash_hub_context(existing: &BTreeMap<String, Value>) -> Result<DeployManifest> {
    let instructions = existing
        .get(INSTRUCTIONS_FILE)
        .map(|entry| {
            entry_content(entry)
                .map(|content| hash_files([(INSTRUCTIONS_FILE, content)]))
                .context("Context Hub instructions.md is not a text file")
        })
        .transpose()?;
    let skills = existing
        .iter()
        .filter(|(path, _)| path.starts_with("skills/"))
        .map(|(path, entry)| {
            entry_content(entry)
                .map(|content| (path.as_str(), content))
                .with_context(|| format!("Context Hub skill '{path}' is not a text file"))
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(DeployManifest {
        instructions,
        skills: hash_files_if_any(skills),
    })
}

fn hash_files_if_any<'a>(files: impl IntoIterator<Item = (&'a str, &'a str)>) -> Option<String> {
    let files = files.into_iter().collect::<Vec<_>>();
    (!files.is_empty()).then(|| hash_files(files))
}

fn hash_files<'a>(files: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    const OFFSET_BASIS: u128 = 144_066_263_297_769_815_596_495_629_667_062_367_629;
    const PRIME: u128 = 309_485_009_821_345_068_724_781_371;

    let mut files = files.into_iter().collect::<Vec<_>>();
    files.sort_unstable_by_key(|(path, _)| *path);
    let mut hash = OFFSET_BASIS;
    for (path, content) in files {
        for bytes in [path.as_bytes(), content.as_bytes()] {
            for byte in (bytes.len() as u64).to_be_bytes() {
                hash ^= u128::from(byte);
                hash = hash.wrapping_mul(PRIME);
            }
            for byte in bytes {
                hash ^= u128::from(*byte);
                hash = hash.wrapping_mul(PRIME);
            }
        }
    }
    format!("fnv1a128:{hash:032x}")
}

fn detect_conflicts(
    baseline: &DeployManifest,
    hub: &DeployManifest,
    local: &DeployManifest,
) -> Vec<ContextSection> {
    let mut conflicts = Vec::new();
    if hub.instructions != baseline.instructions && hub.instructions != local.instructions {
        conflicts.push(ContextSection::Instructions);
    }
    if hub.skills != baseline.skills && hub.skills != local.skills {
        conflicts.push(ContextSection::Skills);
    }
    conflicts
}

fn build_commit_files(
    existing: &BTreeMap<String, Value>,
    local: &LocalContext,
    preserve_instructions: bool,
    preserve_skills: bool,
    baseline: Option<&DeployManifest>,
    local_hashes: &DeployManifest,
) -> Result<BTreeMap<String, Value>> {
    let mut files = BTreeMap::new();

    if !preserve_instructions {
        upsert_or_delete_instructions(existing, local, &mut files);
    }
    if !preserve_skills {
        upsert_local_files(existing, local, &mut files);
        delete_stale_skills(existing, local, &mut files);
    }
    if local.memory_enabled {
        ensure_memory_seed(existing, &mut files);
    }

    let manifest = DeployManifest {
        instructions: if preserve_instructions {
            baseline.and_then(|manifest| manifest.instructions.clone())
        } else {
            local_hashes.instructions.clone()
        },
        skills: if preserve_skills {
            baseline.and_then(|manifest| manifest.skills.clone())
        } else {
            local_hashes.skills.clone()
        },
    };
    let manifest = format!("{}\n", serde_json::to_string(&manifest)?);
    upsert_file_if_changed(existing, &mut files, DEPLOY_MANIFEST_FILE, &manifest);

    Ok(files)
}

fn upsert_or_delete_instructions(
    existing: &BTreeMap<String, Value>,
    local: &LocalContext,
    files: &mut BTreeMap<String, Value>,
) {
    match local.files.get(INSTRUCTIONS_FILE) {
        Some(content) => upsert_file_if_changed(existing, files, INSTRUCTIONS_FILE, content),
        None if existing.contains_key(INSTRUCTIONS_FILE) => {
            files.insert(INSTRUCTIONS_FILE.to_string(), Value::Null);
        }
        None => {}
    }
}

fn upsert_local_files(
    existing: &BTreeMap<String, Value>,
    local: &LocalContext,
    files: &mut BTreeMap<String, Value>,
) {
    for (path, content) in &local.files {
        if path == INSTRUCTIONS_FILE {
            continue;
        }
        upsert_file_if_changed(existing, files, path, content);
    }
}

fn delete_stale_skills(
    existing: &BTreeMap<String, Value>,
    local: &LocalContext,
    files: &mut BTreeMap<String, Value>,
) {
    for path in existing.keys() {
        if path.starts_with("skills/") && !local.files.contains_key(path) {
            files.insert(path.clone(), Value::Null);
        }
    }
}

fn ensure_memory_seed(existing: &BTreeMap<String, Value>, files: &mut BTreeMap<String, Value>) {
    // Prefer the agent-scoped slice. When only a legacy flat `memories/AGENTS.md`
    // exists, migrate its contents into `memories/agent/AGENTS.md` so identity
    // scope `memory: "agent"` (and the local mock) still remount hot memory. An
    // existing agent-scoped file is never rewritten.
    let agent = existing.get(AGENT_MEMORY_FILE).and_then(entry_content);
    let flat = existing.get(FLAT_MEMORY_FILE).and_then(entry_content);
    if let AgentMemorySeed::Write(content) = resolve_agent_memory_seed(agent, flat) {
        files.insert(AGENT_MEMORY_FILE.to_string(), file_entry(&content));
    }
}

fn upsert_file_if_changed(
    existing: &BTreeMap<String, Value>,
    files: &mut BTreeMap<String, Value>,
    path: &str,
    content: &str,
) {
    if existing.get(path).and_then(entry_content) != Some(content) {
        files.insert(path.to_string(), file_entry(content));
    }
}

fn file_entry(content: &str) -> Value {
    json!({ "type": "file", "content": content })
}

fn entry_content(entry: &Value) -> Option<&str> {
    entry.get("content").and_then(Value::as_str)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::{Language, Project};
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::path::PathBuf;
    use std::sync::{Arc, Mutex};
    use std::thread::{self, JoinHandle};

    use crate::context_layout::{DEFAULT_MEMORY_CONTENT, FLAT_MEMORY_FILE};
    use crate::test_support::write_file;

    fn project(root: &Path, agent_file: &str) -> Project {
        project_with_language(root, agent_file, Language::TypeScript)
    }

    fn project_with_language(root: &Path, agent_file: &str, language: Language) -> Project {
        Project {
            root: root.to_path_buf(),
            language,
            agent_file: PathBuf::from(agent_file),
            files: vec![PathBuf::from(agent_file)],
        }
    }

    fn build_all_commit_files(
        existing: &BTreeMap<String, Value>,
        local: &LocalContext,
    ) -> BTreeMap<String, Value> {
        let baseline = read_deploy_manifest(existing).unwrap();
        let local_hashes = hash_local_context(local);
        build_commit_files(
            existing,
            local,
            false,
            false,
            baseline.as_ref().map(|manifest| &manifest.parsed),
            &local_hashes,
        )
        .unwrap()
    }

    fn no_op_sync_plan(
        existing: BTreeMap<String, Value>,
        local_hashes: DeployManifest,
    ) -> SyncPlan {
        let hub_state = HubContextState::read(&existing).unwrap();
        SyncPlan {
            repo: "my-agent".to_string(),
            local: LocalContext {
                memory_enabled: false,
                ..LocalContext::default()
            },
            parent_commit: Some("old".to_string()),
            existing,
            hub_state,
            local_hashes,
            conflicts: Vec::new(),
        }
    }

    struct TestResponse {
        status: &'static str,
        body: &'static str,
    }

    fn serve(responses: Vec<TestResponse>) -> (String, Arc<Mutex<Vec<String>>>, JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let captured = Arc::clone(&requests);

        let handle = thread::spawn(move || {
            for response in responses {
                let (mut stream, _) = listener.accept().unwrap();
                let request = read_http_request(&mut stream);
                captured.lock().unwrap().push(request);
                write!(
                    stream,
                    "HTTP/1.1 {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    response.status,
                    response.body.len(),
                    response.body
                )
                .unwrap();
            }
        });

        (base_url, requests, handle)
    }

    fn read_http_request(stream: &mut std::net::TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            let Some(header_end) = find_headers_end(&data) else {
                continue;
            };
            let headers = String::from_utf8_lossy(&data[..header_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .map(str::to_string)
                })
                .and_then(|value| value.trim().parse::<usize>().ok())
                .unwrap_or(0);
            if data.len() >= header_end + 4 + content_length {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    fn find_headers_end(data: &[u8]) -> Option<usize> {
        data.windows(4).position(|window| window == b"\r\n\r\n")
    }

    /// Run the link against a server that returns one tagged agent and one
    /// repo, then answers the tagging request with `tagging_status`.
    fn link_repo(tagging_status: &'static str) -> Vec<String> {
        let (base, requests, server) = serve(vec![
            TestResponse {
                status: "200 OK",
                body: r#"{"items":[{"id":"actual-agent","tag_value_id":"agent-tag"}],"next_cursor":null}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"repo":{"id":"repo-id"}}"#,
            },
            TestResponse {
                status: tagging_status,
                body: "{}",
            },
        ]);
        let client =
            langsmith::Client::new(&base, &base, langsmith::ApiKey::new("key"), None).unwrap();
        let context_client = ContextHubClient::for_test(&base);
        link_context_repo_to_agent(&client, &context_client, "deployment-id", "repo-handle")
            .unwrap();
        server.join().unwrap();
        let requests = requests.lock().unwrap().clone();
        requests
    }

    #[test]
    fn context_agent_link_tags_the_repo_with_its_agent() {
        let requests = link_repo("200 OK");
        assert!(requests[1].starts_with("GET /api/v1/repos/-/repo-handle "));
        assert!(requests[2].starts_with("POST /api/v1/workspaces/current/taggings "));
        let tagging: Value =
            serde_json::from_str(requests[2].split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(
            tagging,
            json!({"resource_id": "repo-id", "resource_type": "agent", "tag_value_id": "agent-tag"})
        );
    }

    #[test]
    fn context_agent_link_does_not_fail_deploy_when_tagging_is_denied() {
        assert_eq!(link_repo("403 Forbidden").len(), 3);
    }

    #[test]
    fn collect_local_context_reads_instructions_and_skills_next_to_agent() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "src/agent.ts", "export const agent = {};\n");
        write_file(root.path(), "src/instructions.md", "Be direct.\n");
        write_file(root.path(), "src/skills/research/SKILL.md", "Research.\n");
        write_file(root.path(), "instructions.md", "ignore root\n");

        let local = collect_local_context(&project(root.path(), "src/agent.ts"), true).unwrap();

        assert_eq!(
            local.files.get("instructions.md").map(String::as_str),
            Some("Be direct.\n")
        );
        assert_eq!(
            local
                .files
                .get("skills/research/SKILL.md")
                .map(String::as_str),
            Some("Research.\n")
        );
        assert!(local.has_skills);
    }

    #[test]
    fn collect_local_context_marks_local_memories_without_uploading_them() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(root.path(), "memories/agent/AGENTS.md", "local memory\n");

        let local = collect_local_context(&project(root.path(), "agent.ts"), true).unwrap();

        assert!(local.has_local_memories);
        assert!(!local.files.contains_key(AGENT_MEMORY_FILE));
        assert!(!local.files.contains_key(FLAT_MEMORY_FILE));
    }

    #[test]
    fn collect_local_context_skips_the_memory_seed_when_memory_is_off() {
        // A project that declares no memory must not have a hot file seeded for
        // it: deploy and the runtime read the same decision.
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");

        let local = collect_local_context(&project(root.path(), "agent.ts"), false).unwrap();

        assert!(!local.memory_enabled);
        assert!(!local.files.contains_key(AGENT_MEMORY_FILE));
    }

    #[test]
    fn count_hub_memory_files_counts_memory_files_only() {
        let mut files = BTreeMap::new();
        files.insert("instructions.md".to_string(), file_entry("prompt\n"));
        files.insert(
            "memories/agent/AGENTS.md".to_string(),
            file_entry("learned\n"),
        );
        files.insert("memories/agent/notes.md".to_string(), file_entry("more\n"));
        files.insert("skills/x/SKILL.md".to_string(), file_entry("skill\n"));

        // A count, never the paths: agent-chosen filenames are memory content.
        assert_eq!(count_hub_memory_files(&files), 2);
    }

    #[test]
    fn collect_local_context_errors_on_non_utf8_skills() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        let skill = root.path().join("skills/binary/SKILL.md");
        fs::create_dir_all(skill.parent().unwrap()).unwrap();
        fs::write(&skill, [0xff, 0xfe, 0xfd]).unwrap();

        let err = collect_local_context(&project(root.path(), "agent.ts"), true).unwrap_err();

        assert!(
            err.to_string().contains("Context Hub only accepts UTF-8"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn collect_local_context_skips_os_junk_in_skills() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(root.path(), "skills/research/SKILL.md", "Research skill.\n");
        let junk = root.path().join("skills/research/.DS_Store");
        fs::write(&junk, [0x00, 0x01, 0x02, 0xff]).unwrap();

        let local = collect_local_context(&project(root.path(), "agent.ts"), true).unwrap();

        assert_eq!(
            local
                .files
                .get("skills/research/SKILL.md")
                .map(String::as_str),
            Some("Research skill.\n")
        );
        assert!(!local.files.keys().any(|path| path.contains(".DS_Store")));
    }

    #[test]
    fn collect_local_context_keeps_skill_subtrees_named_like_project_dirs() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(
            root.path(),
            "skills/release/build/steps.md",
            "Build steps.\n",
        );
        write_file(root.path(), "skills/release/dist/notes.md", "Dist notes.\n");
        write_file(root.path(), "skills/release/evals/rubric.md", "Rubric.\n");
        write_file(root.path(), "skills/release/memories/hints.md", "Hints.\n");

        let local = collect_local_context(&project(root.path(), "agent.ts"), true).unwrap();

        assert_eq!(
            local
                .files
                .get("skills/release/build/steps.md")
                .map(String::as_str),
            Some("Build steps.\n")
        );
        assert_eq!(
            local
                .files
                .get("skills/release/dist/notes.md")
                .map(String::as_str),
            Some("Dist notes.\n")
        );
        assert_eq!(
            local
                .files
                .get("skills/release/evals/rubric.md")
                .map(String::as_str),
            Some("Rubric.\n")
        );
        assert_eq!(
            local
                .files
                .get("skills/release/memories/hints.md")
                .map(String::as_str),
            Some("Hints.\n")
        );
    }

    #[test]
    fn build_commit_files_updates_local_context_and_preserves_memories() {
        let mut existing = BTreeMap::new();
        existing.insert("instructions.md".to_string(), file_entry("old\n"));
        existing.insert("skills/stale/SKILL.md".to_string(), file_entry("stale\n"));
        existing.insert(AGENT_MEMORY_FILE.to_string(), file_entry("remember\n"));
        existing.insert("memories/notes.md".to_string(), file_entry("keep\n"));
        let mut local = LocalContext::default();
        local
            .files
            .insert("instructions.md".to_string(), "new\n".to_string());
        local
            .files
            .insert("skills/new/SKILL.md".to_string(), "new skill\n".to_string());

        let files = build_all_commit_files(&existing, &local);

        assert_eq!(files.get("instructions.md"), Some(&file_entry("new\n")));
        assert_eq!(
            files.get("skills/new/SKILL.md"),
            Some(&file_entry("new skill\n"))
        );
        assert_eq!(files.get("skills/stale/SKILL.md"), Some(&Value::Null));
        assert!(!files.contains_key(AGENT_MEMORY_FILE));
        assert!(!files.contains_key("memories/notes.md"));
    }

    #[test]
    fn build_commit_files_creates_an_empty_memory_file_when_absent() {
        let existing = BTreeMap::new();
        let local = LocalContext::default();

        let files = build_all_commit_files(&existing, &local);

        // The file exists so the Hub shows where memory lives and `edit_file` works
        // on the first save, but it holds no platform prose — the instructions are
        // injected by the runtime.
        assert_eq!(files.get(AGENT_MEMORY_FILE), Some(&file_entry("")));
        assert!(DEFAULT_MEMORY_CONTENT.is_empty());
    }

    #[test]
    fn build_commit_files_migrates_flat_memory_into_agent_slice() {
        let mut existing = BTreeMap::new();
        existing.insert(FLAT_MEMORY_FILE.to_string(), file_entry("legacy\n"));
        let local = LocalContext::default();

        let files = build_all_commit_files(&existing, &local);

        assert_eq!(files.get(AGENT_MEMORY_FILE), Some(&file_entry("legacy\n")));
        // Copied, not moved: nothing here deletes memory.
        assert!(!files.contains_key(FLAT_MEMORY_FILE));
    }

    #[test]
    fn build_commit_files_leaves_agent_written_memory_untouched() {
        // The steady state after this release: no managed block, so a redeploy must
        // not rewrite durable memory at all.
        let mut existing = BTreeMap::new();
        existing.insert(
            AGENT_MEMORY_FILE.to_string(),
            file_entry("- favorite color: blue\n"),
        );
        let local = LocalContext::default();

        let files = build_all_commit_files(&existing, &local);

        assert!(!files.contains_key(AGENT_MEMORY_FILE));
    }

    #[test]
    fn build_commit_files_skips_memory_file_when_memory_is_disabled() {
        let existing = BTreeMap::new();
        let local = LocalContext {
            memory_enabled: false,
            ..LocalContext::default()
        };

        let files = build_all_commit_files(&existing, &local);

        assert!(!files.contains_key(AGENT_MEMORY_FILE));
    }

    #[test]
    fn build_commit_files_deletes_remote_instructions_when_local_is_absent() {
        let mut existing = BTreeMap::new();
        existing.insert("instructions.md".to_string(), file_entry("old\n"));
        existing.insert(AGENT_MEMORY_FILE.to_string(), file_entry(""));
        let local = LocalContext::default();

        let files = build_all_commit_files(&existing, &local);

        assert_eq!(files.get("instructions.md"), Some(&Value::Null));
    }

    #[test]
    fn build_commit_files_is_empty_when_context_is_current() {
        let mut existing = BTreeMap::new();
        existing.insert("instructions.md".to_string(), file_entry("same\n"));
        existing.insert(
            "skills/research/SKILL.md".to_string(),
            file_entry("same skill\n"),
        );
        existing.insert(AGENT_MEMORY_FILE.to_string(), file_entry(""));

        let mut local = LocalContext::default();
        local
            .files
            .insert("instructions.md".to_string(), "same\n".to_string());
        local.files.insert(
            "skills/research/SKILL.md".to_string(),
            "same skill\n".to_string(),
        );
        let manifest = format!(
            "{}\n",
            serde_json::to_string(&hash_local_context(&local)).unwrap()
        );
        existing.insert(DEPLOY_MANIFEST_FILE.to_string(), file_entry(&manifest));

        assert!(build_all_commit_files(&existing, &local).is_empty());
    }

    #[test]
    fn detects_hub_edits_but_not_local_only_changes() {
        let old = Some(hash_files([(INSTRUCTIONS_FILE, "old\n")]));
        let edited = Some(hash_files([(INSTRUCTIONS_FILE, "edited in Hub\n")]));
        let local = Some(hash_files([(INSTRUCTIONS_FILE, "new locally\n")]));
        let baseline = DeployManifest {
            instructions: old.clone(),
            skills: None,
        };

        assert_eq!(
            detect_conflicts(
                &baseline,
                &DeployManifest {
                    instructions: edited,
                    skills: None,
                },
                &DeployManifest {
                    instructions: local.clone(),
                    skills: None,
                },
            ),
            vec![ContextSection::Instructions]
        );
        assert!(detect_conflicts(
            &baseline,
            &DeployManifest {
                instructions: old,
                skills: None,
            },
            &DeployManifest {
                instructions: local,
                skills: None,
            },
        )
        .is_empty());
    }

    #[test]
    fn sync_context_bootstraps_repo_without_manifest() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(root.path(), "instructions.md", "new\n");
        let (base_url, requests, handle) = serve(vec![
            TestResponse {
                status: "200 OK",
                body: r#"{"repo":{"repo_handle":"my-agent"}}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"commit_hash":"old","files":{"instructions.md":{"type":"file","content":"old\n"}}}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"commit":{"commit_hash":"new"}}"#,
            },
        ]);
        let client = ContextHubClient::for_test(&base_url);

        let plan = prepare_context_sync(
            &client,
            &project(root.path(), "agent.ts"),
            "my-agent",
            false,
        )
        .unwrap();
        assert!(plan.conflicts().is_empty());
        apply_context_sync(&client, plan, ConflictResolution::Overwrite).unwrap();

        handle.join().unwrap();
        let requests = requests.lock().unwrap();
        let (_, body) = requests[2].split_once("\r\n\r\n").unwrap();
        let body: Value = serde_json::from_str(body).unwrap();
        let manifest = DeployManifest {
            instructions: Some(hash_files([(INSTRUCTIONS_FILE, "new\n")])),
            skills: None,
        };
        let manifest = format!("{}\n", serde_json::to_string(&manifest).unwrap());
        assert_eq!(
            body,
            json!({
                "files": {
                    DEPLOY_MANIFEST_FILE: file_entry(&manifest),
                    INSTRUCTIONS_FILE: file_entry("new\n"),
                },
                "parent_commit": "old",
            })
        );
    }

    #[test]
    fn keeping_hub_skills_does_not_update_or_delete_them() {
        let mut existing = BTreeMap::new();
        existing.insert(
            "skills/hub/SKILL.md".to_string(),
            file_entry("edited in Hub\n"),
        );
        existing.insert(AGENT_MEMORY_FILE.to_string(), file_entry("remember\n"));
        let baseline = DeployManifest {
            instructions: None,
            skills: Some(hash_files([("skills/hub/SKILL.md", "old\n")])),
        };
        let mut local = LocalContext::default();
        local
            .files
            .insert("skills/local/SKILL.md".to_string(), "local\n".to_string());
        let local_hashes = hash_local_context(&local);

        let files = build_commit_files(
            &existing,
            &local,
            false,
            true,
            Some(&baseline),
            &local_hashes,
        )
        .unwrap();

        assert!(!files.contains_key("skills/hub/SKILL.md"));
        assert!(!files.contains_key("skills/local/SKILL.md"));
        assert!(!files.contains_key(AGENT_MEMORY_FILE));
    }

    #[test]
    fn no_op_sync_ignores_memory_only_changes() {
        let manifest = DeployManifest::default();
        let manifest_content = format!("{}\n", serde_json::to_string(&manifest).unwrap());
        let mut existing = BTreeMap::new();
        existing.insert(
            DEPLOY_MANIFEST_FILE.to_string(),
            file_entry(&manifest_content),
        );
        existing.insert(AGENT_MEMORY_FILE.to_string(), file_entry("old memory\n"));
        let plan = no_op_sync_plan(existing, manifest);
        let (base_url, _requests, handle) = serve(vec![TestResponse {
            status: "200 OK",
            body: r#"{"commit_hash":"new","files":{".mda/deploy-manifest.json":{"type":"file","content":"{\"instructions\":null,\"skills\":null}\n"},"memories/agent/AGENTS.md":{"type":"file","content":"new memory\n"}}}"#,
        }]);
        let client = ContextHubClient::for_test(&base_url);

        let summary = apply_context_sync(&client, plan, ConflictResolution::KeepHub).unwrap();

        handle.join().unwrap();
        assert!(!summary.changed);
    }

    #[test]
    fn no_op_sync_rejects_deploy_owned_changes() {
        let manifest = DeployManifest::default();
        let manifest_content = format!("{}\n", serde_json::to_string(&manifest).unwrap());
        let mut existing = BTreeMap::new();
        existing.insert(
            DEPLOY_MANIFEST_FILE.to_string(),
            file_entry(&manifest_content),
        );
        let plan = no_op_sync_plan(existing, manifest);
        let (base_url, _requests, handle) = serve(vec![TestResponse {
            status: "200 OK",
            body: r#"{"commit_hash":"new","files":{".mda/deploy-manifest.json":{"type":"file","content":"{\"instructions\":null,\"skills\":null}\n"},"instructions.md":{"type":"file","content":"edited in Hub\n"}}}"#,
        }]);
        let client = ContextHubClient::for_test(&base_url);

        let err = apply_context_sync(&client, plan, ConflictResolution::KeepHub).unwrap_err();

        handle.join().unwrap();
        assert!(err.to_string().contains("changed during deploy"));
    }

    #[test]
    fn no_op_sync_rejects_manifest_text_changes() {
        let manifest = DeployManifest::default();
        let manifest_content = format!("{}\n", serde_json::to_string(&manifest).unwrap());
        let mut existing = BTreeMap::new();
        existing.insert(
            DEPLOY_MANIFEST_FILE.to_string(),
            file_entry(&manifest_content),
        );
        let plan = no_op_sync_plan(existing, manifest);
        let (base_url, _requests, handle) = serve(vec![TestResponse {
            status: "200 OK",
            body: r#"{"commit_hash":"new","files":{".mda/deploy-manifest.json":{"type":"file","content":"{\"instructions\":null,\"skills\":null,\"note\":\"edited\"}\n"}}}"#,
        }]);
        let client = ContextHubClient::for_test(&base_url);

        let err = apply_context_sync(&client, plan, ConflictResolution::KeepHub).unwrap_err();

        handle.join().unwrap();
        assert!(err.to_string().contains("changed during deploy"));
    }

    #[test]
    fn sync_context_creates_repo_when_get_repo_returns_404() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(root.path(), "instructions.md", "Use local.\n");
        let (base_url, requests, handle) = serve(vec![
            TestResponse {
                status: "404 Not Found",
                body: r#"{"detail":"missing"}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"repo":{"repo_handle":"my-agent"}}"#,
            },
            TestResponse {
                status: "404 Not Found",
                body: r#"{"detail":"missing"}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"commit":{"commit_hash":"new-commit"}}"#,
            },
        ]);
        let client = ContextHubClient::new(
            &base_url,
            &format!("{base_url}/api-host"),
            langsmith::ApiKey::new("key"),
            None,
        )
        .unwrap();

        let plan =
            prepare_context_sync(&client, &project(root.path(), "agent.ts"), "my-agent", true)
                .unwrap();
        let summary = apply_context_sync(&client, plan, ConflictResolution::Overwrite).unwrap();

        handle.join().unwrap();
        let requests = requests.lock().unwrap();
        assert!(requests[0].starts_with("GET /api/v1/repos/-/my-agent "));
        assert!(requests[1].starts_with("POST /api/v1/repos "));
        assert!(requests[2].starts_with("GET /api/v1/platform/hub/repos/-/my-agent/directories "));
        assert!(requests[3]
            .starts_with("POST /api/v1/platform/hub/repos/-/my-agent/directories/commits "));
        assert!(summary.changed);
        assert_eq!(summary.repo, "my-agent");
    }

    #[test]
    fn sync_context_reports_directory_commit_conflicts() {
        let root = tempfile::tempdir().unwrap();
        write_file(root.path(), "agent.ts", "export const agent = {};\n");
        write_file(root.path(), "instructions.md", "new\n");
        let (base_url, _requests, handle) = serve(vec![
            TestResponse {
                status: "200 OK",
                body: r#"{"repo":{"repo_handle":"my-agent"}}"#,
            },
            TestResponse {
                status: "200 OK",
                body: r#"{"commit_hash":"old","files":{"instructions.md":{"type":"file","content":"old\n"},"memories/agent/AGENTS.md":{"type":"file","content":""}}}"#,
            },
            TestResponse {
                status: "409 Conflict",
                body: r#"{"detail":"stale parent"}"#,
            },
        ]);
        let client = ContextHubClient::for_test(&base_url);

        let plan =
            prepare_context_sync(&client, &project(root.path(), "agent.ts"), "my-agent", true)
                .unwrap();
        let err = apply_context_sync(&client, plan, ConflictResolution::Overwrite).unwrap_err();

        handle.join().unwrap();
        assert!(
            err.to_string().contains("changed during deploy"),
            "unexpected error: {err:#}"
        );
    }
}
