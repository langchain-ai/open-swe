//! Static extraction helpers for authored `defineDeepAgent` config.
//!
//! Most helpers are deliberately best-effort: if the agent config is dynamic or
//! missing, MDA falls back to the default behavior instead of failing deploy.
//! A file that does not parse is not best-effort territory — the config it
//! declares is unknowable, so it is reported instead of defaulted.
//!
//! A static agent `name` is required because it becomes the LangGraph graph key
//! / assistant id and the default LangSmith deployment name. A factory
//! must return a definition with an inline string literal name so MDA can read it
//! without executing the resolver.
//!
//! Callers that need more than one field should use [`read_agent_static_config`]
//! so the agent file is read and parsed once.

use std::collections::HashMap;
use std::fs;

use anyhow::{bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{
    ArrowFunctionBody, Declaration, Expression, Function, FunctionBody, ObjectExpression, Statement,
};
use serde::Deserialize;

use crate::error::{categorized, ErrorCategory};
use crate::naming::normalize_name;
use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};
use crate::typescript::{
    collect_string_bindings, find_agent_call, first_object_argument, is_define_deep_agent_callee,
    module_export_name, object_property, parse_typescript, statement_variable_declaration,
    static_string_expression,
};

/// Outcome of reading the authored `name` from `defineDeepAgent` /
/// `define_deep_agent`.
///
/// The Python extractor emits this as `{"kind": "STATIC", "value": ...}`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "kind", content = "value", rename_all = "UPPERCASE")]
enum AgentNameValue {
    /// No `name` property/keyword on the agent definition.
    Absent,
    /// A compile-time string literal (or resolvable module-level string binding).
    Static(String),
    /// `name` is present but not a static string MDA can resolve.
    Dynamic,
}

/// Static fields extracted from the authored agent definition in one pass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AgentStaticConfig {
    /// Canonical LangGraph graph / assistant id from authored `name`.
    pub assistant_id: String,
    /// Static `model` string when resolvable.
    pub model: Option<String>,
    /// Whether the exported `agent` is a per-run factory.
    pub is_factory: bool,
    /// Whether the definition still sets the retired `disableMemory` /
    /// `disable_memory` knob (any value). Memory moved to `memory.{ts,py}`, so
    /// its presence is a migration error rather than an opt-out.
    pub declares_disable_memory: bool,
    /// Subagents marked `"async": True`, each built as its own graph.
    pub async_subagents: Vec<AsyncSubagentGraph>,
    /// Subagent entries MDA cannot read statically (helper calls, spreads, imports).
    pub unreadable_subagents: usize,
}

/// Name of the general-purpose subagent, which MDA also runs async on served Python builds.
pub(crate) const GENERAL_PURPOSE_SUBAGENT: &str = "general-purpose";

impl AgentStaticConfig {
    /// Async subagents to build for a served Python agent: the declared ones,
    /// plus the general-purpose subagent unless one is declared async already.
    pub(crate) fn served_async_subagents(&self) -> Vec<AsyncSubagentGraph> {
        let mut graphs = self.async_subagents.clone();
        let graph_id = format!("{}--{GENERAL_PURPOSE_SUBAGENT}", self.assistant_id);
        // Compare graph ids: a declared `general_purpose` normalizes to the same one.
        if !graphs.iter().any(|graph| graph.graph_id == graph_id) {
            graphs.push(AsyncSubagentGraph {
                name: GENERAL_PURPOSE_SUBAGENT.to_string(),
                graph_id,
                export: "agent__general_purpose".to_string(),
            });
        }
        graphs
    }
}

/// An async subagent and the graph MDA builds for it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AsyncSubagentGraph {
    /// Authored subagent `name`.
    pub name: String,
    /// Graph key in `langgraph.json`: `<assistant_id>--<normalized name>`.
    pub graph_id: String,
    /// Python export of its graph factory in the generated entry.
    pub export: String,
}

/// Fields extracted before `name` validation (shared by language extractors).
#[derive(Debug, Clone, PartialEq, Eq)]
struct AgentConfigFields {
    name: AgentNameValue,
    model: Option<String>,
    is_factory: bool,
    declares_disable_memory: bool,
    unsupported_keys: Vec<String>,
    async_subagents: Vec<AgentNameValue>,
    unreadable_subagents: usize,
}

#[derive(Debug, Deserialize)]
struct PythonAgentExtract {
    name: AgentNameValue,
    model: Option<String>,
    is_factory: bool,
    declares_disable_memory: bool,
    unsupported_keys: Vec<String>,
    async_subagents: Vec<AgentNameValue>,
    unreadable_subagents: usize,
}

/// Read the agent file once and extract graph identity and static build metadata.
pub(crate) fn read_agent_static_config(project: &Project) -> Result<AgentStaticConfig> {
    let file_label = project.agent_file.display().to_string();
    let fields = read_agent_config_fields(project)?;
    let assistant_id = resolve_assistant_id(fields.name, &file_label, project.language)?;
    let async_subagents =
        resolve_async_subagents(fields.async_subagents, &assistant_id, &file_label)?;
    Ok(AgentStaticConfig {
        assistant_id,
        model: fields.model,
        is_factory: fields.is_factory,
        declares_disable_memory: fields.declares_disable_memory,
        async_subagents,
        unreadable_subagents: fields.unreadable_subagents,
    })
}

fn resolve_async_subagents(
    values: Vec<AgentNameValue>,
    assistant_id: &str,
    file_label: &str,
) -> Result<Vec<AsyncSubagentGraph>> {
    let mut graphs: Vec<AsyncSubagentGraph> = Vec::new();
    for value in values {
        let AgentNameValue::Static(name) = value else {
            return Err(categorized(
                ErrorCategory::Project,
                format!(
                    "async subagents in {file_label} must set `name` and `\"async\": True` \
                     as literals, after any `**spread`, so MDA can build a graph for each one."
                ),
            ));
        };
        let normalized = normalize_name(&name)
            .with_context(|| format!("invalid async subagent name {name:?} in {file_label}"))?;
        let graph_id = format!("{assistant_id}--{normalized}");
        validate_assistant_id(&graph_id, file_label)?;
        if graphs.iter().any(|graph| graph.graph_id == graph_id) {
            bail!(
                "async subagent {name:?} in {file_label} has the same graph id as another \
                 subagent ({graph_id}); names are compared after normalization"
            );
        }
        let export = format!("agent__{}", normalized.replace('-', "_"));
        graphs.push(AsyncSubagentGraph {
            name,
            graph_id,
            export,
        });
    }
    Ok(graphs)
}

/// Whether the authored agent still sets the retired memory opt-out.
pub(crate) fn agent_declares_disable_memory(project: &Project) -> Result<bool> {
    Ok(read_agent_config_fields(project)?.declares_disable_memory)
}

/// Resolve the LangGraph graph / assistant id from the exported agent entry.
///
/// Static definitions use their required authored `name`. Factories
/// must return a definition whose `name` is an inline string literal.
pub(crate) fn agent_assistant_id(project: &Project) -> Result<String> {
    Ok(read_agent_static_config(project)?.assistant_id)
}

fn read_agent_config_fields(project: &Project) -> Result<AgentConfigFields> {
    let agent_path = project.root.join(&project.agent_file);
    let source = fs::read_to_string(&agent_path)
        .with_context(|| format!("failed to read {}", agent_path.display()))?;
    let file_label = project.agent_file.display().to_string();
    let fields = match project.language {
        Language::TypeScript => extract_agent_config_typescript(&source, &file_label),
        Language::Python => extract_agent_config_python(&source, &file_label),
    }?;
    validate_agent_keys(&fields.unsupported_keys, &file_label, project.language)?;
    Ok(fields)
}

fn validate_agent_keys(keys: &[String], file_label: &str, language: Language) -> Result<()> {
    let define = language.ident("defineDeepAgent", "define_deep_agent");
    for key in keys {
        let hint = match key.as_str() {
            "backend" => "Configure the sandbox in sandbox/.",
            "store" => "MDA provides the store automatically.",
            "checkpointer" => "MDA provides the checkpointer automatically.",
            "instructions" => "Use a factory entrypoint to select instructions per run; static agents use instructions.md.",
            "mcp" => language.ident(
                "Use a factory entrypoint to select MCP servers per run; static agents use tools/mcp.ts.",
                "Use a factory entrypoint to select MCP servers per run; static agents use tools/mcp.py.",
            ),
            "memory" => language.ident(
                "Use a factory entrypoint to select memory per run; static agents use memory.ts.",
                "Use a factory entrypoint to select memory per run; static agents use memory.py.",
            ),
            "sandbox" => language.ident(
                "Use a factory entrypoint to select a sandbox per run; static agents use sandbox/index.ts.",
                "Use a factory entrypoint to select a sandbox per run; static agents use sandbox/__init__.py.",
            ),
            "skills" => "Use a factory entrypoint to select skills per run; static agents use skills/.",
            "systemPrompt" | "system_prompt" => "Use a factory entrypoint with `instructions` for per-run prompts; static agents use instructions.md.",
            "state_schema" => "Custom state schemas are not supported in Python.",
            _ => continue,
        };
        return Err(categorized(
            ErrorCategory::Project,
            format!("{file_label}: Remove `{key}` from {define}(...). {hint}"),
        ));
    }
    Ok(())
}

fn resolve_assistant_id(
    value: AgentNameValue,
    file_label: &str,
    language: Language,
) -> Result<String> {
    let define = language.ident("defineDeepAgent", "define_deep_agent");
    let example = language.string_argument("name", "research-assistant");
    match value {
        AgentNameValue::Absent => Err(categorized(
            ErrorCategory::Project,
            format!(
                "agent `name` is required in {file_label}. Add a static string \
                 (e.g. {example}) to {define}(...); MDA \
                 uses it as the LangGraph assistant id and the default LangSmith \
                 deployment name."
            ),
        )),
        AgentNameValue::Static(name) => {
            let name = normalize_name(&name)
                .with_context(|| format!("invalid agent `name` in {file_label}"))?;
            validate_assistant_id(&name, file_label)?;
            Ok(name)
        }
        AgentNameValue::Dynamic => Err(categorized(
            ErrorCategory::Project,
            format!(
                "agent `name` in {file_label} must be a static string literal \
                 (e.g. {example}). Dynamic values cannot be used \
                 as the LangGraph assistant id."
            ),
        )),
    }
}

/// Whether `name` is a valid assistant / graph id (and scaffold agent name).
pub(crate) fn is_valid_assistant_id(name: &str) -> bool {
    validate_assistant_id(name, "agent").is_ok()
}

/// Validate a candidate assistant / graph id.
pub(crate) fn validate_assistant_id(name: &str, file_label: &str) -> Result<()> {
    if name.is_empty() {
        bail!("agent `name` in {file_label} must be a non-empty string");
    }
    let mut chars = name.chars();
    let Some(first) = chars.next() else {
        bail!("agent `name` in {file_label} must be a non-empty string");
    };
    if !first.is_ascii_alphabetic() {
        bail!(
            "agent `name` in {file_label} must start with an ASCII letter \
             (got {name:?})"
        );
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        bail!(
            "agent `name` in {file_label} must match \
             [a-zA-Z][a-zA-Z0-9_-]* (got {name:?})"
        );
    }
    Ok(())
}

fn extract_agent_config_typescript(source: &str, file_name: &str) -> Result<AgentConfigFields> {
    const DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS: [&str; 9] = [
        "backend",
        "store",
        "checkpointer",
        "instructions",
        "mcp",
        "memory",
        "sandbox",
        "skills",
        "systemPrompt",
    ];
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    let bindings = collect_string_bindings(&parsed.program.body);

    if let Some(name) = typescript_factory_name(&parsed.program.body) {
        return Ok(AgentConfigFields {
            name,
            model: None,
            is_factory: true,
            declares_disable_memory: false,
            unsupported_keys: Vec::new(),
            async_subagents: Vec::new(),
            unreadable_subagents: 0,
        });
    }

    // Model historically accepts any CallExpression on `agent`; name/memory
    // require `defineDeepAgent`. Share one walk and specialize per field.
    let Some(call) = find_agent_call(&parsed.program.body) else {
        return Ok(AgentConfigFields {
            name: AgentNameValue::Absent,
            model: None,
            is_factory: false,
            declares_disable_memory: false,
            unsupported_keys: Vec::new(),
            async_subagents: Vec::new(),
            unreadable_subagents: 0,
        });
    };

    let model =
        first_object_argument(call).and_then(|object| object_model_property(object, &bindings));

    if !is_define_deep_agent_callee(&call.callee) {
        return Ok(AgentConfigFields {
            name: AgentNameValue::Absent,
            model,
            is_factory: false,
            declares_disable_memory: false,
            unsupported_keys: Vec::new(),
            async_subagents: Vec::new(),
            unreadable_subagents: 0,
        });
    }

    let config = first_object_argument(call);
    if config.is_some_and(object_declares_async_subagent) {
        return Err(categorized(
            ErrorCategory::Project,
            format!("{file_name}: async subagents (`async: true`) are only supported in Python projects"),
        ));
    }
    Ok(AgentConfigFields {
        name: config
            .map(|object| object_name_property(object, &bindings))
            .unwrap_or(AgentNameValue::Absent),
        model,
        is_factory: false,
        declares_disable_memory: config.is_some_and(object_declares_disable_memory),
        unsupported_keys: DEFINE_DEEP_AGENT_UNSUPPORTED_KEYS
            .into_iter()
            .filter(|key| config.is_some_and(|object| object_property(object, key).is_some()))
            .map(str::to_string)
            .collect(),
        async_subagents: Vec::new(),
        unreadable_subagents: 0,
    })
}

fn object_declares_async_subagent(object: &ObjectExpression<'_>) -> bool {
    let Some(Expression::ArrayExpression(subagents)) = object_property(object, "subagents") else {
        return false;
    };
    subagents.elements.iter().any(|element| {
        matches!(
            element.as_expression(),
            Some(Expression::ObjectExpression(spec)) if object_property(spec, "async").is_some()
        )
    })
}

fn statement_function<'a>(statement: &'a Statement<'a>) -> Option<&'a Function<'a>> {
    match statement {
        Statement::FunctionDeclaration(function) => Some(function),
        Statement::ExportDeclaration(export) => match &export.declaration {
            Declaration::FunctionDeclaration(function) => Some(function),
            _ => None,
        },
        _ => None,
    }
}

/// Read a returned definition without importing the module or running its factory.
fn typescript_factory_name(body: &[Statement<'_>]) -> Option<AgentNameValue> {
    let mut binding = "agent";
    for statement in body {
        if let Statement::ExportNamedDeclaration(export) = statement {
            for specifier in &export.specifiers {
                if module_export_name(&specifier.exported) == "agent" {
                    binding = module_export_name(&specifier.local);
                }
            }
        }
    }
    // Resolve `agent = build` without evaluating authored code.
    for statement in body {
        if let Some(declaration) = statement_variable_declaration(statement) {
            for declarator in &declaration.declarations {
                if declarator
                    .id
                    .get_identifier_name()
                    .is_some_and(|name| name == binding)
                {
                    if let Some(Expression::Identifier(reference)) = declarator.init.as_ref() {
                        binding = reference.name.as_str();
                    }
                }
            }
        }
    }
    for statement in body {
        if let Some(function) = statement_function(statement) {
            if function.id.as_ref().is_some_and(|id| id.name == binding) {
                return Some(
                    function
                        .body
                        .as_deref()
                        .and_then(direct_return_expression)
                        .map_or(AgentNameValue::Dynamic, factory_definition_name),
                );
            }
        }
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            if !declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == binding)
            {
                continue;
            }
            let returned = match declarator.init.as_ref()? {
                Expression::ArrowFunctionExpression(factory) => match &factory.body {
                    ArrowFunctionBody::FunctionBody(body) => direct_return_expression(body),
                    body => body.as_expression(),
                },
                Expression::FunctionExpression(factory) => {
                    factory.body.as_deref().and_then(direct_return_expression)
                }
                _ => return None,
            };
            return Some(returned.map_or(AgentNameValue::Dynamic, factory_definition_name));
        }
    }
    None
}

fn direct_return_expression<'a>(body: &'a FunctionBody<'a>) -> Option<&'a Expression<'a>> {
    let (returned, prelude) = body.statements.split_last()?;
    if !prelude.iter().all(|statement| {
        matches!(
            statement,
            Statement::EmptyStatement(_)
                | Statement::ExpressionStatement(_)
                | Statement::VariableDeclaration(_)
        )
    }) {
        return None;
    }
    match returned {
        Statement::ReturnStatement(statement) => statement.argument.as_ref(),
        _ => None,
    }
}

fn factory_definition_name(expression: &Expression<'_>) -> AgentNameValue {
    match expression {
        Expression::CallExpression(call) if is_define_deep_agent_callee(&call.callee) => {
            first_object_argument(call).map_or(AgentNameValue::Absent, inline_object_name_property)
        }
        Expression::ParenthesizedExpression(expression) => {
            factory_definition_name(&expression.expression)
        }
        _ => AgentNameValue::Dynamic,
    }
}

fn inline_object_name_property(object: &ObjectExpression<'_>) -> AgentNameValue {
    match object_property(object, "name") {
        Some(Expression::StringLiteral(literal)) => {
            AgentNameValue::Static(literal.value.to_string())
        }
        Some(_) => AgentNameValue::Dynamic,
        None => AgentNameValue::Absent,
    }
}

#[cfg(test)]
fn extract_declares_disable_memory_from_typescript(source: &str, file_name: &str) -> Result<bool> {
    Ok(extract_agent_config_typescript(source, file_name)?.declares_disable_memory)
}

#[cfg(test)]
fn extract_name_from_typescript(source: &str, file_name: &str) -> Result<AgentNameValue> {
    Ok(extract_agent_config_typescript(source, file_name)?.name)
}

fn object_name_property(
    object: &ObjectExpression<'_>,
    bindings: &HashMap<String, String>,
) -> AgentNameValue {
    let Some(value) = object_property(object, "name") else {
        return AgentNameValue::Absent;
    };
    match value {
        Expression::Identifier(reference) => match bindings.get(&*reference.name) {
            Some(bound) => AgentNameValue::Static(bound.clone()),
            None => AgentNameValue::Dynamic,
        },
        other => match static_string_expression(other) {
            Some(text) => AgentNameValue::Static(text),
            None => AgentNameValue::Dynamic,
        },
    }
}

/// Presence of the retired `disableMemory` knob, whatever its value.
///
/// Any value is reported: `false` used to mean "memory on", so honoring it now
/// that memory is opt-in would silently flip what the author asked for.
fn object_declares_disable_memory(object: &ObjectExpression<'_>) -> bool {
    object_property(object, "disableMemory").is_some()
}

fn object_model_property(
    object: &ObjectExpression<'_>,
    bindings: &HashMap<String, String>,
) -> Option<String> {
    match object_property(object, "model")? {
        Expression::Identifier(reference) => bindings.get(&*reference.name).cloned(),
        other => static_string_expression(other),
    }
}

fn extract_agent_config_python(source: &str, file_label: &str) -> Result<AgentConfigFields> {
    let output = run_python_extractor(scripts::AGENT, source, file_label, "the agent config")?
        .ok_or_else(|| {
            categorized(
                ErrorCategory::Project,
                format!("agent config extractor produced no output for {file_label}"),
            )
        })?;
    let extracted: PythonAgentExtract = serde_json::from_str(&output)
        .with_context(|| format!("failed to parse agent config JSON from {file_label}"))?;
    Ok(AgentConfigFields {
        name: extracted.name,
        model: extracted.model,
        is_factory: extracted.is_factory,
        declares_disable_memory: extracted.declares_disable_memory,
        unsupported_keys: extracted.unsupported_keys,
        async_subagents: extracted.async_subagents,
        unreadable_subagents: extracted.unreadable_subagents,
    })
}

#[cfg(test)]
fn extract_declares_disable_memory_from_python(source: &str, file_label: &str) -> Result<bool> {
    Ok(extract_agent_config_python(source, file_label)?.declares_disable_memory)
}

#[cfg(test)]
fn extract_name_from_python(source: &str, file_label: &str) -> Result<AgentNameValue> {
    Ok(extract_agent_config_python(source, file_label)?.name)
}

/// Extract a static `model` string from TypeScript source (test / model module).
pub(crate) fn extract_model_from_typescript_source(
    source: &str,
    file_name: &str,
) -> Result<Option<String>> {
    Ok(extract_agent_config_typescript(source, file_name)?.model)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::toolchain;

    #[test]
    fn python_name_payloads_deserialize_into_agent_name_values() {
        for (payload, expected) in [
            (
                r#"{"kind":"STATIC","value":"x"}"#,
                AgentNameValue::Static("x".to_string()),
            ),
            (r#"{"kind":"DYNAMIC"}"#, AgentNameValue::Dynamic),
            (r#"{"kind":"ABSENT"}"#, AgentNameValue::Absent),
        ] {
            let name: AgentNameValue = serde_json::from_str(payload).unwrap();
            assert_eq!(name, expected, "{payload}");
        }
        for payload in [r#"{"kind":"STATIC"}"#, r#"{"kind":"OTHER"}"#] {
            assert!(
                serde_json::from_str::<AgentNameValue>(payload).is_err(),
                "{payload}"
            );
        }
    }

    #[test]
    fn plain_typescript_factories_have_static_names_without_execution() {
        for source in [
            "export function agent(runtime) { markFactoryCalled(); return defineDeepAgent({ name: 'plain' }); }",
            "export async function agent(runtime) { return defineDeepAgent({ name: 'plain' }); }",
            "export const agent = (runtime) => defineDeepAgent({ name: 'plain' });",
            "export const agent = async function(runtime) { return defineDeepAgent({ name: 'plain' }); };",
            "function build(runtime) { return defineDeepAgent({ name: 'plain' }); } export const agent = build;",
        ] {
            let fields = extract_agent_config_typescript(source, "agent.ts").unwrap();
            assert_eq!(fields.name, AgentNameValue::Static("plain".to_string()));
            assert!(fields.is_factory);
        }
    }

    #[test]
    fn plain_python_factories_have_static_names_without_execution() {
        for source in [
            "def agent(runtime):\n    mark_factory_called()\n    return define_deep_agent(name='plain')\n",
            "async def agent(runtime):\n    return define_deep_agent(name='plain')\n",
            "def build(runtime):\n    return define_deep_agent(name='plain')\nagent = build\n",
            "agent = lambda runtime: define_deep_agent(name='plain')\n",
        ] {
            let fields = extract_agent_config_python(source, "agent.py").unwrap();
            assert_eq!(fields.name, AgentNameValue::Static("plain".to_string()));
            assert!(fields.is_factory);
        }
    }

    #[test]
    fn managed_key_extraction_ignores_nested_config_comments_and_strings() {
        let ts = extract_agent_config_typescript(
            r#"// backend: oldBackend
            export const agent = defineDeepAgent({
                name: 'research', metadata: { memory: 'skills' },
                tools: [{ store: 'backend' }], stateSchema: schema,
            });"#,
            "agent.ts",
        )
        .unwrap();
        assert!(ts.unsupported_keys.is_empty());
        let py = extract_agent_config_python(
            "# backend=old_backend\nagent = define_deep_agent(name='research', metadata={'memory': 'skills'}, tools=[{'store': 'backend'}])",
            "agent.py",
        ).unwrap();
        assert!(py.unsupported_keys.is_empty());
    }

    #[test]
    fn managed_key_extraction_detects_quoted_and_shorthand_properties() {
        for source in [
            "export const agent = defineDeepAgent({ name: 'research', memory });",
            "export const agent = defineDeepAgent({ name: 'research', 'memory': undefined });",
            "export const agent = defineDeepAgent({ name: 'research', ['memory']: [] });",
        ] {
            let fields = extract_agent_config_typescript(source, "agent.ts").unwrap();
            assert_eq!(fields.unsupported_keys, ["memory"]);
        }
    }

    #[test]
    fn typescript_factory_entry_without_direct_definition_has_dynamic_name() {
        let fields = extract_agent_config_typescript(
            concat!(
                "export const agent = () => {\n",
                "  throw new Error('factory must not run during build');\n",
                "};\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(
            fields,
            AgentConfigFields {
                name: AgentNameValue::Dynamic,
                model: None,
                is_factory: true,
                declares_disable_memory: false,
                unsupported_keys: Vec::new(),
                async_subagents: Vec::new(),
                unreadable_subagents: 0,
            }
        );
    }

    #[test]
    fn typescript_factory_entry_reads_static_name_from_concise_factory() {
        let fields = extract_agent_config_typescript(
            concat!(
                "export const agent = (_ctx) =>\n",
                "  defineDeepAgent({ name: 'Support_Assistant', model: selectModel(_ctx) })\n",
                ";\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(
            fields.name,
            AgentNameValue::Static("Support_Assistant".to_string())
        );
        assert!(fields.is_factory);
        assert!(fields.model.is_none());
    }

    #[test]
    fn typescript_factory_entry_reads_static_name_from_direct_return() {
        let fields = extract_agent_config_typescript(
            concat!(
                "export const agent = (_ctx) => {\n",
                "  const model = selectModel(_ctx);\n",
                "  return defineDeepAgent({ name: 'support', model });\n",
                "};\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Static("support".to_string()));
    }

    #[test]
    fn typescript_factory_entry_rejects_module_name_binding() {
        let fields = extract_agent_config_typescript(
            concat!(
                "const NAME = 'support';\n",
                "export const agent = () => defineDeepAgent({ name: NAME });\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
    }

    #[test]
    fn typescript_factory_entry_rejects_local_name_binding() {
        let fields = extract_agent_config_typescript(
            concat!(
                "export const agent = () => {\n",
                "  const NAME = 'support';\n",
                "  return defineDeepAgent({ name: NAME });\n",
                "};\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
    }

    #[test]
    fn typescript_factory_entry_rejects_computed_name() {
        let fields = extract_agent_config_typescript(
            "export const agent = (ctx) => defineDeepAgent({ name: ctx.name });",
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
    }

    #[test]
    fn typescript_factory_entry_rejects_control_flow_around_name() {
        let fields = extract_agent_config_typescript(
            concat!(
                "export const agent = (ctx) => {\n",
                "  if (ctx.internal) return defineDeepAgent({ name: 'internal' });\n",
                "  return defineDeepAgent({ name: 'external' });\n",
                "};\n",
            ),
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
    }

    #[test]
    fn typescript_static_entry_keeps_authored_name_and_model() {
        let fields = extract_agent_config_typescript(
            "export const agent = defineDeepAgent({ name: 'support', model: 'openai:gpt-5' });",
            "agent.ts",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Static("support".to_string()));
        assert_eq!(fields.model.as_deref(), Some("openai:gpt-5"));
        assert!(!fields.is_factory);
    }

    #[test]
    fn python_factory_entry_recognizes_exported_alias() {
        if !python3_available() {
            return;
        }
        let fields = extract_agent_config_python(
            concat!(
                "async def build_agent(ctx):\n",
                "    mark_factory_called()\n",
                "    return define_deep_agent(name='support')\n",
                "\n",
                "agent = build_agent\n",
            ),
            "agent.py",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Static("support".to_string()));
        assert!(fields.is_factory);
        assert!(fields.model.is_none());
    }

    #[test]
    fn python_factory_entry_requires_a_direct_definition() {
        if !python3_available() {
            return;
        }
        let fields =
            extract_agent_config_python("def agent(ctx):\n    return ctx\n", "agent.py").unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
        assert!(fields.is_factory);
    }

    #[test]
    fn python_factory_entry_reads_static_name_from_direct_return() {
        if !python3_available() {
            return;
        }
        let fields = extract_agent_config_python(
            concat!(
                "def build_agent(ctx):\n",
                "    model = select_model(ctx)\n",
                "    return define_deep_agent(name='Support_Assistant', model=model)\n",
                "\n",
                "agent = build_agent\n",
            ),
            "agent.py",
        )
        .unwrap();

        assert_eq!(
            fields.name,
            AgentNameValue::Static("Support_Assistant".to_string())
        );
        assert!(fields.is_factory);
    }

    #[test]
    fn python_factory_entry_rejects_computed_name() {
        if !python3_available() {
            return;
        }
        let fields = extract_agent_config_python(
            concat!(
                "def build_agent(ctx):\n",
                "    return define_deep_agent(name=ctx['name'])\n",
                "\n",
                "agent = build_agent\n",
            ),
            "agent.py",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
        assert!(fields.is_factory);
    }

    #[test]
    fn python_factory_entry_rejects_control_flow_around_name() {
        if !python3_available() {
            return;
        }
        let fields = extract_agent_config_python(
            concat!(
                "def build_agent(ctx):\n",
                "    if ctx['internal']:\n",
                "        return define_deep_agent(name='internal')\n",
                "    return define_deep_agent(name='external')\n",
                "\n",
                "agent = build_agent\n",
            ),
            "agent.py",
        )
        .unwrap();

        assert_eq!(fields.name, AgentNameValue::Dynamic);
        assert!(fields.is_factory);
    }

    /// Extraction against sources that are expected to parse; a parse error is
    /// a bug in the fixture, so these helpers unwrap it.
    fn ts_disable_memory(source: &str) -> bool {
        extract_declares_disable_memory_from_typescript(source, "agent.ts").unwrap()
    }

    fn py_disable_memory(source: &str) -> bool {
        extract_declares_disable_memory_from_python(source, "agent.py").unwrap()
    }

    fn ts_name(source: &str) -> AgentNameValue {
        extract_name_from_typescript(source, "agent.ts").unwrap()
    }

    #[test]
    fn typescript_async_subagents_are_rejected() {
        let error = extract_agent_config_typescript(
            "export const agent = defineDeepAgent({ name: 'lead', subagents: [{ name: 'r', async: true }] });\n",
            "agent.ts",
        )
        .unwrap_err();

        assert!(
            error.to_string().contains("only supported in Python"),
            "{error}"
        );
    }

    fn py_async_subagents(source: &str) -> Vec<AgentNameValue> {
        extract_agent_config_python(source, "agent.py")
            .unwrap()
            .async_subagents
    }

    #[test]
    fn python_async_subagents_are_read_from_inline_and_bound_dicts() {
        if !python3_available() {
            return;
        }
        let names = py_async_subagents(concat!(
            "writer = {'name': 'writer', 'description': 'd', 'async': True}\n",
            "agent = define_deep_agent(name='lead', subagents=[\n",
            "    {'name': 'researcher', 'description': 'd', 'async': True},\n",
            "    writer,\n",
            "    {'name': 'helper', 'description': 'd'},\n",
            "    {'name': 'off', 'description': 'd', 'async': False},\n",
            "    make_subagent(),\n",
            "])\n",
        ));

        assert_eq!(
            names,
            [
                AgentNameValue::Static("researcher".to_string()),
                AgentNameValue::Static("writer".to_string()),
            ]
        );
    }

    #[test]
    fn python_async_subagents_are_read_from_annotated_and_list_bindings() {
        if !python3_available() {
            return;
        }
        let names = py_async_subagents(concat!(
            "researcher: ManagedAsyncSubAgent = {'name': 'researcher', 'description': 'd', 'async': True}\n",
            "SUBAGENTS = [researcher]\n",
            "agent = define_deep_agent(name='lead', subagents=SUBAGENTS)\n",
        ));

        assert_eq!(names, [AgentNameValue::Static("researcher".to_string())]);
    }

    #[test]
    fn python_subagent_type_calls_and_none_are_not_unreadable() {
        if !python3_available() {
            return;
        }
        for source in [
            concat!(
                "helper = SubAgent(name='h', description='d', system_prompt='p')\n",
                "agent = define_deep_agent(name='lead', subagents=[\n",
                "    helper,\n",
                "    SubAgent(name='s', description='d', system_prompt='p'),\n",
                "    deepagents.CompiledSubAgent(name='c', description='d', runnable=graph),\n",
                "])\n",
            ),
            "agent = define_deep_agent(name='lead', subagents=None)\n",
        ] {
            let fields = extract_agent_config_python(source, "agent.py").unwrap();
            assert_eq!(fields.unreadable_subagents, 0, "{source}");
            assert!(fields.async_subagents.is_empty(), "{source}");
        }
    }

    #[test]
    fn python_subagent_spreads_without_async_entries_are_read() {
        if !python3_available() {
            return;
        }
        let read_with = |spread: &str, built: &str| {
            let source = format!(
                concat!(
                    "def agent(ctx):\n",
                    "    {}\n",
                    "    built = [{} for s in specs]\n",
                    "    worker = {{'name': 'worker', 'description': 'd', 'async': True}}\n",
                    "    return define_deep_agent(name='lead', subagents=[*sync, *built, worker])\n",
                ),
                spread,
                built
            );
            let fields = extract_agent_config_python(&source, "agent.py").unwrap();
            (fields.async_subagents, fields.unreadable_subagents)
        };
        let read = |spread: &str| read_with(spread, "{'name': s.name, 'description': 'd'}");
        let worker = vec![AgentNameValue::Static("worker".to_string())];

        assert_eq!(read("sync: list[SubAgent] = build()"), (worker.clone(), 0));
        assert_eq!(read("sync: list[dict] = build()"), (worker.clone(), 1));
        assert_eq!(read("sync = build()"), (worker.clone(), 1));
        // Each `**s` could copy `"async": True` in.
        assert_eq!(
            read_with(
                "sync: list[SubAgent] = build()",
                "{**s, 'description': 'd'}"
            ),
            (worker, 1)
        );
    }

    #[test]
    fn python_async_subagents_take_literal_keys_after_a_spread() {
        if !python3_available() {
            return;
        }
        let names = py_async_subagents(concat!(
            "agent = define_deep_agent(name='lead', subagents=[\n",
            "    {**worker('low'), 'name': 'low', 'async': True},\n",
            "    {'name': 'max', 'async': True, **worker('max')},\n",
            "    {'name': 'mid', **worker('mid'), 'async': True},\n",
            "    {'name': 'off', 'async': False, **worker('off')},\n",
            "])\n",
        ));

        // A spread after the literal keys could replace them at runtime. A sync
        // subagent (`"async": False`) is skipped, like one with no `async` key.
        assert_eq!(
            names,
            [
                AgentNameValue::Static("low".to_string()),
                AgentNameValue::Dynamic,
                AgentNameValue::Dynamic,
            ]
        );
    }

    #[test]
    fn python_async_subagents_are_read_from_factories() {
        if !python3_available() {
            return;
        }
        let names = py_async_subagents(concat!(
            "def agent(ctx):\n",
            "    researcher = {'name': 'researcher', 'description': 'd', 'async': True}\n",
            "    return define_deep_agent(name='lead', subagents=[researcher])\n",
        ));

        assert_eq!(names, [AgentNameValue::Static("researcher".to_string())]);
    }

    #[test]
    fn python_async_subagents_with_computed_fields_are_dynamic() {
        if !python3_available() {
            return;
        }
        let names = py_async_subagents(concat!(
            "agent = define_deep_agent(name='lead', subagents=[\n",
            "    {'name': NAME, 'async': True},\n",
            "    {'name': 'researcher', 'async': ENABLED},\n",
            "])\n",
        ));

        assert_eq!(names, [AgentNameValue::Dynamic, AgentNameValue::Dynamic]);
    }

    #[test]
    fn async_subagents_resolve_to_graphs_under_the_assistant() {
        let graphs = resolve_async_subagents(
            vec![AgentNameValue::Static("Deep Researcher".to_string())],
            "lead",
            "agent.py",
        )
        .unwrap();

        assert_eq!(
            graphs,
            [AsyncSubagentGraph {
                name: "Deep Researcher".to_string(),
                graph_id: "lead--deep-researcher".to_string(),
                export: "agent__deep_researcher".to_string(),
            }]
        );
    }

    fn static_config(async_subagents: Vec<AsyncSubagentGraph>) -> AgentStaticConfig {
        AgentStaticConfig {
            assistant_id: "lead".to_string(),
            model: None,
            is_factory: false,
            declares_disable_memory: false,
            async_subagents,
            unreadable_subagents: 0,
        }
    }

    #[test]
    fn served_agents_also_get_an_async_general_purpose_subagent() {
        let graphs = static_config(Vec::new()).served_async_subagents();

        assert_eq!(
            graphs,
            [AsyncSubagentGraph {
                name: "general-purpose".to_string(),
                graph_id: "lead--general-purpose".to_string(),
                export: "agent__general_purpose".to_string(),
            }]
        );
    }

    #[test]
    fn a_declared_async_general_purpose_subagent_is_not_added_twice() {
        let declared = resolve_async_subagents(
            vec![AgentNameValue::Static("general-purpose".to_string())],
            "lead",
            "agent.py",
        )
        .unwrap();

        assert_eq!(
            static_config(declared.clone()).served_async_subagents(),
            declared
        );
    }

    #[test]
    fn a_declared_subagent_named_like_general_purpose_is_not_added_twice() {
        let declared = resolve_async_subagents(
            vec![AgentNameValue::Static("General Purpose".to_string())],
            "lead",
            "agent.py",
        )
        .unwrap();

        assert_eq!(
            static_config(declared.clone()).served_async_subagents(),
            declared
        );
    }

    #[test]
    fn async_subagents_reject_dynamic_and_duplicate_names() {
        let dynamic = resolve_async_subagents(vec![AgentNameValue::Dynamic], "lead", "agent.py");
        assert!(dynamic.unwrap_err().to_string().contains("as literals"));

        let duplicate = resolve_async_subagents(
            vec![
                AgentNameValue::Static("researcher".to_string()),
                AgentNameValue::Static("Researcher".to_string()),
            ],
            "lead",
            "agent.py",
        );
        assert!(duplicate
            .unwrap_err()
            .to_string()
            .contains("names are compared after normalization"));
    }

    fn py_name(source: &str) -> Result<AgentNameValue> {
        extract_name_from_python(source, "agent.py")
    }

    fn python3_available() -> bool {
        toolchain::python3_available()
    }

    #[test]
    fn ts_disable_memory_is_reported_for_either_value() {
        // Presence is what matters: `false` used to mean "memory on", so both
        // spellings have to reach the migration error rather than one being
        // read as a harmless "off".
        for source in [
            "export const agent = defineDeepAgent({ disableMemory: true });\n",
            "export const agent = defineDeepAgent({ disableMemory: false });\n",
        ] {
            assert!(ts_disable_memory(source), "{source}");
        }
    }

    #[test]
    fn ts_disable_memory_reads_multiline_config() {
        let source = concat!(
            "import { defineDeepAgent } from \"managed-deepagents\";\n",
            "export const agent = defineDeepAgent({\n",
            "  model: \"openai:gpt-5\",\n",
            "  disableMemory: true,\n",
            "});\n",
        );
        assert!(ts_disable_memory(source));
    }

    #[test]
    fn ts_disable_memory_ignores_comments_and_unrelated_objects() {
        let source = concat!(
            "// disableMemory: true\n",
            "const other = { disableMemory: true };\n",
            "export const agent = defineDeepAgent({ model: \"openai:gpt-5\" });\n",
        );
        assert!(!ts_disable_memory(source));
    }

    #[test]
    fn ts_disable_memory_is_reported_for_dynamic_values() {
        // A dynamic value cannot be read, but it still has to fail the build:
        // the knob is gone, so there is nothing legitimate it could mean.
        let source = concat!(
            "const disabled = true;\n",
            "export const agent = defineDeepAgent({ disableMemory: disabled });\n",
        );
        assert!(ts_disable_memory(source));
    }

    #[test]
    fn ts_disable_memory_parses_tsx_sources() {
        let source = "export const agent = defineDeepAgent({ disableMemory: true });\n";
        assert!(extract_declares_disable_memory_from_typescript(source, "agent.tsx").unwrap());
    }

    #[test]
    fn ts_disable_memory_reports_a_syntax_error_instead_of_defaulting() {
        // The `agent` declarator does not survive the error, so the knob would
        // otherwise read as absent and the migration error would never fire.
        let source = "export const agent = defineDeepAgent({{ disableMemory: true });\n";
        let error = extract_declares_disable_memory_from_typescript(source, "agent.ts")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse agent.ts:"), "{error}");
    }

    #[test]
    fn py_disable_memory_reports_a_syntax_error_instead_of_defaulting() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(disable_memory=True\n";
        let error = extract_declares_disable_memory_from_python(source, "agent.py")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse agent.py:"), "{error}");
    }

    #[test]
    fn py_disable_memory_is_reported_for_either_value() {
        if !python3_available() {
            return;
        }
        for source in [
            "agent = define_deep_agent(disable_memory=True)\n",
            "agent = define_deep_agent(disable_memory=False)\n",
        ] {
            assert!(py_disable_memory(source), "{source}");
        }
    }

    #[test]
    fn py_disable_memory_reads_multiline_call() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "agent = define_deep_agent(\n",
            "    model=\"openai:gpt-5\",\n",
            "    disable_memory=True,\n",
            ")\n",
        );
        assert!(py_disable_memory(source));
    }

    #[test]
    fn py_disable_memory_reads_annotated_assignment() {
        if !python3_available() {
            return;
        }
        let source = "agent: object = define_deep_agent(disable_memory=True)\n";
        assert!(py_disable_memory(source));
    }

    #[test]
    fn py_disable_memory_ignores_unrelated_keywords() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "tool = make_tool(disable_memory=True)\n",
            "agent = define_deep_agent(model=\"openai:gpt-5\")\n",
        );
        assert!(!py_disable_memory(source));
    }

    #[test]
    fn py_disable_memory_is_reported_for_dynamic_values() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "disabled = True\n",
            "agent = define_deep_agent(disable_memory=disabled)\n",
        );
        assert!(py_disable_memory(source));
    }

    #[test]
    fn ts_name_reads_static_literal() {
        let source = "export const agent = defineDeepAgent({ name: \"research\" });\n";
        assert_eq!(ts_name(source), AgentNameValue::Static("research".into()));
    }

    #[test]
    fn ts_name_reads_no_substitution_template() {
        let source = "export const agent = defineDeepAgent({ name: `research` });\n";
        assert_eq!(ts_name(source), AgentNameValue::Static("research".into()));
    }

    #[test]
    fn ts_name_resolves_module_level_string_binding() {
        let source = concat!(
            "const AGENT_NAME = \"research\";\n",
            "export const agent = defineDeepAgent({ name: AGENT_NAME });\n",
        );
        assert_eq!(ts_name(source), AgentNameValue::Static("research".into()));
    }

    #[test]
    fn ts_name_is_absent_when_omitted() {
        let source = "export const agent = defineDeepAgent({ model: \"openai:gpt-5\" });\n";
        assert_eq!(ts_name(source), AgentNameValue::Absent);
    }

    #[test]
    fn ts_name_reports_a_syntax_error_rather_than_a_missing_name() {
        // The name is right there in the source, but the AST does not survive
        // the error, so the caller used to demand a `name` already written.
        let source = "export const agent = defineDeepAgent({ name: \"research\" });\nconst x = ;\n";
        let error = extract_name_from_typescript(source, "agent.ts")
            .unwrap_err()
            .to_string();
        assert!(
            error.starts_with("failed to parse agent.ts:2:11:"),
            "{error}"
        );
    }

    #[test]
    fn py_name_reports_a_syntax_error_rather_than_a_missing_name() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"research\"\n";
        let error = py_name(source).unwrap_err().to_string();
        assert!(error.starts_with("failed to parse agent.py:"), "{error}");
    }

    #[test]
    fn is_valid_assistant_id_matches_validate() {
        assert!(is_valid_assistant_id("my-agent"));
        assert!(!is_valid_assistant_id("1bad"));
        assert!(!is_valid_assistant_id(""));
    }

    #[test]
    fn ts_name_is_dynamic_for_env_lookup() {
        let source = "export const agent = defineDeepAgent({ name: process.env.AGENT_NAME });\n";
        assert_eq!(ts_name(source), AgentNameValue::Dynamic);
    }

    #[test]
    fn ts_name_is_dynamic_for_interpolated_template() {
        let source = concat!(
            "const suffix = \"bot\";\n",
            "export const agent = defineDeepAgent({ name: `research-${suffix}` });\n",
        );
        assert_eq!(ts_name(source), AgentNameValue::Dynamic);
    }

    #[test]
    fn ts_name_is_dynamic_for_unresolved_identifier() {
        let source = concat!(
            "const AGENT_NAME = process.env.NAME;\n",
            "export const agent = defineDeepAgent({ name: AGENT_NAME });\n",
        );
        assert_eq!(ts_name(source), AgentNameValue::Dynamic);
    }

    #[test]
    fn validate_assistant_id_accepts_safe_ids() {
        validate_assistant_id("agent", "agent.ts").unwrap();
        validate_assistant_id("research-assistant", "agent.ts").unwrap();
        validate_assistant_id("Research_Bot1", "agent.ts").unwrap();
    }

    #[test]
    fn validate_assistant_id_rejects_invalid_ids() {
        assert!(validate_assistant_id("", "agent.ts").is_err());
        assert!(validate_assistant_id("1agent", "agent.ts").is_err());
        assert!(validate_assistant_id("research assistant", "agent.ts").is_err());
        assert!(validate_assistant_id("foo/bar", "agent.ts").is_err());
    }

    #[test]
    fn py_name_reads_static_literal() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=\"research\")\n";
        assert_eq!(
            py_name(source).unwrap(),
            AgentNameValue::Static("research".into())
        );
    }

    #[test]
    fn py_name_skips_unrelated_agent_assignment_before_define_deep_agent() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "agent = helper(model=\"openai:gpt-5\")\n",
            "agent = define_deep_agent(name=\"research\")\n",
        );
        assert_eq!(
            py_name(source).unwrap(),
            AgentNameValue::Static("research".into())
        );
    }

    #[test]
    fn py_name_is_absent_when_omitted() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(model=\"openai:gpt-5\")\n";
        assert_eq!(py_name(source).unwrap(), AgentNameValue::Absent);
    }

    #[test]
    fn py_name_is_dynamic_for_variable_reference() {
        if !python3_available() {
            return;
        }
        let source = concat!(
            "NAME = \"research\"\n",
            "agent = define_deep_agent(name=NAME)\n",
        );
        assert_eq!(py_name(source).unwrap(), AgentNameValue::Dynamic);
    }

    #[test]
    fn py_name_is_dynamic_for_fstring() {
        if !python3_available() {
            return;
        }
        let source = "agent = define_deep_agent(name=f\"research\")\n";
        assert_eq!(py_name(source).unwrap(), AgentNameValue::Dynamic);
    }
}
