//! Normalize Agent Server wire events before they reach command or output code.

use anyhow::{Context, Result};
use serde_json::Value;

use super::{pending_interrupts, sse::SseEvent, PendingInterrupt};

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RunEvent {
    Message(Message),
    Values(RunState),
    Error(String),
    /// Keep the caller responsive to cancellation between unhandled events.
    Ignored,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Message {
    Assistant {
        text: String,
        tool_calls: Vec<ToolCall>,
    },
    Tool {
        name: String,
        text: String,
    },
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) struct ToolCall {
    /// Stable ID, stream index, or name used to avoid repeated announcements.
    pub(crate) key: String,
    pub(crate) name: String,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct RunState {
    /// Last assistant text added or changed since the pre-run snapshot.
    pub(crate) reply: Option<String>,
    pub(crate) interrupts: Vec<PendingInterrupt>,
}

pub(super) fn decode_event(event: SseEvent, previous: &Value) -> Result<RunEvent> {
    match event.event.as_str() {
        "messages" => {
            let data: Value = serde_json::from_str(&event.data)
                .context("the Agent Server sent a malformed messages event")?;
            Ok(data
                .as_array()
                .and_then(|pair| pair.first())
                .and_then(message)
                .map(RunEvent::Message)
                .unwrap_or(RunEvent::Ignored))
        }
        "values" => {
            let values: Value = serde_json::from_str(&event.data)
                .context("the Agent Server sent a malformed values event")?;
            Ok(RunEvent::Values(RunState {
                reply: last_assistant_text(&values, previous),
                interrupts: pending_interrupts(&values),
            }))
        }
        "error" => Ok(RunEvent::Error(event.data)),
        _ => Ok(RunEvent::Ignored),
    }
}

fn message(chunk: &Value) -> Option<Message> {
    match chunk.get("type").and_then(Value::as_str) {
        Some("ai" | "AIMessageChunk" | "AIMessage") => Some(Message::Assistant {
            text: text_content(chunk.get("content")),
            tool_calls: tool_call_names(chunk),
        }),
        Some("tool" | "ToolMessage" | "ToolMessageChunk") => Some(Message::Tool {
            name: chunk
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("tool")
                .to_string(),
            text: text_content(chunk.get("content")),
        }),
        _ => None,
    }
}

/// Plain text of a message `content`, which is a string or a list of blocks.
fn text_content(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(blocks)) => blocks
            .iter()
            .filter_map(|block| match block {
                Value::String(text) => Some(text.as_str()),
                Value::Object(map) if map.get("type").and_then(Value::as_str) == Some("text") => {
                    map.get("text").and_then(Value::as_str)
                }
                _ => None,
            })
            .collect(),
        _ => String::new(),
    }
}

/// Every named tool call on an assistant chunk, with its announcement key.
///
/// Streamed chunks carry `tool_call_chunks` (name on the first piece only,
/// keyed by `id` or `index`); complete messages carry `tool_calls`.
fn tool_call_names(chunk: &Value) -> Vec<ToolCall> {
    ["tool_call_chunks", "tool_calls"]
        .into_iter()
        .filter_map(|field| chunk.get(field).and_then(Value::as_array))
        .flatten()
        .filter_map(|call| {
            let name = call.get("name").and_then(Value::as_str)?.trim();
            if name.is_empty() {
                return None;
            }
            let key = match (call.get("id").and_then(Value::as_str), call.get("index")) {
                (Some(id), _) if !id.is_empty() => id.to_string(),
                (_, Some(index)) => format!("index:{index}"),
                _ => format!("name:{name}"),
            };
            Some(ToolCall {
                key,
                name: name.to_string(),
            })
        })
        .collect()
}

/// The last assistant message added or changed since the pre-run snapshot.
fn last_assistant_text(values: &Value, previous: &Value) -> Option<String> {
    let previous = previous
        .get("messages")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let messages = values.get("messages")?.as_array()?;
    let history_is_prefix = messages.starts_with(previous);
    messages
        .iter()
        .enumerate()
        .rev()
        .find(|(index, message)| {
            if !matches!(
                message.get("type").and_then(Value::as_str),
                Some("ai" | "AIMessage" | "AIMessageChunk")
            ) {
                return false;
            }
            if let Some(id) = message
                .get("id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
            {
                return previous
                    .iter()
                    .find(|old| old.get("id").and_then(Value::as_str) == Some(id))
                    .is_none_or(|old| {
                        text_content(old.get("content")) != text_content(message.get("content"))
                    });
            }
            // Without IDs, accept only a suffix after unchanged history.
            history_is_prefix && *index >= previous.len()
        })
        .map(|(_, message)| text_content(message.get("content")))
        .filter(|text| !text.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn decode(event: &str, data: &str) -> Result<RunEvent> {
        decode_event(
            SseEvent {
                event: event.into(),
                data: data.into(),
            },
            &Value::Null,
        )
    }

    #[test]
    fn message_aliases_normalize_to_the_same_types() {
        for kind in ["ai", "AIMessage", "AIMessageChunk"] {
            let data = json!([{ "type": kind, "content": ["a", {"type": "text", "text": "b"}],
                "tool_calls": [{"name": "read", "id": "call"}] }, {}]);
            assert_eq!(
                decode("messages", &data.to_string()).unwrap(),
                RunEvent::Message(Message::Assistant {
                    text: "ab".into(),
                    tool_calls: vec![ToolCall {
                        key: "call".into(),
                        name: "read".into()
                    }],
                })
            );
        }
        for kind in ["tool", "ToolMessage", "ToolMessageChunk"] {
            let data = json!([{ "type": kind, "content": "result" }, {}]);
            assert_eq!(
                decode("messages", &data.to_string()).unwrap(),
                RunEvent::Message(Message::Tool {
                    name: "tool".into(),
                    text: "result".into(),
                })
            );
        }
    }

    #[test]
    fn malformed_event_json_keeps_its_context() {
        for kind in ["messages", "values"] {
            let error = decode(kind, "{").unwrap_err();
            assert_eq!(
                error.to_string(),
                format!("the Agent Server sent a malformed {kind} event")
            );
        }
    }

    #[test]
    fn unhandled_events_and_message_types_are_ignored() {
        for (kind, data) in [
            ("metadata", "not JSON"),
            ("future-event", "{}"),
            ("messages", "[]"),
            ("messages", r#"[{"type":"human","content":"hello"},{}]"#),
        ] {
            assert_eq!(decode(kind, data).unwrap(), RunEvent::Ignored);
        }
    }

    #[test]
    fn remote_error_payload_is_preserved_for_the_caller() {
        let data = r#"{"error":"Failure","message":"details"}"#;
        assert_eq!(decode("error", data).unwrap(), RunEvent::Error(data.into()));
    }

    #[test]
    fn text_content_reads_strings_and_text_blocks() {
        assert_eq!(text_content(Some(&json!("hi"))), "hi");
        assert_eq!(
            text_content(Some(&json!([
                {"type": "text", "text": "a"},
                {"type": "tool_use", "id": "x"},
                {"type": "text", "text": "b"},
            ]))),
            "ab"
        );
        assert_eq!(text_content(None), "");
    }

    #[test]
    fn tool_call_names_dedupe_by_id_or_index() {
        let chunk = json!({
            "type": "AIMessageChunk",
            "tool_call_chunks": [
                {"name": "query_db", "id": "call_1", "index": 0},
                {"name": "", "id": null, "index": 0},
                {"name": "search", "id": null, "index": 1},
            ],
        });
        assert_eq!(
            tool_call_names(&chunk),
            vec![
                ToolCall {
                    key: "call_1".into(),
                    name: "query_db".into()
                },
                ToolCall {
                    key: "index:1".into(),
                    name: "search".into()
                },
            ]
        );
    }

    #[test]
    fn last_assistant_text_skips_tool_and_human_messages() {
        let values = json!({"messages": [
            {"type": "human", "content": "hi"},
            {"type": "ai", "content": "hello"},
            {"type": "tool", "content": "result"},
        ]});
        assert_eq!(
            last_assistant_text(&values, &Value::Null).as_deref(),
            Some("hello")
        );
        assert_eq!(
            last_assistant_text(&json!({"messages": []}), &Value::Null),
            None
        );
    }

    #[test]
    fn fallback_does_not_replay_a_previous_answer_on_an_early_interrupt() {
        let values = json!({"messages": [
        {"id": "human-1", "type": "human", "content": "hi"},
        {"id": "ai-1", "type": "ai", "content": "old answer"},
        {"id": "human-2", "type": "human", "content": "new question"}
    ], "__interrupt__": [{"id": "review", "value": "Continue?"}]});
        let previous = json!({"messages": values["messages"].as_array().unwrap()[..2]});
        assert_eq!(last_assistant_text(&values, &previous), None);
    }

    #[test]
    fn fallback_does_not_replay_history_on_resume() {
        let before =
            json!({"messages": [{"id": "ai-1", "type": "ai", "content": "before approval"}]});
        let paused = json!({"messages": before["messages"], "__interrupt__": [{"id": "again", "value": "more review"}]});
        assert_eq!(last_assistant_text(&paused, &before), None);
    }

    #[test]
    fn fallback_keeps_new_unstreamed_answers_with_repeated_text() {
        for with_ids in [true, false] {
            let mut old = json!({"type": "ai", "content": "same answer"});
            let mut new = old.clone();
            if with_ids {
                old["id"] = json!("old");
                new["id"] = json!("new");
            }
            let before = json!({"messages": [old.clone()]});
            let after = json!({"messages": [old, new]});
            assert_eq!(
                last_assistant_text(&after, &before).as_deref(),
                Some("same answer")
            );
        }
    }

    #[test]
    fn fallback_ignores_metadata_changes_but_keeps_changed_message_content() {
        let before = json!({"messages": [{"id": "ai-1", "type": "ai", "content": "old"}]});
        let metadata = json!({"messages": [{"id": "ai-1", "type": "ai", "content": "old", "response_metadata": {"extra": true}}]});
        assert_eq!(last_assistant_text(&metadata, &before), None);
        let changed = json!({"messages": [{"id": "ai-1", "type": "ai", "content": "new"}]});
        assert_eq!(
            last_assistant_text(&changed, &before).as_deref(),
            Some("new")
        );
    }

    #[test]
    fn fallback_does_not_replay_idless_history_after_reordering() {
        let old = json!({"type": "ai", "content": "old answer"});
        let previous = json!({"messages": [old]});
        let reordered = json!({"messages": [
            {"type": "human", "content": "new question"}, old
        ]});
        assert_eq!(last_assistant_text(&reordered, &previous), None);
    }
}
