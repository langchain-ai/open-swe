//! Compile-time checks that discovered connector modules expose the export
//! shape the runtime collector requires, plus the ingress heuristic that
//! decides whether the identity gate applies.
//!
//! Path discovery is name-agnostic. Modules without the required named
//! `connector` export are skipped so helpers and co-located unit tests are not
//! imported at startup. Brand validation still happens in the runtime
//! collector.
//!
//! "Has no such export" and "could not be read" must stay distinguishable: a
//! module that fails to parse is reported, never quietly skipped, or a typo'd
//! brace would drop a connector from the deployment without a word.

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{
    Argument, CallExpression, Declaration, Expression, ObjectExpression, ObjectPropertyKind,
    Statement,
};

use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};
use crate::typescript::{
    collect_managed_export_bindings, module_export_name, parse_typescript,
    statement_variable_declaration, ManagedExportBindings,
};

use super::discovery::{find_channel_entries, find_connector_entries, find_mcp_entry};

/// Named export that holds the first-party connector providers (ingress scan).
const CONNECTORS_NS: &str = "connectors";
/// First-party MCP factory. CLI labels still use the `mcp` stem.
const TS_DEFINE_MCP: &str = "defineMcp";
const CONNECTOR_FACTORY_LABEL: &str = "mcp";
/// Named export that holds the first-party channel providers.
const CHANNELS_NS: &str = "channels";

/// Result of scanning `connectors/` once for exports and ingress.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ConnectorScan {
    /// Modules with a named `connector` export.
    pub entries: Vec<PathBuf>,
    /// Whether any exported module declares Events ingress.
    pub requires_identity: bool,
}

/// First-party channel factories that mount Events ingress.
const INGRESS_CHANNEL_PROVIDERS: [&str; 3] = ["slack", "github", "linear"];

/// What the exported `channel` binding resolves to, as far as compile can tell.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ChannelIngress {
    /// Not a first-party ingress factory.
    None,
    /// A binding compile cannot resolve; treated as ingress, provider unknown.
    Unresolved,
    /// A first-party `channels.<provider>(...)` call.
    Provider(&'static str),
}

impl ChannelIngress {
    fn is_ingress(self) -> bool {
        self != Self::None
    }

    /// Whether local delivery needs a public HTTPS URL. GitHub and Linear
    /// channels do not run under `mda dev`, so only Slack (or an unknown
    /// factory) counts.
    fn needs_public_url(self) -> bool {
        matches!(self, Self::Unresolved | Self::Provider("slack"))
    }
}

/// Result of scanning `channels/` once for exports and ingress.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ChannelScan {
    /// Modules with a named `channel` export.
    pub entries: Vec<PathBuf>,
    /// Whether any exported module declares Events ingress.
    pub requires_identity: bool,
    /// Whether any exported module needs a public Events URL under `mda dev`.
    pub needs_public_url: bool,
    /// First-party ingress providers declared by exported modules.
    pub providers: BTreeSet<String>,
}

/// Scan connector modules once: export gate + ingress heuristic per file.
pub(super) fn scan_exported_connectors(project: &Project) -> Result<ConnectorScan> {
    let mut entries = Vec::new();
    let mut requires_identity = false;
    if let Some(rel) = find_mcp_entry(project) {
        let path = project.root.join(&rel);
        let source = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let file_label = rel.to_string_lossy();
        let exported = match project.language {
            Language::TypeScript => typescript_has_named_mcp_export(&source, &file_label)?,
            Language::Python => python_has_module_binding(&source, &file_label, "mcp")?,
        };
        if !exported {
            bail!(
                "{file_label} must export a named `mcp` declaration: write `export const mcp = defineMcp(...)` (TypeScript) or `mcp = define_mcp(...)` (Python)."
            );
        }
        entries.push(rel);
    }
    let candidates = find_connector_entries(project);
    for rel in candidates {
        let path = project.root.join(&rel);
        let source = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let file_label = rel.to_string_lossy();
        let (exported, ingress) = match project.language {
            Language::TypeScript => scan_typescript_connector(&source, &file_label)?,
            Language::Python => {
                let exported = python_has_module_binding(&source, &file_label, "connector")?;
                let ingress = if exported {
                    python_declares_connector_ingress(&source, &file_label)?
                } else {
                    false
                };
                (exported, ingress)
            }
        };
        if exported {
            entries.push(rel);
            requires_identity |= ingress;
        }
    }
    Ok(ConnectorScan {
        entries,
        requires_identity,
    })
}

fn typescript_has_named_mcp_export(source: &str, file_name: &str) -> Result<bool> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    if !program_has_named_export(&parsed.program.body, "mcp")
        && program_has_default_export(&parsed.program.body)
    {
        bail!(
            "{file_name} has a default export but no `mcp` export. MCP is collected by name: write `export const mcp = defineMcp(...)`."
        );
    }
    Ok(program_has_named_export(&parsed.program.body, "mcp"))
}

/// Scan channel modules once: export gate + ingress heuristic per file.
pub(super) fn scan_exported_channels(project: &Project) -> Result<ChannelScan> {
    let mut entries = Vec::new();
    let mut requires_identity = false;
    let mut needs_public_url = false;
    let mut providers = BTreeSet::new();
    for rel in find_channel_entries(project) {
        let path = project.root.join(&rel);
        let source = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let file_label = rel.to_string_lossy();
        let (exported, ingress) = match project.language {
            Language::TypeScript => scan_typescript_channel(&source, &file_label)?,
            Language::Python => {
                let exported = python_has_module_binding(&source, &file_label, "channel")?;
                let ingress = if exported {
                    python_channel_ingress(&source, &file_label)?
                } else {
                    ChannelIngress::None
                };
                (exported, ingress)
            }
        };
        if exported {
            entries.push(rel);
            requires_identity |= ingress.is_ingress();
            needs_public_url |= ingress.needs_public_url();
            if let ChannelIngress::Provider(provider) = ingress {
                providers.insert(provider.to_string());
            }
        }
    }
    Ok(ChannelScan {
        entries,
        requires_identity,
        needs_public_url,
        providers,
    })
}

/// Connector modules under `connectors/` that expose a named `connector`
/// export, sorted for deterministic generated output.
///
/// Candidates without that binding are omitted so co-located tests and helpers
/// are not imported by `_mda_connectors`. Brand/hook validation still happens
/// at runtime for modules that do expose the export.
#[cfg(test)]
pub(super) fn find_exported_connector_entries(project: &Project) -> Result<Vec<PathBuf>> {
    Ok(scan_exported_connectors(project)?.entries)
}

/// Whether any exported connector module declares ingress (a runtime
/// `events` hook), which requires a root `identity.{ts,py}` declaration.
///
/// Compile cannot run the author's code to see which hooks a connector
/// attaches, so this is a best-effort AST scan of the exported `connector`
/// binding for first-party connector Events ingress. Slack channel ingress is
/// scanned separately via [`scan_exported_channels`]. No first-party connector
/// currently attaches an `events` hook. Import aliases
/// (`import { defineMcp as mcp }`), namespace imports, and top-level config
/// references (`defineMcp(config)`) are resolved the same way as
/// identity / static config extraction. When a recognized ingress factory's
/// argument cannot be proven non-ingress (unresolved reference, spread, …),
/// identity is required. A third-party connector that attaches `events` some
/// other way is not caught by this heuristic and must declare identity itself.
/// Whether any connector or channel declares Events ingress that needs a public
/// HTTPS URL (Slack `on:`) for local delivery.
pub(crate) fn project_needs_events_public_url(project: &Project) -> Result<bool> {
    Ok(scan_exported_connectors(project)?.requires_identity
        || scan_exported_channels(project)?.needs_public_url)
}

#[cfg(test)]
fn typescript_declares_connector_ingress(source: &str, file_name: &str) -> Result<bool> {
    Ok(scan_typescript_connector(source, file_name)?.1)
}

#[cfg(test)]
fn typescript_has_connector_export(source: &str, file_name: &str) -> Result<bool> {
    Ok(scan_typescript_connector(source, file_name)?.0)
}

#[cfg(test)]
fn typescript_declares_channel_ingress(source: &str, file_name: &str) -> Result<bool> {
    Ok(scan_typescript_channel(source, file_name)?.1.is_ingress())
}

/// One TypeScript parse: named `connector` export + ingress heuristic.
fn scan_typescript_connector(source: &str, file_name: &str) -> Result<(bool, bool)> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    if !program_has_named_export(&parsed.program.body, "connector") {
        if program_has_default_export(&parsed.program.body) {
            bail!(
                "{file_name} has a default export but no `connector` export. \
                 Connectors are collected by name: write \
                 `export const connector = defineMcp(...)`."
            );
        }
        return Ok((false, false));
    }
    let ns = collect_managed_export_bindings(&parsed.program.body, CONNECTORS_NS);
    let objects = collect_top_level_object_bindings(&parsed.program.body);
    let expressions = collect_top_level_expression_bindings(&parsed.program.body);
    let Some(init) = find_initializer(&parsed.program.body, &expressions, "connector") else {
        return Ok((true, false));
    };
    let ingress = expression_declares_connector_ingress(init, &ns, &objects, &expressions, 0);
    Ok((true, ingress))
}

/// One TypeScript parse: named `channel` export + ingress heuristic.
fn scan_typescript_channel(source: &str, file_name: &str) -> Result<(bool, ChannelIngress)> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    if !program_has_named_export(&parsed.program.body, "channel") {
        if program_has_default_export(&parsed.program.body) {
            bail!(
                "{file_name} has a default export but no `channel` export. \
                 Channels are collected by name: write \
                 `export const channel = channels.<provider>(...)`."
            );
        }
        return Ok((false, ChannelIngress::None));
    }
    let ns = collect_managed_export_bindings(&parsed.program.body, CHANNELS_NS);
    let expressions = collect_top_level_expression_bindings(&parsed.program.body);
    let Some(init) = find_initializer(&parsed.program.body, &expressions, "channel") else {
        return Ok((true, ChannelIngress::None));
    };
    let ingress = expression_channel_ingress(init, &ns, &expressions, 0);
    Ok((true, ingress))
}

/// Top-level `const name = { … }` (after peeling `as const` / satisfies).
fn collect_top_level_object_bindings<'a>(
    body: &'a [Statement<'a>],
) -> HashMap<String, &'a ObjectExpression<'a>> {
    let mut objects = HashMap::new();
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let Some(name) = declarator.id.get_identifier_name() else {
                continue;
            };
            let Some(init) = declarator.init.as_ref() else {
                continue;
            };
            if let Expression::ObjectExpression(object) = peel_expression(init) {
                objects.insert(name.to_string(), object.as_ref());
            }
        }
    }
    objects
}

/// Top-level `const name = <expression>` for one-hop resolution through aliases.
fn collect_top_level_expression_bindings<'a>(
    body: &'a [Statement<'a>],
) -> HashMap<String, &'a Expression<'a>> {
    let mut expressions = HashMap::new();
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let Some(name) = declarator.id.get_identifier_name() else {
                continue;
            };
            let Some(init) = declarator.init.as_ref() else {
                continue;
            };
            expressions.insert(name.to_string(), peel_expression(init));
        }
    }
    expressions
}

/// Initializer of a local binding, following `export { x as binding }`.
fn find_initializer<'a>(
    body: &'a [Statement<'a>],
    expressions: &HashMap<String, &'a Expression<'a>>,
    binding: &str,
) -> Option<&'a Expression<'a>> {
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            if declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == binding)
            {
                return declarator.init.as_ref().map(peel_expression);
            }
        }
    }
    for statement in body {
        let Statement::ExportNamedDeclaration(export) = statement else {
            continue;
        };
        for specifier in &export.specifiers {
            if module_export_name(&specifier.exported) != binding {
                continue;
            }
            let local = module_export_name(&specifier.local);
            return expressions.get(local).copied();
        }
    }
    None
}

fn expression_declares_connector_ingress<'a>(
    expression: &'a Expression<'a>,
    ns: &ManagedExportBindings,
    objects: &HashMap<String, &'a ObjectExpression<'a>>,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> bool {
    // Bound the identifier chase so a cycle cannot hang compile.
    if depth > 8 {
        return true;
    }
    match peel_expression(expression) {
        Expression::CallExpression(call) => {
            call_declares_connector_ingress(call, ns, objects, expressions, depth)
        }
        Expression::Identifier(id) => match expressions.get(id.name.as_str()) {
            Some(inner) => {
                expression_declares_connector_ingress(inner, ns, objects, expressions, depth + 1)
            }
            // `export const connector = imported` with no local init to inspect.
            None => true,
        },
        _ => false,
    }
}

fn call_declares_connector_ingress<'a>(
    call: &CallExpression<'a>,
    ns: &ManagedExportBindings,
    objects: &HashMap<String, &'a ObjectExpression<'a>>,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> bool {
    // First-party connectors are tools-only today (Slack Events live on
    // `channels/`). Still treat unrecognized option shapes / spreads as
    // potential ingress so identity stays required when we cannot prove
    // tools-only.
    if connector_provider_name(&call.callee, ns).is_none() {
        return false;
    }
    let Some(argument) = call.arguments.first() else {
        return false;
    };
    match resolve_options_object(argument, objects, expressions, depth) {
        OptionsResolve::Object(object) => {
            // Spreads can inject `on` / `events`; cannot prove tools-only.
            object_has_spread(object)
        }
        // Recognized ingress factory, but the argument is not a resolvable
        // object literal — require identity rather than assume tools-only.
        OptionsResolve::Unknown => true,
    }
}

enum OptionsResolve<'a> {
    Object(&'a ObjectExpression<'a>),
    Unknown,
}

fn resolve_options_object<'a>(
    argument: &'a Argument<'a>,
    objects: &HashMap<String, &'a ObjectExpression<'a>>,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> OptionsResolve<'a> {
    if matches!(argument, Argument::SpreadElement(_)) {
        return OptionsResolve::Unknown;
    }
    let Some(expression) = argument.as_expression() else {
        return OptionsResolve::Unknown;
    };
    resolve_options_expression(expression, objects, expressions, depth)
}

fn resolve_options_expression<'a>(
    expression: &'a Expression<'a>,
    objects: &HashMap<String, &'a ObjectExpression<'a>>,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> OptionsResolve<'a> {
    if depth > 8 {
        return OptionsResolve::Unknown;
    }
    match peel_expression(expression) {
        Expression::ObjectExpression(object) => OptionsResolve::Object(object.as_ref()),
        Expression::Identifier(id) => {
            if let Some(object) = objects.get(id.name.as_str()).copied() {
                return OptionsResolve::Object(object);
            }
            match expressions.get(id.name.as_str()) {
                Some(inner) => resolve_options_expression(inner, objects, expressions, depth + 1),
                None => OptionsResolve::Unknown,
            }
        }
        _ => OptionsResolve::Unknown,
    }
}

fn object_has_spread(object: &ObjectExpression<'_>) -> bool {
    object
        .properties
        .iter()
        .any(|property| matches!(property, ObjectPropertyKind::SpreadProperty(_)))
}

/// Provider name when `callee` is a first-party connector factory that may
/// declare Events ingress. None match today — Slack lives on `channels`.
fn connector_provider_name<'a>(
    _callee: &'a Expression<'a>,
    _bindings: &ManagedExportBindings,
) -> Option<&'a str> {
    None
}

/// Factory name when `callee` is `defineMcp` or the deprecated `connectors.mcp`
/// (or an import alias / namespace form). Reported as `mcp` so a declaration
/// named `mcp.ts` is not annotated.
fn connector_factory_name<'a>(
    callee: &'a Expression<'a>,
    define_mcp: &ManagedExportBindings,
    connectors: &ManagedExportBindings,
) -> Option<&'a str> {
    match callee {
        Expression::Identifier(id) if define_mcp.aliases.contains(id.name.as_str()) => {
            Some(CONNECTOR_FACTORY_LABEL)
        }
        Expression::StaticMemberExpression(member) => {
            let property = member.property.name.as_str();
            if property == TS_DEFINE_MCP {
                return match &member.object {
                    Expression::Identifier(id)
                        if define_mcp.namespaces.contains(id.name.as_str()) =>
                    {
                        Some(CONNECTOR_FACTORY_LABEL)
                    }
                    _ => None,
                };
            }
            if property == CONNECTOR_FACTORY_LABEL {
                return match &member.object {
                    Expression::Identifier(id) if connectors.aliases.contains(id.name.as_str()) => {
                        Some(CONNECTOR_FACTORY_LABEL)
                    }
                    Expression::StaticMemberExpression(inner)
                        if inner.property.name.as_str() == CONNECTORS_NS =>
                    {
                        match &inner.object {
                            Expression::Identifier(id)
                                if connectors.namespaces.contains(id.name.as_str()) =>
                            {
                                Some(CONNECTOR_FACTORY_LABEL)
                            }
                            _ => None,
                        }
                    }
                    _ => None,
                };
            }
            None
        }
        _ => None,
    }
}

/// Static `defineMcp` factory name for a connector module, when resolvable.
///
/// Used by `mda dev` so a misnamed file (`notion.ts` exporting `defineMcp()`)
/// is labeled by its factory (`mcp`) rather than only its file stem.
pub(crate) fn read_connector_factory(project: &Project, rel: &Path) -> Result<Option<String>> {
    let path = project.root.join(rel);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let file_label = rel.to_string_lossy();
    match project.language {
        Language::TypeScript => Ok(typescript_connector_factory(&source, &file_label)?),
        Language::Python => Ok(python_connector_factory(&source, &file_label)?),
    }
}

fn typescript_connector_factory(source: &str, file_name: &str) -> Result<Option<String>> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    let define_mcp = collect_managed_export_bindings(&parsed.program.body, TS_DEFINE_MCP);
    let connectors = collect_managed_export_bindings(&parsed.program.body, CONNECTORS_NS);
    let expressions = collect_top_level_expression_bindings(&parsed.program.body);
    let Some(init) = find_initializer(&parsed.program.body, &expressions, "connector") else {
        return Ok(None);
    };
    Ok(
        expression_connector_factory(init, &define_mcp, &connectors, &expressions, 0)
            .map(str::to_string),
    )
}

fn expression_connector_factory<'a>(
    expression: &'a Expression<'a>,
    define_mcp: &ManagedExportBindings,
    connectors: &ManagedExportBindings,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> Option<&'a str> {
    if depth > 8 {
        return None;
    }
    match peel_expression(expression) {
        Expression::CallExpression(call) => {
            connector_factory_name(&call.callee, define_mcp, connectors)
        }
        Expression::Identifier(id) => expressions.get(id.name.as_str()).and_then(|inner| {
            expression_connector_factory(inner, define_mcp, connectors, expressions, depth + 1)
        }),
        _ => None,
    }
}

fn python_connector_factory(source: &str, file_label: &str) -> Result<Option<String>> {
    let output = run_python_extractor(
        scripts::CONNECTOR_FACTORY,
        source,
        file_label,
        "connector factory",
    )?;
    Ok(output)
}

fn peel_expression<'a>(expression: &'a Expression<'a>) -> &'a Expression<'a> {
    match expression {
        Expression::ParenthesizedExpression(inner) => peel_expression(&inner.expression),
        Expression::TSAsExpression(inner) => peel_expression(&inner.expression),
        Expression::TSSatisfiesExpression(inner) => peel_expression(&inner.expression),
        Expression::TSNonNullExpression(inner) => peel_expression(&inner.expression),
        other => other,
    }
}

fn python_declares_connector_ingress(source: &str, file_label: &str) -> Result<bool> {
    let output = run_python_extractor(
        scripts::CONNECTOR_INGRESS,
        source,
        file_label,
        "connector ingress",
    )?;
    Ok(output.as_deref() == Some("INGRESS"))
}

fn expression_channel_ingress<'a>(
    expression: &'a Expression<'a>,
    ns: &ManagedExportBindings,
    expressions: &HashMap<String, &'a Expression<'a>>,
    depth: usize,
) -> ChannelIngress {
    if depth > 8 {
        return ChannelIngress::Unresolved;
    }
    match peel_expression(expression) {
        // Every `channels.slack(...)` / `channels.github(...)` declaration mounts
        // Events ingress.
        Expression::CallExpression(call) => channel_provider_name(&call.callee, ns)
            .map_or(ChannelIngress::None, ChannelIngress::Provider),
        Expression::Identifier(id) => match expressions.get(id.name.as_str()) {
            Some(inner) => expression_channel_ingress(inner, ns, expressions, depth + 1),
            None => ChannelIngress::Unresolved,
        },
        _ => ChannelIngress::None,
    }
}

fn channel_provider_name(
    callee: &Expression<'_>,
    bindings: &ManagedExportBindings,
) -> Option<&'static str> {
    let Expression::StaticMemberExpression(member) = callee else {
        return None;
    };
    let name = member.property.name.as_str();
    let provider = INGRESS_CHANNEL_PROVIDERS
        .into_iter()
        .find(|provider| *provider == name)?;
    match &member.object {
        Expression::Identifier(id) if bindings.aliases.contains(id.name.as_str()) => Some(provider),
        Expression::StaticMemberExpression(inner)
            if inner.property.name.as_str() == CHANNELS_NS =>
        {
            match &inner.object {
                Expression::Identifier(id) if bindings.namespaces.contains(id.name.as_str()) => {
                    Some(provider)
                }
                _ => None,
            }
        }
        _ => None,
    }
}

fn python_channel_ingress(source: &str, file_label: &str) -> Result<ChannelIngress> {
    let output = run_python_extractor(
        scripts::CHANNEL_INGRESS,
        source,
        file_label,
        "channel ingress",
    )?;
    Ok(parse_python_channel_ingress(output.as_deref()))
}

/// Parse the extractor's `INGRESS [<provider>]` line.
fn parse_python_channel_ingress(output: Option<&str>) -> ChannelIngress {
    let Some(rest) = output.and_then(|line| line.strip_prefix("INGRESS")) else {
        return ChannelIngress::None;
    };
    let provider = rest.trim();
    INGRESS_CHANNEL_PROVIDERS
        .into_iter()
        .find(|known| *known == provider)
        .map_or(ChannelIngress::Unresolved, ChannelIngress::Provider)
}

#[cfg(test)]
fn python_declares_channel_ingress(source: &str, file_label: &str) -> Result<bool> {
    Ok(python_channel_ingress(source, file_label)?.is_ingress())
}

/// True when the module exposes a runtime `default` binding — either
/// `export default …` or a named re-export such as `export { x as default }` /
/// `export { default } from "./impl"`.
fn program_has_default_export(body: &[Statement<'_>]) -> bool {
    for statement in body {
        match statement {
            Statement::ExportDefaultDeclaration(_) => return true,
            Statement::ExportNamedDeclaration(export) => {
                for specifier in &export.specifiers {
                    if module_export_name(&specifier.exported) == "default" {
                        return true;
                    }
                }
            }
            Statement::ExportFromDeclaration(export) => {
                for specifier in &export.specifiers {
                    if module_export_name(&specifier.exported) == "default" {
                        return true;
                    }
                }
            }
            _ => {}
        }
    }
    false
}

fn program_has_named_export(body: &[Statement<'_>], export_name: &str) -> bool {
    for statement in body {
        let has_export = match statement {
            Statement::ExportDeclaration(export) => match &export.declaration {
                Declaration::VariableDeclaration(declaration) => {
                    declaration.declarations.iter().any(|declarator| {
                        declarator
                            .id
                            .get_identifier_name()
                            .is_some_and(|name| name == export_name)
                    })
                }
                Declaration::FunctionDeclaration(func) => func
                    .id
                    .as_ref()
                    .is_some_and(|id| id.name.as_str() == export_name),
                Declaration::ClassDeclaration(class) => class
                    .id
                    .as_ref()
                    .is_some_and(|id| id.name.as_str() == export_name),
                _ => false,
            },
            Statement::ExportNamedDeclaration(export) => export
                .specifiers
                .iter()
                .any(|specifier| module_export_name(&specifier.exported) == export_name),
            Statement::ExportFromDeclaration(export) => export
                .specifiers
                .iter()
                .any(|specifier| module_export_name(&specifier.exported) == export_name),
            _ => false,
        };
        if has_export {
            return true;
        }
    }
    false
}

fn python_has_module_binding(source: &str, file_label: &str, binding: &str) -> Result<bool> {
    let script = format!(
        r#"
binding = {binding:?}

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        targets = node.targets
        if any(isinstance(target, ast.Name) and target.id == binding for target in targets):
            print("PRESENT")
            sys.exit(0)
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == binding:
            print("PRESENT")
            sys.exit(0)
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            local = alias.asname or alias.name
            if local == binding:
                print("PRESENT")
                sys.exit(0)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            local = alias.asname or alias.name
            # `import connector` binds the top-level name; dotted imports bind
            # only the first segment (`import pkg.mod` → `pkg`).
            top = local.split(".", 1)[0]
            if top == binding:
                print("PRESENT")
                sys.exit(0)

print("ABSENT")
"#
    );
    let output = run_python_extractor(
        &script,
        source,
        file_label,
        &format!("the `{binding}` declaration"),
    )?;
    Ok(output.as_deref() == Some("PRESENT"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typescript_tools_mcp_export_detects_named_declaration() {
        assert!(typescript_has_named_mcp_export(
            "export const mcp = defineMcp({ servers: {} });\n",
            "tools/mcp.ts",
        )
        .unwrap());
    }

    #[test]
    fn typescript_tools_mcp_export_rejects_default_declaration() {
        let error = typescript_has_named_mcp_export(
            "export default defineMcp({ servers: {} });\n",
            "tools/mcp.ts",
        )
        .unwrap_err()
        .to_string();
        assert!(
            error.contains("default export but no `mcp` export"),
            "{error}"
        );
    }

    #[test]
    fn typescript_connector_export_detects_presence() {
        assert!(typescript_has_connector_export(
            "export const connector = defineMcp({});\n",
            "connectors/mcp.ts"
        )
        .unwrap());
        assert!(typescript_has_connector_export(
            "const connector = defineMcp({});\nexport { connector };\n",
            "connectors/mcp.ts"
        )
        .unwrap());
        assert!(typescript_has_connector_export(
            "export { connector } from \"./shared\";\n",
            "connectors/mcp.ts"
        )
        .unwrap());
        assert!(!typescript_has_connector_export(
            "export const mcp = defineMcp({});\n",
            "connectors/mcp.ts"
        )
        .unwrap());
    }

    #[test]
    fn typescript_connector_export_detects_a_function_declaration() {
        assert!(typescript_has_connector_export(
            "export function connector() {}\n",
            "connectors/mcp.ts"
        )
        .unwrap());
    }

    #[test]
    fn typescript_connector_export_detects_a_class_declaration() {
        assert!(typescript_has_connector_export(
            "export class connector {}\n",
            "connectors/mcp.ts"
        )
        .unwrap());
    }

    #[test]
    fn typescript_connector_export_rejects_a_default_export() {
        let error =
            typescript_has_connector_export("export default defineMcp({});\n", "connectors/mcp.ts")
                .unwrap_err()
                .to_string();
        assert!(
            error.starts_with("connectors/mcp.ts has a default export but no `connector` export."),
            "{error}"
        );
        assert!(error.contains("export const connector ="), "{error}");
    }

    #[test]
    fn typescript_connector_export_rejects_a_reexported_default() {
        assert!(typescript_has_connector_export(
            "export { default } from \"./shared\";\n",
            "connectors/mcp.ts"
        )
        .is_err());
    }

    #[test]
    fn typescript_connector_export_ignores_a_default_alongside_the_named_one() {
        assert!(typescript_has_connector_export(
            "export const connector = defineMcp({});\nexport default connector;\n",
            "connectors/mcp.ts"
        )
        .unwrap());
    }

    #[test]
    fn python_module_binding_detects_presence_and_typos() {
        assert!(python_has_module_binding(
            "connector = define_mcp(servers={})\n",
            "connectors/mcp.py",
            "connector"
        )
        .unwrap());
        assert!(!python_has_module_binding(
            "mcp_connector = define_mcp(servers={})\n",
            "connectors/mcp.py",
            "connector"
        )
        .unwrap());
    }

    #[test]
    fn python_module_binding_accepts_import_reexports() {
        assert!(python_has_module_binding(
            "from shared_connectors import connector\n",
            "connectors/mcp.py",
            "connector"
        )
        .unwrap());
        assert!(python_has_module_binding(
            "from shared_connectors import mcp as connector\n",
            "connectors/mcp.py",
            "connector"
        )
        .unwrap());
        assert!(!python_has_module_binding(
            "from shared_connectors import mcp\n",
            "connectors/mcp.py",
            "connector"
        )
        .unwrap());
    }

    #[test]
    fn typescript_syntax_error_is_reported_rather_than_read_as_missing_export() {
        let error = typescript_has_connector_export(
            "export const connector = defineMcp({{});\n",
            "connectors/mcp.ts",
        )
        .unwrap_err()
        .to_string();
        assert!(
            error.starts_with("failed to parse connectors/mcp.ts:"),
            "{error}"
        );
    }

    #[test]
    fn python_syntax_error_is_reported_rather_than_read_as_missing_export() {
        let error = python_has_module_binding(
            "connector = define_mcp(servers={}\n",
            "connectors/mcp.py",
            "connector",
        )
        .unwrap_err()
        .to_string();
        assert!(
            error.starts_with("failed to parse connectors/mcp.py:"),
            "{error}"
        );
    }

    fn ts_ingress(source: &str) -> bool {
        typescript_declares_connector_ingress(source, "connectors/github.ts").unwrap()
    }

    fn py_ingress(source: &str) -> bool {
        python_declares_connector_ingress(source, "connectors/github.py").unwrap()
    }

    #[test]
    fn typescript_detects_slack_ingress_on_the_exported_channel() {
        assert!(typescript_declares_channel_ingress(
            "export const channel = channels.slack({ name: \"Support Bot\" });\n",
            "channels/slack.ts",
        )
        .unwrap());
        assert!(typescript_declares_channel_ingress(
            "export const channel = channels.slack({ name: \"Support Bot\", autoReply: false });\n",
            "channels/slack.ts",
        )
        .unwrap());
        assert!(typescript_declares_channel_ingress(
            "const config = { name: \"Support Bot\", autoReply: true } as const;\n\
             export const channel = channels.slack(config);\n",
            "channels/slack.ts",
        )
        .unwrap());
    }

    #[test]
    fn typescript_ignores_ingress_markers_outside_the_connector_export() {
        assert!(!ts_ingress(concat!(
            "const helper = channels.slack({ name: \"Support Bot\" });\n",
            "export const connector = defineMcp({ servers: {} });\n",
        )));
    }

    #[test]
    fn typescript_ingress_is_false_for_non_ingress_connectors() {
        assert!(!ts_ingress(
            "export const connector = defineMcp({ servers: {} });\n"
        ));
    }

    #[test]
    fn python_connector_ingress_is_false_for_tools_only_slack() {
        assert!(!py_ingress(
            "connector = connectors.slack(include_tools=[\"chat_postMessage\"])\n"
        ));
    }

    #[test]
    fn typescript_detects_github_channel_provider() {
        let (exported, ingress) = scan_typescript_channel(
            "import { channels } from \"managed-deepagents\";\n\
             export const channel = channels.github({ onEvent: {} });\n",
            "channels/github.ts",
        )
        .unwrap();
        assert!(exported);
        assert_eq!(ingress, ChannelIngress::Provider("github"));
        assert!(ingress.is_ingress());
        assert!(!ingress.needs_public_url());
    }

    #[test]
    fn detects_linear_channel_provider() {
        let (exported, ingress) = scan_typescript_channel(
            "import { channels } from \"managed-deepagents\";\n\
             export const channel = channels.linear({ onEvent: {} });\n",
            "channels/linear.ts",
        )
        .unwrap();
        assert!(exported);
        assert_eq!(ingress, ChannelIngress::Provider("linear"));
        assert!(!ingress.needs_public_url());
        assert_eq!(
            parse_python_channel_ingress(Some("INGRESS linear")),
            ChannelIngress::Provider("linear")
        );
    }

    #[test]
    fn typescript_resolves_github_channel_through_namespace_and_binding() {
        let (_, ingress) = scan_typescript_channel(
            "import * as mda from \"managed-deepagents\";\n\
             const github = mda.channels.github({ onEvent: {} });\n\
             export const channel = github;\n",
            "channels/github.ts",
        )
        .unwrap();
        assert_eq!(ingress, ChannelIngress::Provider("github"));
    }

    #[test]
    fn typescript_unknown_channel_factory_is_not_ingress() {
        let (_, ingress) = scan_typescript_channel(
            "export const channel = channels.http(adapter);\n",
            "channels/webhook.ts",
        )
        .unwrap();
        assert_eq!(ingress, ChannelIngress::None);
    }

    #[test]
    fn python_detects_github_channel_provider() {
        assert_eq!(
            python_channel_ingress(
                "from managed_deepagents import channels\n\
                 channel = channels.github(on_event={})\n",
                "channels/github.py",
            )
            .unwrap(),
            ChannelIngress::Provider("github")
        );
        assert_eq!(
            python_channel_ingress(
                "import managed_deepagents as mda\n\
                 channel = mda.channels.github(on_event={})\n",
                "channels/github.py",
            )
            .unwrap(),
            ChannelIngress::Provider("github")
        );
    }

    #[test]
    fn python_channel_ingress_output_parsing() {
        assert_eq!(parse_python_channel_ingress(None), ChannelIngress::None);
        assert_eq!(
            parse_python_channel_ingress(Some("INGRESS")),
            ChannelIngress::Unresolved
        );
        assert_eq!(
            parse_python_channel_ingress(Some("INGRESS slack")),
            ChannelIngress::Provider("slack")
        );
        assert_eq!(
            parse_python_channel_ingress(Some("INGRESS github")),
            ChannelIngress::Provider("github")
        );
        assert_eq!(
            parse_python_channel_ingress(Some("INGRESS unknown")),
            ChannelIngress::Unresolved
        );
    }

    #[test]
    fn python_detects_slack_ingress_on_the_channel_binding() {
        assert!(python_declares_channel_ingress(
            "channel = channels.slack(name=\"Support Bot\")\n",
            "channels/slack.py",
        )
        .unwrap());
        assert!(python_declares_channel_ingress(
            "channel = channels.slack(name=\"Support Bot\", auto_reply=False)\n",
            "channels/slack.py",
        )
        .unwrap());
    }
}
