//! Fleet agent endpoints read by `mda init --from`.

use std::collections::BTreeSet;

use anyhow::{bail, Context, Result};
use reqwest::{Method, StatusCode, Url};
use serde_json::{json, Value};

use super::{decode_json_body, ApiError, Client};

/// MCP revision of the `initialize` request for Managed Tools servers.
const MCP_PROTOCOL_VERSION: &str = "2025-03-26";

const MCP_SESSION_HEADER: &str = "mcp-session-id";

/// Without this opt-in the agent response omits `middleware`, and an agent
/// with a guardrail would export as if it had none.
const BETA_HEADER: (&str, &str) = ("LangSmith-Beta", "configurable-middleware-2026-07-13");

/// Largest page the schedules endpoint serves.
const SCHEDULES_PAGE_SIZE: &str = "100";

impl Client {
    /// The agent at its latest revision, with its raw file map, and whether
    /// Fleet confirmed the middleware beta (`Vary: LangSmith-Beta`). Fleet
    /// omits `middleware` both when there is none and when it ignores the
    /// beta, so only the header tells them apart.
    pub(crate) fn fleet_agent(&self, agent_id: &str) -> Result<(Value, bool)> {
        let path = format!("/v1/fleet/agents/{agent_id}");
        let mut request = self
            .http
            .request(
                Method::GET,
                format!("{}{path}", self.public_platform_base()?),
            )
            .query(&[("include", "files")])
            .header("Accept", "application/json")
            .header(BETA_HEADER.0, BETA_HEADER.1);
        for (name, value) in self.auth_headers() {
            request = request.header(name, value);
        }
        let response = request
            .send()
            .with_context(|| format!("GET {path} request failed"))?;
        let status = response.status();
        let beta_confirmed = response
            .headers()
            .get_all("vary")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .flat_map(|value| value.split(','))
            .any(|name| name.trim().eq_ignore_ascii_case(BETA_HEADER.0));
        let text = response
            .text()
            .with_context(|| format!("failed to read response body from {path}"))?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        Ok((decode_json_body(&text, &path)?, beta_confirmed))
    }

    /// The workspace's MCP servers. For a caller who may invoke them this
    /// includes header values, so callers must not persist the raw response.
    pub(crate) fn fleet_mcp_servers(&self) -> Result<Vec<Value>> {
        let path = "/v1/fleet/mcp-servers";
        let value = self.request(&self.public_platform_base()?, Method::GET, path, &[], None)?;
        match value {
            Value::Array(items) => Ok(items),
            _ => bail!("Fleet MCP servers response is not a list"),
        }
    }

    /// The tools on a Managed Tools server that ask before they run by
    /// default. Fleet keeps this per tool in the server's `tools/list`
    /// (`_meta.default_interrupt`), not on the agent, so an agent's
    /// `interrupt_config` omits it.
    pub(crate) fn managed_tools_ask_first(&self, url: &str) -> Result<BTreeSet<String>> {
        // The API key only goes to this LangSmith installation: same scheme, host and port.
        let origin = |url: &str| {
            Url::parse(url)
                .ok()
                .map(|url| url.origin())
                .filter(|origin| origin.is_tuple())
        };
        if origin(url).is_none() || origin(url) != origin(self.platform_base()) {
            bail!("{url} is not on this LangSmith installation");
        }
        let (_, session) = self.mcp_call(
            url,
            None,
            json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": { "name": "mda", "version": env!("CARGO_PKG_VERSION") },
                },
            }),
        )?;
        let session = session.as_deref();
        self.mcp_call(
            url,
            session,
            json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }),
        )?;
        let mut ask_first = BTreeSet::new();
        let mut cursor: Option<String> = None;
        loop {
            let params = match &cursor {
                Some(cursor) => json!({ "cursor": cursor }),
                None => json!({}),
            };
            let (response, _) = self.mcp_call(
                url,
                session,
                json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params }),
            )?;
            let result = response
                .get("result")
                .context("MCP tools/list returned no result")?;
            for tool in result
                .get("tools")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let asks = tool.pointer("/_meta/default_interrupt") == Some(&Value::Bool(true));
                if let (true, Some(name)) = (asks, tool.get("name").and_then(Value::as_str)) {
                    ask_first.insert(name.to_string());
                }
            }
            // An empty or repeated cursor would refetch the same page forever.
            match result.get("nextCursor").and_then(Value::as_str) {
                Some(next) if !next.is_empty() && cursor.as_deref() != Some(next) => {
                    cursor = Some(next.to_string())
                }
                _ => return Ok(ask_first),
            }
        }
    }

    /// One MCP Streamable HTTP request: the JSON-RPC response (`Null` for a
    /// notification) and the session id the server assigned, if any.
    fn mcp_call(
        &self,
        url: &str,
        session: Option<&str>,
        body: Value,
    ) -> Result<(Value, Option<String>)> {
        let mut request = self
            .http
            .post(url)
            .header("Accept", "application/json, text/event-stream")
            .json(&body);
        for (name, value) in self.auth_headers() {
            request = request.header(name, value);
        }
        if let Some(session) = session {
            request = request.header(MCP_SESSION_HEADER, session);
        }
        let response = request
            .send()
            .with_context(|| format!("POST {url} request failed"))?;
        let status = response.status();
        let session = response
            .headers()
            .get(MCP_SESSION_HEADER)
            .and_then(|value| value.to_str().ok())
            .map(str::to_string);
        let text = response
            .text()
            .with_context(|| format!("failed to read response body from {url}"))?;
        if !status.is_success() {
            bail!(ApiError::from_response(status, &text));
        }
        if text.trim().is_empty() {
            return Ok((Value::Null, session));
        }
        // A server may answer as a one-event stream instead of plain JSON.
        let json = text
            .lines()
            .filter_map(|line| line.strip_prefix("data:"))
            .next_back()
            .unwrap_or(&text);
        Ok((decode_json_body(json, url)?, session))
    }

    /// One access profile (a sandbox policy) from `backend.sandbox_config.policy_ids`,
    /// or `None` when the caller can't read it. Fleet lets a profile be deleted
    /// while agents still list it.
    pub(crate) fn fleet_access_profile(&self, profile_id: &str) -> Result<Option<Value>> {
        let path = format!("/v1/fleet/access-profiles/{profile_id}");
        match self.request(&self.public_platform_base()?, Method::GET, &path, &[], None) {
            Ok(profile) => Ok(Some(profile)),
            Err(err) => match err.downcast_ref::<ApiError>().map(|api| api.status) {
                Some(StatusCode::NOT_FOUND | StatusCode::FORBIDDEN) => Ok(None),
                _ => Err(err),
            },
        }
    }

    /// Every cron schedule of the agent, across pages.
    pub(crate) fn fleet_agent_schedules(&self, agent_id: &str) -> Result<Vec<Value>> {
        let path = format!("/v1/fleet/agents/{agent_id}/schedules");
        let mut schedules = Vec::new();
        let mut cursor: Option<String> = None;
        let base = self.public_platform_base()?;
        loop {
            let mut params = vec![("page_size", SCHEDULES_PAGE_SIZE)];
            if let Some(cursor) = cursor.as_deref() {
                params.push(("cursor", cursor));
            }
            let page = self.request(&base, Method::GET, &path, &params, None)?;
            let items = page
                .get("items")
                .and_then(Value::as_array)
                .context("Fleet schedules response has no items array")?;
            schedules.extend(items.iter().cloned());
            // An empty or repeated cursor would refetch the same page forever.
            match page.get("next_cursor").and_then(Value::as_str) {
                Some(next) if !next.is_empty() && cursor.as_deref() != Some(next) => {
                    cursor = Some(next.to_string())
                }
                _ => return Ok(schedules),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread::{self, JoinHandle};

    use reqwest::Url;
    use serde_json::{json, Value};

    use crate::langsmith::Client;

    fn serve_all(responses: Vec<Value>) -> (Client, JoinHandle<Vec<String>>) {
        serve_with_headers(responses, "")
    }

    /// Answers each request with the next body, plus `headers` (each line
    /// ending in `\r\n`).
    fn serve_with_headers(
        responses: Vec<Value>,
        headers: &'static str,
    ) -> (Client, JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let handle = thread::spawn(move || {
            responses
                .into_iter()
                .map(|body| {
                    let (mut stream, _) = listener.accept().unwrap();
                    let mut request = [0; 4096];
                    let n = stream.read(&mut request).unwrap();
                    let body = body.to_string();
                    write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}", body.len()).unwrap();
                    String::from_utf8_lossy(&request[..n]).to_lowercase()
                })
                .collect()
        });
        (Client::for_test(&url), handle)
    }

    #[test]
    fn fleet_agent_asks_for_files_and_middleware() {
        let (client, requests) = serve_with_headers(
            vec![json!({ "id": "a1", "name": "Agent" })],
            "Vary: Accept-Encoding, LangSmith-Beta\r\n",
        );

        let (agent, beta_confirmed) = client.fleet_agent("a1").unwrap();
        assert_eq!(agent["name"], "Agent");
        assert!(beta_confirmed);
        let request = requests.join().unwrap().remove(0);
        assert!(request.starts_with("get /v1/fleet/agents/a1?include=files "));
        assert!(request.contains("langsmith-beta: configurable-middleware-2026-07-13"));
        assert!(request.contains("x-tenant-id: tenant"));
    }

    #[test]
    fn self_hosted_fleet_reads_use_the_public_api_prefix() {
        let (client, requests) = serve_all(vec![
            json!({ "id": "a1" }),
            json!([]),
            json!({ "id": "p1" }),
            json!({ "items": [] }),
        ]);
        let client = Client {
            host_backend_url: format!("{}/api-host", client.platform_base()),
            ..client
        };

        client.fleet_agent("a1").unwrap();
        client.fleet_mcp_servers().unwrap();
        client.fleet_access_profile("p1").unwrap();
        client.fleet_agent_schedules("a1").unwrap();

        let requests = requests.join().unwrap();
        assert!(requests[0].starts_with("get /api/v1/fleet/agents/a1?include=files "));
        assert!(requests[1].starts_with("get /api/v1/fleet/mcp-servers "));
        assert!(requests[2].starts_with("get /api/v1/fleet/access-profiles/p1 "));
        assert!(requests[3].starts_with("get /api/v1/fleet/agents/a1/schedules?page_size=100 "));
    }

    #[test]
    fn managed_tools_ask_first_reads_default_interrupt_from_tools_list() {
        let (client, requests) = serve_with_headers(
            vec![
                json!({ "jsonrpc": "2.0", "id": 1, "result": {} }),
                Value::Null,
                json!({ "jsonrpc": "2.0", "id": 2, "result": { "tools": [
                    { "name": "send_email", "_meta": { "default_interrupt": true } },
                    { "name": "read_emails", "_meta": { "default_interrupt": false } },
                    { "name": "list_labels" },
                ] } }),
            ],
            "mcp-session-id: s1\r\n",
        );
        let url = format!(
            "{}/v1/managed-tools/servers/gmail/mcp",
            client.platform_base()
        );

        let ask_first = client.managed_tools_ask_first(&url).unwrap();

        assert_eq!(ask_first.into_iter().collect::<Vec<_>>(), ["send_email"]);
        let requests = requests.join().unwrap();
        assert!(requests[0].contains("\"method\":\"initialize\""));
        assert!(requests[2].contains("\"method\":\"tools/list\""));
        assert!(requests[2].contains("mcp-session-id: s1"));
        assert!(requests[2].contains("x-api-key: key"));
    }

    #[test]
    fn managed_tools_ask_first_keeps_the_key_on_this_installation() {
        let client = Client::for_test("https://api.smith.langchain.com");
        let path = "/v1/managed-tools/servers/gmail/mcp";
        // This installation's own host, but over plain HTTP or on another port.
        let mut plain = Url::parse(client.platform_base()).unwrap();
        plain.set_scheme("http").unwrap();
        let mut other_port = Url::parse(client.platform_base()).unwrap();
        other_port.set_port(Some(8443)).unwrap();

        for url in [
            format!("https://evil.example{path}"),
            plain.join(path).unwrap().to_string(),
            other_port.join(path).unwrap().to_string(),
        ] {
            let err = client.managed_tools_ask_first(&url).unwrap_err();
            assert!(
                err.to_string()
                    .contains("not on this LangSmith installation"),
                "{url}"
            );
        }
    }

    #[test]
    fn fleet_agent_without_the_beta_vary_header_is_unconfirmed() {
        let (client, _) = serve_all(vec![json!({ "id": "a1", "name": "Agent" })]);

        assert!(!client.fleet_agent("a1").unwrap().1);
    }

    #[test]
    fn fleet_agent_schedules_follows_every_page() {
        let (client, requests) = serve_all(vec![
            json!({ "items": [{ "cron": "0 9 * * *" }], "next_cursor": "c2" }),
            json!({ "items": [{ "cron": "0 17 * * *" }], "next_cursor": null }),
        ]);

        let schedules = client.fleet_agent_schedules("a1").unwrap();
        assert_eq!(schedules.len(), 2);
        let requests = requests.join().unwrap();
        assert!(requests[0].starts_with("get /v1/fleet/agents/a1/schedules?page_size=100 "));
        assert!(requests[1].contains("cursor=c2"));
    }

    #[test]
    fn fleet_agent_schedules_stops_on_an_empty_or_repeated_cursor() {
        for last in [json!(""), json!("c2")] {
            let (client, requests) = serve_all(vec![
                json!({ "items": [{ "cron": "0 9 * * *" }], "next_cursor": "c2" }),
                json!({ "items": [{ "cron": "0 17 * * *" }], "next_cursor": last }),
            ]);

            assert_eq!(client.fleet_agent_schedules("a1").unwrap().len(), 2);
            assert_eq!(requests.join().unwrap().len(), 2);
        }
    }

    #[test]
    fn fleet_access_profile_reads_one_profile() {
        let (client, requests) = serve_all(vec![json!({ "id": "p1", "rules": [] })]);

        assert_eq!(
            client.fleet_access_profile("p1").unwrap().unwrap()["id"],
            "p1"
        );
        assert!(requests.join().unwrap()[0].starts_with("get /v1/fleet/access-profiles/p1 "));
    }

    #[test]
    fn fleet_mcp_servers_reads_the_workspace_list() {
        let (client, requests) = serve_all(vec![
            json!([{ "name": "Notion", "url": "https://mcp.notion.com/mcp" }]),
        ]);

        let servers = client.fleet_mcp_servers().unwrap();
        assert_eq!(servers.len(), 1);
        assert!(requests.join().unwrap()[0].starts_with("get /v1/fleet/mcp-servers "));
    }
}
