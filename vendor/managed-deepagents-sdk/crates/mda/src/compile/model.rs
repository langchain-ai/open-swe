//! Extracting the agent's configured `model` from the author's agent file, so
//! the matching chat-model provider dependency can be inferred and injected.
//!
//! Prefer [`crate::agent_config::read_agent_static_config`] when multiple agent
//! fields are needed — this module remains for the model-only call sites and
//! focused unit tests.

use std::fs;

use anyhow::{Context, Result};

use crate::agent_config::extract_model_from_typescript_source;
use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};

/// Read the agent file and extract the `model` configured on the exported
/// agent definition, if it is a static string literal.
///
/// The extraction is AST-based and scoped to the agent definition itself, so an
/// unrelated `model: "..."` elsewhere in the file (a tool config, a subagent, a
/// comment) is never mistaken for the agent's model. Each language is parsed by
/// the parser native to it: `oxc` for TypeScript, and Python's own `ast` module
/// (via `python3`) for Python.
///
/// Unlike [`crate::agent_config::read_agent_static_config`], a missing `name`
/// does not fail this helper — model inference is independent of assistant-id
/// validation.
pub(crate) fn read_agent_model(project: &Project) -> Result<Option<String>> {
    let path = project.root.join(&project.agent_file);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let file_label = project.agent_file.to_string_lossy();
    match project.language {
        Language::TypeScript => extract_model_from_typescript(&source, &file_label),
        Language::Python => extract_model_from_python(&source, &file_label),
    }
}

/// Extract the `model` string from a TypeScript agent file's `agent`
/// definition using the shared `defineDeepAgent` walk.
pub(super) fn extract_model_from_typescript(
    source: &str,
    file_name: &str,
) -> Result<Option<String>> {
    extract_model_from_typescript_source(source, file_name)
}

/// Extract the `model` string from a Python agent file's `agent` definition
/// using Python's own `ast` module via a `python3` subprocess.
pub(super) fn extract_model_from_python(source: &str, file_label: &str) -> Result<Option<String>> {
    run_python_extractor(scripts::MODEL, source, file_label, "the agent model")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::test_support::python3_available;

    /// Extraction against sources that are expected to parse; a parse error is
    /// a bug in the fixture, so these helpers unwrap it.
    fn ts_model(source: &str) -> Option<String> {
        extract_model_from_typescript(source, "agent.ts").unwrap()
    }

    #[test]
    fn ts_model_reads_a_double_quoted_value() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n";
        assert_eq!(ts_model(source).as_deref(), Some("openai:gpt-5.5"));
    }

    #[test]
    fn ts_model_reads_a_single_quoted_value() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: 'anthropic:claude' });\n";
        assert_eq!(ts_model(source).as_deref(), Some("anthropic:claude"));
    }

    #[test]
    fn ts_model_reads_a_no_substitution_template_literal_value() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: `mistral:large` });\n";
        assert_eq!(ts_model(source).as_deref(), Some("mistral:large"));
    }

    #[test]
    fn ts_model_reads_a_string_literal_key() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", \"model\": \"groq:llama\" });\n";
        assert_eq!(ts_model(source).as_deref(), Some("groq:llama"));
    }

    #[test]
    fn ts_model_reads_a_bare_model_name_without_a_provider_prefix() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"gpt-5.5\" });\n";
        assert_eq!(ts_model(source).as_deref(), Some("gpt-5.5"));
    }

    #[test]
    fn ts_model_reads_a_model_that_is_not_the_first_property() {
        let source = "export const agent = defineDeepAgent({ name: \"agent\", tools: [], model: \"xai:grok\" });\n";
        assert_eq!(ts_model(source).as_deref(), Some("xai:grok"));
    }

    #[test]
    fn ts_model_reads_a_model_alongside_a_leading_import_and_other_config() {
        let source = concat!(
            "import { defineDeepAgent } from \"managed-deepagents\";\n",
            "export const agent = defineDeepAgent({\n",
            "  name: \"agent\",\n",
            "  model: \"openai:gpt-5.5\",\n",
            "  tools: [],\n",
            "  middleware: [],\n",
            "});\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("openai:gpt-5.5"));
    }

    #[test]
    fn ts_model_reads_a_model_that_follows_a_spread_property() {
        let source = concat!(
            "const base = { temperature: 0 };\n",
            "export const agent = defineDeepAgent({ name: \"agent\", ...base, model: \"cohere:command\" });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("cohere:command"));
    }

    #[test]
    fn ts_model_reads_a_plain_non_inline_exported_const_agent() {
        let source = concat!(
            "const agent = defineDeepAgent({ name: \"agent\", model: \"deepseek:chat\" });\n",
            "export { agent };\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("deepseek:chat"));
    }

    #[test]
    fn ts_model_reads_a_model_when_another_const_precedes_the_agent() {
        let source = concat!(
            "const helper = makeHelper();\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:o3\" });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("openai:o3"));
    }

    #[test]
    fn ts_model_resolves_a_const_string_variable_declared_before_the_agent() {
        let source = concat!(
            "const MODEL = \"openai:gpt-5.5\";\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: MODEL });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("openai:gpt-5.5"));
    }

    #[test]
    fn ts_model_resolves_a_let_string_variable_via_shorthand() {
        let source = concat!(
            "let model = \"anthropic:claude\";\n",
            "export const agent = defineDeepAgent({ model });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("anthropic:claude"));
    }

    #[test]
    fn ts_model_resolves_a_var_string_variable_declared_after_the_agent() {
        let source = concat!(
            "export const agent = defineDeepAgent({ name: \"agent\", model: MODEL });\n",
            "var MODEL = \"deepseek:chat\";\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("deepseek:chat"));
    }

    #[test]
    fn ts_model_resolves_a_string_variable_backed_by_a_template_literal() {
        let source = concat!(
            "const MODEL = `groq:llama`;\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: MODEL });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("groq:llama"));
    }

    #[test]
    fn extract_model_from_typescript_parses_tsx_sources() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"openai:gpt-5.5\" });\n";
        assert_eq!(
            extract_model_from_typescript(source, "agent.tsx")
                .unwrap()
                .as_deref(),
            Some("openai:gpt-5.5")
        );
    }

    #[test]
    fn ts_model_reports_a_syntax_error_instead_of_skipping_provider_inference() {
        let source = concat!(
            "export const agent = defineDeepAgent({ model: \"openai:gpt-5.5\" });\n",
            "const broken = ;\n",
        );
        let error = extract_model_from_typescript(source, "agent.ts")
            .unwrap_err()
            .to_string();
        assert!(
            error.starts_with("failed to parse agent.ts:2:16:"),
            "{error}"
        );
    }

    #[test]
    fn ts_model_ignores_a_model_on_an_unrelated_object() {
        let source = concat!(
            "const tool = { model: \"openai:gpt-4\" };\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"anthropic:claude\" });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("anthropic:claude"));
    }

    #[test]
    fn ts_model_ignores_a_nested_subagents_own_model() {
        let source = concat!(
            "export const agent = defineDeepAgent({\n",
            "  name: \"agent\",\n",
            "  model: \"openai:gpt-5\",\n",
            "  subagents: [defineDeepAgent({ name: \"agent\", model: \"anthropic:claude\" })],\n",
            "});\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("openai:gpt-5"));
    }

    #[test]
    fn ts_model_ignores_a_model_that_appears_only_in_a_comment() {
        let source = concat!(
            "// model: \"openai:gpt-4\"\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: \"xai:grok\" });\n",
        );
        assert_eq!(ts_model(source).as_deref(), Some("xai:grok"));
    }

    #[test]
    fn ts_model_is_none_for_a_dynamic_identifier_value() {
        let source = concat!(
            "const m = pickModel();\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: m });\n",
        );
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_a_reference_to_a_non_string_binding() {
        let source = concat!(
            "const m = makeModel();\n",
            "export const agent = defineDeepAgent({ name: \"agent\", model: m });\n",
        );
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_a_reference_to_a_binding_that_does_not_exist() {
        let source = "export const agent = defineDeepAgent({ name: \"agent\", model: MISSING });\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_a_shorthand_with_no_matching_string_binding() {
        let source = "export const agent = defineDeepAgent({ model });\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_a_computed_key() {
        let source = concat!(
            "const key = \"model\";\n",
            "export const agent = defineDeepAgent({ [key]: \"openai:gpt-5\" });\n",
        );
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_a_template_literal_with_interpolation() {
        let source =
            "export const agent = defineDeepAgent({ name: \"agent\", model: `openai:${name}` });\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_sibling_keys_that_merely_resemble_model() {
        let source =
            "export const agent = defineDeepAgent({ modelProvider: \"openai\", modelName: \"gpt-5\" });\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_when_the_agent_is_not_a_call() {
        let source = "export const agent = someExistingAgent;\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_when_there_is_no_agent_binding() {
        let source = "export const other = 1;\n";
        assert_eq!(ts_model(source), None);
    }

    #[test]
    fn ts_model_is_none_for_empty_source() {
        assert_eq!(ts_model(""), None);
    }

    fn py_model(source: &str) -> Option<String> {
        extract_model_from_python(source, "agent.py").unwrap()
    }

    #[test]
    fn py_model_reads_a_double_quoted_keyword() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=\"anthropic:claude\")\n";
        assert_eq!(py_model(source).as_deref(), Some("anthropic:claude"));
    }

    #[test]
    fn py_model_reads_a_single_quoted_keyword() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model='openai:gpt-5.5')\n";
        assert_eq!(py_model(source).as_deref(), Some("openai:gpt-5.5"));
    }

    #[test]
    fn py_model_reads_a_bare_model_name() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=\"gpt-5.5\")\n";
        assert_eq!(py_model(source).as_deref(), Some("gpt-5.5"));
    }

    #[test]
    fn py_model_reads_a_model_that_is_not_the_first_keyword() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(tools=[], model=\"groq:llama\")\n";
        assert_eq!(py_model(source).as_deref(), Some("groq:llama"));
    }

    #[test]
    fn py_model_reads_a_model_from_an_annotated_assignment() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "from managed_deepagents import define_deep_agent\n",
            "agent: object = define_deep_agent(name=\"agent\", model=\"mistral:large\")\n",
        );
        assert_eq!(py_model(source).as_deref(), Some("mistral:large"));
    }

    #[test]
    fn py_model_reads_a_model_from_a_multiline_call() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "agent = define_deep_agent(\n",
            "    name=\"agent\",\n",
            "    model=\"openai:gpt-5.5\",\n",
            "    tools=[],\n",
            ")\n",
        );
        assert_eq!(py_model(source).as_deref(), Some("openai:gpt-5.5"));
    }

    #[test]
    fn py_model_reads_a_model_from_chained_assignment_targets() {
        if !python3_available() {
            return;
        }
        let source = "agent = primary = define_deep_agent(name=\"agent\", model=\"xai:grok\")\n";
        assert_eq!(py_model(source).as_deref(), Some("xai:grok"));
    }

    #[test]
    fn py_model_ignores_a_model_on_an_unrelated_call() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "tool = make_tool(model=\"openai:gpt-4\")\n",
            "agent = define_deep_agent(name=\"agent\", model=\"deepseek:chat\")\n",
        );
        assert_eq!(py_model(source).as_deref(), Some("deepseek:chat"));
    }

    #[test]
    fn py_model_ignores_a_nested_subagents_own_model() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "agent = define_deep_agent(\n",
            "    name=\"agent\",\n",
            "    model=\"openai:gpt-5\",\n",
            "    subagents=[define_deep_agent(name=\"agent\", model=\"anthropic:claude\")],\n",
            ")\n",
        );
        assert_eq!(py_model(source).as_deref(), Some("openai:gpt-5"));
    }

    #[test]
    fn py_model_is_none_for_a_dynamically_constructed_model() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=pick_model())\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_for_a_variable_reference() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "m = \"openai:gpt-5\"\n",
            "agent = define_deep_agent(name=\"agent\", model=m)\n",
        );
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_for_an_f_string_interpolation() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=f\"openai:{name}\")\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_for_string_concatenation() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=\"openai:\" + \"gpt-5\")\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_for_a_positional_argument() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(\"openai:gpt-5\")\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_when_there_is_no_model_keyword() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(tools=[])\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_is_none_when_there_is_no_agent_binding() {
        if !python3_available() {
            return;
        }
        let source = "other = define_deep_agent(name=\"agent\", model=\"openai:gpt-5\")\n";
        assert_eq!(py_model(source), None);
    }

    #[test]
    fn py_model_reports_a_syntax_error() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"agent\", model=\n";
        let error = extract_model_from_python(source, "agent.py")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse agent.py:"), "{error}");
    }
}
