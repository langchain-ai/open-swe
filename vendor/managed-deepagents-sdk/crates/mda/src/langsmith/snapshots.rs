//! LangSmith platform API endpoints for sandbox snapshots.
//!
//! Used by deploy bake (`capture` / `get` / wait) and `mda delete` (list +
//! delete recipe snapshots named `{deployment}--setup-{digest}`).

use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use reqwest::{Method, StatusCode};
use serde::Deserialize;
use serde_json::json;

use super::{ApiError, Client};

/// Page size for snapshot listing.
const PAGE_SIZE: usize = 100;

/// Default bound for waiting until a captured snapshot is `ready`.
pub const DEFAULT_SNAPSHOT_READY_TIMEOUT: Duration = Duration::from_secs(60);

/// Poll interval while waiting for snapshot readiness.
const READY_POLL_INTERVAL: Duration = Duration::from_secs(2);

/// Result of deleting a sandbox snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotDelete {
    /// The snapshot was removed.
    Deleted,
    /// The snapshot was already gone (HTTP 404).
    Missing,
    /// LangSmith refused because one or more sandboxes still clone it (HTTP 409).
    InUse,
}

/// A snapshot as returned by list/get endpoints.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct SnapshotSummary {
    /// Snapshot UUID.
    pub id: String,
    /// Human-readable name (recipe snapshots use `{deployment}--setup-{digest}`).
    #[serde(default)]
    pub name: Option<String>,
    /// `building`, `ready`, `failed`, …
    #[serde(default)]
    pub status: Option<String>,
    /// Tags Host assigned. A snapshot built from a Docker image inherits the
    /// image's tag rather than `latest`, which decides how the runtime must
    /// reference the recipe. See `deploy::helpers::recipe_snapshot_reference`.
    #[serde(default)]
    pub tags: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct SnapshotListPage {
    #[serde(default)]
    snapshots: Vec<SnapshotSummary>,
}

impl Client {
    /// List snapshots whose name contains `name_contains`, then keep prefix matches.
    pub fn list_snapshots_with_prefix(&self, prefix: &str) -> Result<Vec<SnapshotSummary>> {
        let base = self.sandboxes_base()?;
        let mut found = Vec::new();
        let mut offset = 0usize;
        loop {
            let offset_param = offset.to_string();
            let limit_param = PAGE_SIZE.to_string();
            let value = self.request(
                &base,
                Method::GET,
                "/snapshots",
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
            let page: SnapshotListPage = serde_json::from_value(value)
                .context("failed to decode the snapshot list response")?;
            let page_len = page.snapshots.len();
            found.extend(page.snapshots.into_iter().filter(|snapshot| {
                snapshot
                    .name
                    .as_deref()
                    .is_some_and(|n| n.starts_with(prefix))
            }));
            if page_len < PAGE_SIZE {
                break;
            }
            offset += page_len;
        }
        Ok(found)
    }

    /// Fetch a snapshot by id.
    pub fn get_snapshot(&self, snapshot_id: &str) -> Result<SnapshotSummary> {
        let base = self.sandboxes_base()?;
        let path = format!("/snapshots/{snapshot_id}");
        let value = self.request(&base, Method::GET, &path, &[], None)?;
        serde_json::from_value(value).context("failed to decode the snapshot response")
    }

    /// Look up a snapshot by exact name, if one exists and is ready.
    pub fn find_ready_snapshot_by_name(&self, name: &str) -> Result<Option<SnapshotSummary>> {
        let matches = self.list_snapshots_with_prefix(name)?;
        Ok(matches.into_iter().find(|snapshot| {
            snapshot.name.as_deref() == Some(name) && snapshot.status.as_deref() == Some("ready")
        }))
    }

    /// Delete a snapshot by id. Idempotent on 404. A 409 is
    /// [`SnapshotDelete::InUse`], not an error: live boxes keep the recipe they
    /// were created from until idle TTL or `mda delete`.
    pub fn delete_snapshot(&self, snapshot_id: &str) -> Result<SnapshotDelete> {
        let base = self.sandboxes_base()?;
        let path = format!("/snapshots/{snapshot_id}");
        match self.request_optional(&base, Method::DELETE, &path, &[], None) {
            Ok(None) => Ok(SnapshotDelete::Missing),
            Ok(Some(_)) => Ok(SnapshotDelete::Deleted),
            Err(err) if snapshot_in_use(&err) => Ok(SnapshotDelete::InUse),
            Err(err) => Err(err),
        }
    }

    /// Capture a snapshot from a running box and wait until it is ready.
    pub fn capture_snapshot(
        &self,
        box_name: &str,
        snapshot_name: &str,
        timeout: Duration,
    ) -> Result<SnapshotSummary> {
        let base = self.sandboxes_base()?;
        let path = format!("/boxes/{}/snapshot", urlencoding_path(box_name));
        let value = self.request(
            &base,
            Method::POST,
            &path,
            &[],
            Some(json!({ "name": snapshot_name })),
        )?;
        let started: SnapshotSummary =
            serde_json::from_value(value).context("failed to decode capture snapshot response")?;
        self.wait_for_snapshot(&started.id, timeout)
    }

    /// Build a snapshot from a Docker image and wait until ready.
    pub fn create_snapshot_from_docker_image(
        &self,
        name: &str,
        docker_image: &str,
        fs_capacity_bytes: u64,
        timeout: Duration,
        registry_id: Option<&str>,
    ) -> Result<SnapshotSummary> {
        let base = self.sandboxes_base()?;
        let mut body = json!({
            "name": name,
            "docker_image": docker_image,
            "fs_capacity_bytes": fs_capacity_bytes,
        });
        if let Some(registry_id) = registry_id {
            body.as_object_mut()
                .expect("snapshot create body is an object")
                .insert("registry_id".into(), json!(registry_id));
        }
        let value = self.request(&base, Method::POST, "/snapshots", &[], Some(body))?;
        let started: SnapshotSummary =
            serde_json::from_value(value).context("failed to decode create snapshot response")?;
        self.wait_for_snapshot(&started.id, timeout)
    }

    /// Poll until the snapshot is `ready` or `failed`, or `timeout` elapses.
    pub fn wait_for_snapshot(
        &self,
        snapshot_id: &str,
        timeout: Duration,
    ) -> Result<SnapshotSummary> {
        let deadline = Instant::now() + timeout;
        loop {
            let snapshot = self.get_snapshot(snapshot_id)?;
            match snapshot.status.as_deref() {
                Some("ready") => return Ok(snapshot),
                Some("failed") => {
                    bail!("snapshot {snapshot_id} failed to become ready");
                }
                _ if Instant::now() >= deadline => {
                    bail!(
                        "snapshot {snapshot_id} did not become ready within {}s",
                        timeout.as_secs()
                    );
                }
                _ => crate::interrupt::pause(READY_POLL_INTERVAL)?,
            }
        }
    }

    /// Platform sandboxes API root (`…/v2/sandboxes`).
    pub(crate) fn sandboxes_base(&self) -> Result<String> {
        let platform = self.platform_base();
        Ok(format!("{platform}/v2/sandboxes"))
    }
}

fn urlencoding_path(value: &str) -> String {
    // Box/snapshot names are deployment-safe ([A-Za-z0-9._-]); encode anyway.
    value
        .bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn snapshot_in_use(err: &anyhow::Error) -> bool {
    err.chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())
        .is_some_and(|api| api.status == StatusCode::CONFLICT)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    fn serve(responses: Vec<(u16, String)>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for (status, body) in responses {
                let (mut stream, _) = listener.accept().unwrap();
                tx.send(read_request(&mut stream)).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
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
            if text.contains("\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    #[test]
    fn lists_snapshots_with_true_prefix() {
        let body = json!({
            "snapshots": [
                { "id": "1", "name": "agent--setup-aaa", "status": "ready" },
                { "id": "2", "name": "other--setup-bbb", "status": "ready" },
                { "id": "3", "name": "agent--setup-ccc", "status": "building" }
            ]
        })
        .to_string();
        let (url, rx) = serve(vec![(200, body)]);
        let found = Client::for_test(&url)
            .list_snapshots_with_prefix("agent--setup-")
            .unwrap();
        assert_eq!(found.len(), 2);
        assert!(rx.recv().unwrap().contains("/v2/sandboxes/snapshots?"));
    }

    #[test]
    fn capture_snapshot_posts_and_waits() {
        let capture = json!({
            "id": "snap-1",
            "name": "agent--setup-abc",
            "status": "building"
        })
        .to_string();
        let ready = json!({
            "id": "snap-1",
            "name": "agent--setup-abc",
            "status": "ready"
        })
        .to_string();
        let (url, rx) = serve(vec![(200, capture), (200, ready)]);
        let snapshot = Client::for_test(&url)
            .capture_snapshot("builder", "agent--setup-abc", Duration::from_secs(10))
            .unwrap();
        assert_eq!(snapshot.status.as_deref(), Some("ready"));
        let first = rx.recv().unwrap();
        assert!(first.contains("POST /v2/sandboxes/boxes/builder/snapshot"));
        assert!(first.contains(r#""name":"agent--setup-abc""#));
    }

    #[test]
    fn delete_snapshot_treats_conflict_as_in_use() {
        let body = json!({
            "error": "Conflict",
            "message": "snapshot is in use by one or more sandboxes; release them before deleting"
        })
        .to_string();
        let (url, rx) = serve(vec![(409, body)]);
        let outcome = Client::for_test(&url).delete_snapshot("stale-id").unwrap();
        assert_eq!(outcome, SnapshotDelete::InUse);
        assert!(rx
            .recv()
            .unwrap()
            .contains("DELETE /v2/sandboxes/snapshots/stale-id"));
    }

    #[test]
    fn delete_snapshot_treats_not_found_as_missing() {
        let (url, _rx) = serve(vec![(404, String::new())]);
        let outcome = Client::for_test(&url).delete_snapshot("gone").unwrap();
        assert_eq!(outcome, SnapshotDelete::Missing);
    }
}
