//! Context Hub endpoints used during deploy-time context sync.
//!
//! Calls go to the Context Hub base URL derived from the LangSmith host backend
//! URL and use the same credential and optional tenant scope.

use std::collections::BTreeMap;

use anyhow::{bail, Context, Result};
use reqwest::blocking::Client as HttpClient;
use reqwest::{Method, StatusCode};
use serde_json::{json, Value};

use super::{
    decode_json_body, is_self_hosted_host_backend_url, require_str, send_json_request, ApiError,
    ApiKey, REQUEST_TIMEOUT,
};

/// Use `-` to resolve the current tenant/workspace owner.
const CURRENT_OWNER: &str = "-";

/// Tag stamped on every repo `mda deploy` creates.
///
/// This is the only durable record that a Context Hub repo is deploy-owned, and
/// `create_repo` has written it since the first release that created repos at
/// all — so every MDA repo in existence carries it and nothing needs
/// backfilling. `repo_type: "agent"` is deliberately *not* used as the signal:
/// the Agent Builder UI creates `agent` repos too, and only this tag is unique
/// to `mda deploy`.
const MANAGED_REPO_TAG: &str = "managed-deepagents";

/// What a same-name Context Hub repo is, from a first deploy's point of view.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RepoOwnership {
    /// No repo with this handle — deploy will create it.
    Missing,
    /// A repo `mda deploy` created. Syncing into it only rewrites content this
    /// deploy already owns.
    ManagedDeepAgent,
    /// A repo something else created that happens to share the name. Syncing
    /// into it would overwrite someone else's work.
    Foreign,
}

/// Blocking JSON client for the subset of Context Hub needed by deploy.
pub struct ContextHubClient {
    base_url: String,
    platform_hub_prefix: &'static str,
    api_key: ApiKey,
    tenant_id: Option<String>,
    http: HttpClient,
    /// Tags added alongside [`MANAGED_REPO_TAG`] when this client creates a
    /// repo (for example the GitHub-sourced marker the Hub UI reads).
    extra_repo_tags: Vec<String>,
}

impl ContextHubClient {
    /// Create a Context Hub client for `base_url`, using the routing mode of
    /// `host_backend_url` and sending `api_key` (and optional `tenant_id`).
    pub(crate) fn new(
        base_url: &str,
        host_backend_url: &str,
        api_key: ApiKey,
        tenant_id: Option<String>,
    ) -> Result<Self> {
        let self_hosted = is_self_hosted_host_backend_url(host_backend_url);
        if base_url.is_empty() {
            bail!("Context Hub base URL is required");
        }
        let parsed = reqwest::Url::parse(base_url).context("Context Hub base URL is invalid")?;
        if !matches!(parsed.scheme(), "http" | "https") {
            bail!("Context Hub base URL must use http or https");
        }
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build Context Hub HTTP client")?;
        Ok(Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            platform_hub_prefix: if self_hosted {
                "/api/v1/platform/hub"
            } else {
                "/v1/platform/hub"
            },
            api_key,
            tenant_id,
            http,
            extra_repo_tags: Vec::new(),
        })
    }

    /// Unscoped client for a stub Context Hub at `base_url`.
    #[cfg(test)]
    pub(crate) fn for_test(base_url: &str) -> Self {
        Self::new(base_url, base_url, ApiKey::new("key"), None).expect("test client builds")
    }

    /// Tag repos this client creates with `tags` in addition to the ownership
    /// tag. Ownership detection only ever looks for [`MANAGED_REPO_TAG`], so
    /// extra tags never change what `repo_ownership` reports.
    pub(crate) fn with_extra_repo_tags(mut self, tags: impl IntoIterator<Item = String>) -> Self {
        self.extra_repo_tags = tags.into_iter().collect();
        self
    }

    fn repo_tags(&self) -> Vec<&str> {
        let mut tags = vec![MANAGED_REPO_TAG];
        tags.extend(
            self.extra_repo_tags
                .iter()
                .map(String::as_str)
                .filter(|tag| *tag != MANAGED_REPO_TAG),
        );
        tags
    }

    /// Ensure a Context Hub repo exists for this deployment.
    pub fn ensure_repo(&self, repo: &str) -> Result<()> {
        if self.repo_ownership(repo)? == RepoOwnership::Missing {
            return self.create_repo(repo);
        }
        Ok(())
    }

    /// Tag `repo` with the Agent tag value `tag_value_id`, which lists the
    /// repo under that agent in Context Hub. Tagging twice is a no-op.
    pub(crate) fn tag_repo(&self, repo: &str, tag_value_id: &str) -> Result<()> {
        let (status, text) = self.send(
            Method::GET,
            &format!("/api/v1/repos/{CURRENT_OWNER}/{repo}"),
            None,
        )?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        let payload = decode_json_body(&text, "Context Hub repo")?;
        let repo_id = require_str(&payload["repo"], "id", "Context Hub repo")?;
        let (status, text) = self.send(
            Method::POST,
            "/api/v1/workspaces/current/taggings",
            Some(json!({"tag_value_id": tag_value_id, "resource_type": "agent", "resource_id": repo_id})),
        )?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        Ok(())
    }

    /// Classify a same-name Context Hub repo as missing, deploy-owned, or
    /// someone else's.
    ///
    /// Fails closed: only a repo whose `tags` array literally contains
    /// [`MANAGED_REPO_TAG`] is reported as [`RepoOwnership::ManagedDeepAgent`].
    /// A body we cannot parse, a repo with no tags, or tags of an unexpected
    /// shape all read as [`RepoOwnership::Foreign`], because the caller uses
    /// this to decide whether overwriting is safe. Non-404 failures surface as
    /// errors rather than being flattened into a verdict — a 403 must never be
    /// mistaken for "nothing is there".
    pub(crate) fn repo_ownership(&self, repo: &str) -> Result<RepoOwnership> {
        let (status, text) = self.send(
            Method::GET,
            &format!("/api/v1/repos/{CURRENT_OWNER}/{repo}"),
            None,
        )?;
        if status == StatusCode::NOT_FOUND {
            return Ok(RepoOwnership::Missing);
        }
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        Ok(match repo_has_managed_tag(&text) {
            true => RepoOwnership::ManagedDeepAgent,
            false => RepoOwnership::Foreign,
        })
    }

    pub(crate) fn get_directory(&self, repo: &str) -> Result<DirectorySnapshot> {
        let (status, text) = self.send(
            Method::GET,
            &format!(
                "{}/repos/{CURRENT_OWNER}/{repo}/directories",
                self.platform_hub_prefix
            ),
            None,
        )?;
        if status == StatusCode::NOT_FOUND {
            return Ok(DirectorySnapshot::default());
        }
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        DirectorySnapshot::from_value(decode_json_body(&text, "Context Hub directory")?)
    }

    pub(crate) fn create_directory_commit(
        &self,
        repo: &str,
        parent_commit: Option<&str>,
        files: BTreeMap<String, Value>,
    ) -> Result<()> {
        let mut payload = json!({ "files": files });
        if let Some(parent_commit) = parent_commit {
            payload["parent_commit"] = Value::String(parent_commit.to_string());
        }

        let (status, text) = self.send(
            Method::POST,
            &format!(
                "{}/repos/{CURRENT_OWNER}/{repo}/directories/commits",
                self.platform_hub_prefix
            ),
            Some(payload),
        )?;
        if status == StatusCode::CONFLICT {
            bail!("Context Hub repo '{repo}' changed during deploy; rerun `mda deploy` to retry");
        }
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        let _: Value = decode_json_body(&text, "Context Hub commit")?;
        Ok(())
    }

    fn create_repo(&self, repo: &str) -> Result<()> {
        let payload = json!({
            "repo_handle": repo,
            "repo_type": "agent",
            "is_public": false,
            "description": "Managed Deep Agents deployed context",
            // Read back by `repo_ownership` to tell our repos from same-name
            // ones we must not overwrite. Keep the two in step.
            "tags": self.repo_tags(),
        });
        let (status, text) = self.send(Method::POST, "/api/v1/repos", Some(payload))?;
        if status.is_success() || status == StatusCode::CONFLICT {
            return Ok(());
        }
        bail!(ApiError::from_response(status, &text))
    }

    /// Delete a Context Hub repo and everything committed to it.
    ///
    /// Returns `false` when the repo is already gone (HTTP 404) so teardown is
    /// idempotent.
    pub fn delete_repo(&self, repo: &str) -> Result<bool> {
        let (status, text) = self.send(
            Method::DELETE,
            &format!(
                "{}/repos/{CURRENT_OWNER}/{repo}/directories",
                self.platform_hub_prefix
            ),
            None,
        )?;
        if status == StatusCode::NOT_FOUND {
            return Ok(false);
        }
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        Ok(true)
    }

    /// List the handles of every non-archived agent repo in the workspace.
    ///
    /// Paginates to exhaustion. Used by teardown to find a deployment's repo
    /// plus any legacy per-user or org memory children under it, which are not
    /// discoverable by name alone.
    pub fn list_agent_repo_handles(&self) -> Result<Vec<String>> {
        const PAGE_SIZE: usize = 100;
        let mut handles = Vec::new();
        let mut offset = 0usize;

        loop {
            let offset_param = offset.to_string();
            let limit_param = PAGE_SIZE.to_string();
            let (status, text) = self.send_with_params(
                Method::GET,
                "/api/v1/repos",
                &[
                    ("repo_type", "agent"),
                    ("is_archived", "false"),
                    ("limit", limit_param.as_str()),
                    ("offset", offset_param.as_str()),
                ],
                None,
            )?;
            if !status.is_success() {
                bail!(ApiError::from_response(status, &text));
            }
            let payload = decode_json_body(&text, "Context Hub repos")?;
            let page = payload
                .get("repos")
                .and_then(Value::as_array)
                .map(Vec::as_slice)
                .unwrap_or_default();
            let page_len = page.len();
            handles.extend(
                page.iter()
                    .filter_map(|repo| repo.get("repo_handle").and_then(Value::as_str))
                    .map(str::to_string),
            );

            if page_len < PAGE_SIZE {
                break;
            }
            offset += page_len;
            if let Some(total) = payload.get("total").and_then(Value::as_u64) {
                if offset as u64 >= total {
                    break;
                }
            }
        }

        Ok(handles)
    }

    fn send(
        &self,
        method: Method,
        path: &str,
        payload: Option<Value>,
    ) -> Result<(StatusCode, String)> {
        self.send_with_params(method, path, &[], payload)
    }

    fn send_with_params(
        &self,
        method: Method,
        path: &str,
        params: &[(&str, &str)],
        payload: Option<Value>,
    ) -> Result<(StatusCode, String)> {
        let url = format!("{}{}", self.base_url, path);
        send_json_request(
            &self.http,
            method.clone(),
            &url,
            self.api_key.as_str(),
            self.tenant_id.as_deref(),
            params,
            payload.as_ref(),
            false,
        )
        .with_context(|| format!("{method} {path} request failed"))
    }
}

/// Whether a get-repo response body carries [`MANAGED_REPO_TAG`].
///
/// Deliberately narrow: the tag must appear as an exact string element of
/// `repo.tags`. Matching on the raw body instead would let a repo that merely
/// mentions the tag in its description or readme pass as deploy-owned. Anything
/// unexpected — unparseable JSON, no `repo`, no `tags`, `tags` that is not an
/// array — answers `false`, so the caller keeps its confirmation prompt.
fn repo_has_managed_tag(body: &str) -> bool {
    serde_json::from_str::<Value>(body)
        .ok()
        .as_ref()
        .and_then(|body| body.get("repo")?.get("tags")?.as_array())
        .is_some_and(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .any(|tag| tag == MANAGED_REPO_TAG)
        })
}

#[derive(Debug, Default)]
pub(crate) struct DirectorySnapshot {
    pub(crate) commit_hash: Option<String>,
    pub(crate) files: BTreeMap<String, Value>,
}

impl DirectorySnapshot {
    /// Parse a directories response, refusing to read shape drift as "empty".
    ///
    /// Strict on purpose. Deploy treats an absent file as "nothing deployed yet" and
    /// seeds memory, and it passes `commit_hash` as the commit's parent so the Hub can
    /// reject a racing write. Quietly defaulting both — which is what this did — turns
    /// a renamed or re-nested field into a deploy that seeds an empty memory file over
    /// the agent's real one *and* waives the conflict check that would have caught it.
    /// An error here costs a failed deploy; the silent version costs someone's memory.
    fn from_value(mut value: Value) -> Result<Self> {
        let commit_hash = value
            .get("commit_hash")
            .and_then(Value::as_str)
            .map(str::to_string);
        let files = match value
            .as_object_mut()
            .and_then(|object| object.remove("files"))
        {
            Some(Value::Object(map)) => map.into_iter().collect(),
            // An existing directory with nothing in it. A repo with no directory at
            // all answers 404, which [`ContextHubClient::get_directory`] handles
            // before parsing — so a missing field here is drift, not emptiness.
            Some(Value::Null) => BTreeMap::new(),
            Some(_) => bail!("Context Hub directory response has a non-object `files` field"),
            None => bail!("Context Hub directory response has no `files` field"),
        };
        if !files.is_empty() && commit_hash.is_none() {
            bail!("Context Hub directory response lists files but no `commit_hash`");
        }
        Ok(Self { commit_hash, files })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_tags_always_lead_with_the_ownership_tag() {
        let client = ContextHubClient::for_test("http://localhost:1980");
        assert_eq!(client.repo_tags(), vec!["managed-deepagents"]);

        let client = client.with_extra_repo_tags([
            "managed-deepagents:github".to_string(),
            "managed-deepagents".to_string(),
        ]);
        assert_eq!(
            client.repo_tags(),
            vec!["managed-deepagents", "managed-deepagents:github"]
        );
    }
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    #[test]
    fn parses_directory_responses() {
        let directory = DirectorySnapshot::from_value(json!({
            "commit_hash": "abc123",
            "files": { "instructions.md": { "type": "file", "content": "hi" } }
        }))
        .expect("valid response");

        assert_eq!(directory.commit_hash.as_deref(), Some("abc123"));
        assert_eq!(directory.files.len(), 1);
    }

    #[test]
    fn parses_an_empty_directory() {
        let directory =
            DirectorySnapshot::from_value(json!({ "files": null })).expect("valid response");

        assert!(directory.files.is_empty());
        assert!(directory.commit_hash.is_none());
    }

    #[test]
    fn refuses_to_read_a_drifted_directory_response_as_empty() {
        // Deploy seeds memory for files it believes are absent, so "empty" has to mean
        // empty — never "the field moved". Each of these used to parse as no files and
        // no parent commit, which commits an empty memory file over the agent's real
        // one with the conflict check waived.
        for payload in [
            // `files` renamed or re-nested.
            json!({}),
            json!({ "data": { "files": { "instructions.md": { "content": "hi" } } } }),
            // `files` became a list of entries.
            json!({ "commit_hash": "abc123", "files": [] }),
            // Files, but no parent commit to pin the write to.
            json!({ "files": { "instructions.md": { "content": "hi" } } }),
        ] {
            assert!(
                DirectorySnapshot::from_value(payload.clone()).is_err(),
                "parsed drift as empty: {payload}"
            );
        }
    }

    /// Serve `responses` in order, capturing each request line for assertions.
    fn serve(responses: Vec<(&'static str, String)>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            for (status, body) in responses {
                let (mut stream, _) = listener.accept().unwrap();
                let request = read_request_head(&mut stream);
                tx.send(request).unwrap();
                write!(
                    stream,
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });

        (base_url, rx)
    }

    /// Read just far enough to capture the request line (these endpoints send
    /// either no body or a body the tests do not assert on).
    fn read_request_head(stream: &mut TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 1024];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            if data.windows(4).any(|window| window == b"\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    fn client(base_url: &str) -> ContextHubClient {
        ContextHubClient::new(
            base_url,
            &format!("{base_url}/api-host"),
            ApiKey::new("key"),
            Some("tenant".into()),
        )
        .unwrap()
    }

    #[test]
    fn tag_repo_does_not_write_when_the_repo_id_is_missing() {
        let (base_url, requests) = serve(vec![("200 OK", managed_repo_body())]);
        assert!(client(&base_url).tag_repo("my-agent", "agent-tag").is_err());
        requests.recv().unwrap();
        assert!(requests.try_recv().is_err());
    }

    #[test]
    fn self_hosted_context_hub_uses_platform_api_prefix() {
        let client = ContextHubClient::new(
            "https://langsmith.example.com",
            "https://langsmith.example.com/api-host",
            ApiKey::new("key"),
            None,
        )
        .unwrap();

        assert_eq!(client.platform_hub_prefix, "/api/v1/platform/hub");
    }

    #[test]
    fn cloud_context_hub_preserves_cloud_api_prefix() {
        let client = ContextHubClient::for_test("https://eu.api.smith.langchain.com");

        assert_eq!(client.platform_hub_prefix, "/v1/platform/hub");
    }

    #[test]
    fn local_dev_context_hub_preserves_local_api_prefix() {
        let client = ContextHubClient::for_test("http://localhost:1980");

        assert_eq!(client.platform_hub_prefix, "/v1/platform/hub");
    }

    fn repo_page(handles: &[String]) -> String {
        let repos: Vec<Value> = handles
            .iter()
            .map(|handle| json!({ "id": handle, "repo_handle": handle }))
            .collect();
        json!({ "repos": repos }).to_string()
    }

    fn managed_repo_body() -> String {
        json!({ "repo": { "repo_handle": "my-agent", "tags": [MANAGED_REPO_TAG] } }).to_string()
    }

    #[test]
    fn repo_ownership_targets_the_current_owner_repo_endpoint() {
        let (base_url, rx) = serve(vec![("200 OK", managed_repo_body())]);

        assert_eq!(
            client(&base_url).repo_ownership("my-agent").unwrap(),
            RepoOwnership::ManagedDeepAgent
        );

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("GET /api/v1/repos/-/my-agent "),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn repo_ownership_reports_missing_for_absent_repo() {
        let (base_url, _rx) = serve(vec![("404 Not Found", r#"{"detail":"nope"}"#.to_string())]);

        assert_eq!(
            client(&base_url).repo_ownership("my-agent").unwrap(),
            RepoOwnership::Missing
        );
    }

    #[test]
    fn repo_ownership_reports_foreign_for_an_untagged_repo() {
        // What the Agent Builder UI creates: `repo_type: "agent"`, no tags.
        let (base_url, _rx) = serve(vec![(
            "200 OK",
            json!({ "repo": { "repo_handle": "my-agent", "repo_type": "agent", "tags": [] } })
                .to_string(),
        )]);

        assert_eq!(
            client(&base_url).repo_ownership("my-agent").unwrap(),
            RepoOwnership::Foreign
        );
    }

    #[test]
    fn repo_ownership_reports_foreign_when_another_tag_is_present() {
        let (base_url, _rx) = serve(vec![(
            "200 OK",
            json!({ "repo": { "repo_handle": "my-agent", "tags": ["production"] } }).to_string(),
        )]);

        assert_eq!(
            client(&base_url).repo_ownership("my-agent").unwrap(),
            RepoOwnership::Foreign
        );
    }

    #[test]
    fn repo_ownership_fails_closed_on_an_unreadable_body() {
        // Shape drift must never read as "ours" — the caller uses this verdict
        // to decide whether overwriting someone else's repo is safe.
        for body in [
            "not json".to_string(),
            "{}".to_string(),
            json!({ "repo": {} }).to_string(),
            json!({ "repo": { "tags": "managed-deepagents" } }).to_string(),
            json!({ "repo": { "tags": [{ "name": MANAGED_REPO_TAG }] } }).to_string(),
        ] {
            let (base_url, _rx) = serve(vec![("200 OK", body.clone())]);

            assert_eq!(
                client(&base_url).repo_ownership("my-agent").unwrap(),
                RepoOwnership::Foreign,
                "body should not read as managed: {body}"
            );
        }
    }

    #[test]
    fn repo_ownership_does_not_match_the_tag_elsewhere_in_the_body() {
        // A description that merely mentions the tag is not ownership.
        let (base_url, _rx) = serve(vec![(
            "200 OK",
            json!({ "repo": { "description": "copied from managed-deepagents", "tags": [] } })
                .to_string(),
        )]);

        assert_eq!(
            client(&base_url).repo_ownership("my-agent").unwrap(),
            RepoOwnership::Foreign
        );
    }

    #[test]
    fn repo_ownership_surfaces_other_errors() {
        // A permissions failure must not be flattened into a verdict.
        let (base_url, _rx) = serve(vec![(
            "403 Forbidden",
            r#"{"detail":"missing permission"}"#.to_string(),
        )]);

        let err = client(&base_url).repo_ownership("my-agent").unwrap_err();
        assert!(
            err.to_string().contains("missing permission"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn ensure_repo_creates_only_when_the_repo_is_missing() {
        let (base_url, rx) = serve(vec![
            ("404 Not Found", r#"{"detail":"nope"}"#.to_string()),
            ("200 OK", managed_repo_body()),
        ]);

        client(&base_url).ensure_repo("my-agent").unwrap();

        assert!(rx
            .recv()
            .unwrap()
            .starts_with("GET /api/v1/repos/-/my-agent "));
        let create = rx.recv().unwrap();
        assert!(
            create.starts_with("POST /api/v1/repos "),
            "unexpected request line: {create}"
        );
    }

    #[test]
    fn ensure_repo_leaves_an_existing_repo_alone() {
        let (base_url, rx) = serve(vec![("200 OK", managed_repo_body())]);

        client(&base_url).ensure_repo("my-agent").unwrap();

        assert!(rx
            .recv()
            .unwrap()
            .starts_with("GET /api/v1/repos/-/my-agent "));
        assert!(
            rx.try_recv().is_err(),
            "an existing repo must not be re-created"
        );
    }

    #[test]
    fn delete_repo_targets_the_current_owner_directory_endpoint() {
        let (base_url, rx) = serve(vec![("200 OK", "{}".to_string())]);

        assert!(client(&base_url).delete_repo("my-agent").unwrap());

        let request = rx.recv().unwrap();
        assert!(
            request.starts_with("DELETE /api/v1/platform/hub/repos/-/my-agent/directories "),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn delete_repo_treats_missing_repo_as_already_gone() {
        let (base_url, _rx) = serve(vec![("404 Not Found", r#"{"detail":"nope"}"#.to_string())]);

        assert!(!client(&base_url).delete_repo("my-agent").unwrap());
    }

    #[test]
    fn delete_repo_surfaces_other_errors() {
        let (base_url, _rx) = serve(vec![(
            "403 Forbidden",
            r#"{"detail":"missing permission"}"#.to_string(),
        )]);

        let err = client(&base_url).delete_repo("my-agent").unwrap_err();
        assert!(
            err.to_string().contains("missing permission"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn list_agent_repo_handles_filters_to_agent_repos() {
        let (base_url, rx) = serve(vec![(
            "200 OK",
            repo_page(&["my-agent".to_string(), "other".to_string()]),
        )]);

        assert_eq!(
            client(&base_url).list_agent_repo_handles().unwrap(),
            vec!["my-agent".to_string(), "other".to_string()]
        );

        let request = rx.recv().unwrap();
        assert!(
            request.contains("repo_type=agent") && request.contains("is_archived=false"),
            "unexpected request line: {request}"
        );
    }

    #[test]
    fn list_agent_repo_handles_paginates_until_a_short_page() {
        let first: Vec<String> = (0..100).map(|index| format!("repo-{index}")).collect();
        let (base_url, rx) = serve(vec![
            ("200 OK", repo_page(&first)),
            ("200 OK", repo_page(&["repo-100".to_string()])),
        ]);

        let handles = client(&base_url).list_agent_repo_handles().unwrap();

        assert_eq!(handles.len(), 101);
        assert_eq!(handles.last().map(String::as_str), Some("repo-100"));
        assert!(rx.recv().unwrap().contains("offset=0"));
        assert!(rx.recv().unwrap().contains("offset=100"));
    }

    #[test]
    fn list_agent_repo_handles_stops_at_the_reported_total() {
        let first: Vec<String> = (0..100).map(|index| format!("repo-{index}")).collect();
        let mut page: Value = serde_json::from_str(&repo_page(&first)).unwrap();
        page["total"] = json!(100);
        let (base_url, _rx) = serve(vec![("200 OK", page.to_string())]);

        // A full page whose `total` is already reached must not request another.
        assert_eq!(
            client(&base_url).list_agent_repo_handles().unwrap().len(),
            100
        );
    }
}
