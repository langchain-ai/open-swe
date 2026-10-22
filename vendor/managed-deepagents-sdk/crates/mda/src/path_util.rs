//! Small path helpers shared across compile and deploy.

use std::path::Path;

/// Join path components with forward slashes (POSIX-style), independent of the
/// host OS separator. Used for generated import paths, archive entries, and
/// Context Hub keys. Non-UTF8 components are lossily preserved (U+FFFD) so
/// path layout is not silently rewritten.
pub(crate) fn to_unix_path(path: &Path) -> String {
    let mut out = String::new();
    for component in path.components() {
        let piece = component.as_os_str().to_string_lossy();
        if !out.is_empty() {
            out.push('/');
        }
        out.push_str(&piece);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn to_unix_path_joins_components_with_forward_slashes() {
        assert_eq!(to_unix_path(&PathBuf::from("agent.ts")), "agent.ts");
        assert_eq!(
            to_unix_path(&PathBuf::from("src/agents/agent.ts")),
            "src/agents/agent.ts"
        );
    }

    #[cfg(unix)]
    #[test]
    fn to_unix_path_preserves_non_utf8_components() {
        use std::ffi::OsStr;
        use std::os::unix::ffi::OsStrExt;

        let path = PathBuf::from(OsStr::from_bytes(b"assets/\xff/file"));
        assert_eq!(to_unix_path(&path), "assets/\u{FFFD}/file");
    }
}
