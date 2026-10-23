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

    #[test]
    fn context_above_a_categorized_failure_keeps_the_text_and_the_category() {
        let err = categorized(ErrorCategory::Deployment, "inner").context("outer");

        assert_eq!(format!("{err:#}"), "outer: inner");
        assert_eq!(category_of(&err), ErrorCategory::Deployment);
    }
}
