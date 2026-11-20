//! Fleet tools become MCP servers in `tools/mcp.*`, one per server URL.

mod approvals;

use std::collections::{BTreeMap, BTreeSet};

use reqwest::Url;

use crate::fleet_export::report::ConnectionToCreate;
use crate::fleet_export::snapshot::{Identity, McpAuthType, ToolEntry, WorkspaceMcpServer};
use crate::fleet_export::{Export, FleetAgent};
use crate::init::{Connection, ConnectionOwner, McpDefinition, McpServer, AGENT_MCP};
use crate::naming::canonical_name;
use crate::project::Language;

/// Path segments of Managed Tools URLs: `/v1/...` on SaaS, `/api/v1/...` on
/// single-origin self-hosted. Fleet writes server URLs itself; a kit (or its
/// old `gateways` name) reaches an agent as a custom MCP server.
const MANAGED_TOOLS_ROUTES: [&str; 3] = [
    "/v1/managed-tools/servers/",
    "/v1/managed-tools/kits/",
    "/v1/managed-tools/gateways/",
];

/// Fleet's own tool server. Only Fleet can call it.
const FLEET_TOOL_SERVER_HOST: &str = "tools.langchain.com";

/// Longest connection slug Agent Auth accepts; server keys double as slugs.
const MAX_KEY_LEN: usize = 64;

/// Longest tool name some model providers accept.
const MAX_TOOL_NAME_LEN: usize = 64;

/// The tools `tools.json` lists for one MCP server URL.
#[derive(Default)]
struct Group<'a> {
    /// The URL as Fleet stored it, written out unchanged.
    url: &'a str,
    /// Fleet's display name for the server, the first one any tool gave.
    server_name: Option<&'a str>,
    tools: BTreeSet<&'a str>,
}

/// Turns the agent's `tools.json` into the `mcp` definition, and each
/// subagent's into its `mcp`. Fleet gives a subagent only its own tools, so
/// each list keeps its own:
///
/// | A subagent's exported servers      | `mcp`                                  |
/// |------------------------------------|----------------------------------------|
/// | none                               | `[]`                                   |
/// | the agent's, with the same tools   | `[mcp]`                                |
/// | anything else                      | its own definition in `tools/mcp`      |
///
/// For each list, the agent's first:
///
/// 1. Group tools by server URL (see [`group_by_url`]).
/// 2. Turn each URL into one server, the first of these that fits. A URL
///    another list already turned into a server reuses it, so the agent and
///    its subagents share one key and connection per server.
///    - Managed Tools: written as is, with no connection. LangSmith signs the
///      caller in when a tool first runs.
///    - A server in the workspace's Fleet MCP server list: written with the
///      connection its Fleet auth maps to (see [`connection`]). One whose
///      connection is agent-owned is written commented out, since that
///      connection can only be created after the first deploy.
///    - Anything else: export can't tell how it signs in, so it isn't
///      exported.
/// 3. Carry over the list's approval rules (see [`approvals`]) and list each
///    new connection under "Connections to create".
///
/// Runs after the subagents mapper, which adds the subagents this fills in.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    out.claimed_files.insert("tools.json".to_string());
    for spec in &agent.subagents {
        out.claimed_files
            .insert(format!("subagents/{}/tools.json", spec.name));
    }
    let shared = agent
        .permissions
        .as_ref()
        .is_some_and(|permissions| permissions.identity == Identity::Shared);
    let mut keys = BTreeSet::new();
    // Keyed like `group_by_url`; `None` for a URL export can't sign in to.
    let mut built: BTreeMap<String, Option<McpServer>> = BTreeMap::new();
    // Too-long names already warned about.
    let mut warned_names = BTreeSet::new();
    let mut agent_servers = Vec::new();
    let mut definition_stems = BTreeSet::new();
    let mut subagent_unmatched = Vec::new();
    // Managed Tools servers whose default approvals couldn't be read, warned once.
    let mut unread_defaults = BTreeSet::new();
    let lists = std::iter::once((None, agent.tools.as_ref())).chain(
        agent
            .subagents
            .iter()
            .map(|spec| (Some(spec.name.as_str()), spec.tools.as_ref())),
    );
    for (index, (subagent, config)) in lists.enumerate() {
        let entries = config.map(|config| config.tools.as_slice()).unwrap_or(&[]);
        let (by_url, builtin) = group_by_url(entries);
        // A subagent's are named, since the subagent itself is still exported.
        let (summary, owner) = match subagent {
            Some(name) => (
                format!("subagent `{name}`: "),
                format!(" in subagent `{name}`"),
            ),
            None => (String::new(), String::new()),
        };
        if !builtin.is_empty() {
            let builtin: Vec<&str> = builtin.into_iter().collect();
            out.report.skip(
                "tools",
                format!("{summary}{} Fleet built-in tools", builtin.len()),
                format!(
                    "Fleet built-in tools{owner} aren't supported yet: {}",
                    builtin.join(", ")
                ),
            );
        }

        let mut servers = Vec::new();
        let mut unknown = Vec::new();
        let mut exported = Vec::new();
        for (normalized, group) in &by_url {
            let server = match built.get(normalized) {
                Some(server) => server.clone(),
                None => {
                    let server = build_server(agent, normalized, group, shared, &mut keys, out);
                    if let Some((server, step)) = &server {
                        if let Some(connection) = &server.connection {
                            report_connection(
                                agent,
                                server,
                                &connection.slug,
                                step.as_deref(),
                                out,
                            );
                        }
                        if let Some(step) = step {
                            out.report
                                .after_first_deploy
                                .push(format!("{}: {step}", server.key));
                        }
                    }
                    let server = server.map(|(server, _)| server);
                    built.insert(normalized.clone(), server.clone());
                    server
                }
            };
            let Some(server) = server else {
                unknown.push(group.url);
                continue;
            };
            out.report.long_tool_names.extend(
                group
                    .tools
                    .iter()
                    .map(|tool| format!("{}_{tool}", server.key))
                    .filter(|name| name.len() > MAX_TOOL_NAME_LEN)
                    .filter(|name| warned_names.insert(name.clone())),
            );
            exported.push(approvals::Server {
                key: server.key.clone(),
                url: normalized.clone(),
                tools: group.tools.iter().copied().collect(),
            });
            servers.push(McpServer {
                include_tools: include_tools(group),
                ..server
            });
        }
        if !unknown.is_empty() {
            out.report.skip(
                "MCP servers",
                format!("{summary}{} not in this workspace", unknown.len()),
                format!(
                    "these servers{owner} aren't in the workspace's MCP server list, so export can't tell how they sign in: {}",
                    unknown.join(", ")
                ),
            );
        }

        let (interrupt_on, unmatched) =
            approvals::rules(config, &exported, &agent.managed_tools_ask_first);
        for server in &exported {
            if matches!(agent.managed_tools_ask_first.get(&server.url), Some(None))
                && unread_defaults.insert(server.key.clone())
            {
                approvals::warn_unread_defaults(&server.key, out);
            }
        }
        match subagent {
            Some(name) => subagent_unmatched
                .extend(unmatched.into_iter().map(|tool| format!("{tool} ({name})"))),
            None if !exported.is_empty() && !unmatched.is_empty() => {
                out.report.warn(format!(
                    "Approvals: Fleet has approval rules for tools export didn't carry over: {}",
                    unmatched.join(", ")
                ));
            }
            None => {}
        }

        let Some(name) = subagent else {
            if !servers.is_empty() {
                out.blueprint.interrupt_on = interrupt_on;
                agent_servers = servers.clone();
                out.blueprint.mcp_definitions.push(McpDefinition {
                    name: AGENT_MCP.to_string(),
                    servers,
                });
            }
            continue;
        };
        let mcp = if servers.is_empty() {
            Vec::new()
        } else if servers == agent_servers {
            vec![AGENT_MCP.to_string()]
        } else {
            let definition = format!(
                "{}_subagent_mcp",
                unique_definition_stem(&mut definition_stems, name)
            );
            out.blueprint.mcp_definitions.push(McpDefinition {
                name: definition.clone(),
                servers,
            });
            vec![definition]
        };
        // The subagents mapper added one entry per Fleet subagent, in order.
        let entry = &mut out.blueprint.subagents[index - 1];
        entry.mcp = mcp;
        entry.interrupt_on = interrupt_on;
    }
    if !subagent_unmatched.is_empty() {
        out.report.warn(format!(
            "Approvals: Fleet has subagent approval rules for tools export didn't carry over: {}",
            subagent_unmatched.join(", ")
        ));
    }
    if !out.report.long_tool_names.is_empty() {
        out.report.warn(format!(
            "Tools: {} tool names are longer than {MAX_TOOL_NAME_LEN} characters, which some model providers reject. Shorten their server names in tools/mcp; \"Tool names to shorten\" in FLEET_EXPORT.md lists them.",
            out.report.long_tool_names.len()
        ));
    }
    if !out.report.after_first_deploy.is_empty() {
        out.report.after_first_deploy.push(
            "Then uncomment those servers in tools/mcp and run `mda deploy` again".to_string(),
        );
    }
}

/// The Managed Tools server URLs the agent's and its subagents' tools use,
/// without a trailing slash, as [`FleetAgent::managed_tools_ask_first`] keys
/// them.
pub(crate) fn managed_tools_urls(agent: &FleetAgent) -> BTreeSet<String> {
    std::iter::once(agent.tools.as_ref())
        .chain(agent.subagents.iter().map(|spec| spec.tools.as_ref()))
        .flatten()
        .flat_map(|config| &config.tools)
        .filter_map(|entry| match entry {
            ToolEntry::Spec(spec) => spec.mcp_server_url.as_deref(),
            ToolEntry::Name(_) => None,
        })
        .map(|url| url.trim().trim_end_matches('/').to_string())
        .filter(|url| managed_tools_id(url).is_some())
        .collect()
}

/// One list's tools by MCP server URL, without a trailing slash as Fleet
/// compares them, and sorted so server keys come out the same on every run.
/// Also the Fleet built-in tools: those without a URL or on Fleet's own tool
/// server. Only Fleet can call them, so they aren't exported.
fn group_by_url(entries: &[ToolEntry]) -> (BTreeMap<String, Group<'_>>, BTreeSet<&str>) {
    let mut by_url: BTreeMap<String, Group> = BTreeMap::new();
    let mut builtin = BTreeSet::new();
    for entry in entries {
        let (name, url, server_name) = match entry {
            ToolEntry::Name(name) => (name.as_str(), None, None),
            ToolEntry::Spec(spec) => (
                spec.name.as_deref().unwrap_or_default(),
                spec.mcp_server_url.as_deref(),
                spec.mcp_server_name.as_deref(),
            ),
        };
        match url.map(str::trim).filter(|url| !url.is_empty()) {
            Some(url) if !is_fleet_tool_server(url) && !name.is_empty() => {
                let group = by_url
                    .entry(url.trim_end_matches('/').to_string())
                    .or_insert_with(|| Group {
                        url,
                        ..Group::default()
                    });
                group.tools.insert(name);
                group.server_name = group
                    .server_name
                    .or(server_name.filter(|server_name| !server_name.is_empty()));
            }
            _ => {
                builtin.insert(if name.is_empty() { "(unnamed)" } else { name });
            }
        }
    }
    (by_url, builtin)
}

/// A server for one URL, with the step that creates its connection after the
/// first deploy when that connection is agent-owned, or `None` when export
/// can't tell how the server signs in.
fn build_server(
    agent: &FleetAgent,
    normalized: &str,
    group: &Group,
    shared: bool,
    keys: &mut BTreeSet<String>,
    out: &mut Export,
) -> Option<(McpServer, Option<String>)> {
    let url = group.url.to_string();
    if let Some(id) = managed_tools_id(normalized) {
        // Managed Tools signs the caller in itself, so no connection, even
        // when Fleet stored a kit with an API key header. Keyed by its id
        // (`gmail`), or its display name when the id is a UUID.
        let base = if is_readable_id(&id) {
            id
        } else {
            slugify(group.server_name.unwrap_or("managed-tools"))
        };
        return Some((
            McpServer {
                key: unique_key(keys, &base),
                url,
                connection: None,
                langsmith_auth: true,
                include_tools: Vec::new(),
                commented_out: None,
            },
            None,
        ));
    }
    // Not Managed Tools and not in the workspace list (for example, removed
    // from the workspace since): export can't tell how it signs in.
    let workspace = workspace_server(agent, normalized)?;
    // A server someone added to the workspace in Fleet. Its auth type and
    // header names decide the connection. Keyed by its name, which doubles
    // as the connection slug.
    let name = Some(workspace.name.clone())
        .filter(|name| !name.is_empty())
        .or(group.server_name.map(str::to_string))
        .or_else(|| host(&url))
        .unwrap_or_else(|| "mcp".to_string());
    // The key doubles as the connection slug, so it skips workspace
    // connections it must not use: only an OAuth one can be reused for a
    // user-owned server (any other kind would send its secret to this URL),
    // and an agent-owned one is always created new.
    let owner = connection_owner(workspace, shared);
    let key = unique_key_avoiding(keys, &slugify(&name), |slug| {
        agent.workspace_connections.iter().any(|existing| {
            existing.slug == slug
                && match owner {
                    Some(ConnectionOwner::User) => existing.kind != "oauth2",
                    Some(ConnectionOwner::Agent) => true,
                    None => false,
                }
        })
    });
    let (connection, step) = match connection(workspace, &key, &url, shared, out) {
        Some((connection, step)) => (Some(connection), step),
        None => (None, None),
    };
    let commented_out = step.as_ref().map(|step| {
        format!(
            "After your first `mda deploy`, {step}.\nThen uncomment this server and deploy again."
        )
    });
    Some((
        McpServer {
            key,
            url,
            connection,
            langsmith_auth: false,
            include_tools: Vec::new(),
            commented_out,
        },
        step,
    ))
}

/// The connection a workspace server signs in with, and for an agent-owned
/// one, the step that creates it after the first deploy.
///
/// | Fleet auth             | Fleet agent | Connection  | In tools/mcp      |
/// |------------------------|-------------|-------------|-------------------|
/// | OAuth                  | personal    | user-owned  | active            |
/// | OAuth                  | shared      | agent-owned | commented out     |
/// | `Authorization` header | either      | agent-owned | commented out     |
/// | other headers / none   | either      | none        | active            |
///
/// A user-owned connection is created by `mda deploy` through the server's
/// OAuth discovery. An agent-owned one has to exist before `mda deploy`, but
/// creating it needs the deployment, hence the step and the commented-out
/// server. Headers other than `Authorization` are warned about, not carried.
fn connection(
    server: &WorkspaceMcpServer,
    slug: &str,
    url: &str,
    shared: bool,
    out: &mut Export,
) -> Option<(Connection, Option<String>)> {
    let is_authorization = |name: &&str| name.eq_ignore_ascii_case("authorization");
    let header_names: Vec<&str> = server
        .headers
        .iter()
        .map(|header| header.key.as_str())
        .collect();
    let other_headers: Vec<&str> = header_names
        .iter()
        .copied()
        .filter(|name| !is_authorization(name))
        .collect();
    if !other_headers.is_empty() {
        out.report.warn(format!(
            "MCP server `{slug}` ({url}) needs headers export can't set up: {}. Add them to tools/mcp yourself before you deploy.",
            other_headers.join(", ")
        ));
    }
    let owner = connection_owner(server, shared)?;
    let after_deploy = match (owner, server.auth_type) {
        (ConnectionOwner::User, _) => None,
        // A shared Fleet agent signs in to one account for every caller.
        (ConnectionOwner::Agent, McpAuthType::Oauth) => Some(format!(
            "run `mda connections create {slug} --mcp {url} --authorize` and sign in to the account every caller shares"
        )),
        // An MCP connection is sent as `Authorization: Bearer <value>`.
        (ConnectionOwner::Agent, _) => {
            let env = token_env(slug);
            Some(format!(
                "set `{env}` to the token (without `Bearer `) and run `mda connections create {slug} --secret-from-env {env}`"
            ))
        }
    };
    Some((
        Connection {
            slug: slug.to_string(),
            owner,
        },
        after_deploy,
    ))
}

/// Who owns a workspace server's connection, or `None` when it needs none.
fn connection_owner(server: &WorkspaceMcpServer, shared: bool) -> Option<ConnectionOwner> {
    match server.auth_type {
        McpAuthType::Oauth if shared => Some(ConnectionOwner::Agent),
        McpAuthType::Oauth => Some(ConnectionOwner::User),
        _ if server
            .headers
            .iter()
            .any(|header| header.key.eq_ignore_ascii_case("authorization")) =>
        {
            Some(ConnectionOwner::Agent)
        }
        _ => None,
    }
}

/// Lists the connection under "Connections to create" with its steps, or
/// notes that an existing user-owned connection with the same slug is reused.
fn report_connection(
    agent: &FleetAgent,
    server: &McpServer,
    slug: &str,
    after_deploy: Option<&str>,
    out: &mut Export,
) {
    let url = &server.url;
    // A user-owned connection with this slug is reused as is. Agent-owned
    // ones are created per deployment, so they keep their step.
    let exists = after_deploy.is_none()
        && agent
            .workspace_connections
            .iter()
            .any(|connection| connection.slug == slug);
    if exists {
        // Nothing to create, so it isn't listed under "Connections to create".
        out.report.note(format!(
            "Connections: the agent uses your existing `{slug}` connection for {url}."
        ));
        return;
    }
    let item = match after_deploy {
        Some(step) => {
            let file = match out.blueprint.language {
                Language::Python => "tools/mcp.py",
                Language::TypeScript => "tools/mcp.ts",
            };
            ConnectionToCreate {
                slug: slug.to_string(),
                after_first_deploy: true,
                summary: format!(
                    "The agent owns this connection to {url}, and a connection the agent owns can only be created once the deployment exists, so the server is commented out in `{file}`."
                ),
                steps: vec![
                    "Deploy: `mda deploy`".to_string(),
                    capitalize(step),
                    format!("In `{file}`, uncomment the server `{slug}`."),
                    "Deploy again: `mda deploy`".to_string(),
                ],
            }
        }
        None => ConnectionToCreate {
            slug: slug.to_string(),
            after_first_deploy: false,
            summary: format!(
                "The agent signs in to {url} with each user's own account. `mda deploy` creates this connection through the server's OAuth discovery, and each user signs in the first time a tool needs it."
            ),
            steps: Vec::new(),
        },
    };
    out.report.connection(item);
}

fn capitalize(text: &str) -> String {
    let mut chars = text.chars();
    chars
        .next()
        .map(|first| first.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

fn include_tools(group: &Group) -> Vec<String> {
    group.tools.iter().map(|tool| tool.to_string()).collect()
}

/// A shell variable name for a server's token: letters, digits and
/// underscores, not starting with a digit.
fn token_env(slug: &str) -> String {
    let name: String = slug
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect();
    if name.starts_with(|c: char| c.is_ascii_digit()) {
        format!("MCP_{name}_TOKEN")
    } else {
        format!("{name}_TOKEN")
    }
}

fn workspace_server<'a>(agent: &'a FleetAgent, url: &str) -> Option<&'a WorkspaceMcpServer> {
    agent
        .workspace_mcp_servers
        .iter()
        .find(|server| server.url.trim().trim_end_matches('/') == url)
}

fn is_fleet_tool_server(url: &str) -> bool {
    host(url).is_some_and(|host| {
        host == FLEET_TOOL_SERVER_HOST || host.ends_with(&format!(".{FLEET_TOOL_SERVER_HOST}"))
    })
}

/// The server or kit id in a Managed Tools URL
/// (`.../v1/managed-tools/servers/<id>/mcp`, or `kits/<id>`).
fn managed_tools_id(url: &str) -> Option<String> {
    let parsed = Url::parse(url).ok()?;
    let path = parsed.path();
    let start = MANAGED_TOOLS_ROUTES
        .iter()
        .find_map(|route| Some(path.find(route)? + route.len()))?;
    let id = path[start..].strip_suffix("/mcp")?;
    (!id.is_empty() && !id.contains('/')).then(|| id.to_string())
}

/// Built-in Managed Tools servers have slug ids (`gmail`); servers people
/// register have UUIDs, which make poor tool name prefixes.
fn is_readable_id(id: &str) -> bool {
    uuid::Uuid::parse_str(id).is_err()
}

fn host(url: &str) -> Option<String> {
    Url::parse(url).ok()?.host_str().map(str::to_string)
}

/// Valid as a server key and as a connection slug.
fn slugify(text: &str) -> String {
    let slug = canonical_name(text);
    if slug.is_empty() {
        "mcp".to_string()
    } else {
        slug
    }
}

/// `base`, or `base-2`, `base-3`… when a different server already took it.
fn unique_key(used: &mut BTreeSet<String>, base: &str) -> String {
    unique_key_avoiding(used, base, |_| false)
}

/// [`unique_key`], also skipping keys `taken` rejects.
fn unique_key_avoiding(
    used: &mut BTreeSet<String>,
    base: &str,
    taken: impl Fn(&str) -> bool,
) -> String {
    // Leave room for a suffix within the slug limit.
    let base: String = base.chars().take(MAX_KEY_LEN - 3).collect();
    let base = base.trim_end_matches('-');
    let mut key = base.to_string();
    let mut n = 2;
    while used.contains(&key) || taken(&key) {
        key = format!("{base}-{n}");
        n += 1;
    }
    used.insert(key.clone());
    key
}

/// A unique snake_case name for a subagent's MCP definition, from its Fleet
/// name (letters, digits, spaces, `_` and `-`): `Web Researcher` becomes
/// `web_researcher`. One starting with a digit gets a `subagent_` prefix.
fn unique_definition_stem(used: &mut BTreeSet<String>, name: &str) -> String {
    let words: Vec<String> = name
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(str::to_ascii_lowercase)
        .collect();
    let mut base = words.join("_");
    if base.is_empty() {
        base = "subagent".to_string();
    } else if base.starts_with(|c: char| c.is_ascii_digit()) {
        base = format!("subagent_{base}");
    }
    let mut candidate = base.clone();
    let mut n = 2;
    while !used.insert(candidate.clone()) {
        candidate = format!("{base}_{n}");
        n += 1;
    }
    candidate
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_tools_urls_match_the_forms_fleet_writes() {
        assert_eq!(
            managed_tools_id("https://api.smith.langchain.com/v1/managed-tools/servers/gmail/mcp")
                .as_deref(),
            Some("gmail")
        );
        assert_eq!(
            managed_tools_id(
                "https://ls.example.com/api/v1/managed-tools/servers/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0/mcp"
            )
            .as_deref(),
            Some("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
        );
        for collection in ["kits", "gateways"] {
            assert_eq!(
                managed_tools_id(&format!(
                    "https://api.smith.langchain.com/v1/managed-tools/{collection}/k1/mcp"
                ))
                .as_deref(),
                Some("k1")
            );
        }
        assert_eq!(
            managed_tools_id("https://api.smith.langchain.com/v1/managed-tools/other/k1/mcp"),
            None
        );
        assert_eq!(managed_tools_id("https://mcp.notion.com/mcp"), None);
    }

    #[test]
    fn readable_ids_exclude_uuids() {
        assert!(is_readable_id("gmail"));
        assert!(is_readable_id("google_calendar"));
        assert!(!is_readable_id("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"));
    }

    #[test]
    fn fleet_tool_server_is_recognized() {
        assert!(is_fleet_tool_server("https://dev.tools.langchain.com"));
        assert!(is_fleet_tool_server("https://tools.langchain.com/mcp"));
        assert!(!is_fleet_tool_server("https://docs.langchain.com/mcp"));
    }

    #[test]
    fn keys_are_slugs_and_unique() {
        assert_eq!(slugify("LangChain Docs!"), "langchain-docs");
        assert_eq!(slugify("***"), "mcp");
        let mut used = BTreeSet::new();
        assert_eq!(unique_key(&mut used, "notion"), "notion");
        assert_eq!(unique_key(&mut used, "notion"), "notion-2");
        assert!(unique_key(&mut used, &"x".repeat(100)).len() <= MAX_KEY_LEN);
    }

    #[test]
    fn token_env_is_a_valid_shell_name() {
        assert_eq!(token_env("acme"), "ACME_TOKEN");
        assert_eq!(token_env("my-api"), "MY_API_TOKEN");
        assert_eq!(token_env("1password"), "MCP_1PASSWORD_TOKEN");
    }

    #[test]
    fn definition_stems_are_unique_snake_case() {
        let mut used = BTreeSet::new();
        assert_eq!(
            unique_definition_stem(&mut used, "Web Researcher"),
            "web_researcher"
        );
        assert_eq!(
            unique_definition_stem(&mut used, "web-researcher"),
            "web_researcher_2"
        );
        assert_eq!(
            unique_definition_stem(&mut used, "3d modeler"),
            "subagent_3d_modeler"
        );
        assert_eq!(unique_definition_stem(&mut used, "--"), "subagent");
    }
}
