use std::env;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use reqwest::blocking::Client as HttpClient;
use reqwest::Url;
use serde::Serialize;

use super::event::EventEnvelope;
use super::transport::TelemetryTransport;

const SEGMENT_WRITE_KEY_ENV: &str = "MDA_SEGMENT_WRITE_KEY";
const SEGMENT_HOST_ENV: &str = "MDA_SEGMENT_HOST";
const DEFAULT_SEGMENT_HOST: &str = "https://api.segment.io";
const SEGMENT_TRACK_PATH: &str = "/v1/track";
/// Sends block command completion, so the bound is short.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(1);
/// Public write-only key: it can only submit events to this source, so shipping
/// it in release binaries is safe. `build.rs` bakes it only into release
/// builds, so source builds stay silent, as with PostHog. Rotating it takes a
/// release.
const BAKED_SEGMENT_WRITE_KEY: &str = env!("MDA_SEGMENT_WRITE_KEY");
/// Segment fills `context.ip` from the request when it is absent; a
/// placeholder keeps the sender's address out, as `$geoip_disable` does for
/// PostHog.
const PLACEHOLDER_IP: &str = "0.0.0.0";

fn nonempty(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn track_url(host: &str) -> Result<Url> {
    let host = host.trim().trim_end_matches('/');
    Url::parse(&format!("{host}{SEGMENT_TRACK_PATH}")).context("Segment host must be a valid URL")
}

struct SegmentConfig {
    write_key: String,
    host: String,
}

impl SegmentConfig {
    fn resolve(
        mut get_env: impl FnMut(&str) -> Option<String>,
        baked_write_key: &str,
    ) -> Option<Self> {
        let write_key = nonempty(get_env(SEGMENT_WRITE_KEY_ENV).as_deref())
            .or_else(|| nonempty(Some(baked_write_key)))?
            .to_string();
        let host = nonempty(get_env(SEGMENT_HOST_ENV).as_deref())
            .unwrap_or(DEFAULT_SEGMENT_HOST)
            .to_string();

        Some(Self { write_key, host })
    }
}

/// One `track` call, as Segment's warehouse loads it.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TrackRequest<'a> {
    event: &'static str,
    /// The subject is an install, never a signed-in person.
    anonymous_id: &'a str,
    /// Segment drops a retried event it already ingested by `messageId`.
    message_id: &'a str,
    timestamp: &'a str,
    context: TrackContext,
    properties: &'a EventEnvelope,
}

#[derive(Serialize)]
struct TrackContext {
    ip: &'static str,
}

impl<'a> TrackRequest<'a> {
    fn new(event: &'a EventEnvelope) -> Self {
        Self {
            event: event.event_type.as_str(),
            anonymous_id: &event.install_id,
            message_id: &event.event_id,
            timestamp: &event.created_at,
            context: TrackContext { ip: PLACEHOLDER_IP },
            properties: event,
        }
    }
}

/// Blocking Segment transport for one-event track requests.
pub struct SegmentTransport {
    write_key: String,
    track_url: Url,
    http: HttpClient,
}

impl SegmentTransport {
    /// Construct a Segment transport for a source write key and host.
    ///
    /// # Errors
    ///
    /// Returns an error when the key is empty, the host is invalid, or the HTTP
    /// client cannot be constructed.
    pub fn new(write_key: impl Into<String>, host: &str) -> Result<Self> {
        let owned_write_key = write_key.into();
        let normalized_write_key = owned_write_key.trim();
        if normalized_write_key.is_empty() {
            bail!("Segment write key is required");
        }

        let track_url = track_url(host)?;
        let http = crate::http::client_builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .context("failed to build Segment HTTP client")?;

        Ok(Self {
            write_key: normalized_write_key.to_string(),
            track_url,
            http,
        })
    }
}

impl TelemetryTransport for SegmentTransport {
    fn send(&self, event: &EventEnvelope) -> Result<()> {
        self.http
            .post(self.track_url.clone())
            // The write key is the username, with an empty password.
            .basic_auth(&self.write_key, Some(""))
            .json(&TrackRequest::new(event))
            .send()
            .context("failed to send Segment analytics event")?
            .error_for_status()
            .context("Segment rejected analytics event")?;
        Ok(())
    }
}

/// Construct the configured Segment transport unless no write key is set.
pub fn segment_transport() -> Option<SegmentTransport> {
    let config = SegmentConfig::resolve(|name| env::var(name).ok(), BAKED_SEGMENT_WRITE_KEY)?;
    SegmentTransport::new(config.write_key, &config.host).ok()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::telemetry::event::fixture_event;

    #[test]
    fn track_request_has_the_expected_envelope() {
        let event = fixture_event();

        let mut request = serde_json::to_value(TrackRequest::new(&event)).unwrap();
        let properties = request
            .as_object_mut()
            .unwrap()
            .remove("properties")
            .unwrap();

        assert_eq!(
            request,
            json!({
                "event": "command_completed",
                "anonymousId": "33333333-3333-4333-8333-333333333333",
                "messageId": "22222222-2222-4222-8222-222222222222",
                "timestamp": "1970-01-01T00:00:01.234Z",
                "context": { "ip": "0.0.0.0" },
            })
        );
        assert_eq!(properties, serde_json::to_value(&event).unwrap());
    }

    #[test]
    fn track_url_uses_the_segment_track_endpoint() {
        assert_eq!(
            track_url(DEFAULT_SEGMENT_HOST).unwrap().as_str(),
            "https://api.segment.io/v1/track"
        );
    }

    #[test]
    fn track_url_normalizes_a_trailing_slash() {
        assert_eq!(
            track_url("https://example.test/").unwrap().as_str(),
            "https://example.test/v1/track"
        );
    }

    #[test]
    fn transport_rejects_an_empty_write_key() {
        assert!(SegmentTransport::new("  ", DEFAULT_SEGMENT_HOST).is_err());
    }

    #[test]
    fn transport_rejects_an_invalid_host() {
        assert!(SegmentTransport::new("public-test-key", "not a URL").is_err());
    }

    #[test]
    fn baked_key_uses_the_us_host() {
        let config = SegmentConfig::resolve(|_| None, "baked-key").unwrap();

        assert_eq!(
            (config.write_key.as_str(), config.host.as_str()),
            ("baked-key", DEFAULT_SEGMENT_HOST)
        );
    }

    #[test]
    fn runtime_config_overrides_baked_defaults() {
        let config = SegmentConfig::resolve(
            |name| match name {
                SEGMENT_WRITE_KEY_ENV => Some(" runtime-key ".to_string()),
                SEGMENT_HOST_ENV => Some(" https://example.test ".to_string()),
                _ => None,
            },
            "baked-key",
        )
        .unwrap();

        assert_eq!(
            (config.write_key.as_str(), config.host.as_str()),
            ("runtime-key", "https://example.test")
        );
    }

    #[test]
    fn config_is_absent_without_any_write_key() {
        assert!(SegmentConfig::resolve(|_| None, "").is_none());
    }
}
