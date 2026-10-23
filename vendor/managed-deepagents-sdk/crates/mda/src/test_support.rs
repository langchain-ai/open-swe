//! Filesystem fixtures shared by unit tests across the crate.

use std::fs;
use std::path::Path;
use std::sync::Mutex;

/// Serializes tests that mutate process `LANGSMITH_API_KEY`.
pub(crate) static LANGSMITH_API_KEY_ENV_LOCK: Mutex<()> = Mutex::new(());

/// Write `contents` to `root/rel`, creating parent directories as needed.
pub(crate) fn write_file(root: &Path, rel: &str, contents: &str) {
    let path = root.join(rel);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(path, contents).unwrap();
}

/// Write a placeholder file at `root/rel`, for tests that care about project
/// layout rather than file contents.
pub(crate) fn write_stub_file(root: &Path, rel: &str) {
    write_file(root, rel, "// stub\n");
}
