//! Detect whether a project's identity declaration requires
//! `MDA_INGRESS_SECRET` at runtime (`backend` HTTP ingress), and extract a
//! static summary of isolation scope for CLI reporting.
//!
//! Detection is AST-based and scoped to the exported `identity` binding — the
//! same approach as agent-model extraction — so comments and unrelated string
//! literals cannot false-trigger the deploy gate. Constructor recognition
//! follows imports: `defineIdentity as di` / `define_identity as di` and
//! namespace imports still count as the managed constructor.

use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{Argument, CallExpression, Expression, ObjectExpression, ObjectPropertyKind};
use serde::Deserialize;

use crate::project::{Language, Project};
use crate::python_extract::{run_python_extractor, scripts};
use crate::typescript::{
    collect_managed_export_bindings, object_property, parse_typescript,
    statement_variable_declaration, static_string_expression, ManagedExportBindings,
};

use super::discovery::find_identity_entry;

/// Resolved isolation axes from a static `defineIdentity` / `define_identity` call.
///
/// Threads are always user-owned and durable memory has its own declaration, so
/// neither is part of the static summary.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct IdentityScopeAxes {
    pub credentials: String,
}

/// How callers authenticate over HTTP, when statically resolvable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdentityAuthMode {
    /// Reserved ingress headers from a backend the author operates.
    Backend,
    /// Caller presents a LangSmith workspace API key.
    LangSmithApiKey,
    /// Caller presents a token MDA verifies (IdP providers).
    ValidatedToken,
}

impl IdentityAuthMode {
    pub fn label(self) -> &'static str {
        match self {
            Self::Backend => "backend",
            Self::LangSmithApiKey => "langsmith_api_key",
            Self::ValidatedToken => "validated_token",
        }
    }
}

/// Root identity declaration discovered for a project, with static scope when known.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IdentitySummary {
    /// Path relative to the project root (`identity.ts`, `identity.py`, …).
    pub path: PathBuf,
    /// HTTP ingress auth mode when statically resolvable.
    pub auth: Option<IdentityAuthMode>,
    /// True when the constructor call definitely omits the required `auth` option.
    pub auth_omitted: bool,
    /// Credentials when the scope is statically resolvable.
    pub scope: Option<IdentityScopeAxes>,
    /// True when the declaration still sets the retired `scope.memory` axis.
    /// Memory moved to `memory.{ts,py}`, so its presence is a migration error.
    pub declares_memory: bool,
}

/// Managed user-isolation policy (mirrors both package runtimes).
///
/// Threads are always user-owned and durable memory has its own declaration, so
/// credentials are the only axis left to report.
fn managed_user_scope() -> IdentityScopeAxes {
    IdentityScopeAxes {
        credentials: "user".into(),
    }
}

/// Read the project's identity declaration for CLI summaries.
///
/// Returns `None` when there is no identity module. A present module with a
/// Auth and the managed scope are extracted from a single parse / Python
/// subprocess.
pub(crate) fn read_identity_summary(project: &Project) -> Result<Option<IdentitySummary>> {
    let Some(rel) = find_identity_entry(project) else {
        return Ok(None);
    };
    let path = project.root.join(&rel);
    let source =
        fs::read_to_string(&path).with_context(|| format!("failed to read {}", path.display()))?;
    let file_label = rel.to_string_lossy();
    let extracted = match project.language {
        Language::TypeScript => extract_identity_summary_typescript(&source, &file_label)?,
        Language::Python => extract_identity_summary_python(&source, &file_label)?,
    };
    Ok(Some(IdentitySummary {
        path: rel,
        auth: extracted.auth,
        auth_omitted: extracted.auth_omitted,
        scope: extracted.scope,
        declares_memory: extracted.declares_memory,
    }))
}

/// Env var the managed auth handler reads for `backend` ingress.
pub(crate) const INGRESS_SECRET_ENV: &str = "MDA_INGRESS_SECRET";

/// Canonical TypeScript export name for the identity constructor.
const TS_DEFINE_IDENTITY: &str = "defineIdentity";

/// Whether the project's identity declaration needs [`INGRESS_SECRET_ENV`].
///
/// Returns `false` when the project has no identity module or the `identity`
/// binding's HTTP ingress cannot be resolved to a static `backend`
/// (best-effort, matching model extraction). An identity module that does not
/// parse is an error: silently reading it as "no secret required" would deploy
/// an ingress the author cannot authenticate against.
pub(crate) fn identity_requires_ingress_secret(project: &Project) -> Result<bool> {
    Ok(read_identity_summary(project)?
        .as_ref()
        .is_some_and(|summary| summary.auth == Some(IdentityAuthMode::Backend)))
}

/// Static identity facts read in one pass.
struct IdentityExtraction {
    auth: Option<IdentityAuthMode>,
    auth_omitted: bool,
    scope: Option<IdentityScopeAxes>,
    declares_memory: bool,
}

/// Classify auth + scope from a TypeScript identity module in one parse.
fn extract_identity_summary_typescript(
    source: &str,
    file_name: &str,
) -> Result<IdentityExtraction> {
    let allocator = Allocator::default();
    let parsed = parse_typescript(&allocator, source, file_name)?;
    let bindings = collect_managed_export_bindings(&parsed.program.body, TS_DEFINE_IDENTITY);

    for statement in &parsed.program.body {
        let Some(declaration) = statement_variable_declaration(statement) else {
            continue;
        };
        for declarator in &declaration.declarations {
            let is_identity = declarator
                .id
                .get_identifier_name()
                .is_some_and(|name| name == "identity");
            if !is_identity {
                continue;
            }
            let Some(Expression::CallExpression(call)) = declarator.init.as_ref() else {
                continue;
            };
            // Skip unrelated calls assigned to `identity` so a later
            // `defineIdentity(...)` declarator still wins.
            if !is_define_identity_call(&call.callee, &bindings) {
                continue;
            }
            let auth = call_auth_mode(call, &bindings);
            return Ok(IdentityExtraction {
                auth,
                auth_omitted: call_auth_omitted(call),
                scope: Some(managed_user_scope()),
                declares_memory: call_declares_scope_memory(call),
            });
        }
    }
    Ok(IdentityExtraction {
        auth: None,
        auth_omitted: false,
        scope: None,
        declares_memory: false,
    })
}

fn call_auth_omitted(call: &CallExpression<'_>) -> bool {
    match call.arguments.first() {
        None => true,
        Some(Argument::ObjectExpression(options)) => {
            object_property(options, "auth").is_none()
                && !options
                    .properties
                    .iter()
                    .any(|property| matches!(property, ObjectPropertyKind::SpreadProperty(_)))
        }
        Some(_) => false,
    }
}

/// Whether a `defineIdentity(...)` call still sets the retired `scope.memory`
/// axis. Reported separately from the resolved axes so the migration error fires
/// even when the rest of the scope cannot be resolved statically.
fn call_declares_scope_memory(call: &CallExpression<'_>) -> bool {
    let Some(Argument::ObjectExpression(options)) = call.arguments.first() else {
        return false;
    };
    let Some(Expression::ObjectExpression(scope)) = object_property(options, "scope") else {
        return false;
    };
    object_property(scope, "memory").is_some()
}

/// Classify a TypeScript identity module: `Some(true)` when the `identity`
/// binding resolves to `backend` HTTP ingress, `None` when it cannot be
/// resolved statically.
#[cfg(test)]
pub(super) fn extract_requires_ingress_secret_typescript(
    source: &str,
    file_name: &str,
) -> Result<Option<bool>> {
    Ok(extract_identity_summary_typescript(source, file_name)?
        .auth
        .map(|auth| auth == IdentityAuthMode::Backend))
}

fn call_auth_mode(
    call: &CallExpression<'_>,
    bindings: &ManagedExportBindings,
) -> Option<IdentityAuthMode> {
    if !is_define_identity_call(&call.callee, bindings) {
        return None;
    }
    let argument = call.arguments.first()?;
    match object_auth_ingress(argument_object(argument)?)? {
        HttpIngressKind::Backend => Some(IdentityAuthMode::Backend),
        HttpIngressKind::LangSmithApiKey => Some(IdentityAuthMode::LangSmithApiKey),
        HttpIngressKind::ValidatedToken => Some(IdentityAuthMode::ValidatedToken),
    }
}

fn is_define_identity_call(callee: &Expression<'_>, bindings: &ManagedExportBindings) -> bool {
    match callee {
        Expression::Identifier(id) => bindings.aliases.contains(id.name.as_str()),
        Expression::StaticMemberExpression(member)
            if member.property.name.as_str() == TS_DEFINE_IDENTITY =>
        {
            matches!(
                &member.object,
                Expression::Identifier(id) if bindings.namespaces.contains(id.name.as_str())
            )
        }
        _ => false,
    }
}

fn argument_object<'a>(argument: &'a Argument<'a>) -> Option<&'a ObjectExpression<'a>> {
    match argument {
        Argument::ObjectExpression(object) => Some(object),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HttpIngressKind {
    Backend,
    LangSmithApiKey,
    ValidatedToken,
}

/// Read `auth` off a `defineIdentity(...)` options object literal.
fn object_auth_ingress(object: &ObjectExpression<'_>) -> Option<HttpIngressKind> {
    let auth = object_property(object, "auth")?;
    if let Some(value) = static_string_expression(auth) {
        return (value.as_str() == "backend").then_some(HttpIngressKind::Backend);
    }
    // A backend is only spellable as the string, so an `auth.*` call, a list
    // of them, or an inline provider is validated-token ingress. Anything else
    // could still evaluate to the string, so it stays unknown rather than being
    // assumed either way.
    match auth {
        Expression::CallExpression(call)
            if matches!(
                &call.callee,
                Expression::StaticMemberExpression(member)
                    if member.property.name.as_str() == "langsmithApiKey"
            ) =>
        {
            Some(HttpIngressKind::LangSmithApiKey)
        }
        Expression::CallExpression(_)
        | Expression::ArrayExpression(_)
        | Expression::ObjectExpression(_) => Some(HttpIngressKind::ValidatedToken),
        _ => None,
    }
}

/// Classify a Python identity module via `python3` + `ast` (same pattern as
/// model extraction).
#[cfg(test)]
pub(super) fn extract_requires_ingress_secret_python(
    source: &str,
    file_label: &str,
) -> Result<Option<bool>> {
    Ok(extract_identity_summary_python(source, file_label)?
        .auth
        .map(|auth| auth == IdentityAuthMode::Backend))
}

/// Extract static scope axes from a TypeScript identity module.
#[cfg(test)]
pub(super) fn extract_identity_scope_typescript(
    source: &str,
    file_name: &str,
) -> Result<Option<IdentityScopeAxes>> {
    Ok(extract_identity_summary_typescript(source, file_name)?.scope)
}

/// Extract static scope axes from a Python identity module.
#[cfg(test)]
pub(super) fn extract_identity_scope_python(
    source: &str,
    file_label: &str,
) -> Result<Option<IdentityScopeAxes>> {
    Ok(extract_identity_summary_python(source, file_label)?.scope)
}

#[derive(Debug, Deserialize)]
struct PythonIdentitySummary {
    auth: Option<String>,
    auth_omitted: bool,
    scope: Option<IdentityScopeAxes>,
    declares_memory: bool,
}

fn extract_identity_summary_python(source: &str, file_label: &str) -> Result<IdentityExtraction> {
    let output = run_python_extractor(
        scripts::IDENTITY_SUMMARY,
        source,
        file_label,
        "identity summary",
    )?
    .ok_or_else(|| {
        anyhow::anyhow!("identity summary extractor produced no output for {file_label}")
    })?;
    let extracted: PythonIdentitySummary = serde_json::from_str(&output)
        .with_context(|| format!("failed to parse identity summary JSON from {file_label}"))?;
    let auth = match extracted.auth.as_deref() {
        Some("backend") => Some(IdentityAuthMode::Backend),
        Some("langsmith_api_key") => Some(IdentityAuthMode::LangSmithApiKey),
        Some("validated_token") => Some(IdentityAuthMode::ValidatedToken),
        _ => None,
    };
    Ok(IdentityExtraction {
        auth,
        auth_omitted: extracted.auth_omitted,
        scope: extracted.scope,
        declares_memory: extracted.declares_memory,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    use crate::project::{Language, Project};

    /// Classification of fixtures that are expected to parse; a parse error is
    /// a bug in the fixture, so these helpers unwrap it.
    fn ts_ingress(source: &str) -> Option<bool> {
        extract_requires_ingress_secret_typescript(source, "identity.ts").unwrap()
    }

    fn py_ingress(source: &str) -> Option<bool> {
        extract_requires_ingress_secret_python(source, "identity.py").unwrap()
    }

    fn ts_auth_omitted(source: &str) -> bool {
        extract_identity_summary_typescript(source, "identity.ts")
            .unwrap()
            .auth_omitted
    }

    fn py_auth_omitted(source: &str) -> bool {
        extract_identity_summary_python(source, "identity.py")
            .unwrap()
            .auth_omitted
    }

    #[test]
    fn ts_skips_unrelated_identity_declarator_before_define_identity() {
        assert_eq!(
            ts_ingress(
                r#"
import { defineIdentity } from "managed-deepagents";
var identity = helper();
var identity = defineIdentity({ auth: "backend" });
"#
            ),
            Some(true)
        );
    }

    #[test]
    fn ts_bare_define_identity_omits_required_auth() {
        assert!(ts_auth_omitted("export const identity = defineIdentity();"));
    }

    #[test]
    fn ts_scope_only_options_omit_required_auth() {
        assert!(ts_auth_omitted(
            r#"
export const identity = defineIdentity({
  scope: { memory: "user" },
});
"#
        ));
    }

    #[test]
    fn ts_spread_options_do_not_prove_auth_is_omitted() {
        assert!(!ts_auth_omitted(
            r#"
const options = { auth: "backend" as const };
export const identity = defineIdentity({ ...options });
"#
        ));
    }

    #[test]
    fn ts_explicit_backend_auth_requires_secret() {
        assert_eq!(
            ts_ingress(r#"export const identity = defineIdentity({ auth: "backend" });"#),
            Some(true)
        );
    }

    #[test]
    fn ts_provider_auth_does_not_require_secret() {
        assert_eq!(
            ts_ingress(
                r#"
export const identity = defineIdentity({
  auth: auth.supabase({ projectRef: "abcdefgh" }),
});
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn ts_langsmith_api_key_auth_is_classified_explicitly() {
        let extracted = extract_identity_summary_typescript(
            r#"export const identity = defineIdentity({ auth: auth.langsmithApiKey() });"#,
            "identity.ts",
        )
        .unwrap();
        assert_eq!(extracted.auth, Some(IdentityAuthMode::LangSmithApiKey));
    }

    #[test]
    fn ts_provider_list_auth_does_not_require_secret() {
        assert_eq!(
            ts_ingress(
                r#"
export const identity = defineIdentity({
  auth: [auth.supabase({ projectRef: "abcdefgh" })],
});
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn ts_comment_mentioning_trusted_backend_is_ignored() {
        assert_eq!(
            ts_ingress(
                r#"
// Previously: auth: "backend"
export const identity = defineIdentity({
  auth: [auth.supabase({ projectRef: "abcdefgh" })],
});
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn ts_unrelated_source_returns_none() {
        assert_eq!(
            ts_ingress("// no identity export\nexport const x = 1;\n"),
            None
        );
    }

    #[test]
    fn ts_syntax_error_is_reported_rather_than_read_as_no_secret() {
        let error = extract_requires_ingress_secret_typescript(
            "export const identity = defineIdentity({ auth: \"backend\";\n",
            "identity.ts",
        )
        .unwrap_err()
        .to_string();
        assert!(error.starts_with("failed to parse identity.ts:"), "{error}");
    }

    #[test]
    fn py_syntax_error_is_reported_rather_than_read_as_no_secret() {
        let error = extract_requires_ingress_secret_python(
            "identity = define_identity(auth=\"backend\"\n",
            "identity.py",
        )
        .unwrap_err()
        .to_string();
        assert!(error.starts_with("failed to parse identity.py:"), "{error}");
    }

    #[test]
    fn py_provider_list_auth_does_not_require_secret() {
        assert_eq!(
            py_ingress(
                r#"
identity = define_identity(auth=[auth.supabase(project_ref="abcdefgh")])
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn py_bare_define_identity_omits_required_auth() {
        assert!(py_auth_omitted("identity = define_identity()\n"));
    }

    #[test]
    fn py_skips_unrelated_identity_assignment_before_define_identity() {
        assert_eq!(
            py_ingress(
                r#"
from managed_deepagents import define_identity
identity = helper()
identity = define_identity(auth="backend")
"#
            ),
            Some(true)
        );
    }

    #[test]
    fn py_scope_only_options_omit_required_auth() {
        assert!(py_auth_omitted(
            "identity = define_identity(scope={\"memory\": \"user\"})\n"
        ));
    }

    #[test]
    fn py_unpacked_options_do_not_prove_auth_is_omitted() {
        assert!(!py_auth_omitted(
            "options = {\"auth\": \"backend\"}\nidentity = define_identity(**options)\n"
        ));
    }

    #[test]
    fn py_explicit_backend_auth_requires_secret() {
        assert_eq!(
            py_ingress("identity = define_identity(auth=\"backend\")\n"),
            Some(true)
        );
    }

    #[test]
    fn py_provider_auth_does_not_require_secret() {
        assert_eq!(
            py_ingress(
                r#"
identity = define_identity(auth=auth.supabase(project_ref="abcdefgh"))
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn py_langsmith_api_key_auth_is_classified_explicitly() {
        let extracted = extract_identity_summary_python(
            "identity = define_identity(auth=auth.langsmith_api_key())\n",
            "identity.py",
        )
        .unwrap();
        assert_eq!(extracted.auth, Some(IdentityAuthMode::LangSmithApiKey));
    }

    #[test]
    fn py_comment_mentioning_backend_is_ignored() {
        assert_eq!(
            py_ingress(
                r#"
# was auth="backend"
identity = define_identity(auth=[auth.supabase(project_ref="abcdefgh")])
"#
            ),
            Some(false)
        );
    }

    #[test]
    fn py_aliased_import_requires_secret() {
        assert_eq!(
            py_ingress(
                r#"
from managed_deepagents import define_identity as di
identity = di(auth="backend")
"#
            ),
            Some(true)
        );
    }

    #[test]
    fn py_module_alias_requires_secret() {
        assert_eq!(
            py_ingress(
                r#"
import managed_deepagents as mda
identity = mda.define_identity(auth="backend")
"#
            ),
            Some(true)
        );
    }

    #[test]
    fn project_without_identity_does_not_require_secret() {
        let root = tempfile::tempdir().unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("agent.py")],
        };
        assert!(!identity_requires_ingress_secret(&project).unwrap());
    }

    #[test]
    fn project_with_omitted_auth_does_not_require_secret() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(
            root.path().join("identity.py"),
            "from managed_deepagents import define_identity\n\
             identity = define_identity()\n",
        )
        .unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::Python,
            agent_file: PathBuf::from("agent.py"),
            files: vec![PathBuf::from("agent.py"), PathBuf::from("identity.py")],
        };
        assert!(!identity_requires_ingress_secret(&project).unwrap());
        assert!(read_identity_summary(&project)
            .unwrap()
            .is_some_and(|identity| identity.auth_omitted));
    }

    fn ts_scope(source: &str) -> Option<IdentityScopeAxes> {
        extract_identity_scope_typescript(source, "identity.ts").unwrap()
    }

    fn py_scope(source: &str) -> Option<IdentityScopeAxes> {
        extract_identity_scope_python(source, "identity.py").unwrap()
    }

    fn user_axes() -> IdentityScopeAxes {
        IdentityScopeAxes {
            credentials: "user".into(),
        }
    }

    fn ts_declares_memory(source: &str) -> bool {
        extract_identity_summary_typescript(source, "identity.ts")
            .unwrap()
            .declares_memory
    }

    fn py_declares_memory(source: &str) -> bool {
        extract_identity_summary_python(source, "identity.py")
            .unwrap()
            .declares_memory
    }

    #[test]
    fn ts_reports_the_retired_memory_axis_for_any_value() {
        // Presence is reported even when the axis value cannot be resolved, so the
        // migration error fires instead of the scope quietly reading as unknown.
        for source in [
            r#"export const identity = defineIdentity({ scope: { memory: "agent" } });"#,
            r#"export const identity = defineIdentity({ scope: { memory: ["agent", "user"] } });"#,
            r#"export const identity = defineIdentity({ scope: { default: "user", memory } });"#,
        ] {
            assert!(ts_declares_memory(source), "{source}");
        }
    }

    #[test]
    fn ts_does_not_report_a_memory_axis_when_absent() {
        for source in [
            "export const identity = defineIdentity();",
            r#"export const identity = defineIdentity({ scope: "user" });"#,
            r#"export const identity = defineIdentity({ scope: { credentials: "user" } });"#,
        ] {
            assert!(!ts_declares_memory(source), "{source}");
        }
    }

    #[test]
    fn py_reports_the_retired_memory_axis_for_any_value() {
        if !crate::toolchain::python3_available() {
            return;
        }
        for source in [
            "identity = define_identity(scope={\"memory\": \"agent\"})\n",
            "identity = define_identity(scope={\"default\": \"user\", \"memory\": [\"agent\"]})\n",
        ] {
            assert!(py_declares_memory(source), "{source}");
        }
        assert!(!py_declares_memory(
            "identity = define_identity(scope=\"user\")\n"
        ));
    }

    #[test]
    fn ts_define_identity_uses_managed_user_scope() {
        assert_eq!(
            ts_scope("export const identity = defineIdentity();"),
            Some(user_axes())
        );
    }

    #[test]
    fn ts_auth_option_keeps_managed_user_scope() {
        assert_eq!(
            ts_scope(
                r#"export const identity = defineIdentity({ auth: auth.supabase({ projectRef: "abc" }) });"#
            ),
            Some(user_axes())
        );
    }

    #[test]
    fn py_define_identity_uses_managed_user_scope() {
        assert_eq!(
            py_scope("identity = define_identity()\n"),
            Some(user_axes())
        );
    }

    #[test]
    fn read_identity_summary_returns_none_without_module() {
        let root = tempfile::tempdir().unwrap();
        let project = Project {
            root: root.path().to_path_buf(),
            language: Language::TypeScript,
            agent_file: PathBuf::from("agent.ts"),
            files: vec![PathBuf::from("agent.ts")],
        };
        assert!(read_identity_summary(&project).unwrap().is_none());
    }
}
