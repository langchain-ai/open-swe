//! Whether analytics reports at all, and whether this run is automated.

const TELEMETRY_DISABLED_ENV: &str = "MDA_TELEMETRY_DISABLED";
const DO_NOT_TRACK_ENV: &str = "DO_NOT_TRACK";

/// Checked in addition to `is_ci`, which matches `CI` only against `"true"` and
/// `"1"`, and which detects Azure Pipelines through
/// `SYSTEM_TEAMFOUNDATIONCOLLECTIONURI` rather than `TF_BUILD`.
const ADDITIONAL_CI_ENV_VARS: &[&str] = &["CI", "TF_BUILD"];

/// Read a boolean-style environment value.
///
/// Blank, `0`, and `false` mean off; every other value means on.
pub(super) fn is_truthy_env(value: &str) -> bool {
    !matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "" | "0" | "false"
    )
}

/// Whether the user has opted out. Analytics is on when neither switch is set.
pub fn is_telemetry_disabled(mut get_env: impl FnMut(&str) -> Option<String>) -> bool {
    [TELEMETRY_DISABLED_ENV, DO_NOT_TRACK_ENV]
        .iter()
        .any(|name| get_env(name).as_deref().is_some_and(is_truthy_env))
}

/// Whether this run is automated rather than a person at a keyboard.
pub(super) fn is_ci_environment(get_env: impl Fn(&str) -> Option<String>) -> bool {
    is_ci::cached() || additional_ci_marker_set(get_env)
}

fn additional_ci_marker_set(get_env: impl Fn(&str) -> Option<String>) -> bool {
    ADDITIONAL_CI_ENV_VARS
        .iter()
        .any(|name| get_env(name).as_deref().is_some_and(is_truthy_env))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn only(name: &'static str, value: &'static str) -> impl Fn(&str) -> Option<String> {
        move |requested| (requested == name).then(|| value.to_string())
    }

    #[test]
    fn blank_zero_and_false_are_off() {
        for value in ["", "   ", "0", "false", "FALSE", " False "] {
            assert!(!is_truthy_env(value), "expected {value:?} to be off");
        }
    }

    #[test]
    fn every_other_value_is_on() {
        for value in ["1", "true", "yes", "maybe"] {
            assert!(is_truthy_env(value), "expected {value:?} to be on");
        }
    }

    #[test]
    fn analytics_is_on_when_neither_switch_is_set() {
        assert!(!is_telemetry_disabled(|_| None));
    }

    #[test]
    fn either_switch_alone_opts_out() {
        assert!(is_telemetry_disabled(only(TELEMETRY_DISABLED_ENV, "1")));
        assert!(is_telemetry_disabled(only(DO_NOT_TRACK_ENV, "1")));
    }

    // `is_ci_environment` itself is not tested: `is_ci::cached()` reads the real
    // process environment and memoizes on first call, so the result would depend
    // on whether the suite is running under CI and on test ordering.
    #[test]
    fn an_additional_marker_alone_reports_ci() {
        assert!(additional_ci_marker_set(only("TF_BUILD", "True")));
        assert!(additional_ci_marker_set(only("CI", "yes")));
    }

    #[test]
    fn no_additional_marker_reports_nothing() {
        assert!(!additional_ci_marker_set(|_| None));
        assert!(!additional_ci_marker_set(only("CI", "false")));
    }

    #[test]
    fn a_switch_set_to_a_falsey_value_does_not_opt_out() {
        for value in ["0", "false", ""] {
            assert!(
                !is_telemetry_disabled(only(TELEMETRY_DISABLED_ENV, value)),
                "expected {value:?} to leave analytics on"
            );
        }
    }
}
