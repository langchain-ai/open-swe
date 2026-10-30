//! Reading a secret in from the outside world, for any command that stores one.
//!
//! A value only travels one way. Nothing here reads a secret back, and there is
//! deliberately no flag that takes one inline, which would leave it in shell
//! history and `ps`. Error messages report sizes and counts, never content.

use std::collections::BTreeMap;
use std::io::{self, IsTerminal, Read};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use crate::error::{categorized, ErrorCategory};
use crate::langsmith::{lookup_env_with_origin, EnvOrigin};

/// Matches the server's cap. A file is checked against its metadata and stdin is
/// read through a limit, so an oversized value never reaches memory either way.
const MAX_VALUE_BYTES: u64 = 1 << 20;

pub enum ValueSource {
    FromEnv(String),
    FromFile(PathBuf),
    /// No flag: a masked prompt on a terminal, stdin otherwise.
    Unspecified,
}

pub(crate) fn value_source_is_explicit(source: &ValueSource) -> bool {
    matches!(source, ValueSource::FromEnv(_) | ValueSource::FromFile(_))
}

/// A resolved value and where it came from. `source` is a label for the
/// confirmation line; the value itself never reaches a message.
#[cfg_attr(test, derive(Debug))]
pub(crate) struct ResolvedValue {
    pub(crate) value: String,
    pub(crate) source: String,
}

/// `prompt` and `stdin_hint` are the two strings that name the caller's own
/// command, so every caller shares this without printing another command's
/// example.
pub(crate) fn resolve_value(
    source: &ValueSource,
    env_vars: &BTreeMap<String, String>,
    prompt: &str,
    stdin_hint: &str,
) -> Result<ResolvedValue> {
    match source {
        ValueSource::FromEnv(name) => {
            let (value, origin) =
                lookup_env_with_origin(name, env_vars, &mut |key| std::env::var(key).ok())
                    .ok_or_else(|| {
                        categorized(
                            ErrorCategory::UserInput,
                            format!("{name} is not set in the environment or the project .env"),
                        )
                    })?;
            let origin = match origin {
                EnvOrigin::Shell => "the shell",
                EnvOrigin::EnvFile => ".env",
            };
            Ok(ResolvedValue {
                value,
                source: format!("{name} in {origin}"),
            })
        }
        ValueSource::FromFile(path) if path == Path::new("-") => Err(categorized(
            ErrorCategory::UserInput,
            format!(
                "--secret-from-file takes a path. To read the value from stdin, redirect or pipe \
                 it instead: `{stdin_hint}`."
            ),
        )),
        ValueSource::FromFile(path) => Ok(ResolvedValue {
            value: value_from_file(path)?,
            source: path.display().to_string(),
        }),
        ValueSource::Unspecified if io::stdin().is_terminal() => {
            let value = cliclack::password(prompt)
                .mask('•')
                .interact()
                .context("failed to read the secret value")?;
            reject_empty(&value, "given")?;
            Ok(ResolvedValue {
                value,
                source: String::new(),
            })
        }
        ValueSource::Unspecified => Ok(ResolvedValue {
            value: value_from_reader(io::stdin().lock())?,
            source: "stdin".to_string(),
        }),
    }
}

/// Read a piped secret without treating a TTY or an empty pipe as a value.
///
/// `ValueSource::Unspecified` covers both “prompt on a terminal” and “read
/// stdin”. Call this only after ruling out OAuth flags and explicit value
/// sources, so a token on stdin is not discarded by another create path.
pub(crate) fn read_piped_stdin_secret() -> Result<Option<String>> {
    if io::stdin().is_terminal() {
        return Ok(None);
    }
    optional_secret_from_reader(io::stdin().lock())
}

pub(crate) fn value_from_file(path: &Path) -> Result<String> {
    let size = std::fs::metadata(path)
        .with_context(|| format!("failed to read {}", path.display()))?
        .len();
    if size > MAX_VALUE_BYTES {
        return Err(too_large(&format!("{}", path.display()), size));
    }
    let raw = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let value = strip_one_trailing_newline(raw);
    reject_empty(&value, &format!("in {}", path.display()))?;
    reject_dotenv_shaped(&value, &path.display().to_string())?;
    Ok(value)
}

fn value_from_reader(reader: impl Read) -> Result<String> {
    optional_secret_from_reader(reader)?
        .ok_or_else(|| categorized(ErrorCategory::UserInput, "no secret value on stdin"))
}

fn optional_secret_from_reader(reader: impl Read) -> Result<Option<String>> {
    let mut raw = String::new();
    reader
        .take(MAX_VALUE_BYTES + 1)
        .read_to_string(&mut raw)
        .context("failed to read the secret value from stdin")?;
    if raw.len() as u64 > MAX_VALUE_BYTES {
        return Err(too_large("the value on stdin", raw.len() as u64));
    }
    let value = strip_one_trailing_newline(raw);
    if value.trim().is_empty() {
        return Ok(None);
    }
    reject_dotenv_shaped(&value, "the input")?;
    Ok(Some(value))
}

fn too_large(what: &str, size: u64) -> anyhow::Error {
    categorized(
        ErrorCategory::UserInput,
        format!("{what} is over the {MAX_VALUE_BYTES} byte limit ({size} bytes or more)"),
    )
}

fn reject_empty(value: &str, what: &str) -> Result<()> {
    if value.trim().is_empty() {
        return Err(categorized(
            ErrorCategory::UserInput,
            format!("no secret value {what}"),
        ));
    }
    Ok(())
}

fn reject_dotenv_shaped(value: &str, what: &str) -> Result<()> {
    let assignments = value.lines().filter(|line| is_assignment(line)).count();
    if assignments < 2 {
        return Ok(());
    }
    Err(categorized(
        ErrorCategory::UserInput,
        format!(
            "{what} looks like a .env file: {assignments} lines assign a variable, and \
             all of it would be stored as one secret. Store one value per secret, \
             and use --secret-from-env <VAR> to read just one variable."
        ),
    ))
}

fn is_assignment(line: &str) -> bool {
    let line = line.trim_start();
    let line = line.strip_prefix("export ").unwrap_or(line).trim_start();
    let Some((name, _)) = line.split_once('=') else {
        return false;
    };
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn strip_one_trailing_newline(mut raw: String) -> String {
    if raw.ends_with('\n') {
        raw.pop();
        if raw.ends_with('\r') {
            raw.pop();
        }
    }
    raw
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stdin_reader_keeps_piped_and_redirected_secrets() {
        assert_eq!(
            value_from_reader(std::io::Cursor::new(b"sk_live_example\n")).unwrap(),
            "sk_live_example"
        );
        assert_eq!(
            value_from_reader(std::io::Cursor::new(b"sk_live_example\r\n")).unwrap(),
            "sk_live_example"
        );
        assert_eq!(
            value_from_reader(std::io::Cursor::new(b"line-one\nline-two")).unwrap(),
            "line-one\nline-two"
        );
    }

    #[test]
    fn optional_stdin_reader_treats_an_empty_pipe_as_absent() {
        assert_eq!(
            optional_secret_from_reader(std::io::Cursor::new(b"")).unwrap(),
            None
        );
        assert_eq!(
            optional_secret_from_reader(std::io::Cursor::new(b"\n")).unwrap(),
            None
        );
    }

    #[test]
    fn optional_stdin_reader_keeps_a_non_empty_pipe() {
        assert_eq!(
            optional_secret_from_reader(std::io::Cursor::new(b"sk_live_example\n")).unwrap(),
            Some("sk_live_example".to_string())
        );
    }

    #[test]
    fn stdin_reader_rejects_empty_and_dotenv_shaped_input() {
        let empty = value_from_reader(std::io::Cursor::new(b"\n"))
            .unwrap_err()
            .to_string();
        assert!(empty.contains("no secret value"), "{empty}");

        let dotenv = value_from_reader(std::io::Cursor::new(b"FOO=1\nBAR=2\n"))
            .unwrap_err()
            .to_string();
        assert!(dotenv.contains(".env"), "{dotenv}");
    }
}
