use std::env;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::blocking::Client as HttpClient;
use reqwest::Url;
use serde::Serialize;

use super::event::EventEnvelope;
use super::transport::TelemetryTransport;

const POSTHOG_API_KEY_ENV: &str = "MDA_POSTHOG_API_KEY";
const POSTHOG_HOST_ENV: &str = "MDA_POSTHOG_HOST";
const DEFAULT_POSTHOG_HOST: &str = "https://us.i.posthog.com";
const POSTHOG_CAPTURE_PATH: &str = "/i/v0/e/";
/// Sends block command completion, so the bound is short.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(1);
const BAKED_POSTHOG_API_KEY: &str = env!("MDA_POSTHOG_API_KEY");

fn nonempty(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn capture_url(host: &str) -> Result<Url> {
    let host = host.trim().trim_end_matches('/');
    Url::parse(&format!("{host}{POSTHOG_CAPTURE_PATH}")).context("PostHog host must be a valid URL")
}

struct PosthogConfig {
    api_key: String,
    host: String,
}

impl PosthogConfig {
    fn resolve(
        mut get_env: impl FnMut(&str) -> Option<String>,
        baked_api_key: &str,
    ) -> Option<Self> {
        let api_key = nonempty(get_env(POSTHOG_API_KEY_ENV).as_deref())
            .or_else(|| nonempty(Some(baked_api_key)))?
            .to_string();
        let host = nonempty(get_env(POSTHOG_HOST_ENV).as_deref())
            .unwrap_or(DEFAULT_POSTHOG_HOST)
            .to_string();

        Some(Self { api_key, host })
    }
}

#[derive(Serialize)]
struct CaptureProperties<'a> {
    #[serde(rename = "$process_person_profile")]
    process_person_profile: bool,
    #[serde(rename = "$geoip_disable")]
    geoip_disable: bool,
    #[serde(flatten)]
    event: &'a EventEnvelope,
}

#[derive(Serialize)]
struct CaptureRequest<'a> {
    api_key: &'a str,
    event: &'static str,
    distinct_id: &'a str,
    timestamp: &'a str,
    properties: CaptureProperties<'a>,
}

impl<'a> CaptureRequest<'a> {
    fn new(api_key: &'a str, event: &'a EventEnvelope) -> Self {
        Self {
            api_key,
            event: event.event_type.as_str(),
            distinct_id: &event.install_id,
            timestamp: &event.created_at,
            properties: CaptureProperties {
                process_person_profile: false,
                geoip_disable: true,
                event,
            },
        }
    }
}

/// Blocking PostHog transport for one-event capture requests.
pub struct PosthogTransport {
    api_key: String,
    capture_url: Url,
    http: HttpClient,
}

impl PosthogTransport {
    /// Construct a PostHog transport for a project ingestion key and host.
    ///
    /// # Errors
    ///
    /// Returns an error when the key is empty, the host is invalid, or the HTTP
    /// client cannot be constructed.
    pub fn new(api_key: impl Into<String>, host: &str) -> Result<Self> {
        let owned_api_key = api_key.into();
        let normalized_api_key = owned_api_key.trim();
        if normalized_api_key.is_empty() {
            bail!("PostHog project ingestion key is required");
        }

        let capture_url = capture_url(host)?;
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build PostHog HTTP client")?;

        Ok(Self {
            api_key: normalized_api_key.to_string(),
            capture_url,
            http,
        })
    }
}

impl TelemetryTransport for PosthogTransport {
    fn send(&self, event: &EventEnvelope) -> Result<()> {
        self.http
            .post(self.capture_url.clone())
            .json(&CaptureRequest::new(&self.api_key, event))
            .send()
            .context("failed to send PostHog analytics event")?
            .error_for_status()
            .context("PostHog rejected analytics event")?;
        Ok(())
    }
}

/// Construct the configured PostHog transport unless telemetry is turned off.
pub fn posthog_transport() -> Option<PosthogTransport> {
    let config = PosthogConfig::resolve(|name| env::var(name).ok(), BAKED_POSTHOG_API_KEY)?;
    PosthogTransport::new(config.api_key, &config.host).ok()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::telemetry::event::fixture_event;

    #[test]
    fn capture_request_has_the_expected_json() {
        let event = fixture_event();

        let request = serde_json::to_value(CaptureRequest::new("public-test-key", &event)).unwrap();

        assert_eq!(
            request,
            json!({
                "api_key": "public-test-key",
                "event": "command_completed",
                "distinct_id": "33333333-3333-4333-8333-333333333333",
                "timestamp": "1970-01-01T00:00:01.234Z",
                "properties": {
                    "$process_person_profile": false,
                    "$geoip_disable": true,
                    "user_id": null, "org_id": null, "workspace_id": null,
                    "deployment_id": null, "deployment_name": null, "langsmith_host_url": null,
                    "event_id": "22222222-2222-4222-8222-222222222222",
                    "install_id": "33333333-3333-4333-8333-333333333333",
                    "invocation_id": "11111111-1111-4111-8111-111111111111",
                    "event_type": "command_completed",
                    "schema_version": "2026-08-20",
                    "created_at": "1970-01-01T00:00:01.234Z",
                    "cli_version": "0.5.3",
                    "os": "linux",
                    "install_channel": "npm",
                    "invoker_detection_method": "interactive_terminal",
                    "command_path": "channel init slack",
                    "flags_present": ["name"],
                    "outcome": "success"
                }
            })
        );
    }

    #[test]
    fn capture_url_uses_the_posthog_capture_endpoint() {
        assert_eq!(
            capture_url(DEFAULT_POSTHOG_HOST).unwrap().as_str(),
            "https://us.i.posthog.com/i/v0/e/"
        );
    }

    #[test]
    fn capture_url_normalizes_a_trailing_slash() {
        assert_eq!(
            capture_url("https://example.test/").unwrap().as_str(),
            "https://example.test/i/v0/e/"
        );
    }

    #[test]
    fn transport_rejects_an_empty_api_key() {
        assert!(PosthogTransport::new("  ", DEFAULT_POSTHOG_HOST).is_err());
    }

    #[test]
    fn transport_rejects_an_invalid_host() {
        assert!(PosthogTransport::new("public-test-key", "not a URL").is_err());
    }

    #[test]
    fn telemetry_is_on_without_any_configuration() {
        let config = PosthogConfig::resolve(|_| None, "baked-key").unwrap();

        assert_eq!(
            (config.api_key.as_str(), config.host.as_str()),
            ("baked-key", DEFAULT_POSTHOG_HOST)
        );
    }

    #[test]
    fn runtime_config_overrides_baked_defaults() {
        let config = PosthogConfig::resolve(
            |name| match name {
                POSTHOG_API_KEY_ENV => Some(" runtime-key ".to_string()),
                POSTHOG_HOST_ENV => Some(" https://example.test ".to_string()),
                _ => None,
            },
            "baked-key",
        )
        .unwrap();

        assert_eq!(
            (config.api_key.as_str(), config.host.as_str()),
            ("runtime-key", "https://example.test")
        );
    }

    #[test]
    fn blank_runtime_key_falls_back_to_baked_key() {
        let config = PosthogConfig::resolve(
            |name| (name == POSTHOG_API_KEY_ENV).then(|| "  ".to_string()),
            "baked-key",
        )
        .unwrap();

        assert_eq!(config.api_key, "baked-key");
    }

    #[test]
    fn config_is_absent_without_any_api_key() {
        let config = PosthogConfig::resolve(|_| None, "");

        assert!(config.is_none());
    }
}
