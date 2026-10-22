//! A terminated `mda` takes the processes it started down with it.
//!
//! Agents and CI stop a command that runs too long with SIGTERM, and a closed
//! terminal sends SIGHUP. The dev server runs in its own process group, so
//! neither reaches it unless `mda` forwards the stop — otherwise it outlives
//! `mda` and keeps holding the port.
#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

/// A `uv` that succeeds at everything and, as `langgraph dev`, starts a server
/// that ignores INT and TERM — so only a group kill from `mda` can end it.
const FAKE_UV: &str = r#"#!/bin/sh
case "$*" in
  *"langgraph dev"*)
    sh -c 'trap "" INT TERM; echo $$ > "$MDA_TEST_SERVER_PID"; exec sleep 300' &
    wait
    ;;
esac
exit 0
"#;

fn write_project(root: &Path) {
    fs::write(
        root.join("agent.py"),
        "from managed_deepagents import define_deep_agent\n\n\
         agent = define_deep_agent(name=\"terminate-probe\", model=\"openai:gpt-5\")\n",
    )
    .unwrap();
    fs::write(root.join("instructions.md"), "Be brief.\n").unwrap();
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname = \"terminate-probe\"\nversion = \"0.0.0\"\n\
         requires-python = \">=3.11\"\ndependencies = [\"managed-deepagents\"]\n",
    )
    .unwrap();
}

fn write_fake_uv(bin: &Path) {
    let uv = bin.join("uv");
    fs::write(&uv, FAKE_UV).unwrap();
    fs::set_permissions(&uv, fs::Permissions::from_mode(0o755)).unwrap();
}

fn bundled_python_runtime() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../packages/pypi/src/managed_deepagents")
}

fn signal(pid: u32, signal: &str) -> bool {
    Command::new("/bin/kill")
        .args([signal, &pid.to_string()])
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn is_running(pid: u32) -> bool {
    let Ok(out) = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
    else {
        return false;
    };
    let stat = String::from_utf8_lossy(&out.stdout);
    let stat = stat.trim();
    !stat.is_empty() && !stat.starts_with('Z')
}

fn wait_for<T>(timeout: Duration, mut probe: impl FnMut() -> Option<T>) -> Option<T> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(value) = probe() {
            return Some(value);
        }
        if Instant::now() >= deadline {
            return None;
        }
        sleep(Duration::from_millis(100));
    }
}

/// Kills whatever the test left running, pass or fail.
struct Cleanup {
    mda: Child,
    server: Option<u32>,
}

impl Drop for Cleanup {
    fn drop(&mut self) {
        let _ = self.mda.kill();
        let _ = self.mda.wait();
        if let Some(server) = self.server {
            signal(server, "-KILL");
        }
    }
}

fn exit_of(child: &mut Child, timeout: Duration) -> Option<ExitStatus> {
    wait_for(timeout, || child.try_wait().ok().flatten())
}

fn assert_signal_stops_the_dev_server(name: &str, expected_code: i32) {
    let project = tempfile::tempdir().unwrap();
    let bin = tempfile::tempdir().unwrap();
    write_project(project.path());
    write_fake_uv(bin.path());
    let pid_file = bin.path().join("server.pid");

    let mda = Command::new(env!("CARGO_BIN_EXE_mda"))
        .args(["dev", "--no-browser"])
        .current_dir(project.path())
        .env_clear()
        .env("PATH", format!("{}:/usr/bin:/bin", bin.path().display()))
        .env("HOME", project.path())
        .env("LANG", "C.UTF-8")
        .env("NO_COLOR", "1")
        .env("DO_NOT_TRACK", "1")
        .env("MDA_INSTALL_CHANNEL", "pypi")
        .env("MDA_BUNDLED_RUNTIME_PATH", bundled_python_runtime())
        .env("MDA_TEST_SERVER_PID", &pid_file)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut cleanup = Cleanup { mda, server: None };

    let server = wait_for(Duration::from_secs(60), || {
        fs::read_to_string(&pid_file)
            .ok()?
            .trim()
            .parse::<u32>()
            .ok()
    })
    .expect("`mda dev` never started the dev server");
    cleanup.server = Some(server);
    assert!(is_running(server));

    assert!(signal(cleanup.mda.id(), name), "failed to send {name}");

    let status = exit_of(&mut cleanup.mda, Duration::from_secs(10))
        .unwrap_or_else(|| panic!("`mda dev` did not exit after {name}"));
    assert_eq!(
        status.code(),
        Some(expected_code),
        "`mda dev` should exit {expected_code} after {name}, got {status:?}"
    );
    assert!(
        wait_for(Duration::from_secs(5), || (!is_running(server))
            .then_some(()))
        .is_some(),
        "the dev server outlived `mda dev` after {name}"
    );
}

#[test]
fn sigterm_stops_the_dev_server() {
    assert_signal_stops_the_dev_server("-TERM", 143);
}

#[test]
fn sighup_stops_the_dev_server() {
    assert_signal_stops_the_dev_server("-HUP", 129);
}
