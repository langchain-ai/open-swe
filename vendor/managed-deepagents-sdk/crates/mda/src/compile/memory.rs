//! Read the project's durable-memory declaration and decide what deploy seeds.
//!
//! Durable memory is opt-in and owned by one root module: a project with no
//! `memory.{ts,tsx,mts,cts,py}` mounts nothing, whatever identity says. This
//! module is the single place build, deploy, and `mda dev` ask "does this project
//! have memory", so the generated entry, the Context Hub seed, and the CLI
//! summary can never disagree.
//!
//! Extraction is AST-based and scoped to the exported `memory` binding — the same
//! approach as identity and agent-model extraction — so comments and unrelated
//! string literals cannot false-trigger a mount. Constructor recognition follows
//! imports: `defineMemory as dm` / `define_memory as dm` and namespace imports
//! still count as the managed constructor.
//!
//! Unlike the identity summary, an unresolvable value is a hard error rather than
//! an unknown: deploy seeds the hot file from this answer, so guessing would
//! either seed a tree the author did not ask for or drop the one they did.

use std::fs;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{
    Argument, ArrayExpressionElement, CallExpression, Expression, ObjectExpression,
    ObjectPropertyKind,
};
use serde::Deserialize;

use crate::agent_config;
use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};
use crate::typescript::{
    collect_managed_export_bindings, parse_typescript, statement_variable_declaration,
    static_string_expression, ManagedExportBindings,
};

use super::discovery::find_memory_entry;
use super::identity::read_identity_summary;

/// Canonical TypeScript export name for the memory constructor.
const TS_DEFINE_MEMORY: &str = "defineMemory";

/// Root memory declaration discovered for a project.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemorySummary {
    /// Path relative to the project root (`memory.ts`, `memory.py`, …).
    pub path: PathBuf,
    /// Statically resolved durable-memory scope.
    pub scope: MemoryLayers,
}

/// Durable-memory scope mounted by the root declaration.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MemoryLayers {
    /// Deployment-shared memory mounted at `/memories/agent/`.
    pub agent: bool,
    /// Caller-scoped memory mounted at `/memories/user/`.
    pub user: bool,
}

/// What memory this project has, from the one place that decides it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MemoryDecision {
    /// A root declaration was found (it may still resolve to no scope).
    Declared(MemorySummary),
    /// No declaration: no durable memory.
    None,
}

impl MemoryDecision {
    /// Whether deploy and dev should seed the shared agent layer.
    pub fn agent_enabled(&self) -> bool {
        match self {
            Self::Declared(summary) => summary.scope.agent,
            Self::None => false,
        }
    }

    /// Whether runs mount caller-scoped memory.
    pub fn user_enabled(&self) -> bool {
        match self {
            Self::Declared(summary) => summary.scope.user,
            Self::None => false,
        }
    }
}

/// Resolve the project's memory decision, rejecting the retired knobs.
///
/// The legacy checks live here rather than at each call site so a project can
/// never end up with two answers to "which layers does a run mount": whichever
/// entry point asks (build, deploy, dev) gets the same error.
pub(crate) fn resolve_memory(project: &Project) -> Result<MemoryDecision> {
    assert_no_legacy_memory_config(project)?;
    let Some(rel) = find_memory_entry(project) else {
        return Ok(MemoryDecision::None);
    };
    let path = project.root.join(&rel);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let file_label = rel.to_string_lossy().to_string();
    let declared = match project.language {
        Language::TypeScript => extract_memory_layers_typescript(&source, &file_label)?,
        Language::Python => extract_memory_layers_python(&source, &file_label)?,
    };
    let scope = match declared {
        Some(declared) => declared.mounted_layers(&file_label)?,
        None => {
            let declare = declare_memory_snippet(project.language);
            let verb = project.language.ident("export", "declare");
            let imperative = project.language.ident("Export", "Declare");
            bail!(
                "{file_label} does not {verb} a static `memory` declaration. \
                 {imperative} `memory` from a `{declare}` call with explicit named layers. \
                 Deploy seeds durable memory from this value, so it cannot be computed at runtime. Delete the file to \
                 keep no memory."
            )
        }
    };
    if scope.user && read_identity_summary(project)?.is_none() {
        bail!(
            "{file_label} enables user memory, but user memory requires identity.{} so the runtime can resolve a trusted caller ID",
            project.language.extension(),
        );
    }
    Ok(MemoryDecision::Declared(MemorySummary { path: rel, scope }))
}

/// Fail when a project still configures memory the old way.
///
/// Both knobs are refused outright instead of being read as "off": `disableMemory:
/// false` and `scope.memory: "agent"` used to *enable* memory, so honoring either
/// under an opt-in contract would silently invert what the author wrote.
fn assert_no_legacy_memory_config(project: &Project) -> Result<()> {
    let language = project.language;
    let declare = declare_memory_snippet(language);
    let verb = language.ident("export", "declare");
    if agent_config::agent_declares_disable_memory(project)? {
        bail!(
            "{} sets `{}`, which is no longer \
             supported. Durable memory is opt-in and declared in its own module: \
             {verb} `memory` from memory.{} with `{declare}` \
             to enable it, or leave that file out to keep no memory. Remove the \
             flag from the agent definition.",
            project.agent_file.display(),
            language.ident("disableMemory", "disable_memory"),
            language.extension(),
        );
    }
    if let Some(identity) = read_identity_summary(project)? {
        if identity.declares_memory {
            bail!(
                "{} sets `scope.memory`, which is no longer supported. Durable \
                 memory has its own root declaration: {verb} `memory` from \
                 memory.{} with `{declare}`, and drop \
                 `scope.memory` from the identity declaration.",
                identity.path.display(),
                language.extension(),
            );
        }
    }
    Ok(())
}

/// The `memory` constructor call an author of this language would write.
pub(crate) fn declare_memory_snippet(language: Language) -> &'static str {
    language.ident(
        "defineMemory({ agent: memoryLayer() })",
        "define_memory(agent=MemoryLayer())",
    )
}

/// A statically resolved `scope` value, before it is reduced to a mount decision.
#[derive(Debug, Clone, PartialEq, Eq)]
enum MemoryLayersValue {
    /// `scope: [{ type: "agent" }]`.
    Agent,
    /// `scope: [{ type: "user" }]`.
    User,
    /// Both layers, normalized agent first.
    AgentAndUser,
    /// `scope: []` / omitted.
    None,
    /// A layer type appeared more than once.
    Duplicate(&'static str),
    /// The retired string shorthand (`scope: ["agent"]`, `scope: "agent"`).
    Shorthand(String),
    /// A literal MDA recognizes but does not accept yet (per-layer options).
    Reserved(String),
    /// A literal outside the contract entirely.
    Unknown(String),
    /// Not a literal at all (an identifier, a call, an env lookup, …).
    Dynamic(String),
}

impl MemoryLayersValue {
    fn mounted_layers(&self, file_label: &str) -> Result<MemoryLayers> {
        match self {
            Self::Agent => Ok(MemoryLayers {
                agent: true,
                user: false,
            }),
            Self::User => Ok(MemoryLayers {
                agent: false,
                user: true,
            }),
            Self::AgentAndUser => Ok(MemoryLayers {
                agent: true,
                user: true,
            }),
            Self::None => Ok(MemoryLayers::default()),
            Self::Duplicate(layer) => {
                bail!("{file_label} declares the {layer} memory layer more than once")
            }
            Self::Shorthand(what) => bail!(
                "{file_label} declares memory as {what}. A layer names whose memory \
                 is mounted. Use named `agent` and `user` options with layer constructors."
            ),
            Self::Reserved(what) => bail!(
                "{file_label} declares a memory layer with {what}, which is not \
                 supported yet. Per-layer mounts, access modes, and retention are \
                 not implemented. Named layers accept only an optional `allow` callback."
            ),
            Self::Unknown(what) => bail!(
                "{file_label} declares an unknown memory layer: {what}. Use \
                 named `agent` and `user` layers."
            ),
            Self::Dynamic(what) => bail!(
                "{file_label} declares memory that is not a static literal \
                 ({what}). Deploy seeds durable memory from this value, so it has to \
                 use explicit named layer constructors."
            ),
        }
    }
}

/// Classify one resolved layer type.
fn layer_type_value(layer_type: &str, has_id: bool) -> MemoryLayersValue {
    match layer_type {
        // The deployment owns the agent layer, so there is no id to assert.
        "agent" if has_id => MemoryLayersValue::Unknown("an `id` on the agent layer".to_string()),
        "agent" => MemoryLayersValue::Agent,
        "user" => MemoryLayersValue::User,
        other => MemoryLayersValue::Unknown(format!("type {other:?}")),
    }
}

/// Reduce a list of layer entries to the canonical mounted set.
fn reduce_layer_entries(entries: Vec<MemoryLayersValue>) -> MemoryLayersValue {
    let mut agent = false;
    let mut user = false;
    for entry in entries {
        match entry {
            MemoryLayersValue::Agent if agent => return MemoryLayersValue::Duplicate("agent"),
            MemoryLayersValue::Agent => agent = true,
            MemoryLayersValue::User if user => return MemoryLayersValue::Duplicate("user"),
            MemoryLayersValue::User => user = true,
            // A duplicate, shorthand, reserved, unknown, or dynamic entry decides
            // the whole list.
            other => return other,
        }
    }
    match (agent, user) {
        (true, true) => MemoryLayersValue::AgentAndUser,
        (true, false) => MemoryLayersValue::Agent,
        (false, true) => MemoryLayersValue::User,
        (false, false) => MemoryLayersValue::None,
    }
}

fn extract_memory_layers_typescript(
    source: &str,
    file_name: &str,
) -> Result<Option<MemoryLayersValue>> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    let bindings = collect_managed_export_bindings(&parsed.program.body, TS_DEFINE_MEMORY);
    let layer_bindings = collect_managed_export_bindings(&parsed.program.body, "memoryLayer");

    for statement in &parsed.program.body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let is_memory = declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == "memory");
            if !is_memory {
                continue;
            }
            let Some(Expression::CallExpression(call)) = declarator.init.as_ref() else {
                continue;
            };
            // Skip unrelated calls assigned to `memory` so a later
            // `defineMemory(...)` declarator still wins.
            if !is_memory_export_call(&call.callee, &bindings, TS_DEFINE_MEMORY) {
                continue;
            }
            return Ok(Some(call_memory_layers(call, &layer_bindings)));
        }
    }
    Ok(None)
}

fn is_memory_export_call(
    callee: &Expression<'_>,
    bindings: &ManagedExportBindings,
    export: &str,
) -> bool {
    match callee {
        Expression::Identifier(id) => bindings.aliases.contains(id.name.as_str()),
        Expression::StaticMemberExpression(member) if member.property.name.as_str() == export => {
            matches!(
                &member.object,
                Expression::Identifier(id) if bindings.namespaces.contains(id.name.as_str())
            )
        }
        _ => false,
    }
}

/// Resolve `scope` off a `defineMemory(...)` call. A declaration that names no
/// layer mounts nothing, matching the runtime default.
fn call_memory_layers(
    call: &CallExpression<'_>,
    layer_bindings: &ManagedExportBindings,
) -> MemoryLayersValue {
    if call.arguments.len() > 1 {
        return MemoryLayersValue::Dynamic("extra positional arguments".to_string());
    }
    let Some(Argument::ObjectExpression(options)) = call.arguments.first() else {
        // `defineMemory()` (or a non-object argument the runtime rejects anyway).
        return match call.arguments.first() {
            None => MemoryLayersValue::None,
            Some(_) => MemoryLayersValue::Dynamic("a non-object argument".to_string()),
        };
    };
    named_memory_layers(options, layer_bindings).unwrap_or_else(|| object_memory_layers(options))
}

/// Read named declarations without executing their policies.
fn named_memory_layers(
    options: &ObjectExpression<'_>,
    bindings: &ManagedExportBindings,
) -> Option<MemoryLayersValue> {
    let has_named = options.properties.iter().any(|property| {
        matches!(property, ObjectPropertyKind::ObjectProperty(property)
            if property.key.is_specific_static_name("agent") || property.key.is_specific_static_name("user"))
    });
    if options.properties.iter().any(|property| {
        matches!(property, ObjectPropertyKind::ObjectProperty(property) if property.computed)
    }) {
        return Some(MemoryLayersValue::Dynamic("a computed memory option".to_string()));
    }
    if !has_named {
        return None;
    }
    let mut agent = None;
    let mut user = None;
    for property in &options.properties {
        let ObjectPropertyKind::ObjectProperty(property) = property else {
            return Some(MemoryLayersValue::Dynamic(
                "a spread that can set memory layers".to_string(),
            ));
        };
        let target = if property.key.is_specific_static_name("agent") {
            &mut agent
        } else if property.key.is_specific_static_name("user") {
            &mut user
        } else {
            return Some(MemoryLayersValue::Unknown(
                "named layers only accept agent and user; do not mix them with scope".to_string(),
            ));
        };
        *target = Some(&property.value);
    }
    let mut entries = Vec::new();
    for (name, value) in [("agent", agent), ("user", user)] {
        let Some(value) = value else { continue };
        let Expression::CallExpression(call) = value else {
            return Some(MemoryLayersValue::Dynamic(format!(
                "{name} must use memoryLayer(...)"
            )));
        };
        if !is_memory_export_call(&call.callee, bindings, "memoryLayer") || call.arguments.len() > 1
        {
            return Some(MemoryLayersValue::Dynamic(format!(
                "{name} must use memoryLayer(...)"
            )));
        }
        if let Some(argument) = call.arguments.first() {
            let Argument::ObjectExpression(layer) = argument else {
                return Some(MemoryLayersValue::Dynamic(
                    "memoryLayer options must be an object literal".to_string(),
                ));
            };
            for option in &layer.properties {
                let ObjectPropertyKind::ObjectProperty(option) = option else {
                    return Some(MemoryLayersValue::Dynamic(
                        "a spread inside memoryLayer".to_string(),
                    ));
                };
                if option.computed || !option.key.is_specific_static_name("allow") {
                    return Some(MemoryLayersValue::Unknown(
                        "memoryLayer only accepts allow".to_string(),
                    ));
                }
            }
        }
        entries.push(layer_type_value(name, false));
    }
    Some(reduce_layer_entries(entries))
}

/// What last decides `scope` in an options object literal.
///
/// JavaScript evaluates properties in order and the last write wins, so reading
/// the first `scope` key is not enough to know what the runtime receives.
enum LayersWriter<'a> {
    /// A literal `scope:` nothing later can override.
    Literal(&'a Expression<'a>),
    /// The retired `scope:` key, which names the shape that replaced it.
    Scope,
    /// A spread that may set `scope` (`{ ...opts }`, `{ scope: [], ...over }`).
    Spread,
    /// No `scope` and no spread.
    Absent,
}

/// Resolve `scope` off the options object, refusing what a spread could change.
///
/// A spread *before* an explicit `scope` cannot change it, so `{ ...opts,
/// scope: [...] }` still resolves. A spread after it, or a spread with no literal
/// `scope` at all, can hand the runtime a value the build cannot see — and deploy
/// seeds the hot file from this answer, so those shapes fail the build instead of
/// being guessed at.
fn object_memory_layers(options: &ObjectExpression<'_>) -> MemoryLayersValue {
    let mut writer = LayersWriter::Absent;
    for property in &options.properties {
        match property {
            ObjectPropertyKind::ObjectProperty(property)
                if property.key.is_specific_static_name("scope") =>
            {
                writer = LayersWriter::Literal(&property.value);
            }
            // Named so the retired shorthand reports the new shape rather than
            // reading as "no memory declared".
            ObjectPropertyKind::ObjectProperty(property)
                if property.key.is_specific_static_name("layers") =>
            {
                writer = LayersWriter::Scope;
            }
            ObjectPropertyKind::ObjectProperty(_) => {}
            ObjectPropertyKind::SpreadProperty(_) => writer = LayersWriter::Spread,
        }
    }
    let scope = match writer {
        LayersWriter::Literal(scope) => scope,
        LayersWriter::Scope => {
            return MemoryLayersValue::Shorthand("`scope`".to_string());
        }
        LayersWriter::Spread => {
            return MemoryLayersValue::Dynamic(
                "a spread that can set or override `scope`".to_string(),
            );
        }
        LayersWriter::Absent => return MemoryLayersValue::None,
    };
    if let Expression::ObjectExpression(object) = scope {
        return layer_entry_value(object);
    }
    let Expression::ArrayExpression(array) = scope else {
        return match static_string_expression(scope) {
            Some(text) => MemoryLayersValue::Shorthand(format!("{text:?}")),
            None => MemoryLayersValue::Dynamic("a computed value".to_string()),
        };
    };
    let entries = array
        .elements
        .iter()
        .map(|element| match element {
            ArrayExpressionElement::ObjectExpression(object) => layer_entry_value(object),
            ArrayExpressionElement::StringLiteral(literal) => {
                MemoryLayersValue::Shorthand(format!("{:?}", literal.value.as_str()))
            }
            other => match other.as_expression().and_then(static_string_expression) {
                Some(text) => MemoryLayersValue::Shorthand(format!("{text:?}")),
                None => MemoryLayersValue::Dynamic("a dynamic list entry".to_string()),
            },
        })
        .collect();
    reduce_layer_entries(entries)
}

/// Resolve one layer object (`{ type: "user", id: "..." }`).
///
/// `type` and `id` must be static. The runtime validates and calls a user layer's
/// optional `allow` callback; extraction does not evaluate or serialize it.
fn layer_entry_value(object: &ObjectExpression<'_>) -> MemoryLayersValue {
    let mut layer_type: Option<String> = None;
    let mut has_id = false;
    for property in &object.properties {
        let ObjectPropertyKind::ObjectProperty(property) = property else {
            return MemoryLayersValue::Dynamic("a spread inside a layer".to_string());
        };
        if property.key.is_specific_static_name("type") {
            match static_string_expression(&property.value) {
                Some(text) => layer_type = Some(text),
                None => {
                    return MemoryLayersValue::Dynamic("a computed layer `type`".to_string());
                }
            }
        } else if property.key.is_specific_static_name("id") {
            has_id = true;
            if static_string_expression(&property.value).is_none() {
                return MemoryLayersValue::Dynamic("a computed layer `id`".to_string());
            }
        } else if !property.key.is_specific_static_name("allow") {
            let name = property
                .key
                .static_name()
                .map(|name| format!("the key `{name}`"))
                .unwrap_or_else(|| "a computed key".to_string());
            return MemoryLayersValue::Reserved(name);
        }
    }
    match layer_type {
        Some(layer_type) => layer_type_value(&layer_type, has_id),
        None => MemoryLayersValue::Unknown("a layer with no `type`".to_string()),
    }
}

#[derive(Debug, Deserialize)]
struct PythonMemoryLayers {
    /// Mounted layer set or an error classification.
    kind: String,
    /// Rendering of the offending value, for the non-mounting cases.
    detail: Option<String>,
}

fn extract_memory_layers_python(
    source: &str,
    file_label: &str,
) -> Result<Option<MemoryLayersValue>> {
    let output = run_python_extractor(
        scripts::MEMORY_LAYERS,
        source,
        file_label,
        "the memory declaration",
    )?
    .ok_or_else(|| {
        anyhow::anyhow!("memory layers extractor produced no output for {file_label}")
    })?;
    let extracted: PythonMemoryLayers = serde_json::from_str(&output)
        .with_context(|| format!("failed to parse memory scope JSON from {file_label}"))?;
    let detail = extracted
        .detail
        .unwrap_or_else(|| "a computed value".to_string());
    Ok(match extracted.kind.as_str() {
        "absent" => None,
        "agent" => Some(MemoryLayersValue::Agent),
        "user" => Some(MemoryLayersValue::User),
        "agent_user" => Some(MemoryLayersValue::AgentAndUser),
        "none" => Some(MemoryLayersValue::None),
        "duplicate_agent" => Some(MemoryLayersValue::Duplicate("agent")),
        "duplicate_user" => Some(MemoryLayersValue::Duplicate("user")),
        "shorthand" => Some(MemoryLayersValue::Shorthand(detail)),
        "reserved" => Some(MemoryLayersValue::Reserved(detail)),
        "unknown" => Some(MemoryLayersValue::Unknown(detail)),
        "dynamic" => Some(MemoryLayersValue::Dynamic(detail)),
        other => bail!("unexpected memory scope kind from {file_label}: {other:?}"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::toolchain;

    fn ts_layers(source: &str) -> Option<MemoryLayersValue> {
        extract_memory_layers_typescript(source, "memory.ts").unwrap()
    }

    fn py_layers(source: &str) -> Option<MemoryLayersValue> {
        extract_memory_layers_python(source, "memory.py").unwrap()
    }

    #[test]
    fn reads_named_layers_without_evaluating_policies() {
        for (ts, py, expected) in [
            (
                "agent: memoryLayer()",
                "agent=MemoryLayer()",
                MemoryLayersValue::Agent,
            ),
            (
                "user: memoryLayer({ allow: policy })",
                "user=MemoryLayer(allow=policy)",
                MemoryLayersValue::User,
            ),
            (
                "agent: memoryLayer({ allow: () => { throw Error(); } }), user: memoryLayer()",
                "agent=MemoryLayer(allow=lambda ctx: 1 / 0), user=MemoryLayer()",
                MemoryLayersValue::AgentAndUser,
            ),
        ] {
            assert_eq!(
                ts_layers(&format!("export const memory = defineMemory({{ {ts} }});")),
                Some(expected.clone())
            );
            assert_eq!(
                py_layers(&format!("memory = define_memory({py})")),
                Some(expected)
            );
        }
    }

    #[test]
    fn reads_named_layer_import_aliases_and_namespaces() {
        for source in [
            "import { defineMemory as dm, memoryLayer as ml } from 'managed-deepagents'; export const memory = dm({ user: ml() });",
            "import * as mda from 'managed-deepagents'; export const memory = mda.defineMemory({ user: mda.memoryLayer() });",
        ] {
            assert_eq!(ts_layers(source), Some(MemoryLayersValue::User));
        }
        for source in [
            "from managed_deepagents import define_memory as dm, MemoryLayer as ML\nmemory = dm(user=ML())",
            "import managed_deepagents as mda\nmemory = mda.define_memory(user=mda.MemoryLayer())",
        ] {
            assert_eq!(py_layers(source), Some(MemoryLayersValue::User));
        }
    }

    #[test]
    fn rejects_unreadable_named_layers_and_mixed_declarations() {
        for options in [
            "agent: true",
            "agent: {}",
            "user: makeLayer()",
            "agent: memoryLayer(), ...opts",
            "...opts, user: memoryLayer()",
            "[kind]: memoryLayer()",
            "agent: memoryLayer(), scope: []",
            "agent: memoryLayer({ ...opts })",
        ] {
            let value = ts_layers(&format!(
                "export const memory = defineMemory({{ {options} }});"
            ));
            assert!(
                matches!(
                    value,
                    Some(MemoryLayersValue::Dynamic(_)) | Some(MemoryLayersValue::Unknown(_))
                ),
                "{options}: {value:?}"
            );
        }
        for options in [
            "agent=True",
            "agent={}",
            "user=make_layer()",
            "agent=MemoryLayer(), **opts",
            "agent=MemoryLayer(), scope=[]",
            "agent=MemoryLayer(**opts)",
        ] {
            let value = py_layers(&format!("memory = define_memory({options})"));
            assert!(
                matches!(
                    value,
                    Some(MemoryLayersValue::Dynamic(_)) | Some(MemoryLayersValue::Unknown(_))
                ),
                "{options}: {value:?}"
            );
        }
    }

    #[test]
    fn ts_reads_the_agent_layer() {
        for source in [
            "export const memory = defineMemory({ agent: memoryLayer() });\n",
            "export const memory = defineMemory({\n  scope: [{ type: \"agent\" }],\n});\n",
        ] {
            assert_eq!(
                ts_layers(source),
                Some(MemoryLayersValue::Agent),
                "{source}"
            );
        }
    }

    #[test]
    fn reads_single_scope_objects() {
        assert_eq!(
            ts_layers(r#"export const memory = defineMemory({ scope: { type: "user" } });"#),
            Some(MemoryLayersValue::User)
        );
        assert_eq!(
            py_layers(r#"memory = define_memory(scope={"type": "agent"})"#),
            Some(MemoryLayersValue::Agent)
        );
    }

    #[test]
    fn ts_reads_user_and_both_layers() {
        assert_eq!(
            ts_layers("export const memory = defineMemory({ scope: [{ type: \"user\" }] });\n"),
            Some(MemoryLayersValue::User)
        );
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"user\" }, { type: \"agent\" }] });\n"
            ),
            Some(MemoryLayersValue::AgentAndUser)
        );
    }

    #[test]
    fn ts_reads_a_user_layer_that_asserts_an_id() {
        // The id is an assertion the runtime checks against the authenticated
        // principal, so the build only has to see that it is written out.
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"user\", id: \"usr_1\" }] });\n"
            ),
            Some(MemoryLayersValue::User)
        );
    }

    #[test]
    fn ts_reads_user_allow_callbacks_without_evaluating_them() {
        for callback in [
            "(context) => context.source?.audience === 'private'",
            "async (context) => context.source?.audience === 'private'",
            "function (context) { return context.source?.audience === 'private'; }",
            "async function (context) { return context.source?.audience === 'private'; }",
            "allowUserMemory",
        ] {
            let source = format!(
                "import {{ defineMemory as dm }} from 'managed-deepagents';\n\
                 import {{ allowUserMemory }} from './policy';\n\
                 export const memory = dm({{ scope: [{{ type: 'agent' }}, \
                 {{ allow: {callback}, type: 'user', id: 'usr_1' }}] }});"
            );
            assert_eq!(ts_layers(&source), Some(MemoryLayersValue::AgentAndUser));
        }
    }

    #[test]
    fn ts_reads_allow_on_the_agent_layer() {
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: { allow: policy, type: 'agent' } });"
            ),
            Some(MemoryLayersValue::Agent)
        );
    }

    #[test]
    fn ts_allow_keeps_layer_ownership_static() {
        for scope in [
            "layers",
            "[{ allow: policy, type: kind }]",
            "[{ allow: policy, type: 'user', id: caller }]",
            "[{ allow: policy, ...base }]",
            "[{ allow: policy, type: 'user' }, ...layers]",
        ] {
            let source = format!("export const memory = defineMemory({{ scope: {scope} }});");
            assert!(
                matches!(ts_layers(&source), Some(MemoryLayersValue::Dynamic(_))),
                "{source}"
            );
        }
    }

    #[test]
    fn ts_allow_does_not_hide_invalid_layers() {
        for (scope, expected) in [
            (
                "[{ allow: policy, type: 'user' }, { type: 'user' }]",
                MemoryLayersValue::Duplicate("user"),
            ),
            (
                "[{ allow: policy, type: 'workspace' }]",
                MemoryLayersValue::Unknown("type \"workspace\"".to_string()),
            ),
            (
                "[{ allow: policy }]",
                MemoryLayersValue::Unknown("a layer with no `type`".to_string()),
            ),
            (
                "[{ allow: policy, type: 'user', access: 'rw' }]",
                MemoryLayersValue::Reserved("the key `access`".to_string()),
            ),
        ] {
            let source = format!("export const memory = defineMemory({{ scope: {scope} }});");
            assert_eq!(ts_layers(&source), Some(expected), "{source}");
        }
    }

    #[test]
    fn ts_refuses_an_id_on_the_agent_layer() {
        // The deployment owns the agent layer, so there is no principal to assert.
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"agent\", id: \"usr_1\" }] });\n"
            ),
            Some(MemoryLayersValue::Unknown(
                "an `id` on the agent layer".to_string()
            ))
        );
    }

    #[test]
    fn ts_reads_no_memory_for_an_inert_declaration() {
        // A file that names no layer is legal and mounts nothing — memory is
        // opt-in, so silence means "no memory" rather than a default layer.
        for source in [
            "export const memory = defineMemory();\n",
            "export const memory = defineMemory({});\n",
            "export const memory = defineMemory({ scope: [] });\n",
        ] {
            assert_eq!(ts_layers(source), Some(MemoryLayersValue::None), "{source}");
        }
    }

    #[test]
    fn ts_follows_import_aliases_and_namespaces() {
        let aliased = concat!(
            "import { defineMemory as dm } from \"managed-deepagents\";\n",
            "export const memory = dm({ scope: [{ type: \"agent\" }] });\n",
        );
        assert_eq!(ts_layers(aliased), Some(MemoryLayersValue::Agent));

        let namespaced = concat!(
            "import * as mda from \"managed-deepagents\";\n",
            "export const memory = mda.defineMemory({ agent: memoryLayer() });\n",
        );
        assert_eq!(ts_layers(namespaced), Some(MemoryLayersValue::Agent));
    }

    #[test]
    fn ts_ignores_comments_and_unrelated_bindings() {
        let source = concat!(
            "// export const memory = defineMemory({ agent: memoryLayer() });\n",
            "const other = defineMemory({ agent: memoryLayer() });\n",
        );
        assert_eq!(ts_layers(source), None);
    }

    #[test]
    fn ts_names_the_retired_string_shorthand() {
        // The shape that shipped before scope has to report the new form rather
        // than read as "no memory declared".
        assert_eq!(
            ts_layers("export const memory = defineMemory({ scope: \"agent\" });\n"),
            Some(MemoryLayersValue::Shorthand("\"agent\"".to_string()))
        );
        assert_eq!(
            ts_layers("export const memory = defineMemory({ scope: \"agent\" });\n"),
            Some(MemoryLayersValue::Shorthand("\"agent\"".to_string()))
        );
        assert_eq!(
            ts_layers("export const memory = defineMemory({ scope: [\"agent\"] });\n"),
            Some(MemoryLayersValue::Shorthand("\"agent\"".to_string()))
        );
    }

    #[test]
    fn ts_names_reserved_and_unknown_layers() {
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"agent\", access: \"rw\" }] });\n"
            ),
            Some(MemoryLayersValue::Reserved("the key `access`".to_string()))
        );
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"workspace\" }] });\n"
            ),
            Some(MemoryLayersValue::Unknown("type \"workspace\"".to_string()))
        );
        assert_eq!(
            ts_layers("export const memory = defineMemory({ scope: [{}] });\n"),
            Some(MemoryLayersValue::Unknown(
                "a layer with no `type`".to_string()
            ))
        );
    }

    #[test]
    fn ts_reports_a_duplicate_layer() {
        assert_eq!(
            ts_layers(
                "export const memory = defineMemory({ scope: [{ type: \"agent\" }, { type: \"agent\" }] });\n"
            ),
            Some(MemoryLayersValue::Duplicate("agent"))
        );
    }

    #[test]
    fn ts_treats_a_dynamic_layer_value_as_unresolvable() {
        let computed = concat!(
            "const scope = process.env.LAYERS;\n",
            "export const memory = defineMemory({ scope });\n",
        );
        assert_eq!(
            ts_layers(computed),
            Some(MemoryLayersValue::Dynamic("a computed value".to_string()))
        );

        let computed_type = concat!(
            "const kind = process.env.KIND;\n",
            "export const memory = defineMemory({ scope: [{ type: kind }] });\n",
        );
        assert_eq!(
            ts_layers(computed_type),
            Some(MemoryLayersValue::Dynamic(
                "a computed layer `type`".to_string()
            ))
        );

        let computed_id = concat!(
            "const caller = process.env.CALLER;\n",
            "export const memory = defineMemory({ scope: [{ type: \"user\", id: caller }] });\n",
        );
        assert_eq!(
            ts_layers(computed_id),
            Some(MemoryLayersValue::Dynamic(
                "a computed layer `id`".to_string()
            ))
        );

        let spread_layer = "export const memory = defineMemory({ scope: [{ ...base }] });\n";
        assert_eq!(
            ts_layers(spread_layer),
            Some(MemoryLayersValue::Dynamic(
                "a spread inside a layer".to_string()
            ))
        );
    }

    #[test]
    fn ts_refuses_a_spread_that_can_decide_layers() {
        // JS evaluates the spread last, so the runtime would mount memory while
        // the build read none — deploy seeds from this answer, so the shapes it
        // cannot read have to fail rather than guess.
        for source in [
            "export const memory = defineMemory({ scope: [], ...override });\n",
            "export const memory = defineMemory({ ...opts });\n",
        ] {
            assert_eq!(
                ts_layers(source),
                Some(MemoryLayersValue::Dynamic(
                    "a spread that can set or override `scope`".to_string()
                )),
                "{source}"
            );
        }
    }

    #[test]
    fn ts_resolves_a_spread_that_cannot_reach_layers() {
        // A spread before an explicit `scope` cannot change it, so this is still
        // provably final and does not need to fail.
        let source =
            "export const memory = defineMemory({ ...opts, scope: [{ type: \"agent\" }] });\n";
        assert_eq!(ts_layers(source), Some(MemoryLayersValue::Agent));
    }

    #[test]
    fn ts_takes_the_last_layers_key_like_javascript_does() {
        let source =
            "export const memory = defineMemory({ scope: [{ type: \"agent\" }], scope: [] });\n";
        assert_eq!(ts_layers(source), Some(MemoryLayersValue::None));
    }

    #[test]
    fn ts_reports_a_syntax_error_instead_of_defaulting() {
        // The `memory` declarator does not survive the error, so the declaration
        // would otherwise read as absent and silently drop durable memory.
        let source = "export const memory = defineMemory({{ scope: [] });\n";
        let error = extract_memory_layers_typescript(source, "memory.ts")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse memory.ts:"), "{error}");
    }

    #[test]
    fn non_mounting_values_fail_with_actionable_errors() {
        let shorthand = MemoryLayersValue::Shorthand("`scope`".to_string())
            .mounted_layers("memory.ts")
            .unwrap_err()
            .to_string();
        assert!(shorthand.contains("names whose memory"), "{shorthand}");
        assert!(
            shorthand.contains("named `agent` and `user`"),
            "{shorthand}"
        );

        let reserved = MemoryLayersValue::Reserved("the key `access`".to_string())
            .mounted_layers("memory.ts")
            .unwrap_err()
            .to_string();
        assert!(reserved.contains("not supported yet"), "{reserved}");
        assert!(reserved.contains("memory.ts"), "{reserved}");

        let unknown = MemoryLayersValue::Unknown("type \"workspace\"".to_string())
            .mounted_layers("memory.py")
            .unwrap_err()
            .to_string();
        assert!(unknown.contains("unknown memory layer"), "{unknown}");

        // The dynamic case says why a literal is required, since the author did
        // not write anything invalid — just something the build cannot read.
        let dynamic = MemoryLayersValue::Dynamic("a computed value".to_string())
            .mounted_layers("memory.ts")
            .unwrap_err()
            .to_string();
        assert!(dynamic.contains("not a static literal"), "{dynamic}");
        assert!(dynamic.contains("Deploy seeds durable memory"), "{dynamic}");
    }

    /// The scaffolded file has to be readable by the extractor that gates deploy.
    ///
    /// The template and the extractor are edited in different files, so a
    /// mismatch between them would otherwise surface as `mda build` failing on a
    /// freshly scaffolded project — the one place it must never fail.
    #[test]
    fn the_scaffolded_declaration_resolves_for_every_choice() {
        use crate::init::scaffold::{MEMORY_LAYERS_PLACEHOLDER, MEMORY_PY, MEMORY_TS};
        use crate::init::MemoryChoice;

        for (choice, expected) in [
            (MemoryChoice::Agent, MemoryLayersValue::Agent),
            (MemoryChoice::None, MemoryLayersValue::None),
        ] {
            let ts = MEMORY_TS.replace(
                MEMORY_LAYERS_PLACEHOLDER,
                &choice.layers_argument(Language::TypeScript),
            );
            assert_eq!(ts_layers(&ts), Some(expected.clone()), "{ts}");

            if !toolchain::python3_available() {
                continue;
            }
            let py = MEMORY_PY.replace(
                MEMORY_LAYERS_PLACEHOLDER,
                &choice.layers_argument(Language::Python),
            );
            assert_eq!(py_layers(&py), Some(expected), "{py}");
        }
    }

    #[test]
    fn the_declaration_snippet_follows_the_project_language() {
        assert_eq!(
            declare_memory_snippet(Language::Python),
            "define_memory(agent=MemoryLayer())"
        );
        assert_eq!(
            declare_memory_snippet(Language::TypeScript),
            "defineMemory({ agent: memoryLayer() })"
        );
    }

    #[test]
    fn py_reads_the_agent_layer() {
        if !toolchain::python3_available() {
            return;
        }
        for source in [
            "memory = define_memory(agent=MemoryLayer())\n",
            "memory: object = define_memory(agent=MemoryLayer())\n",
        ] {
            assert_eq!(
                py_layers(source),
                Some(MemoryLayersValue::Agent),
                "{source}"
            );
        }
    }

    #[test]
    fn py_reads_user_and_both_layers() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers("memory = define_memory(scope=[{\"type\": \"user\"}])\n"),
            Some(MemoryLayersValue::User)
        );
        assert_eq!(
            py_layers(
                "memory = define_memory(scope=[{\"type\": \"user\"}, {\"type\": \"agent\"}])\n"
            ),
            Some(MemoryLayersValue::AgentAndUser)
        );
        assert_eq!(
            py_layers("memory = define_memory(scope=[{\"type\": \"user\", \"id\": \"usr_1\"}])\n"),
            Some(MemoryLayersValue::User)
        );
    }

    #[test]
    fn py_reads_user_allow_callbacks_without_evaluating_them() {
        if !toolchain::python3_available() {
            return;
        }
        for callback in [
            "lambda context: context.source.audience == 'private'",
            "allow_user_memory",
            "allow_async",
        ] {
            let source = format!(
                "from managed_deepagents import define_memory as dm\n\
                 from policy import allow_user_memory\n\
                 async def allow_async(context):\n    raise RuntimeError('must not run at build time')\n\
                 memory = dm(scope=[{{'type': 'agent'}}, \
                 {{'allow': {callback}, 'type': 'user', 'id': 'usr_1'}}])\n"
            );
            assert_eq!(py_layers(&source), Some(MemoryLayersValue::AgentAndUser));
        }
    }

    #[test]
    fn py_reads_allow_on_the_agent_layer() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers("memory = define_memory(scope={'allow': policy, 'type': 'agent'})"),
            Some(MemoryLayersValue::Agent)
        );
    }

    #[test]
    fn py_allow_keeps_layer_ownership_static() {
        if !toolchain::python3_available() {
            return;
        }
        for scope in [
            "layers",
            "[{'allow': policy, 'type': kind}]",
            "[{'allow': policy, 'type': 'user', 'id': caller}]",
            "[{'allow': policy, **base}]",
            "[{'allow': policy, 'type': 'user'}, *layers]",
        ] {
            let source = format!("memory = define_memory(scope={scope})");
            assert!(
                matches!(py_layers(&source), Some(MemoryLayersValue::Dynamic(_))),
                "{source}"
            );
        }
    }

    #[test]
    fn py_allow_does_not_hide_invalid_layers() {
        if !toolchain::python3_available() {
            return;
        }
        for (scope, expected) in [
            (
                "[{'allow': policy, 'type': 'user'}, {'type': 'user'}]",
                MemoryLayersValue::Duplicate("user"),
            ),
            (
                "[{'allow': policy, 'type': 'workspace'}]",
                MemoryLayersValue::Unknown("type 'workspace'".to_string()),
            ),
            (
                "[{'allow': policy}]",
                MemoryLayersValue::Unknown("a layer with no `type`".to_string()),
            ),
            (
                "[{'allow': policy, 'type': 'user', 'access': 'rw'}]",
                MemoryLayersValue::Reserved("the key `access`".to_string()),
            ),
        ] {
            let source = format!("memory = define_memory(scope={scope})");
            assert_eq!(py_layers(&source), Some(expected), "{source}");
        }
    }

    #[test]
    fn py_reads_no_memory_for_an_inert_declaration() {
        if !toolchain::python3_available() {
            return;
        }
        for source in [
            "memory = define_memory()\n",
            "memory = define_memory(scope=[])\n",
        ] {
            assert_eq!(py_layers(source), Some(MemoryLayersValue::None), "{source}");
        }
    }

    #[test]
    fn py_follows_import_aliases_and_namespaces() {
        if !toolchain::python3_available() {
            return;
        }
        let aliased = concat!(
            "from managed_deepagents import define_memory as dm\n",
            "memory = dm(scope=[{\"type\": \"agent\"}])\n",
        );
        assert_eq!(py_layers(aliased), Some(MemoryLayersValue::Agent));

        let namespaced = concat!(
            "import managed_deepagents\n",
            "memory = managed_deepagents.define_memory(agent=MemoryLayer())\n",
        );
        assert_eq!(py_layers(namespaced), Some(MemoryLayersValue::Agent));
    }

    #[test]
    fn py_names_the_retired_string_shorthand() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers("memory = define_memory(scope=\"agent\")\n"),
            Some(MemoryLayersValue::Shorthand("\"agent\"".to_string()))
        );
        assert!(matches!(
            py_layers("memory = define_memory(scope=[\"agent\"])\n"),
            Some(MemoryLayersValue::Shorthand(_))
        ));
    }

    #[test]
    fn py_names_reserved_and_unknown_layers() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers(
                "memory = define_memory(scope=[{\"type\": \"agent\", \"access\": \"rw\"}])\n"
            ),
            Some(MemoryLayersValue::Reserved("the key `access`".to_string()))
        );
        assert!(matches!(
            py_layers("memory = define_memory(scope=[{\"type\": \"workspace\"}])\n"),
            Some(MemoryLayersValue::Unknown(_))
        ));
        assert_eq!(
            py_layers("memory = define_memory(scope=[{\"type\": \"agent\", \"id\": \"usr_1\"}])\n"),
            Some(MemoryLayersValue::Unknown(
                "an `id` on the agent layer".to_string()
            ))
        );
    }

    #[test]
    fn py_reports_a_duplicate_layer() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers(
                "memory = define_memory(scope=[{\"type\": \"user\"}, {\"type\": \"user\"}])\n"
            ),
            Some(MemoryLayersValue::Duplicate("user"))
        );
    }

    #[test]
    fn py_treats_a_dynamic_layer_value_as_unresolvable() {
        if !toolchain::python3_available() {
            return;
        }
        for source in [
            "import os\nmemory = define_memory(scope=os.environ[\"LAYERS\"])\n",
            "import os\nmemory = define_memory(scope=[{\"type\": os.environ[\"KIND\"]}])\n",
            "import os\nmemory = define_memory(scope=[{\"type\": \"user\", \"id\": os.environ[\"WHO\"]}])\n",
            "memory = define_memory(scope=[{**base}])\n",
        ] {
            assert!(
                matches!(py_layers(source), Some(MemoryLayersValue::Dynamic(_))),
                "{source}"
            );
        }
    }

    #[test]
    fn py_refuses_a_double_star_entry_that_can_set_layers() {
        if !toolchain::python3_available() {
            return;
        }
        for source in [
            "memory = define_memory(**opts)\n",
            "memory = define_memory(**{\"scope\": []})\n",
        ] {
            assert!(
                matches!(py_layers(source), Some(MemoryLayersValue::Dynamic(_))),
                "{source}"
            );
        }
    }

    #[test]
    fn py_ignores_unrelated_bindings() {
        if !toolchain::python3_available() {
            return;
        }
        assert_eq!(
            py_layers("other = define_memory(agent=MemoryLayer())\n"),
            None
        );
    }

    #[test]
    fn py_reports_a_syntax_error_instead_of_defaulting() {
        if !toolchain::python3_available() {
            return;
        }
        let source = "memory = define_memory(scope=[{\"type\": \"agent\"}]\n";
        let error = extract_memory_layers_python(source, "memory.py")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse memory.py:"), "{error}");
    }
}
