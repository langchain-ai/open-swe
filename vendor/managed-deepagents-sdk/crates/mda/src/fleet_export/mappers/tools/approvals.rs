//! Fleet approval rules become `interrupt_on` entries, the way Fleet resolves
//! them: a rule in the list's `interrupt_config` wins, and a tool without one
//! asks first when its Managed Tools server marks it `default_interrupt`.
//! Other servers' defaults would need the caller's credentials to read, and
//! Fleet's own servers are the ones that set them.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use crate::fleet_export::snapshot::ToolsConfig;
use crate::fleet_export::Export;
use crate::init::InterruptRule;
use crate::project::Language;

/// Every decision, in the order `interrupt_on: true` allows them.
const ALL_DECISIONS: [&str; 4] = ["approve", "edit", "reject", "respond"];

/// An exported MCP server and the Fleet tools it carries.
pub(in crate::fleet_export::mappers) struct Server<'a> {
    pub key: String,
    /// Without a trailing slash, as `managed_tools_ask_first` keys it.
    pub url: String,
    pub tools: Vec<&'a str>,
}

/// The `interrupt_on` entries for the exported tools, and the Fleet tools
/// with rules that match no exported tool. `ask_first` is each Managed Tools
/// server's default approvals by URL, `None` where they couldn't be read.
pub(in crate::fleet_export::mappers) fn rules(
    config: Option<&ToolsConfig>,
    servers: &[Server],
    ask_first: &BTreeMap<String, Option<BTreeSet<String>>>,
) -> (BTreeMap<String, InterruptRule>, Vec<String>) {
    let rules = config
        .map(|config| normalize(&config.interrupt_config))
        .unwrap_or_default();
    let mut matched = BTreeSet::new();
    let mut interrupt_on = BTreeMap::new();
    for server in servers {
        let asks_by_default = |tool: &str| {
            ask_first
                .get(&server.url)
                .and_then(Option::as_ref)
                .is_some_and(|names| names.contains(tool))
        };
        for tool in &server.tools {
            let rule = match rules.get(*tool) {
                Some(rule) => {
                    matched.insert(*tool);
                    rule.clone()
                }
                None if asks_by_default(tool) => Some(InterruptRule::default()),
                None => None,
            };
            if let Some(rule) = rule {
                interrupt_on.insert(format!("{}_{tool}", server.key), rule);
            }
        }
    }
    let unmatched = rules
        .keys()
        .filter(|tool| !matched.contains(tool.as_str()))
        .cloned()
        .collect();
    (interrupt_on, unmatched)
}

/// Warns that a Managed Tools server's default approvals couldn't be read.
pub(in crate::fleet_export::mappers) fn warn_unread_defaults(key: &str, out: &mut Export) {
    let option = match out.blueprint.language {
        Language::Python => "interrupt_on",
        Language::TypeScript => "interruptOn",
    };
    out.report.warn(format!(
        "Approvals: couldn't read which `{key}` tools Fleet asks about before running. Check them in Fleet and add them to `{option}` in the agent file."
    ));
}

/// Fleet's `interrupt_config` keyed by tool name, `None` where Fleet turned
/// approval off. A `url::name` key applies to the tool name; when one tool
/// has rules that disagree, it asks first.
fn normalize(raw: &BTreeMap<String, Value>) -> BTreeMap<String, Option<InterruptRule>> {
    let mut by_name: BTreeMap<&str, Vec<&Value>> = BTreeMap::new();
    for (key, value) in raw {
        let name = key.split("::").nth(1).unwrap_or(key);
        by_name.entry(name).or_default().push(value);
    }
    by_name
        .into_iter()
        .filter_map(|(name, values)| {
            let rule = if values.iter().all(|value| *value == values[0]) {
                parse(values[0])?
            } else {
                Some(InterruptRule::default())
            };
            Some((name.to_string(), rule))
        })
        .collect()
}

/// `None` for a value that is neither a boolean nor a config object.
fn parse(value: &Value) -> Option<Option<InterruptRule>> {
    match value {
        Value::Bool(false) => Some(None),
        Value::Bool(true) => Some(Some(InterruptRule::default())),
        Value::Object(config) => {
            let mut allowed_decisions: Vec<String> = config
                .get("allowed_decisions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            allowed_decisions.sort();
            allowed_decisions.dedup();
            let description = config
                .get("description")
                .and_then(Value::as_str)
                .filter(|text| !text.is_empty())
                .map(str::to_string);
            // A config without decisions is invalid to the HITL middleware;
            // give it what `true` resolves to so the tool still asks first.
            if allowed_decisions.is_empty() && description.is_some() {
                allowed_decisions = ALL_DECISIONS.map(str::to_string).to_vec();
            }
            Some(Some(InterruptRule {
                allowed_decisions,
                description,
            }))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rules(raw: Value) -> BTreeMap<String, Option<InterruptRule>> {
        normalize(&serde_json::from_value(raw).unwrap())
    }

    fn asks() -> Option<InterruptRule> {
        Some(InterruptRule::default())
    }

    #[test]
    fn fleet_rules_apply_by_tool_name() {
        let rules = rules(json!({
            "https://a.example/mcp::send": {"allowed_decisions": ["reject", "approve"], "description": "Check it"},
            "read": false,
            "draft": true,
        }));
        let send = rules["send"].as_ref().expect("send should ask first");
        assert_eq!(send.allowed_decisions, ["approve", "reject"]);
        assert_eq!(send.description.as_deref(), Some("Check it"));
        assert_eq!(rules["read"], None);
        assert_eq!(rules["draft"], asks());
    }

    #[test]
    fn conflicting_rules_for_one_tool_ask_first() {
        let rules = rules(json!({
            "a::send": false,
            "send": {"allowed_decisions": ["approve"]},
            "b::read": false,
            "read": false,
        }));
        assert_eq!(rules["send"], asks());
        assert_eq!(rules["read"], None);
    }

    #[test]
    fn a_description_alone_still_asks_with_every_decision() {
        let rules = rules(json!({
            "send": {"description": "Check it"},
            "draft": {"allowed_decisions": [], "description": "Check it"},
        }));
        for tool in ["send", "draft"] {
            let rule = rules[tool].as_ref().expect(tool);
            assert_eq!(rule.allowed_decisions, ALL_DECISIONS, "{tool}");
        }
    }
}
