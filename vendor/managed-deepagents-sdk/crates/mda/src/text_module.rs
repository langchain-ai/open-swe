//! Resolve TypeScript "text module" imports at compile time.
//!
//! The [text modules proposal] lets a module import a file's raw contents as a
//! string via an import attribute:
//!
//! ```ts
//! import systemPrompt from "./instructions.md" with { type: "text" };
//! ```
//!
//! This syntax is still not supported across every runtime/bundler, so the CLI
//! resolves it ahead of time: it parses each TypeScript source with the `oxc`
//! parser, finds default imports carrying a `type: "text"` attribute, reads the
//! referenced file, and rewrites the import into an inline `const` bound to a
//! template literal:
//!
//! ```ts
//! const systemPrompt = `# Assistant
//!
//! You are a helpful assistant.`;
//! ```
//!
//! Only the matched import statements are rewritten; every other byte of the
//! source is preserved. Because the inlined content goes into a template
//! literal, backticks, `${` interpolation starts, and backslashes are escaped.
//!
//! [text modules proposal]: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/import/with#text_modules_type_text

use std::path::Path;

use anyhow::{anyhow, Context, Result};
use oxc::allocator::Allocator;
use oxc::ast::ast::{ImportDeclarationSpecifier, Statement};

use crate::typescript::parse_typescript_recovered;

/// Import-attribute `type` value whose modules are inlined as raw text.
/// Matches the [text modules proposal] (`type: "text"` only).
const TEXT_MODULE_TYPE: &str = "text";

/// Inline every text-module import found in `source`.
///
/// `file_name` is used to pick the TypeScript dialect (`.tsx` vs `.ts`) and for
/// error messages. `resolve_dir` is the directory the importing file lives in,
/// against which relative import specifiers are resolved.
///
/// Returns the rewritten source, or the original source unchanged when there
/// are no text-module imports.
///
/// Every TypeScript file in the project passes through here, so syntax errors
/// are deliberately not reported: the file is copied verbatim and the language
/// toolchain that compiles it produces the authoritative diagnostic. Imports
/// this rewriter never sees stay in the output as written.
pub fn inline_text_modules(source: &str, file_name: &str, resolve_dir: &Path) -> Result<String> {
    let allocator = Allocator::default();
    let parsed = parse_typescript_recovered(&allocator, source, file_name);

    let mut edits: Vec<Edit> = Vec::new();
    for statement in &parsed.program.body {
        let Statement::ImportDeclaration(import) = statement else {
            continue;
        };
        let Some(with_clause) = import.with_clause.as_ref() else {
            continue;
        };

        let type_value = with_clause
            .with_entries
            .iter()
            .find_map(|entry| (entry.key.as_js_str() == "type").then_some(entry.value.value));
        let Some(type_value) = type_value else {
            continue;
        };
        if type_value != TEXT_MODULE_TYPE {
            continue;
        }
        let type_value = TEXT_MODULE_TYPE;

        let specifier = import.source.value.as_str().ok_or_else(|| {
            anyhow!("text-module import path in {file_name} contains a lone surrogate")
        })?;
        let local = default_import_binding(import.specifiers.as_ref().map(|v| &v[..])).ok_or_else(
            || {
                anyhow!(
                "text-module import of \"{specifier}\" in {file_name} must use a single default \
                 import, e.g. `import name from \"{specifier}\" with {{ type: \"{type_value}\" }}`"
            )
            },
        )?;

        let resolved = resolve_dir.join(specifier);
        let contents = std::fs::read_to_string(&resolved).with_context(|| {
            format!(
                "failed to inline text module \"{specifier}\" imported by {file_name} \
                 (resolved to {})",
                resolved.display()
            )
        })?;

        let start = import.span.start as usize;
        let end = consume_trailing_semicolon(source, import.span.end as usize);
        let replacement = format!("const {local} = `{}`;", escape_template_literal(&contents));
        edits.push(Edit {
            start,
            end,
            text: replacement,
        });
    }

    if edits.is_empty() {
        return Ok(source.to_string());
    }
    Ok(apply_edits(source, edits))
}

/// Escape a raw string so it can be embedded inside a backtick template
/// literal without changing its meaning.
///
/// Backslashes are escaped first (so the escapes added below are not
/// double-escaped), then backticks (which would otherwise close the literal)
/// and `${` sequences (which would otherwise start an interpolation).
fn escape_template_literal(content: &str) -> String {
    content
        .replace('\\', "\\\\")
        .replace('`', "\\`")
        .replace("${", "\\${")
}

/// A single non-overlapping replacement of `source[start..end]` with `text`.
struct Edit {
    start: usize,
    end: usize,
    text: String,
}

/// Apply non-overlapping `edits`, right-to-left so earlier offsets stay valid.
fn apply_edits(source: &str, mut edits: Vec<Edit>) -> String {
    edits.sort_by_key(|edit| (edit.start, edit.end));
    let mut out = source.to_string();
    for edit in edits.into_iter().rev() {
        out.replace_range(edit.start..edit.end, &edit.text);
    }
    out
}

/// Return the byte offset just past an optional `;` following `from`, skipping
/// only spaces/tabs (never newlines, to avoid swallowing the next statement).
fn consume_trailing_semicolon(source: &str, from: usize) -> usize {
    let bytes = source.as_bytes();
    let mut i = from;
    while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
        i += 1;
    }
    if i < bytes.len() && bytes[i] == b';' {
        i + 1
    } else {
        from
    }
}

/// Return the binding name of a sole default import (e.g. `x` in
/// `import x from "..."`), or `None` for anything else (named/namespace
/// imports, multiple specifiers, side-effect imports).
fn default_import_binding<'a>(
    specifiers: Option<&'a [ImportDeclarationSpecifier<'a>]>,
) -> Option<&'a str> {
    let specifiers = specifiers?;
    if specifiers.len() != 1 {
        return None;
    }
    match &specifiers[0] {
        ImportDeclarationSpecifier::ImportDefaultSpecifier(default) => {
            Some(default.local.name.as_str())
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    /// Write `contents` to `dir/name` and return the directory path.
    fn write(dir: &Path, name: &str, contents: &str) {
        fs::write(dir.join(name), contents).unwrap();
    }

    fn inline(dir: &Path, source: &str) -> String {
        inline_text_modules(source, "agent.ts", dir).unwrap()
    }

    #[test]
    fn escapes_backticks_dollars_and_backslashes() {
        assert_eq!(escape_template_literal("plain"), "plain");
        assert_eq!(escape_template_literal("a`b"), "a\\`b");
        assert_eq!(escape_template_literal("${x}"), "\\${x}");
        assert_eq!(escape_template_literal("a\\b"), "a\\\\b");
        // Backslash-escaped first so the original `\${` survives intact.
        assert_eq!(escape_template_literal("\\${x}"), "\\\\\\${x}");
        assert_eq!(escape_template_literal("`${a}`"), "\\`\\${a}\\`");
    }

    #[test]
    fn inlines_a_text_import_as_a_template_literal() {
        let dir = tempfile::tempdir().unwrap();
        write(
            dir.path(),
            "instructions.md",
            "# Assistant\n\nBe helpful.\n",
        );

        let source =
            "import systemPrompt from \"./instructions.md\" with { type: \"text\" };\nexport const x = systemPrompt;\n";
        let out = inline(dir.path(), source);

        assert_eq!(
            out,
            "const systemPrompt = `# Assistant\n\nBe helpful.\n`;\nexport const x = systemPrompt;\n"
        );
    }

    #[test]
    fn leaves_markdown_type_untouched() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "instructions.md", "hello");

        // Only `type: "text"` is supported (text modules proposal). A bare
        // `type: "markdown"` attribute is left for the language toolchain.
        let source = "import sp from \"./instructions.md\" with { type: \"markdown\" };\n";
        let out = inline(dir.path(), source);

        assert_eq!(out, source);
    }

    #[test]
    fn escapes_markdown_containing_backticks_and_interpolation() {
        let dir = tempfile::tempdir().unwrap();
        write(
            dir.path(),
            "instructions.md",
            "Use `code` and ${vars} and a path C:\\tmp",
        );

        let source = "import sp from \"./instructions.md\" with { type: \"text\" };\n";
        let out = inline(dir.path(), source);

        assert_eq!(
            out,
            "const sp = `Use \\`code\\` and \\${vars} and a path C:\\\\tmp`;\n"
        );
    }

    #[test]
    fn leaves_non_text_attribute_types_untouched() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "data.json", "{}");

        let source = "import data from \"./data.json\" with { type: \"json\" };\n";
        let out = inline(dir.path(), source);

        assert_eq!(out, source);
    }

    #[test]
    fn leaves_plain_imports_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let source = "import { foo } from \"./foo\";\nimport bar from \"./bar\";\n";
        let out = inline(dir.path(), source);
        assert_eq!(out, source);
    }

    #[test]
    fn rejects_named_text_imports() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "instructions.md", "hi");

        let source =
            "import { systemPrompt } from \"./instructions.md\" with { type: \"text\" };\n";
        let err = inline_text_modules(source, "agent.ts", dir.path()).unwrap_err();
        assert!(err.to_string().contains("single default import"));
    }

    #[test]
    fn rejects_text_import_paths_with_lone_surrogates() {
        let dir = tempfile::tempdir().unwrap();
        let source = r#"import sp from "./\uD800.md" with { "type": "text" };"#;
        let error = inline_text_modules(source, "agent.ts", dir.path()).unwrap_err();
        assert!(error.to_string().contains("contains a lone surrogate"));
    }

    #[test]
    fn errors_when_referenced_file_is_missing() {
        let dir = tempfile::tempdir().unwrap();
        let source = "import sp from \"./missing.md\" with { type: \"text\" };\n";
        let err = inline_text_modules(source, "agent.ts", dir.path()).unwrap_err();
        assert!(err.to_string().contains("failed to inline text module"));
    }

    #[test]
    fn resolves_relative_paths_in_subdirectories() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("prompts")).unwrap();
        write(&dir.path().join("prompts"), "system.md", "nested prompt");

        let source = "import sp from \"./prompts/system.md\" with { type: \"text\" };\n";
        let out = inline(dir.path(), source);
        assert_eq!(out, "const sp = `nested prompt`;\n");
    }

    #[test]
    fn inlines_multiple_imports_and_preserves_surrounding_code() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "a.md", "AAA");
        write(dir.path(), "b.md", "BBB");

        let source = concat!(
            "import { defineDeepAgent } from \"managed-deepagents\";\n",
            "import a from \"./a.md\" with { type: \"text\" };\n",
            "import b from \"./b.md\" with { type: \"text\" };\n",
            "export const agent = defineDeepAgent({ a, b });\n",
        );
        let out = inline(dir.path(), source);

        assert_eq!(
            out,
            concat!(
                "import { defineDeepAgent } from \"managed-deepagents\";\n",
                "const a = `AAA`;\n",
                "const b = `BBB`;\n",
                "export const agent = defineDeepAgent({ a, b });\n",
            )
        );
    }

    #[test]
    fn handles_tsx_sources() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "prompt.md", "tsx prompt");

        let source = "import sp from \"./prompt.md\" with { type: \"text\" };\nconst el = <div>{sp}</div>;\n";
        let out = inline_text_modules(source, "view.tsx", dir.path()).unwrap();
        assert!(out.starts_with("const sp = `tsx prompt`;\n"));
    }

    #[test]
    fn supports_the_legacy_assert_keyword() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "instructions.md", "asserted");

        let source = "import sp from \"./instructions.md\" assert { type: \"text\" };\n";
        let out = inline(dir.path(), source);
        assert_eq!(out, "const sp = `asserted`;\n");
    }

    #[test]
    fn round_trips_path_components() {
        // Guards the PathBuf join behavior used for resolution.
        let joined = PathBuf::from("/tmp/project").join("./instructions.md");
        assert!(joined.ends_with("instructions.md"));
    }
}
