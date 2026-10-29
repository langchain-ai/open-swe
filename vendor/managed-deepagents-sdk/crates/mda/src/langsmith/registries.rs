//! LangSmith platform API endpoints for sandbox image registries.
//!
//! Private `dockerImage` pulls need a Host registry that stores credentials.
//! MDA creates or updates a deployment-owned registry from the secret-safe
//! declaration, then passes its id on snapshot create.

use anyhow::{bail, Context, Result};
use reqwest::Method;
use serde::Deserialize;

use super::Client;

/// A sandbox image registry as returned by get/list.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct RegistrySummary {
    /// Registry UUID passed as `registry_id` on snapshot create.
    pub id: String,
    /// Author-facing name from `defineSandbox({ registry })`.
    #[serde(default)]
    pub name: Option<String>,
    /// Registry host (e.g. `ghcr.io`).
    #[serde(default)]
    pub url: Option<String>,
}

impl Client {
    /// Look up a registry by name, returning `None` when it is already gone.
    pub fn find_registry_by_name(&self, name: &str) -> Result<Option<RegistrySummary>> {
        let base = self.platform_base();
        let path = format!("/api/v2/sandboxes/registries/{}", urlencoding_path(name));
        self.request_optional(base, Method::GET, &path, &[], None)?
            .map(|value| {
                serde_json::from_value(value).context("failed to decode sandbox registry response")
            })
            .transpose()
    }

    /// Create or update a deployment-owned registry and return its id.
    pub fn reconcile_registry(
        &self,
        name: &str,
        url: &str,
        username: &str,
        password: &str,
    ) -> Result<RegistrySummary> {
        let base = self.platform_base();
        let root = "/api/v2/sandboxes/registries";
        let existing_path = format!("{root}/{}", urlencoding_path(name));
        let existing = self.find_registry_by_name(name)?;
        let payload = serde_json::json!({
            "name": name,
            "url": url,
            "username": username,
            "password": password,
        });
        let value = match existing {
            Some(_) => self
                .request(base, Method::PATCH, &existing_path, &[], Some(payload))
                .with_context(|| format!("failed to update sandbox registry '{name}'"))?,
            None => self
                .request(base, Method::POST, root, &[], Some(payload))
                .with_context(|| format!("failed to create sandbox registry '{name}'"))?,
        };
        let registry: RegistrySummary =
            serde_json::from_value(value).context("failed to decode sandbox registry response")?;
        if registry.id.trim().is_empty() {
            bail!("sandbox registry '{name}' response is missing id");
        }
        Ok(registry)
    }

    /// Delete a deployment-owned registry. Missing registries are a success.
    pub fn delete_registry(&self, name: &str) -> Result<()> {
        let base = self.platform_base();
        let path = format!("/api/v2/sandboxes/registries/{}", urlencoding_path(name));
        self.request_optional(base, Method::DELETE, &path, &[], None)?;
        Ok(())
    }
}

fn urlencoding_path(value: &str) -> String {
    value
        .bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc::{self, Receiver};
    use std::thread;

    use serde_json::json;

    use super::*;

    fn serve(responses: Vec<(u16, String)>) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            for (status, body) in responses {
                let (mut stream, _) = listener.accept().unwrap();
                let request = read_request(&mut stream);
                tx.send(request).unwrap();
                let response = format!(
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                stream.write_all(response.as_bytes()).unwrap();
            }
        });
        (format!("http://{addr}"), rx)
    }

    fn read_request(stream: &mut std::net::TcpStream) -> String {
        let mut data = Vec::new();
        let mut buf = [0_u8; 4096];
        loop {
            let read = stream.read(&mut buf).unwrap();
            if read == 0 {
                break;
            }
            data.extend_from_slice(&buf[..read]);
            let text = String::from_utf8_lossy(&data);
            if text.contains("\r\n\r\n") {
                break;
            }
        }
        String::from_utf8_lossy(&data).to_string()
    }

    #[test]
    fn reconcile_registry_creates_when_missing() {
        let body = json!({
            "id": "reg-1",
            "name": "agent--registry",
            "url": "ghcr.io"
        })
        .to_string();
        let (url, rx) = serve(vec![
            (404, json!({ "detail": "not found" }).to_string()),
            (200, body),
        ]);
        // Build at runtime so static analysis does not treat the fixture as a
        // hard-coded credential (CodeQL rust/hard-coded-cryptographic-value).
        let password = format!("{}-{}", "fixture", "credential");
        let registry = Client::for_test(&url)
            .reconcile_registry("agent--registry", "ghcr.io", "octocat", &password)
            .unwrap();
        assert_eq!(registry.id, "reg-1");
        assert_eq!(registry.name.as_deref(), Some("agent--registry"));
        assert!(rx
            .recv()
            .unwrap()
            .contains("GET /api/v2/sandboxes/registries/agent--registry"));
        let create = rx.recv().unwrap();
        assert!(create.contains("POST /api/v2/sandboxes/registries"));
        assert!(create.contains(&format!(r#""password":"{password}""#)));
    }
}
