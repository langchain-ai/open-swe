use std::fs;
use std::path::Path;
use std::process::{Command, Output, Stdio};

#[derive(Clone, Copy)]
enum TestChannel {
    Npm,
    PyPi,
}

fn run_init(root: &Path, args: &[&str], channel: TestChannel, inside_agent: bool) -> Output {
    let runtime = root.join("runtime");
    let install_channel = match channel {
        TestChannel::Npm => {
            fs::create_dir_all(runtime.join("runtime")).unwrap();
            fs::write(runtime.join("runtime/index.js"), "").unwrap();
            "npm"
        }
        TestChannel::PyPi => {
            fs::create_dir_all(&runtime).unwrap();
            fs::write(runtime.join("runtime.py"), "").unwrap();
            "pypi"
        }
    };

    let mut command = Command::new(env!("CARGO_BIN_EXE_mda"));
    command
        .arg("init")
        .args(args)
        .current_dir(root)
        .env_clear()
        .env("MDA_INSTALL_CHANNEL", install_channel)
        .env("MDA_BUNDLED_RUNTIME_PATH", &runtime)
        .env("LANG", "C.UTF-8")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null());
    if inside_agent {
        command.env("DEEPAGENTS_CODE_SERVER_INTERACTIVE", "true");
    }
    command.output().unwrap()
}

fn output_text(output: &Output) -> String {
    format!(
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

#[test]
fn bare_init_without_a_tty_requires_a_name_for_headless_use() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(root.path(), &[], TestChannel::PyPi, false);
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(!output.status.success(), "{}", output_text(&output));
    assert!(
        stderr
            .contains("project name is required without a terminal; run `mda init <name>` instead"),
        "{}",
        output_text(&output)
    );
    assert!(!stderr.contains("⡷⢾"), "{}", output_text(&output));
    assert!(!stderr.contains("┌  v"), "{}", output_text(&output));
}

#[test]
fn interactive_init_without_a_tty_fails_before_scaffolding() {
    let root = tempfile::tempdir().unwrap();

    for args in [&["-i"][..], &["--interactive", "my-agent"][..]] {
        let output = run_init(root.path(), args, TestChannel::PyPi, false);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(!output.status.success(), "{}", output_text(&output));
        assert!(
            stderr.contains("coding-agent handoff requires a terminal; omit `-i` to disable it"),
            "{}",
            output_text(&output)
        );
        assert!(!stderr.contains("⡷⢾"), "{}", output_text(&output));
        assert!(!stderr.contains("┌  v"), "{}", output_text(&output));
        assert!(!root.path().join("my-agent").exists());
    }
}

#[test]
fn invalid_explicit_interactive_name_fails_before_the_presentation() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(
        root.path(),
        &["--interactive", "1bad"],
        TestChannel::PyPi,
        false,
    );
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(!output.status.success(), "{}", output_text(&output));
    assert!(
        stderr.contains("project name \"1bad\" is not a valid agent name"),
        "{}",
        output_text(&output)
    );
    assert!(!stderr.contains("⡷⢾"), "{}", output_text(&output));
    assert!(!stderr.contains("┌  v"), "{}", output_text(&output));
    assert!(!root.path().join("1bad").exists());
}

#[test]
fn named_init_inside_a_coding_agent_prints_python_next_steps() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(root.path(), &["my-agent"], TestChannel::PyPi, true);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(output.status.success(), "{}", output_text(&output));
    assert!(root.path().join("my-agent/agent.py").is_file());
    assert!(stderr.contains("│  Created my-agent/\n│\n◇  Get started\n"));
    assert!(
        stderr.contains(concat!(
            "│  Docs: https://docs.langchain.com/langsmith/python/",
            "managed-deep-agents-quickstart"
        )),
        "{}",
        output_text(&output)
    );
    assert!(
        stderr.contains("├  Use a coding agent to build your agent. Give it this prompt:"),
        "{}",
        output_text(&output)
    );
    assert!(!stderr.contains("◇  Use a coding agent to build your agent"));
    assert!(
        stderr.contains("npx skills add"),
        "{}",
        output_text(&output)
    );
    assert!(!stderr.contains("Quickstart"), "{}", output_text(&output));
    let expected_completion = concat!(
        "◇  Deploy when ready\n",
        "│  cd my-agent\n",
        "│  uv sync\n",
        "└  mda deploy\n\n"
    );
    assert!(
        stderr.contains("╯\n│\n◇  Deploy when ready\n"),
        "{}",
        output_text(&output)
    );
    assert!(
        stderr.ends_with(expected_completion),
        "{}",
        output_text(&output)
    );
    assert!(stdout.is_empty(), "{}", output_text(&output));
}

#[test]
fn named_typescript_init_prints_javascript_get_started_and_npm_deploy_steps() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(root.path(), &["my-agent"], TestChannel::Npm, false);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let expected_completion = concat!(
        "◇  Deploy when ready\n",
        "│  cd my-agent\n",
        "│  npm install\n",
        "└  mda deploy\n\n"
    );

    assert!(output.status.success(), "{}", output_text(&output));
    assert!(root.path().join("my-agent/agent.ts").is_file());
    assert!(
        stderr.contains("◇  Get started\n"),
        "{}",
        output_text(&output)
    );
    assert!(stderr.contains(concat!(
        "│  Docs: https://docs.langchain.com/langsmith/javascript/",
        "managed-deep-agents-quickstart"
    )));
    assert!(
        stderr.contains("├  Use a coding agent to build your agent. Give it this prompt:"),
        "{}",
        output_text(&output)
    );
    assert!(!stderr.contains("◇  Use a coding agent to build your agent"));
    assert!(
        stderr.ends_with(expected_completion),
        "{}",
        output_text(&output)
    );
    assert!(
        stderr.contains("npx skills add"),
        "{}",
        output_text(&output)
    );
}

#[test]
fn named_init_accepts_channel_flags_and_writes_slack() {
    let flags = [
        ["-c", "slack"].as_slice(),
        ["--channel", "slack"].as_slice(),
        ["--channels", "slack"].as_slice(),
    ];

    for channel_args in flags {
        let root = tempfile::tempdir().unwrap();
        let mut args = vec!["my-agent"];
        args.extend(channel_args.iter().copied());

        let output = run_init(root.path(), &args, TestChannel::PyPi, false);
        let slack = root.path().join("my-agent/channels/slack.py");

        assert!(output.status.success(), "{}", output_text(&output));
        assert!(
            slack.is_file(),
            "missing Slack channel for {channel_args:?}\n{}",
            output_text(&output)
        );
        let contents = fs::read_to_string(&slack).unwrap();
        assert!(
            contents.contains("channel = channels.slack()"),
            "{channel_args:?}: {contents}"
        );
    }
}

#[test]
fn named_typescript_init_with_channel_flag_writes_slack() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(
        root.path(),
        &["./myAgent", "--channel", "slack"],
        TestChannel::Npm,
        false,
    );

    assert!(output.status.success(), "{}", output_text(&output));
    assert!(root.path().join("myAgent/channels/slack.ts").is_file());
}

#[test]
fn init_starts_with_the_final_wordmark_immediately_before_the_clack_intro() {
    let root = tempfile::tempdir().unwrap();

    let output = run_init(root.path(), &["my-agent"], TestChannel::PyPi, false);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.starts_with(concat!(
            "\n",
            " ⡷⢾ ⣎⣱ ⡷⣸ ⣎⣱ ⡎⠑ ⣏⡉ ⡏⢱           \n",
            " ⠇⠸ ⠇⠸ ⠇⠹ ⠇⠸ ⠣⠝ ⠧⠤ ⠧⠜           \n",
            " ⡏⢱ ⣏⡉ ⣏⡉ ⣏⡱   ⣎⣱ ⡎⠑ ⣏⡉ ⡷⣸ ⢹⠁ ⢎⡑\n",
            " ⠧⠜ ⠧⠤ ⠧⠤ ⠇    ⠇⠸ ⠣⠝ ⠧⠤ ⠇⠹ ⠸  ⠢⠜\n",
            "┌  v0.0.1\n",
            "│\n",
        )),
        "{}",
        output_text(&output)
    );
}
