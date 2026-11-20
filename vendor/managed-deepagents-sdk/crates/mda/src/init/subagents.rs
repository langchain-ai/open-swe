//! Source text for a blueprint's subagents: the `subagents` argument in the
//! agent file, and the import of the `tools/mcp` definitions they select.

use crate::project::Language;

use super::mcp::interrupt_option;
use super::source::lit;
use super::{Subagent, AGENT_MCP};

/// A `tools/mcp` export name as written in `language`: snake_case in Python,
/// camelCase in TypeScript (`web_researcher_subagent_mcp` becomes
/// `webResearcherSubagentMcp`).
pub(super) fn export_name(name: &str, language: Language) -> String {
    match language {
        Language::Python => name.to_string(),
        Language::TypeScript => name
            .split('_')
            .filter(|word| !word.is_empty())
            .enumerate()
            .map(|(index, word)| {
                let mut chars = word.chars();
                let first = chars.next().map(|first| match index {
                    0 => first,
                    _ => first.to_ascii_uppercase(),
                });
                first.into_iter().chain(chars).collect::<String>()
            })
            .collect(),
    }
}

/// The import of the definitions the subagents select, for the line after the
/// agent file's `managed-deepagents` import, or nothing.
pub(super) fn imports(subagents: &[Subagent], language: Language) -> String {
    let mut names: Vec<&str> = Vec::new();
    for name in subagents.iter().flat_map(|subagent| &subagent.mcp) {
        if !names.contains(&name.as_str()) {
            names.push(name);
        }
    }
    // `mcp` first, then each subagent's own in order.
    names.sort_by_key(|name| *name != AGENT_MCP);
    if names.is_empty() {
        return String::new();
    }
    let names: Vec<String> = names
        .into_iter()
        .map(|name| export_name(name, language))
        .collect();
    match language {
        Language::Python => format!("\nfrom tools.mcp import {}\n", names.join(", ")),
        Language::TypeScript => {
            format!("import {{ {} }} from \"./tools/mcp\";\n", names.join(", "))
        }
    }
}

/// The `subagents` argument lines for the agent file, or nothing.
pub(super) fn option(subagents: &[Subagent], language: Language) -> String {
    if subagents.is_empty() {
        return String::new();
    }
    let py = language == Language::Python;
    let (open, close, item, field) = if py {
        ("    subagents=[\n", "    ],\n", "        ", "            ")
    } else {
        ("  subagents: [\n", "  ],\n", "    ", "      ")
    };
    // Python dict keys are string literals; TypeScript object keys are bare.
    let key = |python: &str, typescript: &str| {
        if py {
            lit(python)
        } else {
            typescript.to_string()
        }
    };
    let mut out = open.to_string();
    for subagent in subagents {
        out.push_str(&format!("{item}{{\n"));
        out.push_str(&format!(
            "{field}{}: {},\n",
            key("name", "name"),
            lit(&subagent.name)
        ));
        out.push_str(&format!(
            "{field}{}: {},\n",
            key("description", "description"),
            lit(&subagent.description)
        ));
        out.push_str(&format!(
            "{field}{}: {},\n",
            key("system_prompt", "systemPrompt"),
            lit(&subagent.system_prompt)
        ));
        if let Some(model) = &subagent.model {
            out.push_str(&format!(
                "{field}{}: {},\n",
                key("model", "model"),
                lit(model)
            ));
        }
        let mcp: Vec<String> = subagent
            .mcp
            .iter()
            .map(|name| export_name(name, language))
            .collect();
        let mcp = mcp.join(", ");
        out.push_str(&format!("{field}{}: [{mcp}],\n", key("mcp", "mcp")));
        out.push_str(&interrupt_option(&subagent.interrupt_on, 3, language));
        out.push_str(&format!("{item}}},\n"));
    }
    out.push_str(close);
    out
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;
    use crate::init::InterruptRule;

    fn subagents() -> Vec<Subagent> {
        vec![
            Subagent {
                name: "web researcher".to_string(),
                description: "Searches the web.".to_string(),
                system_prompt: "You research.\nCite sources.".to_string(),
                model: Some("anthropic:claude-sonnet-5-5".to_string()),
                mcp: vec!["web_researcher_subagent_mcp".to_string()],
                interrupt_on: BTreeMap::from([(
                    "tavily_tavily_search".to_string(),
                    InterruptRule::default(),
                )]),
            },
            Subagent {
                name: "summarizer".to_string(),
                description: "Summarizes.".to_string(),
                system_prompt: "You summarize.".to_string(),
                model: None,
                mcp: Vec::new(),
                interrupt_on: BTreeMap::new(),
            },
            Subagent {
                name: "notion helper".to_string(),
                description: "Uses the agent's tools.".to_string(),
                system_prompt: "You help.".to_string(),
                model: None,
                mcp: vec![AGENT_MCP.to_string()],
                interrupt_on: BTreeMap::new(),
            },
        ]
    }

    #[test]
    fn export_names_follow_each_language() {
        assert_eq!(
            export_name("web_researcher_subagent_mcp", Language::Python),
            "web_researcher_subagent_mcp"
        );
        assert_eq!(
            export_name("web_researcher_subagent_mcp", Language::TypeScript),
            "webResearcherSubagentMcp"
        );
        assert_eq!(
            export_name("triage_bot_2_subagent_mcp", Language::TypeScript),
            "triageBot2SubagentMcp"
        );
        assert_eq!(export_name(AGENT_MCP, Language::TypeScript), "mcp");
    }

    #[test]
    fn python_subagents_and_imports() {
        assert_eq!(
            imports(&subagents(), Language::Python),
            "\nfrom tools.mcp import mcp, web_researcher_subagent_mcp\n"
        );
        assert_eq!(
            option(&subagents(), Language::Python),
            r#"    subagents=[
        {
            "name": "web researcher",
            "description": "Searches the web.",
            "system_prompt": "You research.\nCite sources.",
            "model": "anthropic:claude-sonnet-5-5",
            "mcp": [web_researcher_subagent_mcp],
            "interrupt_on": {
                "tavily_tavily_search": True,
            },
        },
        {
            "name": "summarizer",
            "description": "Summarizes.",
            "system_prompt": "You summarize.",
            "mcp": [],
        },
        {
            "name": "notion helper",
            "description": "Uses the agent's tools.",
            "system_prompt": "You help.",
            "mcp": [mcp],
        },
    ],
"#
        );
    }

    #[test]
    fn typescript_subagents_and_imports() {
        assert_eq!(
            imports(&subagents(), Language::TypeScript),
            "import { mcp, webResearcherSubagentMcp } from \"./tools/mcp\";\n"
        );
        assert_eq!(
            option(&subagents(), Language::TypeScript),
            r#"  subagents: [
    {
      name: "web researcher",
      description: "Searches the web.",
      systemPrompt: "You research.\nCite sources.",
      model: "anthropic:claude-sonnet-5-5",
      mcp: [webResearcherSubagentMcp],
      interruptOn: {
        "tavily_tavily_search": true,
      },
    },
    {
      name: "summarizer",
      description: "Summarizes.",
      systemPrompt: "You summarize.",
      mcp: [],
    },
    {
      name: "notion helper",
      description: "Uses the agent's tools.",
      systemPrompt: "You help.",
      mcp: [mcp],
    },
  ],
"#
        );
    }

    #[test]
    fn no_subagents_writes_nothing() {
        assert_eq!(option(&[], Language::Python), "");
        assert_eq!(imports(&[], Language::TypeScript), "");
    }
}
