//! Compile-time checks that discovered MCP and channel modules expose the
//! export shape the runtime collector requires, plus the channel ingress
//! heuristic that decides whether the identity gate applies.
//!
//! Channel discovery is name-agnostic. Modules without the required named
//! `channel` export are skipped so helpers and co-located unit tests are not
//! imported at startup. Brand validation still happens in the runtime
//! collector.
//!
//! "Has no such export" and "could not be read" must stay distinguishable: a
//! module that fails to parse is reported, never quietly skipped, or a typo'd
//! brace would drop a connector from the deployment without a word.

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{Declaration, Expression, Statement};

use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};
use crate::typescript::{
    collect_managed_export_bindings, module_export_name, parse_typescript,
    statement_variable_declaration, ManagedExportBindings,
};

use super::discovery::{find_channel_entries, find_legacy_connector_entries, find_mcp_entry};

/// Named export that holds the first-party channel providers.
const CHANNELS_NS: &str = "channels";

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

/// Connector modules collected into `_mda_connectors`: the `tools/mcp.{ts,py}`
/// declaration when the project has one.
///
/// Fails when a module under the removed `connectors/` layout still exports a
/// `connector` (or has a default export), so an old MCP declaration is not
/// silently dropped from the deployment.
pub(super) fn scan_exported_connectors(project: &Project) -> Result<Vec<PathBuf>> {
    reject_legacy_connector_modules(project)?;
    let mut entries = Vec::new();
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
    Ok(entries)
}

fn reject_legacy_connector_modules(project: &Project) -> Result<()> {
    for rel in find_legacy_connector_entries(project) {
        let path = project.root.join(&rel);
        let source = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        let file_label = rel.to_string_lossy();
        // A default export counts too: before the removal it was a hard error
        // here, so treating it as a helper would drop the MCP tools silently.
        let exported = match project.language {
            Language::TypeScript => typescript_has_connector_export(&source, &file_label)?,
            Language::Python => python_has_module_binding(&source, &file_label, "connector")?,
        };
        if exported {
            bail!(
                "{file_label} uses the removed `connectors/` MCP layout. Move the declaration to `tools/mcp.ts` or `tools/mcp.py` and export it as `mcp`: write `export const mcp = defineMcp(...)` (TypeScript) or `mcp = define_mcp(...)` (Python)."
            );
        }
    }
    Ok(())
}

fn typescript_has_connector_export(source: &str, file_name: &str) -> Result<bool> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    Ok(program_has_named_export(&parsed.program.body, "connector")
        || program_has_default_export(&parsed.program.body))
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

/// Whether any channel declares Events ingress that needs a public HTTPS URL
/// (Slack `on:`) for local delivery.
pub(crate) fn project_needs_events_public_url(project: &Project) -> Result<bool> {
    Ok(scan_exported_channels(project)?.needs_public_url)
}

#[cfg(test)]
fn typescript_declares_channel_ingress(source: &str, file_name: &str) -> Result<bool> {
    Ok(scan_typescript_channel(source, file_name)?.1.is_ingress())
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

fn peel_expression<'a>(expression: &'a Expression<'a>) -> &'a Expression<'a> {
    match expression {
        Expression::ParenthesizedExpression(inner) => peel_expression(&inner.expression),
        Expression::TSAsExpression(inner) => peel_expression(&inner.expression),
        Expression::TSSatisfiesExpression(inner) => peel_expression(&inner.expression),
        Expression::TSNonNullExpression(inner) => peel_expression(&inner.expression),
        other => other,
    }
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
    fn typescript_connector_export_detects_named_and_default_exports() {
        assert!(typescript_has_connector_export(
            "export const connector = defineMcp({});\n",
            "connectors/mcp.ts",
        )
        .unwrap());
        assert!(typescript_has_connector_export(
            "const connector = defineMcp({});\nexport { connector };\n",
            "connectors/mcp.ts",
        )
        .unwrap());
        assert!(typescript_has_connector_export(
            "export default defineMcp({ servers: {} });\n",
            "connectors/mcp.ts",
        )
        .unwrap());
        assert!(!typescript_has_connector_export(
            "export const sharedUrl = \"https://docs.example.com/mcp\";\n",
            "connectors/helper.ts",
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
        let error =
            typescript_has_named_mcp_export("export const mcp = defineMcp({{});\n", "tools/mcp.ts")
                .unwrap_err()
                .to_string();
        assert!(
            error.starts_with("failed to parse tools/mcp.ts:"),
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
