use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;

use super::artifact::read_artifact_manifest;
use super::compile_result_items;
use super::harbor::{project_env_keys, provider_env_templates, stage_harbor_adapter};
use super::helpers::{looks_like_project_root, project_digest, ProjectDigestInputs};
use super::init::{
    coding_agent_handoff_prompt, coding_agent_prompt, documentation_url, harbor_run_command,
    harbor_run_command_for_shell, LaunchShell,
};
use super::types::{
    CompileOptions, CompileSummary, InitSummary, EVAL_DATASET_DIR, GENERATED_EVAL_DIR,
};
use crate::cli_output::SequenceItem;
use crate::compile::test_support::setup_mock_ts_runtime;
use crate::compile::LANGGRAPH_API_VERSION_ENV;
use crate::install::{BUNDLED_RUNTIME_PATH_ENV, INSTALL_CHANNEL_ENV};
use crate::project::{Language, Project};

static INSTALLATION_ENV_LOCK: Mutex<()> = Mutex::new(());

#[test]
fn compile_result_uses_the_shared_sequence_without_the_internal_graph() {
    let summary = CompileSummary {
        artifact_dir: PathBuf::from("agent/.mda/evals/artifact"),
        model_name: Some("openai:gpt-5".to_string()),
        artifact_sha256: "64fe".to_string(),
    };

    assert_eq!(
        compile_result_items(Path::new("agent"), &summary),
        [SequenceItem::section(
            "Eval artifact ready",
            [".mda/evals/artifact", "Model: openai:gpt-5", "Digest: 64fe",],
        )]
    );
}

#[test]
fn coding_agent_handoff_prompt_contains_only_skill_guidance() {
    assert_eq!(
        coding_agent_handoff_prompt(),
        concat!(
            "Create Harbor eval tasks under evals/ using the eval-engineering skill:\n",
            "npx skills add langchain-ai/langchain-skills --skill eval-engineering --yes\n\n",
            "If npx isn't installed, use the skill directly from:\n",
            "https://github.com/langchain-ai/langchain-skills/blob/main/config/skills/",
            "eval-engineering/SKILL.md",
        )
    );
}

#[test]
fn coding_agent_prompt_asks_for_harbor_tasks() {
    let root = tempfile::tempdir().unwrap();
    let summary = init_summary(root.path().join("agent-project"));
    let prompt = coding_agent_prompt(&summary).unwrap();

    assert!(
        prompt.contains("Create Harbor eval tasks under evals/"),
        "unexpected prompt: {prompt}"
    );
}

#[test]
fn coding_agent_prompt_ends_with_the_harbor_run_command_and_a_newline() {
    let root = tempfile::tempdir().unwrap();
    let summary = init_summary(root.path().join("agent-project"));
    let prompt = coding_agent_prompt(&summary).unwrap();
    let command = harbor_run_command(&summary).unwrap();

    assert!(
        prompt.contains("To run these evals from the project root:\n"),
        "unexpected prompt: {prompt}"
    );
    assert!(prompt.ends_with(&format!("{command}\n")));
    assert!(!prompt.contains("first task"));
}

#[test]
fn coding_agent_prompt_asks_a_typescript_project_for_typescript_verifiers() {
    let root = tempfile::tempdir().unwrap();
    let summary = init_summary_for(root.path().join("agent-project"), Language::TypeScript);

    let prompt = coding_agent_prompt(&summary).unwrap();

    assert!(
        prompt.contains("This is a TypeScript agent."),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("`evals/<task>/tests/*.test.ts`"),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("node --experimental-strip-types --test /tests/*.test.ts"),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("`environment/Dockerfile` on `node:24`"),
        "unexpected prompt: {prompt}"
    );
    assert!(prompt.contains(documentation_url(Language::TypeScript)));
    assert!(!prompt.contains("pytest"));
}

#[test]
fn coding_agent_prompt_asks_a_python_project_for_python_verifiers() {
    let root = tempfile::tempdir().unwrap();
    let summary = init_summary_for(root.path().join("agent-project"), Language::Python);

    let prompt = coding_agent_prompt(&summary).unwrap();

    assert!(
        prompt.contains("This is a Python agent."),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("`evals/<task>/tests/test_*.py`"),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("python -m pytest /tests -q"),
        "unexpected prompt: {prompt}"
    );
    assert!(
        prompt.contains("`environment/Dockerfile` on `python:3.12`"),
        "unexpected prompt: {prompt}"
    );
    assert!(prompt.contains(documentation_url(Language::Python)));
    assert!(!prompt.contains(".test.ts"));
}

fn init_summary(project_root: PathBuf) -> InitSummary {
    init_summary_for(project_root, Language::Python)
}

fn init_summary_for(project_root: PathBuf, language: Language) -> InitSummary {
    InitSummary {
        language,
        dataset_dir: project_root.join("evals"),
        job_config_path: project_root.join("evals/harbor-job.json"),
        project_root,
    }
}

#[test]
fn eval_documentation_url_uses_python_docs_for_python_projects() {
    assert_eq!(
        documentation_url(Language::Python),
        "https://docs.langchain.com/langsmith/python/managed-deep-agents-evals"
    );
}

#[test]
fn eval_documentation_url_uses_javascript_docs_for_typescript_projects() {
    assert_eq!(
        documentation_url(Language::TypeScript),
        "https://docs.langchain.com/langsmith/javascript/managed-deep-agents-evals"
    );
}

struct InstallationEnv {
    channel: Option<std::ffi::OsString>,
    runtime_source: Option<std::ffi::OsString>,
    api_version: Option<std::ffi::OsString>,
}

impl InstallationEnv {
    fn set(runtime_source: &Path, api_version: Option<&str>) -> Self {
        let previous = Self {
            channel: std::env::var_os(INSTALL_CHANNEL_ENV),
            runtime_source: std::env::var_os(BUNDLED_RUNTIME_PATH_ENV),
            api_version: std::env::var_os(LANGGRAPH_API_VERSION_ENV),
        };
        std::env::set_var(INSTALL_CHANNEL_ENV, "npm");
        std::env::set_var(BUNDLED_RUNTIME_PATH_ENV, runtime_source);
        match api_version {
            Some(value) => std::env::set_var(LANGGRAPH_API_VERSION_ENV, value),
            None => std::env::remove_var(LANGGRAPH_API_VERSION_ENV),
        }
        previous
    }
}

impl Drop for InstallationEnv {
    fn drop(&mut self) {
        match self.channel.take() {
            Some(value) => std::env::set_var(INSTALL_CHANNEL_ENV, value),
            None => std::env::remove_var(INSTALL_CHANNEL_ENV),
        }
        match self.runtime_source.take() {
            Some(value) => std::env::set_var(BUNDLED_RUNTIME_PATH_ENV, value),
            None => std::env::remove_var(BUNDLED_RUNTIME_PATH_ENV),
        }
        match self.api_version.take() {
            Some(value) => std::env::set_var(LANGGRAPH_API_VERSION_ENV, value),
            None => std::env::remove_var(LANGGRAPH_API_VERSION_ENV),
        }
    }
}

fn write_eval_compile_project(root: &Path) {
    setup_mock_ts_runtime(root);
    fs::write(
        root.join("agent.ts"),
        "export const agent = defineDeepAgent({ name: \"eval-agent\", model: \"openai:gpt-5-mini\" });\n",
    )
    .unwrap();
    fs::write(root.join("instructions.md"), "Be precise.\n").unwrap();
}

fn write_harbor_task(root: &Path) {
    let task = root.join(EVAL_DATASET_DIR).join("sample");
    fs::create_dir_all(task.join("tests")).unwrap();
    fs::write(task.join("instruction.md"), "Do the thing.\n").unwrap();
}

fn compile_eval(root: &Path, models: &[&str]) -> anyhow::Result<super::CompileSummary> {
    compile_eval_with_api_version(root, models, None)
}

fn compile_eval_with_api_version(
    root: &Path,
    models: &[&str],
    api_version: Option<&str>,
) -> anyhow::Result<super::CompileSummary> {
    assert!(models.len() <= 1, "eval compile accepts one model override");
    let _lock = INSTALLATION_ENV_LOCK.lock().unwrap();
    let runtime_source = root.join("node_modules/managed-deepagents/dist");
    let _env = InstallationEnv::set(&runtime_source, api_version);
    super::compile(CompileOptions {
        path: root.to_path_buf(),
        model: models.first().map(|model| (*model).to_string()),
        artifact_id: None,
    })
}

fn compile_eval_for_artifact(
    root: &Path,
    artifact_id: &str,
) -> anyhow::Result<super::CompileSummary> {
    let _lock = INSTALLATION_ENV_LOCK.lock().unwrap();
    let runtime_source = root.join("node_modules/managed-deepagents/dist");
    let _env = InstallationEnv::set(&runtime_source, None);
    super::compile(CompileOptions {
        path: root.to_path_buf(),
        model: None,
        artifact_id: Some(artifact_id.to_string()),
    })
}

fn artifact_digest(artifact_dir: &Path) -> String {
    read_artifact_manifest(artifact_dir).unwrap()["artifact_sha256"]
        .as_str()
        .unwrap()
        .to_string()
}

fn test_project(root: &Path, language: Language) -> Project {
    Project {
        root: root.to_path_buf(),
        language,
        agent_file: match language {
            Language::TypeScript => "agent.ts".into(),
            Language::Python => "agent.py".into(),
        },
        files: vec![match language {
            Language::TypeScript => "agent.ts".into(),
            Language::Python => "agent.py".into(),
        }],
    }
}

#[test]
fn compile_reuses_current_artifact_without_replacing_internal_files() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    write_harbor_task(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let expected = root
        .path()
        .canonicalize()
        .unwrap()
        .join(GENERATED_EVAL_DIR)
        .join("artifact");
    assert_eq!(first.artifact_dir, expected);
    let manifest_path = expected.join("mda-eval-manifest.json");
    let first_manifest = fs::read_to_string(&manifest_path).unwrap();
    let first_digest = artifact_digest(&expected);
    let sentinel = expected.join("cache-sentinel");
    fs::write(&sentinel, "keep me\n").unwrap();

    let second = compile_eval(root.path(), &[]).unwrap();

    assert_eq!(second.artifact_dir, expected);
    assert_eq!(artifact_digest(&expected), first_digest);
    assert_eq!(fs::read_to_string(manifest_path).unwrap(), first_manifest);
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "keep me\n");
}

#[test]
fn compile_isolates_artifact_by_validated_job_id() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    let artifact_id = "11111111-1111-4111-8111-111111111111";

    let summary = compile_eval_for_artifact(root.path(), artifact_id).unwrap();

    assert_eq!(
        summary.artifact_dir,
        root.path()
            .canonicalize()
            .unwrap()
            .join(GENERATED_EVAL_DIR)
            .join("artifacts")
            .join(artifact_id)
    );
    assert!(!root
        .path()
        .join(GENERATED_EVAL_DIR)
        .join("artifact")
        .exists());
}

#[test]
fn compile_rejects_invalid_artifact_id_without_creating_artifact() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let error = compile_eval_for_artifact(root.path(), "../outside").unwrap_err();

    assert!(error.to_string().contains("artifact ID"));
    assert!(!root
        .path()
        .join(GENERATED_EVAL_DIR)
        .join("artifacts")
        .exists());
}

#[test]
fn compile_replaces_stale_artifact_when_authored_input_changes() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    write_harbor_task(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let first_digest = artifact_digest(&first.artifact_dir);
    let sentinel = first.artifact_dir.join("stale-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::write(root.path().join("instructions.md"), "Be concise.\n").unwrap();

    let second = compile_eval(root.path(), &[]).unwrap();

    assert_ne!(artifact_digest(&second.artifact_dir), first_digest);
    assert!(!sentinel.exists());
}

#[test]
fn compile_replaces_stale_artifact_when_bundled_runtime_changes() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let first_digest = artifact_digest(&first.artifact_dir);
    let sentinel = first.artifact_dir.join("runtime-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    let runtime_entry = root
        .path()
        .join("node_modules/managed-deepagents/dist/runtime/index.js");
    fs::write(&runtime_entry, "// updated runtime entry\n").unwrap();

    let second = compile_eval(root.path(), &[]).unwrap();

    assert_ne!(artifact_digest(&second.artifact_dir), first_digest);
    assert!(!sentinel.exists());
    assert_eq!(
        fs::read_to_string(
            second
                .artifact_dir
                .join("project/__runtime__/runtime/index.js")
        )
        .unwrap(),
        "// updated runtime entry\n"
    );
}

#[test]
fn compile_replaces_stale_artifact_when_langgraph_api_version_changes() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval_with_api_version(root.path(), &[], Some("0.12.0rc9")).unwrap();
    let first_digest = artifact_digest(&first.artifact_dir);
    let sentinel = first.artifact_dir.join("api-version-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();

    let second = compile_eval_with_api_version(root.path(), &[], Some("0.13.0rc1")).unwrap();
    let manifest = read_artifact_manifest(&second.artifact_dir).unwrap();
    let langgraph: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(second.artifact_dir.join("project/langgraph.json")).unwrap(),
    )
    .unwrap();

    assert_ne!(artifact_digest(&second.artifact_dir), first_digest);
    assert!(!sentinel.exists());
    assert_eq!(langgraph["_INTERNAL_docker_tag"], "0.13.0rc1-node22");
    assert_eq!(manifest["langgraph_api_version"], "0.13.0rc1");
}

#[test]
fn compile_fingerprints_exact_manifest_provenance() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    fs::write(root.path().join(".gitignore"), ".mda/\nnode_modules/\n").unwrap();
    for args in [
        &["init", "-q"][..],
        &["config", "user.email", "eval-test@example.com"][..],
        &["config", "user.name", "Eval Test"][..],
        &["add", ".gitignore", "agent.ts", "instructions.md"][..],
        &["commit", "-q", "-m", "initial"][..],
    ] {
        let status = Command::new("git")
            .arg("-C")
            .arg(root.path())
            .args(args)
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?} failed");
    }

    let first = compile_eval(root.path(), &[]).unwrap();
    let first_digest = artifact_digest(&first.artifact_dir);
    let first_manifest = read_artifact_manifest(&first.artifact_dir).unwrap();
    let revision = first_manifest["source_revision"]
        .as_str()
        .unwrap()
        .to_string();
    assert_eq!(first_manifest["dirty"], false);
    let dirty_sentinel = first.artifact_dir.join("dirty-sentinel");
    fs::write(&dirty_sentinel, "remove me\n").unwrap();
    fs::write(root.path().join(".env"), "TEST_ONLY=1\n").unwrap();

    let dirty = compile_eval(root.path(), &[]).unwrap();
    let dirty_digest = artifact_digest(&dirty.artifact_dir);
    let dirty_manifest = read_artifact_manifest(&dirty.artifact_dir).unwrap();
    assert_ne!(dirty_digest, first_digest);
    assert!(!dirty_sentinel.exists());
    assert_eq!(dirty_manifest["source_revision"], revision);
    assert_eq!(dirty_manifest["dirty"], true);

    fs::remove_file(root.path().join(".env")).unwrap();
    let status = Command::new("git")
        .arg("-C")
        .arg(root.path())
        .args(["commit", "-q", "--allow-empty", "-m", "new revision"])
        .status()
        .unwrap();
    assert!(status.success());
    let revision_sentinel = dirty.artifact_dir.join("revision-sentinel");
    fs::write(&revision_sentinel, "remove me\n").unwrap();

    let revised = compile_eval(root.path(), &[]).unwrap();
    let revised_manifest = read_artifact_manifest(&revised.artifact_dir).unwrap();
    assert_ne!(artifact_digest(&revised.artifact_dir), dirty_digest);
    assert!(!revision_sentinel.exists());
    assert_ne!(revised_manifest["source_revision"], revision);
    assert_eq!(revised_manifest["dirty"], false);
}

#[test]
fn compile_marks_project_outside_git_as_dirty() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let summary = compile_eval(root.path(), &[]).unwrap();
    let manifest = read_artifact_manifest(&summary.artifact_dir).unwrap();

    assert_eq!(manifest["source_revision"], serde_json::Value::Null);
    assert_eq!(manifest["dirty"], true);
}

#[test]
fn compile_replaces_structurally_incomplete_cached_artifact() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let sentinel = first.artifact_dir.join("incomplete-cache-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::remove_file(first.artifact_dir.join("project.tar.gz")).unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert!(first.artifact_dir.join("project.tar.gz").is_file());
}

#[test]
fn compile_rebuilds_cache_after_live_runtime_directory_is_removed() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let runtime_dir = first.artifact_dir.join("project/__runtime__");
    let runtime_entry = runtime_dir.join("runtime/index.js");
    let expected = fs::read_to_string(&runtime_entry).unwrap();
    let sentinel = first.artifact_dir.join("missing-runtime-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::remove_dir_all(&runtime_dir).unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert_eq!(fs::read_to_string(runtime_entry).unwrap(), expected);
}

#[test]
fn compile_rebuilds_cache_after_live_project_contents_are_tampered() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let runtime_entry = first
        .artifact_dir
        .join("project/__runtime__/runtime/index.js");
    let expected = fs::read_to_string(&runtime_entry).unwrap();
    let unexpected = first.artifact_dir.join("project/unexpected.txt");
    let sentinel = first.artifact_dir.join("tampered-project-sentinel");
    fs::write(&runtime_entry, "// tampered cached runtime\n").unwrap();
    fs::write(&unexpected, "not in the compiled artifact\n").unwrap();
    fs::write(&sentinel, "remove me\n").unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert!(!unexpected.exists());
    assert_eq!(fs::read_to_string(runtime_entry).unwrap(), expected);
}

#[test]
fn compile_rebuilds_cache_after_langgraph_json_is_malformed() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let langgraph_path = first.artifact_dir.join("project/langgraph.json");
    let expected = fs::read_to_string(&langgraph_path).unwrap();
    let sentinel = first.artifact_dir.join("malformed-langgraph-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::write(&langgraph_path, "{not json\n").unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert_eq!(fs::read_to_string(langgraph_path).unwrap(), expected);
}

#[test]
fn compile_rebuilds_cache_after_project_archive_is_corrupt() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let archive_path = first.artifact_dir.join("project.tar.gz");
    let sentinel = first.artifact_dir.join("corrupt-archive-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::write(&archive_path, []).unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    let archive_file = fs::File::open(archive_path).unwrap();
    let decoder = flate2::read::GzDecoder::new(archive_file);
    let mut archive = tar::Archive::new(decoder);
    let contains_langgraph = archive.entries().unwrap().any(|entry| {
        entry.unwrap().path().unwrap().as_ref() == Path::new("project/langgraph.json")
    });
    assert!(contains_langgraph);
}

#[test]
fn compile_rebuilds_cache_after_build_marker_is_wrong() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let marker_path = first.artifact_dir.join("project/.mda-build");
    let expected = fs::read_to_string(&marker_path).unwrap();
    let sentinel = first.artifact_dir.join("wrong-marker-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    fs::write(&marker_path, []).unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert_eq!(fs::read_to_string(marker_path).unwrap(), expected);
}

#[test]
fn compile_rebuilds_cache_after_manifest_graph_is_tampered() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let first = compile_eval(root.path(), &[]).unwrap();
    let manifest_path = first.artifact_dir.join("mda-eval-manifest.json");
    let sentinel = first.artifact_dir.join("tampered-graph-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();
    let mut manifest = read_artifact_manifest(&first.artifact_dir).unwrap();
    manifest["graph"] = serde_json::json!("another-valid-agent");
    fs::write(
        &manifest_path,
        format!("{}\n", serde_json::to_string_pretty(&manifest).unwrap()),
    )
    .unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert!(!sentinel.exists());
    assert_eq!(
        read_artifact_manifest(&first.artifact_dir).unwrap()["graph"],
        serde_json::json!("eval-agent")
    );
}

#[test]
fn compile_rebuilds_cache_after_runtime_contract_is_tampered() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    let summary = compile_eval(root.path(), &[]).unwrap();
    let manifest_path = summary.artifact_dir.join("mda-eval-manifest.json");
    let cases = [
        ("language", serde_json::json!("python")),
        ("node_version", serde_json::json!("999")),
        ("python_version", serde_json::json!("3.11")),
        ("adapter_protocol_version", serde_json::json!(99)),
        ("runtime_version", serde_json::json!("9.9.9")),
    ];

    for (field, wrong_value) in cases {
        let mut manifest = read_artifact_manifest(&summary.artifact_dir).unwrap();
        let expected_value = manifest[field].clone();
        assert_ne!(expected_value, wrong_value);
        manifest[field] = wrong_value;
        fs::write(
            &manifest_path,
            format!("{}\n", serde_json::to_string_pretty(&manifest).unwrap()),
        )
        .unwrap();
        let sentinel = summary
            .artifact_dir
            .join(format!("tampered-{field}-sentinel"));
        fs::write(&sentinel, "remove me\n").unwrap();

        compile_eval(root.path(), &[]).unwrap();

        assert!(
            !sentinel.exists(),
            "cache reused manifest with stale {field}"
        );
        assert_eq!(
            read_artifact_manifest(&summary.artifact_dir).unwrap()[field],
            expected_value,
            "compile did not restore {field}"
        );
    }
}

#[test]
fn compile_does_not_stage_harbor_runtime_state() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    write_harbor_task(root.path());

    compile_eval(root.path(), &[]).unwrap();

    let generated = root.path().join(GENERATED_EVAL_DIR);
    assert!(!generated.join("tasks").exists());
    assert!(!generated.join("harbor-adapter").exists());
    assert!(!generated.join("harbor-job.json").exists());
    assert!(!generated.join("harbor-jobs").exists());
    assert!(!generated.join("jobs").exists());
}

#[test]
fn compile_needs_no_dataset_and_preserves_user_job_config() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    let evals = root.path().join(EVAL_DATASET_DIR);
    fs::create_dir_all(&evals).unwrap();
    let config = evals.join("harbor-job.json");
    fs::write(&config, "user-owned config\n").unwrap();

    compile_eval(root.path(), &[]).unwrap();

    assert_eq!(fs::read_to_string(config).unwrap(), "user-owned config\n");
    assert!(!root.path().join(GENERATED_EVAL_DIR).join("tasks").exists());
}

#[test]
fn compile_model_change_updates_manifest_and_invalidates_cache() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    write_harbor_task(root.path());

    let first = compile_eval(root.path(), &["anthropic:claude-sonnet-4-5"]).unwrap();
    let first_digest = artifact_digest(&first.artifact_dir);
    let sentinel = first.artifact_dir.join("model-sentinel");
    fs::write(&sentinel, "remove me\n").unwrap();

    let second = compile_eval(root.path(), &["openai:gpt-5.2"]).unwrap();
    let manifest = read_artifact_manifest(&second.artifact_dir).unwrap();

    assert_eq!(manifest["model_name"], serde_json::json!("openai:gpt-5.2"));
    assert_eq!(
        manifest["model_override"],
        serde_json::json!("openai:gpt-5.2")
    );
    assert_ne!(artifact_digest(&second.artifact_dir), first_digest);
    assert!(!sentinel.exists());
}

#[test]
fn compile_without_override_preserves_dynamic_authored_model() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    fs::write(
        root.path().join("agent.ts"),
        concat!(
            "const gatewayModel = new ChatOpenAI({ model: \"openai/gpt-5\", ",
            "configuration: { baseURL: \"https://api.smith.langchain.com/api/v1/gateway\" } });\n",
            "export const agent = defineDeepAgent({ name: \"eval-agent\", model: gatewayModel });\n",
        ),
    )
    .unwrap();

    let summary = compile_eval(root.path(), &[]).unwrap();
    let manifest = read_artifact_manifest(&summary.artifact_dir).unwrap();

    assert!(summary.model_name.is_none());
    assert!(manifest["model_name"].is_null());
    assert!(manifest["model_override"].is_null());
}

#[test]
fn compile_rejects_blank_model_override() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    let _lock = INSTALLATION_ENV_LOCK.lock().unwrap();
    let runtime_source = root.path().join("node_modules/managed-deepagents/dist");
    let _env = InstallationEnv::set(&runtime_source, None);

    let error = super::compile(CompileOptions {
        path: root.path().to_path_buf(),
        model: Some("   ".to_string()),
        artifact_id: None,
    })
    .unwrap_err();

    assert!(error.to_string().contains("must not be blank"), "{error:#}");
}

#[test]
fn compile_explicit_model_records_override_and_injects_provider() {
    let root = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());

    let summary = compile_eval(root.path(), &["anthropic:claude-sonnet-4-5"]).unwrap();
    let manifest = read_artifact_manifest(&summary.artifact_dir).unwrap();

    assert_eq!(
        manifest["model_name"],
        serde_json::json!("anthropic:claude-sonnet-4-5")
    );
    assert_eq!(
        manifest["model_override"],
        serde_json::json!("anthropic:claude-sonnet-4-5")
    );
}

#[cfg(unix)]
fn assert_compile_rejects_output_symlink(relative: &Path) {
    use std::os::unix::fs::symlink;

    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    write_harbor_task(root.path());
    fs::write(outside.path().join("sentinel"), "keep me\n").unwrap();
    if let Some(parent) = relative
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(root.path().join(parent)).unwrap();
    }
    symlink(outside.path(), root.path().join(relative)).unwrap();

    let error = compile_eval(root.path(), &[]).unwrap_err();

    assert!(error.to_string().contains("symlink"), "{error:#}");
    assert_eq!(
        fs::read_to_string(outside.path().join("sentinel")).unwrap(),
        "keep me\n"
    );
    let mut outside_entries = fs::read_dir(outside.path())
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect::<Vec<_>>();
    outside_entries.sort();
    assert_eq!(outside_entries, vec![std::ffi::OsString::from("sentinel")]);
}

#[cfg(unix)]
#[test]
fn compile_rejects_symlinked_mda_root_before_external_mutation() {
    assert_compile_rejects_output_symlink(Path::new(".mda"));
}

#[cfg(unix)]
#[test]
fn compile_rejects_symlinked_evals_root_before_external_mutation() {
    assert_compile_rejects_output_symlink(Path::new(GENERATED_EVAL_DIR));
}

#[cfg(unix)]
#[test]
fn compile_rejects_symlinked_artifact_before_external_mutation() {
    assert_compile_rejects_output_symlink(Path::new(GENERATED_EVAL_DIR).join("artifact").as_path());
}

#[cfg(unix)]
#[test]
fn compile_replaces_nested_project_symlink_without_following_it() {
    use std::os::unix::fs::symlink;

    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    write_eval_compile_project(root.path());
    let artifact = root.path().join(GENERATED_EVAL_DIR).join("artifact");
    fs::create_dir_all(&artifact).unwrap();
    fs::write(outside.path().join("sentinel"), "keep me\n").unwrap();
    symlink(outside.path(), artifact.join("project")).unwrap();

    let summary = compile_eval(root.path(), &[]).unwrap();

    assert_eq!(
        fs::read_to_string(outside.path().join("sentinel")).unwrap(),
        "keep me\n"
    );
    let metadata = fs::symlink_metadata(summary.artifact_dir.join("project")).unwrap();
    assert!(metadata.is_dir());
    assert!(!metadata.file_type().is_symlink());
}

#[test]
fn stage_harbor_adapter_writes_package_files() {
    let root = tempfile::tempdir().unwrap();
    let adapter = stage_harbor_adapter(root.path()).unwrap();
    assert_eq!(
        adapter,
        root.path().join(GENERATED_EVAL_DIR).join("harbor-adapter")
    );
    assert!(adapter.join("mda_harbor/__init__.py").is_file());
    assert!(adapter.join("mda_harbor/agent.py").is_file());
    assert!(adapter.join("mda_harbor/job_plugin.py").is_file());
    assert!(adapter.join("mda_harbor/runners/node_runner.mjs").is_file());
    assert!(adapter
        .join("mda_harbor/runners/python_runner.py")
        .is_file());
    // Warm-snapshot helper was never wired into Harbor job generation.
    assert!(!adapter.join("mda_harbor/ensure_snapshot.py").exists());
}

#[test]
fn stage_harbor_adapter_writes_langsmith_plugin() {
    let root = tempfile::tempdir().unwrap();

    let adapter = stage_harbor_adapter(root.path()).unwrap();

    assert!(adapter.join("mda_harbor/langsmith_plugin.py").is_file());
}

#[test]
fn harbor_run_command_matches_documented_invocation() {
    let summary = init_summary(PathBuf::from("/tmp/research-agent"));

    let command = harbor_run_command(&summary).unwrap();

    assert_eq!(
        command,
        concat!(
            "HARBOR_LANGSMITH_DATASET=mda-research-agent-evals \\\n",
            "PYTHONPATH=.mda/evals/harbor-adapter \\\n",
            "uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor run \\\n",
            "  --config evals/harbor-job.json --yes \\\n",
            "  --plugin mda_harbor.job_plugin:MDAJobPlugin \\\n",
            "  --plugin mda_harbor.langsmith_plugin:LangSmithPlugin",
        )
    );
}

#[test]
fn harbor_run_command_loads_project_dotenv_when_present() {
    let root = tempfile::tempdir().unwrap();
    let project = root.path().join("research-agent");
    fs::create_dir(&project).unwrap();
    fs::write(project.join(".env"), "LANGSMITH_API_KEY=test-only\n").unwrap();
    let summary = init_summary(project);

    let command = harbor_run_command_for_shell(&summary, LaunchShell::Posix).unwrap();

    assert!(
        command.contains("uv run --env-file .env --python 3.12 --with 'harbor[langsmith]==0.21.0'"),
        "unexpected command: {command}"
    );
}

#[test]
fn harbor_run_command_loads_project_dotenv_for_powershell() {
    let root = tempfile::tempdir().unwrap();
    let project = root.path().join("research-agent");
    fs::create_dir(&project).unwrap();
    fs::write(project.join(".env"), "LANGSMITH_API_KEY=test-only\n").unwrap();
    let summary = init_summary(project);

    let command = harbor_run_command_for_shell(&summary, LaunchShell::PowerShell).unwrap();

    assert!(
        command.contains("uv run --env-file .env --python 3.12 --with 'harbor[langsmith]==0.21.0'"),
        "unexpected command: {command}"
    );
}

#[test]
fn harbor_run_command_does_not_use_harbor_primary_key_flag() {
    let summary = init_summary(PathBuf::from("/tmp/research-agent"));

    let command = harbor_run_command(&summary).unwrap();

    assert!(!command.contains("--pk"));
}

#[test]
fn harbor_run_command_posix_quotes_unsafe_project_basename() {
    let summary = init_summary(PathBuf::from("/tmp/research;$(touch pwned)'agent"));

    let command = harbor_run_command(&summary).unwrap();

    assert_eq!(
        command.lines().next(),
        Some(r#"HARBOR_LANGSMITH_DATASET='mda-research;$(touch pwned)'\''agent-evals' \"#)
    );
}

#[test]
fn harbor_run_command_renders_powershell_launcher() {
    let summary = init_summary(PathBuf::from("C:/agents/research-agent"));

    let command = harbor_run_command_for_shell(&summary, LaunchShell::PowerShell).unwrap();

    assert_eq!(
        command,
        concat!(
            "$env:HARBOR_LANGSMITH_DATASET = 'mda-research-agent-evals'\n",
            "$env:PYTHONPATH = '.mda/evals/harbor-adapter'\n",
            "uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor run `\n",
            "  --config evals/harbor-job.json --yes `\n",
            "  --plugin mda_harbor.job_plugin:MDAJobPlugin `\n",
            "  --plugin mda_harbor.langsmith_plugin:LangSmithPlugin",
        )
    );
}

#[test]
fn harbor_run_command_powershell_quotes_project_basename() {
    let summary = init_summary(PathBuf::from("C:/agents/research'agent"));

    let command = harbor_run_command_for_shell(&summary, LaunchShell::PowerShell).unwrap();

    assert_eq!(
        command.lines().next(),
        Some("$env:HARBOR_LANGSMITH_DATASET = 'mda-research''agent-evals'")
    );
}

#[test]
fn staged_harbor_adapter_forwards_langsmith_parent() {
    let root = tempfile::tempdir().unwrap();
    let adapter = stage_harbor_adapter(root.path()).unwrap();
    let source = fs::read_to_string(adapter.join("mda_harbor/agent.py")).unwrap();

    assert!(source.contains("parent_env(self.context_id)"));
}

#[test]
fn staged_python_runner_uses_langsmith_parent() {
    let root = tempfile::tempdir().unwrap();
    let adapter = stage_harbor_adapter(root.path()).unwrap();
    let source = fs::read_to_string(adapter.join("mda_harbor/runners/python_runner.py")).unwrap();

    assert!(source.contains("tracing_context(parent=headers, enabled=True)"));
}

#[test]
fn staged_node_runner_uses_langsmith_parent() {
    let root = tempfile::tempdir().unwrap();
    let adapter = stage_harbor_adapter(root.path()).unwrap();
    let source = fs::read_to_string(adapter.join("mda_harbor/runners/node_runner.mjs")).unwrap();

    assert!(source.contains("RunTree.fromHeaders(headers, { tracingEnabled: true })"));
}

#[test]
fn provider_env_templates_include_known_provider_api_keys() {
    let empty = BTreeSet::new();
    let templates = provider_env_templates(&empty);
    assert_eq!(
        templates
            .get("OPENAI_API_KEY")
            .and_then(|value| value.as_str()),
        Some("${OPENAI_API_KEY:-}")
    );
}

#[test]
fn provider_env_templates_includes_project_env_keys() {
    let mut keys = BTreeSet::new();
    keys.insert("ANTHROPIC_API_KEY".to_string());
    keys.insert("LANGSMITH_GATEWAY_API_KEY".to_string());
    let templates = provider_env_templates(&keys);
    assert_eq!(
        templates
            .get("ANTHROPIC_API_KEY")
            .and_then(|value| value.as_str()),
        Some("${ANTHROPIC_API_KEY:-}")
    );
    assert_eq!(
        templates
            .get("LANGSMITH_GATEWAY_API_KEY")
            .and_then(|value| value.as_str()),
        Some("${LANGSMITH_GATEWAY_API_KEY:-}")
    );
}

#[test]
fn provider_env_templates_forwards_connector_and_tool_secrets_from_dotenv() {
    let mut keys = BTreeSet::new();
    keys.insert("MCP_BEARER_TOKEN".to_string());
    keys.insert("STRIPE_API_KEY".to_string());
    // Container/runner wiring must not be overridable from `.env`.
    keys.insert("PYTHONPATH".to_string());
    keys.insert("HARBOR_SESSION_ID".to_string());
    let templates = provider_env_templates(&keys);
    assert_eq!(
        templates
            .get("MCP_BEARER_TOKEN")
            .and_then(|value| value.as_str()),
        Some("${MCP_BEARER_TOKEN:-}")
    );
    assert_eq!(
        templates
            .get("STRIPE_API_KEY")
            .and_then(|value| value.as_str()),
        Some("${STRIPE_API_KEY:-}")
    );
    assert!(templates.get("PYTHONPATH").is_none());
    assert!(templates.get("HARBOR_SESSION_ID").is_none());
}

#[test]
fn provider_env_templates_drops_retired_workspace_and_key_names() {
    let mut keys = BTreeSet::new();
    for name in [
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_TENANT_ID",
        "LANGCHAIN_WORKSPACE_ID",
        "LANGSMITH_TENANT_ID",
    ] {
        keys.insert(name.to_string());
    }
    keys.insert("LANGSMITH_WORKSPACE_ID".to_string());
    let templates = provider_env_templates(&keys);
    for name in [
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_TENANT_ID",
        "LANGCHAIN_WORKSPACE_ID",
        "LANGSMITH_TENANT_ID",
    ] {
        assert!(
            templates.get(name).is_none(),
            "{name} must not reach trials"
        );
    }
    assert_eq!(
        templates
            .get("LANGSMITH_WORKSPACE_ID")
            .and_then(|value| value.as_str()),
        Some("${LANGSMITH_WORKSPACE_ID:-}")
    );
}

#[test]
fn provider_env_templates_skips_keys_a_shell_cannot_export() {
    // `dotenvy` accepts these; `export Key.Value=…` does not parse, which would
    // leave Harbor unable to resolve the template it was handed.
    let mut keys = BTreeSet::new();
    for name in ["Key.Value", "WITH-DASH", "1LEADING_DIGIT", "WITH SPACE", ""] {
        keys.insert(name.to_string());
    }
    keys.insert("_OK".to_string());
    keys.insert("ok_KEY2".to_string());
    let templates = provider_env_templates(&keys);
    for name in ["Key.Value", "WITH-DASH", "1LEADING_DIGIT", "WITH SPACE", ""] {
        assert!(
            templates.get(name).is_none(),
            "{name} is not shell-exportable and must not be templated"
        );
    }
    assert!(templates.get("_OK").is_some());
    assert!(templates.get("ok_KEY2").is_some());
}

#[test]
fn provider_env_templates_are_independent_of_host_values() {
    const KEYS: [&str; 2] = ["OPENAI_BASE_URL", "LANGSMITH_GATEWAY_API_KEY"];
    let previous: Vec<_> = KEYS
        .iter()
        .map(|key| (*key, std::env::var_os(key)))
        .collect();
    let empty = BTreeSet::new();

    for key in KEYS {
        std::env::remove_var(key);
    }
    let without_host = provider_env_templates(&empty);
    assert_eq!(
        without_host.get(KEYS[0]).and_then(|value| value.as_str()),
        Some("${OPENAI_BASE_URL:-}")
    );
    assert_eq!(
        without_host.get(KEYS[1]).and_then(|value| value.as_str()),
        Some("${LANGSMITH_GATEWAY_API_KEY:-}")
    );

    std::env::set_var(KEYS[0], "https://sentinel.invalid");
    std::env::set_var(KEYS[1], "sentinel-secret");
    assert_eq!(provider_env_templates(&empty), without_host);

    for (key, value) in previous {
        match value {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }
}

#[test]
fn provider_env_templates_never_serialize_dotenv_values() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join(".env"),
        "CUSTOM_TOOL_TOKEN=sentinel-secret-value\n",
    )
    .unwrap();
    let templates = provider_env_templates(&project_env_keys(root.path()));
    let serialized = serde_json::to_string(&templates).unwrap();

    assert_eq!(
        templates
            .get("CUSTOM_TOOL_TOKEN")
            .and_then(|value| value.as_str()),
        Some("${CUSTOM_TOOL_TOKEN:-}")
    );
    assert!(!serialized.contains("sentinel-secret-value"));
}

#[test]
fn project_env_keys_reads_non_empty_dotenv_keys() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join(".env"),
        "OPENAI_API_KEY=sk-test\nEMPTY=\n# ANTHROPIC_API_KEY=skip\n",
    )
    .unwrap();
    let keys = project_env_keys(root.path());
    assert!(keys.contains("OPENAI_API_KEY"));
    assert!(!keys.contains("EMPTY"));
    assert!(!keys.contains("ANTHROPIC_API_KEY"));
}

#[test]
fn project_digest_changes_when_agent_changes() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    let agent = root.path().join("agent.ts");
    fs::write(&agent, "export const agent = 1;\n").unwrap();
    let project = test_project(root.path(), Language::TypeScript);
    let inputs = ProjectDigestInputs {
        model_override: Some("openai:gpt-5-mini"),
        runtime_source: &root.path().join("node_modules/managed-deepagents/dist"),
        langgraph_api_version: "0.12.0rc9",
        python_version: None,
        source_revision: None,
        dirty: false,
    };
    let first = project_digest(&project, &inputs).unwrap();
    fs::write(&agent, "export const agent = 2;\n").unwrap();
    let second = project_digest(&project, &inputs).unwrap();
    assert_ne!(first, second);
}

#[test]
fn project_digest_includes_the_python_deployment_target() {
    let root = tempfile::tempdir().unwrap();
    crate::test_support::write_file(root.path(), "agent.py", "agent = 1\n");
    let project = test_project(root.path(), Language::Python);
    let mut inputs = ProjectDigestInputs {
        model_override: None,
        runtime_source: root.path(),
        langgraph_api_version: "0.16.0.dev1",
        python_version: Some("3.11"),
        source_revision: None,
        dirty: false,
    };
    let first = project_digest(&project, &inputs).unwrap();
    inputs.python_version = Some("3.14");
    assert_ne!(first, project_digest(&project, &inputs).unwrap());
}

#[test]
fn looks_like_project_root_detects_agent_entry() {
    let root = tempfile::tempdir().unwrap();
    assert!(!looks_like_project_root(root.path()));
    fs::write(root.path().join("agent.py"), "agent = 1\n").unwrap();
    assert!(looks_like_project_root(root.path()));
}
