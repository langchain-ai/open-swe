//! GitHub values the CLI handles while setting up a GitHub channel.
//!
//! The CLI makes no GitHub API calls: Agent Auth creates the App, keeps its
//! private key, and records the installation. What remains here is input
//! validation for values the developer types or points at.

use std::fmt;

use anyhow::{bail, Result};

/// A GitHub App private key in PEM form. `Debug` never prints the key.
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct PrivateKeyPem(String);

impl PrivateKeyPem {
    /// Accept PEM text that looks like a private key.
    pub(crate) fn parse(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        let trimmed = value.trim();
        if !(trimmed.starts_with("-----BEGIN") && trimmed.contains("PRIVATE KEY-----")) {
            bail!("The file is not a PEM private key. Download a private key from the GitHub App's settings page.");
        }
        Ok(Self(format!("{trimmed}\n")))
    }

    pub(crate) fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for PrivateKeyPem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("PrivateKeyPem([redacted])")
    }
}

/// GitHub logins: 1–39 ASCII alphanumerics or single hyphens, not at either end.
pub(crate) fn validate_login(login: &str) -> Result<()> {
    let valid = !login.is_empty()
        && login.len() <= 39
        && !login.starts_with('-')
        && !login.ends_with('-')
        && !login.contains("--")
        && login.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
    if !valid {
        bail!("'{login}' is not a valid GitHub account name.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PEM: &str = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n";

    #[test]
    fn private_key_requires_pem() {
        assert!(PrivateKeyPem::parse(PEM).is_ok());
        assert!(
            PrivateKeyPem::parse("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----")
                .is_ok()
        );
        assert!(PrivateKeyPem::parse("not a key").is_err());
        assert_eq!(
            format!("{:?}", PrivateKeyPem::parse(PEM).unwrap()),
            "PrivateKeyPem([redacted])"
        );
    }

    #[test]
    fn login_validation() {
        for valid in ["acme", "a", "acme-inc", "A1"] {
            assert!(validate_login(valid).is_ok(), "{valid}");
        }
        for invalid in ["", "-acme", "acme-", "ac--me", "acme/inc", &"a".repeat(40)] {
            assert!(validate_login(invalid).is_err(), "{invalid}");
        }
    }
}
