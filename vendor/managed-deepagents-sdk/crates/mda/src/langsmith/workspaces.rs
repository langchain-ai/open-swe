//! Workspace and organization lookup on the LangSmith platform API
//! (`api.smith.langchain.com`).
//!
//! Best-effort workspace/organization names and authenticated identity for CLI
//! output and invocation metadata. An ambiguous workspace stays unknown rather
//! than guessing which accessible workspace the credential selected.

use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::Client;

/// Timeout for the optional workspace lookup.
///
/// These lookups only ever decorate output — a dashboard link, or the lines
/// naming where a deploy is going. At the shared 30-second `REQUEST_TIMEOUT` an
/// unreachable platform API would sit there for another half minute, which reads
/// as a hung CLI, so cap it at something a developer will not notice.
const LOOKUP_TIMEOUT: Duration = Duration::from_secs(5);

/// A workspace as listed by the platform API.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSummary {
    /// Workspace id, the `/o/{id}` segment of every dashboard link.
    pub id: String,
    /// Workspace name as shown in the dashboard, when the listing carried one.
    pub display_name: Option<String>,
}

impl WorkspaceSummary {
    /// The workspace's name, falling back to its id when unnamed.
    pub fn label(&self) -> &str {
        self.display_name.as_deref().unwrap_or(&self.id)
    }
}

/// The LangSmith organization and workspace a client acts in.
///
/// Both fields are optional: this exists to tell a developer where their deploy
/// is going, so an unavailable platform API or a credential without
/// organization read degrades to saying less rather than to an error.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeployTarget {
    /// Organization display name.
    pub organization: Option<String>,
    /// The workspace, when exactly one could be identified.
    pub workspace: Option<WorkspaceSummary>,
    /// Every workspace the lookup saw, for naming an id that turns up later.
    workspaces: Vec<WorkspaceSummary>,
}

impl DeployTarget {
    /// Name `workspace_id`, falling back to the id when it was not listed.
    pub fn label_for<'a>(&'a self, workspace_id: &'a str) -> &'a str {
        self.workspaces
            .iter()
            .find(|workspace| workspace.id == workspace_id)
            .map_or(workspace_id, WorkspaceSummary::label)
    }
}

/// The user, organization and workspace LangSmith verifies behind a key.
#[derive(Default, Deserialize, Serialize)]
pub(crate) struct AuthIdentity {
    #[serde(rename(deserialize = "ls_user_id"))]
    pub user_id: Option<String>,
    #[serde(rename(deserialize = "organization_id"))]
    pub org_id: Option<String>,
    #[serde(rename(deserialize = "tenant_id"))]
    pub workspace_id: Option<String>,
}

impl Client {
    /// [`Self::auth_identity`], giving up after `timeout`. Skipped when the key
    /// would cross the network in cleartext, since nothing the user asked for
    /// depends on it.
    pub(crate) fn auth_identity_within(&self, timeout: Duration) -> Option<AuthIdentity> {
        if !self.sends_key_securely() {
            return None;
        }
        self.clone().with_timeout(timeout).ok()?.auth_identity()
    }

    /// Best-effort identity lookup; errors leave optional metadata unknown.
    pub(crate) fn auth_identity(&self) -> Option<AuthIdentity> {
        let mut metadata: AuthIdentity = serde_json::from_value(
            self.request(self.platform_base(), Method::GET, "/auth/public", &[], None)
                .ok()?,
        )
        .ok()?;
        for id in [
            &mut metadata.user_id,
            &mut metadata.org_id,
            &mut metadata.workspace_id,
        ] {
            *id = id
                .take()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty() && !value.contains(['\n', '\r']));
        }
        if self
            .tenant_id
            .as_ref()
            .is_some_and(|id| metadata.workspace_id.as_ref() != Some(id))
        {
            return None;
        }
        Some(metadata)
    }

    /// Resolve a workspace id for this client, for use in dashboard links.
    ///
    /// Prefers the workspace the client is already scoped to and otherwise asks
    /// `GET /api/v1/workspaces`, which lists the workspaces the credential can
    /// see *in its own organization*. Any of them is good enough for an
    /// organization-level page (billing, plans): the route segment selects the
    /// org through the workspace, so every workspace in the org lands on the same
    /// settings page.
    ///
    /// Best effort by design — this only ever decorates a message, so an
    /// unavailable platform API, a credential without organization read, or an
    /// unexpected body all read as "no link to offer" rather than an error. The
    /// lookup is bounded by [`LOOKUP_TIMEOUT`] for the same reason: failing to
    /// decorate a message must not hold up printing it.
    pub fn workspace_id_for_links(&self) -> Option<String> {
        self.tenant_id
            .clone()
            .or_else(|| self.first_workspace_id().ok().flatten())
    }

    /// Name the organization and workspace this client acts in.
    ///
    /// The workspace is only claimed when it is unambiguous: either the client
    /// is scoped to one, or the credential can see exactly one. A personal
    /// access token that can see several resolves its default workspace
    /// server-side, and guessing which one that is would be worse than saying
    /// nothing — [`DeployTarget::label_for`] names it from the same listing
    /// once a response reveals the id.
    ///
    /// Best effort, like [`Self::workspace_id_for_links`]: every lookup failure
    /// leaves the corresponding field `None`.
    pub fn describe_deploy_target(&self) -> DeployTarget {
        let workspaces = self.list_workspaces().unwrap_or_default();
        let workspace = match self.tenant_id.as_deref() {
            Some(id) => Some(WorkspaceSummary {
                id: id.to_string(),
                display_name: workspaces
                    .iter()
                    .find(|workspace| workspace.id == id)
                    .and_then(|workspace| workspace.display_name.clone()),
            }),
            None => match workspaces.as_slice() {
                [only] => Some(only.clone()),
                _ => None,
            },
        };
        DeployTarget {
            organization: self.organization_name().ok().flatten(),
            workspace,
            workspaces,
        }
    }

    /// Organization display name from `GET /api/v1/orgs/current`.
    fn organization_name(&self) -> Result<Option<String>> {
        let value = self.lookup("/api/v1/orgs/current")?;
        Ok(non_empty_str(value.get("display_name")))
    }

    /// The workspaces the credential can see, from `GET /api/v1/workspaces`.
    fn list_workspaces(&self) -> Result<Vec<WorkspaceSummary>> {
        let value = self.lookup("/api/v1/workspaces")?;
        Ok(value
            .as_array()
            .map(|workspaces| workspaces.iter().filter_map(decode_workspace).collect())
            .unwrap_or_default())
    }

    /// Whether the workspace uses the Agent platform, `false` when unknown.
    pub(crate) fn agent_platform_ready(&self) -> bool {
        self.lookup("/v1/platform/workspaces/current/info")
            .ok()
            .and_then(|info| {
                info.pointer("/config/agent_platform_workspace_ready")?
                    .as_bool()
            })
            .unwrap_or(false)
    }

    /// Authoritative dashboard base URL for this LangSmith installation.
    pub(crate) fn dashboard_url(&self) -> Result<String> {
        let info = self.lookup("/v1/platform/workspaces/current/info")?;
        let value = info
            .get("dashboard_url")
            .and_then(Value::as_str)
            .context("workspace info did not include dashboard_url")?;
        normalize_dashboard_url(value)
    }

    fn first_workspace_id(&self) -> Result<Option<String>> {
        Ok(self
            .list_workspaces()?
            .into_iter()
            .next()
            .map(|workspace| workspace.id))
    }

    /// A bounded, read-only GET against the platform API.
    fn lookup(&self, path: &str) -> Result<Value> {
        self.request_with_timeout(
            self.platform_base(),
            Method::GET,
            path,
            &[],
            None,
            LOOKUP_TIMEOUT,
        )
    }
}

fn decode_workspace(value: &Value) -> Option<WorkspaceSummary> {
    Some(WorkspaceSummary {
        id: non_empty_str(value.get("id"))?,
        display_name: non_empty_str(value.get("display_name")),
    })
}

fn non_empty_str(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn normalize_dashboard_url(value: &str) -> Result<String> {
    let url = reqwest::Url::parse(value.trim()).context("dashboard_url is not a valid URL")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        bail!("dashboard_url must be an absolute HTTP(S) URL without credentials or query data");
    }
    Ok(url.as_str().trim_end_matches('/').to_string())
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    use serde_json::json;

    use super::*;
    use crate::langsmith::Client;

    /// A platform API that answers a fixed body per path, one connection per
    /// request (the client's requests are sequential and `Connection: close`).
    fn serve_paths(routes: Vec<(&'static str, &'static str, String)>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        thread::spawn(move || {
            for _ in 0..routes.len() {
                let Ok((mut stream, _)) = listener.accept() else {
                    return;
                };
                let mut buf = [0_u8; 2048];
                let read = stream.read(&mut buf).unwrap_or(0);
                let request = String::from_utf8_lossy(&buf[..read]).to_string();
                let (status, body) = routes
                    .iter()
                    .find(|(path, _, _)| request.contains(&format!("GET {path} ")))
                    .map(|(_, status, body)| (*status, body.clone()))
                    .unwrap_or(("404 Not Found", "{}".to_string()));
                let response = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        format!("http://{addr}")
    }

    fn client_for(platform_api_url: String, tenant_id: Option<&str>) -> Client {
        Client {
            tenant_id: tenant_id.map(str::to_string),
            ..Client::for_test(&platform_api_url)
        }
    }

    fn two_workspaces() -> String {
        json!([
            { "id": "ws-1", "display_name": "Production" },
            { "id": "ws-2", "display_name": "Development" }
        ])
        .to_string()
    }

    fn current_org() -> String {
        json!({ "id": "org-1", "display_name": "LangChain Inc." }).to_string()
    }

    #[test]
    fn a_scoped_client_names_its_own_workspace_and_organization() {
        let base = serve_paths(vec![
            ("/api/v1/workspaces", "200 OK", two_workspaces()),
            ("/api/v1/orgs/current", "200 OK", current_org()),
        ]);

        let target = client_for(base, Some("ws-2")).describe_deploy_target();

        assert_eq!(target.organization.as_deref(), Some("LangChain Inc."));
        assert_eq!(
            target.workspace.as_ref().map(WorkspaceSummary::label),
            Some("Development")
        );
    }

    #[test]
    fn a_credential_that_sees_one_workspace_names_it_unscoped() {
        let base = serve_paths(vec![
            (
                "/api/v1/workspaces",
                "200 OK",
                json!([{ "id": "ws-1", "display_name": "Production" }]).to_string(),
            ),
            ("/api/v1/orgs/current", "200 OK", current_org()),
        ]);

        let target = client_for(base, None).describe_deploy_target();

        assert_eq!(
            target.workspace.as_ref().map(WorkspaceSummary::label),
            Some("Production")
        );
    }

    #[test]
    fn an_unscoped_credential_that_sees_several_workspaces_names_none() {
        // The host backend resolves the credential's default workspace itself,
        // so naming one of these would be a guess. The organization still holds.
        let base = serve_paths(vec![
            ("/api/v1/workspaces", "200 OK", two_workspaces()),
            ("/api/v1/orgs/current", "200 OK", current_org()),
        ]);

        let target = client_for(base, None).describe_deploy_target();

        assert_eq!(target.workspace, None);
        assert_eq!(target.organization.as_deref(), Some("LangChain Inc."));
    }

    #[test]
    fn a_scoped_workspace_the_credential_cannot_list_keeps_its_id() {
        let base = serve_paths(vec![
            ("/api/v1/workspaces", "403 Forbidden", "{}".to_string()),
            ("/api/v1/orgs/current", "403 Forbidden", "{}".to_string()),
        ]);

        let target = client_for(base, Some("ws-9")).describe_deploy_target();

        assert_eq!(target.organization, None);
        assert_eq!(
            target.workspace.as_ref().map(WorkspaceSummary::label),
            Some("ws-9")
        );
    }

    #[test]
    fn a_workspace_named_later_comes_from_the_same_listing() {
        // The unscoped case above leaves `workspace` unset; the id the host
        // backend later reports still resolves without a second lookup.
        let base = serve_paths(vec![
            ("/api/v1/workspaces", "200 OK", two_workspaces()),
            ("/api/v1/orgs/current", "200 OK", current_org()),
        ]);

        let target = client_for(base, None).describe_deploy_target();

        assert_eq!(target.label_for("ws-1"), "Production");
        assert_eq!(target.label_for("ws-unlisted"), "ws-unlisted");
    }

    #[test]
    fn a_scoped_client_answers_with_its_own_workspace() {
        let client = client_for("http://unused.invalid".into(), Some("ws-1"));
        assert_eq!(client.workspace_id_for_links().as_deref(), Some("ws-1"));
    }

    #[test]
    fn a_workspace_without_a_name_is_labeled_by_its_id() {
        let workspace = decode_workspace(&json!({ "id": "ws-1", "display_name": "  " }))
            .expect("workspace decodes");

        assert_eq!(workspace.display_name, None);
        assert_eq!(workspace.label(), "ws-1");
    }

    #[test]
    fn a_workspace_without_an_id_is_not_a_workspace() {
        assert_eq!(
            decode_workspace(&json!({ "display_name": "Production" })),
            None
        );
    }

    #[test]
    fn dashboard_url_uses_the_authoritative_byoc_base() {
        let base = serve_paths(vec![(
            "/v1/platform/workspaces/current/info",
            "200 OK",
            json!({ "dashboard_url": "https://aws.smith.langchain.com/" }).to_string(),
        )]);

        assert_eq!(
            client_for(base, Some("ws-1")).dashboard_url().unwrap(),
            "https://aws.smith.langchain.com"
        );
    }

    #[test]
    fn dashboard_url_preserves_a_self_hosted_base_path() {
        assert_eq!(
            normalize_dashboard_url("https://corp.example.com/langsmith/").unwrap(),
            "https://corp.example.com/langsmith"
        );
    }

    #[test]
    fn dashboard_url_rejects_query_data() {
        assert!(normalize_dashboard_url("https://smith.example.com/?tenant=other").is_err());
    }
}
