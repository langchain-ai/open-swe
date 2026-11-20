use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

fn fixture(case: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src/fleet_export/testdata")
        .join(case)
}

fn run_init(root: &Path, install_channel: &str, args: &[&str]) -> Output {
    let runtime = root.join("runtime");
    fs::create_dir_all(runtime.join("runtime")).unwrap();
    fs::write(runtime.join("runtime.py"), "").unwrap();
    fs::write(runtime.join("runtime/index.js"), "").unwrap();
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
    if let Some(path) = std::env::var_os("PATH") {
        command.env("PATH", path);
    }
    command.output().unwrap()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

/// Copy a fixture's `agent.json` into a scratch snapshot directory, so the
/// `blueprint.json` export writes next to it stays out of the source tree.
fn snapshot(root: &Path, case: &str) -> PathBuf {
    let dir = root.join(".data").join(case);
    fs::create_dir_all(&dir).unwrap();
    fs::copy(fixture(case).join("agent.json"), dir.join("agent.json")).unwrap();
    dir
}

#[test]
fn from_snapshot_writes_the_project_blueprint_and_report() {
    let root = tempfile::tempdir().unwrap();
    let dir = snapshot(root.path(), "minimal");

    let output = run_init(
        root.path(),
        "pypi",
        &["demo", "--from-snapshot", dir.to_str().unwrap()],
    );

    assert!(output.status.success(), "{}", stderr(&output));
    let project = root.path().join("demo");
    assert_eq!(
        fs::read_to_string(project.join("instructions.md")).unwrap(),
        "You are a helpful research assistant.\n"
    );
    let agent = fs::read_to_string(project.join("agent.py")).unwrap();
    assert!(agent.contains("anthropic:claude-sonnet-4-6"));
    assert!(!project.join("sandbox").exists());
    assert_eq!(
        fs::read_to_string(dir.join("blueprint.json")).unwrap(),
        fs::read_to_string(fixture("minimal").join("blueprint.json")).unwrap()
    );
    let report = fs::read_to_string(project.join("FLEET_EXPORT.md")).unwrap();
    assert!(report.contains("## Not exported\n\n- skills: Fleet's built-in skills"));
    assert!(report.contains("## Left behind"));
    assert!(!stderr(&output).contains("Model:"));
    assert!(stderr(&output)
        .contains("Left behind in Fleet:\n│  - Conversations: past threads stay in Fleet."));
    assert!(stderr(&output).contains(
        "◇  Check the export\n│  demo/FLEET_EXPORT.md lists the steps you need to take before your first deploy\n"
    ));
    assert!(stderr(&output).contains("◇  Run locally\n│  cd demo\n│  uv sync\n│  mda dev\n"));
    assert!(stderr(&output).contains("◇  Deploy when ready\n└  mda deploy\n"));
    assert!(!stderr(&output).contains("Use a coding agent"));
}

#[test]
fn features_export_cannot_carry_are_listed_and_the_rest_is_written() {
    let root = tempfile::tempdir().unwrap();
    let dir = snapshot(root.path(), "not-exported-all");

    let output = run_init(
        root.path(),
        "pypi",
        &["demo", "--from-snapshot", dir.to_str().unwrap()],
    );

    assert!(output.status.success(), "{}", stderr(&output));
    assert!(stderr(&output).contains(
        "Not exported: MCP servers (1 not in this workspace), skills (Fleet's built-in skills), linked skills (1), schedules (1), middleware (1)"
    ));
    let project = root.path().join("demo");
    assert!(fs::read_to_string(project.join("agent.py"))
        .unwrap()
        .contains("openai:gpt-6-luna"));
    assert!(stderr(&output).contains("Model: the Fleet agent runs on Fleet's default model"));
    let report = fs::read_to_string(project.join("FLEET_EXPORT.md")).unwrap();
    assert!(report.contains("## Not exported\n\n- MCP servers: "));
    assert!(report.contains("- schedules: schedules aren't exported yet: 0 9 * * 1-5\n"));
}

#[test]
fn npm_cli_exports_a_typescript_project() {
    let root = tempfile::tempdir().unwrap();
    let dir = snapshot(root.path(), "minimal");

    let output = run_init(
        root.path(),
        "npm",
        &["demo", "--from-snapshot", dir.to_str().unwrap()],
    );

    assert!(output.status.success(), "{}", stderr(&output));
    let project = root.path().join("demo");
    let agent = fs::read_to_string(project.join("agent.ts")).unwrap();
    assert!(agent.contains("anthropic:claude-sonnet-4-6"));
    assert_eq!(
        fs::read_to_string(project.join("instructions.md")).unwrap(),
        "You are a helpful research assistant.\n"
    );
    assert!(project.join("FLEET_EXPORT.md").exists());
}

#[test]
fn mcp_servers_and_approvals_are_written_in_both_languages() {
    for (channel, mcp_file, agent_file, approval) in [
        (
            "pypi",
            "tools/mcp.py",
            "agent.py",
            "\"notion_create_page\": {\"allowed_decisions\": [",
        ),
        (
            "npm",
            "tools/mcp.ts",
            "agent.ts",
            "\"notion_create_page\": { allowedDecisions: [",
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        let dir = snapshot(root.path(), "workspace-servers");

        let output = run_init(
            root.path(),
            channel,
            &["demo", "--from-snapshot", dir.to_str().unwrap()],
        );

        assert!(output.status.success(), "{channel}: {}", stderr(&output));
        let project = root.path().join("demo");
        let mcp = fs::read_to_string(project.join(mcp_file)).unwrap();
        assert!(
            mcp.contains("connections.get(\"notion\""),
            "{channel}: {mcp}"
        );
        assert!(
            mcp.contains("\"https://weather.example.com/mcp\""),
            "{channel}: {mcp}"
        );
        let agent = fs::read_to_string(project.join(agent_file)).unwrap();
        assert!(agent.contains(approval), "{channel}: {agent}");
        // Agent-owned connections can only be created after the first deploy.
        let comment = if channel == "pypi" { "# " } else { "// " };
        // The workspace already has a secret `acme`, so the new agent-owned
        // connection takes the next free slug.
        assert!(
            mcp.contains(&format!("{comment}\"acme-2\": {{")),
            "{channel}: {mcp}"
        );
        // Only an OAuth connection is reused; `github` is a secret.
        assert!(
            mcp.contains("connections.get(\"github-2\""),
            "{channel}: {mcp}"
        );
        let report = fs::read_to_string(project.join("FLEET_EXPORT.md")).unwrap();
        assert!(report.contains("## Connections to create"));
        assert!(!report.contains("`notion` (https://mcp.notion.com/mcp)"));
        assert!(report.contains("mda connections create acme-2 --secret-from-env ACME_2_TOKEN"));
        let stderr = stderr(&output);
        assert!(stderr.contains("uses your existing `notion` connection"));
        assert!(stderr
            .contains("◇  After your first deploy\n│  acme-2: set `ACME_2_TOKEN` to the token"));
        assert!(!stderr.contains("Approvals: check which tools"));
        assert!(mcp.contains("\"create_page\","), "{channel}: {mcp}");
    }
}

#[test]
fn subagents_and_their_mcp_definitions_are_written_in_both_languages() {
    for (channel, agent_file, mcp_file, import, own, entry) in [
        (
            "pypi",
            "agent.py",
            "tools/mcp.py",
            "from tools.mcp import mcp, web_researcher_subagent_mcp, issue_triager_subagent_mcp\n",
            "web_researcher_subagent_mcp = define_mcp(\n",
            "\"mcp\": [web_researcher_subagent_mcp],",
        ),
        (
            "npm",
            "agent.ts",
            "tools/mcp.ts",
            "import { mcp, webResearcherSubagentMcp, issueTriagerSubagentMcp } from \"./tools/mcp\";\n",
            "export const webResearcherSubagentMcp = defineMcp({\n",
            "mcp: [webResearcherSubagentMcp],",
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        let dir = snapshot(root.path(), "subagents");

        let output = run_init(
            root.path(),
            channel,
            &["demo", "--from-snapshot", dir.to_str().unwrap()],
        );

        assert!(output.status.success(), "{channel}: {}", stderr(&output));
        let project = root.path().join("demo");
        let agent = fs::read_to_string(project.join(agent_file)).unwrap();
        assert!(agent.contains(import), "{channel}: {agent}");
        assert!(agent.contains(entry), "{channel}: {agent}");
        let mcp = fs::read_to_string(project.join(mcp_file)).unwrap();
        assert!(mcp.contains(own), "{channel}: {mcp}");
        assert!(stderr(&output).contains("tools (subagent `Web Researcher`: 1 Fleet built-in tools"));
    }
}

#[test]
fn sandbox_and_its_access_profiles_are_written_in_both_languages() {
    for (channel, sandbox_file, connection) in [
        (
            "pypi",
            "sandbox/__init__.py",
            r#"bearer(connections.get("google", {"type": "user"}))"#,
        ),
        (
            "npm",
            "sandbox/index.ts",
            r#"bearer(connections.get("google", { type: "user" }))"#,
        ),
    ] {
        let root = tempfile::tempdir().unwrap();
        let dir = snapshot(root.path(), "sandbox-policies");

        let output = run_init(
            root.path(),
            channel,
            &["demo", "--from-snapshot", dir.to_str().unwrap()],
        );

        assert!(output.status.success(), "{channel}: {}", stderr(&output));
        let sandbox = fs::read_to_string(root.path().join("demo").join(sandbox_file)).unwrap();
        assert!(sandbox.contains(connection), "{channel}: {sandbox}");
        assert!(
            sandbox.contains("\"Bearer {ACME_KEY}\""),
            "{channel}: {sandbox}"
        );
        assert!(
            sandbox.contains("\"allow_list\": ["),
            "{channel}: {sandbox}"
        );
        assert!(stderr(&output).contains(
            "Sandbox: Requests to api.github.com, github.com won't be signed in to GitHub."
        ));
        let project = root.path().join("demo");
        // The sandbox sees `LOG_LEVEL`, read from a prefixed `.env` key so it
        // stays out of the deployment's own environment.
        assert!(sandbox.contains("\"LOG_LEVEL\""), "{channel}: {sandbox}");
        assert!(
            sandbox.contains("\"SANDBOX_ENV_LOG_LEVEL\""),
            "{channel}: {sandbox}"
        );
        assert!(!sandbox.contains("debug"), "{channel}: {sandbox}");
        let env = fs::read_to_string(project.join(".env")).unwrap();
        assert!(
            env.contains("\nSANDBOX_ENV_LOG_LEVEL='debug'\n"),
            "{channel}: {env}"
        );
        assert!(!env.contains("\nLOG_LEVEL="), "{channel}: {env}");
        let report = fs::read_to_string(project.join("FLEET_EXPORT.md")).unwrap();
        assert!(
            report.contains("### `google`: before you deploy\n"),
            "{report}"
        );
        assert!(
            report.contains(
                "1. Register an OAuth app at https://console.cloud.google.com/apis/credentials.\n"
            ),
            "{report}"
        );
        assert!(
            report.contains("managed-deep-agents-connections"),
            "{report}"
        );
        assert!(
            report.contains(
                "## Sandbox\n\nHow sandboxes work: https://docs.langchain.com/langsmith/"
            ),
            "{report}"
        );
    }
}

#[test]
fn skills_and_memory_are_written_in_both_languages() {
    for (channel, memory_file, layers) in [
        ("pypi", "memory.py", "define_memory(agent=MemoryLayer())"),
        ("npm", "memory.ts", "defineMemory({ agent: memoryLayer() })"),
    ] {
        let root = tempfile::tempdir().unwrap();
        let dir = snapshot(root.path(), "skills-and-memory");

        let output = run_init(
            root.path(),
            channel,
            &["demo", "--from-snapshot", dir.to_str().unwrap()],
        );

        assert!(output.status.success(), "{channel}: {}", stderr(&output));
        let project = root.path().join("demo");
        let read = |path: &str| fs::read_to_string(project.join(path)).unwrap();
        assert!(read("skills/triage/SKILL.md").starts_with("---\nname: triage\n"));
        assert_eq!(read("skills/triage/examples.md"), "# Examples\n");
        assert!(read("skills/summarizer/SKILL.md").contains("name: summarizer"));
        assert!(read(memory_file).contains(layers), "{channel}");
        assert_eq!(
            read("fleet_export_files/memories/wiki/INDEX.md"),
            "# Wiki\n\n- refunds.md\n"
        );
        assert_eq!(
            read("fleet_export_files/memories/skills/README.md"),
            "Use triage first.\n"
        );
        assert!(!project.join("fleet_export_files/upload_memory.sh").exists());
        assert!(read(".gitignore").ends_with("\nfleet_export_files/\n"));
        let report = read("FLEET_EXPORT.md");
        assert!(
            report.contains("won't receive updates: summarizer.\n"),
            "{report}"
        );
        assert!(
            report.contains(
                "`fleet_export_files/memories/`: `pricing.md`, `skills/README.md`, `wiki/INDEX.md`."
            ),
            "{report}"
        );
        assert!(
            report.contains("2. Upload: `mda memory upload fleet_export_files/memories`."),
            "{report}"
        );
        assert!(stderr(&output).contains(
            "Memory: after your first deploy, run `mda memory upload fleet_export_files/memories`"
        ));
    }
}

#[test]
fn from_rejects_flags_the_export_decides() {
    let root = tempfile::tempdir().unwrap();
    for flag in [
        &["--instructions", "be terse"][..],
        &["--model", "openai:gpt-5.6-terra"],
        &["--no-sandbox"],
        &["--memory", "agent"],
        &["--channel", "slack"],
        &["--from-snapshot", "dir"],
    ] {
        let mut args = vec!["demo", "--from", "agent-1"];
        args.extend_from_slice(flag);
        let output = run_init(root.path(), "pypi", &args);
        assert!(!output.status.success(), "{flag:?}");
        assert!(stderr(&output).contains("cannot be used with"), "{flag:?}");
    }
    for flag in [
        ["--workspace-id", "ws"],
        ["--host-url", "http://localhost"],
        ["--env-file", ".env"],
    ] {
        let mut args = vec!["demo", "--from-snapshot", "dir"];
        args.extend_from_slice(&flag);
        let output = run_init(root.path(), "pypi", &args);
        assert!(!output.status.success(), "{flag:?}");
        assert!(stderr(&output).contains("cannot be used with"), "{flag:?}");
    }
}
