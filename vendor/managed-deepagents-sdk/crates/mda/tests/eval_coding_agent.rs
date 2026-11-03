use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const HARBOR_RUN_COMMAND: &str = concat!(
    "HARBOR_LANGSMITH_DATASET=mda-agent-project-evals \\\n",
    "PYTHONPATH=.mda/evals/harbor-adapter \\\n",
    "uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor run \\\n",
    "  --config evals/harbor-job.json --yes \\\n",
    "  --plugin mda_harbor.job_plugin:MDAJobPlugin \\\n",
    "  --plugin mda_harbor.langsmith_plugin:LangSmithPlugin",
);
const EVAL_PROMPT_START: &str =
    "Create Harbor eval tasks under evals/ using the eval-engineering skill:";
const EVAL_NOTE_TITLE: &str = "Use a coding agent to create evals. Give it this prompt:";

fn create_project(root: &Path) -> PathBuf {
    let project = root.join("agent-project");
    fs::create_dir_all(&project).unwrap();
    fs::write(project.join("agent.py"), "agent = object()\n").unwrap();
    project
}

fn run_eval_init(root: &Path, project: &Path, inside_agent: bool) -> Output {
    run_eval_init_with_args(root, project, &[], inside_agent)
}

fn run_eval_init_with_args(
    root: &Path,
    project: &Path,
    args: &[&str],
    inside_agent: bool,
) -> Output {
    let runtime = root.join("runtime");
    fs::create_dir_all(&runtime).unwrap();
    fs::write(runtime.join("runtime.py"), "").unwrap();

    let mut command = Command::new(env!("CARGO_BIN_EXE_mda"));
    command
        .args(["evals", "init"])
        .args(args)
        .current_dir(project)
        .env_clear()
        .env("MDA_INSTALL_CHANNEL", "pypi")
        .env("MDA_BUNDLED_RUNTIME_PATH", &runtime)
        .env("MDA_RUNTIME_VERSION", "0.5.2")
        .env("LANG", "C.UTF-8")
        .env("MDA_TEST_SECRET", "must-not-appear")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null());
    if inside_agent {
        command.env("DEEPAGENTS_CODE_SERVER_INTERACTIVE", "true");
    }
    command.output().unwrap()
}

fn assert_success(output: &Output) {
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn write_legacy_compile_job_config(project: &Path) -> PathBuf {
    let project_root = project.canonicalize().unwrap();
    fs::create_dir_all(project.join("evals")).unwrap();
    let job = project.join("evals/harbor-job.json");
    let legacy = serde_json::json!({
        "job_name": "mda-eval",
        "jobs_dir": project_root.join("evals/harbor-jobs/1720000000000"),
        "n_attempts": 3,
        "n_concurrent_trials": 1,
        "quiet": false,
        "environment": { "type": "docker" },
        "agents": [{
            "import_path": "mda_harbor.agent:ManagedDeepAgent",
            "model_name": "openai:gpt-5-mini",
            "kwargs": {
                "artifact_path": project_root.join("evals/artifact"),
                "artifact_sha256": "0123456789abcdef",
                "runtime": "auto",
                "adapter_protocol_version": 1,
                "graph": "research-agent"
            },
            "env": { "CUSTOM_SETTING": "keep-me" },
            "include_logs": ["**/*"]
        }],
        "datasets": [{ "path": project_root.join("evals/tasks") }],
        "verifier": { "include_logs": ["**/*"] },
        "custom_metadata": { "owner": "eval-team" }
    });
    fs::write(
        &job,
        format!("{}\n", serde_json::to_string_pretty(&legacy).unwrap()),
    )
    .unwrap();
    job
}

#[test]
fn eval_init_creates_authored_and_generated_workspace_layout() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, true);

    assert_success(&output);
    assert!(project.join("evals").is_dir());
    assert!(project.join("evals/harbor-job.json").is_file());
    assert!(project
        .join(".mda/evals/harbor-adapter/mda_harbor/agent.py")
        .is_file());
    assert!(project
        .join(".mda/evals/harbor-adapter/mda_harbor/langsmith_plugin.py")
        .is_file());
    assert!(project.join(".mda/evals/runtime.json").is_file());
}

#[test]
fn eval_init_does_not_create_legacy_task_directories() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, true);

    assert_success(&output);
    assert!(!project.join("evals/scaffold").exists());
    assert!(!project.join("evals/tasks").exists());
}

#[test]
fn eval_init_rejects_a_positional_task_name() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init_with_args(root.path(), &project, &["test-task"], true);

    assert!(!output.status.success());
    assert!(!project.join("evals").exists());
}

#[test]
fn eval_init_preserves_user_edited_job_config() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let first = run_eval_init(root.path(), &project, true);
    assert_success(&first);
    let job = project.join("evals/harbor-job.json");
    fs::write(&job, "user-owned sentinel\n").unwrap();

    let second = run_eval_init(root.path(), &project, true);

    assert_success(&second);
    assert_eq!(fs::read_to_string(job).unwrap(), "user-owned sentinel\n");
}

#[test]
fn eval_init_rejects_directory_at_job_config_path() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    fs::create_dir_all(project.join("evals/harbor-job.json")).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_job_config_symlink_without_touching_target() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    fs::create_dir_all(project.join("evals")).unwrap();
    let sentinel = root.path().join("outside-job-config.json");
    fs::write(&sentinel, "outside sentinel\n").unwrap();
    std::os::unix::fs::symlink(&sentinel, project.join("evals/harbor-job.json")).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "outside sentinel\n");
}

#[test]
fn eval_init_preserves_authored_task_files() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let task = project.join("evals/my-task");
    fs::create_dir_all(&task).unwrap();
    let sentinel = task.join("author-owned.txt");
    fs::write(&sentinel, "keep me\n").unwrap();

    let first = run_eval_init(root.path(), &project, true);
    let second = run_eval_init(root.path(), &project, true);

    assert_success(&first);
    assert_success(&second);
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "keep me\n");
}

#[test]
fn eval_init_refreshes_generated_adapter_files() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let first = run_eval_init(root.path(), &project, true);
    assert_success(&first);
    let adapter = project.join(".mda/evals/harbor-adapter/mda_harbor/agent.py");
    let job_plugin = project.join(".mda/evals/harbor-adapter/mda_harbor/job_plugin.py");
    let langsmith_plugin = project.join(".mda/evals/harbor-adapter/mda_harbor/langsmith_plugin.py");
    fs::write(&adapter, "generated sentinel\n").unwrap();
    fs::write(&job_plugin, "generated plugin sentinel\n").unwrap();
    fs::write(&langsmith_plugin, "generated LangSmith sentinel\n").unwrap();

    let second = run_eval_init(root.path(), &project, true);

    assert_success(&second);
    assert_ne!(fs::read_to_string(adapter).unwrap(), "generated sentinel\n");
    assert_ne!(
        fs::read_to_string(job_plugin).unwrap(),
        "generated plugin sentinel\n"
    );
    assert_ne!(
        fs::read_to_string(langsmith_plugin).unwrap(),
        "generated LangSmith sentinel\n"
    );
}

#[test]
fn eval_init_writes_only_canonical_non_secret_runtime_settings() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, true);

    assert_success(&output);
    let settings: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(project.join(".mda/evals/runtime.json")).unwrap())
            .unwrap();
    assert_eq!(settings.as_object().unwrap().len(), 5);
    assert_eq!(
        settings["project_root"].as_str().map(Path::new),
        Some(project.canonicalize().unwrap().as_path())
    );
    assert_eq!(
        settings["mda_executable"].as_str().map(Path::new),
        Some(
            Path::new(env!("CARGO_BIN_EXE_mda"))
                .canonicalize()
                .unwrap()
                .as_path()
        )
    );
    assert_eq!(settings["install_channel"].as_str(), Some("pypi"));
    assert_eq!(settings["runtime_version"].as_str(), Some("0.5.2"));
    assert_eq!(
        settings["bundled_runtime_path"].as_str().map(Path::new),
        Some(
            root.path()
                .join("runtime")
                .canonicalize()
                .unwrap()
                .as_path()
        )
    );
    assert!(!settings.to_string().contains("must-not-appear"));
}

#[test]
fn eval_init_writes_relocatable_user_job_config_paths_without_compile_metadata() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, true);

    assert_success(&output);
    let config: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(project.join("evals/harbor-job.json")).unwrap())
            .unwrap();
    assert_eq!(config["datasets"][0]["path"].as_str(), Some("evals"));
    assert_eq!(
        config["agents"][0]["kwargs"]["artifact_path"].as_str(),
        Some(".mda/evals/artifact")
    );
    assert_eq!(config["jobs_dir"].as_str(), Some(".mda/evals/jobs"));
    assert!(config.get("job_name").is_none());
    assert!(config.get("plugins").is_none());
    assert!(config["agents"][0].get("model_name").is_none());
    assert!(config["agents"][0]["kwargs"]
        .get("artifact_sha256")
        .is_none());
    assert!(config["agents"][0]["kwargs"].get("graph").is_none());
    assert_eq!(
        config["agents"][0]["env"]["OPENAI_API_KEY"].as_str(),
        Some("${OPENAI_API_KEY:-}")
    );
    assert_eq!(
        config["agents"][0]["env"]["LANGSMITH_GATEWAY_API_KEY"].as_str(),
        Some("${LANGSMITH_GATEWAY_API_KEY:-}")
    );
}

#[test]
fn eval_init_job_config_paths_resolve_inside_a_moved_project() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let output = run_eval_init(root.path(), &project, true);
    assert_success(&output);
    let moved_project = root.path().join("moved-agent-project");
    fs::rename(&project, &moved_project).unwrap();
    let config: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(moved_project.join("evals/harbor-job.json")).unwrap(),
    )
    .unwrap();

    let resolved = [
        moved_project.join(config["jobs_dir"].as_str().unwrap()),
        moved_project.join(
            config["agents"][0]["kwargs"]["artifact_path"]
                .as_str()
                .unwrap(),
        ),
        moved_project.join(config["datasets"][0]["path"].as_str().unwrap()),
    ];

    assert_eq!(
        resolved,
        [
            moved_project.join(".mda/evals/jobs"),
            moved_project.join(".mda/evals/artifact"),
            moved_project.join("evals"),
        ]
    );
}

#[test]
fn eval_init_rejects_a_job_config_from_mda_0_5_with_the_fix() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let job = write_legacy_compile_job_config(&project);
    let original = fs::read_to_string(&job).unwrap();
    let legacy_task = project.join("evals/tasks/legacy-task");
    fs::create_dir_all(&legacy_task).unwrap();
    fs::write(legacy_task.join("author-owned.txt"), "keep me\n").unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("generated by mda 0.5 or earlier"),
        "{stderr}"
    );
    assert!(
        stderr.contains("move evals/tasks/* into evals/, then re-run `mda evals init`"),
        "{stderr}"
    );
    assert_eq!(fs::read_to_string(job).unwrap(), original);
    assert_eq!(
        fs::read_to_string(legacy_task.join("author-owned.txt")).unwrap(),
        "keep me\n"
    );
    assert!(!project.join(".mda/evals").exists());
}

#[test]
fn eval_init_inside_coding_agent_uses_noninteractive_output() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, true);

    assert_success(&output);
    assert!(project.join(".mda/evals/runtime.json").is_file());
    assert!(output.stdout.is_empty());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains(EVAL_NOTE_TITLE));
    assert!(stderr.contains(EVAL_PROMPT_START));
    assert!(!stderr.contains(HARBOR_RUN_COMMAND));
}

#[test]
fn eval_init_noninteractive_prints_one_guidance_block_to_stderr() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init(root.path(), &project, false);

    assert_success(&output);
    assert!(project.join(".mda/evals/runtime.json").is_file());
    assert!(output.stdout.is_empty());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.starts_with(concat!(
        "\n◇  Eval workspace initialized\n",
        "│  dataset:   evals\n",
        "│  config:    evals/harbor-job.json\n",
        "│  \n",
    )));
    assert!(stderr.contains(concat!(
        "◇  Get started\n",
        "│  Docs: https://docs.langchain.com/langsmith/python/managed-deep-agents-evals\n",
        "│  \n",
    )));
    assert!(stderr.contains("├  Use a coding agent to create evals. Give it this prompt:"));
    assert!(stderr.contains(EVAL_PROMPT_START));
    assert!(stderr.ends_with("╯\n\n"));
    assert!(stderr.trim_end().lines().last().unwrap().starts_with('└'));
    assert!(!stderr.contains(HARBOR_RUN_COMMAND));
}

#[test]
fn eval_init_interactive_without_tty_fails_before_creating_workspace() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());

    let output = run_eval_init_with_args(root.path(), &project, &["--interactive"], false);

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("coding-agent handoff requires a terminal; omit `-i` to disable it"));
    assert!(!project.join("evals").exists());
    assert!(!project.join(".mda").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_dataset_symlink_outside_project() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let outside = root.path().join("outside-dataset");
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, project.join("evals")).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert!(!outside.join("harbor-job.json").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_generated_symlink_outside_project() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let outside = root.path().join("outside-generated");
    fs::create_dir_all(&outside).unwrap();
    fs::create_dir_all(project.join(".mda")).unwrap();
    std::os::unix::fs::symlink(&outside, project.join(".mda/evals")).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert!(!outside.join("runtime.json").exists());
    assert!(!outside.join("harbor-adapter").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_mda_parent_symlink_before_creating_outside_directory() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let outside = root.path().join("outside-mda");
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, project.join(".mda")).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert!(!outside.join("evals").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_runtime_file_symlink_without_modifying_target() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    let first = run_eval_init(root.path(), &project, true);
    assert_success(&first);
    let runtime_settings = project.join(".mda/evals/runtime.json");
    fs::remove_file(&runtime_settings).unwrap();
    let outside = root.path().join("outside-runtime.json");
    fs::write(&outside, "outside sentinel\n").unwrap();
    std::os::unix::fs::symlink(&outside, &runtime_settings).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert_eq!(fs::read_to_string(outside).unwrap(), "outside sentinel\n");
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_adapter_directory_symlink_before_writing_outside() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    fs::create_dir_all(project.join(".mda/evals")).unwrap();
    let adapter = project.join(".mda/evals/harbor-adapter");
    let outside = root.path().join("outside-adapter");
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, &adapter).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert!(fs::read_dir(outside).unwrap().next().is_none());
    assert!(!project.join("evals").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_adapter_file_symlink_without_modifying_target() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    fs::create_dir_all(project.join(".mda/evals/harbor-adapter/mda_harbor/runners")).unwrap();
    let agent = project.join(".mda/evals/harbor-adapter/mda_harbor/agent.py");
    let outside = root.path().join("outside-agent.py");
    fs::write(&outside, "outside sentinel\n").unwrap();
    std::os::unix::fs::symlink(&outside, &agent).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert_eq!(fs::read_to_string(outside).unwrap(), "outside sentinel\n");
    assert!(!project.join("evals").exists());
}

#[cfg(unix)]
#[test]
fn eval_init_rejects_langsmith_plugin_symlink_without_modifying_target() {
    let root = tempfile::tempdir().unwrap();
    let project = create_project(root.path());
    fs::create_dir_all(project.join(".mda/evals/harbor-adapter/mda_harbor/runners")).unwrap();
    let plugin = project.join(".mda/evals/harbor-adapter/mda_harbor/langsmith_plugin.py");
    let outside = root.path().join("outside-langsmith-plugin.py");
    fs::write(&outside, "outside sentinel\n").unwrap();
    std::os::unix::fs::symlink(&outside, &plugin).unwrap();

    let output = run_eval_init(root.path(), &project, true);

    assert!(!output.status.success());
    assert_eq!(fs::read_to_string(outside).unwrap(), "outside sentinel\n");
    assert!(!project.join("evals").exists());
}
