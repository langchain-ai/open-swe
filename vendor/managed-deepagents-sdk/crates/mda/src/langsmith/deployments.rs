//! Host-backend (LangGraph Deployment) endpoints.
//!
//! Only the subset needed by the remote-source deploy flow is implemented:
//! list/create deployments, request an upload URL, trigger an internal-source
//! build, and poll revisions. All calls go to the host backend
//! (`api.host.langchain.com`).

use anyhow::{bail, Context, Result};
use reqwest::header::{HeaderMap, HeaderValue};
use reqwest::{redirect::Policy, Method};
use serde_json::{json, Value};

use super::{
    decode_json_body, send_json_request, ApiError, Client, DeploymentAgent, DeploymentType, Secret,
    CREATE_DEPLOYMENT_TIMEOUT, REQUEST_TIMEOUT,
};

impl Client {
    /// Fetch a deployment object from the host backend.
    pub fn get_deployment(&self, deployment_id: &str) -> Result<Value> {
        self.request(
            &self.host_backend_url,
            Method::GET,
            &format!("/v2/deployments/{deployment_id}"),
            &[],
            None,
        )
    }

    /// List deployments, optionally filtering by a name substring.
    ///
    /// `name_contains` is the only filter the host backend is known to support,
    /// and the endpoint paginates (20 per page by default). Callers must still
    /// pick the exact match out of the page with
    /// [`find_deployment_by_name`](crate::deploy::helpers::find_deployment_by_name)
    /// rather than assuming a single resource comes back.
    pub fn list_deployments(&self, name_contains: &str) -> Result<Value> {
        self.request(
            &self.host_backend_url,
            Method::GET,
            "/v2/deployments",
            &[("name_contains", name_contains)],
            None,
        )
    }

    pub fn list_agent_deployments(&self, agent: &DeploymentAgent) -> Result<Value> {
        self.request(
            &self.host_backend_url,
            Method::GET,
            "/v2/deployments",
            &[
                ("agent_id", agent.agent_id.as_str()),
                ("agent_environment", agent.environment.as_str()),
            ],
            None,
        )
    }

    /// Create a new internal-source deployment, returning the created object.
    ///
    /// MDA always creates deployments as `internal_source`; the tarball +
    /// `langgraph.json` path are attached later via
    /// [`Self::update_deployment_internal_source`].
    pub fn create_deployment(
        &self,
        name: &str,
        deployment_type: DeploymentType,
        secrets: &[Secret],
    ) -> Result<Value> {
        self.post_deployment("name", json!(name), deployment_type, secrets)
    }

    pub fn create_agent_deployment(
        &self,
        agent: &DeploymentAgent,
        deployment_type: DeploymentType,
        secrets: &[Secret],
    ) -> Result<Value> {
        self.post_deployment("agent", json!(agent), deployment_type, secrets)
    }

    fn post_deployment(
        &self,
        identity_key: &str,
        identity: Value,
        deployment_type: DeploymentType,
        secrets: &[Secret],
    ) -> Result<Value> {
        let mut payload = json!({
            "source": "internal_source",
            // MDA deployments carry the caller-selected `deployment_type` plus
            // the request-only `managed_deep_agent` flag; the Host Backend gates
            // the flag on the org's Managed Deep Agents entitlement and persists
            // the marker used for billing and the UI tag.
            "source_config": { "deployment_type": deployment_type.as_str() },
            "source_revision_config": {},
            "managed_deep_agent": true,
        });
        payload[identity_key] = identity;
        // The host backend requires `secrets` to be present (an empty list is
        // valid), so always include it.
        payload["secrets"] = serde_json::to_value(secrets)?;
        self.request_with_timeout(
            &self.host_backend_url,
            Method::POST,
            "/v2/deployments",
            &[],
            Some(payload),
            CREATE_DEPLOYMENT_TIMEOUT,
        )
    }

    /// Create a deployment from an image in a customer-managed registry.
    pub fn create_external_deployment(
        &self,
        name: &str,
        image_uri: &str,
        secrets: &[Secret],
    ) -> Result<Value> {
        self.post_external_deployment("name", json!(name), image_uri, secrets)
    }

    /// Create an agent deployment from an image in a customer-managed registry.
    pub fn create_external_agent_deployment(
        &self,
        agent: &DeploymentAgent,
        image_uri: &str,
        secrets: &[Secret],
    ) -> Result<Value> {
        self.post_external_deployment("agent", json!(agent), image_uri, secrets)
    }

    fn post_external_deployment(
        &self,
        identity_key: &str,
        identity: Value,
        image_uri: &str,
        secrets: &[Secret],
    ) -> Result<Value> {
        let mut payload = json!({
            "source": "external_docker",
            "source_config": { "resource_spec": {} },
            "source_revision_config": { "image_uri": image_uri },
            "managed_deep_agent": true,
        });
        payload[identity_key] = identity;
        payload["secrets"] = serde_json::to_value(secrets)?;
        self.request_with_timeout(
            &self.host_backend_url,
            Method::POST,
            "/v2/deployments",
            &[],
            Some(payload),
            CREATE_DEPLOYMENT_TIMEOUT,
        )
    }

    /// Request a signed upload URL for the source tarball used by remote builds.
    pub fn request_upload_url(&self, deployment_id: &str) -> Result<Value> {
        self.request(
            &self.host_backend_url,
            Method::POST,
            &format!("/v2/deployments/{deployment_id}/upload-url"),
            &[],
            None,
        )
    }

    /// Trigger a remote build revision from an uploaded source tarball.
    pub fn update_deployment_internal_source(
        &self,
        deployment_id: &str,
        source_tarball_path: &str,
        config_path: &str,
        secrets: &[Secret],
    ) -> Result<Value> {
        let mut payload = json!({
            "revision_source": "internal_source",
            "source_revision_config": {
                "source_tarball_path": source_tarball_path,
                "langgraph_config_path": config_path,
            },
            // Re-affirm the MDA marker on update: this keeps the flag on the
            // "existing deployment" path (where `create_deployment` is skipped)
            // so the Host Backend re-checks the entitlement here too. The
            // deployment type is set at create and is not resent.
            "managed_deep_agent": true,
        });
        payload["secrets"] = serde_json::to_value(secrets)?;
        self.request(
            &self.host_backend_url,
            Method::PATCH,
            &format!("/v2/deployments/{deployment_id}"),
            &[],
            Some(payload),
        )
    }

    /// Create a new revision using a customer-managed image.
    pub fn update_deployment_external_image(
        &self,
        deployment_id: &str,
        image_uri: &str,
        secrets: &[Secret],
    ) -> Result<Value> {
        let mut payload = json!({
            "source_revision_config": { "image_uri": image_uri },
            "managed_deep_agent": true,
        });
        payload["secrets"] = serde_json::to_value(secrets)?;
        self.request(
            &self.host_backend_url,
            Method::PATCH,
            &format!("/v2/deployments/{deployment_id}"),
            &[],
            Some(payload),
        )
    }

    /// Delete a deployment, optionally removing its tracing project too.
    ///
    /// Returns `false` when the deployment is already gone (HTTP 404), so
    /// teardown can report "nothing to do" instead of failing.
    ///
    /// The tracing project LangSmith auto-creates alongside a deployment is a
    /// separate resource that outlives the deployment. Leaving it behind makes
    /// the next `mda deploy` of the same name fail with a conflict, so callers
    /// tearing a deployment down should pass `delete_tracing_project`.
    pub fn delete_deployment(
        &self,
        deployment_id: &str,
        delete_tracing_project: bool,
    ) -> Result<bool> {
        let deleted = self.request_optional(
            &self.host_backend_url,
            Method::DELETE,
            &format!("/v2/deployments/{deployment_id}"),
            &[(
                "delete_tracing_project",
                if delete_tracing_project {
                    "true"
                } else {
                    "false"
                },
            )],
            None,
        )?;
        Ok(deleted.is_some())
    }

    /// List a deployment's revisions, newest first.
    pub fn list_revisions(&self, deployment_id: &str, limit: u32) -> Result<Value> {
        let limit = limit.to_string();
        self.request(
            &self.host_backend_url,
            Method::GET,
            &format!("/v2/deployments/{deployment_id}/revisions"),
            &[("limit", limit.as_str())],
            None,
        )
    }

    /// Store the build artifacts `mda prepare` produced for a revision.
    ///
    /// Host-backend accepts this only while the revision is building; the
    /// deploy step reads the snapshot reference and the reconcile step reads
    /// the cron and channel payloads.
    pub fn put_managed_deep_agent_artifacts(
        &self,
        deployment_id: &str,
        revision_id: &str,
        artifacts: Value,
        build_token: &HeaderValue,
    ) -> Result<Value> {
        // This credential must not enter shared client state or follow redirects.
        let mut headers = HeaderMap::new();
        let mut build_token = build_token.clone();
        build_token.set_sensitive(true);
        headers.insert("X-MDA-Build-Token", build_token);
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .redirect(Policy::none())
            .default_headers(headers)
            .build()
            .context("failed to build artifact-report HTTP client")?;
        let path = format!(
            "/v2/managed-deep-agents/{deployment_id}/revisions/{revision_id}/build-artifacts"
        );
        let (status, text) = send_json_request(
            &http,
            Method::PUT,
            &format!("{}{path}", self.host_backend_url),
            self.api_key(),
            self.tenant_id.as_deref(),
            &[],
            Some(&artifacts),
            false,
        )
        .context("build artifact report request failed")?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        decode_json_body(&text, &path)
    }

    /// Fetch a single revision (expected to carry a `status`).
    pub fn get_revision(&self, deployment_id: &str, revision_id: &str) -> Result<Value> {
        self.request(
            &self.host_backend_url,
            Method::GET,
            &format!("/v2/deployments/{deployment_id}/revisions/{revision_id}"),
            &[],
            None,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::langsmith::ApiError;
    use reqwest::StatusCode;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    fn client_for_host(host_backend_url: String) -> Client {
        Client {
            host_backend_url,
            ..Client::for_test("http://unused.invalid")
        }
    }

    fn read_http_request(stream: &mut TcpStream) -> String {
        let mut buf = Vec::new();
        let mut tmp = [0_u8; 1024];
        loop {
            let n = stream.read(&mut tmp).unwrap();
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&tmp[..n]);
            let request = String::from_utf8_lossy(&buf);
            let Some(headers_end) = request.find("\r\n\r\n") else {
                continue;
            };
            let content_length = request
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    (name.eq_ignore_ascii_case("content-length"))
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or(0);
            if buf.len() >= headers_end + 4 + content_length {
                break;
            }
        }
        String::from_utf8(buf).unwrap()
    }

    fn serve_json_once(response_body: &str) -> (String, Receiver<String>) {
        serve_once("200 OK", response_body)
    }

    fn serve_once(status: &str, response_body: &str) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        let response_body = response_body.to_string();
        let status = status.to_string();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let request = read_http_request(&mut stream);
            tx.send(request).unwrap();
            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{response_body}",
                response_body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        (format!("http://{addr}"), rx)
    }

    fn request_json_body(request: &str) -> Value {
        let body = request.split("\r\n\r\n").nth(1).unwrap();
        serde_json::from_str(body).unwrap()
    }

    #[test]
    fn artifact_report_sends_revision_credential_without_changing_other_requests() {
        let (url, rx) = serve_once("204 No Content", "");
        let mut client = client_for_host(url);
        let token = HeaderValue::from_static("test-build-token");
        let artifacts = json!({"version": 1, "assistant_id": "agent"});

        let response = client
            .put_managed_deep_agent_artifacts("deployment", "revision", artifacts.clone(), &token)
            .unwrap();
        assert!(response.is_null());
        let request = rx.recv().unwrap();
        assert!(request.starts_with(
            "PUT /v2/managed-deep-agents/deployment/revisions/revision/build-artifacts "
        ));
        let headers = request
            .split("\r\n\r\n")
            .next()
            .unwrap()
            .to_ascii_lowercase();
        assert!(headers
            .lines()
            .any(|line| line == "x-mda-build-token: test-build-token"));
        assert!(headers.lines().any(|line| line == "x-api-key: key"));
        assert!(headers.lines().any(|line| line == "x-tenant-id: tenant"));
        assert_eq!(request_json_body(&request), artifacts);

        let (url, rx) = serve_json_once(r#"{"id":"deployment"}"#);
        client.host_backend_url = url;
        client.get_deployment("deployment").unwrap();
        assert!(!rx
            .recv()
            .unwrap()
            .to_ascii_lowercase()
            .contains("x-mda-build-token"));
    }

    #[test]
    fn artifact_report_preserves_forbidden_error() {
        let (url, _rx) = serve_once(
            "403 Forbidden",
            r#"{"detail":"A revision build credential is required"}"#,
        );
        let err = client_for_host(url)
            .put_managed_deep_agent_artifacts(
                "deployment",
                "revision",
                json!({}),
                &HeaderValue::from_static("rejected-token"),
            )
            .unwrap_err();
        assert_eq!(
            err.downcast_ref::<ApiError>().unwrap().status,
            StatusCode::FORBIDDEN
        );
        assert!(!format!("{err:#}").contains("rejected-token"));
    }

    #[test]
    fn artifact_report_does_not_follow_redirects() {
        for target in ["/other-endpoint", "https://other.example/artifacts"] {
            let (url, rx) =
                serve_once(&format!("307 Temporary Redirect\r\nLocation: {target}"), "");
            let err = client_for_host(url)
                .put_managed_deep_agent_artifacts(
                    "deployment",
                    "revision",
                    json!({}),
                    &HeaderValue::from_static("test-build-token"),
                )
                .unwrap_err();
            assert_eq!(
                err.downcast_ref::<ApiError>().unwrap().status,
                StatusCode::TEMPORARY_REDIRECT
            );
            assert!(rx
                .recv()
                .unwrap()
                .starts_with("PUT /v2/managed-deep-agents/"));
        }
    }

    #[test]
    fn list_deployments_filters_by_name_substring() {
        let (url, rx) = serve_json_once(r#"{"resources":[]}"#);
        let client = client_for_host(url);

        client.list_deployments("my-agent").unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /v2/deployments?name_contains=my-agent "),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn create_deployment_requests_caller_type_and_managed_deep_agent_flag() {
        let (url, rx) = serve_json_once(r#"{"id":"deployment-1"}"#);
        let client = client_for_host(url);

        client
            .create_deployment("agent", DeploymentType::Dev, &[])
            .unwrap();

        let request = rx.recv().unwrap();
        let body = request_json_body(&request);
        // The caller-selected deployment type is sent, and the deployment is
        // still tagged with the request-only `managed_deep_agent` flag.
        assert_eq!(body["source"], "internal_source");
        assert_eq!(body["source_config"]["deployment_type"], "dev");
        assert_eq!(body["managed_deep_agent"], true);
    }

    #[test]
    fn create_deployment_forwards_prod_type_and_keeps_managed_deep_agent_flag() {
        let (url, rx) = serve_json_once(r#"{"id":"deployment-1"}"#);
        let client = client_for_host(url);

        client
            .create_deployment("agent", DeploymentType::Prod, &[])
            .unwrap();

        let request = rx.recv().unwrap();
        let body = request_json_body(&request);
        assert_eq!(body["source"], "internal_source");
        assert_eq!(body["source_config"]["deployment_type"], "prod");
        assert_eq!(body["managed_deep_agent"], true);
    }

    #[test]
    fn create_agent_deployment_sends_agent_instead_of_name() {
        let (url, rx) = serve_json_once(r#"{"id":"deployment-1","name":"generated"}"#);
        let client = client_for_host(url);
        let agent = DeploymentAgent {
            agent_id: "customer-support".into(),
            environment: crate::langsmith::AgentEnvironment::Staging,
        };

        client
            .create_agent_deployment(&agent, DeploymentType::Dev, &[])
            .unwrap();

        let body = request_json_body(&rx.recv().unwrap());
        assert_eq!(
            body["agent"],
            json!({"agent_id": "customer-support", "environment": "staging"})
        );
        assert!(body.get("name").is_none());
    }

    #[test]
    fn create_external_deployment_sends_image_source() {
        let (url, rx) = serve_json_once(r#"{"id":"deployment-1"}"#);
        let client = client_for_host(url);
        let image = format!("registry.example/agent@sha256:{}", "a".repeat(64));

        client
            .create_external_deployment("agent", &image, &[])
            .unwrap();

        let body = request_json_body(&rx.recv().unwrap());
        assert_eq!(body["source"], "external_docker");
        assert_eq!(body["source_config"], json!({"resource_spec": {}}));
        assert_eq!(body["source_revision_config"]["image_uri"], image);
        assert_eq!(body["managed_deep_agent"], true);
    }

    #[test]
    fn update_external_deployment_sends_image_revision_without_revision_source() {
        let (url, rx) = serve_json_once(r#"{"id":"deployment-1"}"#);
        let client = client_for_host(url);
        let image = format!("registry.example/agent@sha256:{}", "b".repeat(64));

        client
            .update_deployment_external_image("deployment-1", &image, &[])
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(request.starts_with("PATCH /v2/deployments/deployment-1 "));
        let body = request_json_body(&request);
        assert_eq!(body["source_revision_config"]["image_uri"], image);
        assert_eq!(body["managed_deep_agent"], true);
        assert!(body.get("revision_source").is_none());
    }

    /// The entitlement gate has to reach `mda deploy` as a typed error, not just
    /// a message — that is what lets deploy report it as a plan upsell.
    #[test]
    fn create_deployment_reports_an_entitlement_gate_as_a_typed_error() {
        let (url, _rx) = serve_once(
            "403 Forbidden",
            r#"{"detail":"Managed Deep Agents is not enabled for this organization"}"#,
        );
        let client = client_for_host(url);

        let err = client
            .create_deployment("agent", DeploymentType::Dev, &[])
            .unwrap_err();

        let api = err
            .chain()
            .find_map(|cause| cause.downcast_ref::<ApiError>())
            .expect("a typed API error in the chain");
        assert_eq!(api.status, StatusCode::FORBIDDEN);
        assert_eq!(
            api.detail,
            "Managed Deep Agents is not enabled for this organization"
        );
    }

    #[test]
    fn delete_deployment_requests_tracing_project_removal() {
        let (url, rx) = serve_json_once("{}");
        let client = client_for_host(url);

        assert!(client.delete_deployment("deployment-1", true).unwrap());

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("DELETE /v2/deployments/deployment-1?delete_tracing_project=true "),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn delete_deployment_can_keep_the_tracing_project() {
        let (url, rx) = serve_json_once("{}");
        let client = client_for_host(url);

        client.delete_deployment("deployment-1", false).unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.contains("delete_tracing_project=false"),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn delete_deployment_treats_missing_deployment_as_already_gone() {
        let (url, _rx) = serve_once("404 Not Found", r#"{"detail":"not found"}"#);
        let client = client_for_host(url);

        assert!(!client.delete_deployment("deployment-1", true).unwrap());
    }

    #[test]
    fn delete_deployment_surfaces_other_errors() {
        let (url, _rx) = serve_once("403 Forbidden", r#"{"detail":"missing permission"}"#);
        let client = client_for_host(url);

        let err = client.delete_deployment("deployment-1", true).unwrap_err();
        assert!(
            err.to_string().contains("missing permission"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn put_managed_deep_agent_artifacts_targets_the_revision_route() {
        let (url, rx) = serve_json_once("{}");
        let client = client_for_host(url);

        client
            .put_managed_deep_agent_artifacts(
                "deployment-1",
                "revision-1",
                json!({"version": 1}),
                &HeaderValue::from_static("test-build-token"),
            )
            .unwrap();

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with(
                "PUT /v2/managed-deep-agents/deployment-1/revisions/revision-1/build-artifacts "
            ),
            "unexpected request line: {request}"
        );
        assert_eq!(request_json_body(&request)["version"], 1);
    }

    #[test]
    fn update_internal_source_sends_managed_deep_agent_flag() {
        let (url, rx) = serve_json_once(r#"{"latest_revision_id":"revision-1"}"#);
        let client = client_for_host(url);

        client
            .update_deployment_internal_source(
                "deployment-1",
                "source.tar.gz",
                "langgraph.json",
                &[],
            )
            .unwrap();

        let request = rx.recv().unwrap();
        let body = request_json_body(&request);
        // The update carries the flag but does not resend `source_config`
        // (the deployment type is fixed at create time).
        assert_eq!(body["managed_deep_agent"], true);
        assert!(body.get("source_config").is_none());
    }
}
