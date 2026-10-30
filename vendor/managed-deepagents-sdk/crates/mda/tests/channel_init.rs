use std::fs;
use std::path::Path;
use std::process::{Command, Output, Stdio};

fn run_channel_init(project: &Path) -> Output {
    run_channel_init_for(project, "slack")
}

fn run_channel_init_for(project: &Path, channel: &str) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mda"));
    command
        .args(["channels", "init", channel])
        .current_dir(project)
        .env_clear()
        .env("LANG", "C.UTF-8")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null());
    if let Some(path) = std::env::var_os("PATH") {
        command.env("PATH", path);
    }
    command.output().unwrap()
}

fn create_python_project(root: &Path) {
    fs::write(
        root.join("agent.py"),
        "agent = define_deep_agent(name=\"support-agent\")\n",
    )
    .unwrap();
}

#[test]
fn channel_init_reports_the_added_github_channel() {
    let project = tempfile::tempdir().unwrap();
    create_python_project(project.path());

    let output = run_channel_init_for(project.path(), "github");

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("GitHub channel added"));
    assert!(stderr.contains("channels/github.py"));
    assert!(stderr.contains("The next deploy sets up a GitHub App for your agent."));
    assert!(project.path().join("channels/github.py").exists());
}

#[test]
fn channel_init_reports_the_added_channel() {
    let project = tempfile::tempdir().unwrap();
    create_python_project(project.path());

    let output = run_channel_init(project.path());

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("Slack channel added"));
    assert!(stderr.contains("channels/slack.py"));
    assert!(stderr.contains("Your agent will be added to Slack on the next deploy."));
}
