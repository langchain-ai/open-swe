use std::process::{Command, Stdio};

#[test]
fn prepare_without_runner_credential_fails_before_loading_project() {
    let output = Command::new(env!("CARGO_BIN_EXE_mda"))
        .args([
            "prepare",
            "missing-project",
            "--deployment-id",
            "deployment",
            "--revision-id",
            "revision",
        ])
        .env_clear()
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("MDA_BUILD_ARTIFACTS_TOKEN is required"),
        "{stderr}"
    );
}

#[cfg(unix)]
#[test]
fn compiler_subprocess_does_not_inherit_revision_credential() {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().unwrap();
    let project = root.path().join("project");
    let runtime = root.path().join("runtime");
    let bin = root.path().join("bin");
    fs::create_dir_all(&project).unwrap();
    fs::create_dir_all(runtime.join("runtime")).unwrap();
    fs::create_dir_all(&bin).unwrap();
    fs::write(runtime.join("runtime/index.js"), "// test runtime\n").unwrap();
    fs::write(runtime.join("index.js"), "// test runtime\n").unwrap();
    fs::write(
        project.join("agent.ts"),
        r#"
import { defineDeepAgent } from "managed-deepagents";
export const agent = defineDeepAgent({name: "agent", model: "openai:gpt-5.5"});
"#,
    )
    .unwrap();
    fs::write(
        project.join("package.json"),
        r#"{"name":"agent","version":"1.0.0","type":"module"}"#,
    )
    .unwrap();
    let npm = bin.join("npm");
    fs::write(
        &npm,
        r#"#!/bin/sh
if [ "$1" = "--version" ]; then
    printf '10.0.0\n'
    exit 0
fi
if [ "${MDA_BUILD_ARTIFACTS_TOKEN+set}" = set ]; then
    printf 'leaked' > "$PROBE_FILE"
else
    printf 'absent:%s' "$APP_SETTING" > "$PROBE_FILE"
fi
exit 1
"#,
    )
    .unwrap();
    fs::set_permissions(&npm, fs::Permissions::from_mode(0o755)).unwrap();
    let probe = root.path().join("probe");
    let output = Command::new(env!("CARGO_BIN_EXE_mda"))
        .arg("build")
        .arg(&project)
        .env_clear()
        .env("PATH", &bin)
        .env("MDA_INSTALL_CHANNEL", "npm")
        .env("MDA_BUNDLED_RUNTIME_PATH", &runtime)
        .env("MDA_BUILD_ARTIFACTS_TOKEN", "test-revision-credential")
        .env("APP_SETTING", "preserved")
        .env("PROBE_FILE", &probe)
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(
        fs::read_to_string(&probe).unwrap_or_else(|_| panic!(
            "compiler subprocess was not reached: {}",
            String::from_utf8_lossy(&output.stderr)
        )),
        "absent:preserved"
    );
    assert!(!String::from_utf8_lossy(&output.stderr).contains("test-revision-credential"));
    assert!(!String::from_utf8_lossy(&output.stdout).contains("test-revision-credential"));
}
