//! Shared TypeScript / TSX parsing via `oxc`.

use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;

use anyhow::{anyhow, Error, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{
    Argument, CallExpression, Declaration, Expression, ImportDeclarationSpecifier,
    ModuleExportName, ObjectExpression, ObjectPropertyKind, Statement, VariableDeclaration,
};
use oxc::diagnostics::{Diagnostics, OxcDiagnostic};
use oxc::parser::{Parser, ParserReturn};
use oxc::span::SourceType;

/// Parse `source` as TypeScript (or TSX when `file_name` ends with `.tsx`),
/// rejecting any source the parser reported a syntax error for.
///
/// `oxc` handles a syntax error one of two ways: it recovers, patching over the
/// offending construct and leaving `fatal_error` false, or it bails out and hands
/// back an empty program. Either way the AST stops reflecting what the author
/// wrote, and only `diagnostics` says so. Deriving deploy configuration from
/// such an AST is worse than failing: a stray brace in `agent.ts` takes the
/// `agent` declarator with it, so a `name` that is plainly in the file reads as
/// missing, and a `memory.ts` that will not parse reads as no memory at all. The
/// first error is reported with its position instead.
pub(crate) fn parse_typescript<'a>(
    allocator: &'a Allocator,
    source: &'a str,
    file_name: &str,
) -> Result<ParserReturn<'a>> {
    let parsed = parse_typescript_recovered(allocator, source, file_name);
    match syntax_error(&parsed.diagnostics, source, file_name) {
        Some(error) => Err(error),
        None => Ok(parsed),
    }
}

/// Parse `source` keeping whatever the parser recovered, for callers that only
/// rewrite source text and leave its meaning to the downstream toolchain.
///
/// The program is empty when the parser bailed out entirely, so callers must
/// treat a missing construct as "not found here", never as "not authored".
pub(crate) fn parse_typescript_recovered<'a>(
    allocator: &'a Allocator,
    source: &'a str,
    file_name: &str,
) -> ParserReturn<'a> {
    let source_type = if file_name.ends_with(".tsx") {
        SourceType::tsx()
    } else {
        SourceType::ts()
    };
    Parser::new(allocator, source, source_type).parse()
}

/// Render the first error-severity diagnostic as `failed to parse
/// <file>:<line>:<column>: <message>`, or `None` when parsing was clean.
///
/// Warnings are ignored: only errors mean the AST lost something.
fn syntax_error(diagnostics: &Diagnostics, source: &str, file_name: &str) -> Option<Error> {
    let mut errors = diagnostics.errors();
    let first = errors.next()?;
    let remaining = errors.count();

    let mut message = match diagnostic_offset(first) {
        Some(offset) => {
            let (line, column) = line_column(source, offset);
            format!("failed to parse {file_name}:{line}:{column}: {first}")
        }
        None => format!("failed to parse {file_name}: {first}"),
    };
    if remaining > 0 {
        let plural = if remaining == 1 { "" } else { "s" };
        // Writing to a String cannot fail.
        let _ = write!(message, " (and {remaining} more error{plural})");
    }
    Some(anyhow!(message))
}

/// Byte offset a diagnostic points at, taken from its first label.
fn diagnostic_offset(diagnostic: &OxcDiagnostic) -> Option<usize> {
    diagnostic
        .labels
        .as_slice()
        .first()
        .map(|label| label.offset() as usize)
}

/// 1-based line and column (counting characters) of byte `offset` in `source`.
fn line_column(source: &str, offset: usize) -> (usize, usize) {
    let mut line = 1;
    let mut column = 1;
    for (index, character) in source.char_indices() {
        if index >= offset {
            break;
        }
        if character == '\n' {
            line += 1;
            column = 1;
        } else {
            column += 1;
        }
    }
    (line, column)
}

/// Collect every module-level binding whose initializer is a static string,
/// keyed by binding name. Covers `const`/`let`/`var` (and their exported forms);
/// later declarations win, which is good enough for best-effort resolution of
/// string references (agent `model`, `name`, …).
pub(crate) fn collect_string_bindings(body: &[Statement<'_>]) -> HashMap<String, String> {
    let mut bindings = HashMap::new();
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let Some(name) = declarator.id.get_identifier_name() else {
                continue;
            };
            let Some(value) = declarator.init.as_ref().and_then(static_string_expression) else {
                continue;
            };
            bindings.insert(name.to_string(), value);
        }
    }
    bindings
}

/// The string value of an expression that is a plain string literal or a
/// no-substitution template literal; `None` for anything dynamic.
pub(crate) fn static_string_expression(expression: &Expression<'_>) -> Option<String> {
    match expression {
        Expression::StringLiteral(literal) => literal.value.as_str().map(str::to_owned),
        Expression::TemplateLiteral(literal) => literal
            .single_quasi()
            .and_then(|cooked| cooked.as_str())
            .map(str::to_owned),
        _ => None,
    }
}

/// The `VariableDeclaration` introduced by a statement, whether it is exported
/// (`export const ...`) or a plain top-level declaration (`const ...`).
pub(crate) fn statement_variable_declaration<'a>(
    statement: &'a Statement<'a>,
) -> Option<&'a VariableDeclaration<'a>> {
    match statement {
        Statement::VariableDeclaration(declaration) => Some(declaration),
        Statement::ExportDeclaration(export) => match &export.declaration {
            Declaration::VariableDeclaration(declaration) => Some(declaration.as_ref()),
            _ => None,
        },
        _ => None,
    }
}

/// Look up a static-named property on an object literal.
pub(crate) fn object_property<'a>(
    object: &'a ObjectExpression<'a>,
    name: &str,
) -> Option<&'a Expression<'a>> {
    for property in &object.properties {
        let ObjectPropertyKind::ObjectProperty(property) = property else {
            continue;
        };
        if property.key.is_specific_static_name(name) {
            return Some(&property.value);
        }
    }
    None
}

/// The identifier / string form of a module export name, via
/// [`ModuleExportName::name`].
pub(crate) fn module_export_name<'a>(name: &'a ModuleExportName<'a>) -> &'a str {
    name.name().as_str()
}

/// Local aliases and namespace imports for a named export from
/// `managed-deepagents` / `managed_deepagents`.
#[derive(Debug, Default)]
pub(crate) struct ManagedExportBindings {
    /// Local names bound to the named export (including aliases).
    pub aliases: HashSet<String>,
    /// Local names of `import * as ns from "…managed-deepagents…"`.
    pub namespaces: HashSet<String>,
}

/// Collect aliases for `export_name` plus managed-deepagents namespace imports.
///
/// The canonical `export_name` always counts as an alias even when the import
/// is omitted from a snippet under test.
pub(crate) fn collect_managed_export_bindings(
    body: &[Statement<'_>],
    export_name: &str,
) -> ManagedExportBindings {
    let mut aliases = HashSet::from([export_name.to_string()]);
    let mut namespaces = HashSet::new();

    for statement in body {
        let Statement::ImportDeclaration(import) = statement else {
            continue;
        };
        let Some(specifiers) = import.specifiers.as_ref() else {
            continue;
        };
        let from_managed = import
            .source
            .value
            .as_str()
            .is_some_and(|source| source.contains("managed-deepagents"));
        for specifier in specifiers {
            match specifier {
                ImportDeclarationSpecifier::ImportSpecifier(named) => {
                    if module_export_name(&named.imported) == export_name {
                        aliases.insert(named.local.name.to_string());
                    }
                }
                ImportDeclarationSpecifier::ImportNamespaceSpecifier(ns) if from_managed => {
                    namespaces.insert(ns.local.name.to_string());
                }
                _ => {}
            }
        }
    }

    ManagedExportBindings {
        aliases,
        namespaces,
    }
}

/// Find the call that initializes the top-level `agent` binding, if any.
pub(crate) fn find_agent_call<'a>(body: &'a [Statement<'a>]) -> Option<&'a CallExpression<'a>> {
    for statement in body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let is_agent = declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == "agent");
            if !is_agent {
                continue;
            }
            return match declarator.init.as_ref() {
                Some(Expression::CallExpression(call)) => Some(call),
                _ => None,
            };
        }
    }
    None
}

/// True when `callee` is the identifier `defineDeepAgent`.
pub(crate) fn is_define_deep_agent_callee(callee: &Expression<'_>) -> bool {
    matches!(callee, Expression::Identifier(id) if id.name == "defineDeepAgent")
}

/// First object-literal argument of a call, if present.
pub(crate) fn first_object_argument<'a>(
    call: &'a CallExpression<'a>,
) -> Option<&'a ObjectExpression<'a>> {
    call.arguments.iter().find_map(|argument| match argument {
        Argument::ObjectExpression(object) => Some(&**object),
        _ => None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A recovered syntax error: `oxc` reports the duplicate modifier, keeps a
    /// complete-looking AST, and never sets `fatal_error`.
    const RECOVERED: &str = "class A { public public x = 1; }\n";

    /// A syntax error the parser cannot recover from: the AST comes back empty.
    const BAILOUT: &str = "export const agent = defineDeepAgent({ name: \"x\" });\nconst b = ;\n";

    fn parse_error(source: &str, file_name: &str) -> String {
        let allocator = Allocator::default();
        match parse_typescript(&allocator, source, file_name) {
            Ok(_) => panic!("expected {file_name} to fail parsing"),
            Err(error) => error.to_string(),
        }
    }

    #[test]
    fn accepts_clean_typescript() {
        let allocator = Allocator::default();
        let parsed = parse_typescript(&allocator, "export const agent = 1;\n", "agent.ts").unwrap();
        assert_eq!(parsed.program.body.len(), 1);
    }

    #[test]
    fn finds_an_exported_variable_declaration() {
        let allocator = Allocator::default();
        let parsed = parse_typescript(&allocator, "export const agent = 1;\n", "agent.ts").unwrap();
        let declaration = statement_variable_declaration(&parsed.program.body[0]).unwrap();
        assert!(declaration.declarations[0]
            .id
            .get_identifier_name()
            .is_some_and(|name| name == "agent"));
    }

    #[test]
    fn rejects_a_recovered_syntax_error() {
        let message = parse_error(RECOVERED, "agent.ts");
        assert!(
            message.starts_with("failed to parse agent.ts:1:18:"),
            "{message}"
        );
    }

    #[test]
    fn rejects_a_syntax_error_the_parser_bailed_out_on() {
        let message = parse_error(BAILOUT, "agent.ts");
        assert!(
            message.starts_with("failed to parse agent.ts:2:11:"),
            "{message}"
        );
    }

    #[test]
    fn recovered_parse_keeps_the_ast_and_stays_quiet() {
        let allocator = Allocator::default();
        let parsed = parse_typescript_recovered(&allocator, RECOVERED, "agent.ts");
        assert!(!parsed.fatal_error);
        assert_eq!(parsed.program.body.len(), 1);
    }

    #[test]
    fn reports_the_count_of_further_errors() {
        let source = "class A { public public x = 1; }\nclass B { public public y = 1; }\n";
        let message = parse_error(source, "agent.ts");
        assert!(message.ends_with("(and 1 more error)"), "{message}");
    }

    #[test]
    fn line_column_counts_from_one() {
        assert_eq!(line_column("ab\ncd", 0), (1, 1));
        assert_eq!(line_column("ab\ncd", 3), (2, 1));
        assert_eq!(line_column("ab\ncd", 4), (2, 2));
    }
}
