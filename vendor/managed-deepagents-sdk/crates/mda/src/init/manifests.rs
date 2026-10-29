//! Dynamic package manifests for scaffolded projects.

use crate::dependencies;
use crate::project::Language;

/// Build a minimal `package.json` for a TypeScript project named `name`.
pub(super) fn package_json(name: &str) -> String {
    let deps = dependencies::package_json_entries(4);
    format!(
        r#"{{
  "name": "{name}",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "description": "A Managed Deep Agent project.",
  "dependencies": {{
{deps}
  }},
  "devDependencies": {{
    "@types/node": "^26.1.1"
  }}
}}
"#
    )
}

/// Build a minimal `pyproject.toml` for a Python project named `name`.
pub(super) fn pyproject_toml(name: &str) -> String {
    let deps = dependencies::pyproject_entries(4);
    format!(
        r#"[project]
name = "{name}"
version = "0.0.0"
description = "A Managed Deep Agent project."
requires-python = ">=3.11"
dependencies = [
{deps}
]

[tool.mda]
python-version = "3.14"
"#
    )
}

/// Manifest contents for `language`.
pub(super) fn manifest_contents(name: &str, language: Language) -> String {
    match language {
        Language::TypeScript => package_json(name),
        Language::Python => pyproject_toml(name),
    }
}
