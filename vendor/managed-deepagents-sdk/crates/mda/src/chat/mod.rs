//! `mda chat` — talk to a Managed Deep Agent from the terminal.
//!
//! This is a client of the Agent Server's runs API, not a channel. Channels are
//! provider-authenticated ingress routes that live inside the deployment and
//! push one reply out through a transport; a terminal has no such return path
//! and wants token streaming and inline interrupt handling instead, which the
//! runs API already provides.
//!
//! The command targets the local `mda dev` server, using the URL and assistant
//! id `mda dev` staged into `.mda/dev/.env`, or whatever `--url` names. It
//! sends no credentials: `mda dev` runs with `MDA_LOCAL_DEV=1`, which resolves
//! headerless callers to the local principal. Deploy never sets that, so a
//! deployed Agent Server rejects chat — `--url` reaches a local server or a
//! tunnel to one, and nothing else.
//!
//! Without `--url`, a dev server is not a precondition: chat reuses one that
//! already answers locally, and otherwise starts its own for the session and
//! takes it down on the way out. That server logs to `.mda/dev-server.log`
//! rather than the transcript, so an author who wants its logs and errors in
//! front of them runs `mda dev` separately and lets chat attach to it.
//!
//! Each turn streams one run with `messages-tuple` (tokens and tool calls) and
//! `values` (final state). A run that stops on a human-in-the-loop interrupt is
//! resumed from a prompt in the same loop, so `interrupt_on` tools can be
//! approved or rejected without leaving the terminal.

use std::collections::BTreeMap;
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, Context, Result};
use cliclack::{input, log, note, select, spinner};
use console::style;
use serde_json::{json, Map, Value};

use crate::agent_config;
use crate::cli_output::{self, chat as chat_output, SequenceItem};
use crate::command::CommandCompletion;
use crate::dependencies;
use crate::deploy::env_file;
use crate::deploy::helpers::{MDA_ASSISTANT_ID_ENV, MDA_PUBLIC_API_URL_ENV};
use crate::dev;
use crate::error::{categorized, ErrorCategory};
use crate::install::Installation;
use crate::interrupt::Interrupt;
use crate::langsmith::agent_server::{AgentServer, PendingInterrupt, RunEvent, RunState};
use crate::project::Project;
use crate::telemetry;
use crate::telemetry::otel_logger;
use crate::telemetry::stage::Stage;
use chat_output::{sanitize, TurnRenderer};

/// Where `mda dev` listens when nothing staged a local URL.
const DEFAULT_LOCAL_API_URL: &str = "http://localhost:2024";

const EXIT_COMMANDS: &[&str] = &["/exit", "/quit"];
const NEW_THREAD_COMMAND: &str = "/new";

/// User-facing options for `mda chat`.
pub struct ChatOptions {
    /// Path to the Managed Deep Agent project.
    pub path: PathBuf,
    /// Local Agent Server URL. Naming one also opts out of starting a server.
    pub url: Option<String>,
    /// Assistant (graph) id. Defaults to what `mda dev` staged, then the
    /// authored agent name.
    pub assistant: Option<String>,
    /// Existing thread to continue.
    pub thread: Option<String>,
}

/// How the session reached its Agent Server, which decides both what the
/// closing resume command should pin and what has to be torn down.
enum Target {
    /// The server the user named with `--url`. Nothing was started for them,
    /// and it may not be local at all.
    Pinned { api_url: String },
    /// A local dev server that was already answering.
    Attached { api_url: String },
    /// A server started for this session, and stopped when it ends.
    Started(dev::DevServer),
}

impl Target {
    fn api_url(&self) -> &str {
        match self {
            Target::Pinned { api_url } | Target::Attached { api_url } => api_url,
            Target::Started(server) => &server.api_url,
        }
    }

    /// The URL the resume command has to pin, if any. A server this session
    /// started is gone by then, and one it found is found again next time, so
    /// only an explicit `--url` is worth repeating.
    fn resume_url(&self) -> Option<&str> {
        match self {
            Target::Pinned { api_url } => Some(api_url),
            Target::Attached { .. } | Target::Started(_) => None,
        }
    }
}

/// Run the chat command end-to-end.
pub fn run(opts: ChatOptions) -> Result<CommandCompletion> {
    cli_output::show_wordmark_intro()?;
    let interrupt = Interrupt::install()?;

    // Only for the model-key checks: a project that cannot be read here fails
    // elsewhere with its own error, or is not needed with `--assistant`.
    let project = Installation::from_env()
        .and_then(|installation| installation.load_project(&opts.path))
        .ok();
    let Some(target) = resolve_target(&opts, project.as_ref(), &interrupt)? else {
        return Ok(CommandCompletion::Cancelled);
    };
    // Every target, so a run that fails in the session is recorded as chatting.
    telemetry::stage::enter(Stage::Chatting);
    // The assistant id is read after the server is up: starting one stages the
    // `.env` this falls back to.
    let staged = env_file::parse_env_file(&dev::dev_out_dir(&opts.path).join(".env"));
    let assistant_id = resolve_assistant_id(&opts, &staged)?;
    let server = AgentServer::new(target.api_url().to_string(), assistant_id)?;
    let pickup = env_pickup(&target);
    let model_key = project.as_ref().and_then(model_api_key);
    let env_path = opts
        .path
        .canonicalize()
        .unwrap_or_else(|_| opts.path.clone())
        .join(".env");

    let mut connection = vec![
        format!("Agent Server: {}", server.api_url),
        format!("Assistant: {}", server.assistant_id),
    ];
    if let Target::Started(started) = &target {
        connection.push(format!("Server log: {}", started.log_path.display()));
        // This server has no watcher, so an edit mid-session is invisible
        // until the next run of chat. Say so where the author is looking.
        connection.push("Edits need a restart — `mda dev` reloads".to_string());
    }
    cli_output::continue_open_sequence(&[SequenceItem::section("Connection", connection)])?;

    let mut thread_id = server.ensure_thread(opts.thread.as_deref())?;
    cli_output::continue_sequence(&[SequenceItem::section(
        "Conversation",
        [
            format!("Thread: {thread_id}"),
            format!(
                "Type a message, {NEW_THREAD_COMMAND} for a fresh thread, {} to leave.",
                EXIT_COMMANDS[0]
            ),
        ],
    )])?;

    let completion = chat_loop(
        &server,
        &mut thread_id,
        &interrupt,
        || prompt_message(&interrupt),
        prompt_resume,
        |message| {
            let hint = run_failure_hint(message, model_key, &env_path, pickup);
            Ok(chat_output::show_run_failure(message, hint.as_deref())?)
        },
    )?;

    chat_output::show_notice(&format!(
        "Resume this conversation with {}",
        resume_command(&opts, &target, &server, &thread_id)
    ))?;
    Ok(completion)
}

/// Find the Agent Server to talk to, starting one when nothing is serving and
/// the user named no URL. `None` means they interrupted the startup.
///
/// A local server is checked for the model's API key first, before a start
/// that the missing key can itself make fail. A pinned server's environment
/// is its own, so there is nothing to check.
fn resolve_target(
    opts: &ChatOptions,
    project: Option<&Project>,
    interrupt: &Interrupt,
) -> Result<Option<Target>> {
    if let Some(url) = flag(opts.url.as_deref()) {
        return Ok(Some(Target::Pinned {
            api_url: normalize_api_url(url)?,
        }));
    }

    let staged = env_file::parse_env_file(&dev::dev_out_dir(&opts.path).join(".env"));
    let api_url = normalize_api_url(
        flag(staged.get(MDA_PUBLIC_API_URL_ENV).map(String::as_str))
            .unwrap_or(DEFAULT_LOCAL_API_URL),
    )?;
    if dev::dev_server_is_up(&api_url) {
        warn_missing_model_api_key(project, &staged, "attached", ATTACHED_PICKUP)?;
        return Ok(Some(Target::Attached { api_url }));
    }
    if let Some(project) = project {
        // The file the server about to start stages, as `mda dev` picks it.
        let env = [".env", ".env.local"]
            .into_iter()
            .map(|name| project.root.join(name))
            .find(|path| path.is_file())
            .map(|path| env_file::parse_env_file(&path))
            .unwrap_or_default();
        warn_missing_model_api_key(Some(project), &env, "started", STARTED_PICKUP)?;
    }

    // Preparing compiles the project and can stop to ask for a login, so it
    // gets the terminal to itself; the spinner covers only the wait after.
    log::info("No local dev server is running — starting one for this session")?;
    let pending = dev::prepare_background(&opts.path)?;

    telemetry::stage::enter(Stage::ServerStart);
    let starting = spinner();
    starting.start(format!("Starting the dev server on {}", pending.api_url()));
    match dev::launch_background(pending, interrupt) {
        Ok(Some(server)) => {
            starting.stop(format!("Dev server ready at {}", server.api_url));
            Ok(Some(Target::Started(server)))
        }
        Ok(None) => {
            starting.cancel("Stopped before the dev server was ready");
            Ok(None)
        }
        Err(err) => {
            starting.error("Could not start the dev server");
            Err(err)
        }
    }
}

/// Converse until the user leaves. A run that fails inside the agent goes to
/// `report_failure` and the session carries on, so a fix (a key added to
/// `.env`, say) can be tried without starting over.
fn chat_loop(
    server: &AgentServer,
    thread_id: &mut String,
    interrupt: &Interrupt,
    mut read_message: impl FnMut() -> Result<Option<String>>,
    mut review: impl FnMut(&[PendingInterrupt]) -> Result<Option<Value>>,
    mut report_failure: impl FnMut(&str) -> Result<()>,
) -> Result<CommandCompletion> {
    let mut pending = server.thread_interrupts(thread_id)?;
    loop {
        if interrupt.is_requested() {
            return Ok(CommandCompletion::Cancelled);
        }
        let body = if pending.is_empty() {
            let Some(text) = read_message()? else {
                return Ok(CommandCompletion::Succeeded);
            };
            if EXIT_COMMANDS.contains(&text.as_str()) {
                return Ok(CommandCompletion::Succeeded);
            }
            if text == NEW_THREAD_COMMAND {
                *thread_id = server.ensure_thread(None)?;
                chat_output::show_notice(&format!("New thread: {thread_id}"))?;
                continue;
            }
            json!({ "input": { "messages": [{ "role": "human", "content": text }] } })
        } else {
            let Some(resume) = review(&pending)? else {
                return Ok(CommandCompletion::Cancelled);
            };
            json!({ "command": { "resume": resume } })
        };
        pending = match stream_turn(server, thread_id, body, interrupt) {
            Ok(pending) => pending,
            Err(err) => {
                let Some(RunFailed(message)) = err.downcast_ref::<RunFailed>() else {
                    return Err(err);
                };
                otel_logger::warn("chat run failed", &err);
                report_failure(message)?;
                // A failed resume can leave the thread paused on its interrupt.
                server.thread_interrupts(thread_id)?
            }
        };
    }
}

/// A run that failed inside the agent, as opposed to a failure to reach the
/// Agent Server. Carries the readable, sanitized message.
#[derive(Debug)]
struct RunFailed(String);

impl fmt::Display for RunFailed {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "the run failed: {}", self.0)
    }
}

impl std::error::Error for RunFailed {}

/// The readable part of a run's `error` event. The Agent Server sends
/// `{"error": "<type>", "message": "<text>"}`; anything else is shown as sent.
///
/// The type is kept unless it is a bare `Error`: the Python server replaces
/// messages with "An internal error occurred", which leaves the type
/// (`OpenAIAuthenticationError`) as the only clue.
fn run_error_message(raw: &str) -> String {
    let readable = match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(fields)) => {
            let text = |key| {
                fields
                    .get(key)
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|text| !text.is_empty())
            };
            match (
                text("error").filter(|kind| *kind != "Error"),
                text("message"),
            ) {
                (Some(kind), Some(message)) => Some(format!("{kind}: {message}")),
                (kind, message) => message.or(kind).map(str::to_string),
            }
        }
        Ok(Value::String(message)) => Some(message),
        _ => None,
    };
    sanitize(readable.as_deref().unwrap_or(raw).trim())
}

const ATTACHED_PICKUP: &str = "and `mda dev` reloads it on save.";
const STARTED_PICKUP: &str = "then restart `mda chat`.";

/// How the server this session talks to picks up a key added to `.env`.
fn env_pickup(target: &Target) -> &'static str {
    match target {
        Target::Attached { .. } => ATTACHED_PICKUP,
        Target::Started(_) => STARTED_PICKUP,
        Target::Pinned { .. } => "then restart the Agent Server.",
    }
}

/// Deploy's model-key preflight, as a warning before the first message.
/// `server` says whether the session attached to a running dev server or
/// started one, so the two can be told apart in telemetry.
fn warn_missing_model_api_key(
    project: Option<&Project>,
    env: &BTreeMap<String, String>,
    server: &str,
    pickup: &str,
) -> Result<()> {
    let Some(project) = project else {
        return Ok(());
    };
    let pickup = format!("{pickup} Messages fail until then.");
    if let Some(warning) = dev::missing_model_api_key_warning(project, env, &pickup) {
        otel_logger::warn(
            &format!("model API key missing ({server} dev server)"),
            &anyhow!(warning.clone()),
        );
        log::warning(warning)?;
    }
    Ok(())
}

/// The provider key the project's model authenticates with locally.
fn model_api_key(project: &Project) -> Option<&'static str> {
    let model = agent_config::read_agent_static_config(project)
        .ok()?
        .model?;
    match dependencies::deploy_model_api_key_requirement(&model) {
        dependencies::ModelApiKeyRequirement::DeploymentSecret(key) => Some(key),
        _ => None,
    }
}

/// The fix for a failed run that is about the model's credentials: one that
/// names a provider key (a missing key, from inside the run), or an
/// authentication failure (a rejected one) when the model's key is known.
fn run_failure_hint(
    message: &str,
    model_key: Option<&str>,
    env_path: &Path,
    pickup: &str,
) -> Option<String> {
    let env = env_path.display();
    let provider_keys = dependencies::provider_api_key_envs();
    if let Some(key) = message
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .find(|word| provider_keys.contains(word))
    {
        return Some(format!("Add `{key}=...` to {env}, {pickup}"));
    }
    let key = model_key.filter(|_| message.to_ascii_lowercase().contains("authentication"))?;
    Some(format!("Set a valid `{key}` in {env}, {pickup}"))
}

fn resume_command(
    opts: &ChatOptions,
    target: &Target,
    server: &AgentServer,
    thread_id: &str,
) -> String {
    let pinned = match target.resume_url() {
        Some(url) => format!(" --url {}", shell_quote(url)),
        None => String::new(),
    };
    format!(
        "mda chat{pinned} --assistant {} --thread {} -- {}",
        shell_quote(&server.assistant_id),
        shell_quote(thread_id),
        shell_quote(&opts.path.to_string_lossy()),
    )
}

/// Quote an argument for the displayed command, using PowerShell on Windows.
fn shell_quote(value: &str) -> String {
    if cfg!(windows) {
        format!("'{}'", value.replace('\'', "''"))
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

/// A trimmed, non-empty CLI or env value.
fn flag(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn normalize_api_url(url: &str) -> Result<String> {
    let trimmed = url.trim().trim_end_matches('/');
    if !(trimmed.starts_with("http://") || trimmed.starts_with("https://")) {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!("Agent Server URL must start with http:// or https:// (got {url:?})"),
        ));
    }
    Ok(trimmed.to_string())
}

/// `--assistant`, then what `mda dev` staged, then the authored agent name.
fn resolve_assistant_id(opts: &ChatOptions, staged: &BTreeMap<String, String>) -> Result<String> {
    if let Some(id) = flag(opts.assistant.as_deref())
        .or_else(|| flag(staged.get(MDA_ASSISTANT_ID_ENV).map(String::as_str)))
    {
        return Ok(id.to_string());
    }
    let project = Installation::from_env()?.load_project(&opts.path)?;
    agent_config::agent_assistant_id(&project)
}

/// Read one message. `None` means the user is done (Ctrl-C, Ctrl-D).
fn prompt_message(interrupt: &Interrupt) -> Result<Option<String>> {
    if interrupt.is_requested() {
        return Ok(None);
    }
    let result: io::Result<String> = chat_output::with_input(|| {
        input("You")
            .placeholder("Message the agent")
            .validate(|value: &String| {
                if value.trim().is_empty() {
                    Err("Type a message first")
                } else {
                    Ok(())
                }
            })
            .interact()
    });
    match result {
        Ok(text) => Ok(Some(text.trim().to_string())),
        Err(err)
            if matches!(
                err.kind(),
                io::ErrorKind::Interrupted | io::ErrorKind::UnexpectedEof
            ) =>
        {
            Ok(None)
        }
        Err(err) => Err(err).context("failed to read the message"),
    }
}

/// Stream one run, rendering it as it arrives. Returns the interrupts the
/// run paused on, if any.
fn stream_turn(
    server: &AgentServer,
    thread_id: &str,
    body: Value,
    interrupt: &Interrupt,
) -> Result<Vec<PendingInterrupt>> {
    let _cursor = chat_output::HiddenCursor::new()?;
    let events = server.stream_run(thread_id, body)?;

    let waiting = spinner();
    waiting.start("Thinking…");
    let mut renderer = TurnRenderer::new(io::stderr());
    let mut state = RunState::default();
    let mut paused = false;
    for event in events {
        let event = event?;
        if interrupt.is_requested() {
            break;
        }
        match event {
            RunEvent::Message(message) => {
                waiting.clear();
                renderer.render_message(&message)?;
            }
            RunEvent::Values(values) => {
                paused |= !values.interrupts.is_empty();
                state = values;
            }
            RunEvent::Ignored => {}
            RunEvent::Error(error) => {
                waiting.clear();
                renderer.finish()?;
                return Err(RunFailed(run_error_message(&error)).into());
            }
        }
    }
    waiting.clear();

    renderer.finish_turn(state.reply.as_deref())?;
    // Calls that pause in parallel report their interrupts in separate `values`
    // events, so read the full set from thread state.
    if paused {
        server.thread_interrupts(thread_id)
    } else {
        Ok(Vec::new())
    }
}

// --- interrupts ------------------------------------------------------------

/// Ask the user how to resume. `None` means they backed out.
fn prompt_resume(interrupts: &[PendingInterrupt]) -> Result<Option<Value>> {
    let mut answers = Vec::with_capacity(interrupts.len());
    for pending in interrupts {
        let answer = if is_tool_approval(&pending.value) {
            prompt_tool_approval(pending)?
        } else {
            match tool_actions(&pending.value)? {
                Some(actions) => prompt_tool_decisions(actions, &pending.value)?,
                None => prompt_free_form(&pending.value)?,
            }
        };
        let Some(answer) = answer else {
            return Ok(None);
        };
        answers.push((pending.id.clone(), answer));
    }
    Ok(Some(resume_payload(answers)?))
}

/// One interrupt resumes with its value; several resume keyed by interrupt id.
fn resume_payload(mut answers: Vec<(Option<String>, Value)>) -> Result<Value> {
    if answers.len() == 1 {
        return Ok(answers.remove(0).1);
    }
    let mut map = Map::new();
    for (id, value) in answers {
        let id = id.filter(|id| !id.is_empty()).context(
            "Cannot resume multiple interrupts without their IDs. Resume this thread in Studio.",
        )?;
        if map.insert(id, value).is_some() {
            bail!("Cannot resume interrupts with duplicate IDs. Resume this thread in Studio.");
        }
    }
    Ok(Value::Object(map))
}

/// Approve or reject each tool call a human-in-the-loop interrupt proposes,
/// in the `{"decisions": [...]}` shape LangChain's middleware resumes with.
fn prompt_tool_decisions(actions: &[Value], request: &Value) -> Result<Option<Value>> {
    // Validate every policy before asking for approval of any action.
    let choices = actions
        .iter()
        .map(|action| {
            let name = action
                .get("name")
                .and_then(Value::as_str)
                .context("the tool review is missing an action name")?;
            review_choices(request, name)
        })
        .collect::<Result<Vec<_>>>()?;
    let mut decisions = Vec::with_capacity(actions.len());
    for (action, choices) in actions.iter().zip(choices) {
        note("The agent wants to run a tool", tool_review_note(action))?;
        let mut prompt = select("Allow it?");
        for choice in choices {
            prompt = match choice {
                "approve" => prompt.item(choice, "Approve", "run the tool as proposed"),
                _ => prompt.item(choice, "Reject", "skip it and let the agent continue"),
            };
        }
        let decision: io::Result<&str> = prompt.interact();
        match decision {
            Ok(decision) => decisions.push(json!({ "type": decision })),
            Err(err) if err.kind() == io::ErrorKind::Interrupted => return Ok(None),
            Err(err) => return Err(err).context("failed to read the decision"),
        }
    }
    Ok(Some(json!({ "decisions": decisions })))
}

/// Decisions per-call HITL can offer, in the order the menu lists them.
const TOOL_DECISIONS: [&str; 4] = ["approve", "edit", "reject", "respond"];

/// A per-call human-in-the-loop interrupt: one tool call, answered with one
/// decision object.
fn is_tool_approval(value: &Value) -> bool {
    value.get("type").and_then(Value::as_str) == Some("tool_approval")
}

/// Ask how to answer one tool call paused by per-call human-in-the-loop. The
/// choices come from the interrupt's `response_schema`.
fn prompt_tool_approval(pending: &PendingInterrupt) -> Result<Option<Value>> {
    let request = &pending.value;
    let choices = pending
        .response_schema
        .as_ref()
        .map(decision_choices)
        .unwrap_or_default();
    if choices.is_empty() {
        bail!("mda chat can't answer this tool approval. Answer it in Studio.");
    }
    note("The agent wants to run a tool", tool_review_note(request))?;
    let mut prompt = select("Allow it?");
    for choice in choices {
        prompt = match choice {
            "approve" => prompt.item(choice, "Approve", "run the tool as proposed"),
            "edit" => prompt.item(choice, "Edit", "change the arguments, then run it"),
            "reject" => prompt.item(choice, "Reject", "skip it and tell the agent why"),
            _ => prompt.item(
                choice,
                "Respond",
                "answer for the tool instead of running it",
            ),
        };
    }
    let decision = match prompt.interact() {
        Ok(decision) => decision,
        Err(err) if err.kind() == io::ErrorKind::Interrupted => return Ok(None),
        Err(err) => return Err(err).context("failed to read the decision"),
    };
    // Each answer is one decision object that the interrupt's `response_schema` accepts.
    Ok(match decision {
        "approve" => Some(json!({ "type": "approve" })),
        "edit" => prompt_edited_args(request)?.map(|args| {
            json!({ "type": "edit", "edited_action": { "name": request["name"], "args": args } })
        }),
        "reject" => read_text("Reason for the agent (optional)", false)?.map(|reason| {
            if reason.is_empty() {
                json!({ "type": "reject" })
            } else {
                json!({ "type": "reject", "message": reason })
            }
        }),
        _ => read_text("Your answer, given to the agent as the tool's result", true)?
            .map(|message| json!({ "type": "respond", "message": message })),
    })
}

/// Read replacement arguments as a JSON object, starting from the current ones.
fn prompt_edited_args(request: &Value) -> Result<Option<Value>> {
    let current =
        serde_json::to_string(request.get("args").unwrap_or(&json!({}))).unwrap_or_default();
    let reply: io::Result<String> = input("New arguments as JSON")
        .default_input(&current)
        .validate(|text: &String| match serde_json::from_str::<Value>(text) {
            Ok(Value::Object(_)) => Ok(()),
            _ => Err("Enter a JSON object, like {\"to\": \"bob\"}"),
        })
        .interact();
    match reply {
        Ok(text) => Ok(Some(
            serde_json::from_str(&text).context("failed to read the arguments")?,
        )),
        Err(err) if err.kind() == io::ErrorKind::Interrupted => Ok(None),
        Err(err) => Err(err).context("failed to read the arguments"),
    }
}

/// Read one line of text. `None` means the user backed out.
fn read_text(prompt: &str, required: bool) -> Result<Option<String>> {
    let reply: io::Result<String> = input(prompt).required(required).interact();
    match reply {
        Ok(text) => Ok(Some(text.trim().to_string())),
        Err(err) if err.kind() == io::ErrorKind::Interrupted => Ok(None),
        Err(err) => Err(err).context("failed to read the reply"),
    }
}

/// The decision branches of a per-call `response_schema`: the union's members
/// (`oneOf` or `anyOf`), or the schema itself for a one-decision tool, with
/// `$ref`s into `$defs` followed.
fn decision_branches(schema: &Value) -> Vec<&Value> {
    match schema
        .get("oneOf")
        .or_else(|| schema.get("anyOf"))
        .and_then(Value::as_array)
    {
        Some(members) => members.iter().map(|m| resolve_ref(schema, m)).collect(),
        None => vec![schema],
    }
}

fn resolve_ref<'a>(root: &'a Value, node: &'a Value) -> &'a Value {
    node.get("$ref")
        .and_then(Value::as_str)
        .and_then(|reference| reference.strip_prefix("#/$defs/"))
        .and_then(|name| root.get("$defs")?.get(name))
        .unwrap_or(node)
}

/// The decision a branch stands for: its `type` property's `const`.
fn branch_type(branch: &Value) -> Option<&str> {
    branch.pointer("/properties/type/const")?.as_str()
}

/// The decisions a per-call `response_schema` allows, in menu order.
fn decision_choices(schema: &Value) -> Vec<&'static str> {
    let allowed: Vec<&str> = decision_branches(schema)
        .into_iter()
        .filter_map(branch_type)
        .collect();
    TOOL_DECISIONS
        .into_iter()
        .filter(|decision| allowed.contains(decision))
        .collect()
}

fn tool_actions(request: &Value) -> Result<Option<&[Value]>> {
    request
        .get("action_requests")
        .or_else(|| request.get("actionRequests"))
        .map(|actions| {
            actions
                .as_array()
                .map(Vec::as_slice)
                .context("the tool review action requests must be an array")
        })
        .transpose()
}

fn review_choices(request: &Value, name: &str) -> Result<Vec<&'static str>> {
    let configs = request
        .get("review_configs")
        .or_else(|| request.get("reviewConfigs"))
        .and_then(Value::as_array)
        .context("the tool review is missing review configs; resume this thread in Studio")?;
    let config = configs
        .iter()
        .find(|config| {
            config
                .get("action_name")
                .or_else(|| config.get("actionName"))
                .and_then(Value::as_str)
                == Some(name)
        })
        .context("the tool review is missing an action's policy; resume this thread in Studio")?;
    let allowed = config
        .get("allowed_decisions")
        .or_else(|| config.get("allowedDecisions"))
        .and_then(Value::as_array)
        .context("the tool review policy is missing allowed decisions")?;
    if allowed.iter().any(|decision| !decision.is_string()) {
        bail!("the tool review allowed decisions must be strings");
    }
    let supported: Vec<_> = ["approve", "reject"]
        .into_iter()
        .filter(|choice| {
            allowed
                .iter()
                .any(|decision| decision.as_str() == Some(*choice))
        })
        .collect();
    if supported.is_empty() {
        bail!("this tool review does not allow approve or reject; resume this thread in Studio");
    }
    Ok(supported)
}

fn tool_review_note(action: &Value) -> String {
    let name = sanitize(action.get("name").and_then(Value::as_str).unwrap_or("tool"));
    let args = serde_json::to_string_pretty(action.get("args").unwrap_or(&Value::Null))
        .unwrap_or_default();
    format!("{}\n{}", style(name).bold(), sanitize(&args))
}

/// An interrupt that is not a tool review: show it and resume with free text.
fn prompt_free_form(value: &Value) -> Result<Option<Value>> {
    let shown = match value {
        Value::String(text) => text.clone(),
        other => serde_json::to_string_pretty(other).unwrap_or_default(),
    };
    note("The agent paused and is waiting on you", sanitize(&shown))?;
    let reply: io::Result<String> = input("Your reply").required(false).interact();
    match reply {
        Ok(text) => Ok(Some(Value::String(text.trim().to_string()))),
        Err(err) if err.kind() == io::ErrorKind::Interrupted => Ok(None),
        Err(err) => Err(err).context("failed to read the reply"),
    }
}

#[cfg(test)]
mod tests;
