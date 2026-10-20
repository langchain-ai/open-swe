//! Minimal `.env` reading/writing for resolving and persisting the LangSmith
//! API key (and forwarding other vars as deployment secrets).

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

/// Parse a `.env` file into a map of `KEY=VALUE` pairs.
///
/// Uses [`dotenvy`] for standard dotenv semantics (quotes, `export`, full-line
/// and inline `#` comments, escapes, and `$VAR` substitution). Missing or
/// unreadable files yield an empty map; malformed lines are skipped.
pub(crate) fn parse_env_file(path: &Path) -> BTreeMap<String, String> {
    let Ok(iter) = dotenvy::from_path_iter(path) else {
        return BTreeMap::new();
    };
    iter.filter_map(Result::ok).collect()
}

/// Set (or replace) `key=value` in the `.env` file at `path`, creating it if
/// necessary and preserving other lines.
///
/// No-ops when the key is already present with the same value so watch-mode
/// restages do not bump mtime and wake LangGraph's `.env` watcher.
pub fn set_env_key(path: &Path, key: &str, value: &str) -> Result<()> {
    let existing = fs::read_to_string(path).unwrap_or_default();
    let current = parse_env_file(path);
    if current.get(key).map(String::as_str) == Some(value) {
        return Ok(());
    }

    let mut lines: Vec<String> = if existing.is_empty() {
        Vec::new()
    } else {
        existing.lines().map(str::to_string).collect()
    };

    let new_line = format!("{key}={value}");
    let matches_key = |line: &str| {
        let trimmed = line.trim_start();
        let trimmed = trimmed.strip_prefix("export ").unwrap_or(trimmed);
        trimmed
            .split_once('=')
            .map(|(k, _)| k.trim() == key)
            .unwrap_or(false)
    };

    if let Some(idx) = lines.iter().position(|l| matches_key(l)) {
        lines[idx] = new_line;
    } else {
        lines.push(new_line);
    }

    let mut out = lines.join("\n");
    out.push('\n');
    fs::write(path, out).with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

/// Project env file to update when recovering LangSmith credentials.
///
/// Prefers an existing `.env`, then `.env.local`, then creates `.env` — the same
/// order [`crate::dev`] uses when staging env for local runs.
pub fn project_env_persist_path(project_root: &Path) -> PathBuf {
    let env = project_root.join(".env");
    if env.is_file() {
        return env;
    }
    let local = project_root.join(".env.local");
    if local.is_file() {
        return local;
    }
    env
}

/// Remove `key` from the `.env` file at `path` when present.
///
/// No-ops when the key is absent so watch restages do not bump mtime.
pub fn remove_env_key(path: &Path, key: &str) -> Result<()> {
    let existing = fs::read_to_string(path).unwrap_or_default();
    if existing.is_empty() {
        return Ok(());
    }
    let matches_key = |line: &str| {
        let trimmed = line.trim_start();
        let trimmed = trimmed.strip_prefix("export ").unwrap_or(trimmed);
        trimmed
            .split_once('=')
            .map(|(k, _)| k.trim() == key)
            .unwrap_or(false)
    };
    let lines: Vec<&str> = existing.lines().collect();
    if !lines.iter().any(|line| matches_key(line)) {
        return Ok(());
    }
    let mut out = lines
        .into_iter()
        .filter(|line| !matches_key(line))
        .collect::<Vec<_>>()
        .join("\n");
    if !out.is_empty() {
        out.push('\n');
    }
    fs::write(path, out).with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_handles_comments_quotes_and_export() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        fs::write(
            &path,
            "# comment\nexport FOO=bar\nBAZ=\"quoted value\"\nEMPTY=\n",
        )
        .unwrap();

        let map = parse_env_file(&path);
        assert_eq!(map.get("FOO").unwrap(), "bar");
        assert_eq!(map.get("BAZ").unwrap(), "quoted value");
        assert_eq!(map.get("EMPTY").unwrap(), "");
    }

    #[test]
    fn parse_strips_inline_comments_from_unquoted_values() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        fs::write(
            &path,
            "TOKEN=abc123 # prod key\nHASHY=abc#123\nQUOTED=\"abc123 # keep\"\nQUOTED_TRAIL=\"abc123\" # drop\nSINGLE='x # y'\n",
        )
        .unwrap();

        let map = parse_env_file(&path);
        assert_eq!(map.get("TOKEN").unwrap(), "abc123");
        assert_eq!(map.get("HASHY").unwrap(), "abc#123");
        assert_eq!(map.get("QUOTED").unwrap(), "abc123 # keep");
        assert_eq!(map.get("QUOTED_TRAIL").unwrap(), "abc123");
        assert_eq!(map.get("SINGLE").unwrap(), "x # y");
    }

    #[test]
    fn parse_missing_file_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        assert!(parse_env_file(&dir.path().join(".env")).is_empty());
    }

    #[test]
    fn set_appends_then_replaces() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        fs::write(&path, "EXISTING=1\n").unwrap();

        set_env_key(&path, "LANGSMITH_API_KEY", "ls-abc").unwrap();
        let map = parse_env_file(&path);
        assert_eq!(map.get("EXISTING").unwrap(), "1");
        assert_eq!(map.get("LANGSMITH_API_KEY").unwrap(), "ls-abc");

        set_env_key(&path, "LANGSMITH_API_KEY", "ls-xyz").unwrap();
        let map = parse_env_file(&path);
        assert_eq!(map.get("LANGSMITH_API_KEY").unwrap(), "ls-xyz");
        // Ensure the key wasn't duplicated.
        let count = fs::read_to_string(&path)
            .unwrap()
            .lines()
            .filter(|l| l.starts_with("LANGSMITH_API_KEY="))
            .count();
        assert_eq!(count, 1);
    }

    #[test]
    fn set_env_key_noop_when_value_already_matches() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        fs::write(&path, "FOO=bar\nOTHER=1\n").unwrap();
        let before = fs::read(&path).unwrap();

        set_env_key(&path, "FOO", "bar").unwrap();

        assert_eq!(fs::read(&path).unwrap(), before);
    }

    #[test]
    fn remove_env_key_drops_matching_line_and_noops_when_absent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        fs::write(&path, "FOO=bar\nOTHER=1\n").unwrap();

        remove_env_key(&path, "FOO").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "OTHER=1\n");

        let before = fs::read(&path).unwrap();
        remove_env_key(&path, "FOO").unwrap();
        assert_eq!(fs::read(&path).unwrap(), before);
    }
}
