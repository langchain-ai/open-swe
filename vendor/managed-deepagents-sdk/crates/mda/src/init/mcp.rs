//! Source text for a blueprint's MCP definitions (`tools/mcp.{ts,py}`) and
//! its `interrupt_on` entries in the agent file.
//!
//! The agent loads only `mcp`; each other definition loads only for the
//! subagents that select it.

use crate::project::Language;

use super::source::{in_comment, lit, owner};
use super::subagents::export_name;
use super::{InterruptRule, McpDefinition, McpServer, AGENT_MCP};
use std::collections::BTreeMap;

/// `tools/mcp.ts` or `tools/mcp.py`, or `None` with no definitions. `mcp` comes
/// first and is written even when only subagents have servers, since the
/// build requires it. Every server may be commented out, leaving a map empty.
pub(super) fn mcp_module(definitions: &[McpDefinition], language: Language) -> Option<String> {
    if definitions.is_empty() {
        return None;
    }
    // Commented-out servers count too, so uncommenting one just works.
    let uses_connections = definitions
        .iter()
        .flat_map(|definition| &definition.servers)
        .any(|server| server.connection.is_some());
    let mut out = match (language, uses_connections) {
        (Language::Python, true) => "from managed_deepagents import connections, define_mcp\n\n",
        (Language::Python, false) => "from managed_deepagents import define_mcp\n\n",
        (Language::TypeScript, true) => {
            "import { connections, defineMcp } from \"managed-deepagents\";\n\n"
        }
        (Language::TypeScript, false) => "import { defineMcp } from \"managed-deepagents\";\n\n",
    }
    .to_string();
    let agent = definitions
        .iter()
        .find(|definition| definition.name == AGENT_MCP)
        .map_or(&[][..], |definition| &definition.servers);
    out.push_str(&definition(AGENT_MCP, agent, language));
    for own in definitions
        .iter()
        .filter(|definition| definition.name != AGENT_MCP)
    {
        out.push('\n');
        out.push_str(&definition(
            &export_name(&own.name, language),
            &own.servers,
            language,
        ));
    }
    Some(out)
}

/// One exported `define_mcp` / `defineMcp` named `name`.
fn definition(name: &str, servers: &[McpServer], language: Language) -> String {
    let (open, close, comment) = match language {
        Language::Python => (
            format!("{name} = define_mcp(\n    servers={{\n"),
            "    },\n)\n",
            "# ",
        ),
        Language::TypeScript => (
            format!("export const {name} = defineMcp({{\n  servers: {{\n"),
            "  },\n});\n",
            "// ",
        ),
    };
    let mut out = open;
    for server in servers {
        let block = server_block(server, language);
        let Some(note) = &server.commented_out else {
            out.push_str(&block);
            continue;
        };
        let indent = match language {
            Language::Python => "        ",
            Language::TypeScript => "    ",
        };
        for line in note.lines() {
            out.push_str(&format!("{indent}{comment}{}\n", in_comment(line)));
        }
        for line in block.lines() {
            let rest = line.trim_start();
            let lead = &line[..line.len() - rest.len()];
            out.push_str(&format!("{lead}{comment}{}\n", in_comment(rest)));
        }
    }
    out.push_str(close);
    out
}

/// One `servers` entry, indented for its place in the module.
fn server_block(server: &McpServer, language: Language) -> String {
    let mut out = String::new();
    match language {
        Language::Python => {
            out.push_str(&format!("        {}: {{\n", lit(&server.key)));
            out.push_str("            \"transport\": \"http\",\n");
            out.push_str(&format!("            \"url\": {},\n", lit(&server.url)));
            if server.langsmith_auth {
                out.push_str("            \"auth\": \"langsmith\",\n");
            }
            if let Some(connection) = &server.connection {
                out.push_str(&format!(
                    "            \"connection\": connections.get({}, {{\"type\": \"{}\"}}),\n",
                    lit(&connection.slug),
                    owner(connection.owner)
                ));
            }
            out.push_str("            \"include_tools\": [\n");
            for tool in &server.include_tools {
                out.push_str(&format!("                {},\n", lit(tool)));
            }
            out.push_str("            ],\n        },\n");
        }
        Language::TypeScript => {
            out.push_str(&format!("    {}: {{\n", lit(&server.key)));
            out.push_str("      transport: \"http\",\n");
            out.push_str(&format!("      url: {},\n", lit(&server.url)));
            if server.langsmith_auth {
                out.push_str("      auth: \"langsmith\",\n");
            }
            if let Some(connection) = &server.connection {
                out.push_str(&format!(
                    "      connection: connections.get({}, {{ type: \"{}\" }}),\n",
                    lit(&connection.slug),
                    owner(connection.owner)
                ));
            }
            out.push_str("      includeTools: [\n");
            for tool in &server.include_tools {
                out.push_str(&format!("        {},\n", lit(tool)));
            }
            out.push_str("      ],\n    },\n");
        }
    }
    out
}

/// The `interrupt_on` / `interruptOn` argument lines for the agent file, or
/// nothing when no tool asks first.
pub(super) fn agent_options(
    interrupt_on: &BTreeMap<String, InterruptRule>,
    language: Language,
) -> String {
    interrupt_option(interrupt_on, 1, language)
}

/// An `interrupt_on` / `interruptOn` entry indented `depth` levels, or nothing
/// when no tool asks first. Python keys are quoted, as in a dict literal.
pub(super) fn interrupt_option(
    interrupt_on: &BTreeMap<String, InterruptRule>,
    depth: usize,
    language: Language,
) -> String {
    if interrupt_on.is_empty() {
        return String::new();
    }
    let step = match language {
        Language::Python => "    ",
        Language::TypeScript => "  ",
    };
    let outer = step.repeat(depth);
    let indent = step.repeat(depth + 1);
    let (open, yes, decisions) = match (language, depth) {
        (Language::Python, 1) => ("interrupt_on=", "True", "\"allowed_decisions\""),
        (Language::Python, _) => ("\"interrupt_on\": ", "True", "\"allowed_decisions\""),
        (Language::TypeScript, _) => ("interruptOn: ", "true", "allowedDecisions"),
    };
    let description = match language {
        Language::Python => "\"description\"",
        Language::TypeScript => "description",
    };
    let mut out = format!("{outer}{open}{{\n");
    for (tool, rule) in interrupt_on {
        let value = if rule.allowed_decisions.is_empty() && rule.description.is_none() {
            yes.to_string()
        } else {
            let mut fields = Vec::new();
            if !rule.allowed_decisions.is_empty() {
                let items: Vec<String> = rule.allowed_decisions.iter().map(|d| lit(d)).collect();
                fields.push(format!("{decisions}: [{}]", items.join(", ")));
            }
            if let Some(text) = &rule.description {
                fields.push(format!("{description}: {}", lit(text)));
            }
            match language {
                Language::Python => format!("{{{}}}", fields.join(", ")),
                Language::TypeScript => format!("{{ {} }}", fields.join(", ")),
            }
        };
        out.push_str(&format!("{indent}{}: {value},\n", lit(tool)));
    }
    out.push_str(&format!("{outer}}},\n"));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::init::{Connection, ConnectionOwner};

    fn agent(servers: Vec<McpServer>) -> Vec<McpDefinition> {
        vec![McpDefinition {
            name: AGENT_MCP.to_string(),
            servers,
        }]
    }

    fn servers() -> Vec<McpServer> {
        vec![
            McpServer {
                key: "gmail".to_string(),
                url: "https://example.com/v1/managed-tools/servers/gmail/mcp".to_string(),
                connection: None,
                langsmith_auth: true,
                include_tools: vec!["gmail_send_email".to_string()],
                commented_out: None,
            },
            McpServer {
                key: "notion".to_string(),
                url: "https://mcp.notion.com/mcp".to_string(),
                connection: Some(Connection {
                    slug: "notion".to_string(),
                    owner: ConnectionOwner::User,
                }),
                langsmith_auth: false,
                include_tools: vec!["search".to_string()],
                commented_out: None,
            },
        ]
    }

    #[test]
    fn python_module_declares_each_server() {
        assert_eq!(
            mcp_module(&agent(servers()), Language::Python).unwrap(),
            r#"from managed_deepagents import connections, define_mcp

mcp = define_mcp(
    servers={
        "gmail": {
            "transport": "http",
            "url": "https://example.com/v1/managed-tools/servers/gmail/mcp",
            "auth": "langsmith",
            "include_tools": [
                "gmail_send_email",
            ],
        },
        "notion": {
            "transport": "http",
            "url": "https://mcp.notion.com/mcp",
            "connection": connections.get("notion", {"type": "user"}),
            "include_tools": [
                "search",
            ],
        },
    },
)
"#
        );
    }

    #[test]
    fn typescript_module_declares_each_server() {
        assert_eq!(
            mcp_module(&agent(servers()), Language::TypeScript).unwrap(),
            r#"import { connections, defineMcp } from "managed-deepagents";

export const mcp = defineMcp({
  servers: {
    "gmail": {
      transport: "http",
      url: "https://example.com/v1/managed-tools/servers/gmail/mcp",
      auth: "langsmith",
      includeTools: [
        "gmail_send_email",
      ],
    },
    "notion": {
      transport: "http",
      url: "https://mcp.notion.com/mcp",
      connection: connections.get("notion", { type: "user" }),
      includeTools: [
        "search",
      ],
    },
  },
});
"#
        );
    }

    #[test]
    fn connections_is_imported_only_when_used() {
        let plain = vec![servers().remove(0)];
        assert!(mcp_module(&agent(plain.clone()), Language::Python)
            .unwrap()
            .starts_with("from managed_deepagents import define_mcp\n"));
        assert!(mcp_module(&agent(plain), Language::TypeScript)
            .unwrap()
            .starts_with("import { defineMcp } from"));
    }

    #[test]
    fn commented_out_servers_keep_their_note_and_block() {
        let mut servers = servers();
        servers[1].commented_out = Some("Deploy first, then uncomment.".to_string());
        let python = mcp_module(&agent(servers.clone()), Language::Python).unwrap();
        assert!(python.starts_with("from managed_deepagents import connections, define_mcp\n"));
        assert!(python.contains(
            "        # Deploy first, then uncomment.\n        # \"notion\": {\n            # \"transport\": \"http\",\n"
        ));
        let typescript = mcp_module(&agent(servers.clone()), Language::TypeScript).unwrap();
        assert!(typescript.contains("    // Deploy first, then uncomment.\n    // \"notion\": {\n"));

        servers[0].commented_out = Some("Later.".to_string());
        let python = mcp_module(&agent(servers.clone()), Language::Python).unwrap();
        assert!(python.contains("    servers={\n        # Later.\n        # \"gmail\": {\n"));
        assert_eq!(mcp_module(&[], Language::Python), None);
    }

    #[test]
    fn commented_out_servers_keep_fleet_text_inside_the_comment() {
        let mut servers = servers();
        servers[1].url = "https://x.dev/mcp?a=\u{2028}[run()]:{},\u{2029}\r//".to_string();
        servers[1].commented_out = Some(format!("Server {}.", servers[1].url));
        for language in [Language::Python, Language::TypeScript] {
            let module = mcp_module(&agent(servers.clone()), language).unwrap();
            assert!(!module.contains(['\r', '\u{2028}', '\u{2029}']), "{module}");
            assert!(
                module.contains(r"a=\u2028[run()]:{},\u2029\r//"),
                "{module}"
            );
        }
    }

    #[test]
    fn interrupt_options_escape_fleet_text() {
        let rules = BTreeMap::from([
            (
                "gmail_gmail_send_email".to_string(),
                InterruptRule::default(),
            ),
            (
                "notion_search".to_string(),
                InterruptRule {
                    allowed_decisions: vec!["approve".to_string(), "reject".to_string()],
                    description: Some("Say \"yes\"\nfirst".to_string()),
                },
            ),
        ]);
        assert_eq!(
            agent_options(&rules, Language::Python),
            "    interrupt_on={\n        \"gmail_gmail_send_email\": True,\n        \"notion_search\": {\"allowed_decisions\": [\"approve\", \"reject\"], \"description\": \"Say \\\"yes\\\"\\nfirst\"},\n    },\n"
        );
        assert_eq!(
            agent_options(&rules, Language::TypeScript),
            "  interruptOn: {\n    \"gmail_gmail_send_email\": true,\n    \"notion_search\": { allowedDecisions: [\"approve\", \"reject\"], description: \"Say \\\"yes\\\"\\nfirst\" },\n  },\n"
        );
        assert_eq!(agent_options(&BTreeMap::new(), Language::Python), "");
    }

    #[test]
    fn subagent_definitions_follow_mcp_even_when_the_agent_has_none() {
        let definitions = vec![McpDefinition {
            name: "web_researcher_subagent_mcp".to_string(),
            servers: servers(),
        }];
        let typescript = mcp_module(&definitions, Language::TypeScript).unwrap();
        assert!(typescript.starts_with(
            "import { connections, defineMcp } from \"managed-deepagents\";\n\nexport const mcp = defineMcp({\n  servers: {\n  },\n});\n\nexport const webResearcherSubagentMcp = defineMcp({\n"
        ));
        let python = mcp_module(&definitions, Language::Python).unwrap();
        assert!(python.contains(")\n\nweb_researcher_subagent_mcp = define_mcp(\n    servers={\n"));
        assert!(python.contains("connections.get(\"notion\""));
    }
}
