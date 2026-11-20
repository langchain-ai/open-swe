use crate::fleet_export::{Export, FleetAgent};
use crate::init::scaffold::DEFAULT_MODEL;

/// Model ids MDA cannot run: Fleet's served tiers and Baseten.
const FLEET_ONLY_PREFIXES: &[&str] = &["langchain:", "baseten:"];

/// The model id is copied as written. An id MDA can't run would only fail at
/// model init after a successful deploy, so it becomes MDA's default instead.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    let id = agent
        .model
        .as_ref()
        .map(|model| model.id.trim())
        .filter(|id| !id.is_empty());
    let Some(id) = id else {
        out.blueprint.model = Some(DEFAULT_MODEL.to_string());
        out.report.warn(format!(
            "Model: the Fleet agent runs on Fleet's default model, which MDA can't run, so export wrote MDA's default `{DEFAULT_MODEL}`."
        ));
        return;
    };
    if fleet_only(id) {
        out.blueprint.model = Some(DEFAULT_MODEL.to_string());
        out.report.warn(format!(
            "Model: `{id}` is a Fleet model MDA can't run, so export wrote MDA's default `{DEFAULT_MODEL}` in its place."
        ));
        return;
    }
    out.blueprint.model = Some(id.to_string());
}

/// Whether MDA can't run `id`. A workspace model id has no `provider:` prefix.
pub(super) fn fleet_only(id: &str) -> bool {
    !id.contains(':')
        || FLEET_ONLY_PREFIXES
            .iter()
            .any(|prefix| id.starts_with(prefix))
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
        let agent: FleetAgent =
            serde_json::from_value(json!({ "name": "demo", "model": model })).unwrap();
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
    fn copies_a_provider_model_without_a_warning() {
        let out = run(json!({ "id": "anthropic:claude-sonnet-4-6" }));
        assert_eq!(
            out.blueprint.model.as_deref(),
            Some("anthropic:claude-sonnet-4-6")
        );
        assert!(out.report.warnings.is_empty());
    }

    #[test]
    fn replaces_models_mda_cannot_run_with_the_default() {
        for (model, replaced) in [
            (json!(null), "Fleet's default model"),
            (json!({ "id": "" }), "Fleet's default model"),
            (json!({ "id": "langchain:default" }), "`langchain:default`"),
            (json!({ "id": "baseten:kimi" }), "`baseten:kimi`"),
            (
                json!({ "id": "0f6c1e5a-2f43-4b8e-9d1c-3a7b2c9e8d10" }),
                "`0f6c1e5a-2f43-4b8e-9d1c-3a7b2c9e8d10`",
            ),
        ] {
            let out = run(model);
            assert_eq!(out.blueprint.model.as_deref(), Some(DEFAULT_MODEL));
            assert_eq!(out.report.warnings.len(), 1, "{replaced}");
            assert!(out.report.warnings[0].contains(replaced), "{replaced}");
            assert!(out.report.warnings[0].contains(DEFAULT_MODEL), "{replaced}");
        }
    }
}
