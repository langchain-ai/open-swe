//! Shared `python3` subprocess runner for AST-based Python extraction.
//!
//! Individual extractors live beside this module as `.py` scripts loaded via
//! [`include_str!`], so the Rust call sites stay small and error handling is
//! consistent. Every script runs after [`PARSE_PRELUDE`], which parses the
//! piped source into `tree` and reports a `SyntaxError` back to Rust rather
//! than printing nothing — an empty result is indistinguishable from "this
//! option is not configured", which is how a broken file used to deploy with
//! defaults silently applied.
//!
//! A script's stdout is the extraction result and nothing else. Diagnostics
//! travel out of band ([`SYNTAX_ERROR_EXIT_CODE`] plus stderr) because scripts
//! print authored values verbatim and must not be able to spell a diagnostic.

use anyhow::{anyhow, Context, Error, Result};

use crate::child;
use crate::error::child_failed;
use crate::toolchain;

/// Prepended to every extractor script: binds the parsed source to `tree`.
const PARSE_PRELUDE: &str = include_str!("python_extract/prelude.py");

/// Exit code [`PARSE_PRELUDE`] uses to report a `SyntaxError`, with
/// `<line>:<column>:<message>` on stderr.
///
/// Signalling out of band keeps stdout purely the extraction result: scripts
/// print authored strings verbatim, so any in-band marker could be produced by
/// a real config (`model="ERROR:1:2:oops"`). 3 avoids python3's own codes — 1
/// for an uncaught exception, 2 for a usage error.
const SYNTAX_ERROR_EXIT_CODE: i32 = 3;

/// Python name [`PARSE_PRELUDE`] reads [`SYNTAX_ERROR_EXIT_CODE`] from, so the
/// code has one definition rather than one per language.
const SYNTAX_ERROR_EXIT_CODE_BINDING: &str = "_MDA_SYNTAX_ERROR_EXIT_CODE";

/// Run `script` over `source` and return its trimmed stdout, or `Ok(None)` when
/// the script printed nothing (the value it looks for is absent or dynamic).
///
/// `file_label` names the file `source` was read from and `purpose` describes
/// what is being read; both appear in error messages. An unusable interpreter,
/// a crashing script, and a syntax error in `source` are all hard errors:
/// these extractors decide deploy configuration, so guessing a default would
/// deploy something the author did not write.
pub(crate) fn run_python_extractor(
    script: &str,
    source: &str,
    file_label: &str,
    purpose: &str,
) -> Result<Option<String>> {
    let program = format!(
        "{SYNTAX_ERROR_EXIT_CODE_BINDING} = {SYNTAX_ERROR_EXIT_CODE}\n{PARSE_PRELUDE}\n{script}"
    );
    let python = toolchain::python3_program();
    let output = child::output_with_stdin(
        toolchain::python3().arg("-c").arg(program),
        source.as_bytes(),
    )
    .with_context(|| {
        format!(
            "failed to run {python} to read {purpose} from {file_label} (is Python 3 installed?)"
        )
    })?;
    let stderr = String::from_utf8_lossy(&output.stderr);
    if output.status.code() == Some(SYNTAX_ERROR_EXIT_CODE) {
        return Err(syntax_error(file_label, stderr.trim()));
    }
    if !output.status.success() {
        return Err(child_failed(
            &output,
            format!("{python} failed to read {purpose} from {file_label}"),
        ));
    }

    let text = String::from_utf8(output.stdout).with_context(|| {
        format!("reading {purpose} from {file_label} returned non-UTF-8 output")
    })?;
    let text = text.trim();
    Ok((!text.is_empty()).then(|| text.to_string()))
}

/// Render the `<line>:<column>:<message>` report [`PARSE_PRELUDE`] writes to
/// stderr the same way TypeScript parse errors are rendered:
/// `failed to parse <file>:<line>:<column>: <message>`.
fn syntax_error(file_label: &str, report: &str) -> Error {
    let mut parts = report.splitn(3, ':');
    match (parts.next(), parts.next(), parts.next()) {
        (Some(line), Some(column), Some(message)) => {
            anyhow!("failed to parse {file_label}:{line}:{column}: {message}")
        }
        _ => anyhow!("failed to parse {file_label}: {report}"),
    }
}

pub(crate) mod scripts {
    pub(crate) const MODEL: &str = include_str!("python_extract/model.py");
    /// Combined agent name / model / retired-memory-knob extraction (one subprocess).
    pub(crate) const AGENT: &str = include_str!("python_extract/agent.py");
    /// Combined identity auth + scope extraction (one subprocess).
    pub(crate) const IDENTITY_SUMMARY: &str = include_str!("python_extract/identity_summary.py");
    /// Root `memory` declaration scope extraction.
    pub(crate) const MEMORY_LAYERS: &str = include_str!("python_extract/memory_layers.py");
    pub(crate) const CHANNEL_INGRESS: &str = include_str!("python_extract/channel_ingress.py");
}

#[cfg(test)]
mod tests {
    use super::*;

    const AGENT: &str = "agent.py";

    fn model(source: &str) -> Result<Option<String>> {
        run_python_extractor(scripts::MODEL, source, AGENT, "the agent model")
    }

    #[test]
    fn returns_the_extracted_value() {
        let source = "agent = define_deep_agent(model=\"openai:gpt-5\")\n";
        assert_eq!(model(source).unwrap().as_deref(), Some("openai:gpt-5"));
    }

    #[test]
    fn returns_none_when_the_script_prints_nothing() {
        assert_eq!(model("agent = define_deep_agent()\n").unwrap(), None);
    }

    #[test]
    fn reports_a_syntax_error_with_its_position() {
        let error = model("agent = define_deep_agent(model=\n")
            .unwrap_err()
            .to_string();
        assert!(error.starts_with("failed to parse agent.py:1:"), "{error}");
    }

    #[test]
    fn an_extracted_value_cannot_impersonate_a_diagnostic() {
        let source = "agent = define_deep_agent(model=\"ERROR:1:2:not a diagnostic\")\n";
        assert_eq!(
            model(source).unwrap().as_deref(),
            Some("ERROR:1:2:not a diagnostic")
        );
    }

    #[test]
    fn reports_a_crashing_script() {
        let error =
            run_python_extractor("raise RuntimeError('boom')", "x = 1\n", AGENT, "the model")
                .unwrap_err()
                .to_string();
        let prefix = format!(
            "{} failed to read the model from agent.py",
            crate::toolchain::python3_program()
        );
        assert!(error.starts_with(&prefix), "{error}");
    }
}
