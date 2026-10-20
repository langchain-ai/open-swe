//! README generation for scaffolded projects.

use crate::project::Language;

use super::{Blueprint, InitChannel};

/// Build a `README.md` describing the project that was actually written.
pub(super) fn readme(name: &str, blueprint: &Blueprint) -> String {
    let language = blueprint.language;
    let (ext, define, install, manifest, sandbox_entry, mcp_entry, dev_note) =
        match language {
            Language::TypeScript => (
                "ts",
                "defineDeepAgent",
                "npm install",
                "package.json",
                "sandbox/index.ts",
                "tools/mcp.ts",
                "",
            ),
            Language::Python => (
                "py",
                "define_deep_agent",
                "uv sync",
                "pyproject.toml",
                "sandbox/__init__.py",
                "tools/mcp.py",
                "For Python projects, `mda dev` requires `uv` on `PATH`, but it resolves the local LangGraph dev server automatically; you do not need to install a global `langgraph` command.\n\n",
            ),
        };
    let agent = format!("agent.{ext}");
    let identity_entry = match language {
        Language::TypeScript => "identity.ts",
        Language::Python => "identity.py",
    };
    let memory_entry = match language {
        Language::TypeScript => "memory.ts",
        Language::Python => "memory.py",
    };

    let mut entries = vec![
        (
            agent,
            format!("{define}(...) — required `name` is the deploy id"),
        ),
        (
            "instructions.md".to_string(),
            "always-loaded system prompt".to_string(),
        ),
        (manifest.to_string(), "project dependencies".to_string()),
        (
            ".env".to_string(),
            "API keys (LangSmith + model providers); never commit".to_string(),
        ),
    ];
    if blueprint.identity {
        entries.push((
            identity_entry.to_string(),
            "managed authentication".to_string(),
        ));
    }
    if let Some(choice) = blueprint.memory {
        entries.push((
            memory_entry.to_string(),
            format!(
                "durable memory (`{layer}` layer)",
                layer = choice.layer_name()
            ),
        ));
    }
    if blueprint.sandbox {
        entries.push((
            sandbox_entry.to_string(),
            "managed LangSmith sandbox (delete `sandbox/` to opt out)".to_string(),
        ));
    }
    if blueprint.channels.contains(&InitChannel::Slack) {
        entries.push((
            format!("channels/slack.{ext}"),
            "Slack messaging channel".to_string(),
        ));
    }
    if blueprint.channels.contains(&InitChannel::GitHub) {
        entries.push((
            format!("channels/github.{ext}"),
            "GitHub channel (GitHub App set up on deploy)".to_string(),
        ));
    }
    entries.extend(
        [
            ("tools/", "optional custom tools"),
            ("middleware/", "optional middleware"),
            ("skills/", "optional skills synced to Context Hub"),
            (mcp_entry, "optional MCP server declaration"),
        ]
        .map(|(entry, comment)| (entry.to_string(), comment.to_string())),
    );
    let structure = tree(name, &entries);

    let evaluate = evaluate();
    let sandbox = sandbox_section(sandbox_entry, blueprint);
    let identity = identity_section(identity_entry, blueprint);
    let memory = memory_section(memory_entry, blueprint, language);

    format!(
        r#"# {name}

A Managed Deep Agent built with [`managed-deepagents`](https://github.com/langchain-ai/managed-deepagents-sdk).

## Project structure

```text
{structure}
```

## Install

```bash
{install}
```

{evaluate}
## Develop

Edit `agent.{ext}` to configure your model, tools, and middleware, and edit
`instructions.md` to shape the system prompt.

Run the compiled app on the local LangGraph dev server:

```bash
mda dev
```

{dev_note}{identity}
{memory}
{sandbox}
## Optional Runtime Pieces

Add `{mcp_entry}` to attach MCP servers. The file must expose a named `mcp`
declaration.

## Deploy

Compile and deploy the project to LangSmith:

```bash
mda deploy
```

This copies your files verbatim, generates a managed entry module, and writes a
deployable build (including `langgraph.json`) to `.mda/build`. The CLI uploads
that build to LangSmith to run your agent on the managed runtime.

Common options:

```bash
mda deploy --name {name}-dev --deployment-type dev
mda deploy --workspace-id "$LANGSMITH_WORKSPACE_ID"
mda deploy --no-wait
```

Deploy prints both the Agent Server URL to call and the LangSmith dashboard URL
to inspect.

## Logs

Read the deployed agent's server logs:

```bash
mda logs
mda logs --lines 200 --level error
```

In a terminal this streams new output until you press Ctrl-C. When the output is
piped or redirected it prints the most recent lines (1000 by default) and exits.

## Delete

Remove the deployment and the LangSmith resources it created:

```bash
mda delete
```

This deletes the deployment, the tracing project created alongside it, the
Context Hub repo holding this agent's context and memory, and the managed
sandboxes this agent created. It asks first; pass `--yes` to skip the prompt.
Agent memory and thread history are not recoverable afterwards.

## Environment

`mda deploy` loads `.env`, uses `LANGSMITH_API_KEY` for LangSmith, and forwards
model provider keys such as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` as deployment
secrets. Provider keys must be in `.env` or configured as LangSmith workspace
secrets — a value exported in your shell is not read. Set
`LANGSMITH_WORKSPACE_ID` or pass `--workspace-id` if your LangSmith API key
requires a workspace selection.
"#
    )
}

/// The project listing, with every comment in the same column.
///
/// Which entries appear depends on what was scaffolded, so the column they line
/// up in is whatever the longest of them needs rather than a fixed width.
fn tree(name: &str, entries: &[(String, String)]) -> String {
    let width = entries
        .iter()
        .map(|(entry, _)| entry.chars().count())
        .max()
        .unwrap_or_default();
    let mut lines = vec![format!("{name}/")];
    lines.extend(
        entries
            .iter()
            .map(|(entry, comment)| format!("  {entry:<width$}  # {comment}")),
    );
    lines.join("\n")
}

/// Explain how to create and run the project's first eval task.
fn evaluate() -> String {
    "## Evaluate\n\n\
     From the project root, initialize the Harbor eval workspace:\n\n\
     ```bash\n\
     mda evals init -i\n\
     ```\n\n\
     Follow the coding-agent prompt to author tasks directly under `evals/<task>/`. The CLI\n\
     creates `evals/harbor-job.json` once and preserves your edits; `.mda/evals/` is generated.\n\n\
     A task may include an authored `evals/<task>/identity.json` fixture. It requires a\n\
     non-empty `user.id`; `user.kind`, `user.email`, and top-level `groups`, `claims`, and\n\
     `source.provider` are optional. Keep the fixture with the task, not in generated\n\
     `.mda/evals/`.\n\n\
     Running evals requires `uv` and Docker. Export `LANGSMITH_API_KEY`,\n\
     `LANGSMITH_WORKSPACE_ID` when your credentials require it, and the model or tool\n\
     credential variables used by the agent. Then run the pinned two-plugin Harbor command\n\
     included at the end of the coding-agent prompt from the project root. It uses POSIX\n\
     syntax on macOS/Linux and PowerShell on native Windows. `MDAJobPlugin` compiles a fresh\n\
     eval artifact at every Harbor job start. From the same project root, inspect results:\n\n\
     ```bash\n\
     uv run --python 3.12 --with 'harbor[langsmith]==0.21.0' harbor view .mda/evals/jobs\n\
     ```\n\n\
     This POC keeps MDA's custom Harbor adapter. Migration to Harbor's built-in LangGraph\n\
     agent is deferred.\n"
        .to_string()
}

/// The sandbox section, which either explains the declaration that was written
/// or says how to add one.
fn sandbox_section(sandbox_entry: &str, blueprint: &Blueprint) -> String {
    if !blueprint.sandbox {
        return format!(
            "## Sandbox\n\n\
             This project has no sandbox: MDA only provisions one when a declaration\n\
             is present. Add `{sandbox_entry}` to give the agent a managed execution\n\
             environment with a filesystem and a shell.\n"
        );
    }
    format!(
        "## Sandbox\n\n\
         `{sandbox_entry}` declares a managed LangSmith sandbox. MDA only enables the\n\
         sandbox when this declaration is present — remove the `sandbox/` directory to\n\
         opt out (for example for chat-only agents). Add `sandbox/setup.sh` if you want\n\
         to provision a recipe snapshot; `mda deploy` / `mda dev` bake it once and new\n\
         threads clone that image without re-running the script.\n"
    )
}

/// The memory section, which either names the declared layer or says plainly that
/// the agent keeps nothing between runs.
fn memory_section(memory_entry: &str, blueprint: &Blueprint, language: Language) -> String {
    let Some(choice) = blueprint.memory else {
        let declare = crate::compile::declare_memory_snippet(language);
        let verb = language.ident("exporting", "declaring");
        return format!(
            "## Memory\n\n\
             This project declares no memory, so nothing is kept between runs. Add\n\
             `{memory_entry}` {verb} `{declare}` to mount one deployment-shared tree\n\
             at `/memories/agent/`.\n"
        );
    };
    if choice.layer_name() == "none" {
        return format!(
            "## Memory\n\n\
             `{memory_entry}` declares no scope, so this agent keeps no durable\n\
             memory. Add `{{ type: \"agent\" }}` to `scope` to mount\n\
             `/memories/agent/`.\n"
        );
    }
    format!(
        "## Memory\n\n\
         `{memory_entry}` mounts one deployment-shared memory tree at\n\
         `/memories/agent/` (read/write). `/memories/agent/AGENTS.md` is hot memory,\n\
         loaded into every run; other files under that path are read on demand. The\n\
         content lives in Context Hub, so deploys never overwrite what earlier runs\n\
         learned. Every caller shares it — keep procedural knowledge there, never\n\
         per-person facts or credentials.\n\n\
         The memory file holds only what the agent wrote. How to use memory — the\n\
         paths, which tools reach them, that the tree is shared — is stated in the\n\
         system prompt at runtime whenever a slice is mounted, so there is nothing to\n\
         maintain in the file and nothing to repeat in `instructions.md`.\n"
    )
}

/// The identity section, or a warning that the deployment is open.
fn identity_section(identity_entry: &str, blueprint: &Blueprint) -> String {
    if !blueprint.identity {
        return format!(
            "## Identity\n\n\
             This project declares no identity, so the deployment is open to any caller.\n\
             Add `{identity_entry}` to say who may call it and how runs are scoped.\n"
        );
    }
    format!(
        "## Identity\n\n\
         `{identity_entry}` enables managed authentication: threads are owned\n\
         per caller. Set `auth` to one or more `auth.*` entries if browsers call\n\
         the deployment directly. Durable memory is declared separately.\n"
    )
}
