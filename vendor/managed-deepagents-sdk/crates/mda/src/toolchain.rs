//! Resolve host toolchain binaries for subprocesses.
//!
//! Windows `CreateProcess` does not honor `PATHEXT` the way shells do: a bare
//! `npm` looks for `npm.exe` only, while Node ships `npm.cmd`. The same applies
//! to `npx` and npm `.bin` shims (`tsx.cmd`). Python 3 is usually installed as
//! `python.exe` with no `python3.exe` (and a Windows Store `python3` alias may
//! exist but fail). This module picks working program names so compile/dev
//! spawn sites stay OS-agnostic.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

use anyhow::{anyhow, bail, Context, Result};

/// `Command` that runs the host `npm` (or `npm.cmd` on Windows).
pub(crate) fn npm() -> Command {
    Command::new(npm_program())
}

/// `Command` that runs the host `npx` (or `npx.cmd` on Windows).
pub(crate) fn npx() -> Command {
    Command::new(npx_program())
}

/// `Command` that runs the host `node` binary.
pub(crate) fn node() -> Command {
    Command::new(node_program())
}

/// `Command` that runs the host `uv`.
pub(crate) fn uv() -> Command {
    Command::new("uv")
}

/// Whether a working `npm` is on PATH.
#[cfg(test)]
pub(crate) fn npm_available() -> bool {
    probe_version(npm_program())
}

/// Whether a working `uv` is on PATH.
pub(crate) fn uv_available() -> bool {
    probe_version("uv")
}

/// Whether `python -m pip` works for the given interpreter program/path.
///
/// `uv`-created environments often omit pip; prefer probing the module over
/// assuming a host `pip` binary exists.
pub(crate) fn python_pip_available_for(python: impl AsRef<OsStr>) -> bool {
    Command::new(python)
        .args(["-m", "pip", "--version"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

/// Python interpreter for subprocesses that import project or runtime packages.
///
/// Prefers `<root>/.venv`, then `$VIRTUAL_ENV`. A candidate must actually run
/// (`--version`); a stale `.venv` file does not block a working active env.
/// Does not fall back to `python3` on PATH: a global install can shadow a
/// valid project environment (see langchain-ai/managed-deepagents-sdk#312).
pub(crate) fn project_python(root: &Path) -> Result<PathBuf> {
    let virtual_env = std::env::var_os("VIRTUAL_ENV");
    resolve_project_python(root, virtual_env.as_deref().map(Path::new))
}

/// Interpreter for connector/channel emit.
///
/// Uses the project environment when it exists. Otherwise creates an isolated
/// venv next to the build directory (not inside it) so deploy never archives
/// a local interpreter.
pub(crate) fn python_for_emit(project_root: &Path, out_dir: &Path) -> Result<PathBuf> {
    if let Ok(python) = project_python(project_root) {
        return Ok(python);
    }
    isolated_emit_python(out_dir)
}

pub(crate) const PYTHON_EMIT_VENV_DIR: &str = ".mda-emit-venv";

/// Isolated emit venv path: sibling of `out_dir`, falling back to `out_dir`
/// only when the build has no parent.
pub(crate) fn emit_venv_dir(out_dir: &Path) -> PathBuf {
    out_dir
        .parent()
        .unwrap_or(out_dir)
        .join(PYTHON_EMIT_VENV_DIR)
}

fn isolated_emit_python(out_dir: &Path) -> Result<PathBuf> {
    let venv = emit_venv_dir(out_dir);
    if let Some(python) = venv_interpreter(&venv) {
        return Ok(python);
    }
    create_isolated_venv(&venv)?;
    venv_interpreter(&venv).ok_or_else(|| {
        anyhow!(
            "created isolated emit venv at {} but found no usable interpreter",
            venv.display()
        )
    })
}

fn create_isolated_venv(venv: &Path) -> Result<()> {
    if venv.exists() {
        std::fs::remove_dir_all(venv)
            .with_context(|| format!("failed to replace {}", venv.display()))?;
    }
    if let Some(parent) = venv.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }

    if uv_available() {
        let output = uv()
            .args(["venv", "--python", python3_program()])
            .arg(venv)
            .output()
            .context("failed to run uv venv for connector emit")?;
        if output.status.success() {
            return Ok(());
        }
    }

    let output = python3()
        .args(["-m", "venv"])
        .arg(venv)
        .output()
        .with_context(|| {
            format!(
                "failed to create isolated emit venv at {}\n\
                 Run `uv sync` so `.venv` exists, or invoke `mda` via `uv run`.",
                venv.display()
            )
        })?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!(
            "failed to create isolated emit venv at {}\n{stderr}\n\
             Run `uv sync` so `.venv` exists, or invoke `mda` via `uv run`.",
            venv.display()
        );
    }
    Ok(())
}

fn resolve_project_python(root: &Path, virtual_env: Option<&Path>) -> Result<PathBuf> {
    if let Some(python) = venv_interpreter(&root.join(".venv")) {
        return Ok(python);
    }
    if let Some(python) = virtual_env.and_then(venv_interpreter) {
        return Ok(python);
    }
    bail!(
        "no usable Python virtualenv for connector discovery\n\
         Looked for {} and $VIRTUAL_ENV.\n\
         Run `uv sync` so `.venv` exists, or invoke `mda` via `uv run`.",
        root.join(".venv").display()
    )
}

fn venv_interpreter(venv: &Path) -> Option<PathBuf> {
    venv_interpreter_candidates(venv)
        .into_iter()
        .find(|candidate| candidate.is_file() && probe_version(candidate))
}

fn venv_interpreter_candidates(venv: &Path) -> Vec<PathBuf> {
    if cfg!(windows) {
        vec![
            venv.join("Scripts").join("python.exe"),
            venv.join("Scripts").join("python3.exe"),
        ]
    } else {
        vec![
            venv.join("bin").join("python"),
            venv.join("bin").join("python3"),
        ]
    }
}

#[cfg(test)]
pub(crate) fn write_usable_venv_interpreter(venv: &Path) -> PathBuf {
    let python = venv_interpreter_candidates(venv)
        .into_iter()
        .next()
        .expect("venv interpreter candidate");
    std::fs::create_dir_all(python.parent().expect("interpreter parent")).unwrap();
    write_usable_python_stub(&python);
    python
}

#[cfg(test)]
fn write_usable_python_stub(python: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(python, "#!/bin/sh\necho Python 3.11.0\n").unwrap();
        let mut perms = std::fs::metadata(python).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(python, perms).unwrap();
    }
    #[cfg(windows)]
    {
        let output = Command::new(python3_program())
            .args(["-c", "import sys; print(sys.executable)"])
            .output()
            .expect("host python");
        assert!(
            output.status.success(),
            "host python executable lookup failed"
        );
        let host = String::from_utf8(output.stdout).unwrap();
        std::fs::copy(host.trim(), python).expect("copy host python into test venv");
    }
}

/// Program name used for `npm` spawns (also useful in error messages).
pub(crate) fn npm_program() -> &'static str {
    static PROGRAM: OnceLock<&'static str> = OnceLock::new();
    PROGRAM.get_or_init(|| first_working(npm_candidates()))
}

/// Program name used for `npx` spawns (also useful in error messages).
pub(crate) fn npx_program() -> &'static str {
    static PROGRAM: OnceLock<&'static str> = OnceLock::new();
    PROGRAM.get_or_init(|| first_working(npx_candidates()))
}

/// Program name used for `node` spawns (also useful in error messages).
pub(crate) fn node_program() -> &'static str {
    static PROGRAM: OnceLock<&'static str> = OnceLock::new();
    PROGRAM.get_or_init(|| first_working(node_candidates()))
}

/// `Command` that runs Python 3 (`python3`, or `python` on Windows when needed).
pub(crate) fn python3() -> Command {
    Command::new(python3_program())
}

/// Whether a working Python 3 interpreter is on PATH.
#[cfg(test)]
pub(crate) fn python3_available() -> bool {
    probe_version(python3_program())
}

/// Resolved Python 3 program name (also useful in error messages).
pub(crate) fn python3_program() -> &'static str {
    static PROGRAM: OnceLock<&'static str> = OnceLock::new();
    PROGRAM.get_or_init(|| first_working(python3_candidates()))
}

/// Resolve an npm-installed `.bin` shim, preferring the Windows `.cmd` form.
pub(crate) fn npm_bin(bin_dir: &Path, name: &str) -> Option<PathBuf> {
    npm_bin_candidates(bin_dir, name)
        .into_iter()
        .find(|candidate| candidate.is_file())
}

/// First candidate whose `--version` succeeds, else the first candidate.
fn first_working(candidates: &'static [&'static str]) -> &'static str {
    candidates
        .iter()
        .copied()
        .find(|candidate| probe_version(candidate))
        .unwrap_or(candidates[0])
}

fn node_candidates() -> &'static [&'static str] {
    if cfg!(windows) {
        &["node.exe", "node"]
    } else {
        &["node"]
    }
}

fn npm_candidates() -> &'static [&'static str] {
    if cfg!(windows) {
        // Node's Windows install ships `npm.cmd`. Custom shims may be
        // `npm.exe` (resolved via the bare `npm` name).
        &["npm.cmd", "npm"]
    } else {
        &["npm"]
    }
}

fn npx_candidates() -> &'static [&'static str] {
    if cfg!(windows) {
        &["npx.cmd", "npx"]
    } else {
        &["npx"]
    }
}

fn npm_bin_candidates(bin_dir: &Path, name: &str) -> Vec<PathBuf> {
    if cfg!(windows) {
        vec![
            bin_dir.join(format!("{name}.cmd")),
            bin_dir.join(format!("{name}.exe")),
            bin_dir.join(name),
        ]
    } else {
        vec![bin_dir.join(name)]
    }
}

fn python3_candidates() -> &'static [&'static str] {
    if cfg!(windows) {
        &["python3", "python"]
    } else {
        &["python3"]
    }
}

fn probe_version(program: impl AsRef<OsStr>) -> bool {
    Command::new(program)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn npm_candidates_prefer_cmd_on_windows() {
        let candidates = npm_candidates();
        if cfg!(windows) {
            assert_eq!(candidates[0], "npm.cmd");
            assert!(candidates.contains(&"npm"));
        } else {
            assert_eq!(candidates, &["npm"]);
        }
    }

    #[test]
    fn npx_candidates_prefer_cmd_on_windows() {
        let candidates = npx_candidates();
        if cfg!(windows) {
            assert_eq!(candidates[0], "npx.cmd");
        } else {
            assert_eq!(candidates, &["npx"]);
        }
    }

    #[test]
    fn python3_candidates_include_python_on_windows() {
        let candidates = python3_candidates();
        assert_eq!(candidates[0], "python3");
        if cfg!(windows) {
            assert!(candidates.contains(&"python"));
        } else {
            assert_eq!(candidates, &["python3"]);
        }
    }

    #[test]
    fn npm_bin_prefers_cmd_on_windows() {
        let bin_dir = Path::new("node_modules/.bin");
        let candidates = npm_bin_candidates(bin_dir, "tsx");
        if cfg!(windows) {
            assert_eq!(candidates[0], bin_dir.join("tsx.cmd"));
        } else {
            assert_eq!(candidates, vec![bin_dir.join("tsx")]);
        }
    }

    #[test]
    fn resolved_programs_are_cached_candidates() {
        assert!(npm_candidates().contains(&npm_program()));
        assert!(npx_candidates().contains(&npx_program()));
        assert!(python3_candidates().contains(&python3_program()));
    }

    fn write_venv_python(venv: &Path, usable: bool) -> PathBuf {
        if usable {
            return write_usable_venv_interpreter(venv);
        }
        let python = venv_interpreter_candidates(venv)
            .into_iter()
            .next()
            .expect("venv interpreter candidate");
        fs::create_dir_all(python.parent().expect("interpreter parent")).unwrap();
        fs::write(&python, "").unwrap();
        python
    }

    #[test]
    fn project_python_prefers_dot_venv_over_virtual_env() {
        let root = tempfile::tempdir().unwrap();
        let expected = write_venv_python(&root.path().join(".venv"), true);
        let other = write_venv_python(&root.path().join("other-env"), true);
        let resolved = resolve_project_python(root.path(), Some(&root.path().join("other-env")))
            .expect("project .venv");
        assert_eq!(resolved, expected);
        assert_ne!(resolved, other);
    }

    #[test]
    fn project_python_uses_virtual_env_when_dot_venv_is_missing() {
        let root = tempfile::tempdir().unwrap();
        let env = root.path().join("custom-env");
        let expected = write_venv_python(&env, true);
        let resolved = resolve_project_python(root.path(), Some(&env)).expect("VIRTUAL_ENV");
        assert_eq!(resolved, expected);
    }

    #[test]
    fn project_python_skips_unusable_dot_venv_for_virtual_env() {
        let root = tempfile::tempdir().unwrap();
        write_venv_python(&root.path().join(".venv"), false);
        let env = root.path().join("active-env");
        let expected = write_venv_python(&env, true);
        let resolved = resolve_project_python(root.path(), Some(&env))
            .expect("working VIRTUAL_ENV after stale .venv");
        assert_eq!(resolved, expected);
    }

    #[test]
    fn project_python_does_not_fall_back_to_path_python() {
        let root = tempfile::tempdir().unwrap();
        write_venv_python(&root.path().join(".venv"), false);
        let err = resolve_project_python(root.path(), None).unwrap_err();
        let message = err.to_string();
        assert!(
            message.contains("no usable Python virtualenv"),
            "unexpected error: {message}"
        );
        assert!(
            message.contains("uv sync"),
            "expected uv sync guidance, got: {message}"
        );
        assert!(
            !message.contains(python3_program()),
            "must not silently fall back to PATH python, got: {message}"
        );
    }

    #[test]
    fn python_for_emit_prefers_project_venv() {
        let root = tempfile::tempdir().unwrap();
        let expected = write_venv_python(&root.path().join(".venv"), true);
        let out_dir = root.path().join("build");
        fs::create_dir_all(&out_dir).unwrap();
        let resolved = python_for_emit(root.path(), &out_dir).expect("project venv");
        assert_eq!(resolved, expected);
        assert!(!emit_venv_dir(&out_dir).exists());
        assert!(!out_dir.join(PYTHON_EMIT_VENV_DIR).exists());
    }

    #[test]
    #[expect(clippy::print_stderr, reason = "optional local tool skip reason")]
    fn python_for_emit_creates_isolated_venv_when_project_env_is_missing() {
        if !uv_available() && !python3_available() {
            eprintln!("skipping: no uv or python3 to create an emit venv");
            return;
        }
        if std::env::var_os("VIRTUAL_ENV").is_some() {
            // project_python would reuse the process env instead of creating one.
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let out_dir = root.path().join("build");
        fs::create_dir_all(&out_dir).unwrap();
        let python = python_for_emit(root.path(), &out_dir).expect("isolated emit venv");
        let venv = emit_venv_dir(&out_dir);
        assert!(
            python.starts_with(&venv),
            "expected interpreter under isolated emit venv {}, got {}",
            venv.display(),
            python.display()
        );
        assert!(
            !python.starts_with(&out_dir),
            "emit venv must live outside the build directory, got {}",
            python.display()
        );
        assert!(probe_version(&python));
        let python_again = python_for_emit(root.path(), &out_dir).expect("reuse emit venv");
        assert_eq!(python, python_again);
    }
}
