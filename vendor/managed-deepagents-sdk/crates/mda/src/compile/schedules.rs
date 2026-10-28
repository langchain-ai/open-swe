//! Static extraction of `schedules/` declarations into LangGraph cron config.
//!
//! Schedule modules are authored as code, but deploy-time cron provisioning needs
//! JSON. To keep the compiler predictable and side-effect free, only statically
//! serializable declarations are accepted: literals, arrays, objects, and
//! references to top-level literal constants.

use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::Path;

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Map, Value};

use super::discovery::{entry_stem_name, language_dir_entries};
use super::static_extract::{extract_python_keyword_config, extract_typescript_object_config};
use crate::project::{Language, Project};

/// Cron entries keyed by their file-derived schedule name.
pub(crate) type ScheduleCrons = BTreeMap<String, Value>;

/// Inputs derived by the compiler before static schedule extraction.
pub(super) struct ScheduleReadContext<'a> {
    pub source_root: &'a Path,
    pub assistant_id: &'a str,
    /// The file-derived names of the channels the project declares.
    pub channels: &'a [String],
}

/// Read every direct child of `schedules/` for the project's language and turn
/// its named `schedule` export into a LangGraph `crons` entry.
///
/// `assistant_id` is written into each cron as `graph` so deploy can map it to
/// the LangGraph assistant id (matching `langgraph.json` `graphs` keys).
pub(super) fn read_schedule_crons(
    project: &Project,
    context: ScheduleReadContext<'_>,
) -> Result<ScheduleCrons> {
    let mut crons = BTreeMap::new();
    let mut seen = HashSet::new();

    for rel in language_dir_entries(project, "schedules") {
        let name = entry_stem_name(&rel, "schedule")?;
        if !seen.insert(name.clone()) {
            bail!("duplicate schedule name '{name}' from schedules/");
        }
        let path = context.source_root.join(&rel);
        let source = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let config = match project.language {
            Language::TypeScript => {
                extract_typescript_object_config(&source, &rel, "schedule", "defineSchedule")?
            }
            Language::Python => {
                extract_python_keyword_config(&source, &rel, "schedule", "define_schedule")?
            }
        };
        crons.insert(
            name.clone(),
            schedule_cron(&name, &config, context.assistant_id, context.channels)?,
        );
    }

    Ok(crons)
}

fn schedule_cron(
    name: &str,
    config: &Value,
    assistant_id: &str,
    channels: &[String],
) -> Result<Value> {
    let object = config
        .as_object()
        .ok_or_else(|| anyhow!("schedule '{name}' must be a configuration object"))?;

    let cron = string_field(object, "cron", name)?;
    validate_cron(cron, name)?;

    let input = match (object.get("prompt"), object.get("input")) {
        (Some(prompt), None) => {
            let prompt = prompt
                .as_str()
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| anyhow!("schedule '{name}' prompt must be a non-empty string"))?;
            json!({ "messages": [{ "role": "user", "content": prompt }] })
        }
        (None, Some(input)) => input.clone(),
        _ => bail!("schedule '{name}' must define exactly one of `prompt` or `input`"),
    };

    let mut cron_entry = Map::new();
    cron_entry.insert("graph".to_string(), Value::String(assistant_id.to_string()));
    cron_entry.insert("schedule".to_string(), Value::String(cron.to_string()));
    cron_entry.insert("input".to_string(), input);
    cron_entry.insert(
        "metadata".to_string(),
        json!({
            "mda_managed": true,
            "mda_managed_by": "deploy",
            "mda_schedule": name,
        }),
    );
    let mut context = match object.get("context") {
        Some(Value::Object(map)) => map.clone(),
        Some(_) => bail!("schedule '{name}' context must be an object"),
        None => Map::new(),
    };
    if context
        .keys()
        .any(|key| key == "channel" || key.starts_with("mda_"))
    {
        bail!("schedule '{name}' context keys `channel` and `mda_*` are reserved");
    }
    let mode = thread_mode(object.get("thread"), name)?;
    let mut schedule = json!({ "name": name });
    let deliver_to = object.get("deliverTo").or_else(|| object.get("deliver_to"));
    if let Some(deliver_to) = deliver_to {
        schedule["deliver_to"] = deliver_to.clone();
    }
    let opens_thread =
        matches!(mode, ThreadMode::Ephemeral) && starts_conversation(deliver_to, channels);
    if opens_thread {
        schedule["new_thread"] = Value::Bool(true);
    }
    context.insert("mda_schedule".to_string(), schedule);
    cron_entry.insert("context".to_string(), Value::Object(context));

    if let Some(timezone) = object.get("timezone") {
        let timezone = timezone
            .as_str()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| anyhow!("schedule '{name}' timezone must be a non-empty string"))?;
        cron_entry.insert("timezone".to_string(), Value::String(timezone.to_string()));
    }

    match mode {
        ThreadMode::Ephemeral => {
            let completed = if opens_thread { "keep" } else { "delete" };
            cron_entry.insert(
                "on_run_completed".to_string(),
                Value::String(completed.to_string()),
            );
        }
        ThreadMode::Persistent(id) => {
            cron_entry.insert("thread_id".to_string(), Value::String(id));
        }
    }

    Ok(Value::Object(cron_entry))
}

/// Whether a run's `deliverTo` starts a conversation in a declared channel,
/// whose replies continue the run's thread. Other deliveries keep no thread.
fn starts_conversation(deliver_to: Option<&Value>, channels: &[String]) -> bool {
    let Some(target) = deliver_to else {
        return false;
    };
    let declared = target
        .get("channel")
        .and_then(Value::as_str)
        .is_some_and(|channel| channels.iter().any(|name| name == channel));
    let posts = target
        .get("autoPost")
        .or_else(|| target.get("auto_post"))
        .and_then(Value::as_bool)
        != Some(false);
    let conversation = target
        .get("to")
        .and_then(|to| to.get("type"))
        .and_then(Value::as_str)
        == Some("provider_conversation");
    declared && posts && conversation
}

fn string_field<'a>(object: &'a Map<String, Value>, key: &str, name: &str) -> Result<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| anyhow!("schedule '{name}' must declare a non-empty `{key}`"))
}

fn validate_cron(cron: &str, name: &str) -> Result<()> {
    if cron.split_whitespace().count() != 5 {
        bail!("schedule '{name}' cron must be a standard 5-field expression");
    }
    Ok(())
}

enum ThreadMode {
    Ephemeral,
    Persistent(String),
}

fn thread_mode(thread: Option<&Value>, name: &str) -> Result<ThreadMode> {
    let Some(thread) = thread else {
        return Ok(ThreadMode::Ephemeral);
    };
    let object = thread
        .as_object()
        .ok_or_else(|| anyhow!("schedule '{name}' thread must be an object"))?;
    let mode = object
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("ephemeral");
    match mode {
        "ephemeral" => Ok(ThreadMode::Ephemeral),
        "persistent" => {
            let id = object
                .get("id")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| anyhow!("schedule '{name}' persistent thread requires an id"))?;
            Ok(ThreadMode::Persistent(id.to_string()))
        }
        other => bail!("schedule '{name}' has unsupported thread mode '{other}'"),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::{json, Value};

    use super::schedule_cron;

    fn cron(config: Value) -> Value {
        schedule_cron("daily", &config, "agent", &["slack".to_string()])
            .expect("schedule should compile")
    }

    fn cron_error(config: Value) -> String {
        schedule_cron("daily", &config, "agent", &[])
            .expect_err("schedule should be rejected")
            .to_string()
    }

    #[test]
    fn prompt_becomes_user_message_on_ephemeral_thread() {
        let entry = cron(json!({ "cron": "0 9 * * *", "prompt": "Summarize" }));

        assert_eq!(
            entry["input"],
            json!({ "messages": [{ "role": "user", "content": "Summarize" }] })
        );
        assert_eq!(entry["on_run_completed"], "delete");
        assert!(entry.get("thread_id").is_none());
    }

    #[test]
    fn input_passes_through_and_persistent_thread_sets_thread_id() {
        let entry = cron(json!({
            "cron": "0 9 * * *",
            "input": { "topic": "news" },
            "thread": { "mode": "persistent", "id": "daily-thread" },
        }));

        assert_eq!(entry["input"], json!({ "topic": "news" }));
        assert_eq!(entry["thread_id"], "daily-thread");
        assert!(entry.get("on_run_completed").is_none());
        assert!(entry["context"]["mda_schedule"].get("new_thread").is_none());
    }

    #[test]
    fn deliver_to_lands_in_schedule_context_for_both_spellings() {
        for key in ["deliverTo", "deliver_to"] {
            let mut config = json!({ "cron": "0 9 * * *", "prompt": "Summarize" });
            config[key] = json!({ "channel": "slack" });

            assert_eq!(
                cron(config)["context"]["mda_schedule"],
                json!({ "name": "daily", "deliver_to": { "channel": "slack" } })
            );
        }
    }

    #[test]
    fn conversation_deliver_to_keeps_its_ephemeral_thread() {
        let entry = cron(json!({
            "cron": "0 9 * * *",
            "prompt": "Summarize",
            "deliverTo": {
                "channel": "slack",
                "to": { "type": "provider_conversation", "conversationId": "C1" },
            },
        }));
        assert_eq!(entry["on_run_completed"], "keep");

        assert_eq!(entry["context"]["mda_schedule"]["new_thread"], true);

        for deliver_to in [
            json!({
                "channel": "slack",
                "to": { "type": "provider_thread", "conversationId": "C1", "threadId": "1.2" },
            }),
            json!({
                "channel": "other",
                "to": { "type": "provider_conversation", "conversationId": "C1" },
            }),
            json!({
                "channel": "slack",
                "to": { "type": "provider_conversation", "conversationId": "C1" },
                "autoPost": false,
            }),
        ] {
            let entry = cron(json!({
                "cron": "0 9 * * *",
                "prompt": "Summarize",
                "deliverTo": deliver_to,
            }));
            assert_eq!(entry["on_run_completed"], "delete");
            assert!(entry["context"]["mda_schedule"].get("new_thread").is_none());
        }
    }

    #[test]
    fn rejects_both_or_neither_prompt_and_input() {
        for config in [
            json!({ "cron": "0 9 * * *" }),
            json!({ "cron": "0 9 * * *", "prompt": "Summarize", "input": {} }),
        ] {
            assert_eq!(
                cron_error(config),
                "schedule 'daily' must define exactly one of `prompt` or `input`"
            );
        }
    }

    #[test]
    fn rejects_invalid_fields() {
        let cases = [
            (
                json!({ "cron": "0 9 * * *", "prompt": "  " }),
                "schedule 'daily' prompt must be a non-empty string",
            ),
            (
                json!({ "cron": "0 9 * *", "prompt": "Summarize" }),
                "schedule 'daily' cron must be a standard 5-field expression",
            ),
            (
                json!({ "cron": "0 9 * * *", "prompt": "Summarize", "context": { "channel": "x" } }),
                "schedule 'daily' context keys `channel` and `mda_*` are reserved",
            ),
            (
                json!({ "cron": "0 9 * * *", "prompt": "Summarize", "context": { "mda_x": 1 } }),
                "schedule 'daily' context keys `channel` and `mda_*` are reserved",
            ),
            (
                json!({ "cron": "0 9 * * *", "prompt": "Summarize", "thread": { "mode": "persistent" } }),
                "schedule 'daily' persistent thread requires an id",
            ),
            (
                json!({ "cron": "0 9 * * *", "prompt": "Summarize", "thread": { "mode": "forever" } }),
                "schedule 'daily' has unsupported thread mode 'forever'",
            ),
        ];

        for (config, expected) in cases {
            assert_eq!(cron_error(config), expected);
        }
    }
}
