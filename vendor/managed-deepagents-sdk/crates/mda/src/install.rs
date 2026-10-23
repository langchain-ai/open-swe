//! Installed-package contract shared by the npm and PyPI launchers.

use std::env;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};

use crate::project::{self, Language, Project};
use crate::version;

/// Environment variable set by package launchers to identify their registry.
pub const INSTALL_CHANNEL_ENV: &str = "MDA_INSTALL_CHANNEL";

/// Environment variable set by package launchers to locate their bundled runtime.
pub const BUNDLED_RUNTIME_PATH_ENV: &str = "MDA_BUNDLED_RUNTIME_PATH";

/// Registry package that launched the CLI.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InstallChannel {
    /// The TypeScript package published to npm.
    Npm,
    /// The Python package published to PyPI.
    PyPi,
}

impl InstallChannel {
    /// Authoring language supported by this install channel.
    pub fn language(self) -> Language {
        match self {
            Self::Npm => Language::TypeScript,
            Self::PyPi => Language::Python,
        }
    }

    fn parse(value: &str) -> Result<Self> {
        match value {
            "npm" => Ok(Self::Npm),
            "pypi" => Ok(Self::PyPi),
            _ => bail!(
                "invalid {INSTALL_CHANNEL_ENV} value {value:?} (expected \"npm\" or \"pypi\")"
            ),
        }
    }

    fn reinstall_hint(self) -> &'static str {
        match self {
            Self::Npm => "reinstall it with `npm install -g managed-deepagents`",
            Self::PyPi => "reinstall it with `pip install managed-deepagents`",
        }
    }

    /// Reject a project authored for the other install channel.
    pub fn ensure_language(self, actual: Language) -> Result<()> {
        if actual == self.language() {
            return Ok(());
        }

        match self {
            Self::Npm => bail!(
                "this mda CLI was installed from npm and supports TypeScript projects only; \
                 install the Python CLI with `pip install managed-deepagents`"
            ),
            Self::PyPi => bail!(
                "this mda CLI was installed from PyPI and supports Python projects only; \
                 install the TypeScript CLI with `npm install -g managed-deepagents`"
            ),
        }
    }
}

/// Validated package installation used by language-dependent commands.
#[derive(Debug)]
pub struct Installation {
    channel: InstallChannel,
    runtime_source: PathBuf,
}

impl Installation {
    /// Load the install channel and bundled runtime path supplied by the launcher.
    pub fn from_env() -> Result<Self> {
        let channel = env::var(INSTALL_CHANNEL_ENV)
            .with_context(|| {
                format!(
                    "{INSTALL_CHANNEL_ENV} is not set; run mda through the installed \
                     managed-deepagents npm or PyPI package"
                )
            })
            .and_then(|value| InstallChannel::parse(value.trim()))?;

        let runtime_source = env::var(BUNDLED_RUNTIME_PATH_ENV)
            .with_context(|| {
                format!(
                    "{BUNDLED_RUNTIME_PATH_ENV} is not set; {}",
                    channel.reinstall_hint()
                )
            })
            .map(PathBuf::from)?;

        validate_runtime_source(channel, &runtime_source)?;

        Ok(Self {
            channel,
            runtime_source,
        })
    }

    /// Authoring language supported by this installation.
    pub fn language(&self) -> Language {
        self.channel.language()
    }

    /// Launcher channel name persisted for child CLI invocations.
    pub(crate) fn channel_name(&self) -> &'static str {
        match self.channel {
            InstallChannel::Npm => "npm",
            InstallChannel::PyPi => "pypi",
        }
    }

    /// Absolute or launcher-resolved path to the bundled runtime package.
    pub fn runtime_source(&self) -> &Path {
        &self.runtime_source
    }

    /// Load a project and validate it against this package installation.
    pub fn load_project(&self, root: &Path) -> Result<Project> {
        let project = project::load(root)?;
        self.channel.ensure_language(project.language)?;
        project::validate_authoring_package_version(&project, &version::package_version())?;
        Ok(project)
    }
}

fn validate_runtime_source(channel: InstallChannel, source: &Path) -> Result<()> {
    let marker = match channel {
        InstallChannel::Npm => source.join("runtime/index.js"),
        InstallChannel::PyPi => source.join("runtime.py"),
    };
    if marker.is_file() {
        return Ok(());
    }

    bail!(
        "the managed-deepagents installation is incomplete: bundled runtime not found at {} \
         (expected {}); {}",
        source.display(),
        marker.display(),
        channel.reinstall_hint()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_accepts_npm_channel() {
        assert_eq!(InstallChannel::parse("npm").unwrap(), InstallChannel::Npm);
    }

    #[test]
    fn parse_accepts_pypi_channel() {
        assert_eq!(InstallChannel::parse("pypi").unwrap(), InstallChannel::PyPi);
    }

    #[test]
    fn parse_rejects_unknown_channel() {
        let err = InstallChannel::parse("pip").unwrap_err();
        assert!(err.to_string().contains("expected \"npm\" or \"pypi\""));
    }

    #[test]
    fn npm_rejects_python_projects_with_install_hint() {
        let err = InstallChannel::Npm
            .ensure_language(Language::Python)
            .unwrap_err();
        assert!(err.to_string().contains("pip install"));
    }

    #[test]
    fn pypi_rejects_typescript_projects_with_install_hint() {
        let err = InstallChannel::PyPi
            .ensure_language(Language::TypeScript)
            .unwrap_err();
        assert!(err.to_string().contains("npm install"));
    }
}
