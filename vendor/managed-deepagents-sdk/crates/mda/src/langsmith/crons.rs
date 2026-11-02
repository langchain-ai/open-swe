//! Agent Server cron endpoints.
//!
//! These calls go to the deployed Agent Server URL, not the host backend. They
//! authenticate as the workspace (LangSmith API key + optional tenant header),
//! and declare the LangSmith studio auth scheme via
//! [`Client::request_agent_server`] so a managed-identity deployment's custom
//! `authenticate` handler (which requires an end-user bearer token) does not
//! reject these control-plane calls.

use anyhow::{anyhow, Result};
use reqwest::Method;
use serde_json::{json, Value};

use super::Client;

/// Page size for cron search; a shorter page means the last page was reached.
const CRON_SEARCH_PAGE_SIZE: usize = 100;

impl Client {
    /// Search crons owned by Managed Deep Agents on a deployed Agent Server.
    pub fn search_managed_crons(&self, deployment_url: &str) -> Result<Value> {
        let mut all = Vec::new();
        loop {
            let response = self.request_agent_server(
                deployment_url.trim_end_matches('/'),
                Method::POST,
                "/runs/crons/search",
                &[],
                Some(managed_crons_search_payload(all.len())),
            )?;
            let page = response
                .as_array()
                .or_else(|| response.get("items").and_then(Value::as_array))
                .ok_or_else(|| anyhow!("unexpected cron search response shape"))?;
            let complete = page.len() < CRON_SEARCH_PAGE_SIZE;
            all.extend(page.iter().cloned());
            if complete {
                return Ok(Value::Array(all));
            }
        }
    }

    /// Create a stateless cron, which runs on a new thread each time.
    pub fn create_cron(&self, deployment_url: &str, payload: Value) -> Result<Value> {
        self.request_agent_server(
            deployment_url.trim_end_matches('/'),
            Method::POST,
            "/runs/crons",
            &[],
            Some(payload),
        )
    }

    /// Create a cron bound to an existing/persistent thread.
    pub fn create_thread_cron(
        &self,
        deployment_url: &str,
        thread_id: &str,
        payload: Value,
    ) -> Result<Value> {
        self.request_agent_server(
            deployment_url.trim_end_matches('/'),
            Method::POST,
            &format!("/threads/{thread_id}/runs/crons"),
            &[],
            Some(payload),
        )
    }

    /// Delete a cron by ID.
    pub fn delete_cron(&self, deployment_url: &str, cron_id: &str) -> Result<Value> {
        self.request_agent_server(
            deployment_url.trim_end_matches('/'),
            Method::DELETE,
            &format!("/runs/crons/{cron_id}"),
            &[],
            None,
        )
    }
}

fn managed_crons_search_payload(offset: usize) -> Value {
    json!({
        "limit": CRON_SEARCH_PAGE_SIZE,
        "offset": offset,
        "sort_by": "created_at",
        "sort_order": "asc",
    })
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;
    use std::time::Duration;

    use super::*;

    #[test]
    fn managed_crons_search_payload_does_not_filter_by_assistant_id() {
        let payload = managed_crons_search_payload(100);

        assert_eq!(payload["offset"], 100);
        assert!(payload.get("assistant_id").is_none());
    }

    #[test]
    fn search_collects_all_pages_before_returning_records() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = thread::spawn(move || {
            for (offset, count) in [(0, 100), (100, 1)] {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut data = Vec::new();
                let request = loop {
                    let mut buffer = [0; 4096];
                    let read = stream.read(&mut buffer).unwrap();
                    assert!(read > 0);
                    data.extend_from_slice(&buffer[..read]);
                    let text = String::from_utf8_lossy(&data);
                    if let Some((headers, body)) = text.split_once("\r\n\r\n") {
                        let length: usize = headers
                            .lines()
                            .find_map(|line| {
                                let (name, value) = line.split_once(':')?;
                                name.eq_ignore_ascii_case("content-length")
                                    .then(|| value.trim().parse().unwrap())
                            })
                            .unwrap();
                        if body.len() == length {
                            break serde_json::from_str::<Value>(body).unwrap();
                        }
                    }
                };
                assert_eq!(request["offset"], offset);
                let body = serde_json::to_string(&(offset..offset + count).map(|id| {
                    json!({"cron_id": id.to_string(), "metadata": {"mda_managed": true}})
                }).collect::<Vec<_>>()).unwrap();
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            }
        });
        let client = Client::for_test("http://unused.invalid");
        let result = client.search_managed_crons(&url).unwrap();
        server.join().unwrap();
        assert_eq!(result.as_array().unwrap().len(), 101);
        assert_eq!(result[100]["cron_id"], "100");
    }
}
