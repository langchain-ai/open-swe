//! Who an analytics event is attributed to.
//!
//! A random UUID minted on first use and kept in the machine state directory.
//! It carries no relationship to the user, the machine, or any repository, and
//! its only job is to let repeat runs from one installation be counted as one
//! installation. Modelled on OpenWiki's `src/telemetry/install-id.ts`.

use std::env;
use std::fs::File;
use std::io::{ErrorKind, Read};
use std::path::{Path, PathBuf};

use uuid::Uuid;

use crate::state::{create_state_dir, restrict, state_dir_from_process};

use super::gates::{is_ci_environment, is_telemetry_disabled};

const INSTALL_ID_FILE: &str = "install-id";

/// Most a read will pull in, so a file that has grown or been replaced with
/// something else is never loaded whole. A canonical UUID is 36 bytes; the
/// slack covers the trailing newline and any stray whitespace.
const MAX_INSTALL_ID_BYTES: u64 = 64;

/// Stands in for an installation id on automated runs.
///
/// CI runners are usually ephemeral: each job gets a fresh home directory, so a
/// real id would be minted every run and every job would count as a separate
/// installation. Collapsing them all to one value keeps them out of the
/// installation count; `invoker_detection_method` still marks them as CI.
const CI_SENTINEL_ID: &str = "ci";

/// The identity for this run, or `None` when nothing should be reported.
pub(super) fn resolve_identity() -> Option<String> {
    let dir = state_dir_from_process();
    let get_env = |name: &str| env::var(name).ok();
    let is_ci = is_ci_environment(get_env);

    resolve(get_env, is_ci, dir.as_deref())
}

fn resolve(
    get_env: impl Fn(&str) -> Option<String>,
    is_ci: bool,
    state_dir: Option<&Path>,
) -> Option<String> {
    if is_telemetry_disabled(&get_env) {
        return None;
    }
    if is_ci {
        return Some(CI_SENTINEL_ID.to_string());
    }

    get_or_create_install_id(state_dir?).ok()
}

/// Read the installation id from `dir`, minting and persisting one on first use.
fn get_or_create_install_id(dir: &Path) -> std::io::Result<String> {
    let path = install_id_path(dir);

    if let Some(existing) = read_install_id(&path)? {
        return Ok(existing);
    }

    create_state_dir(dir)?;
    claim_install_id(&path)
}

/// The stored id, or `None` when there is not a usable one to reuse.
///
/// Absent, blank, non-UTF-8, and anything that is not a UUID within the first
/// [`MAX_INSTALL_ID_BYTES`] all read as `None`, so a corrupt file is replaced
/// rather than reported as an identity.
fn read_install_id(path: &Path) -> std::io::Result<Option<String>> {
    let mut bytes = Vec::new();
    match File::open(path) {
        Ok(file) => file.take(MAX_INSTALL_ID_BYTES).read_to_end(&mut bytes)?,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };

    Ok(parse_install_id(&bytes))
}

fn parse_install_id(bytes: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(bytes).ok()?;
    Uuid::parse_str(text.trim()).ok().map(|id| id.to_string())
}

/// Mint an id and publish it, or adopt the one a concurrent first run published.
///
/// The id is written to a temporary file and linked into place, because
/// `hard_link` fails rather than overwrites when the target exists. Exactly one
/// racing process wins, and the file only ever becomes visible with its contents
/// already in it, so a reader can never catch it empty.
fn claim_install_id(path: &Path) -> std::io::Result<String> {
    let install_id = Uuid::new_v4().to_string();
    let temp = path.with_extension(format!("tmp-{install_id}"));

    // Fill and lock down the file before it is reachable at `path`, so it is
    // never briefly readable by anyone else either.
    std::fs::write(&temp, format!("{install_id}\n"))?;
    restrict(&temp)?;

    match std::fs::hard_link(&temp, path) {
        Ok(()) => {
            let _ = std::fs::remove_file(&temp);
            Ok(install_id)
        }
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            // Another first run published first, and its file is complete, so an
            // unusable value here means corrupt rather than half-written.
            match read_install_id(path)? {
                Some(existing) => {
                    let _ = std::fs::remove_file(&temp);
                    Ok(existing)
                }
                None => {
                    std::fs::rename(&temp, path)?;
                    Ok(install_id)
                }
            }
        }
        // Some filesystems have no hard links. Publishing by rename gives up
        // first-writer-wins, but still never exposes a partial file.
        Err(_) => {
            std::fs::rename(&temp, path)?;
            Ok(install_id)
        }
    }
}

fn install_id_path(dir: &Path) -> PathBuf {
    dir.join(INSTALL_ID_FILE)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn only(name: &'static str, value: &'static str) -> impl Fn(&str) -> Option<String> {
        move |requested| (requested == name).then(|| value.to_string())
    }

    fn stored(dir: &Path) -> String {
        std::fs::read_to_string(install_id_path(dir)).unwrap()
    }

    fn seed(contents: &[u8]) -> (tempfile::TempDir, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("mda");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(install_id_path(&dir), contents).unwrap();
        (root, dir)
    }

    #[test]
    fn opting_out_reports_nothing_and_mints_nothing() {
        let dir = tempfile::tempdir().unwrap();

        let identity = resolve(only("DO_NOT_TRACK", "1"), false, Some(dir.path()));

        assert!(identity.is_none());
        assert!(!install_id_path(dir.path()).exists());
    }

    #[test]
    fn ci_reports_the_sentinel_and_mints_nothing() {
        let dir = tempfile::tempdir().unwrap();

        let identity = resolve(|_| None, true, Some(dir.path())).unwrap();

        assert_eq!(identity, "ci");
        assert!(!install_id_path(dir.path()).exists());
    }

    #[test]
    fn a_human_run_reports_the_installation_id() {
        let dir = tempfile::tempdir().unwrap();

        let identity = resolve(|_| None, false, Some(dir.path())).unwrap();

        assert!(Uuid::parse_str(&identity).is_ok());
        assert!(install_id_path(dir.path()).exists());
    }

    #[test]
    fn without_a_state_directory_there_is_nothing_to_report_under() {
        assert!(resolve(|_| None, false, None).is_none());
    }

    #[test]
    fn a_first_run_mints_and_persists_an_id() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("mda");

        let install_id = get_or_create_install_id(&dir).unwrap();

        assert!(Uuid::parse_str(&install_id).is_ok());
        assert_eq!(stored(&dir), format!("{install_id}\n"));
    }

    #[test]
    fn a_later_run_reuses_the_stored_id() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("mda");

        let first = get_or_create_install_id(&dir).unwrap();
        let second = get_or_create_install_id(&dir).unwrap();

        assert_eq!(first, second);
    }

    /// The losing side of a concurrent first run: the file appears between the
    /// read and the claim, so the claim must adopt it rather than overwrite it.
    #[test]
    fn a_claim_that_loses_the_race_adopts_the_stored_id() {
        let winner = Uuid::new_v4().to_string();
        let (_root, dir) = seed(format!("{winner}\n").as_bytes());

        let claimed = claim_install_id(&install_id_path(&dir)).unwrap();

        assert_eq!(claimed, winner);
        assert_eq!(stored(&dir), format!("{winner}\n"));
    }

    /// The case the file format exists to survive: many first runs at once must
    /// converge on one id, and it must be the one left on disk.
    #[test]
    fn concurrent_first_runs_agree_on_one_id() {
        const RACERS: usize = 32;

        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("mda");
        std::fs::create_dir_all(&dir).unwrap();
        let barrier = std::sync::Barrier::new(RACERS);

        let ids: Vec<String> = std::thread::scope(|scope| {
            let handles: Vec<_> = (0..RACERS)
                .map(|_| {
                    scope.spawn(|| {
                        barrier.wait();
                        get_or_create_install_id(&dir).unwrap()
                    })
                })
                .collect();
            handles.into_iter().map(|h| h.join().unwrap()).collect()
        });

        let persisted = stored(&dir);
        assert_eq!(persisted, format!("{}\n", ids[0]));
        assert!(
            ids.iter().all(|id| *id == ids[0]),
            "racers disagreed: {ids:?}"
        );
    }

    #[test]
    fn a_claim_over_a_corrupt_file_replaces_it() {
        let (_root, dir) = seed(b"not-a-uuid\n");

        let claimed = claim_install_id(&install_id_path(&dir)).unwrap();

        assert!(Uuid::parse_str(&claimed).is_ok());
        assert_eq!(stored(&dir), format!("{claimed}\n"));
    }

    #[test]
    fn a_claim_leaves_no_temporary_file_behind() {
        let root = tempfile::tempdir().unwrap();

        claim_install_id(&install_id_path(root.path())).unwrap();

        let leftovers: Vec<_> = std::fs::read_dir(root.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .filter(|name| name.to_string_lossy().contains("tmp-"))
            .collect();
        assert!(leftovers.is_empty(), "left behind {leftovers:?}");
    }

    #[test]
    fn an_empty_file_is_replaced_rather_than_returned() {
        let (_root, dir) = seed(b"  \n");

        let install_id = get_or_create_install_id(&dir).unwrap();

        assert!(Uuid::parse_str(&install_id).is_ok());
        assert_eq!(stored(&dir), format!("{install_id}\n"));
    }

    #[test]
    fn a_value_that_is_not_a_uuid_is_replaced() {
        let (_root, dir) = seed(b"not-a-uuid\n");

        let install_id = get_or_create_install_id(&dir).unwrap();

        assert!(Uuid::parse_str(&install_id).is_ok());
        assert_eq!(stored(&dir), format!("{install_id}\n"));
    }

    /// Only the first [`MAX_INSTALL_ID_BYTES`] are read, so an id sitting past
    /// that bound is never seen and the file is replaced.
    #[test]
    fn a_value_past_the_read_bound_is_replaced() {
        let buried = format!("{}{}\n", " ".repeat(128), Uuid::new_v4());
        let (_root, dir) = seed(buried.as_bytes());

        let install_id = get_or_create_install_id(&dir).unwrap();

        assert!(Uuid::parse_str(&install_id).is_ok());
        assert_eq!(stored(&dir), format!("{install_id}\n"));
    }

    #[test]
    fn a_stored_id_padded_with_whitespace_is_still_reused() {
        let existing = Uuid::new_v4().to_string();
        let (_root, dir) = seed(format!("  {existing}  \n").as_bytes());

        assert_eq!(get_or_create_install_id(&dir).unwrap(), existing);
    }

    #[test]
    fn non_utf8_contents_are_replaced() {
        let (_root, dir) = seed(&[0xff, 0xfe, 0xfd]);

        let install_id = get_or_create_install_id(&dir).unwrap();

        assert!(Uuid::parse_str(&install_id).is_ok());
        assert_eq!(stored(&dir), format!("{install_id}\n"));
    }

    #[cfg(unix)]
    #[test]
    fn the_stored_id_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("mda");

        get_or_create_install_id(&dir).unwrap();

        let file = std::fs::metadata(install_id_path(&dir)).unwrap();
        let directory = std::fs::metadata(&dir).unwrap();
        assert_eq!(file.permissions().mode() & 0o777, 0o600);
        assert_eq!(directory.permissions().mode() & 0o777, 0o700);
    }

    #[cfg(unix)]
    #[test]
    fn an_unwritable_state_directory_is_an_error_rather_than_a_panic() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::tempdir().unwrap();
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o500)).unwrap();

        let result = get_or_create_install_id(&root.path().join("mda"));

        // Restore write access so the temporary directory can be cleaned up.
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        assert!(result.is_err());
    }
}
