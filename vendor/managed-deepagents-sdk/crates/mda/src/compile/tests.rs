//! Tests for the compile pipeline itself (`compile` / `compile_for_deploy`).
//!
//! Unit tests for the individual pipeline stages are colocated with the stage
//! that owns them (`discovery`, `entry`, `exports`, `model`, ...). What lives
//! here drives a full compile end to end, so its subject is the orchestration
//! in the parent module rather than any one stage. Shared fixtures come from
//! [`super::test_support`] and [`crate::test_support`].

use std::fs;
use std::path::{Path, PathBuf};

use super::test_support::{
    compile, compile_for_deploy, compile_for_eval, compile_for_eval_with_model_override,
    compile_refresh, npm_available, python3_available, setup_mock_ts_runtime,
    with_live_npm_lockfile, with_py_runtime,
};
use super::{load_sandbox_recipe_manifest, DeployContext, BUILD_MARKER, LANGGRAPH_API_VERSION};
use crate::project::{load, Language, Project};
use crate::test_support::write_file;
use crate::toolchain;

// End-to-end TypeScript compilation.

#[test]
fn compile_rejects_managed_settings_before_replacing_build_output() {
    for language in [Language::TypeScript, Language::Python] {
        let mut cases = vec![
            ("backend", "Configure the sandbox in sandbox/."),
            ("store", "MDA provides the store automatically."),
            (
                "checkpointer",
                "MDA provides the checkpointer automatically.",
            ),
            (
                "instructions",
                language.ident(
                    "Use a factory entrypoint to select instructions per run; static agents use instructions.md.",
                    "Use a factory entrypoint to select instructions per run; static agents use instructions.md.",
                ),
            ),
            (
                "mcp",
                language.ident(
                    "Use a factory entrypoint to select MCP servers per run; static agents use tools/mcp.ts.",
                    "Use a factory entrypoint to select MCP servers per run; static agents use tools/mcp.py.",
                ),
            ),
            (
                "memory",
                language.ident(
                    "Use a factory entrypoint to select memory per run; static agents use memory.ts.",
                    "Use a factory entrypoint to select memory per run; static agents use memory.py.",
                ),
            ),
            (
                "sandbox",
                language.ident(
                    "Use a factory entrypoint to select a sandbox per run; static agents use sandbox/index.ts.",
                    "Use a factory entrypoint to select a sandbox per run; static agents use sandbox/__init__.py.",
                ),
            ),
            (
                "skills",
                language.ident(
                    "Use a factory entrypoint to select skills per run; static agents use skills/.",
                    "Use a factory entrypoint to select skills per run; static agents use skills/.",
                ),
            ),
            (
                language.ident("systemPrompt", "system_prompt"),
                language.ident(
                    "Use a factory entrypoint with `instructions` for per-run prompts; static agents use instructions.md.",
                    "Use a factory entrypoint with `instructions` for per-run prompts; static agents use instructions.md.",
                ),
            ),
        ];
        if language == Language::Python {
            cases.push((
                "state_schema",
                "Custom state schemas are not supported in Python.",
            ));
        }
        for (key, hint) in cases {
            let root = tempfile::tempdir().unwrap();
            setup_mock_ts_runtime(root.path());
            let agent_file = format!("agent.{}", language.extension());
            let source = match language {
                Language::TypeScript => format!(
                    "export const agent = defineDeepAgent({{ name: 'research', {key}: undefined }});"
                ),
                Language::Python => format!(
                    "agent = define_deep_agent(name='research', {key}=None)\n"
                ),
            };
            write_file(root.path(), &agent_file, &source);
            write_file(root.path(), ".mda/build/previous.txt", "keep this build");
            write_file(root.path(), &format!(".mda/build/{BUILD_MARKER}"), "");
            let project = Project {
                root: root.path().to_path_buf(),
                language,
                agent_file: PathBuf::from(&agent_file),
                files: vec![PathBuf::from(&agent_file)],
            };
            let error = with_py_runtime(root.path(), || {
                compile(&project, &root.path().join(".mda/build"))
            })
            .unwrap_err()
            .to_string();
            let define = language.ident("defineDeepAgent", "define_deep_agent");
            assert_eq!(
                error,
                format!("{agent_file}: Remove `{key}` from {define}(...). {hint}")
            );
            assert_eq!(
                fs::read_to_string(root.path().join(".mda/build/previous.txt")).unwrap(),
                "keep this build"
            );
            assert!(!root.path().join(".mda/__contexthub__").exists());
        }
    }
}

#[test]
fn compile_copies_files_verbatim_and_generates_a_managed_entry() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "tools/query-db.ts",
        "export const queryDB = 1;\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("tools/query-db.ts"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();

    assert_eq!(summary.entry_file, PathBuf::from("_mda_entry.ts"));
    assert_eq!(summary.langgraph_graph, "./_mda_entry.ts:agent");
    assert_eq!(summary.assistant_id, "agent");

    // The user's agent file is carried over byte-for-byte: no transform.
    let agent_out = fs::read_to_string(out_dir.join("agent.ts")).unwrap();
    assert_eq!(
        agent_out,
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n"
    );

    // The generated entry imports from the vendored runtime (workspace package).
    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("managed-deepagents/runtime"));
    assert!(entry_out.contains("import { agent as definition } from \"./agent\""));
    assert!(entry_out.contains(
        "export function agent(config: ManagedRunConfig, runtime: ManagedServerRuntime)"
    ));

    // Other files are carried over byte-for-byte too.
    let tool_out = fs::read_to_string(out_dir.join("tools/query-db.ts")).unwrap();
    assert_eq!(tool_out, "export const queryDB = 1;\n");

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(value["graphs"]["agent"], "./_mda_entry.ts:agent");
}

#[test]
fn compile_normalizes_static_name_as_assistant_id() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "export const agent = defineDeepAgent({\n",
            "  name: \"Research_Assistant\",\n",
            "  model: \"openai:gpt-5\",\n",
            "});\n",
        ),
    );
    write_file(
        root.path(),
        "schedules/daily.ts",
        concat!(
            "import { defineSchedule } from \"managed-deepagents\";\n",
            "export const schedule = defineSchedule({\n",
            "  cron: \"0 9 * * *\",\n",
            "  prompt: \"Write the digest.\",\n",
            "});\n",
        ),
    );

    write_file(root.path(), "identity.ts", "import { defineIdentity } from \"managed-deepagents\";\nexport const identity = defineIdentity({ auth: \"backend\" });\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("identity.ts"),
            PathBuf::from("agent.ts"),
            PathBuf::from("schedules/daily.ts"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();

    assert_eq!(summary.assistant_id, "research-assistant");
    assert_eq!(summary.langgraph_graph, "./_mda_entry.ts:agent");

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(
        value["graphs"]["research-assistant"],
        "./_mda_entry.ts:agent"
    );
    assert!(value["graphs"].get("agent").is_none());
    assert_eq!(
        summary.schedule_crons["daily"]["graph"],
        "research-assistant"
    );
}

#[test]
fn compile_builds_plain_factories_without_running_authored_code() {
    for (language, source) in [
        (Language::TypeScript, "export async function agent(runtime) { markFactoryCalled(); return defineDeepAgent({ name: 'plain' }); }"),
        (Language::Python, "async def agent(runtime):\n    mark_factory_called()\n    return define_deep_agent(name='plain')\n"),
    ] {
        let root = tempfile::tempdir().unwrap();
        let agent_file = language.ident("agent.ts", "agent.py");
        write_file(root.path(), agent_file, source);
        let project = Project {
            root: root.path().to_path_buf(),
            language,
            agent_file: PathBuf::from(agent_file),
            files: vec![PathBuf::from(agent_file)],
        };
        let out_dir = root.path().join(".mda/build");
        let summary = match language {
            Language::TypeScript => {
                setup_mock_ts_runtime(root.path());
                compile(&project, &out_dir)
            }
            Language::Python => with_py_runtime(root.path(), || compile(&project, &out_dir)),
        }.unwrap();
        assert_eq!(summary.assistant_id, "plain");
        let entry_file = language.ident("_mda_entry.ts", "_mda_entry.py");
        let entry = fs::read_to_string(out_dir.join(entry_file)).unwrap();
        assert!(entry.contains(language.ident(
            "compileManagedAgent(definition, config,",
            "compile_managed_agent_async(_definition, config,",
        )), "{entry}");
        assert!(entry.contains(language.ident("}, runtime)", "runtime=runtime)")), "{entry}");
        assert!(!entry.contains(language.ident("assertStaticAgentDefinition", "assert_static_agent_definition")), "{entry}");
    }
}

#[test]
fn compile_builds_typescript_factory_entry_without_running_factory() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "export const agent = () => {\n",
            "  markFactoryCalled();\n",
            "  return defineDeepAgent({ name: \"support\" });\n",
            "};\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        concat!(
            "{\n",
            "  \"name\": \"dynamic-example\",\n",
            "  \"dependencies\": {\n",
            "    \"@langchain/openai\": \"1.5.5\",\n",
            "    \"@langchain/anthropic\": \"1.5.1\"\n",
            "  }\n",
            "}\n",
        ),
    );
    write_file(
        root.path(),
        "tools/mcp.ts",
        concat!(
            "import { defineMcp } from \"managed-deepagents\";\n",
            "export const mcp = defineMcp({\n",
            "  servers: { docs: { transport: \"http\", url: \"https://docs.example.com/mcp\" } },\n",
            "});\n",
        ),
    );
    write_file(
        root.path(),
        "memory.ts",
        concat!(
            "import { defineMemory, memoryLayer } from \"managed-deepagents\";\n",
            "export const memory = defineMemory({ agent: memoryLayer() });\n",
        ),
    );
    write_file(
        root.path(),
        "skills/team-playbook/SKILL.md",
        "# Team playbook\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("memory.ts"),
            PathBuf::from("package.json"),
            PathBuf::from("skills/team-playbook/SKILL.md"),
            PathBuf::from("tools/mcp.ts"),
        ],
    };
    let out_dir = root.path().join(".mda/build");

    let summary = compile(&project, &out_dir).unwrap();

    assert_eq!(summary.assistant_id, "support");
    assert_eq!(summary.connectors, [PathBuf::from("tools/mcp.ts")]);
    assert!(summary.memory.agent_enabled());
    assert!(summary.has_skills);
    let langgraph: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(langgraph["graphs"]["support"], "./_mda_entry.ts:agent");
    assert_eq!(langgraph["graphs"].as_object().unwrap().len(), 1);
    let entry = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry.contains("assertStaticAgentDefinition"), "{entry}");
    assert!(entry.contains("connectors"), "{entry}");
    assert!(entry.contains("memory"), "{entry}");
    let package: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    assert_eq!(package["dependencies"]["@langchain/openai"], "1.5.5");
    assert_eq!(package["dependencies"]["@langchain/anthropic"], "1.5.1");
}

#[test]
fn compile_uses_static_name_from_typescript_factory() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "export const agent = (ctx) =>\n",
            "  defineDeepAgent({ name: \"Support_Assistant\", model: selectModel(ctx) })\n",
            ";\n",
        ),
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };
    let out_dir = root.path().join(".mda/build");

    let summary = compile(&project, &out_dir).unwrap();

    assert_eq!(summary.assistant_id, "support-assistant");
    let langgraph: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(
        langgraph["graphs"]["support-assistant"],
        "./_mda_entry.ts:agent"
    );
    assert!(langgraph["graphs"].get("agent").is_none());
}

#[test]
fn compile_builds_python_factory_entry_without_running_factory() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "def build_agent(_ctx):\n",
            "    mark_factory_called()\n",
            "    return define_deep_agent(name=\"support\")\n",
            "\n",
            "agent = build_agent\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"dynamic-example\"\n",
            "version = \"0.0.0\"\n",
            "dependencies = [\n",
            "    \"langchain-openai==1.4.2\",\n",
            "    \"langchain-anthropic==1.5.4\",\n",
            "]\n",
        ),
    );
    write_file(
        root.path(),
        "tools/mcp.py",
        concat!(
            "from managed_deepagents import define_mcp\n",
            "mcp = define_mcp(servers={\n",
            "    \"docs\": {\"transport\": \"http\", \"url\": \"https://docs.example.com/mcp\"}\n",
            "})\n",
        ),
    );
    write_file(
        root.path(),
        "memory.py",
        concat!(
            "from managed_deepagents import MemoryLayer, define_memory\n",
            "memory = define_memory(agent=MemoryLayer())\n",
        ),
    );
    write_file(
        root.path(),
        "skills/team-playbook/SKILL.md",
        "# Team playbook\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("memory.py"),
            PathBuf::from("pyproject.toml"),
            PathBuf::from("skills/team-playbook/SKILL.md"),
            PathBuf::from("tools/mcp.py"),
        ],
    };
    let out_dir = root.path().join(".mda/build");

    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert_eq!(summary.assistant_id, "support");
    assert_eq!(summary.connectors, [PathBuf::from("tools/mcp.py")]);
    assert!(summary.memory.agent_enabled());
    assert!(summary.has_skills);
    let langgraph: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(
        langgraph["graphs"],
        serde_json::json!({
            "support": "./_mda_entry.py:agent",
            "support--general-purpose": "./_mda_entry.py:agent__general_purpose",
        })
    );
    let entry = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(!entry.contains("assert_static_agent_definition"), "{entry}");
    assert!(entry.contains("_connectors"), "{entry}");
    assert!(entry.contains("_memory"), "{entry}");
    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert_eq!(pyproject.matches("langchain-openai==1.4.2").count(), 1);
    assert_eq!(pyproject.matches("langchain-anthropic==1.5.4").count(), 1);
}

#[test]
fn compile_uses_static_name_from_python_factory() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "def build_agent(ctx):\n",
            "    return define_deep_agent(name=\"Support_Assistant\", model=select_model(ctx))\n",
            "\n",
            "agent = build_agent\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"dynamic-example\"\n",
            "version = \"0.0.0\"\n",
            "dependencies = []\n",
        ),
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };
    let out_dir = root.path().join(".mda/build");

    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert_eq!(summary.assistant_id, "support-assistant");
    let langgraph: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(
        langgraph["graphs"]["support-assistant"],
        "./_mda_entry.py:agent"
    );
    assert!(langgraph["graphs"].get("agent").is_none());
}

#[test]
fn compile_rejects_missing_agent_name() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let err = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        err.contains("agent `name` is required"),
        "unexpected error: {err}"
    );
    assert!(
        err.contains("name: \"research-assistant\"") && err.contains("defineDeepAgent(...)"),
        "unexpected error: {err}"
    );
}

#[test]
fn compile_rejects_dynamic_agent_name() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: process.env.AGENT_NAME });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let err = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        err.contains("must be a static string literal"),
        "unexpected error: {err}"
    );
}

#[test]
fn compile_rejects_nonliteral_typescript_factory_agent_name() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "const NAME = \"support\";\n",
            "export const agent = () => defineDeepAgent({ name: NAME });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let err = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        err.contains("must be a static string literal"),
        "unexpected error: {err}"
    );
}

#[test]
fn compile_rejects_invalid_agent_name() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"1bad\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let err = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        err.contains("must start with an ASCII letter"),
        "unexpected error: {err}"
    );
}

#[test]
fn compile_inlines_text_module_imports() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "prompt.md",
        "# Prompt\n\nUse `code` and ${vars}.\n",
    );
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "import { defineDeepAgent } from \"managed-deepagents\";\n",
            "import promptText from \"./prompt.md\" with { type: \"text\" };\n",
            "export const agent = defineDeepAgent({ name: \"agent\", promptText });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("prompt.md")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let agent_out = fs::read_to_string(out_dir.join("agent.ts")).unwrap();
    assert_eq!(
        agent_out,
        concat!(
            "import { defineDeepAgent } from \"managed-deepagents\";\n",
            "const promptText = `# Prompt\n\nUse \\`code\\` and \\${vars}.\n`;\n",
            "export const agent = defineDeepAgent({ name: \"agent\", promptText });\n",
        )
    );
    // The referenced markdown is still carried into the build verbatim.
    assert!(out_dir.join("prompt.md").exists());
}

#[test]
fn compile_replaces_previous_build_output() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join("build");
    // Seed a stale artifact in a stamped build directory: it should be removed
    // by the next compile.
    fs::create_dir_all(&out_dir).unwrap();
    fs::write(out_dir.join(BUILD_MARKER), "stamp").unwrap();
    fs::write(out_dir.join("stale.txt"), "old").unwrap();

    compile(&project, &out_dir).unwrap();

    assert!(!out_dir.join("stale.txt").exists());
    assert!(out_dir.join("agent.ts").exists());
    assert!(out_dir.join("_mda_entry.ts").exists());
    assert!(out_dir.join(BUILD_MARKER).is_file());
}

#[test]
fn compile_refresh_preserves_node_modules_and_staged_env_while_dropping_stale_sources() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "tools/query.ts", "export const q = 1;\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("tools/query.ts")],
    };
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();

    // Simulate a prior `mda dev` install + staged env + LangGraph state.
    fs::create_dir_all(out_dir.join("node_modules/left-pad")).unwrap();
    fs::write(
        out_dir.join("node_modules/left-pad/index.js"),
        "module.exports=1\n",
    )
    .unwrap();
    fs::write(out_dir.join(".mda-deps-stamp"), "stamp\n").unwrap();
    fs::write(out_dir.join(".env"), "LANGSMITH_API_KEY=test\n").unwrap();
    fs::create_dir_all(out_dir.join(".langgraph_api")).unwrap();
    fs::write(out_dir.join(".langgraph_api/keep"), "1\n").unwrap();
    let site = out_dir.join(super::requirements::PYTHON_EMIT_SITE_DIR);
    fs::create_dir_all(site.join("pydantic")).unwrap();
    fs::write(site.join("pydantic/__init__.py"), "\n").unwrap();

    // Drop a source file and refresh: stale copy goes away; preserved dirs stay.
    let refreshed = Project {
        files: vec![PathBuf::from("agent.ts")],
        ..project
    };
    compile_refresh(&refreshed, &out_dir).unwrap();

    assert!(!out_dir.join("tools/query.ts").exists());
    assert!(out_dir.join("agent.ts").exists());
    assert!(out_dir.join("node_modules/left-pad/index.js").is_file());
    assert_eq!(
        fs::read_to_string(out_dir.join(".mda-deps-stamp")).unwrap(),
        "stamp\n"
    );
    assert_eq!(
        fs::read_to_string(out_dir.join(".env")).unwrap(),
        "LANGSMITH_API_KEY=test\n"
    );
    assert!(out_dir.join(".langgraph_api/keep").is_file());
    assert!(site.join("pydantic/__init__.py").is_file());
}

#[test]
fn compile_full_keeps_dependency_caches_but_drops_staged_env_and_stale_sources() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "tools/query.ts", "export const q = 1;\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("tools/query.ts")],
    };
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();

    // Simulate a prior emit / `mda dev` install, emit site, and staged env.
    fs::create_dir_all(out_dir.join("node_modules/left-pad")).unwrap();
    fs::write(
        out_dir.join("node_modules/left-pad/index.js"),
        "module.exports=1\n",
    )
    .unwrap();
    let site = out_dir.join(super::requirements::PYTHON_EMIT_SITE_DIR);
    fs::create_dir_all(site.join("pydantic")).unwrap();
    fs::write(out_dir.join(".env"), "LANGSMITH_API_KEY=test\n").unwrap();
    fs::create_dir_all(out_dir.join(".langgraph_api")).unwrap();

    let rebuilt = Project {
        files: vec![PathBuf::from("agent.ts")],
        ..project
    };
    compile(&rebuilt, &out_dir).unwrap();

    assert!(out_dir.join("node_modules/left-pad/index.js").is_file());
    assert!(site.join("pydantic").is_dir());
    assert!(out_dir.join("agent.ts").is_file());
    assert!(!out_dir.join("tools/query.ts").exists());
    assert!(!out_dir.join(".env").exists());
    assert!(!out_dir.join(".langgraph_api").exists());
}

#[test]
fn write_if_changed_preserves_mtime_when_bytes_match() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("langgraph.json");
    fs::write(&path, "{\"graphs\":{}}\n").unwrap();
    let mtime_before = fs::metadata(&path).unwrap().modified().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(20));

    assert!(!super::write_if_changed(&path, b"{\"graphs\":{}}\n").unwrap());
    assert_eq!(
        fs::metadata(&path).unwrap().modified().unwrap(),
        mtime_before
    );

    assert!(super::write_if_changed(&path, b"{\"graphs\":{\"a\":\"./x\"}}\n").unwrap());
    assert_ne!(
        fs::metadata(&path).unwrap().modified().unwrap(),
        mtime_before
    );
}

#[test]
fn compile_refresh_does_not_replace_managed_lockfile_with_author_lock() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"managed-deepagents\": \"^0.0.2\" }\n}\n",
    );
    write_file(
        root.path(),
        "package-lock.json",
        "{\n  \"name\": \"example\",\n  \"lockfileVersion\": 3,\n  \"requires\": true,\n  \"packages\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("package.json"),
            PathBuf::from("package-lock.json"),
        ],
    };
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();
    let managed_lock = fs::read(out_dir.join("package-lock.json")).unwrap();
    assert!(
        String::from_utf8_lossy(&managed_lock).contains("mda-test-stub"),
        "expected compile to replace the author lock with a managed lockfile"
    );

    // Author lock stays stale; refresh must keep the managed lock so the deps
    // stamp does not look dirty on every model-only rebuild.
    compile_refresh(&project, &out_dir).unwrap();
    assert_eq!(
        fs::read(out_dir.join("package-lock.json")).unwrap(),
        managed_lock
    );
}

// A build dir written by an older CLI still holds the author's pnpm lockfile;
// refresh must prune it rather than let it survive next to the managed lock.
#[test]
fn compile_refresh_prunes_lockfiles_an_older_cli_copied() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"managed-deepagents\": \"^0.0.2\" }\n}\n",
    );
    write_file(root.path(), "pnpm-lock.yaml", "author\n");
    let project = load(root.path()).unwrap();
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();
    assert!(!out_dir.join("pnpm-lock.yaml").exists());
    write_file(&out_dir, "pnpm-lock.yaml", "stale\n");

    compile_refresh(&project, &out_dir).unwrap();

    assert!(!out_dir.join("pnpm-lock.yaml").exists());
    assert!(out_dir.join("package-lock.json").is_file());
}

#[test]
fn compile_refresh_keeps_the_synced_python_lockfile() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents>=0.1.2\"]\n",
    );
    write_file(root.path(), "uv.lock", "version = 1\n# author lock\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("pyproject.toml"),
            PathBuf::from("uv.lock"),
        ],
    };
    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();
    assert_eq!(
        fs::read_to_string(out_dir.join("uv.lock")).unwrap(),
        "version = 1\n# author lock\n",
        "a full build seeds the build lock from the author's lock"
    );

    // `mda dev` runs `uv sync` in the build, which rewrites the lock to cover
    // the injected managed stack. Refresh must not copy the author's lock back
    // over it, or every source edit re-syncs `.venv` and bounces the worker.
    fs::write(out_dir.join("uv.lock"), "version = 1\n# synced lock\n").unwrap();
    with_py_runtime(root.path(), || compile_refresh(&project, &out_dir)).unwrap();
    assert_eq!(
        fs::read_to_string(out_dir.join("uv.lock")).unwrap(),
        "version = 1\n# synced lock\n"
    );

    // Upgrading over a build that predates the lock-input marker must not
    // recopy the author's lock either — that would dirty the deps stamp once.
    fs::remove_file(out_dir.join(".mda-lock-input")).unwrap();
    with_py_runtime(root.path(), || compile_refresh(&project, &out_dir)).unwrap();
    assert_eq!(
        fs::read_to_string(out_dir.join("uv.lock")).unwrap(),
        "version = 1\n# synced lock\n"
    );
}

#[test]
fn compile_refresh_copies_python_lockfile_when_the_author_edits_it() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents>=0.1.2\"]\n",
    );
    write_file(root.path(), "uv.lock", "version = 1\n# author lock\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("pyproject.toml"),
            PathBuf::from("uv.lock"),
        ],
    };
    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();
    fs::write(out_dir.join("uv.lock"), "version = 1\n# synced lock\n").unwrap();

    write_file(root.path(), "uv.lock", "version = 1\n# author lock v2\n");
    with_py_runtime(root.path(), || compile_refresh(&project, &out_dir)).unwrap();
    assert_eq!(
        fs::read_to_string(out_dir.join("uv.lock")).unwrap(),
        "version = 1\n# author lock v2\n"
    );
}

#[cfg(unix)]
#[test]
fn compile_preserves_executable_permissions_on_copied_helpers() {
    use std::os::unix::fs::PermissionsExt;

    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "scripts/run.sh", "#!/bin/sh\necho hi\n");
    fs::set_permissions(
        root.path().join("scripts/run.sh"),
        fs::Permissions::from_mode(0o755),
    )
    .unwrap();

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("scripts/run.sh")],
    };
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();

    let mode = |path: &Path| fs::metadata(path).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(&out_dir.join("scripts/run.sh")), 0o755);

    // Mode-only drift on the build copy must be repaired on refresh even when
    // bytes are unchanged (`write_if_changed` skips the write).
    fs::set_permissions(
        out_dir.join("scripts/run.sh"),
        fs::Permissions::from_mode(0o644),
    )
    .unwrap();
    compile_refresh(&project, &out_dir).unwrap();
    assert_eq!(mode(&out_dir.join("scripts/run.sh")), 0o755);
}

#[test]
fn poke_dev_reload_signal_updates_entry_mtime() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };
    let out_dir = root.path().join("build");
    compile(&project, &out_dir).unwrap();

    let entry = out_dir.join("_mda_entry.ts");
    let before = fs::metadata(&entry).unwrap().modified().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(20));
    super::poke_dev_reload_signal(&out_dir, Language::TypeScript).unwrap();
    let after = fs::metadata(&entry).unwrap().modified().unwrap();
    assert!(after >= before);
}

#[test]
fn compile_replaces_a_build_directory_written_before_the_stamp_existed() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    // A build from a CLI version that predates the stamp: generated artifacts,
    // no marker file.
    let out_dir = root.path().join("build");
    fs::create_dir_all(&out_dir).unwrap();
    fs::write(out_dir.join("langgraph.json"), "{}").unwrap();
    fs::write(out_dir.join("_mda_entry.ts"), "// old entry\n").unwrap();
    fs::write(out_dir.join("stale.txt"), "old").unwrap();

    compile(&project, &out_dir).unwrap();

    assert!(!out_dir.join("stale.txt").exists());
    assert!(out_dir.join(BUILD_MARKER).is_file());
}

#[test]
fn compile_refuses_an_out_dir_it_did_not_write() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join("notes");
    write_file(&out_dir, "NOTES.md", "keep me\n");
    write_file(&out_dir, "nested/.env", "OPENAI_API_KEY=secret\n");

    let err = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        err.contains("it is not empty and was not written by `mda`"),
        "unexpected error: {err}"
    );
    assert_eq!(
        fs::read_to_string(out_dir.join("NOTES.md")).unwrap(),
        "keep me\n"
    );
    assert!(out_dir.join("nested/.env").is_file());
}

#[test]
fn compile_accepts_an_empty_out_dir() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join("empty");
    fs::create_dir_all(&out_dir).unwrap();

    compile(&project, &out_dir).unwrap();

    assert!(out_dir.join("_mda_entry.ts").is_file());
}

#[test]
fn compile_refuses_an_out_dir_equal_to_the_project_root() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), ".env", "OPENAI_API_KEY=secret\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let err = compile(&project, root.path()).unwrap_err().to_string();
    assert!(
        err.contains("refusing to build into the project directory"),
        "unexpected error: {err}"
    );
    assert!(root.path().join("agent.ts").is_file());
    assert!(root.path().join(".env").is_file());
}

#[test]
fn compile_refuses_an_out_dir_that_contains_the_project() {
    let root = tempfile::tempdir().unwrap();
    let project_root = root.path().join("project");
    setup_mock_ts_runtime(&project_root);
    write_file(
        &project_root,
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: project_root.clone(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let err = compile(&project, root.path()).unwrap_err().to_string();
    assert!(
        err.contains("it contains the project at"),
        "unexpected error: {err}"
    );
    assert!(project_root.join("agent.ts").is_file());
}

// End-to-end Python compilation.

#[test]
fn compile_generates_a_python_entry() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert_eq!(summary.entry_file, PathBuf::from("_mda_entry.py"));
    assert_eq!(summary.langgraph_graph, "./_mda_entry.py:agent");
    assert_eq!(summary.assistant_id, "agent");

    let agent_out = fs::read_to_string(out_dir.join("agent.py")).unwrap();
    assert_eq!(
        agent_out,
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n"
    );

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(
        entry_out.contains("from managed_deepagents.runtime import compile_managed_agent_async")
    );
    assert!(entry_out.contains("from agent import agent as _definition"));
    assert!(entry_out.contains("async def agent(config, runtime: ServerRuntime):"));
    assert!(entry_out.contains("from langgraph_sdk.runtime import ServerRuntime"));
    assert!(entry_out.contains("runtime=runtime"));
    assert!(!entry_out.contains("run_context=execution.context"));

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(value["python_version"], "3.14");
    assert_eq!(
        value["_INTERNAL_docker_tag"],
        format!("{LANGGRAPH_API_VERSION}-py3.14")
    );
    assert_eq!(value["graphs"]["agent"], "./_mda_entry.py:agent");
}

#[test]
fn compile_builds_a_graph_per_async_subagent() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "agent = define_deep_agent(name=\"lead\", subagents=[\n",
            "    {\"name\": \"researcher\", \"description\": \"d\", \"async\": True},\n",
            "    {\"name\": \"helper\", \"description\": \"d\"},\n",
            "])\n",
        ),
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };
    let out_dir = root.path().join("build");

    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(
        manifest["graphs"],
        serde_json::json!({
            "lead": "./_mda_entry.py:agent",
            "lead--researcher": "./_mda_entry.py:agent__researcher",
            "lead--general-purpose": "./_mda_entry.py:agent__general_purpose",
        })
    );
    let entry = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(
        entry.contains(
            "_async_subagent_graphs = {\"researcher\": \"lead--researcher\", \"general-purpose\": \"lead--general-purpose\"}"
        ),
        "{entry}"
    );
    assert!(
        entry.contains("async_subagent_graphs=_async_subagent_graphs"),
        "{entry}"
    );
    assert!(
        entry.contains(
            "@asynccontextmanager\nasync def agent__researcher(config, runtime: ServerRuntime):\n    async with parent_run(config):"
        ),
        "{entry}"
    );
    assert!(entry.contains("async_subagent=\"researcher\")"), "{entry}");
}

#[test]
fn eval_builds_no_async_general_purpose_subagent() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"lead\")\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };
    let out_dir = root.path().join("build");

    with_py_runtime(root.path(), || compile_for_eval(&project, &out_dir)).unwrap();

    let entry = fs::read_to_string(out_dir.join("_mda_eval_entry.py")).unwrap();
    assert!(!entry.contains("general_purpose"), "{entry}");
    assert!(!entry.contains("async_subagent_graphs"), "{entry}");
}

#[test]
fn eval_rejects_async_subagents() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"lead\", subagents=[{\"name\": \"r\", \"description\": \"d\", \"async\": True}])\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let error = compile_for_eval(&project, &root.path().join("build")).unwrap_err();

    assert!(
        error.to_string().contains("not supported in eval"),
        "{error}"
    );
}

#[test]
fn compile_warns_about_subagents_it_cannot_read() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"lead\", subagents=[make_subagent()])\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let summary = with_py_runtime(root.path(), || {
        compile(&project, &root.path().join("build"))
    })
    .unwrap();

    assert!(
        summary
            .warnings
            .iter()
            .any(|warning| warning.contains("1 subagent entry")),
        "{:?}",
        summary.warnings
    );
}

#[test]
fn compile_ignores_python_config_for_typescript() {
    // A TypeScript manifest keys the runtime on `node_version`, so Python
    // configuration is irrelevant to it. An unparseable `requires-python` at the
    // project root must not fail a build that never reads it.
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"unrelated\"\nrequires-python = \"not a specifier\"\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("pyproject.toml")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(value["node_version"], "22");
    assert!(value.get("python_version").is_none());
}

#[test]
fn compile_applies_python_version_configuration() {
    for (requirement, target, expected, warns) in [
        (">=3.11", "", Some("3.14"), false),
        (">=3.11,<3.14", "", Some("3.13"), false),
        (">=3.13.2,<3.14", "", Some("3.13"), true),
        (
            ">=3.11",
            "[tool.mda]\npython-version = \"3.12\"\n",
            Some("3.12"),
            false,
        ),
        (">=3.9,<3.11", "", None, false),
    ] {
        let root = tempfile::tempdir().unwrap();
        write_file(
            root.path(),
            "agent.py",
            "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
        );
        write_file(root.path(), "pyproject.toml", &format!(
            "[project]\nname = \"agent\"\nversion = \"0.0.0\"\nrequires-python = {requirement:?}\n{target}"
        ));
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
        };
        let out_dir = root.path().join("build");
        let result = with_py_runtime(root.path(), || compile(&project, &out_dir));
        let Some(version) = expected else {
            assert!(result.unwrap_err().to_string().contains("allows no Python"));
            continue;
        };
        let summary = result.unwrap();
        assert_eq!(
            summary
                .warnings
                .iter()
                .any(|warning| warning.starts_with("requires-python ")),
            warns,
            "{requirement}, {target}"
        );
        let manifest: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap())
                .unwrap();
        assert_eq!(
            manifest["python_version"], version,
            "{requirement}, {target}"
        );
        assert_eq!(
            manifest["_INTERNAL_docker_tag"],
            format!("{LANGGRAPH_API_VERSION}-py{version}")
        );
        let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
        assert!(pyproject.contains(&format!("requires-python = {requirement:?}")));
    }
}

#[test]
fn compile_normalizes_static_python_name_as_assistant_id() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"Research_Assistant\", model=\"openai:gpt-5\")\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert_eq!(summary.assistant_id, "research-assistant");
    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert_eq!(
        value["graphs"]["research-assistant"],
        "./_mda_entry.py:agent"
    );
    assert!(value["graphs"].get("agent").is_none());
}

#[test]
fn compile_rejects_missing_python_agent_name() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(model=\"openai:gpt-5\")\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    let err = with_py_runtime(root.path(), || compile(&project, &out_dir))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("agent `name` is required"),
        "unexpected error: {err}"
    );
    assert!(
        err.contains("name=\"research-assistant\"") && err.contains("define_deep_agent(...)"),
        "python guidance must not be written in TypeScript: {err}"
    );
}

#[test]
fn compile_rejects_dynamic_python_agent_name() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "import os\n",
            "agent = define_deep_agent(name=os.environ[\"AGENT_NAME\"])\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    let err = with_py_runtime(root.path(), || compile(&project, &out_dir))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("must be a static string literal"),
        "unexpected error: {err}"
    );
    assert!(
        err.contains("name=\"research-assistant\""),
        "python guidance must not be written in TypeScript: {err}"
    );
}

#[test]
fn compile_rejects_nonliteral_python_factory_agent_name() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "NAME = \"support\"\n",
            "def build_agent(_ctx):\n",
            "    return define_deep_agent(name=NAME)\n",
            "\n",
            "agent = build_agent\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    let err = with_py_runtime(root.path(), || compile(&project, &out_dir))
        .unwrap_err()
        .to_string();
    assert!(
        err.contains("must be a static string literal"),
        "unexpected error: {err}"
    );
}

// Managed schedules materialized as crons.

#[test]
fn compile_materializes_typescript_schedules_as_crons() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "schedules/daily.md",
        "Write the daily digest.\n",
    );
    write_file(
        root.path(),
        "schedules/daily-digest.ts",
        concat!(
            "import { defineSchedule } from \"managed-deepagents\";\n",
            "import prompt from \"./daily.md\" with { type: \"text\" };\n",
            "export const schedule = defineSchedule({\n",
            "  cron: \"0 8 * * 1-5\",\n",
            "  timezone: \"America/Los_Angeles\",\n",
            "  prompt,\n",
            "});\n",
        ),
    );

    write_file(root.path(), "identity.ts", "import { defineIdentity } from \"managed-deepagents\";\nexport const identity = defineIdentity({ auth: \"backend\" });\n");

    let mut project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("identity.ts"),
            PathBuf::from("agent.ts"),
            PathBuf::from("schedules/daily.md"),
            PathBuf::from("schedules/daily-digest.ts"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    fs::remove_file(root.path().join("identity.ts")).unwrap();
    project
        .files
        .retain(|path| path != &PathBuf::from("identity.ts"));
    let error = compile(&project, &out_dir).unwrap_err();
    assert!(format!("{error:#}").contains("schedules require a root identity"));
    write_file(root.path(), "identity.ts", "import { defineIdentity } from \"managed-deepagents\";\nexport const identity = defineIdentity({ auth: \"backend\" });\n");
    project.files.push(PathBuf::from("identity.ts"));
    let summary = compile(&project, &out_dir).unwrap();

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert!(value.get("crons").is_none());
    let cron = &summary.schedule_crons["daily-digest"];
    assert_eq!(cron["graph"], "agent");
    assert_eq!(cron["schedule"], "0 8 * * 1-5");
    assert_eq!(cron["timezone"], "America/Los_Angeles");
    assert_eq!(cron["on_run_completed"], "delete");
    assert_eq!(cron["input"]["messages"][0]["role"], "user");
    assert_eq!(
        cron["input"]["messages"][0]["content"],
        "Write the daily digest.\n"
    );
    assert_eq!(cron["metadata"]["mda_managed"], true);
    assert_eq!(cron["metadata"]["mda_schedule"], "daily-digest");
    assert!(cron.get("config").is_none());
    assert_eq!(
        cron["context"],
        serde_json::json!({ "mda_schedule": { "name": "daily-digest" } })
    );
}

#[test]
fn compile_preserves_typescript_schedule_deliver_to() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "schedules/monday-greeting.ts",
        concat!(
            "import { defineSchedule } from \"managed-deepagents\";\n",
            "const BOTCAVE_CHANNEL_ID = \"C0123456789\";\n",
            "export const schedule = defineSchedule({\n",
            "  cron: \"0 9 * * 1\",\n",
            "  timezone: \"America/Los_Angeles\",\n",
            "  prompt: \"Say hello\",\n",
            "  deliverTo: {\n",
            "    channel: \"slack\",\n",
            "    to: { type: \"provider_conversation\", conversationId: BOTCAVE_CHANNEL_ID },\n",
            "    autoPost: true,\n",
            "  },\n",
            "  context: { team: \"eng\", dryRun: true },\n",
            "});\n",
        ),
    );

    write_file(root.path(), "identity.ts", "import { defineIdentity } from \"managed-deepagents\";\nexport const identity = defineIdentity({ auth: \"backend\" });\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("identity.ts"),
            PathBuf::from("agent.ts"),
            PathBuf::from("schedules/monday-greeting.ts"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();
    let cron = &summary.schedule_crons["monday-greeting"];
    assert!(cron.get("config").is_none());
    assert_eq!(
        cron["context"],
        serde_json::json!({
            "mda_schedule": {
                "name": "monday-greeting",
                "deliver_to": {
                    "channel": "slack",
                    "to": {
                        "type": "provider_conversation",
                        "conversationId": "C0123456789"
                    },
                    "autoPost": true
                }
            },
            "team": "eng",
            "dryRun": true
        })
    );
    write_file(
        root.path(),
        "schedules/monday-greeting.ts",
        concat!(
            "import { defineSchedule } from \"managed-deepagents\";\n",
            "export const schedule = defineSchedule({\n",
            "  cron: \"0 9 * * 1\",\n",
            "  prompt: \"Say hello\",\n",
            "  context: { channel: \"slack\" },\n",
            "});\n",
        ),
    );
    let error = compile(&project, &out_dir).unwrap_err();
    assert!(format!("{error:#}").contains("reserved"));
}

#[test]
fn compile_materializes_python_schedules_as_crons() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "schedules/nightly.py",
        concat!(
            "from managed_deepagents import define_schedule\n",
            "schedule = define_schedule(\n",
            "    cron=\"30 2 * * *\",\n",
            "    input={\"messages\": [{\"role\": \"user\", \"content\": \"Sweep stale tickets.\"}]},\n",
            "    thread={\"mode\": \"persistent\", \"id\": \"nightly-thread\"},\n",
            ")\n",
        ),
    );

    write_file(root.path(), "identity.py", "from managed_deepagents import define_identity\nidentity = define_identity(auth=\"backend\")\n");

    let mut project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("identity.py"),
            PathBuf::from("agent.py"),
            PathBuf::from("schedules/nightly.py"),
        ],
    };

    let out_dir = root.path().join("build");
    fs::remove_file(root.path().join("identity.py")).unwrap();
    project
        .files
        .retain(|path| path != &PathBuf::from("identity.py"));
    let error = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap_err();
    assert!(format!("{error:#}").contains("schedules require a root identity"));
    write_file(root.path(), "identity.py", "from managed_deepagents import define_identity\nidentity = define_identity(auth=\"backend\")\n");
    project.files.push(PathBuf::from("identity.py"));
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let manifest = fs::read_to_string(out_dir.join("langgraph.json")).unwrap();
    let value: serde_json::Value = serde_json::from_str(&manifest).unwrap();
    assert!(value.get("crons").is_none());
    let cron = &summary.schedule_crons["nightly"];
    assert_eq!(cron["graph"], "agent");
    assert_eq!(cron["schedule"], "30 2 * * *");
    assert_eq!(cron["thread_id"], "nightly-thread");
    assert!(cron.get("on_run_completed").is_none());
    assert_eq!(
        cron["input"]["messages"][0]["content"],
        "Sweep stale tickets."
    );
}

#[test]
fn compile_preserves_python_schedule_deliver_to() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "schedules/monday_greeting.py",
        concat!(
            "from managed_deepagents import define_schedule\n",
            "schedule = define_schedule(\n",
            "    cron=\"0 9 * * 1\",\n",
            "    timezone=\"America/Los_Angeles\",\n",
            "    prompt=\"Say hello\",\n",
            "    deliver_to={\n",
            "        \"channel\": \"slack\",\n",
            "        \"to\": {\"type\": \"provider_conversation\", \"conversation_id\": \"C0123456789\"},\n",
            "        \"auto_post\": True,\n",
            "    },\n",
            "    context={\"team\": \"eng\", \"limit\": 3},\n",
            ")\n",
        ),
    );

    write_file(root.path(), "identity.py", "from managed_deepagents import define_identity\nidentity = define_identity(auth=\"backend\")\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("identity.py"),
            PathBuf::from("agent.py"),
            PathBuf::from("schedules/monday_greeting.py"),
        ],
    };

    let out_dir = root.path().join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();
    let cron = &summary.schedule_crons["monday_greeting"];
    assert!(cron.get("config").is_none());
    assert_eq!(
        cron["context"],
        serde_json::json!({
            "mda_schedule": {
                "name": "monday_greeting",
                "deliver_to": {
                    "channel": "slack",
                    "to": {
                        "type": "provider_conversation",
                        "conversation_id": "C0123456789"
                    },
                    "auto_post": true
                }
            },
            "team": "eng",
            "limit": 3
        })
    );
}

// Context Hub wiring (local build/dev and deploy).

#[test]
fn compile_wires_local_context_hub_for_typescript() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "instructions.md",
        "# Assistant\n\nUse `code` and ${vars}.\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("instructions.md")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    // The agent file is still carried over verbatim — never rewritten.
    let agent_out = fs::read_to_string(out_dir.join("agent.ts")).unwrap();
    assert_eq!(
        agent_out,
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n"
    );

    let hub = root.path().join(".mda").join("__contexthub__");
    assert_eq!(
        fs::read_to_string(hub.join("instructions.md")).unwrap(),
        "# Assistant\n\nUse `code` and ${vars}.\n"
    );
    // No memory declaration → no hot file seeded locally, matching deploy.
    assert!(!hub.join("memories/agent/AGENTS.md").exists());

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    let hub_path = hub.canonicalize().unwrap();
    let hub_literal = hub_path.to_string_lossy().replace('\\', "\\\\");
    assert!(entry_out.contains(&format!("const contextRoot = \"{hub_literal}\";")));
    assert!(entry_out.contains("const hasSkills = false;"));
    assert!(entry_out
        .contains("compileManagedAgent(definition, config, { contextRoot, hasSkills }, runtime)"));
    assert!(!entry_out.contains("systemPrompt"));
}

#[test]
fn compile_for_deploy_wires_context_hub_metadata_for_typescript() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "instructions.md", "Do not embed me.\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("instructions.md")],
    };
    let deploy_context = DeployContext {
        repo: "my-agent".to_string(),
        has_skills: true,
    };

    let out_dir = root.path().join(".mda").join("build");
    compile_for_deploy(&project, &out_dir, &deploy_context).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("const contextRepo = \"my-agent\";"));
    assert!(entry_out.contains("const hasSkills = true;"));
    assert!(entry_out
        .contains("compileManagedAgent(definition, config, { contextRepo, hasSkills }, runtime)"));
    // Commit pinning is not wired in the runtime yet — do not emit it.
    assert!(!entry_out.contains("contextCommit"));
    assert!(!entry_out.contains("systemPrompt"));
    assert!(!entry_out.contains("Do not embed me"));
}

#[test]
fn compile_wires_local_context_hub_when_instructions_are_absent() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let hub = root.path().join(".mda").join("__contexthub__");
    assert!(!hub.join("instructions.md").is_file());
    // No memory declaration → no hot file seeded locally, matching deploy.
    assert!(!hub.join("memories/agent/AGENTS.md").exists());

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("systemPrompt"));
    assert!(entry_out.contains("const contextRoot = "));
    assert!(entry_out
        .contains("compileManagedAgent(definition, config, { contextRoot, hasSkills }, runtime)"));
}

#[test]
fn compile_wires_local_context_hub_for_python() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(root.path(), "instructions.md", "Be helpful.\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("instructions.md")],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let hub = root.path().join(".mda").join("__contexthub__");
    assert_eq!(
        fs::read_to_string(hub.join("instructions.md")).unwrap(),
        "Be helpful.\n"
    );

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("_context_root = "));
    assert!(entry_out.contains("_has_skills = False"));
    assert!(entry_out.contains(
        "await compile_managed_agent_async(_definition, config, context_root=_context_root, has_skills=_has_skills, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)"
    ));
    assert!(!entry_out.contains("_system_prompt"));
}

#[test]
fn compile_for_deploy_wires_context_hub_metadata_for_python() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(root.path(), "instructions.md", "Do not embed me.\n");
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents>=0.1.2\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("instructions.md"),
            PathBuf::from("pyproject.toml"),
        ],
    };
    let deploy_context = DeployContext {
        repo: "my-agent".to_string(),
        has_skills: false,
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || {
        compile_for_deploy(&project, &out_dir, &deploy_context)
    })
    .unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("_context_repo = \"my-agent\""));
    assert!(entry_out.contains("_has_skills = False"));
    assert!(entry_out.contains(
        "await compile_managed_agent_async(_definition, config, context_repo=_context_repo, has_skills=_has_skills, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)"
    ));
    // Commit pinning is not wired in the runtime yet — do not emit it.
    assert!(!entry_out.contains("_context_commit"));
    assert!(!entry_out.contains("context_commit"));
    assert!(!entry_out.contains("Do not embed me"));

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(!pyproject.contains("langgraph-cli[inmem]"));
    assert!(!pyproject.contains("langgraph-api"));
    assert!(!pyproject.contains("managed-deepagents"));

    let langgraph: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(
        langgraph["dependencies"],
        serde_json::json!([".", "./__runtime__"])
    );
}

#[test]
fn compile_finds_instructions_next_to_a_nested_agent_file() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "src/agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "src/instructions.md", "Nested prompt.\n");
    // A root-level instructions.md must be ignored — only the one next to
    // the agent file counts.
    write_file(root.path(), "instructions.md", "Root prompt.\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("src/agent.ts"),
        files: vec![
            PathBuf::from("src/agent.ts"),
            PathBuf::from("src/instructions.md"),
            PathBuf::from("instructions.md"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let hub = root.path().join(".mda").join("__contexthub__");
    assert_eq!(
        fs::read_to_string(hub.join("instructions.md")).unwrap(),
        "Nested prompt.\n"
    );
    assert_ne!(
        fs::read_to_string(hub.join("instructions.md")).unwrap(),
        "Root prompt.\n"
    );

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("const contextRoot = "));
    assert!(!entry_out.contains("Root prompt"));
    assert!(!entry_out.contains("Nested prompt"));
}

// Sandbox declaration (setup.sh is bake-only; not embedded in the runtime entry).

#[test]
fn compile_wires_typescript_sandbox_without_setup_script() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "sandbox/index.ts",
        concat!(
            "import { defineSandbox } from \"managed-deepagents\";\n",
            "export const sandbox = defineSandbox({ defaultTimeout: 600 });\n",
        ),
    );
    write_file(
        root.path(),
        "sandbox/setup.sh",
        "#!/usr/bin/env bash\necho hello > /tmp/marker\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("sandbox/index.ts"),
            PathBuf::from("sandbox/setup.sh"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("import { sandbox } from \"./sandbox/index\";"));
    assert!(!entry_out.contains("setupScript"));
    assert!(entry_out.contains(
        "compileManagedAgent(definition, config, { contextRoot, hasSkills, sandbox }, runtime)"
    ));
    assert!(
        entry_out.contains("import { compileManagedAgent } from \"managed-deepagents/runtime\";")
    );

    // Sandbox module and setup script are still carried for bake/recipe digest.
    assert!(out_dir.join("sandbox/index.ts").exists());
    assert!(out_dir.join("sandbox/setup.sh").exists());
}

#[test]
fn compile_wires_python_sandbox_without_setup_script() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "sandbox/__init__.py",
        concat!(
            "from managed_deepagents import define_sandbox\n",
            "sandbox = define_sandbox(default_timeout=600)\n",
        ),
    );
    write_file(
        root.path(),
        "sandbox/setup.sh",
        "#!/usr/bin/env bash\necho hi\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("sandbox/__init__.py"),
            PathBuf::from("sandbox/setup.sh"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from sandbox import sandbox as _sandbox"));
    assert!(!entry_out.contains("_setup_script"));
    assert!(!entry_out.contains("setup_script="));
    assert!(entry_out
        .contains(
            "await compile_managed_agent_async(_definition, config, context_root=_context_root, has_skills=_has_skills, sandbox=_sandbox, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)",
        ));
    assert!(
        entry_out.contains("from managed_deepagents.runtime import compile_managed_agent_async")
    );
}

#[test]
fn compile_omits_sandbox_wiring_when_absent() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("import { sandbox }"));
    assert!(!entry_out.contains("setupScript"));
    assert!(entry_out
        .contains("compileManagedAgent(definition, config, { contextRoot, hasSkills }, runtime)"));
}

#[test]
fn compile_ignores_setup_script_without_sandbox_declaration() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "sandbox/setup.sh",
        "#!/usr/bin/env bash\necho orphaned\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("sandbox/setup.sh")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("import { sandbox }"));
    assert!(!entry_out.contains("setupScript"));
    assert!(!entry_out.contains("sandboxSetup"));
    assert!(entry_out
        .contains("compileManagedAgent(definition, config, { contextRoot, hasSkills }, runtime)"));
    assert!(!out_dir.join("sandbox-recipe.json").exists());
}

#[test]
fn compile_bakes_factory_setup_script_without_sandbox_declaration() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = () => defineDeepAgent({ name: \"agent\" });\n",
    );
    write_file(
        root.path(),
        "sandbox/setup.sh",
        "#!/usr/bin/env bash\necho ready\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("sandbox/setup.sh")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("const sandboxSetup = true;"));
    assert!(entry_out.contains(
        "compileManagedAgent(definition, config, { contextRoot, hasSkills, sandboxSetup }, runtime)"
    ));
    let manifest = load_sandbox_recipe_manifest(&out_dir).unwrap().unwrap();
    assert!(manifest.has_setup_script);
    assert_eq!(manifest.docker_image, None);
}

#[test]
fn compile_marks_python_factory_sandbox_setup() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "def agent(runtime):\n    return define_deep_agent(name=\"agent\")\n",
    );
    write_file(
        root.path(),
        "sandbox/__init__.py",
        "sandbox = define_sandbox(docker_image=\"python:3.12-slim\")\n",
    );
    write_file(
        root.path(),
        "sandbox/setup.sh",
        "#!/usr/bin/env bash\necho ready\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("sandbox/__init__.py"),
            PathBuf::from("sandbox/setup.sh"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains(
        "await compile_managed_agent_async(_definition, config, context_root=_context_root, has_skills=_has_skills, sandbox=_sandbox, sandbox_setup=True, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)"
    ));
}

// Connector discovery and the generated aggregator.

/// The managed `@langchain/mcp-adapters` spec. Asserting through it keeps these
/// tests stable while the pin moves to `^2.0.0`;
/// `dependencies.rs` pins the major itself.
fn managed_mcp_adapter_version() -> &'static str {
    crate::dependencies::managed_dependencies(Language::TypeScript)
        .iter()
        .find(|dep| dep.name == "@langchain/mcp-adapters")
        .expect("the managed TypeScript stack carries the MCP adapter")
        .version
}

#[test]
fn compile_wires_tools_typescript_mcp() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "tools/mcp.ts",
        concat!(
            "import { defineMcp } from \"managed-deepagents\";\n",
            "export const mcp = defineMcp({\n",
            "  servers: { docs: { transport: \"http\", url: \"https://docs.example.com/mcp\" } },\n",
            "});\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("tools/mcp.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    // The entry imports the validated `connectors` list from the generated
    // aggregator and passes it in shorthand form to the managed runtime.
    assert!(entry_out.contains("import { connectors } from \"./_mda_connectors\";"));
    assert!(entry_out.contains(
        "compileManagedAgent(definition, config, { contextRoot, hasSkills, connectors }, runtime)"
    ));

    // The aggregator imports each connector module name-agnostically and
    // validates the connector brand via `collectConnectors`.
    let connectors_out = fs::read_to_string(out_dir.join("_mda_connectors.ts")).unwrap();
    assert!(connectors_out
        .contains("import { collectConnectors } from \"managed-deepagents/runtime\";"));
    assert!(connectors_out.contains("import * as _connector0 from \"./tools/mcp\";"));
    assert!(connectors_out
        .contains("{ source: \"tools/mcp.ts\", module: { connector: _connector0.mcp } }"));

    // The connectors module is carried over verbatim.
    assert!(out_dir.join("tools/mcp.ts").exists());

    // The built-in MCP adapter rides along in the managed stack.
    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    assert_eq!(
        manifest["dependencies"]["@langchain/mcp-adapters"],
        managed_mcp_adapter_version()
    );
}

#[test]
fn compile_wires_tools_python_mcp() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "tools/mcp.py",
        concat!(
            "from managed_deepagents import define_mcp\n",
            "mcp = define_mcp(servers={\"docs\": {\"transport\": \"http\", \"url\": \"https://docs.example.com/mcp\"}})\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("tools/mcp.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from _mda_connectors import connectors as _connectors"));
    assert!(entry_out
        .contains(
            "await compile_managed_agent_async(_definition, config, context_root=_context_root, has_skills=_has_skills, connectors=_connectors, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)",
        ));

    let connectors_out = fs::read_to_string(out_dir.join("_mda_connectors.py")).unwrap();
    assert!(connectors_out.contains("from managed_deepagents.runtime import collect_connectors"));
    assert!(connectors_out.contains("import tools.mcp as _connector0"));
    assert!(
        connectors_out.contains("(\"tools/mcp.py\", SimpleNamespace(connector=_connector0.mcp))")
    );
    assert!(out_dir.join("tools/mcp.py").exists());

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(pyproject.contains("fastmcp>=4.0.10,<5"), "got: {pyproject}");
    assert!(pyproject.contains("langchain[mcp]>=1.4.6,<2"));
    assert!(!pyproject.contains("langchain-mcp-adapters"));
    assert!(
        pyproject.contains("mcp>=2.2.0,<3"),
        "mcp must use the native SDK 2 baseline, got: {pyproject}"
    );
    assert!(
        out_dir
            .join("__runtime__/managed_deepagents/_connectors/__init__.py")
            .exists()
            || out_dir
                .join("__runtime__/managed_deepagents/_connectors.py")
                .exists()
    );
}

#[test]
fn compile_skips_typescript_connector_without_a_connector_export() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "connectors/mcp.ts",
        concat!(
            "import { defineMcp } from \"managed-deepagents\";\n",
            "export const connector = defineMcp({\n",
            "  servers: { docs: { transport: \"http\", url: \"https://docs.example.com/mcp\" } },\n",
            "});\n",
        ),
    );
    // Co-located unit test / helper: no `connector` export, must not be imported.
    write_file(
        root.path(),
        "connectors/mcp.test.ts",
        concat!(
            "import { describe, it, expect } from \"@jest/globals\";\n",
            "import { connector } from \"./mcp\";\n",
            "describe(\"mcp\", () => {\n",
            "  it(\"is defined\", () => { expect(connector).toBeDefined(); });\n",
            "});\n",
        ),
    );
    write_file(
        root.path(),
        "connectors/helper.ts",
        "export const sharedUrl = \"https://docs.example.com/mcp\";\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("connectors/helper.ts"),
            PathBuf::from("connectors/mcp.test.ts"),
            PathBuf::from("connectors/mcp.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();
    assert_eq!(summary.connectors, vec![PathBuf::from("connectors/mcp.ts")]);

    let connectors_out = fs::read_to_string(out_dir.join("_mda_connectors.ts")).unwrap();
    assert!(connectors_out.contains("import * as _connector0 from \"./connectors/mcp\";"));
    assert!(!connectors_out.contains("mcp.test"));
    assert!(!connectors_out.contains("helper"));
    // Non-connector files are still copied into the build (uploaded on deploy)
    // but must not be imported at startup.
    assert!(out_dir.join("connectors/mcp.test.ts").exists());
    assert!(out_dir.join("connectors/helper.ts").exists());
}

#[test]
fn compile_rejects_a_typescript_connector_exported_as_default() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "connectors/mcp.ts",
        concat!(
            "import { defineMcp } from \"managed-deepagents\";\n",
            "export default defineMcp({ servers: {} });\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("connectors/mcp.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    // Silently skipping the old shape would deploy an agent missing its tools.
    assert!(
        error.contains("connectors/mcp.ts has a default export but no `connector` export"),
        "{error}"
    );
    assert!(error.contains("export const connector ="), "{error}");
}

#[test]
fn compile_skips_python_connector_without_named_connector() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "connectors/mcp.py",
        concat!(
            "from managed_deepagents import define_mcp\n",
            "connector = define_mcp(servers={\"docs\": {\"transport\": \"http\", \"url\": \"https://docs.example.com/mcp\"}})\n",
        ),
    );
    // Co-located unit test (filename pattern) / helper (no `connector =`).
    write_file(
        root.path(),
        "connectors/test_mcp.py",
        concat!(
            "import pytest\n",
            "from connectors.mcp import connector\n",
            "def test_connector_kind():\n",
            "    assert connector.kind == \"mcp_servers\"\n",
        ),
    );
    write_file(
        root.path(),
        "connectors/helper.py",
        "SHARED_URL = \"https://docs.example.com/mcp\"\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("connectors/helper.py"),
            PathBuf::from("connectors/mcp.py"),
            PathBuf::from("connectors/test_mcp.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();
    assert_eq!(summary.connectors, vec![PathBuf::from("connectors/mcp.py")]);
    assert!(summary.warnings.iter().any(|warning| {
        warning.contains("connectors/") && warning.contains("removed in 0.8.0")
    }));

    let connectors_out = fs::read_to_string(out_dir.join("_mda_connectors.py")).unwrap();
    assert!(connectors_out.contains("import connectors.mcp as _connector0"));
    assert!(!connectors_out.contains("test_mcp"));
    assert!(!connectors_out.contains("helper"));
    assert!(out_dir.join("connectors/test_mcp.py").exists());
    assert!(out_dir.join("connectors/helper.py").exists());
}

#[test]
fn compile_omits_connectors_wiring_when_absent() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("connectors"));

    // No connectors means no aggregator module is generated.
    assert!(!out_dir.join("_mda_connectors.ts").exists());

    // The built-in MCP adapter still rides along in the managed stack, since
    // the CLI no longer infers connector dependencies from `connectors/`.
    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    assert_eq!(
        manifest["dependencies"]["@langchain/mcp-adapters"],
        managed_mcp_adapter_version()
    );
}

// Ingress channels (Slack `on:`) and connectors (GitHub `events:`), aggregators,
// and the managed HTTP app. Helpers / co-located tests under plugin dirs must not be
// imported; HTTP wiring uses live `_mda_connectors`, never compile-time embeds.

#[test]
fn compile_skips_typescript_ingress_channel_helpers() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        concat!(
            "import { defineIdentity } from \"managed-deepagents\";\n",
            "export const identity = defineIdentity({ auth: \"backend\" });\n",
        ),
    );
    write_file(
        root.path(),
        "channels/slack.ts",
        concat!(
            "import { channels } from \"managed-deepagents\";\n",
            "export const channel = channels.slack({ name: \"Support Bot\" });\n",
        ),
    );
    write_file(
        root.path(),
        "channels/slack.test.ts",
        concat!(
            "throw new Error(\n",
            "  \"channels/slack.test.ts must not be imported at managed runtime startup\"\n",
            ");\n",
        ),
    );
    write_file(
        root.path(),
        "channels/helper.ts",
        "export const SHARED_ON = [\"app_mention\"] as const;\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("identity.ts"),
            PathBuf::from("channels/helper.ts"),
            PathBuf::from("channels/slack.test.ts"),
            PathBuf::from("channels/slack.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();
    assert_eq!(summary.channels, vec![PathBuf::from("channels/slack.ts")]);

    let channels_out = fs::read_to_string(out_dir.join("_mda_channels.ts")).unwrap();
    assert!(channels_out.contains("from \"./channels/slack\""));
    assert!(!channels_out.contains("slack.test"));
    assert!(!channels_out.contains("helper"));
    assert!(out_dir.join("channels/slack.test.ts").exists());
    assert!(out_dir.join("channels/helper.ts").exists());
}

#[test]
fn compile_skips_python_ingress_channel_helpers() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "identity.py",
        concat!(
            "from managed_deepagents import define_identity\n",
            "identity = define_identity(auth=\"backend\")\n",
        ),
    );
    write_file(
        root.path(),
        "channels/slack.py",
        concat!(
            "from managed_deepagents import channels\n",
            "channel = channels.slack(name=\"Support Bot\")\n",
        ),
    );
    write_file(
        root.path(),
        "channels/test_slack.py",
        concat!(
            "raise RuntimeError(\n",
            "    \"channels/test_slack.py must not be imported at managed runtime startup\"\n",
            ")\n",
        ),
    );
    write_file(
        root.path(),
        "channels/helper.py",
        "SHARED_ON = [\"app_mention\"]\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("identity.py"),
            PathBuf::from("channels/helper.py"),
            PathBuf::from("channels/slack.py"),
            PathBuf::from("channels/test_slack.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();
    assert_eq!(summary.channels, vec![PathBuf::from("channels/slack.py")]);

    let channels_out = fs::read_to_string(out_dir.join("_mda_channels.py")).unwrap();
    assert!(channels_out.contains("import channels.slack"));
    assert!(!channels_out.contains("test_slack"));
    assert!(!channels_out.contains("helper"));
    assert!(out_dir.join("channels/test_slack.py").exists());
    assert!(out_dir.join("channels/helper.py").exists());
}

#[test]
fn compile_requires_identity_for_ingress_channel() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "channels/slack.ts",
        concat!(
            "import { channels } from \"managed-deepagents\";\n",
            "export const channel = channels.slack({ name: \"Support Bot\" });\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("channels/slack.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        error.contains("require a root identity"),
        "expected identity gate for ingress connectors, got: {error}"
    );
}

#[test]
fn compile_requires_identity_for_python_ingress_channel() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "channels/slack.py",
        concat!(
            "from managed_deepagents import channels\n",
            "channel = channels.slack(name=\"Support Bot\")\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = []\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("channels/slack.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = with_py_runtime(root.path(), || compile(&project, &out_dir))
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("require a root identity"),
        "expected identity gate for ingress channel, got: {error}"
    );
}

#[test]
fn compile_requires_identity_for_github_channel() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "channels/github.ts",
        concat!(
            "import { channels } from \"managed-deepagents\";\n",
            "export const channel = channels.github({ onEvent: {} });\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("channels/github.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(
        error.contains("require a root identity"),
        "expected identity gate for GitHub channel, got: {error}"
    );
}

#[test]
fn compile_requires_identity_for_python_github_channel() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "channels/github.py",
        concat!(
            "from managed_deepagents import channels\n",
            "channel = channels.github(on_event={})\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = []\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("channels/github.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = with_py_runtime(root.path(), || compile(&project, &out_dir))
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("require a root identity"),
        "expected identity gate for GitHub channel, got: {error}"
    );
}

#[test]
fn compile_wires_typescript_http_app_to_live_channels() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        concat!(
            "import { defineIdentity } from \"managed-deepagents\";\n",
            "export const identity = defineIdentity({ auth: \"backend\" });\n",
        ),
    );
    write_file(
        root.path(),
        "channels/slack.ts",
        concat!(
            "import { channels } from \"managed-deepagents\";\n",
            "export const channel = channels.slack({ name: \"Support Bot\" });\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {}\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("identity.ts"),
            PathBuf::from("channels/slack.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let channels_out = fs::read_to_string(out_dir.join("_mda_channels.ts")).unwrap();
    assert!(channels_out.contains("collectChannels(["));
    assert!(channels_out.contains("from \"./channels/slack\""));

    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.ts")).unwrap();
    assert!(
        auth_out.contains("import \"./_mda_channels\";"),
        "auth must load declared channel providers: {auth_out}"
    );
    assert!(!auth_out.contains("_mda_http"));

    let http_out = fs::read_to_string(out_dir.join("_mda_http.ts")).unwrap();
    assert!(
        http_out.contains(
            "buildManagedHttpApp({ identity, channels, root: fileURLToPath(new URL(\".\", import.meta.url)) })"
        ),
        "expected channels kwargs in buildManagedHttpApp call:\n{http_out}"
    );
    assert!(
        !http_out.contains("_mda_connectors"),
        "HTTP module must not wire connectors when none are declared:\n{http_out}"
    );
}

#[test]
fn compile_wires_python_http_app_to_live_channels() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "identity.py",
        concat!(
            "from managed_deepagents import define_identity\n",
            "identity = define_identity(auth=\"backend\")\n",
        ),
    );
    write_file(
        root.path(),
        "channels/slack.py",
        concat!(
            "from managed_deepagents import channels\n",
            "channel = channels.slack(name=\"Support Bot\")\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("identity.py"),
            PathBuf::from("channels/slack.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let channels_out = fs::read_to_string(out_dir.join("_mda_channels.py")).unwrap();
    assert!(channels_out.contains("channels = collect_channels(["));
    assert!(channels_out.contains("import channels.slack"));

    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.py")).unwrap();
    assert!(
        auth_out.contains("import _mda_channels"),
        "auth must load declared channel providers: {auth_out}"
    );
    assert!(!auth_out.contains("_mda_http"));

    let http_out = fs::read_to_string(out_dir.join("_mda_http.py")).unwrap();
    assert!(
        http_out.contains("channels=_channels, root=str(_Path(__file__).resolve().parent)"),
        "expected channels kwargs in build_managed_http_app call:\n{http_out}"
    );
    assert!(
        !http_out.contains("_mda_connectors"),
        "HTTP module must not wire connectors when none are declared:\n{http_out}"
    );
}

// Identity wiring and the generated auth module.

#[test]
fn compile_rejects_identity_that_omits_auth() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        "export const identity = defineIdentity();\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("identity.ts")],
    };

    let error = compile(&project, &root.path().join("build"))
        .unwrap_err()
        .to_string();

    assert!(
        error.contains("must select authentication explicitly"),
        "{error}"
    );
}

#[test]
fn compile_for_deploy_keeps_studio_auth_enabled() {
    // Deploy leaves the Studio scheme on so LangSmith Studio and the CLI's
    // schedule reconciliation can still reach the deployment. The runtime, not
    // the manifest, is what stops a Studio principal reading other callers'
    // threads.
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        concat!(
            "import { defineIdentity } from \"managed-deepagents\";\n",
            "export const identity = defineIdentity({ auth: \"backend\" });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("identity.ts")],
    };
    let deploy_context = DeployContext {
        repo: "my-agent".to_string(),
        has_skills: false,
    };

    let out_dir = root.path().join(".mda").join("build");
    compile_for_deploy(&project, &out_dir, &deploy_context).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(manifest["auth"]["disable_studio_auth"], false);
}

#[test]
fn compile_wires_typescript_identity() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        concat!(
            "import { defineIdentity } from \"managed-deepagents\";\n",
            "export const identity = defineIdentity({ auth: \"backend\" });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("identity.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    // The entry imports the identity declaration and passes it to the runtime.
    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("import { identity } from \"./identity\";"));
    assert!(entry_out.contains(
        "compileManagedAgent(definition, config, { contextRoot, hasSkills, identity }, runtime)"
    ));

    // The thin custom-auth module is generated and delegates to the runtime.
    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.ts")).unwrap();
    assert!(auth_out.contains("import { buildManagedAuth } from \"managed-deepagents/runtime\";"));
    assert!(auth_out.contains("import { identity } from \"./identity\";"));
    assert!(auth_out.contains("export const auth = buildManagedAuth(identity);"));

    let identity_http_out = fs::read_to_string(out_dir.join("_mda_http.ts")).unwrap();
    assert!(identity_http_out
        .contains("import { buildManagedHttpApp } from \"managed-deepagents/runtime\";"));
    assert!(identity_http_out.contains("export const app = buildManagedHttpApp({ identity });"));

    // langgraph.json points at the auth module and allowlists reserved headers.
    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(manifest["auth"]["path"], "./_mda_auth.ts:auth");
    assert_eq!(manifest["http"]["app"], "./_mda_http.ts:app");
    assert_eq!(manifest["http"]["middleware_order"], "middleware_first");
    // Local builds turn off the Studio auth scheme: the dev server grants that
    // identity on the `x-auth-scheme` header alone, with no credential check.
    assert_eq!(manifest["auth"]["disable_studio_auth"], true);
    assert_eq!(
        manifest["http"]["configurable_headers"]["includes"],
        serde_json::json!(["x-mda-user-id",])
    );

    // The identity module is carried over verbatim.
    assert!(out_dir.join("identity.ts").exists());
}

#[test]
fn compile_wires_python_identity() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "identity.py",
        concat!(
            "from managed_deepagents import define_identity\n",
            "identity = define_identity(auth=\"backend\")\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("identity.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from identity import identity as _identity"));
    assert!(
        entry_out.contains(
            "await compile_managed_agent_async(_definition, config, context_root=_context_root, has_skills=_has_skills, identity=_identity, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)",
        )
    );

    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.py")).unwrap();
    assert!(auth_out.contains("from managed_deepagents.runtime import build_managed_auth"));
    assert!(auth_out.contains("from identity import identity as _identity"));
    assert!(auth_out.contains("auth = build_managed_auth(_identity)"));

    let identity_http_out = fs::read_to_string(out_dir.join("_mda_http.py")).unwrap();
    assert!(
        identity_http_out.contains("from managed_deepagents.runtime import build_managed_http_app")
    );
    assert!(identity_http_out.contains("from identity import identity as _identity"));
    assert!(identity_http_out.contains("app = build_managed_http_app(identity=_identity)"));

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert_eq!(manifest["auth"]["path"], "./_mda_auth.py:auth");
    assert_eq!(manifest["http"]["app"], "./_mda_http.py:app");
    assert_eq!(manifest["http"]["middleware_order"], "middleware_first");
}

#[test]
fn compile_omits_identity_wiring_when_absent() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("identity"));
    assert!(!out_dir.join("_mda_auth.ts").exists());

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("langgraph.json")).unwrap()).unwrap();
    assert!(manifest.get("auth").is_none());
    assert!(manifest.get("http").is_none());
}

// Vendoring the managed runtime into the build.

#[test]
fn compile_vendors_typescript_runtime_into_build() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(out_dir.join("__runtime__/index.js").exists());
    assert!(out_dir.join("__runtime__/runtime/index.js").exists());
}

#[test]
fn compile_vendors_python_runtime_into_build() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert!(out_dir.join("__runtime__/pyproject.toml").exists());
    let vendored_pyproject =
        fs::read_to_string(out_dir.join("__runtime__/pyproject.toml")).unwrap();
    assert!(
        vendored_pyproject.contains("pydantic"),
        "vendored runtime must declare pydantic for Host/emit installs:\n{vendored_pyproject}"
    );
    assert!(out_dir
        .join("__runtime__/managed_deepagents/__init__.py")
        .exists());
    assert!(out_dir
        .join("__runtime__/managed_deepagents/runtime.py")
        .exists());
    assert!(
        out_dir
            .join("__runtime__/managed_deepagents/_connectors/__init__.py")
            .exists()
            || out_dir
                .join("__runtime__/managed_deepagents/_connectors.py")
                .exists()
    );
}

// Managed dependencies, lockfiles, and Python packaging.

#[test]
fn compile_makes_python_build_deps_only_installable() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "tools/query_db.py",
        "def query_db():\n    pass\n",
    );
    write_file(
        root.path(),
        "middleware/audit.py",
        "def audit():\n    pass\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\", \"managed-deepagents>=0.1.0\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("tools/query_db.py"),
            PathBuf::from("middleware/audit.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };
    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(
        pyproject.contains("build-backend = \"setuptools.build_meta\""),
        "got: {pyproject}"
    );
    assert!(pyproject.contains("py-modules = []"), "got: {pyproject}");
    assert!(pyproject.contains("deepagents>=0.7.3"));
    assert!(
        pyproject.contains("managed-deepagents @ file://"),
        "expected file:// URI for managed-deepagents, got: {pyproject}"
    );
}

#[test]
fn compile_synthesizes_pyproject_when_author_omits_it() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"langsmith:moonshotai/kimi-k3\")\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py")],
    };
    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(
        pyproject.contains("name = \"mda-agent\""),
        "got: {pyproject}"
    );
    assert!(
        pyproject.contains("build-backend = \"setuptools.build_meta\""),
        "got: {pyproject}"
    );
    assert!(pyproject.contains("py-modules = []"), "got: {pyproject}");
    assert!(
        pyproject.contains("managed-deepagents @ file://"),
        "expected injected runtime dep, got: {pyproject}"
    );
    assert!(
        pyproject.contains("langchain-openai"),
        "expected the LangSmith provider's OpenAI integration package, got: {pyproject}"
    );
}

#[test]
fn compile_injects_managed_stack_and_provider_for_typescript() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"langsmith:moonshotai/kimi-k3\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"managed-deepagents\": \"^0.0.2\", \"deepagents\": \"1.12.1\" }\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    let deps = &manifest["dependencies"];
    // Runtime is an npm workspace package, not a `file:` dependency.
    assert!(deps.get("managed-deepagents").is_none());
    assert_eq!(manifest["workspaces"], serde_json::json!(["__runtime__"]));
    assert_eq!(manifest["private"], serde_json::json!(true));
    // Other author-declared packages are preserved.
    assert_eq!(deps["deepagents"], "1.12.1");
    // The managed runtime stack is injected as compatible ranges.
    assert_eq!(deps["@langchain/core"], "^1.2.17");
    assert_eq!(deps["@langchain/langgraph"], "^1.4.18");
    assert_eq!(deps["@langchain/langgraph-checkpoint"], "^1.1.3");
    assert_eq!(deps["@langchain/langgraph-sdk"], "^1.12.0");
    assert_eq!(deps["langchain"], "^1.5.12");
    assert_eq!(deps["uuid"], "^13.0.2");
    // The `langsmith:` provider uses the OpenAI integration package to speak
    // the LangSmith Gateway's OpenAI-compatible API.
    assert_eq!(deps["@langchain/openai"], "^1.5.5");
}

#[test]
fn compile_materializes_an_npm_lockfile_for_typescript_builds() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"managed-deepagents\": \"^0.0.2\" }\n}\n",
    );
    // Stale lockfile copied from the author's tree must not survive compile.
    write_file(
        root.path(),
        "package-lock.json",
        "{\n  \"name\": \"example\",\n  \"lockfileVersion\": 3,\n  \"requires\": true,\n  \"packages\": {\n    \"\": {\n      \"name\": \"example\",\n      \"dependencies\": {\n        \"managed-deepagents\": \"^0.0.2\"\n      }\n    }\n  }\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("package.json"),
            PathBuf::from("package-lock.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let lockfile = fs::read_to_string(out_dir.join("package-lock.json")).unwrap();
    assert!(
        lockfile.contains("mda-test-stub"),
        "compile must replace the author lockfile"
    );
    assert!(
        !lockfile.contains("\"managed-deepagents\": \"^0.0.2\""),
        "stale author packages entry must not survive"
    );
    assert!(out_dir.join(".mda-lock-input").is_file());
}

/// Live registry check for lockfile materialization. Ignored in the default
/// suite so unit tests stay offline and fast; run with
/// `cargo test -p mda -- --ignored`.
#[test]
#[ignore = "hits the npm registry; run explicitly with --ignored"]
fn compile_materializes_a_registry_npm_lockfile_for_typescript_builds() {
    if !npm_available() {
        return;
    }
    with_live_npm_lockfile(|| {
        let root = tempfile::tempdir().unwrap();
        setup_mock_ts_runtime(root.path());
        write_file(
            root.path(),
            "agent.ts",
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n",
        );
        write_file(
            root.path(),
            "package.json",
            "{\n  \"name\": \"example\",\n  \"dependencies\": { \"managed-deepagents\": \"^0.0.2\" }\n}\n",
        );

        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
        };

        let out_dir = root.path().join(".mda").join("build");
        compile(&project, &out_dir).unwrap();

        let lockfile = fs::read_to_string(out_dir.join("package-lock.json")).unwrap();
        assert!(lockfile.contains("@langchain/openai"));
        assert!(lockfile.contains("@langchain/mcp-adapters"));

        let mut npm = toolchain::npm();
        npm.args(["install", "--ignore-scripts"])
            .current_dir(&out_dir);
        let output =
            toolchain::output_with_timeout(&mut npm, std::time::Duration::from_secs(90)).unwrap();
        assert!(
            output.status.success(),
            "npm install failed against the materialized lockfile: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    });
}

#[test]
fn compile_resolves_authored_langchain_and_mcp_tool_dependencies() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "import { authoredTool } from \"./tools/authored-tool\";\n",
            "export const agent = defineDeepAgent({\n",
            "  name: \"agent\",\n",
            "  model: \"openai:gpt-5.5\",\n",
            "  tools: [authoredTool],\n",
            "});\n",
        ),
    );
    write_file(
        root.path(),
        "tools/authored-tool.ts",
        concat!(
            "import { BaseMessage } from \"@langchain/core/messages\";\n",
            "import { MultiServerMCPClient } from \"@langchain/mcp-adapters\";\n",
            "import { tool } from \"langchain\";\n",
            "import { z } from \"zod\";\n",
            "\n",
            "void BaseMessage;\n",
            "void MultiServerMCPClient;\n",
            "export const authoredTool = tool(\n",
            "  ({ query }) => query,\n",
            "  { name: \"search\", schema: z.object({ query: z.string() }) },\n",
            ");\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"langchain\": \"1.5.10\" }\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("tools/authored-tool.ts"),
            PathBuf::from("package.json"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    let deps = &manifest["dependencies"];
    assert_eq!(deps["@langchain/core"], "^1.2.17");
    assert_eq!(deps["langchain"], "1.5.10");
    assert_eq!(
        deps["@langchain/mcp-adapters"],
        managed_mcp_adapter_version()
    );
    assert_eq!(deps["deepagents"], "^1.12.1");
    assert!(out_dir.join("package-lock.json").is_file());
}

#[test]
fn compile_for_deploy_strips_dev_dependencies_before_lockfile() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n",
    );
    write_file(
        root.path(),
        "package.json",
        r#"{
  "name": "example",
  "dependencies": { "managed-deepagents": "^0.0.2" },
  "devDependencies": { "vite": "^8.0.14", "wrangler": "^4.112.0" }
}
"#,
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };
    let deploy_context = DeployContext {
        repo: "example".to_string(),
        has_skills: false,
    };

    let out_dir = root.path().join(".mda").join("build");
    compile_for_deploy(&project, &out_dir, &deploy_context).unwrap();

    let package_json = fs::read_to_string(out_dir.join("package.json")).unwrap();
    let manifest: serde_json::Value = serde_json::from_str(&package_json).unwrap();
    assert!(
        manifest.get("devDependencies").is_none(),
        "deploy build must not ship author UI tooling as installable deps"
    );
    assert!(
        !package_json.contains("vite"),
        "stripped package.json must not still name vite"
    );

    // Lockfile is stubbed in unit tests; the strip above is what keeps vite out
    // of a real `npm install --package-lock-only` input. Registry coverage lives
    // in `compile_materializes_a_registry_npm_lockfile_for_typescript_builds`.
    assert!(out_dir.join("package-lock.json").is_file());
}

#[test]
fn compile_injects_managed_stack_and_provider_for_python() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"anthropic:claude-sonnet-4-5\")\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\n    \"deepagents>=0.7.3\",\n    \"managed-deepagents>=0.1.2\",\n]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    // Author-declared packages are preserved (not duplicated).
    assert_eq!(pyproject.matches("deepagents>=0.7.3").count(), 1);
    // The managed runtime stack is injected.
    assert!(
        pyproject.contains("langgraph>=1.2.15,<2"),
        "got: {pyproject}"
    );
    assert!(
        pyproject.contains("langgraph-prebuilt>=1.1.1,<2"),
        "got: {pyproject}"
    );
    assert!(
        pyproject.contains("langgraph-checkpoint>=4.2.0"),
        "got: {pyproject}"
    );
    assert!(pyproject.contains("langsmith>=0.10.17"), "got: {pyproject}");
    assert!(
        pyproject.contains("langchain[mcp]>=1.4.6,<2"),
        "got: {pyproject}"
    );
    assert!(
        pyproject.contains("langgraph-api>=0.16.0"),
        "got: {pyproject}"
    );
    assert!(
        pyproject.contains("langgraph-cli[inmem]>=0.4.36"),
        "got: {pyproject}"
    );
    // The provider package implied by `anthropic:...` is injected — but its
    // model is read via Python's `ast`, so only assert when `python3` runs.
    if python3_available() {
        assert!(
            pyproject.contains("langchain-anthropic>=1.5.4"),
            "got: {pyproject}"
        );
    }
}

#[test]
fn compile_for_eval_injects_explicit_override_provider_for_python() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=object())\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents\"]\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };
    let out_dir = root.path().join("build");

    with_py_runtime(root.path(), || {
        compile_for_eval_with_model_override(&project, &out_dir, "anthropic:claude-sonnet-4-5")
    })
    .unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(
        pyproject.contains("langchain-anthropic>=1.5.4"),
        "got: {pyproject}"
    );
}

#[test]
fn factory_python_eval_injects_only_explicit_override_provider() {
    if !python3_available() {
        return;
    }
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        concat!(
            "def build_agent(_ctx):\n",
            "    return define_deep_agent(name=\"selected\", model=\"openai:gpt-5\")\n",
            "\n",
            "agent = build_agent\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents\"]\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };
    let out_dir = root.path().join("build");

    with_py_runtime(root.path(), || {
        compile_for_eval_with_model_override(&project, &out_dir, "anthropic:claude-sonnet-4-5")
    })
    .unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(
        pyproject.contains("langchain-anthropic>=1.5.4"),
        "got: {pyproject}"
    );
    assert!(!pyproject.contains("langchain-openai"), "got: {pyproject}");
}

#[test]
fn factory_typescript_eval_injects_only_explicit_override_provider() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "export const agent = () =>\n",
            "  defineDeepAgent({ name: \"selected\", model: \"openai:gpt-5\" })\n",
            ";\n",
        ),
    );
    write_file(
        root.path(),
        "package.json",
        "{\"name\":\"example\",\"dependencies\":{}}\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };
    let out_dir = root.path().join("build");

    compile_for_eval_with_model_override(&project, &out_dir, "anthropic:claude-sonnet-4-5")
        .unwrap();

    let package: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    assert!(package["dependencies"]
        .get("@langchain/anthropic")
        .is_some());
    assert!(package["dependencies"].get("@langchain/openai").is_none());
}

#[test]
fn eval_dependency_injection_prefers_explicit_override_for_typescript() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "package.json",
        "{\"name\":\"example\",\"dependencies\":{}}\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("package.json")],
    };

    super::manifest::inject_managed_dependencies(
        &project,
        root.path(),
        true,
        Some("openai:gpt-5-mini"),
        Some("anthropic:claude-sonnet-4-5"),
        false,
    )
    .unwrap();

    let package: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(root.path().join("package.json")).unwrap())
            .unwrap();
    assert!(package["dependencies"]
        .get("@langchain/anthropic")
        .is_some());
    assert!(package["dependencies"].get("@langchain/openai").is_none());
}

#[test]
fn compile_dedups_a_python_dependency_declared_with_underscores() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    // The author declares a managed package using the PEP 503-equivalent
    // underscore spelling. The canonical-name dedup must treat it as the
    // same package as the managed `langgraph-checkpoint` and not inject a
    // hyphenated duplicate alongside it.
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"langgraph_checkpoint>=4.1.1\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert_eq!(
        pyproject.matches("langgraph_checkpoint").count(),
        1,
        "got: {pyproject}"
    );
    assert!(
        !pyproject.contains("langgraph-checkpoint"),
        "got: {pyproject}"
    );
}

#[test]
fn compile_respects_an_author_pinned_managed_dependency() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n",
    );
    // The author overrides the managed `langchain` range with an exact version
    // accepted by the current deepagents peer, so lockfile generation still
    // resolves; the point of this test is that compile does not clobber the
    // author-declared version.
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": { \"langchain\": \"1.5.10\" }\n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    // The author's version wins; the managed pin does not clobber it.
    assert_eq!(manifest["dependencies"]["langchain"], "1.5.10");
}

#[test]
fn compile_skips_provider_injection_for_unreadable_models() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "const m = pickModel();\nexport const agent = defineDeepAgent({ name: \"agent\", model: m });\n",
    );
    write_file(
        root.path(),
        "package.json",
        "{\n  \"name\": \"example\",\n  \"dependencies\": {} \n}\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join("package.json")).unwrap()).unwrap();
    let deps = manifest["dependencies"].as_object().unwrap();
    // The managed runtime stack is still injected.
    assert!(deps.contains_key("@langchain/core"));
    // But no provider package is guessed when the model isn't a literal.
    assert!(!deps.keys().any(|k| k.starts_with("@langchain/openai")));
}

#[test]
fn compile_respects_an_existing_python_build_system() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[build-system]\nrequires = [\"hatchling\"]\nbuild-backend = \"hatchling.build\"\n\n[project]\nname = \"example\"\nversion = \"0.0.0\"\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };
    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(pyproject.contains("hatchling.build"));
    assert!(!pyproject.contains("setuptools.build_meta"));
    assert!(!pyproject.contains("py-modules"));
}

#[test]
fn compile_wires_typescript_memory() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        concat!(
            "import { defineMemory } from \"managed-deepagents\";\n",
            "export const memory = defineMemory({ scope: [{ type: \"agent\" }] });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("memory.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();

    // Agent memory does not require identity.
    assert!(summary.memory.agent_enabled());
    assert_eq!(
        summary.memory.path().map(PathBuf::as_path),
        Some(Path::new("memory.ts"))
    );

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("import { memory } from \"./memory\";"));
    assert!(entry_out.contains(
        "compileManagedAgent(definition, config, { contextRoot, hasSkills, memory }, runtime)"
    ));

    // Carried over verbatim, like every other authored declaration.
    assert!(out_dir.join("memory.ts").exists());
}

#[test]
fn compile_preserves_named_memory_policies() {
    for language in [Language::TypeScript, Language::Python] {
        let root = tempfile::tempdir().unwrap();
        let agent_file = format!("agent.{}", language.extension());
        let identity_file = format!("identity.{}", language.extension());
        let memory_file = format!("memory.{}", language.extension());
        write_file(
            root.path(),
            &agent_file,
            language.ident(
                "export const agent = defineDeepAgent({ name: 'agent', model: 'openai:gpt-5' });\n",
                "agent = define_deep_agent(name='agent', model='openai:gpt-5')\n",
            ),
        );
        write_file(
            root.path(),
            &identity_file,
            language.ident(
                "export const identity = defineIdentity({ auth: 'backend' });\n",
                "identity = define_identity(auth='backend')\n",
            ),
        );
        let memory_source = language.ident(
            "import { defineMemory as dm, memoryLayer } from 'managed-deepagents';\n\
             const allow = async () => { throw new Error('must not run at build time'); };\n\
             export const memory = dm({ agent: memoryLayer({ allow }), user: memoryLayer({ allow }) });\n",
            "from managed_deepagents import define_memory as dm, MemoryLayer\n\
             async def allow(context):\n    raise RuntimeError('must not run at build time')\n\
             memory = dm(agent=MemoryLayer(allow=allow), user=MemoryLayer(allow=allow))\n",
        );
        write_file(root.path(), &memory_file, memory_source);
        let mut files = vec![
            PathBuf::from(&agent_file),
            PathBuf::from(identity_file),
            PathBuf::from(&memory_file),
        ];
        if language == Language::TypeScript {
            setup_mock_ts_runtime(root.path());
        } else {
            write_file(
                root.path(),
                "pyproject.toml",
                "[project]\nname = 'example'\nversion = '0.0.0'\ndependencies = []\n",
            );
            files.push(PathBuf::from("pyproject.toml"));
        }
        let project = Project {
            root: root.path().to_path_buf(),
            language,
            agent_file: PathBuf::from(agent_file),
            files,
        };
        let out_dir = root.path().join("build");
        let summary = match language {
            Language::TypeScript => compile(&project, &out_dir),
            Language::Python => with_py_runtime(root.path(), || compile(&project, &out_dir)),
        }
        .unwrap();

        assert!(summary.memory.agent_enabled());
        assert!(summary.memory.user_enabled());
        assert_eq!(
            fs::read_to_string(out_dir.join(memory_file)).unwrap(),
            memory_source
        );
        let entry_out = fs::read_to_string(out_dir.join(summary.entry_file)).unwrap();
        assert!(entry_out.contains(language.ident(
            "import { memory } from \"./memory\";",
            "from memory import memory as _memory",
        )));
        assert!(entry_out.contains(language.ident(
            "identity, memory }, runtime)",
            "identity=_identity, memory=_memory, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)",
        )));
        assert!(!entry_out.contains("must not run at build time"));
    }
}

#[test]
fn compile_rejects_user_memory_without_identity() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        "export const memory = defineMemory({ scope: [{ type: \"user\" }] });\n",
    );
    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("memory.ts")],
    };

    let error = compile(&project, &root.path().join("build"))
        .unwrap_err()
        .to_string();

    assert!(
        error.contains("user memory requires identity.ts"),
        "{error}"
    );
}

#[test]
fn compile_wires_python_memory() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "memory.py",
        concat!(
            "from managed_deepagents import define_memory\n",
            "memory = define_memory(scope=[{\"type\": \"agent\"}])\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("memory.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    assert!(summary.memory.agent_enabled());

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from memory import memory as _memory"));
    assert!(entry_out.contains(
        "await compile_managed_agent_async(_definition, config, context_root=_context_root, \
         has_skills=_has_skills, memory=_memory, async_subagent_graphs=_async_subagent_graphs, runtime=runtime)"
    ));
}

#[test]
fn compile_wires_python_browser_and_adds_stagehand() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "browser.py",
        concat!(
            "from managed_deepagents import define_browser\n",
            "browser = define_browser(scope=\"user\")\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("browser.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from browser import browser as _browser"));
    assert!(entry_out.contains(
        "await compile_managed_agent_async(_definition, config, context_root=_context_root, \
         has_skills=_has_skills, browser=_browser, runtime=runtime)"
    ));
    let pyproject = fs::read_to_string(out_dir.join("pyproject.toml")).unwrap();
    assert!(pyproject.contains("stagehand==4.3.0"));

    // Eval trials never get a browser, so they never install its client.
    let eval_dir = root.path().join("eval");
    with_py_runtime(root.path(), || compile_for_eval(&project, &eval_dir)).unwrap();
    let eval_pyproject = fs::read_to_string(eval_dir.join("pyproject.toml")).unwrap();
    assert!(!eval_pyproject.contains("stagehand"));
}

#[test]
fn compile_ignores_browser_in_typescript() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(root.path(), "browser.py", "browser = None\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("browser.py")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("browser"));
}

#[test]
fn compile_omits_memory_when_the_project_declares_none() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();

    assert!(!summary.memory.agent_enabled());
    assert!(!summary.memory.user_enabled());
    assert_eq!(summary.memory.path(), None);

    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(!entry_out.contains("memory"), "{entry_out}");
}

#[test]
fn compile_keeps_memory_off_the_generated_auth_module() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        "export const identity = defineIdentity({ auth: \"backend\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        "export const memory = defineMemory({ scope: [{ type: \"agent\" }] });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("identity.ts"),
            PathBuf::from("memory.ts"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    // Auth takes identity only; memory mounts are wired through the graph entry.
    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.ts")).unwrap();
    assert!(!auth_out.contains("memory"));
    assert!(auth_out.contains("export const auth = buildManagedAuth(identity);"));
    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.ts")).unwrap();
    assert!(entry_out.contains("import { memory } from \"./memory\";"));
}

#[test]
fn compile_keeps_memory_off_the_generated_python_auth_module() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "identity.py",
        concat!(
            "from managed_deepagents import define_identity\n",
            "identity = define_identity(auth=\"backend\")\n",
        ),
    );
    write_file(
        root.path(),
        "memory.py",
        concat!(
            "from managed_deepagents import define_memory\n",
            "memory = define_memory(scope=[{\"type\": \"agent\"}])\n",
        ),
    );
    write_file(
        root.path(),
        "pyproject.toml",
        "[project]\nname = \"example\"\nversion = \"0.0.0\"\ndependencies = [\"deepagents>=0.7.3\"]\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("identity.py"),
            PathBuf::from("memory.py"),
            PathBuf::from("pyproject.toml"),
        ],
    };

    let out_dir = root.path().join("build");
    with_py_runtime(root.path(), || compile(&project, &out_dir)).unwrap();

    // Regression: passing memory crashed Host boot with
    // `build_managed_auth() takes 1 positional argument but 2 were given`.
    let auth_out = fs::read_to_string(out_dir.join("_mda_auth.py")).unwrap();
    assert!(!auth_out.contains("memory"), "{auth_out}");
    assert!(auth_out.contains("auth = build_managed_auth(_identity)"));
    let entry_out = fs::read_to_string(out_dir.join("_mda_entry.py")).unwrap();
    assert!(entry_out.contains("from memory import memory as _memory"));
}

#[test]
fn compile_for_eval_never_mounts_declared_memory() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        "export const memory = defineMemory({ scope: [{ type: \"agent\" }] });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("memory.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    compile_for_eval(&project, &out_dir).unwrap();

    // Harbor trials get no durable memory, declared or not.
    let entry_out = fs::read_to_string(out_dir.join("_mda_eval_entry.ts")).unwrap();
    assert!(entry_out.contains("compileManagedAgentForEval"));
    assert!(!entry_out.contains("memory"), "{entry_out}");
}

#[test]
fn compile_for_eval_mounts_the_projects_skills() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "skills/research-brief/SKILL.md",
        "---\nname: research-brief\ndescription: Write a brief.\n---\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![
            PathBuf::from("agent.ts"),
            PathBuf::from("skills/research-brief/SKILL.md"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile_for_eval(&project, &out_dir).unwrap();

    assert!(summary.has_skills);
    // Harbor copies the artifact project into the trial workspace, so the trial
    // reads the same `skills/` tree carried into the build.
    assert!(out_dir.join("skills/research-brief/SKILL.md").is_file());
    let entry_out = fs::read_to_string(out_dir.join("_mda_eval_entry.ts")).unwrap();
    assert!(entry_out.contains("const hasSkills = true;"), "{entry_out}");
    assert!(
        entry_out.contains("compileManagedAgentForEval(definition, config, { hasSkills })"),
        "{entry_out}"
    );
}

#[test]
fn compile_for_eval_mounts_no_skills_without_a_skills_directory() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let summary = compile_for_eval(&project, &out_dir).unwrap();

    assert!(!summary.has_skills);
    let entry_out = fs::read_to_string(out_dir.join("_mda_eval_entry.ts")).unwrap();
    assert!(
        entry_out.contains("const hasSkills = false;"),
        "{entry_out}"
    );
}

#[test]
fn compile_for_eval_mounts_the_projects_skills_for_python() {
    let root = tempfile::tempdir().unwrap();
    write_file(
        root.path(),
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        root.path(),
        "skills/research-brief/SKILL.md",
        "---\nname: research-brief\ndescription: Write a brief.\n---\n",
    );
    write_file(root.path(), "instructions.md", "# Research assistant\n");

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![
            PathBuf::from("agent.py"),
            PathBuf::from("instructions.md"),
            PathBuf::from("skills/research-brief/SKILL.md"),
        ],
    };

    let out_dir = root.path().join(".mda").join("build");
    with_py_runtime(root.path(), || compile_for_eval(&project, &out_dir)).unwrap();

    let entry_out = fs::read_to_string(out_dir.join("_mda_eval_entry.py")).unwrap();
    assert!(entry_out.contains("_has_skills = True"), "{entry_out}");
    assert!(
        entry_out.contains(
            "compile_managed_agent_for_eval(_definition, config, \
             system_prompt=_system_prompt, has_skills=_has_skills)"
        ),
        "{entry_out}"
    );
}

#[test]
fn compile_rejects_the_retired_disable_memory_flag() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        concat!(
            "export const agent = defineDeepAgent({\n",
            "  name: \"agent\",\n",
            "  model: \"openai:gpt-5\",\n",
            "  disableMemory: true,\n",
            "});\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(error.contains("no longer supported"), "{error}");
    assert!(error.contains("memory.ts"), "{error}");
}

#[test]
fn compile_rejects_the_retired_identity_memory_axis() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "identity.ts",
        "export const identity = defineIdentity({ scope: { memory: \"agent\" } });\n",
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("identity.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    assert!(error.contains("`scope.memory`"), "{error}");
    assert!(error.contains("memory.ts"), "{error}");
}

#[test]
fn compile_rejects_dynamic_memory_layers() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        concat!(
            "const scope = process.env.MEMORY_LAYERS;\n",
            "export const memory = defineMemory({ scope });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("memory.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    // Deploy seeds from this value, so unresolvable scope fail the build
    // instead of quietly seeding (or dropping) durable memory.
    assert!(error.contains("not a static literal"), "{error}");
    assert!(error.contains("Deploy seeds durable memory"), "{error}");
}

#[test]
fn compile_rejects_a_spread_that_can_override_memory_layers() {
    let root = tempfile::tempdir().unwrap();
    setup_mock_ts_runtime(root.path());
    write_file(
        root.path(),
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        root.path(),
        "memory.ts",
        concat!(
            "const override = { scope: [{ type: \"agent\" }] };\n",
            "export const memory = defineMemory({ scope: [], ...override });\n",
        ),
    );

    let project = Project {
        root: root.path().to_path_buf(),
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("memory.ts")],
    };

    let out_dir = root.path().join(".mda").join("build");
    let error = compile(&project, &out_dir).unwrap_err().to_string();
    // Reading the literal would report "none" while the runtime mounts agent
    // memory, so the build refuses rather than seeding the wrong tree.
    assert!(error.contains("not a static literal"), "{error}");
    assert!(error.contains("spread"), "{error}");
}

// Local packages outside the project root.

fn ts_project_with_local_package(dependency: &str) -> (tempfile::TempDir, Project) {
    let workspace = tempfile::tempdir().unwrap();
    let root = workspace.path().join("app");
    setup_mock_ts_runtime(&root);
    write_file(
        &root,
        "agent.ts",
        "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
    );
    write_file(
        &root,
        "package.json",
        &format!(
            r#"{{"name":"app","dependencies":{{"managed-deepagents":"^0.0.2","dep":"{dependency}"}}}}"#
        ),
    );
    write_file(
        workspace.path(),
        "dep/package.json",
        r#"{"name":"dep","version":"1.0.0","main":"index.js","dependencies":{"zod":"^3.0.0"},"devDependencies":{"vitest":"^3.0.0"}}"#,
    );
    write_file(
        workspace.path(),
        "dep/index.js",
        "export const helper = () => 1;\n",
    );
    write_file(workspace.path(), "dep/node_modules/zod/index.js", "");
    write_file(workspace.path(), "dep/.env", "SECRET=1\n");
    let project = Project {
        root,
        language: Language::TypeScript,
        agent_file: PathBuf::from("agent.ts"),
        files: vec![PathBuf::from("agent.ts"), PathBuf::from("package.json")],
    };
    (workspace, project)
}

fn read_json(path: &Path) -> serde_json::Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn compile_vendors_a_typescript_local_package_as_a_workspace() {
    let (_workspace, project) = ts_project_with_local_package("file:../dep");
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(out_dir.join("__local__/dep/index.js").is_file());
    assert!(out_dir.join("__local__/dep/package.json").is_file());
    assert!(!out_dir.join("__local__/dep/node_modules").exists());
    assert!(!out_dir.join("__local__/dep/.env").exists());

    let manifest = read_json(&out_dir.join("package.json"));
    assert!(manifest["dependencies"].get("dep").is_none());
    assert!(manifest["workspaces"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!("__local__/dep")));
}

#[test]
fn compile_vendors_a_pnpm_link_local_package_as_a_workspace() {
    let (_workspace, project) = ts_project_with_local_package("link:../dep");
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(out_dir.join("__local__/dep/index.js").is_file());
    let manifest = read_json(&out_dir.join("package.json"));
    assert!(manifest["dependencies"].get("dep").is_none());
    assert!(manifest["workspaces"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!("__local__/dep")));
}

#[test]
fn compile_for_deploy_strips_dev_dependencies_from_typescript_local_packages() {
    let (_workspace, project) = ts_project_with_local_package("file:../dep");
    let out_dir = project.root.join(".mda").join("build");
    let deploy_context = DeployContext {
        repo: "app".to_string(),
        has_skills: false,
    };
    compile_for_deploy(&project, &out_dir, &deploy_context).unwrap();

    let manifest = read_json(&out_dir.join("__local__/dep/package.json"));
    assert!(manifest.get("devDependencies").is_none());
    assert_eq!(manifest["dependencies"]["zod"], "^3.0.0");
}

#[test]
fn compile_leaves_typescript_local_packages_inside_the_project_alone() {
    let (_workspace, mut project) = ts_project_with_local_package("file:./packages/dep");
    write_file(
        &project.root,
        "packages/dep/package.json",
        r#"{"name":"dep","version":"1.0.0"}"#,
    );
    project
        .files
        .push(PathBuf::from("packages/dep/package.json"));
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(!out_dir.join("__local__").exists());
    let manifest = read_json(&out_dir.join("package.json"));
    assert_eq!(manifest["dependencies"]["dep"], "file:./packages/dep");
}

#[test]
fn compile_rejects_a_missing_local_package() {
    let (_workspace, project) = ts_project_with_local_package("file:../missing");
    let out_dir = project.root.join(".mda").join("build");
    let error = format!("{:#}", compile(&project, &out_dir).unwrap_err());
    assert!(error.contains("`dep`"), "{error}");
    assert!(error.contains("../missing"), "{error}");
}

#[test]
fn compile_vendors_nested_typescript_local_packages_as_workspaces() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        workspace.path(),
        "dep/package.json",
        r#"{"name":"dep","version":"1.0.0","dependencies":{"zod":"^3.0.0","other":"file:../other"}}"#,
    );
    write_file(
        workspace.path(),
        "other/package.json",
        r#"{"name":"other","version":"1.0.0","main":"index.js"}"#,
    );
    write_file(
        workspace.path(),
        "other/index.js",
        "export const other = 1;\n",
    );
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(out_dir.join("__local__/other/index.js").is_file());
    let manifest = read_json(&out_dir.join("package.json"));
    let workspaces = manifest["workspaces"].as_array().unwrap();
    assert!(workspaces.contains(&serde_json::json!("__local__/dep")));
    assert!(workspaces.contains(&serde_json::json!("__local__/other")));
    let dep = read_json(&out_dir.join("__local__/dep/package.json"));
    assert!(dep["dependencies"].get("other").is_none());
    assert_eq!(dep["dependencies"]["zod"], "^3.0.0");
}

#[test]
fn compile_copies_a_shared_local_package_once() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        &project.root,
        "package.json",
        r#"{"name":"app","dependencies":{"dep":"file:../dep","other":"file:../other"}}"#,
    );
    write_file(
        workspace.path(),
        "dep/package.json",
        r#"{"name":"dep","version":"1.0.0","dependencies":{"other":"file:../other"}}"#,
    );
    write_file(
        workspace.path(),
        "other/package.json",
        r#"{"name":"other"}"#,
    );
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    let manifest = read_json(&out_dir.join("package.json"));
    assert_eq!(
        manifest["workspaces"],
        serde_json::json!(["__runtime__", "__local__/dep", "__local__/other"])
    );
}

#[test]
fn compile_rejects_two_local_packages_with_the_same_name() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        workspace.path(),
        "dep/package.json",
        r#"{"name":"dep","version":"1.0.0","dependencies":{"dep":"file:../copy"}}"#,
    );
    write_file(workspace.path(), "copy/package.json", r#"{"name":"dep"}"#);
    let out_dir = project.root.join(".mda").join("build");
    let error = format!("{:#}", compile(&project, &out_dir).unwrap_err());
    assert!(error.contains("`dep`"), "{error}");
}

#[test]
fn compile_rejects_a_local_package_declared_under_another_name() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        workspace.path(),
        "dep/package.json",
        r#"{"name":"@acme/actual","version":"1.0.0","main":"index.js"}"#,
    );
    let out_dir = project.root.join(".mda").join("build");
    let error = format!("{:#}", compile(&project, &out_dir).unwrap_err());
    assert!(error.contains("`dep`"), "{error}");
    assert!(error.contains("`@acme/actual`"), "{error}");
}

#[test]
fn compile_rejects_a_project_package_that_declares_a_package_outside_the_project() {
    let (workspace, mut project) = ts_project_with_local_package("file:./packages/dep");
    write_file(
        &project.root,
        "packages/dep/package.json",
        r#"{"name":"dep","version":"1.0.0","dependencies":{"shared":"file:../../../shared"}}"#,
    );
    write_file(
        workspace.path(),
        "shared/package.json",
        r#"{"name":"shared"}"#,
    );
    project
        .files
        .push(PathBuf::from("packages/dep/package.json"));
    let out_dir = project.root.join(".mda").join("build");
    let error = format!("{:#}", compile(&project, &out_dir).unwrap_err());
    assert!(error.contains("`shared`"), "{error}");
    assert!(error.contains("packages/dep"), "{error}");
}

#[test]
fn compile_refresh_recopies_local_packages_and_prunes_removed_files() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    let out_dir = project.root.join(".mda").join("build");
    write_file(workspace.path(), "dep/extra.js", "export {};\n");
    compile(&project, &out_dir).unwrap();
    assert!(out_dir.join("__local__/dep/extra.js").is_file());

    fs::remove_file(workspace.path().join("dep/extra.js")).unwrap();
    write_file(
        workspace.path(),
        "dep/index.js",
        "export const helper = () => 2;\n",
    );
    compile_refresh(&project, &out_dir).unwrap();

    assert!(!out_dir.join("__local__/dep/extra.js").exists());
    assert_eq!(
        fs::read_to_string(out_dir.join("__local__/dep/index.js")).unwrap(),
        "export const helper = () => 2;\n"
    );
}

#[test]
fn compile_warns_about_typescript_imports_outside_the_project() {
    let (_workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        &project.root,
        "agent.ts",
        concat!(
            "import { helper } from \"../dep/helper\";\n",
            "import type { Shape } from \"../dep/types\";\n",
            "import { local } from \"./local\";\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5\" });\n",
        ),
    );
    let out_dir = project.root.join(".mda").join("build");
    let summary = compile(&project, &out_dir).unwrap();

    let outside: Vec<&String> = summary
        .warnings
        .iter()
        .filter(|warning| warning.contains("outside the project"))
        .collect();
    assert_eq!(outside.len(), 1, "{:?}", summary.warnings);
    assert!(outside[0].contains("agent.ts"), "{}", outside[0]);
    assert!(outside[0].contains("../dep/helper"), "{}", outside[0]);
}

fn py_project_with_local_package() -> (tempfile::TempDir, Project) {
    let workspace = tempfile::tempdir().unwrap();
    let root = workspace.path().join("app");
    write_file(
        &root,
        "agent.py",
        "agent = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n",
    );
    write_file(
        &root,
        "pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"app\"\n",
            "version = \"0.0.0\"\n",
            "dependencies = [\"managed-deepagents\", \"my-dep\"]\n",
            "\n",
            "[tool.uv.sources]\n",
            "my-dep = { path = \"../dep\" }\n",
        ),
    );
    write_file(
        workspace.path(),
        "dep/pyproject.toml",
        "[project]\nname = \"my-dep\"\nversion = \"0.1.0\"\ndependencies = [\"httpx\"]\n",
    );
    write_file(workspace.path(), "dep/my_dep/__init__.py", "VALUE = 1\n");
    write_file(workspace.path(), "dep/my_dep/__pycache__/x.pyc", "");
    write_file(workspace.path(), "dep/.venv/bin/python", "");
    let project = Project {
        root,
        language: Language::Python,
        agent_file: PathBuf::from("agent.py"),
        files: vec![PathBuf::from("agent.py"), PathBuf::from("pyproject.toml")],
    };
    (workspace, project)
}

fn read_toml(path: &Path) -> toml_edit::DocumentMut {
    fs::read_to_string(path).unwrap().parse().unwrap()
}

fn toml_dependencies(doc: &toml_edit::DocumentMut) -> Vec<String> {
    doc["project"]["dependencies"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap().to_string())
        .collect()
}

#[test]
fn compile_points_a_python_dev_build_at_the_vendored_local_package() {
    let (_workspace, project) = py_project_with_local_package();
    let out_dir = project.root.join(".mda").join("build");
    with_py_runtime(&project.root, || compile(&project, &out_dir)).unwrap();

    assert!(out_dir
        .join("__local__/my-dep/my_dep/__init__.py")
        .is_file());
    assert!(!out_dir.join("__local__/my-dep/.venv").exists());
    assert!(!out_dir.join("__local__/my-dep/my_dep/__pycache__").exists());

    let pyproject = read_toml(&out_dir.join("pyproject.toml"));
    let source = &pyproject["tool"]["uv"]["sources"]["my-dep"];
    assert_eq!(source["path"].as_str(), Some("__local__/my-dep"));
    assert_eq!(source["editable"].as_bool(), Some(true));
    assert!(toml_dependencies(&pyproject).contains(&"my-dep".to_string()));
}

#[test]
fn compile_for_deploy_installs_a_python_local_package_from_langgraph_json() {
    let (_workspace, project) = py_project_with_local_package();
    let out_dir = project.root.join(".mda").join("build");
    let deploy_context = DeployContext {
        repo: "app".to_string(),
        has_skills: false,
    };
    with_py_runtime(&project.root, || {
        compile_for_deploy(&project, &out_dir, &deploy_context)
    })
    .unwrap();

    let langgraph = read_json(&out_dir.join("langgraph.json"));
    assert_eq!(
        langgraph["dependencies"],
        serde_json::json!([".", "./__runtime__", "./__local__/my-dep"])
    );
    let pyproject = read_toml(&out_dir.join("pyproject.toml"));
    assert!(!toml_dependencies(&pyproject)
        .iter()
        .any(|dep| dep.contains("my-dep")));
    assert!(pyproject["tool"]["uv"]["sources"].get("my-dep").is_none());
}

#[test]
fn compile_for_eval_points_a_python_local_package_at_the_container_path() {
    let (_workspace, project) = py_project_with_local_package();
    let out_dir = project.root.join(".mda").join("build");
    with_py_runtime(&project.root, || compile_for_eval(&project, &out_dir)).unwrap();

    let pyproject = read_toml(&out_dir.join("pyproject.toml"));
    assert!(toml_dependencies(&pyproject)
        .contains(&"my-dep @ file:///app/__local__/my-dep".to_string()));
    assert!(pyproject["tool"]["uv"]["sources"].get("my-dep").is_none());
}

#[test]
fn compile_leaves_a_local_runtime_dependency_to_the_vendored_runtime() {
    let (workspace, project) = ts_project_with_local_package("file:../dep");
    write_file(
        &project.root,
        "package.json",
        r#"{"name":"app","dependencies":{"managed-deepagents":"file:../runtime"}}"#,
    );
    write_file(
        workspace.path(),
        "runtime/package.json",
        r#"{"name":"managed-deepagents","version":"0.0.0"}"#,
    );
    let out_dir = project.root.join(".mda").join("build");
    compile(&project, &out_dir).unwrap();

    assert!(!out_dir.join("__local__").exists());
    let manifest = read_json(&out_dir.join("package.json"));
    assert_eq!(manifest["workspaces"], serde_json::json!(["__runtime__"]));
}

#[test]
fn compile_leaves_a_local_python_runtime_source_to_the_vendored_runtime() {
    let (workspace, project) = py_project_with_local_package();
    write_file(
        &project.root,
        "pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"app\"\n",
            "version = \"0.0.0\"\n",
            "dependencies = [\"managed-deepagents\"]\n",
            "\n",
            "[tool.uv.sources]\n",
            "managed_deepagents = { path = \"../runtime\" }\n",
        ),
    );
    write_file(
        workspace.path(),
        "runtime/pyproject.toml",
        "[project]\nname = \"managed-deepagents\"\nversion = \"0.0.0\"\n",
    );
    let out_dir = project.root.join(".mda").join("build");
    with_py_runtime(&project.root, || compile(&project, &out_dir)).unwrap();

    assert!(!out_dir.join("__local__").exists());
    let pyproject = read_toml(&out_dir.join("pyproject.toml"));
    assert!(pyproject["tool"]["uv"]["sources"]
        .get("managed_deepagents")
        .is_none());
}

fn py_project_with_nested_local_package() -> (tempfile::TempDir, Project) {
    let (workspace, project) = py_project_with_local_package();
    write_file(
        workspace.path(),
        "dep/pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"my-dep\"\n",
            "version = \"0.1.0\"\n",
            "dependencies = [\"httpx\", \"other\"]\n",
            "\n",
            "[tool.uv.sources]\n",
            "other = { path = \"../other\" }\n",
        ),
    );
    write_file(
        workspace.path(),
        "other/pyproject.toml",
        "[project]\nname = \"other\"\nversion = \"0.1.0\"\n",
    );
    write_file(workspace.path(), "other/other/__init__.py", "VALUE = 2\n");
    (workspace, project)
}

#[test]
fn compile_points_a_nested_python_local_package_at_its_sibling_copy() {
    let (_workspace, project) = py_project_with_nested_local_package();
    let out_dir = project.root.join(".mda").join("build");
    with_py_runtime(&project.root, || compile(&project, &out_dir)).unwrap();

    assert!(out_dir.join("__local__/other/other/__init__.py").is_file());
    let dep = read_toml(&out_dir.join("__local__/my-dep/pyproject.toml"));
    let source = &dep["tool"]["uv"]["sources"]["other"];
    assert_eq!(source["path"].as_str(), Some("../other"));
    assert_eq!(source["editable"].as_bool(), Some(true));
    let root = read_toml(&out_dir.join("pyproject.toml"));
    assert!(root["tool"]["uv"]["sources"].get("other").is_none());
}

#[test]
fn compile_for_deploy_installs_nested_python_local_packages_from_langgraph_json() {
    let (_workspace, project) = py_project_with_nested_local_package();
    let out_dir = project.root.join(".mda").join("build");
    let deploy_context = DeployContext {
        repo: "app".to_string(),
        has_skills: false,
    };
    with_py_runtime(&project.root, || {
        compile_for_deploy(&project, &out_dir, &deploy_context)
    })
    .unwrap();

    let langgraph = read_json(&out_dir.join("langgraph.json"));
    assert_eq!(
        langgraph["dependencies"],
        serde_json::json!([
            ".",
            "./__runtime__",
            "./__local__/my-dep",
            "./__local__/other"
        ])
    );
    let dep = read_toml(&out_dir.join("__local__/my-dep/pyproject.toml"));
    assert_eq!(toml_dependencies(&dep), vec!["httpx".to_string()]);
    assert!(dep["tool"]["uv"]["sources"].get("other").is_none());
}

fn py_project_requiring_local_package(requirement: &str) -> (tempfile::TempDir, Project) {
    let (workspace, project) = py_project_with_local_package();
    write_file(
        &project.root,
        "pyproject.toml",
        &format!(
            "[project]\nname = \"app\"\nversion = \"0.0.0\"\ndependencies = [\"managed-deepagents\", {requirement:?}]\n\n[tool.uv.sources]\nmy-dep = {{ path = \"../dep\" }}\n"
        ),
    );
    write_file(
        workspace.path(),
        "dep/pyproject.toml",
        concat!(
            "[project]\n",
            "name = \"my-dep\"\n",
            "version = \"0.1.0\"\n",
            "dependencies = [\"httpx\"]\n",
            "\n",
            "[project.optional-dependencies]\n",
            "fast = [\"orjson\"]\n",
            "all = [\"my-dep[fast]\", \"rich\"]\n",
        ),
    );
    (workspace, project)
}

#[test]
fn compile_for_eval_keeps_extras_and_markers_of_a_python_local_package() {
    let (_workspace, project) =
        py_project_requiring_local_package("my-dep[fast]>=0.1; python_version >= '3.11'");
    let out_dir = project.root.join(".mda").join("build");
    with_py_runtime(&project.root, || compile_for_eval(&project, &out_dir)).unwrap();

    let pyproject = read_toml(&out_dir.join("pyproject.toml"));
    assert!(toml_dependencies(&pyproject).contains(
        &"my-dep[fast] @ file:///app/__local__/my-dep ; python_version >= '3.11'".to_string()
    ));
}

#[test]
fn compile_for_deploy_installs_the_extras_of_a_python_local_package() {
    let (_workspace, project) = py_project_requiring_local_package("my-dep[all]");
    let out_dir = project.root.join(".mda").join("build");
    let deploy_context = DeployContext {
        repo: "app".to_string(),
        has_skills: false,
    };
    with_py_runtime(&project.root, || {
        compile_for_deploy(&project, &out_dir, &deploy_context)
    })
    .unwrap();

    let dep = read_toml(&out_dir.join("__local__/my-dep/pyproject.toml"));
    let mut deps = toml_dependencies(&dep);
    deps.sort();
    assert_eq!(deps, vec!["httpx", "orjson", "rich"]);
}

#[test]
fn compile_for_deploy_rejects_a_marker_on_a_python_local_package() {
    let (_workspace, project) =
        py_project_requiring_local_package("my-dep; sys_platform == 'linux'");
    let out_dir = project.root.join(".mda").join("build");
    let deploy_context = DeployContext {
        repo: "app".to_string(),
        has_skills: false,
    };
    let error = with_py_runtime(&project.root, || {
        compile_for_deploy(&project, &out_dir, &deploy_context)
    })
    .unwrap_err();
    let error = format!("{error:#}");
    assert!(error.contains("sys_platform == 'linux'"), "{error}");
}
