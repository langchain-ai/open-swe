//! Version reporting for the `mda` CLI.
//!
//! `mda` is never shipped on its own: every published artifact bundles the CLI
//! binary together with a matching `managed-deepagents` runtime (on npm and
//! PyPI). The version a user selects when they install from a registry is the
//! *runtime* (package) version, so that is what `mda --version` should report.
//! The internal Rust crate version is an implementation detail of the binary
//! and is not meaningful to users.
//!
//! The launcher that spawns the binary (the npm `bin/mda.mjs` shim or the PyPI
//! `mda` console-script entry point) knows its own package version and passes
//! it through the [`RUNTIME_VERSION_ENV`] environment variable. When that
//! variable is absent — e.g. the binary is run straight from a `cargo` checkout
//! with no bundled runtime — we fall back to the crate version.
//!
//! Build metadata (short git SHA + commit date) is baked in by `build.rs` and
//! appended so support can map a binary to an exact commit, e.g.
//! `mda 0.1.2 (a1b2c3d 2026-07-11)`.

/// Environment variable the runtime launcher uses to advertise its version.
pub const RUNTIME_VERSION_ENV: &str = "MDA_RUNTIME_VERSION";

/// Raw registry package version, without diagnostic build metadata.
///
/// Use this value in generated dependency manifests and compatibility checks.
pub fn package_version() -> String {
    resolve(std::env::var(RUNTIME_VERSION_ENV).ok().as_deref())
}

/// The human-readable version reported by `mda --version` / `-v`.
///
/// Prefers the bundled runtime version advertised by the launcher via
/// [`RUNTIME_VERSION_ENV`], falling back to the CLI crate version when it is
/// unset or blank (a bare binary with no bundled runtime). Appends baked git
/// metadata when available.
pub fn version() -> String {
    format_version(
        &package_version(),
        env!("MDA_GIT_SHA"),
        env!("MDA_GIT_DATE"),
    )
}

/// Resolve the reported package version from the runtime-version environment
/// value, isolated from the process environment so it can be tested directly.
fn resolve(runtime_version: Option<&str>) -> String {
    match runtime_version {
        Some(version) if !version.trim().is_empty() => version.to_string(),
        _ => env!("CARGO_PKG_VERSION").to_string(),
    }
}

/// Compose `{package}` or `{package} ({sha} {date})`.
///
/// Omits the parenthetical when both SHA and date are unknown so local builds
/// without git metadata stay clean.
fn format_version(package: &str, sha: &str, date: &str) -> String {
    if sha == "unknown" && date == "unknown" {
        package.to_string()
    } else {
        format!("{package} ({sha} {date})")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefers_runtime_version_when_set() {
        assert_eq!(resolve(Some("1.2.3")), "1.2.3");
    }

    #[test]
    fn falls_back_to_crate_version_when_unset() {
        assert_eq!(resolve(None), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn falls_back_to_crate_version_when_blank() {
        assert_eq!(resolve(Some("   ")), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn formats_package_with_git_metadata() {
        assert_eq!(
            format_version("0.1.2", "a1b2c3d", "2026-07-11"),
            "0.1.2 (a1b2c3d 2026-07-11)"
        );
    }

    #[test]
    fn omits_parenthetical_when_git_metadata_unknown() {
        assert_eq!(format_version("0.1.2", "unknown", "unknown"), "0.1.2");
    }

    #[test]
    fn keeps_partial_git_metadata() {
        assert_eq!(
            format_version("0.1.2", "a1b2c3d", "unknown"),
            "0.1.2 (a1b2c3d unknown)"
        );
    }
}
