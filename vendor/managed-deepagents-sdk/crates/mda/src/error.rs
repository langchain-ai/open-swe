use std::fmt::Display;
use std::process::Output;

use anyhow::Error;
use reqwest::StatusCode;
use serde::Serialize;

use crate::langsmith::ApiError;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    Project,
    Compilation,
    Authentication,
    Permission,
    Remote,
    Network,
    Toolchain,
    Deployment,
    UserInput,
    Unclassified,
}

pub fn categorized(category: ErrorCategory, message: impl Into<String>) -> Error {
    Error::new(Categorized {
        category,
        message: message.into(),
    })
}

/// `err` with `category`, leaving its message and causes as they were. Unlike
/// [`categorized`], the chain survives, so a cancellation or a child's stderr
/// below it is still found.
pub fn with_category(category: ErrorCategory, err: Error) -> Error {
    Error::new(Recategorized {
        category,
        inner: err,
    })
}

/// `err` with `category` unless it already has one, as a cancellation or a
/// LangSmith API status would.
pub fn categorize_unclassified(category: ErrorCategory, err: Error) -> Error {
    if is_cancellation(&err) || category_of(&err) != ErrorCategory::Unclassified {
        return err;
    }
    with_category(category, err)
}

/// The error for a child process that exited unsuccessfully: `message`, caused
/// by the child's stderr when it wrote any. The stderr is its own link in the
/// chain, so reporters can tell it apart from our messages.
pub fn child_failed(output: &Output, message: impl Into<String>) -> Error {
    stderr_failed(&output.stderr, message)
}

/// [`child_failed`] for a child whose `stderr` was collected some other way.
pub fn stderr_failed(stderr: &[u8], message: impl Into<String>) -> Error {
    let message = message.into();
    let stderr = String::from_utf8_lossy(stderr).trim().to_string();
    if stderr.is_empty() {
        return Error::msg(message);
    }
    Error::new(ChildStderr(stderr)).context(message)
}

/// What a failed child process wrote to stderr.
#[derive(Debug)]
pub struct ChildStderr(String);

impl Display for ChildStderr {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ChildStderr {}

pub fn is_cancellation(err: &Error) -> bool {
    err.chain().any(|cause| {
        cause
            .downcast_ref::<std::io::Error>()
            .is_some_and(|io| io.kind() == std::io::ErrorKind::Interrupted)
    })
}

pub fn category_of(err: &Error) -> ErrorCategory {
    err.chain()
        .find_map(|cause| {
            if let Some(recategorized) = cause.downcast_ref::<Recategorized>() {
                return Some(recategorized.category);
            }
            if let Some(named) = cause.downcast_ref::<Categorized>() {
                return Some(named.category);
            }
            if let Some(api) = cause.downcast_ref::<ApiError>() {
                return Some(api_category(api.status));
            }
            cause
                .downcast_ref::<reqwest::Error>()
                .and_then(transport_category)
        })
        .unwrap_or(ErrorCategory::Unclassified)
}

#[derive(Debug)]
struct Categorized {
    category: ErrorCategory,
    message: String,
}

impl std::fmt::Display for Categorized {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Categorized {}

/// Stands in for the error it wraps: the same message and the same causes.
#[derive(Debug)]
struct Recategorized {
    category: ErrorCategory,
    inner: Error,
}

impl std::fmt::Display for Recategorized {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The top message only, never `{:#}`: the causes come from `source`.
        write!(f, "{}", self.inner)
    }
}

impl std::error::Error for Recategorized {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.inner.source()
    }
}

fn api_category(status: StatusCode) -> ErrorCategory {
    match status {
        StatusCode::UNAUTHORIZED => ErrorCategory::Authentication,
        StatusCode::FORBIDDEN => ErrorCategory::Permission,
        _ => ErrorCategory::Remote,
    }
}

fn transport_category(err: &reqwest::Error) -> Option<ErrorCategory> {
    (err.is_timeout() || err.is_connect()).then_some(ErrorCategory::Network)
}

#[cfg(test)]
mod tests {
    use anyhow::anyhow;
    use serde_json::json;

    use super::*;

    fn api_error(status: StatusCode) -> Error {
        anyhow!(ApiError {
            status,
            detail: "LangSmith Deployment is not enabled for this organization".to_string(),
        })
    }

    #[test]
    fn every_category_serializes_to_its_documented_name() {
        let table = [
            (ErrorCategory::Project, "project"),
            (ErrorCategory::Compilation, "compilation"),
            (ErrorCategory::Authentication, "authentication"),
            (ErrorCategory::Permission, "permission"),
            (ErrorCategory::Remote, "remote"),
            (ErrorCategory::Network, "network"),
            (ErrorCategory::Toolchain, "toolchain"),
            (ErrorCategory::Deployment, "deployment"),
            (ErrorCategory::UserInput, "user_input"),
            (ErrorCategory::Unclassified, "unclassified"),
        ];

        for (category, name) in table {
            assert_eq!(serde_json::to_value(category).unwrap(), json!(name));
            match category {
                ErrorCategory::Project
                | ErrorCategory::Compilation
                | ErrorCategory::Authentication
                | ErrorCategory::Permission
                | ErrorCategory::Remote
                | ErrorCategory::Network
                | ErrorCategory::Toolchain
                | ErrorCategory::Deployment
                | ErrorCategory::UserInput
                | ErrorCategory::Unclassified => {}
            }
        }

        assert_eq!(table.len(), 10, "a new category needs a row here");
    }

    #[test]
    fn a_status_decides_the_category_and_the_detail_never_does() {
        let cases = [
            (StatusCode::UNAUTHORIZED, ErrorCategory::Authentication),
            (StatusCode::FORBIDDEN, ErrorCategory::Permission),
            (StatusCode::TOO_MANY_REQUESTS, ErrorCategory::Remote),
            (StatusCode::BAD_GATEWAY, ErrorCategory::Remote),
            (StatusCode::CONFLICT, ErrorCategory::Remote),
        ];

        for (status, expected) in cases {
            assert_eq!(category_of(&api_error(status)), expected, "{status}");
        }
    }

    #[test]
    fn a_wrapped_api_error_is_still_found() {
        let err = api_error(StatusCode::UNAUTHORIZED).context("failed to sync Context Hub");

        assert_eq!(category_of(&err), ErrorCategory::Authentication);
    }

    #[test]
    fn a_prompt_interrupt_is_a_cancellation() {
        let bare = Error::new(std::io::Error::from(std::io::ErrorKind::Interrupted));
        let wrapped = Error::new(std::io::Error::from(std::io::ErrorKind::Interrupted))
            .context("failed to read the LangSmith API key");

        assert!(is_cancellation(&bare));
        assert!(is_cancellation(&wrapped));
    }

    #[test]
    fn other_failures_are_not_cancellations() {
        let other_kind = Error::new(std::io::Error::from(std::io::ErrorKind::PermissionDenied));

        assert!(!is_cancellation(&other_kind));
        assert!(!is_cancellation(&api_error(StatusCode::UNAUTHORIZED)));
        assert!(!is_cancellation(&anyhow!("no agent entry file found")));
    }

    #[test]
    fn an_unrecognized_failure_is_unclassified() {
        let err = anyhow!("no agent entry file found in /private/project");

        assert_eq!(category_of(&err), ErrorCategory::Unclassified);
    }

    #[test]
    fn a_categorized_failure_renders_what_bail_would_have() {
        let err = categorized(
            ErrorCategory::Project,
            ". is not a Managed Deep Agent project",
        );

        assert_eq!(format!("{err:#}"), ". is not a Managed Deep Agent project");
        assert!(err.source().is_none());
    }

    fn failed_output(stderr: &str) -> Output {
        Output {
            status: failed_status(),
            stdout: Vec::new(),
            stderr: stderr.as_bytes().to_vec(),
        }
    }

    #[cfg(unix)]
    fn failed_status() -> std::process::ExitStatus {
        use std::os::unix::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(1 << 8)
    }

    #[cfg(windows)]
    fn failed_status() -> std::process::ExitStatus {
        use std::os::windows::process::ExitStatusExt;
        std::process::ExitStatus::from_raw(1)
    }

    #[test]
    fn a_failed_child_carries_its_stderr_in_the_chain() {
        let err = child_failed(&failed_output("Traceback\nImportError: x\n"), "emit failed");

        assert_eq!(format!("{err:#}"), "emit failed: Traceback\nImportError: x");
        assert_eq!(
            err.chain()
                .find_map(|cause| cause.downcast_ref::<ChildStderr>())
                .map(ToString::to_string)
                .as_deref(),
            Some("Traceback\nImportError: x")
        );
    }

    #[test]
    fn a_silent_failed_child_adds_no_stderr_link() {
        let err = child_failed(&failed_output("  \n"), "emit failed");

        assert_eq!(format!("{err:#}"), "emit failed");
        assert!(err.chain().all(|cause| !cause.is::<ChildStderr>()));
    }

    #[test]
    fn a_recategorized_error_keeps_its_text_and_its_causes() {
        let child = child_failed(&failed_output("ImportError: x\n"), "emit failed");
        let err = with_category(ErrorCategory::Compilation, child.context("compile failed"));

        assert_eq!(
            format!("{err:#}"),
            "compile failed: emit failed: ImportError: x"
        );
        assert_eq!(category_of(&err), ErrorCategory::Compilation);
        assert!(err.chain().any(|cause| cause.is::<ChildStderr>()));
    }

    #[test]
    fn only_an_unclassified_error_takes_a_new_category() {
        let unclassified = categorize_unclassified(ErrorCategory::Project, Error::msg("x"));
        let named = categorize_unclassified(
            ErrorCategory::Project,
            categorized(ErrorCategory::Authentication, "x"),
        );

        assert_eq!(category_of(&unclassified), ErrorCategory::Project);
        assert_eq!(category_of(&named), ErrorCategory::Authentication);
    }

    #[test]
    fn a_recategorized_cancellation_is_still_a_cancellation() {
        let interrupted = Error::new(std::io::Error::from(std::io::ErrorKind::Interrupted));

        assert!(is_cancellation(&with_category(
            ErrorCategory::Compilation,
            interrupted.context("failed to run npm")
        )));
    }

    #[test]
    fn context_above_a_categorized_failure_keeps_the_text_and_the_category() {
        let err = categorized(ErrorCategory::Deployment, "inner").context("outer");

        assert_eq!(format!("{err:#}"), "outer: inner");
        assert_eq!(category_of(&err), ErrorCategory::Deployment);
    }
}
