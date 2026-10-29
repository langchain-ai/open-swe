//! Preflight check that LangSmith accepts the resolved `LANGSMITH_API_KEY`.
//!
//! LangSmith answers a bad key with a bare 403 on most endpoints, which is
//! indistinguishable from a real entitlement or role refusal. Without this
//! check an expired key, or one from another region, surfaces as whatever the
//! command asked for first — "sandbox access is unavailable for this
//! organization", say — and is counted as a `permission` failure. Asking the
//! identity endpoint first lets those 403s mean what they say.

use std::path::{Path, PathBuf};

use anyhow::{Error, Result};
use reqwest::StatusCode;

use crate::error::{categorized, ErrorCategory};
use crate::langsmith::{self, ApiError, ApiKey, KeyCheck, Login, API_KEY_ENV};

/// Where to make a new key. Named rather than linked: the dashboard origin
/// comes from an authenticated lookup, which a refused key cannot make.
const NEW_KEY_PAGE: &str = "Settings → API Keys in LangSmith";

/// Where the `LANGSMITH_API_KEY` a command uses came from.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum ApiKeySource {
    /// The project's env file, including a key just pasted at the prompt.
    EnvFile(PathBuf),
    /// The process environment, such as a shell export.
    ProcessEnv,
    /// A short-lived key minted by browser sign-in and cached on this machine.
    SignIn,
}

impl ApiKeySource {
    /// The source of a key found in the shell or the project env file. The
    /// shell wins when both are set (see `lookup_env_with_origin`).
    pub(crate) fn of_env_key(in_shell: bool, env_path: &Path) -> Self {
        if in_shell {
            Self::ProcessEnv
        } else {
            Self::EnvFile(env_path.to_path_buf())
        }
    }

    /// The source of the key `mda dev` staged for its server: a CLI sign-in
    /// key, else the project env file when it holds the same key, else the
    /// process environment it was mirrored from.
    pub(crate) fn of_staged_key(
        key: &str,
        is_session_key: bool,
        project_key: Option<&str>,
        project_env: &Path,
    ) -> Self {
        if is_session_key {
            Self::SignIn
        } else if project_key == Some(key) {
            Self::EnvFile(project_env.to_path_buf())
        } else {
            Self::ProcessEnv
        }
    }

    fn describe(&self) -> String {
        match self {
            Self::EnvFile(path) => format!("{API_KEY_ENV} in {}", path.display()),
            Self::ProcessEnv => format!("{API_KEY_ENV} from the environment"),
            Self::SignIn => "the key from your cached browser sign-in".to_string(),
        }
    }

    fn remedy(&self) -> String {
        match self {
            Self::EnvFile(_) => format!(
                "Replace it with a new key from {NEW_KEY_PAGE}, or delete that line and rerun \
                 this command in a terminal to sign in with your browser."
            ),
            Self::ProcessEnv => format!(
                "Export a new key from {NEW_KEY_PAGE}, or unset {API_KEY_ENV} and rerun this \
                 command in a terminal to sign in with your browser."
            ),
            Self::SignIn => "The cached sign-in was cleared. Rerun this command to sign in \
                             with your browser again."
                .to_string(),
        }
    }
}

/// A resolved LangSmith API key and where it came from.
#[derive(Debug, Clone)]
pub(crate) struct ResolvedApiKey {
    pub api_key: ApiKey,
    pub source: ApiKeySource,
}

/// Fail with an [`ErrorCategory::Authentication`] error when LangSmith refuses
/// `client`'s key, naming where the key came from and how to replace it.
///
/// Returns the check's outcome otherwise: [`KeyCheck::Accepted`] means a later
/// 403 really is about permissions, while [`KeyCheck::Unverified`] means the
/// key could still be the cause. A refused cached sign-in is dropped from the
/// cache so the next run signs in again.
pub(crate) fn verify_api_key(
    client: &langsmith::Client,
    source: &ApiKeySource,
    workspace_id: Option<&str>,
) -> Result<KeyCheck> {
    let check = client.check_api_key();
    let KeyCheck::Rejected(status) = check else {
        return Ok(check);
    };
    let platform_url = client.platform_base();
    if *source == ApiKeySource::SignIn {
        Login::for_api_url(platform_url).forget_cached_api_key();
    }
    Err(categorized(
        ErrorCategory::Authentication,
        rejected_key_message(source, status.as_u16(), platform_url, workspace_id),
    ))
}

/// Whether `err` is LangSmith refusing the request (HTTP 401 or 403), rather
/// than failing to answer it.
pub(crate) fn is_refusal(err: &Error) -> bool {
    err.chain()
        .filter_map(|cause| cause.downcast_ref::<ApiError>())
        .any(|api| matches!(api.status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN))
}

fn rejected_key_message(
    source: &ApiKeySource,
    status: u16,
    platform_url: &str,
    workspace_id: Option<&str>,
) -> String {
    let mut message = format!(
        "LangSmith rejected {source} (HTTP {status} from {platform_url}).\n\
         The key is invalid, expired, or was created for another LangSmith region or \
         installation. If it belongs to another region, set LANGSMITH_ENDPOINT to that \
         region's API, for example https://eu.api.smith.langchain.com.",
        source = source.describe(),
    );
    if let Some(workspace_id) = workspace_id {
        message.push_str(&format!(
            "\nIt may also be a key that cannot access workspace {workspace_id} \
             (LANGSMITH_WORKSPACE_ID or --workspace-id)."
        ));
    }
    message.push('\n');
    message.push_str(&source.remedy());
    message
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    use super::*;
    use crate::error::category_of;

    /// A platform API whose `/auth/public` answers `status` once.
    fn identity_endpoint(status: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        thread::spawn(move || {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            let mut buf = [0_u8; 2048];
            let _ = stream.read(&mut buf);
            let body = r#"{"error":"Forbidden"}"#;
            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\
                 Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        format!("http://{addr}")
    }

    fn client_at(base: &str) -> langsmith::Client {
        langsmith::Client::new(base, base, ApiKey::new("lsv2_pt_secret"), None).unwrap()
    }

    #[test]
    fn a_rejected_key_is_an_authentication_failure() {
        let base = identity_endpoint("403 Forbidden");
        let source = ApiKeySource::EnvFile(PathBuf::from("/project/.env"));

        let err = verify_api_key(&client_at(&base), &source, None).unwrap_err();

        assert_eq!(category_of(&err), ErrorCategory::Authentication);
    }

    #[test]
    fn a_rejected_key_message_never_contains_the_key() {
        let base = identity_endpoint("401 Unauthorized");

        let err = verify_api_key(&client_at(&base), &ApiKeySource::ProcessEnv, None).unwrap_err();

        assert!(!format!("{err:#}").contains("lsv2_pt_secret"));
    }

    #[test]
    fn an_accepted_key_reports_that_it_was_verified() {
        let base = identity_endpoint("200 OK");

        let check = verify_api_key(&client_at(&base), &ApiKeySource::ProcessEnv, None).unwrap();

        assert_eq!(check, KeyCheck::Accepted);
    }

    #[test]
    fn an_unanswered_check_lets_the_command_continue_unverified() {
        let base = identity_endpoint("503 Service Unavailable");

        let check = verify_api_key(&client_at(&base), &ApiKeySource::ProcessEnv, None).unwrap();

        assert_eq!(check, KeyCheck::Unverified);
    }

    #[test]
    fn an_env_file_key_names_the_file_and_how_to_replace_or_drop_it() {
        let message = rejected_key_message(
            &ApiKeySource::EnvFile(PathBuf::from("/project/.env")),
            403,
            "https://api.smith.langchain.com",
            None,
        );

        assert_eq!(
            message,
            "LangSmith rejected LANGSMITH_API_KEY in /project/.env (HTTP 403 from \
             https://api.smith.langchain.com).\n\
             The key is invalid, expired, or was created for another LangSmith region or \
             installation. If it belongs to another region, set LANGSMITH_ENDPOINT to that \
             region's API, for example https://eu.api.smith.langchain.com.\n\
             Replace it with a new key from Settings → API Keys in LangSmith, or delete that \
             line and rerun this command in a terminal to sign in with your browser."
        );
    }

    #[test]
    fn an_environment_key_says_to_export_or_unset_it() {
        let message = rejected_key_message(
            &ApiKeySource::ProcessEnv,
            403,
            "https://api.smith.langchain.com",
            None,
        );

        assert!(message.contains("LANGSMITH_API_KEY from the environment"));
        assert!(message.contains("unset LANGSMITH_API_KEY"));
    }

    #[test]
    fn a_cached_sign_in_says_it_was_cleared() {
        let message = rejected_key_message(
            &ApiKeySource::SignIn,
            401,
            "https://api.smith.langchain.com",
            None,
        );

        assert!(message.contains("cached browser sign-in"));
        assert!(message.contains("The cached sign-in was cleared."));
    }

    #[test]
    fn a_scoped_workspace_is_named_as_another_possible_cause() {
        let message = rejected_key_message(
            &ApiKeySource::ProcessEnv,
            403,
            "https://api.smith.langchain.com",
            Some("ws-9"),
        );

        assert!(message.contains("cannot access workspace ws-9"));
    }

    #[test]
    fn only_a_401_or_403_is_a_refusal() {
        let api = |status| {
            anyhow::anyhow!(ApiError {
                status,
                detail: "Forbidden".to_string(),
            })
            .context("outer")
        };

        assert!(is_refusal(&api(StatusCode::FORBIDDEN)));
        assert!(is_refusal(&api(StatusCode::UNAUTHORIZED)));
        assert!(!is_refusal(&api(StatusCode::BAD_GATEWAY)));
        assert!(!is_refusal(&anyhow::anyhow!("connection reset")));
    }

    #[test]
    fn a_staged_key_is_traced_to_where_dev_found_it() {
        let env = Path::new("/project/.env");

        assert_eq!(
            ApiKeySource::of_staged_key("k", true, Some("k"), env),
            ApiKeySource::SignIn
        );
        assert_eq!(
            ApiKeySource::of_staged_key("k", false, Some("k"), env),
            ApiKeySource::EnvFile(env.to_path_buf())
        );
        assert_eq!(
            ApiKeySource::of_staged_key("k", false, Some("other"), env),
            ApiKeySource::ProcessEnv
        );
    }

    #[test]
    fn a_shell_key_wins_over_the_env_file() {
        let path = Path::new("/project/.env");

        assert_eq!(
            ApiKeySource::of_env_key(true, path),
            ApiKeySource::ProcessEnv
        );
        assert_eq!(
            ApiKeySource::of_env_key(false, path),
            ApiKeySource::EnvFile(path.to_path_buf())
        );
    }
}
