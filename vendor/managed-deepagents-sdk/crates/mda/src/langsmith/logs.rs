//! Agent Server (deploy) logs for a deployment.
//!
//! These live on the host backend's `v1` project routes, which are registered
//! with `include_in_schema=False` and therefore absent from the published
//! control plane OpenAPI document. The LangSmith deployment UI reads its
//! "Server Logs" tab through the same endpoint with the same `X-Api-Key` /
//! `X-Tenant-Id` auth, so treat the shape below as an internal contract that
//! may change without a version bump.
//!
//! A `v1` project id is the same identifier as a `v2` deployment id.

use std::time::Duration;

use anyhow::{Context, Result};
use clap::ValueEnum;
use reqwest::Method;
use serde::Deserialize;
use serde_json::{json, Value};

use super::Client;

/// Log queries scan a time range in the logging backend and can outlast the
/// default per-request timeout when a large `limit` is requested.
const LOGS_REQUEST_TIMEOUT: Duration = Duration::from_secs(60);

/// Chronological direction of a log query.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum LogOrder {
    /// Newest entries first.
    #[default]
    Newest,
    /// Oldest entries first.
    Oldest,
}

impl LogOrder {
    fn as_str(self) -> &'static str {
        match self {
            Self::Newest => "desc",
            Self::Oldest => "asc",
        }
    }
}

/// Minimum severity accepted by `mda logs --level`. The backend filters to this
/// level and everything above it.
#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Error,
    Critical,
}

impl LogLevel {
    /// Wire value sent to the host backend.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Debug => "DEBUG",
            Self::Info => "INFO",
            Self::Warning => "WARNING",
            Self::Error => "ERROR",
            Self::Critical => "CRITICAL",
        }
    }
}

/// Filters for a single page of deployment logs.
#[derive(Debug, Default)]
pub struct LogQuery<'a> {
    /// Maximum number of entries to return.
    pub limit: u32,
    pub order: LogOrder,
    /// Inclusive lower bound, as an RFC 3339 timestamp. Defaults to the
    /// deployment's creation time when omitted.
    pub start_time: Option<&'a str>,
    pub level: Option<LogLevel>,
}

/// Timestamp of a log entry.
///
/// The hosted logging backend reports epoch milliseconds; the self-hosted
/// Kubernetes reader passes the container's ISO 8601 timestamp through
/// verbatim.
#[derive(Clone, Debug, Deserialize)]
#[serde(untagged)]
pub enum LogTimestamp {
    EpochMillis(i64),
    Text(String),
}

impl Default for LogTimestamp {
    fn default() -> Self {
        Self::Text(String::new())
    }
}

/// A single Agent Server log line.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct LogEntry {
    /// Backend-assigned identifier. Empty when the source cannot supply one.
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub timestamp: LogTimestamp,
    #[serde(default)]
    pub level: String,
    #[serde(default)]
    pub message: String,
}

/// One page of deployment logs.
#[derive(Debug, Default, Deserialize)]
pub struct LogPage {
    #[serde(default)]
    pub logs: Vec<LogEntry>,
}

impl Client {
    /// Fetch Agent Server logs for a deployment, across all of its revisions.
    pub fn deployment_logs(&self, deployment_id: &str, query: &LogQuery<'_>) -> Result<LogPage> {
        let mut payload = json!({
            "order": query.order.as_str(),
            "limit": query.limit,
        });
        if let Some(start_time) = query.start_time {
            payload["start_time"] = Value::String(start_time.to_string());
        }
        if let Some(level) = query.level {
            payload["level"] = Value::String(level.as_str().to_string());
        }

        let response = self.request_with_timeout(
            &self.host_backend_url,
            Method::POST,
            &format!("/v1/projects/{deployment_id}/deploy_logs"),
            &[],
            Some(payload),
            LOGS_REQUEST_TIMEOUT,
        )?;
        serde_json::from_value(response).context("failed to decode the deployment logs response")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    fn serve_json_once(response_body: &str) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        let response_body = response_body.to_string();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0_u8; 4096];
            let read = stream.read(&mut buf).unwrap();
            tx.send(String::from_utf8_lossy(&buf[..read]).to_string())
                .unwrap();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{response_body}",
                response_body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        (format!("http://{addr}"), rx)
    }

    fn client_for_host(host_backend_url: String) -> Client {
        Client {
            host_backend_url,
            ..Client::for_test("http://unused.invalid")
        }
    }

    #[test]
    fn deployment_logs_posts_filters_to_the_v1_project_route() {
        let (url, rx) = serve_json_once(r#"{"logs":[],"next_offset":null}"#);
        let client = client_for_host(url);

        client
            .deployment_logs(
                "deployment-1",
                &LogQuery {
                    limit: 250,
                    order: LogOrder::Newest,
                    start_time: Some("2026-07-26T00:00:00.000Z"),
                    level: Some(LogLevel::Warning),
                },
            )
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(request.starts_with("POST /v1/projects/deployment-1/deploy_logs"));
        let body: Value = serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["order"], "desc");
        assert_eq!(body["limit"], 250);
        assert_eq!(body["start_time"], "2026-07-26T00:00:00.000Z");
        assert_eq!(body["level"], "WARNING");
    }

    #[test]
    fn deployment_logs_omits_unset_filters() {
        let (url, rx) = serve_json_once(r#"{"logs":[]}"#);
        let client = client_for_host(url);

        client
            .deployment_logs(
                "deployment-1",
                &LogQuery {
                    limit: 10,
                    order: LogOrder::Oldest,
                    ..LogQuery::default()
                },
            )
            .unwrap();

        let request = rx.recv().unwrap();
        let body: Value = serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["order"], "asc");
        assert!(body.get("start_time").is_none());
        assert!(body.get("level").is_none());
    }

    #[test]
    fn log_entries_decode_both_timestamp_shapes() {
        let page: LogPage = serde_json::from_str(
            r#"{"logs":[
                {"id":"a","timestamp":1769472000000,"level":"INFO","message":"hosted"},
                {"id":"","timestamp":"2026-07-26T00:00:00.123456Z","level":"ERROR","message":"self-hosted"}
            ],"next_offset":"cursor"}"#,
        )
        .unwrap();

        assert!(matches!(
            page.logs[0].timestamp,
            LogTimestamp::EpochMillis(1769472000000)
        ));
        assert!(
            matches!(&page.logs[1].timestamp, LogTimestamp::Text(text) if text.starts_with("2026-07-26"))
        );
    }
}
