use anyhow::{anyhow, bail, Context, Result};
use reqwest::blocking::Client as HttpClient;
use reqwest::{redirect, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::{humanize_error_body, Client, REQUEST_TIMEOUT, TENANT_HEADER};

const SETUP_ERROR_FIELD_MAX_CHARS: usize = 512;

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ChannelSetupRequest {
    pub(crate) provider: String,
    pub(crate) target: ChannelSetupTarget,
    pub(crate) slack: SlackChannelSetup,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ChannelSetupTarget {
    pub(crate) deployment_id: String,
    pub(crate) endpoint_url: String,
    pub(crate) channel_name: String,
    pub(crate) agent_id: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SlackChannelSetup {
    pub(crate) app: SlackChannelApp,
    pub(crate) options: SlackChannelOptions,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SlackChannelApp {
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) image: String,
    pub(crate) background_color: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct SlackChannelOptions {
    /// Deprecated in favor of `trigger_mode`; still sent for Trigger Servers
    /// that only read this flag.
    pub(crate) trigger_on_all_messages: bool,
    /// Only sent when the channel sets it, so default payloads stay identical
    /// for consumers that predate the option. Unset lets Trigger Server keep an
    /// existing app's mode or default a new one to `thread`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) trigger_mode: Option<SlackTriggerMode>,
    pub(crate) allow_bot_triggers: bool,
}

/// Which channel messages start a run; DMs always do.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum SlackTriggerMode {
    /// Only messages that mention the agent.
    Mention,
    /// Mentions, and later replies in threads the agent was mentioned in.
    Thread,
    /// Every message in the agent's channels. Sent to Trigger Server as the
    /// legacy `triggerOnAllMessages` flag, which every version understands.
    All,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ChannelSetupResponse {
    pub(crate) status: ChannelSetupStatus,
    #[serde(default)]
    pub(crate) channel_id: Option<String>,
    #[serde(default)]
    pub(crate) source_registration_id: Option<String>,
    #[serde(default)]
    pub(crate) authorization_url: Option<String>,
    #[serde(default)]
    pub(crate) message: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ChannelSetupStatus {
    Active,
    AuthorizationRequired,
    PendingApproval,
}

impl Client {
    pub(crate) fn setup_trigger_channel(
        &self,
        payload: &ChannelSetupRequest,
    ) -> Result<ChannelSetupResponse> {
        let mut url = Url::parse(&self.public_platform_base()?)
            .context("LangSmith platform API URL is invalid")?;
        url.path_segments_mut()
            .map_err(|_| anyhow!("Channel setup URL is invalid."))?
            .pop_if_empty()
            .extend(["v1", "channels"]);
        let http = HttpClient::builder()
            .timeout(REQUEST_TIMEOUT)
            .redirect(redirect::Policy::none())
            .build()
            .context("Failed to initialize channel setup.")?;
        let api_key = self.api_key()?;
        let mut request = http
            .post(url)
            .header("X-Api-Key", api_key.as_str())
            .header("Accept", "application/json")
            .json(payload);
        if let Some(tenant_id) = &self.tenant_id {
            request = request.header(TENANT_HEADER, tenant_id);
        }
        let response = request.send().context("Channel setup request failed.")?;
        let status = response.status();
        if !status.is_success() {
            let text = response.text().unwrap_or_default();
            bail!("{}", setup_failure_message(status, &text, api_key.as_str()));
        }
        let text = response
            .text()
            .context("Failed to read channel setup response.")?;
        let mut response: ChannelSetupResponse = serde_json::from_str(&text)
            .map_err(|_| anyhow!("Channel setup returned an invalid response."))?;
        if let Some(message) = &mut response.message {
            *message = message.replace(api_key.as_str(), "[REDACTED]");
        }
        if response
            .authorization_url
            .as_deref()
            .is_some_and(|url| url.contains(api_key.as_str()))
        {
            response.authorization_url = None;
        }
        Ok(response)
    }
}

/// The error for a failed channel setup: the status, plus the Trigger Server's
/// `detail` and `remedy` when its body is JSON. A body that is not JSON is
/// never echoed, and each field is dropped unless it is short, printable text.
fn setup_failure_message(status: StatusCode, text: &str, api_key: &str) -> String {
    let code = status.as_u16();
    let safe = |field: &str| {
        let field = if api_key.is_empty() {
            field.to_string()
        } else {
            field.replace(api_key, "[REDACTED]")
        };
        let field = field.trim();
        (!field.is_empty()
            && field.chars().count() <= SETUP_ERROR_FIELD_MAX_CHARS
            && !field.chars().any(char::is_control))
        .then(|| field.to_string())
    };
    let Some(detail) = humanize_error_body(text).as_deref().and_then(safe) else {
        return format!("Channel setup failed (HTTP {code}).");
    };
    let mut message = format!("Channel setup failed (HTTP {code}): {detail}");
    if !detail.ends_with(['.', '!', '?']) {
        message.push('.');
    }
    let remedy = serde_json::from_str::<Value>(text)
        .ok()
        .and_then(|body| body.get("remedy")?.as_str().and_then(safe));
    if let Some(remedy) = remedy {
        message.push(' ');
        message.push_str(&remedy);
    }
    message
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc::{self, Receiver};
    use std::thread;
    use std::time::Duration;

    use reqwest::blocking::Client as HttpClient;
    use serde_json::{json, Value};

    use super::*;
    use crate::langsmith::{Client, Credential, REQUEST_TIMEOUT};

    fn client(tenant_id: Option<String>, platform_api_url: &Url) -> Client {
        Client {
            http: HttpClient::builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .unwrap(),
            credential: Credential::ApiKey("resolved-login-key".into()),
            tenant_id,
            host_backend_url: "https://host.invalid".into(),
            platform_api_url: Some(platform_api_url.to_string()),
        }
    }

    fn self_hosted_client(platform_api_url: &Url, base_path: &str) -> Client {
        let mut platform_api_url = platform_api_url.clone();
        platform_api_url.set_path(base_path);
        let mut host_backend_url = platform_api_url.clone();
        let path = host_backend_url.path().trim_end_matches('/');
        host_backend_url.set_path(&format!("{path}/api-host"));
        Client {
            http: HttpClient::builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .unwrap(),
            credential: Credential::ApiKey("resolved-login-key".into()),
            tenant_id: None,
            host_backend_url: host_backend_url.to_string(),
            platform_api_url: Some(platform_api_url.to_string()),
        }
    }

    fn request() -> ChannelSetupRequest {
        serde_json::from_value(json!({
            "provider": "slack",
            "target": {
                "deployment_id": "deployment-id",
                "endpoint_url": "https://agent.example/v1/channels/slack/events",
                "channel_name": "slack",
                "agent_id": "assistant-id"
            },
            "slack": {
                "app": {
                    "name": "Managed Deep Agent",
                    "description": "Managed Deep Agent channel",
                    "image": "data:image/png;base64,aW1hZ2U=",
                    "background_color": "#09090f"
                },
                "options": {
                    "triggerOnAllMessages": false,
                    "allowBotTriggers": false
                }
            }
        }))
        .unwrap()
    }

    fn read_http_request(stream: &mut TcpStream) -> String {
        let mut buf = Vec::new();
        let mut chunk = [0_u8; 1024];
        loop {
            let read = stream.read(&mut chunk).unwrap();
            if read == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..read]);
            let request = String::from_utf8_lossy(&buf);
            let Some(headers_end) = request.find("\r\n\r\n") else {
                continue;
            };
            let content_length = request
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or(0);
            if buf.len() >= headers_end + 4 + content_length {
                break;
            }
        }
        String::from_utf8(buf).unwrap()
    }

    fn serve_once(
        status: &str,
        headers: &[(&str, &str)],
        body: &str,
    ) -> (reqwest::Url, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, receiver) = mpsc::channel();
        let status = status.to_string();
        let headers = headers
            .iter()
            .map(|(name, value)| (name.to_string(), value.to_string()))
            .collect::<Vec<_>>();
        let body = body.to_string();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let _ = sender.send(read_http_request(&mut stream));
            let mut response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
                body.len()
            );
            for (name, value) in headers {
                response.push_str(&format!("{name}: {value}\r\n"));
            }
            response.push_str("\r\n");
            response.push_str(&body);
            stream.write_all(response.as_bytes()).unwrap();
        });
        (
            reqwest::Url::parse(&format!("http://{address}")).unwrap(),
            receiver,
        )
    }

    fn body(request: &str) -> Value {
        serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap()
    }

    #[test]
    fn setup_channel_posts_exact_path_body_and_auth_headers() {
        let response = json!({
            "status": "active",
            "channel_id": "a6190f67-865d-4705-8171-35c90e64c730",
            "source_registration_id": "f4cde312-d221-44a7-8417-00f13d3b868a",
            "authorization_url": null,
            "message": null
        });
        let (origin, receiver) = serve_once("200 OK", &[], &response.to_string());

        client(Some("tenant-id".into()), &origin)
            .setup_trigger_channel(&request())
            .unwrap();
        let raw = receiver.recv_timeout(Duration::from_secs(1)).unwrap();

        assert!(raw.starts_with("POST /v1/channels HTTP/1.1\r\n"));
        assert!(raw
            .to_ascii_lowercase()
            .contains("x-api-key: resolved-login-key"));
        assert!(raw.to_ascii_lowercase().contains("x-tenant-id: tenant-id"));
        assert_eq!(body(&raw), serde_json::to_value(request()).unwrap());
    }

    #[test]
    fn setup_channel_uses_the_client_platform_base() {
        let response = json!({
            "status": "active",
            "channel_id": "a6190f67-865d-4705-8171-35c90e64c730",
            "source_registration_id": "f4cde312-d221-44a7-8417-00f13d3b868a",
            "authorization_url": null,
            "message": null
        });
        let (platform, receiver) = serve_once("200 OK", &[], &response.to_string());
        client(None, &platform)
            .setup_trigger_channel(&request())
            .unwrap();
        let raw = receiver.recv_timeout(Duration::from_secs(1)).unwrap();

        assert!(raw.starts_with("POST /v1/channels HTTP/1.1\r\n"));
    }

    #[test]
    fn self_hosted_setup_channel_uses_the_public_api_prefix() {
        let response = json!({
            "status": "active",
            "channel_id": "a6190f67-865d-4705-8171-35c90e64c730",
            "source_registration_id": "f4cde312-d221-44a7-8417-00f13d3b868a",
            "authorization_url": null,
            "message": null
        });
        let (platform, receiver) = serve_once("200 OK", &[], &response.to_string());
        self_hosted_client(&platform, "")
            .setup_trigger_channel(&request())
            .unwrap();
        let raw = receiver.recv_timeout(Duration::from_secs(1)).unwrap();

        assert!(raw.starts_with("POST /api/v1/channels HTTP/1.1\r\n"));
    }

    #[test]
    fn self_hosted_setup_channel_preserves_the_frontend_base_path() {
        let response = json!({
            "status": "active",
            "channel_id": "a6190f67-865d-4705-8171-35c90e64c730",
            "source_registration_id": "f4cde312-d221-44a7-8417-00f13d3b868a",
            "authorization_url": null,
            "message": null
        });
        let (platform, receiver) = serve_once("200 OK", &[], &response.to_string());
        self_hosted_client(&platform, "/langsmith")
            .setup_trigger_channel(&request())
            .unwrap();
        let raw = receiver.recv_timeout(Duration::from_secs(1)).unwrap();

        assert!(raw.starts_with("POST /langsmith/api/v1/channels HTTP/1.1\r\n"));
    }

    #[test]
    fn setup_channel_redacts_the_sent_api_key_from_response_fields() {
        let response = json!({
            "status": "authorization_required",
            "channel_id": null,
            "source_registration_id": null,
            "authorization_url": "https://trigger.example/authorize?state=resolved-login-key",
            "message": "Trigger echoed resolved-login-key in its response"
        });
        let (origin, _) = serve_once("200 OK", &[], &response.to_string());

        let response = client(None, &origin)
            .setup_trigger_channel(&request())
            .unwrap();

        assert_eq!(
            response.message.as_deref(),
            Some("Trigger echoed [REDACTED] in its response")
        );
        assert_eq!(response.authorization_url, None);
    }

    #[test]
    fn setup_channel_does_not_follow_redirects() {
        let destination = TcpListener::bind("127.0.0.1:0").unwrap();
        destination.set_nonblocking(true).unwrap();
        let destination_url = format!("http://{}/captured", destination.local_addr().unwrap());
        let (origin, _) = serve_once(
            "302 Found",
            &[("Location", destination_url.as_str())],
            "redirect-body-secret",
        );

        let error = client(None, &origin)
            .setup_trigger_channel(&request())
            .unwrap_err();

        assert!(error.to_string().contains("HTTP 302"));
        thread::sleep(Duration::from_millis(50));
        assert!(
            destination.accept().is_err(),
            "redirect destination was contacted"
        );
    }

    #[test]
    fn setup_channel_non_success_error_isolated_from_credentials_and_response() {
        let (origin, _) = serve_once(
            "401 Unauthorized",
            &[("Location", "https://evil.example/leak")],
            "remote-response-secret",
        );

        let rendered = client(None, &origin)
            .setup_trigger_channel(&request())
            .unwrap_err()
            .to_string();

        assert_eq!(rendered, "Channel setup failed (HTTP 401).");
    }

    fn setup_error(status: &str, body: &Value) -> String {
        let (origin, _) = serve_once(status, &[], &body.to_string());
        client(None, &origin)
            .setup_trigger_channel(&request())
            .unwrap_err()
            .to_string()
    }

    #[test]
    fn setup_channel_error_includes_the_trigger_server_detail() {
        let rendered = setup_error(
            "502 Bad Gateway",
            &json!({ "detail": "Unable to update the existing Slack app" }),
        );

        assert_eq!(
            rendered,
            "Channel setup failed (HTTP 502): Unable to update the existing Slack app."
        );
    }

    #[test]
    fn setup_channel_error_appends_the_remedy() {
        let rendered = setup_error(
            "403 Forbidden",
            &json!({
                "type": "https://docs.langchain.com/errors/channel-setup",
                "title": "Forbidden",
                "status": 403,
                "detail": "Slack did not allow this user to update the app.",
                "remedy": "Ask a Slack admin to add you as an app collaborator."
            }),
        );

        assert_eq!(
            rendered,
            "Channel setup failed (HTTP 403): Slack did not allow this user to update the app. \
             Ask a Slack admin to add you as an app collaborator."
        );
    }

    #[test]
    fn setup_channel_error_summarizes_validation_details() {
        let rendered = setup_error(
            "422 Unprocessable Entity",
            &json!({ "detail": [{
                "type": "string_too_long",
                "loc": ["body", "slack", "app", "description"],
                "msg": "String should have at most 139 characters",
                "input": "resolved-login-key"
            }] }),
        );

        assert_eq!(
            rendered,
            "Channel setup failed (HTTP 422): Invalid value for 'slack.app.description': \
             String should have at most 139 characters."
        );
    }

    #[test]
    fn setup_channel_error_redacts_the_api_key_from_detail_and_remedy() {
        let rendered = setup_error(
            "409 Conflict",
            &json!({
                "detail": "Key resolved-login-key cannot own this channel.",
                "remedy": "Rotate resolved-login-key."
            }),
        );

        assert!(!rendered.contains("resolved-login-key"));
        assert_eq!(
            rendered,
            "Channel setup failed (HTTP 409): Key [REDACTED] cannot own this channel. \
             Rotate [REDACTED]."
        );
    }

    #[test]
    fn setup_channel_error_drops_unsafe_detail_and_remedy() {
        let rendered = setup_error(
            "502 Bad Gateway",
            &json!({ "detail": "Slack failed\u{1b}[2J", "remedy": "Retry." }),
        );
        assert_eq!(rendered, "Channel setup failed (HTTP 502).");

        let rendered = setup_error(
            "502 Bad Gateway",
            &json!({ "detail": "Slack failed.", "remedy": "x".repeat(513) }),
        );
        assert_eq!(rendered, "Channel setup failed (HTTP 502): Slack failed.");
    }
}
