//! Source text for `sandbox/__init__.py` or `sandbox/index.ts`.
//!
//! The starter template keeps its docstring and import line; this module fills
//! in its placeholders: `import os` when Python reads env vars, the extra
//! names to import, and the arguments to `define_sandbox(...)` /
//! `defineSandbox({...})`. With no [`SandboxOptions`],
//! the output is exactly the starter `mda init` has always written.
//!
//! Key casing follows the SDKs. Top-level arguments use each language's own
//! casing (`idle_ttl_seconds` / `idleTtlSeconds`), while everything inside
//! `proxy_config` and `run_config` stays snake_case in both languages: those
//! objects are LangSmith's sandbox API shapes, which the SDKs pass on as
//! written. Keys inside them are quoted so one renderer serves both languages.
//!
//! Every Fleet string goes through [`lit`], so nothing from Fleet reaches the
//! source unescaped, and commented-out lines also go through [`in_comment`].
//!
//! Env var values are never written here: they go into `.env` (see
//! `env.rs`), and `sandbox/` reads each by name when the agent runs. The
//! build only reads `sandbox/` for string literals, so nothing evaluates
//! these lookups before `.env` is loaded.

use serde_json::Value;

use crate::project::Language;

use super::source::{in_comment, lit, owner};
use super::{AccessControl, Connection, ConnectionFormat, HeaderValue, ProxyRule, SandboxOptions};

/// Placeholder in the Python template replaced with `import os` when env vars
/// are read, or nothing.
const STDLIB_PLACEHOLDER: &str = "__MDA_SANDBOX_STDLIB__";

/// Placeholder in sandbox templates replaced with the names imported next to
/// `define_sandbox` / `defineSandbox`.
const IMPORTS_PLACEHOLDER: &str = "__MDA_SANDBOX_IMPORTS__";

/// Placeholder in sandbox templates replaced with the sandbox's arguments.
const OPTIONS_PLACEHOLDER: &str = "__MDA_SANDBOX_OPTIONS__";

/// Idle seconds before the starter's sandbox is reclaimed.
const DEFAULT_IDLE_TTL_SECONDS: u64 = 600;

/// `template` with its arguments filled in from `options`, or the starter's
/// when there are none. `template` is the language's starter (`SANDBOX_PY` or
/// `SANDBOX_TS` in `scaffold.rs`).
pub(super) fn sandbox_module(
    template: &str,
    options: Option<&SandboxOptions>,
    language: Language,
) -> String {
    let default = SandboxOptions::default();
    let options = options.unwrap_or(&default);
    let stdlib = if language == Language::Python && !options.env_vars.is_empty() {
        "import os\n\n"
    } else {
        ""
    };
    template
        .replace(STDLIB_PLACEHOLDER, stdlib)
        .replace(IMPORTS_PLACEHOLDER, &imports(options, language))
        .replace(OPTIONS_PLACEHOLDER, &arguments(options, language))
}

/// Names to import before `define_sandbox` / `defineSandbox`, each followed by
/// `, `, or nothing. Only what the arguments use is imported, so the starter's
/// import line is unchanged and no generated file trips an unused-import lint.
/// Commented-out rules count too, so uncommenting one just works.
fn imports(options: &SandboxOptions, language: Language) -> String {
    let formats: Vec<&ConnectionFormat> = options
        .rules
        .iter()
        .flat_map(|rule| &rule.headers)
        .filter_map(|header| match &header.value {
            HeaderValue::Connection { format, .. } => Some(format),
            HeaderValue::WorkspaceSecret(_) => None,
        })
        .collect();
    if formats.is_empty() {
        return String::new();
    }
    let mut names = Vec::new();
    if formats
        .iter()
        .any(|format| matches!(format, ConnectionFormat::Basic { .. }))
    {
        names.push("basic");
    }
    if formats
        .iter()
        .any(|format| matches!(format, ConnectionFormat::Bearer))
    {
        names.push("bearer");
    }
    // Every connection-backed header reads its credential through
    // `connections.get`, whatever its format.
    names.push("connections");
    if formats
        .iter()
        .any(|format| matches!(format, ConnectionFormat::Template(_)))
    {
        names.push(match language {
            Language::Python => "credential_template",
            Language::TypeScript => "credentialTemplate",
        });
    }
    format!("{}, ", names.join(", "))
}

/// The arguments, one per line and indented one level, in a fixed order:
/// snapshot, idle TTL, delete-after-stop, timeout, env vars, then the proxy.
/// Unset options are left out so the SDK's defaults apply.
fn arguments(options: &SandboxOptions, language: Language) -> String {
    let py = language == Language::Python;
    let (indent, assign) = if py { ("    ", "=") } else { ("  ", ": ") };
    let key = |python: &str, typescript: &str| if py { python } else { typescript }.to_string();
    let mut out = String::new();
    if let Some(id) = &options.snapshot_id {
        out.push_str(&format!(
            "{indent}{}{assign}{},\n",
            key("snapshot_id", "snapshotId"),
            lit(id)
        ));
    }
    let idle = key("idle_ttl_seconds", "idleTtlSeconds");
    match options.idle_ttl_seconds {
        Some(seconds) => out.push_str(&format!("{indent}{idle}{assign}{seconds},\n")),
        // The starter's value and comment, so an export without a Fleet TTL
        // writes the same file `mda init` does.
        None => {
            out.push_str(&format!(
                "{indent}{} Reclaim idle sandboxes after 10 minutes.\n{indent}{idle}{assign}{DEFAULT_IDLE_TTL_SECONDS},\n",
                comment(language)
            ));
        }
    }
    if let Some(seconds) = options.delete_after_stop_seconds {
        out.push_str(&format!(
            "{indent}{}{assign}{seconds},\n",
            key("delete_after_stop_seconds", "deleteAfterStopSeconds")
        ));
    }
    // Fleet has no per-command timeout, so the starter's always applies.
    out.push_str(&format!(
        "{indent}{} Default per-command timeout, in seconds.\n{indent}{}{assign}600,\n",
        comment(language),
        key("default_timeout", "defaultTimeout")
    ));
    // `run_config.env_vars` is set for every command in the sandbox; the
    // rest of `run_config` (user, working directory) has no Fleet source.
    // Values are read from `.env` under a prefix (see `sandbox_env_key`); a
    // missing one becomes "" in both languages.
    if !options.env_vars.is_empty() {
        let vars: Vec<(String, String)> = options
            .env_vars
            .keys()
            .map(|name| {
                let env = lit(&super::env::sandbox_env_key(name));
                let read = match language {
                    Language::Python => format!("os.environ.get({env}, \"\")"),
                    Language::TypeScript => format!("process.env[{env}] ?? \"\""),
                };
                (lit(name), read)
            })
            .collect();
        let env = object(&vars, language);
        out.push_str(&format!(
            "{indent}{}{assign}{},\n",
            key("run_config", "runConfig"),
            object(&[(lit("env_vars"), env)], language)
        ));
    }
    let proxy = proxy_config(options, language);
    if !proxy.is_empty() {
        out.push_str(&format!(
            "{indent}{}{assign}{{\n{proxy}{indent}}},\n",
            key("proxy_config", "proxyConfig")
        ));
    }
    out
}

/// The body of `proxy_config` / `proxyConfig`, indented two levels, or empty
/// when there is no network list and no rule (the argument is then left out).
/// Header rules keep Fleet's order and come before the AWS and GCP rules,
/// since the proxy takes the first rule that matches a request.
fn proxy_config(options: &SandboxOptions, language: Language) -> String {
    let py = language == Language::Python;
    let indent = if py { "        " } else { "    " };
    let mut out = String::new();
    if let Some(control) = &options.access_control {
        let (name, hosts) = match control {
            AccessControl::AllowList(hosts) => ("allow_list", hosts),
            AccessControl::DenyList(hosts) => ("deny_list", hosts),
        };
        out.push_str(&format!(
            "{indent}{}: {},\n",
            lit("access_control"),
            object(&[(lit(name), list(hosts))], language)
        ));
    }
    // An empty `rules` list says nothing the proxy's default doesn't.
    if options.rules.is_empty() && options.provider_rules.is_empty() {
        return out;
    }
    out.push_str(&format!("{indent}{}: [\n", lit("rules")));
    for rule in &options.rules {
        let block = header_rule(rule, language);
        let Some(note) = &rule.commented_out else {
            out.push_str(&block);
            continue;
        };
        let lead = step(language).repeat(3);
        for line in note.lines() {
            out.push_str(&format!(
                "{lead}{} {}\n",
                comment(language),
                in_comment(line)
            ));
        }
        for line in block.lines() {
            let rest = line.trim_start();
            let indent = &line[..line.len() - rest.len()];
            out.push_str(&format!(
                "{indent}{} {}\n",
                comment(language),
                in_comment(rest)
            ));
        }
    }
    // AWS and GCP rules are LangSmith's own shape, and their credentials are
    // workspace secret names, so they are written as Fleet stored them.
    for rule in &options.provider_rules {
        out.push_str(&format!(
            "{indent}{}{},\n",
            step(language),
            value(rule, language)
        ));
    }
    out.push_str(&format!("{indent}],\n"));
    out
}

/// One header rule as a multi-line object, indented three levels inside the
/// `rules` list:
///
/// ```text
/// {
///     "name": "api-acme-dev",
///     "match_hosts": ["api.acme.dev"],
///     "headers": [
///         {"name": "X-Api-Key", "type": "workspace_secret", "value": "{ACME_KEY}"},
///         {"name": "Authorization", "value": bearer(connections.get("acme", {"type": "user"}))},
///     ],
/// },
/// ```
fn header_rule(rule: &ProxyRule, language: Language) -> String {
    let step = step(language);
    let outer = step.repeat(3);
    let field = step.repeat(4);
    let header_indent = step.repeat(5);
    let mut out = format!("{outer}{{\n");
    out.push_str(&format!("{field}{}: {},\n", lit("name"), lit(&rule.name)));
    out.push_str(&format!(
        "{field}{}: {},\n",
        lit("match_hosts"),
        list(&rule.match_hosts)
    ));
    out.push_str(&format!("{field}{}: [\n", lit("headers")));
    for header in &rule.headers {
        let mut fields = vec![(lit("name"), lit(&header.name))];
        match &header.value {
            // The proxy fills `{NAME}` in the template from the workspace
            // secret, so the credential never enters the sandbox.
            HeaderValue::WorkspaceSecret(template) => {
                fields.push((lit("type"), lit("workspace_secret")));
                fields.push((lit("value"), lit(template)));
            }
            // No `type`: the SDKs reject one on a connection-backed header,
            // and resolve the connection before each command instead.
            HeaderValue::Connection { connection, format } => {
                fields.push((lit("value"), connection_value(connection, format, language)));
            }
        }
        out.push_str(&format!("{header_indent}{},\n", object(&fields, language)));
    }
    out.push_str(&format!("{field}],\n{outer}}},\n"));
    out
}

/// `connections.get(slug, {type: owner})`, wrapped in `bearer` or `basic` for
/// those formats. A raw reference writes the credential as the header value.
fn connection_value(
    connection: &Connection,
    format: &ConnectionFormat,
    language: Language,
) -> String {
    let reference = match language {
        Language::Python => format!(
            "connections.get({}, {{\"type\": \"{}\"}})",
            lit(&connection.slug),
            owner(connection.owner)
        ),
        Language::TypeScript => format!(
            "connections.get({}, {{ type: \"{}\" }})",
            lit(&connection.slug),
            owner(connection.owner)
        ),
    };
    match format {
        ConnectionFormat::Raw => reference,
        ConnectionFormat::Bearer => format!("bearer({reference})"),
        ConnectionFormat::Basic { username } => format!("basic({}, {reference})", lit(username)),
        ConnectionFormat::Template(text) => match language {
            Language::Python => format!("credential_template({}, {reference})", lit(text)),
            Language::TypeScript => format!("credentialTemplate({}, {reference})", lit(text)),
        },
    }
}

/// One level of indentation.
fn step(language: Language) -> &'static str {
    match language {
        Language::Python => "    ",
        Language::TypeScript => "  ",
    }
}

/// A one-line object literal of already-rendered keys and values.
fn object(fields: &[(String, String)], language: Language) -> String {
    let body: Vec<String> = fields
        .iter()
        .map(|(key, value)| format!("{key}: {value}"))
        .collect();
    match language {
        Language::Python => format!("{{{}}}", body.join(", ")),
        Language::TypeScript => format!("{{ {} }}", body.join(", ")),
    }
}

/// A one-line list of string literals.
fn list(items: &[String]) -> String {
    let items: Vec<String> = items.iter().map(|item| lit(item)).collect();
    format!("[{}]", items.join(", "))
}

/// `value` as a Python or TypeScript literal. JSON is already valid TypeScript;
/// Python only differs in `None`, `True` and `False`.
fn value(value: &Value, language: Language) -> String {
    match (value, language) {
        (Value::Null, Language::Python) => "None".to_string(),
        (Value::Bool(true), Language::Python) => "True".to_string(),
        (Value::Bool(false), Language::Python) => "False".to_string(),
        (Value::String(text), _) => lit(text),
        (Value::Array(items), _) => {
            let items: Vec<String> = items
                .iter()
                .map(|item| self::value(item, language))
                .collect();
            format!("[{}]", items.join(", "))
        }
        (Value::Object(map), _) => {
            let fields: Vec<(String, String)> = map
                .iter()
                .map(|(key, item)| (lit(key), self::value(item, language)))
                .collect();
            object(&fields, language)
        }
        _ => value.to_string(),
    }
}

/// The line-comment marker for the starter's explanatory comments.
fn comment(language: Language) -> &'static str {
    match language {
        Language::Python => "#",
        Language::TypeScript => "//",
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use serde_json::json;

    use super::*;
    use crate::init::{ConnectionOwner, ProxyHeader};

    const PY: &str = "__MDA_SANDBOX_STDLIB__from managed_deepagents import __MDA_SANDBOX_IMPORTS__define_sandbox\n\nsandbox = define_sandbox(\n__MDA_SANDBOX_OPTIONS__)\n";
    const TS: &str = "import { __MDA_SANDBOX_IMPORTS__defineSandbox } from \"managed-deepagents\";\n\nexport const sandbox = defineSandbox({\n__MDA_SANDBOX_OPTIONS__});\n";

    fn options() -> SandboxOptions {
        SandboxOptions {
            snapshot_id: Some("snap-1".to_string()),
            idle_ttl_seconds: Some(1200),
            delete_after_stop_seconds: None,
            env_vars: BTreeMap::from([("LOG_LEVEL".to_string(), "debug".to_string())]),
            access_control: Some(AccessControl::AllowList(vec!["api.acme.dev".to_string()])),
            rules: vec![ProxyRule {
                name: "api-acme-dev".to_string(),
                match_hosts: vec!["api.acme.dev".to_string()],
                headers: vec![
                    ProxyHeader {
                        name: "X-Api-Key".to_string(),
                        value: HeaderValue::WorkspaceSecret("{ACME_KEY}".to_string()),
                    },
                    ProxyHeader {
                        name: "Authorization".to_string(),
                        value: HeaderValue::Connection {
                            connection: Connection {
                                slug: "acme".to_string(),
                                owner: ConnectionOwner::User,
                            },
                            format: ConnectionFormat::Bearer,
                        },
                    },
                ],
                commented_out: None,
            }],
            provider_rules: vec![json!({"name": "aws", "type": "aws", "enabled": true})],
        }
    }

    #[test]
    fn no_options_keeps_the_starter_arguments() {
        assert_eq!(
            sandbox_module(PY, None, Language::Python),
            "from managed_deepagents import define_sandbox\n\nsandbox = define_sandbox(\n    # Reclaim idle sandboxes after 10 minutes.\n    idle_ttl_seconds=600,\n    # Default per-command timeout, in seconds.\n    default_timeout=600,\n)\n"
        );
        assert_eq!(
            sandbox_module(TS, None, Language::TypeScript),
            "import { defineSandbox } from \"managed-deepagents\";\n\nexport const sandbox = defineSandbox({\n  // Reclaim idle sandboxes after 10 minutes.\n  idleTtlSeconds: 600,\n  // Default per-command timeout, in seconds.\n  defaultTimeout: 600,\n});\n"
        );
    }

    #[test]
    fn python_module_writes_every_option() {
        assert_eq!(
            sandbox_module(PY, Some(&options()), Language::Python),
            r#"import os

from managed_deepagents import bearer, connections, define_sandbox

sandbox = define_sandbox(
    snapshot_id="snap-1",
    idle_ttl_seconds=1200,
    # Default per-command timeout, in seconds.
    default_timeout=600,
    run_config={"env_vars": {"LOG_LEVEL": os.environ.get("SANDBOX_ENV_LOG_LEVEL", "")}},
    proxy_config={
        "access_control": {"allow_list": ["api.acme.dev"]},
        "rules": [
            {
                "name": "api-acme-dev",
                "match_hosts": ["api.acme.dev"],
                "headers": [
                    {"name": "X-Api-Key", "type": "workspace_secret", "value": "{ACME_KEY}"},
                    {"name": "Authorization", "value": bearer(connections.get("acme", {"type": "user"}))},
                ],
            },
            {"name": "aws", "type": "aws", "enabled": True},
        ],
    },
)
"#
        );
    }

    #[test]
    fn typescript_module_writes_every_option() {
        assert_eq!(
            sandbox_module(TS, Some(&options()), Language::TypeScript),
            r#"import { bearer, connections, defineSandbox } from "managed-deepagents";

export const sandbox = defineSandbox({
  snapshotId: "snap-1",
  idleTtlSeconds: 1200,
  // Default per-command timeout, in seconds.
  defaultTimeout: 600,
  runConfig: { "env_vars": { "LOG_LEVEL": process.env["SANDBOX_ENV_LOG_LEVEL"] ?? "" } },
  proxyConfig: {
    "access_control": { "allow_list": ["api.acme.dev"] },
    "rules": [
      {
        "name": "api-acme-dev",
        "match_hosts": ["api.acme.dev"],
        "headers": [
          { "name": "X-Api-Key", "type": "workspace_secret", "value": "{ACME_KEY}" },
          { "name": "Authorization", "value": bearer(connections.get("acme", { type: "user" })) },
        ],
      },
      { "name": "aws", "type": "aws", "enabled": true },
    ],
  },
});
"#
        );
    }

    #[test]
    fn basic_and_raw_connections_import_what_they_use() {
        let mut options = options();
        options.rules[0].headers = vec![
            ProxyHeader {
                name: "Authorization".to_string(),
                value: HeaderValue::Connection {
                    connection: Connection {
                        slug: "git".to_string(),
                        owner: ConnectionOwner::Agent,
                    },
                    format: ConnectionFormat::Basic {
                        username: "x-access-token".to_string(),
                    },
                },
            },
            ProxyHeader {
                name: "X-Token".to_string(),
                value: HeaderValue::Connection {
                    connection: Connection {
                        slug: "git".to_string(),
                        owner: ConnectionOwner::Agent,
                    },
                    format: ConnectionFormat::Raw,
                },
            },
        ];
        options.env_vars.clear();
        let module = sandbox_module(PY, Some(&options), Language::Python);
        assert!(module
            .starts_with("from managed_deepagents import basic, connections, define_sandbox\n"));
        assert!(module.contains(
            r#"{"name": "Authorization", "value": basic("x-access-token", connections.get("git", {"type": "agent"}))},"#
        ));
        assert!(module.contains(
            r#"{"name": "X-Token", "value": connections.get("git", {"type": "agent"})},"#
        ));
    }

    #[test]
    fn template_headers_and_commented_out_rules() {
        let mut options = options();
        options.env_vars.clear();
        options.rules[0].headers = vec![ProxyHeader {
            name: "Authorization".to_string(),
            value: HeaderValue::Connection {
                connection: Connection {
                    slug: "github".to_string(),
                    owner: ConnectionOwner::Agent,
                },
                format: ConnectionFormat::Template("token {token}".to_string()),
            },
        }];
        options.rules[0].commented_out = Some("Create `github` first.".to_string());
        let python = sandbox_module(PY, Some(&options), Language::Python);
        assert!(python.starts_with(
            "from managed_deepagents import connections, credential_template, define_sandbox\n"
        ));
        assert!(python.contains(
            "            # Create `github` first.\n            # {\n                # \"name\": \"api-acme-dev\",\n"
        ));
        assert!(python.contains(
            r#"# {"name": "Authorization", "value": credential_template("token {token}", connections.get("github", {"type": "agent"}))},"#
        ));
        let typescript = sandbox_module(TS, Some(&options), Language::TypeScript);
        assert!(typescript.contains(
            "credentialTemplate(\"token {token}\", connections.get(\"github\", { type: \"agent\" }))"
        ));
        assert!(typescript.contains("      // Create `github` first.\n      // {\n"));
    }
}
