//! The machine state directory and the permissions its contents are held at.
//!
//! Holds per-machine CLI state that is not part of any project: cached
//! LangSmith credentials (see [`crate::langsmith::login`]) and the anonymous
//! analytics installation id (see [`crate::telemetry`]). Callers own their own
//! file names and formats; this module only resolves where they live and keeps
//! them readable by their owner alone.

use std::path::{Path, PathBuf};

/// Overrides the directory holding cached CLI state.
pub(crate) const STATE_DIR_ENV: &str = "MDA_STATE_DIR";

/// Directory for cached CLI state, following the platform convention.
pub(crate) fn state_dir(env: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let non_empty = |name: &str| env(name).filter(|value| !value.trim().is_empty());

    if let Some(override_dir) = non_empty(STATE_DIR_ENV) {
        return Some(PathBuf::from(override_dir));
    }
    if cfg!(windows) {
        return non_empty("LOCALAPPDATA").map(|dir| Path::new(&dir).join("mda"));
    }
    if let Some(xdg) = non_empty("XDG_STATE_HOME") {
        return Some(Path::new(&xdg).join("mda"));
    }
    non_empty("HOME").map(|home| Path::new(&home).join(".local/state/mda"))
}

/// [`state_dir`] resolved against the running process's environment.
pub(crate) fn state_dir_from_process() -> Option<PathBuf> {
    state_dir(|name| std::env::var(name).ok())
}

/// Create `path` and every missing parent, owner-only where supported.
///
/// `0700` rather than `0600`: on a directory the execute bit is what permits
/// traversing into it, so without it the directory cannot be used at all.
pub(crate) fn create_state_dir(path: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(path)?;
    restrict_dir(path)
}

#[cfg(unix)]
fn restrict_dir(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn restrict_dir(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

/// Keep a state file readable only by its owner: they hold bearer tokens and
/// the analytics installation id.
#[cfg(unix)]
pub(crate) fn restrict(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
pub(crate) fn restrict(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env_from<'a>(
        pairs: &'a [(&'static str, &'static str)],
    ) -> impl Fn(&str) -> Option<String> + 'a {
        move |name| {
            pairs
                .iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| (*value).to_string())
        }
    }

    #[test]
    fn an_explicit_state_directory_wins() {
        let dir = state_dir(env_from(&[
            (STATE_DIR_ENV, "/tmp/state"),
            ("XDG_STATE_HOME", "/ignored"),
            ("HOME", "/ignored"),
        ]));

        assert_eq!(dir, Some(PathBuf::from("/tmp/state")));
    }

    #[cfg(not(windows))]
    #[test]
    fn falls_back_through_xdg_to_home() {
        assert_eq!(
            state_dir(env_from(&[("XDG_STATE_HOME", "/home/dev/.state")])),
            Some(PathBuf::from("/home/dev/.state/mda"))
        );
        assert_eq!(
            state_dir(env_from(&[("HOME", "/home/dev")])),
            Some(PathBuf::from("/home/dev/.local/state/mda"))
        );
        // A blank value is as good as unset.
        assert_eq!(
            state_dir(env_from(&[("XDG_STATE_HOME", "  "), ("HOME", "/home/dev")])),
            Some(PathBuf::from("/home/dev/.local/state/mda"))
        );
    }

    #[test]
    fn without_any_environment_there_is_no_state_directory() {
        assert_eq!(state_dir(env_from(&[])), None);
    }

    #[cfg(unix)]
    #[test]
    fn a_created_directory_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let nested = root.path().join("mda/nested");

        create_state_dir(&nested).unwrap();

        let mode = std::fs::metadata(&nested).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o700);
    }

    #[cfg(unix)]
    #[test]
    fn a_restricted_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("secret");
        std::fs::write(&path, "value").unwrap();

        restrict(&path).unwrap();

        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600);
    }
}
