//! Agent Platform endpoints used by deploy and dev.

use anyhow::{Context, Result};
use reqwest::Method;
use serde_json::{json, Value};

use super::Client;

impl Client {
    /// Find the Agent resource tag of the MDA agent bound to `deployment_id`.
    ///
    /// Returns `None` when the server has no Agent API, no agent is bound, or
    /// the agent has no tag. Looks up by deployment, not name, because the
    /// host backend can derive or suffix the agent ID.
    pub(crate) fn deployment_agent_tag_value_id(
        &self,
        deployment_id: &str,
    ) -> Result<Option<String>> {
        let Some(page) = self.request_optional(
            self.platform_base(),
            Method::GET,
            "/api/v1/agents",
            &[
                ("runtime_binding_source_id", deployment_id),
                ("source_type", "MDA"),
            ],
            None,
        )?
        else {
            return Ok(None);
        };
        let Some(tag) = page
            .get("items")
            .and_then(Value::as_array)
            .context("Agent lookup response has no items array")?
            .first()
            .and_then(|agent| agent.get("tag_value_id"))
            .filter(|value| !value.is_null())
        else {
            return Ok(None);
        };
        let tag = tag
            .as_str()
            .filter(|tag| !tag.is_empty())
            .context("Agent lookup response has an invalid tag_value_id")?;
        Ok(Some(tag.to_string()))
    }

    /// Tracing project of `agent_id`'s LOCAL environment, creating the agent
    /// or the environment when missing.
    pub(crate) fn local_environment_project(&self, agent_id: &str) -> Result<String> {
        let base = self.platform_base();
        let path = format!("/api/v1/agents/{agent_id}/environments");
        let local = json!({ "environment": "LOCAL" });
        let environment =
            match self.request_optional(base, Method::POST, &path, &[], Some(local.clone()))? {
                Some(environment) => environment,
                None => {
                    let agent = json!({ "id": agent_id, "source_type": "MDA" });
                    self.request(base, Method::POST, "/api/v1/agents", &[], Some(agent))?;
                    self.request(base, Method::POST, &path, &[], Some(local))?
                }
            };
        environment
            .get("tracer_session_name")
            .and_then(Value::as_str)
            .map(str::to_string)
            .context("Agent environment response has no tracer_session_name")
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread::{self, JoinHandle};

    use serde_json::{json, Value};

    use crate::langsmith::Client;

    fn serve(status: &'static str, body: Value) -> (Client, JoinHandle<String>) {
        let (client, requests) = serve_all(vec![(status, body)]);
        (
            client,
            thread::spawn(move || requests.join().unwrap().remove(0)),
        )
    }

    fn serve_all(responses: Vec<(&'static str, Value)>) -> (Client, JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            responses
                .into_iter()
                .map(|(status, body)| {
                    let (mut stream, _) = listener.accept().unwrap();
                    let mut request = [0; 4096];
                    let n = stream.read(&mut request).unwrap();
                    let body = body.to_string();
                    write!(stream, "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
                    String::from_utf8_lossy(&request[..n]).into_owned()
                })
                .collect()
        });
        (Client::for_test(&url), handle)
    }

    #[test]
    fn local_environment_project_creates_a_missing_agent() {
        let local = json!({ "environment": "LOCAL", "tracer_session_name": "research-local" });
        let (client, requests) = serve_all(vec![
            ("404 Not Found", json!({ "detail": "not found" })),
            ("201 Created", json!({ "id": "research" })),
            ("200 OK", local),
        ]);

        assert_eq!(
            client.local_environment_project("research").unwrap(),
            "research-local"
        );
        let requests = requests.join().unwrap();
        assert!(requests[0].starts_with("POST /api/v1/agents/research/environments "));
        assert!(requests[1].starts_with("POST /api/v1/agents "));
        assert!(requests[1].contains(r#""source_type":"MDA""#));
    }

    #[test]
    fn deployment_agent_tag_uses_the_registered_agent_not_the_deployment_name() {
        let (client, request) = serve(
            "200 OK",
            json!({"items": [{"id": "actual-agent", "tag_value_id": "agent-tag"}], "next_cursor": null}),
        );
        assert_eq!(
            client
                .deployment_agent_tag_value_id("deployment-id")
                .unwrap()
                .as_deref(),
            Some("agent-tag")
        );
        let lookup = request.join().unwrap();
        assert!(lookup.starts_with("GET /api/v1/agents?"));
        assert!(lookup.contains("runtime_binding_source_id=deployment-id"));
        assert!(lookup.contains("source_type=MDA"));
        assert!(lookup.to_lowercase().contains("x-tenant-id: tenant"));
    }

    #[test]
    fn deployment_agent_tag_skips_workspaces_without_the_rollout() {
        for (status, body) in [
            ("200 OK", json!({"items": [], "next_cursor": null})),
            (
                "200 OK",
                json!({"items": [{"id": "actual-agent", "tag_value_id": null}], "next_cursor": null}),
            ),
            (
                "200 OK",
                json!({"items": [{"id": "actual-agent"}], "next_cursor": null}),
            ),
            ("404 Not Found", json!({"detail": "not found"})),
        ] {
            let (client, request) = serve(status, body);
            assert_eq!(
                client
                    .deployment_agent_tag_value_id("deployment-id")
                    .unwrap(),
                None
            );
            request.join().unwrap();
        }
    }

    #[test]
    fn deployment_agent_tag_reports_lookup_errors() {
        for (status, body) in [
            ("403 Forbidden", json!({"detail": "lookup failed"})),
            (
                "500 Internal Server Error",
                json!({"detail": "lookup failed"}),
            ),
        ] {
            let (client, request) = serve(status, body);
            assert!(client
                .deployment_agent_tag_value_id("deployment-id")
                .is_err());
            request.join().unwrap();
        }
    }
}
