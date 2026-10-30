//! LangSmith platform API endpoints for managed sandboxes.
//!
//! Used by `mda delete` to reclaim the sandboxes a deployment's runtime created.
//! Sandboxes carry no deployment reference of their own, so attribution rides on
//! the name: the runtimes name every sandbox they create
//! `{deployment}--{digest}`, and teardown matches on that prefix.
//!
//! Calls go to the platform API (`api.smith.langchain.com`), not the host
//! backend.

use std::collections::BTreeMap;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::Method;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{ApiError, Client};

/// Page size for sandbox listing. The API defaults to 50 and reports no total,
/// so pagination runs until a short page comes back.
const PAGE_SIZE: usize = 100;

/// Status of a sandbox that is already being torn down by LangSmith.
const STATUS_DELETING: &str = "deleting";

/// A sandbox as returned by the list endpoint.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct SandboxSummary {
    /// Display name, which is also the delete key.
    pub name: String,
    /// `provisioning`, `ready`, `failed`, `stopped`, or `deleting`.
    #[serde(default)]
    pub status: Option<String>,
    /// Dataplane URL used for execute during bake.
    #[serde(default)]
    pub dataplane_url: Option<String>,
    /// Sandbox UUID.
    #[serde(default)]
    pub id: Option<String>,
}

/// Outcome of a batch delete: LangSmith reports per-name results rather than
/// failing the whole request.
#[derive(Debug, Default, Deserialize)]
pub struct SandboxBatchDelete {
    /// Names LangSmith accepted for deletion.
    #[serde(default)]
    pub deleted: Vec<String>,
    /// Names LangSmith declined, with its reason.
    #[serde(default)]
    pub skipped: Vec<SandboxSkipped>,
}

/// A sandbox the batch delete did not remove.
#[derive(Debug, Deserialize)]
pub struct SandboxSkipped {
    /// Name that was skipped.
    pub name: String,
    /// LangSmith's explanation, when it supplied one.
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize)]
struct SandboxListPage {
    #[serde(default)]
    sandboxes: Vec<SandboxSummary>,
}

impl Client {
    /// Verify that the current organization may use managed sandboxes.
    ///
    /// A sandbox declaration without a recipe does not otherwise touch the
    /// sandbox API during deploy, so an entitlement failure would only surface
    /// later when the agent first uses a filesystem or execution tool.
    pub fn ensure_sandbox_access(&self) -> Result<()> {
        let base = self.platform_base();
        self.request(
            base,
            Method::GET,
            "/v2/sandboxes/boxes",
            &[("limit", "1"), ("offset", "0")],
            None,
        )?;
        Ok(())
    }

    /// List sandboxes whose name starts with `prefix`, excluding ones already
    /// being deleted.
    ///
    /// The API filter is `name_contains` (a substring match), so results are
    /// narrowed server-side and then re-checked as a true prefix locally —
    /// otherwise a deployment would adopt sandboxes that merely mention its name.
    pub fn list_sandboxes_with_prefix(&self, prefix: &str) -> Result<Vec<SandboxSummary>> {
        let base = self.platform_base();

        let mut found = Vec::new();
        let mut offset = 0usize;
        loop {
            let offset_param = offset.to_string();
            let limit_param = PAGE_SIZE.to_string();
            let value = self.request(
                base,
                Method::GET,
                "/v2/sandboxes/boxes",
                &[
                    ("name_contains", prefix),
                    ("limit", limit_param.as_str()),
                    ("offset", offset_param.as_str()),
                ],
                None,
            )?;
            if value.is_null() {
                break;
            }
            let page: SandboxListPage = serde_json::from_value(value)
                .context("failed to decode the sandbox list response")?;
            let page_len = page.sandboxes.len();
            found.extend(
                page.sandboxes
                    .into_iter()
                    .filter(|sandbox| sandbox.name.starts_with(prefix))
                    .filter(|sandbox| sandbox.status.as_deref() != Some(STATUS_DELETING)),
            );
            if page_len < PAGE_SIZE {
                break;
            }
            offset += page_len;
        }
        Ok(found)
    }

    /// Delete sandboxes by name in one request.
    ///
    /// LangSmith reports per-name results instead of failing outright, so a
    /// sandbox that vanished between listing and deleting is reported as skipped
    /// rather than erroring the teardown.
    pub fn batch_delete_sandboxes(&self, names: &[String]) -> Result<SandboxBatchDelete> {
        if names.is_empty() {
            return Ok(SandboxBatchDelete::default());
        }
        let base = self.platform_base();

        let value = self.request(
            base,
            Method::POST,
            "/v2/sandboxes/boxes/batch-delete",
            &[],
            Some(json!({ "names": names })),
        )?;
        if value.is_null() {
            return Ok(SandboxBatchDelete::default());
        }
        decode_batch_delete(value)
    }

    /// Create a sandbox box (bake builder) and wait until ready.
    pub fn create_sandbox_box(
        &self,
        name: &str,
        options: CreateSandboxBoxOptions<'_>,
    ) -> Result<SandboxSummary> {
        let base = self.platform_base();

        let mut payload = json!({
            "name": name,
            "wait_for_ready": true,
            "timeout": options.timeout_secs,
            "idle_ttl_seconds": options.idle_ttl_seconds,
        });
        if let Some(snapshot_name) = options.snapshot_name {
            payload["snapshot_name"] = json!(snapshot_name);
        }
        if let Some(snapshot_id) = options.snapshot_id {
            payload["snapshot_id"] = json!(snapshot_id);
        }
        if let Some(env_vars) = options.env_vars {
            if !env_vars.is_empty() {
                payload["env_vars"] = json!(env_vars);
            }
        }

        let value = self.request_with_timeout(
            base,
            Method::POST,
            "/v2/sandboxes/boxes",
            &[],
            Some(payload),
            Duration::from_secs(options.timeout_secs.saturating_add(30)),
        )?;
        serde_json::from_value(value).context("failed to decode create sandbox response")
    }

    /// Delete one sandbox by name (best-effort; 404 is ok).
    pub fn delete_sandbox_box(&self, name: &str) -> Result<()> {
        let base = self.platform_base();
        let path = format!("/v2/sandboxes/boxes/{name}");
        let _ = self.request_optional(base, Method::DELETE, &path, &[], None)?;
        Ok(())
    }

    /// Run a command on a sandbox via the dataplane HTTP execute endpoint.
    pub fn execute_sandbox_command(
        &self,
        dataplane_url: &str,
        command: &str,
        timeout_secs: u64,
    ) -> Result<SandboxCommandResult> {
        let url = format!("{}/execute", dataplane_url.trim_end_matches('/'));
        let payload = json!({
            "command": command,
            "timeout": timeout_secs,
            "shell": "/bin/bash",
        });
        let http = crate::http::client_builder()
            .timeout(Duration::from_secs(timeout_secs.saturating_add(30)))
            .build()
            .context("failed to build execute HTTP client")?;
        let mut req = http
            .post(&url)
            .header("X-Api-Key", self.api_key())
            .header("Content-Type", "application/json")
            .header("Accept", "application/json")
            .json(&payload);
        if let Some(tenant) = self.tenant_id.as_deref() {
            req = req.header(super::TENANT_HEADER, tenant);
        }
        let response = req.send().context("sandbox execute request failed")?;
        let status = response.status();
        let text = response.text().context("failed to read execute response")?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        serde_json::from_str(&text).context("failed to decode execute response")
    }
}

/// Options for [`Client::create_sandbox_box`].
#[derive(Debug, Clone)]
pub struct CreateSandboxBoxOptions<'a> {
    pub snapshot_name: Option<&'a str>,
    pub snapshot_id: Option<&'a str>,
    pub env_vars: Option<&'a BTreeMap<String, String>>,
    pub timeout_secs: u64,
    pub idle_ttl_seconds: u64,
}

impl Default for CreateSandboxBoxOptions<'_> {
    fn default() -> Self {
        Self {
            snapshot_name: None,
            snapshot_id: None,
            env_vars: None,
            timeout_secs: 120,
            idle_ttl_seconds: 600,
        }
    }
}

/// Result of a blocking dataplane `/execute` call.
#[derive(Debug, Clone, Deserialize)]
pub struct SandboxCommandResult {
    #[serde(default)]
    pub stdout: String,
    #[serde(default)]
    pub stderr: String,
    #[serde(default = "missing_exit_code")]
    pub exit_code: i32,
}

fn missing_exit_code() -> i32 {
    -1
}

fn decode_batch_delete(value: Value) -> Result<SandboxBatchDelete> {
    serde_json::from_value(value).context("failed to decode the sandbox batch-delete response")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    /// Serve `responses` in order, capturing each request (head + body).
    fn serve(responses: Vec<String>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            for body in responses {
                let (mut stream, _) = listener.accept().unwrap();
                tx.send(read_request(&mut stream)).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });

        (base_url, rx)
    }

    fn serve_status(status: u16, body: String) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            tx.send(read_request(&mut stream)).unwrap();
            write!(
                stream,
                "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });

        (base_url, rx)
    }

    fn read_request(stream: &mut TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            let text = String::from_utf8_lossy(&data);
            let Some((headers, body)) = text.split_once("\r\n\r\n") else {
                continue;
            };
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .map(|value| value.trim().parse::<usize>().ok())
                })
                .flatten()
                .unwrap_or(0);
            if body.len() >= content_length {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    fn page(sandboxes: &[(&str, &str)]) -> String {
        let entries: Vec<Value> = sandboxes
            .iter()
            .map(|(name, status)| json!({ "name": name, "status": status }))
            .collect();
        json!({ "sandboxes": entries, "offset": 0 }).to_string()
    }

    #[test]
    fn lists_only_sandboxes_that_truly_carry_the_prefix() {
        let (url, rx) = serve(vec![page(&[
            ("my-agent--aaa", "ready"),
            ("my-agent--bbb", "stopped"),
            // Substring match that is not a prefix: a different deployment.
            ("staging-my-agent--ccc", "ready"),
            // A sibling deployment whose name merely starts the same way.
            ("my-agent-staging--ddd", "ready"),
        ])]);

        let found = Client::for_test(&url)
            .list_sandboxes_with_prefix("my-agent--")
            .unwrap();

        assert_eq!(
            found.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
            vec!["my-agent--aaa", "my-agent--bbb"]
        );
        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /v2/sandboxes/boxes?name_contains=my-agent--"),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn skips_sandboxes_langsmith_is_already_deleting() {
        let (url, _rx) = serve(vec![page(&[
            ("my-agent--aaa", "ready"),
            ("my-agent--bbb", "deleting"),
        ])]);

        let found = Client::for_test(&url)
            .list_sandboxes_with_prefix("my-agent--")
            .unwrap();

        assert_eq!(found.len(), 1);
        assert_eq!(found[0].name, "my-agent--aaa");
    }

    #[test]
    fn paginates_until_a_short_page() {
        let first: Vec<String> = (0..PAGE_SIZE)
            .map(|index| format!("my-agent--{index:04}"))
            .collect();
        let first_page: Vec<(&str, &str)> =
            first.iter().map(|name| (name.as_str(), "ready")).collect();
        let (url, rx) = serve(vec![
            page(&first_page),
            page(&[("my-agent--last", "ready")]),
        ]);

        let found = Client::for_test(&url)
            .list_sandboxes_with_prefix("my-agent--")
            .unwrap();

        assert_eq!(found.len(), PAGE_SIZE + 1);
        assert!(rx.recv().unwrap().contains("offset=0"));
        assert!(rx.recv().unwrap().contains(&format!("offset={PAGE_SIZE}")));
    }

    #[test]
    fn batch_delete_posts_every_name_in_one_request() {
        let (url, rx) = serve(vec![
            json!({ "deleted": ["my-agent--aaa", "my-agent--bbb"], "skipped": [] }).to_string(),
        ]);

        let outcome = Client::for_test(&url)
            .batch_delete_sandboxes(&["my-agent--aaa".to_string(), "my-agent--bbb".to_string()])
            .unwrap();

        assert_eq!(outcome.deleted.len(), 2);
        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("POST /v2/sandboxes/boxes/batch-delete"),
            "unexpected request line: {request}"
        );
        assert!(
            request.contains(r#"{"names":["my-agent--aaa","my-agent--bbb"]}"#),
            "unexpected request body: {request}"
        );
    }

    #[test]
    fn batch_delete_of_nothing_makes_no_request() {
        // No mock server: a request here would fail to connect.
        let client = Client::for_test("http://unused.invalid");
        assert!(client
            .batch_delete_sandboxes(&[])
            .unwrap()
            .deleted
            .is_empty());
    }

    #[test]
    fn sandbox_access_probe_makes_a_bounded_list_request() {
        let (url, rx) = serve(vec![page(&[])]);

        Client::for_test(&url).ensure_sandbox_access().unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /v2/sandboxes/boxes?limit=1&offset=0"),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn sandbox_access_probe_preserves_entitlement_error() {
        let body = json!({
            "detail": {
                "error": "FeatureDisabled",
                "message": "Sandbox feature is not enabled for this organization"
            }
        })
        .to_string();
        let (url, _rx) = serve_status(403, body);

        let error = Client::for_test(&url).ensure_sandbox_access().unwrap_err();

        assert_eq!(
            error.to_string(),
            "Sandbox feature is not enabled for this organization (HTTP 403)"
        );
    }

    #[test]
    fn decodes_batch_delete_results() {
        let outcome = decode_batch_delete(json!({
            "deleted": ["agent--abc"],
            "skipped": [{ "name": "agent--def", "reason": "not found" }]
        }))
        .unwrap();

        assert_eq!(outcome.deleted, vec!["agent--abc".to_string()]);
        assert_eq!(outcome.skipped.len(), 1);
        assert_eq!(outcome.skipped[0].name, "agent--def");
        assert_eq!(outcome.skipped[0].reason.as_deref(), Some("not found"));
    }

    #[test]
    fn decodes_batch_delete_without_skipped() {
        let outcome = decode_batch_delete(json!({ "deleted": [] })).unwrap();
        assert!(outcome.deleted.is_empty());
        assert!(outcome.skipped.is_empty());
    }
}
