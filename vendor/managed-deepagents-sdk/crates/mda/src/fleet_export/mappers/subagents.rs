//! Fleet subagents become `subagents` in the agent file. The tools mapper
//! fills in each one's `mcp` and `interrupt_on` from its `tools.json`.

use std::collections::BTreeMap;

use super::model::fleet_only;
use crate::fleet_export::{Export, FleetAgent};
use crate::init::scaffold::DEFAULT_MODEL;
use crate::init::Subagent;

pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    for spec in &agent.subagents {
        out.claimed_files
            .insert(format!("subagents/{}/AGENTS.md", spec.name));
        let system_prompt = if spec.system_prompt.is_empty() {
            &spec.instructions
        } else {
            &spec.system_prompt
        };
        let model = spec
            .model
            .as_ref()
            .map(|model| model.id.trim())
            .filter(|id| !id.is_empty())
            .map(|id| {
                if !fleet_only(id) {
                    return id.to_string();
                }
                out.report.warn(format!(
                    "Model: subagent `{}` runs on `{id}`, a Fleet model MDA can't run, so export wrote MDA's default `{DEFAULT_MODEL}` in its place.",
                    spec.name
                ));
                DEFAULT_MODEL.to_string()
            });
        out.blueprint.subagents.push(Subagent {
            name: spec.name.clone(),
            description: spec.description.clone(),
            system_prompt: system_prompt.clone(),
            model,
            mcp: Vec::new(),
            interrupt_on: BTreeMap::new(),
        });
    }
}

#[cfg(test)]
mod tests {
    use std::collections::{BTreeMap, BTreeSet};

    use serde_json::json;

    use super::*;
    use crate::fleet_export::ExportReport;
    use crate::init::Blueprint;
    use crate::project::Language;

    fn run(model: serde_json::Value) -> Export {
        let agent: FleetAgent = serde_json::from_value(json!({
            "name": "demo",
            "subagents": [{ "name": "helper", "description": "Helps.", "model": model }],
        }))
        .unwrap();
        let mut out = Export {
            blueprint: Blueprint::new("demo", Language::Python),
            files: BTreeMap::new(),
            claimed_files: BTreeSet::new(),
            report: ExportReport::default(),
        };
        map(&agent, &mut out);
        out
    }

    #[test]
    fn copies_a_subagent_model_mda_can_run() {
        let out = run(json!({ "id": "anthropic:claude-sonnet-4-6" }));
        assert_eq!(
            out.blueprint.subagents[0].model.as_deref(),
            Some("anthropic:claude-sonnet-4-6")
        );
        assert!(out.report.warnings.is_empty());
    }

    #[test]
    fn replaces_a_subagent_model_mda_cannot_run_with_the_default() {
        for id in ["langchain:default", "baseten:kimi", "0f6c1e5a-2f43-4b8e"] {
            let out = run(json!({ "id": id }));
            assert_eq!(
                out.blueprint.subagents[0].model.as_deref(),
                Some(DEFAULT_MODEL)
            );
            assert_eq!(out.report.warnings.len(), 1, "{id}");
            assert!(out.report.warnings[0].contains("subagent `helper`"), "{id}");
            assert!(out.report.warnings[0].contains(id), "{id}");
        }
    }

    #[test]
    fn a_subagent_without_a_model_inherits_the_agents() {
        let out = run(json!(null));
        assert_eq!(out.blueprint.subagents[0].model, None);
        assert!(out.report.warnings.is_empty());
    }
}
