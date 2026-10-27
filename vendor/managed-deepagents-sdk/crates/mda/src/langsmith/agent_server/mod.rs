//! Unauthenticated Agent Server thread and run operations for the terminal client.

mod events;
mod sse;

pub(crate) use events::{Message, RunEvent, RunState, ToolCall};

use std::io::BufReader;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::blocking::{Client as HttpClient, Response};
use reqwest::StatusCode;
use serde_json::{json, Value};

use super::{ApiError, REQUEST_TIMEOUT};
use crate::error::{categorized, ErrorCategory};

/// Time allowed to open a connection.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);

pub(crate) struct AgentServer {
    http: HttpClient,
    /// Base URL, trailing slash trimmed.
    pub(crate) api_url: String,
    pub(crate) assistant_id: String,
}

/// One entry of a paused run's `__interrupt__`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PendingInterrupt {
    pub(crate) id: Option<String>,
    pub(crate) value: Value,
    /// JSON schema for the answer, when the interrupt declares one (per-call HITL
    /// does). Run streams carry it, and thread state does from langgraph-api 0.16.
    pub(crate) response_schema: Option<Value>,
}

impl AgentServer {
    pub(crate) fn new(api_url: String, assistant_id: String) -> Result<Self> {
        let http = crate::http::client_builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build HTTP client")?;
        Ok(Self {
            http,
            api_url,
            assistant_id,
        })
    }

    fn post(&self, path: &str, body: &Value) -> Result<Response> {
        self.http
            .post(format!("{}{path}", self.api_url))
            .json(body)
            .send()
            .map_err(|error| self.connection_error(error))
    }

    fn get(&self, path: &str) -> Result<Response> {
        self.http
            .get(format!("{}{path}", self.api_url))
            .send()
            .map_err(|error| self.connection_error(error))
    }

    fn connection_error(&self, error: reqwest::Error) -> anyhow::Error {
        let error = anyhow::Error::new(error);
        // TLS failures also count as connection errors. A dev server only
        // helps when no process accepts the connection.
        let refused = error.chain().any(|cause| {
            cause
                .downcast_ref::<std::io::Error>()
                .is_some_and(|io| io.kind() == std::io::ErrorKind::ConnectionRefused)
        });
        if refused && is_local_url(&self.api_url) {
            // Reaching here means a URL was named: without one `mda chat`
            // starts its own server, so it never hits a dead local one.
            return categorized(
                ErrorCategory::Network,
                format!(
                    "Nothing is serving {}. Start it with `mda dev`, or run `mda chat` without \
                     `--url` to have it start one.",
                    self.api_url
                ),
            );
        }
        error.context("failed to reach the Agent Server")
    }

    /// Create the thread, or adopt `requested` when it already exists.
    pub(crate) fn ensure_thread(&self, requested: Option<&str>) -> Result<String> {
        let response = if let Some(id) = requested.map(str::trim) {
            uuid::Uuid::parse_str(id).map_err(|_| {
                categorized(
                    ErrorCategory::UserInput,
                    format!("--thread must be a UUID (got {id:?})"),
                )
            })?;
            self.get(&format!("/threads/{id}"))?
        } else {
            self.post("/threads", &json!({}))?
        };
        let value = read_json(response)?;
        match value.get("thread_id").and_then(Value::as_str) {
            Some(id) => Ok(uuid::Uuid::parse_str(id)
                .context("the Agent Server returned an invalid thread_id")?
                .to_string()),
            _ => bail!("the Agent Server did not return a thread_id"),
        }
    }

    /// Load pending decisions without advancing or changing the thread.
    pub(crate) fn thread_interrupts(&self, thread_id: &str) -> Result<Vec<PendingInterrupt>> {
        let state = read_json(self.get(&format!("/threads/{thread_id}/state"))?)?;
        Ok(state_interrupts(&state))
    }

    /// Snapshot message history before a run so final state is not mistaken
    /// for newly generated output.
    fn thread_values(&self, thread_id: &str) -> Result<Value> {
        let mut state = read_json(self.get(&format!("/threads/{thread_id}/state"))?)?;
        Ok(state
            .get_mut("values")
            .map(Value::take)
            .unwrap_or(Value::Null))
    }

    /// Snapshot history and open a typed run stream. Rendering and user decisions
    /// belong to the caller; wire formats and reply detection stay here.
    pub(crate) fn stream_run(
        &self,
        thread_id: &str,
        mut body: Value,
    ) -> Result<impl Iterator<Item = Result<RunEvent>>> {
        let previous = self.thread_values(thread_id)?;
        body["assistant_id"] = Value::String(self.assistant_id.clone());
        body["stream_mode"] = json!(["messages-tuple", "values"]);
        let response = self.post(&format!("/threads/{thread_id}/runs/stream"), &body)?;
        let status = response.status();
        if !status.is_success() {
            return Err(response_error(status, &response.text().unwrap_or_default()));
        }

        Ok(sse::SseReader::new(BufReader::new(response))
            .map(move |event| event.and_then(|event| events::decode_event(event, &previous))))
    }
}

fn is_local_url(value: &str) -> bool {
    let Ok(url) = reqwest::Url::parse(value) else {
        return false;
    };
    url.host_str().is_some_and(|host| {
        host.trim_end_matches('.').eq_ignore_ascii_case("localhost")
            || host
                .trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|address| address.is_loopback())
    })
}

/// Turn a failed Agent Server response into an error.
///
/// A rejected credential gets its own explanation, because `mda chat` never
/// sends one: the cause is almost always a URL naming a deployment instead of
/// a local dev server, and the bare status leaves the author to work that out.
/// A tunnel to a local server still authenticates, so the URL cannot be
/// screened up front — only its answer can.
fn response_error(status: StatusCode, text: &str) -> anyhow::Error {
    if matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
        return categorized(
            ErrorCategory::Authentication,
            format!(
                "The Agent Server rejected the request as unauthenticated ({status}). \
                 `mda chat` sends no credentials, so it reaches only a local `mda dev` \
                 server or a tunnel to one. Chat with a deployed agent in Studio."
            ),
        );
    }
    anyhow::Error::new(ApiError::from_response(status, text))
}

fn read_json(response: Response) -> Result<Value> {
    let status = response.status();
    let text = response
        .text()
        .context("failed to read the Agent Server response")?;
    if !status.is_success() {
        return Err(response_error(status, &text));
    }
    serde_json::from_str(&text).context("failed to decode the Agent Server response")
}

/// State snapshots carry interrupts on tasks; some versions also expose them
/// at the top level. Choose one representation so each interrupt is asked once.
fn state_interrupts(state: &Value) -> Vec<PendingInterrupt> {
    let top_level = parse_interrupts(state.get("interrupts"), None);
    if !top_level.is_empty() {
        return top_level;
    }
    let tasks: Vec<_> = state
        .get("tasks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|task| {
            parse_interrupts(
                task.get("interrupts"),
                task.get("id").and_then(Value::as_str),
            )
        })
        .collect();
    if !tasks.is_empty() {
        return tasks;
    }
    state
        .get("values")
        .map(pending_interrupts)
        .unwrap_or_default()
}

/// `__interrupt__` arrives as a flat list, or keyed by task id.
pub(crate) fn pending_interrupts(values: &Value) -> Vec<PendingInterrupt> {
    parse_interrupts(values.get("__interrupt__"), None)
}

fn parse_interrupts(raw: Option<&Value>, fallback_id: Option<&str>) -> Vec<PendingInterrupt> {
    let entries: Vec<_> = match raw {
        Some(Value::Array(list)) => list.iter().map(|entry| (fallback_id, entry)).collect(),
        Some(Value::Object(map)) => map
            .iter()
            .flat_map(|(key, value)| match value {
                Value::Array(list) => list
                    .iter()
                    .map(|entry| (Some(key.as_str()), entry))
                    .collect::<Vec<_>>(),
                other => vec![(Some(key.as_str()), other)],
            })
            .collect(),
        _ => Vec::new(),
    };
    entries
        .into_iter()
        .map(|(fallback_id, entry)| PendingInterrupt {
            id: entry
                .get("id")
                .and_then(Value::as_str)
                .filter(|id| !id.is_empty())
                .or(fallback_id)
                .map(str::to_string),
            value: entry.get("value").cloned().unwrap_or_else(|| entry.clone()),
            response_schema: entry
                .get("response_schema")
                .filter(|s| !s.is_null())
                .cloned(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_url_detection_does_not_match_remote_names() {
        for url in [
            "http://localhost:2024",
            "http://LOCALHOST.:2024",
            "http://127.0.0.1:2024",
            "http://127.0.0.2:2024",
            "http://[::1]:2024",
        ] {
            assert!(is_local_url(url), "{url}");
        }
        for url in [
            "http://localhost.example.com",
            "http://example.com/localhost",
            "http://192.168.1.1",
            "bad url",
        ] {
            assert!(!is_local_url(url), "{url}");
        }
    }

    #[test]
    fn local_tls_failure_does_not_suggest_starting_dev() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("https://{}", listener.local_addr().unwrap());
        let handle = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            let mut socket = loop {
                match listener.accept() {
                    Ok((socket, _)) => break socket,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(std::time::Instant::now() < deadline);
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("{error}"),
                }
            };
            socket.set_nonblocking(false).unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            socket
                .set_write_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut hello = [0; 4096];
            assert!(socket.read(&mut hello).unwrap() > 0);
            socket
                .write_all(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                .unwrap();
        });
        let server = AgentServer::new(url.clone(), "agent".into()).unwrap();
        let error = HttpClient::builder()
            .no_proxy()
            .build()
            .unwrap()
            .get(url)
            .send()
            .unwrap_err();
        assert!(error.is_connect());
        let error = server.connection_error(error);
        assert!(!format!("{error:#}").contains("mda dev"));
        assert!(error.downcast_ref::<reqwest::Error>().is_some());
        handle.join().unwrap();
    }

    #[test]
    fn local_connection_failure_explains_how_to_start_dev() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        drop(listener);
        let server = AgentServer::new(url.clone(), "agent".into()).unwrap();
        for requested in [None, Some("11111111-1111-4111-8111-111111111111")] {
            let error = server.ensure_thread(requested).unwrap_err();
            assert_eq!(crate::error::category_of(&error), ErrorCategory::Network);
            let message = format!("{error:#}");
            assert!(
                message.starts_with(&format!("Nothing is serving {url}.")),
                "{message}"
            );
            assert!(message.contains("`mda dev`"), "{message}");
            assert!(message.contains("--url"), "{message}");
        }
        let error = HttpClient::builder()
            .no_proxy()
            .build()
            .unwrap()
            .get(url)
            .send()
            .unwrap_err();
        let remote = AgentServer::new("https://example.com".into(), "agent".into()).unwrap();
        let error = remote.connection_error(error);
        assert!(!format!("{error:#}").contains("mda dev"));
        assert_eq!(crate::error::category_of(&error), ErrorCategory::Network);
    }

    /// A deployment answers, so this is not a connection failure — it is the
    /// one status that means "you sent no credentials", and the bare 401 left
    /// the author to work that out.
    #[test]
    fn an_unauthenticated_response_explains_that_chat_sends_no_credentials() {
        for status in [401, 403] {
            let error = response_error(
                StatusCode::from_u16(status).unwrap(),
                r#"{"detail": "Unauthorized"}"#,
            );
            assert_eq!(
                crate::error::category_of(&error),
                ErrorCategory::Authentication
            );
            let message = format!("{error:#}");
            assert!(message.contains("sends no credentials"), "{message}");
            assert!(message.contains("Studio"), "{message}");
        }
    }

    /// Every other status keeps the server's own explanation.
    #[test]
    fn other_failures_keep_the_server_detail() {
        let error = response_error(StatusCode::NOT_FOUND, r#"{"detail": "Thread not found"}"#);
        let message = format!("{error:#}");
        assert!(message.contains("Thread not found"), "{message}");
        assert!(!message.contains("sends no credentials"), "{message}");
    }

    #[test]
    fn pending_interrupts_accepts_list_and_task_keyed_shapes() {
        let flat = json!({"__interrupt__": [{"id": "i1", "value": {"q": 1}}]});
        assert_eq!(
            pending_interrupts(&flat),
            vec![PendingInterrupt {
                id: Some("i1".into()),
                value: json!({"q": 1}),
                response_schema: None,
            }]
        );
        let keyed = json!({"__interrupt__": {"task": [{"value": "ask"}]}});
        assert_eq!(
            pending_interrupts(&keyed),
            vec![PendingInterrupt {
                id: Some("task".into()),
                value: json!("ask"),
                response_schema: None,
            }]
        );
        assert!(pending_interrupts(&json!({"messages": []})).is_empty());
    }

    #[test]
    fn pending_interrupts_keep_the_response_schema() {
        let schema = json!({"oneOf": [{"properties": {"type": {"const": "approve"}}}]});
        let parsed = pending_interrupts(&json!({"__interrupt__": [
            {"id": "i1", "value": {"type": "tool_approval"}, "response_schema": schema},
            {"id": "i2", "value": "ask", "response_schema": null}
        ]}));
        assert_eq!(parsed[0].response_schema, Some(schema));
        assert_eq!(parsed[1].response_schema, None);
    }

    #[test]
    fn concurrent_interrupts_keep_keys_and_prefer_explicit_ids() {
        let parsed = pending_interrupts(&json!({"__interrupt__": {
            "task-a": [{"value": "first"}],
            "task-b": {"id": "interrupt-b", "value": "second"}
        }}));
        assert_eq!(
            parsed,
            vec![
                PendingInterrupt {
                    id: Some("task-a".into()),
                    value: json!("first"),
                    response_schema: None,
                },
                PendingInterrupt {
                    id: Some("interrupt-b".into()),
                    value: json!("second"),
                    response_schema: None,
                },
            ]
        );
        assert_eq!(
            state_interrupts(
                &json!({"tasks": [{"id": "task-a", "interrupts": [{"value": "first"}]}]})
            ),
            vec![parsed[0].clone()]
        );
    }

    #[test]
    fn state_recovers_interrupts_from_tasks_top_level_or_values_without_duplicates() {
        let interrupt = json!({"id": "i1", "value": {"question": "Proceed?"}});
        let expected = vec![PendingInterrupt {
            id: Some("i1".into()),
            value: json!({"question": "Proceed?"}),
            response_schema: None,
        }];
        for state in [
            json!({"tasks": [{"interrupts": [interrupt]}]}),
            json!({"interrupts": [interrupt]}),
            json!({"values": {"__interrupt__": {"task": [interrupt]}}}),
            json!({"interrupts": [interrupt], "tasks": [{"interrupts": [interrupt]}]}),
        ] {
            assert_eq!(state_interrupts(&state), expected);
        }
        assert!(state_interrupts(&json!({"values": null, "tasks": []})).is_empty());
    }
}
