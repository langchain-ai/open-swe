use anyhow::{bail, Result};

/// Agent Platform IDs are capped at a DNS label so a hostname can carry them.
const MAX_AGENT_ID_LENGTH: usize = 63;

/// Normalize a name to lowercase ASCII words separated by single hyphens.
pub(crate) fn normalize_name(value: &str) -> Result<String> {
    let normalized = canonical_name(value);
    if normalized.is_empty() {
        bail!("name must contain at least one ASCII letter or digit");
    }
    if legacy_deployment_name(value) != normalized {
        bail!("name {value:?} is ambiguous after normalization; use {normalized:?} explicitly");
    }
    Ok(normalized)
}

/// Validate an Agent Platform agent ID: 1-63 lowercase ASCII letters, digits,
/// or hyphens, starting with a letter and ending with a letter or digit.
pub(crate) fn validate_agent_id(value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    let valid = bytes.len() <= MAX_AGENT_ID_LENGTH
        && matches!(bytes.first(), Some(b'a'..=b'z'))
        && matches!(bytes.last(), Some(b'a'..=b'z' | b'0'..=b'9'))
        && bytes
            .iter()
            .all(|byte| matches!(byte, b'a'..=b'z' | b'0'..=b'9' | b'-'));
    if !valid {
        bail!(
            "agent ID {value:?} must be 1-{MAX_AGENT_ID_LENGTH} lowercase letters, digits, or \
             hyphens, start with a letter, and end with a letter or digit"
        );
    }
    Ok(())
}

fn canonical_name(value: &str) -> String {
    let mut out = String::new();
    let mut previous_was_dash = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
            previous_was_dash = false;
        } else if !out.is_empty() && !previous_was_dash {
            out.push('-');
            previous_was_dash = true;
        }
    }
    if previous_was_dash {
        out.pop();
    }
    out
}

fn legacy_deployment_name(value: &str) -> String {
    let mut out = String::new();
    let mut previous_was_dash = false;
    for ch in value.to_lowercase().chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() {
            out.push(ch);
            previous_was_dash = false;
        } else if ch == '-' || !previous_was_dash {
            out.push('-');
            previous_was_dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_name_lowercases_and_replaces_separators() {
        assert_eq!(
            normalize_name("My_Super Bot.Agent").unwrap(),
            "my-super-bot-agent"
        );
    }

    #[test]
    fn normalize_name_collapses_and_trims_separators() {
        assert_eq!(normalize_name("__My_Agent__").unwrap(), "my-agent");
    }

    #[test]
    fn normalize_name_rejects_empty_output() {
        assert!(normalize_name("!!!").is_err());
    }

    #[test]
    fn validate_agent_id_accepts_platform_ids() {
        for id in ["a", "support-agent", "billing-v2", &"a".repeat(63)] {
            assert!(validate_agent_id(id).is_ok(), "{id}");
        }
    }

    #[test]
    fn validate_agent_id_rejects_ids_outside_platform_rules() {
        for id in [
            "",
            "2-support",
            "support-",
            "Support",
            "support_agent",
            &"a".repeat(64),
        ] {
            assert!(validate_agent_id(id).is_err(), "{id}");
        }
    }

    #[test]
    fn normalize_name_rejects_legacy_mapping_change() {
        let error = normalize_name("foo--bar").unwrap_err().to_string();

        assert!(error.contains("foo-bar"), "{error}");
    }
}
