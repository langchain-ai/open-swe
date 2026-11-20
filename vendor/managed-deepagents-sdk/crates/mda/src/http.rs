use std::ffi::OsString;
use std::sync::OnceLock;

use reqwest::blocking::{Client, ClientBuilder};
use reqwest::Certificate;

/// PEM bundle of extra CA certificates to trust on top of the platform roots,
/// like Node's `NODE_EXTRA_CA_CERTS`. Opt-in for LangSmith endpoints behind a
/// private CA (self-hosted installs, the local stack in `tools/local-stack`).
const EXTRA_CA_CERTS_ENV: &str = "MDA_EXTRA_CA_CERTS";

pub(crate) fn client_builder() -> ClientBuilder {
    client_builder_for_version(&crate::version::package_version())
}

fn client_builder_for_version(version: &str) -> ClientBuilder {
    let builder = Client::builder()
        .user_agent(format!("mda-cli/{version}"))
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            if attempt
                .previous()
                .first()
                .is_some_and(|initial| initial.origin() != attempt.url().origin())
            {
                attempt.error("cross-origin redirect refused")
            } else {
                reqwest::redirect::Policy::default().redirect(attempt)
            }
        }));
    let extra = extra_ca_certs();
    if extra.is_empty() {
        builder
    } else {
        builder.tls_certs_merge(extra.iter().cloned())
    }
}

fn extra_ca_certs() -> &'static [Certificate] {
    static CERTS: OnceLock<Vec<Certificate>> = OnceLock::new();
    CERTS.get_or_init(
        || match load_extra_ca_certs(std::env::var_os(EXTRA_CA_CERTS_ENV)) {
            Ok(certs) => certs,
            Err(message) => {
                warn_extra_ca_certs(&message);
                Vec::new()
            }
        },
    )
}

/// Read the bundle named by [`EXTRA_CA_CERTS_ENV`]. Unset or empty means none.
fn load_extra_ca_certs(path: Option<OsString>) -> Result<Vec<Certificate>, String> {
    let Some(path) = path.filter(|path| !path.is_empty()) else {
        return Ok(Vec::new());
    };
    let pem = std::fs::read(&path)
        .map_err(|err| format!("could not read {}: {err}", path.to_string_lossy()))?;
    let certs = Certificate::from_pem_bundle(&pem)
        .map_err(|err| format!("could not parse {}: {err}", path.to_string_lossy()))?;
    if certs.is_empty() {
        return Err(format!(
            "{} contains no certificates",
            path.to_string_lossy()
        ));
    }
    Ok(certs)
}

#[expect(
    clippy::print_stderr,
    reason = "HTTP clients are built before any command UI exists"
)]
fn warn_extra_ca_certs(message: &str) {
    eprintln!("warning: ignoring {EXTRA_CA_CERTS_ENV}: {message}");
}

#[cfg(test)]
mod tests {
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;
    use std::thread;

    use super::*;

    fn redirect_server(
        listener: TcpListener,
        responses: Vec<String>,
    ) -> thread::JoinHandle<Vec<String>> {
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            responses
                .into_iter()
                .map(|response| {
                    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
                    let mut stream = loop {
                        match listener.accept() {
                            Ok((stream, _)) => break stream,
                            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                                assert!(
                                    std::time::Instant::now() < deadline,
                                    "no request received"
                                );
                                thread::sleep(std::time::Duration::from_millis(10));
                            }
                            Err(error) => panic!("accept failed: {error}"),
                        }
                    };
                    stream
                        .set_read_timeout(Some(std::time::Duration::from_secs(5)))
                        .unwrap();
                    let mut request = String::new();
                    let mut reader = BufReader::new(&mut stream);
                    loop {
                        let mut line = String::new();
                        reader.read_line(&mut line).unwrap();
                        if line == "\r\n" || line.is_empty() {
                            break;
                        }
                        request.push_str(&line);
                    }
                    let content_length = request.lines().find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    });
                    if let Some(length) = content_length {
                        let mut body = vec![0; length];
                        reader.read_exact(&mut body).unwrap();
                        request.push_str(std::str::from_utf8(&body).unwrap());
                    }
                    stream.write_all(response.as_bytes()).unwrap();
                    request.to_ascii_lowercase()
                })
                .collect()
        })
    }

    fn redirect_response(status: u16, location: &str) -> String {
        format!(
            "HTTP/1.1 {status} Redirect\r\nLocation: {location}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )
    }

    fn credential_client(builder: ClientBuilder) -> Client {
        builder
            .no_proxy()
            .timeout(std::time::Duration::from_secs(5))
            .build()
            .unwrap()
    }

    #[test]
    fn locked_reqwest_default_forwards_api_key_but_strips_standard_credentials() {
        for status in [302, 307, 308] {
            let source = TcpListener::bind("127.0.0.1:0").unwrap();
            let destination = TcpListener::bind("127.0.0.1:0").unwrap();
            let source_url = format!("http://{}", source.local_addr().unwrap());
            let destination_url = format!("http://{}", destination.local_addr().unwrap());
            let source_server =
                redirect_server(source, vec![redirect_response(status, &destination_url)]);
            let destination_server = redirect_server(
                destination,
                vec!["HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n".to_owned()],
            );
            let mut api_key = reqwest::header::HeaderValue::from_static("dummy-api-key");
            api_key.set_sensitive(true);
            let response = credential_client(Client::builder())
                .post(source_url)
                .body("dummy-sensitive-body")
                .header("X-Api-Key", api_key)
                .header("Authorization", "Bearer dummy-token")
                .header("Cookie", "session=dummy-session")
                .header("Proxy-Authorization", "Basic dummy-proxy")
                .send()
                .unwrap();
            assert!(response.status().is_success());
            let original = source_server.join().unwrap().pop().unwrap();
            let redirected = destination_server.join().unwrap().pop().unwrap();
            assert!(original.contains("authorization: bearer dummy-token"));
            assert!(original.contains("cookie: session=dummy-session"));
            assert!(original.contains("proxy-authorization: basic dummy-proxy"));
            assert!(redirected.contains("x-api-key: dummy-api-key"));
            assert!(!redirected.contains("authorization:"));
            assert!(!redirected.contains("cookie:"));
            assert_eq!(redirected.contains("dummy-sensitive-body"), status != 302);
        }
    }

    #[test]
    fn client_rejects_cross_origin_redirects_without_contacting_destination() {
        for status in [301, 302, 303, 307, 308] {
            let source = TcpListener::bind("127.0.0.1:0").unwrap();
            let destination = TcpListener::bind("127.0.0.1:0").unwrap();
            destination.set_nonblocking(true).unwrap();
            let source_url = format!("http://{}", source.local_addr().unwrap());
            let destination_url = format!("http://{}", destination.local_addr().unwrap());
            let server = redirect_server(
                source,
                vec![
                    redirect_response(307, "/same-origin-hop"),
                    redirect_response(status, &destination_url),
                ],
            );
            let error = credential_client(client_builder_for_version("1.2.3"))
                .post(source_url)
                .header("X-Api-Key", "dummy-api-key")
                .bearer_auth("dummy-token")
                .body("dummy-sensitive-body")
                .send()
                .unwrap_err();
            assert!(error.is_redirect(), "{status}: {error}");
            assert_eq!(
                destination.accept().unwrap_err().kind(),
                std::io::ErrorKind::WouldBlock
            );
            let original = server.join().unwrap().pop().unwrap();
            assert!(original.contains("x-api-key: dummy-api-key"));
        }
    }

    #[test]
    fn client_preserves_credentials_on_same_origin_redirects() {
        for status in [301, 302, 303, 307, 308] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            let server = redirect_server(
                listener,
                vec![
                    redirect_response(status, "/redirected"),
                    "HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n".to_owned(),
                ],
            );
            let response = credential_client(client_builder_for_version("1.2.3"))
                .get(url)
                .header("X-Api-Key", "dummy-api-key")
                .bearer_auth("dummy-token")
                .send()
                .unwrap();
            assert!(response.status().is_success());
            let redirected = server.join().unwrap().pop().unwrap();
            assert!(redirected.starts_with("get /redirected "));
            assert!(redirected.contains("x-api-key: dummy-api-key"));
            assert!(redirected.contains("authorization: bearer dummy-token"));
        }
    }

    #[test]
    fn client_rejects_redirect_host_and_scheme_changes() {
        for location_kind in ["host", "scheme"] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let location = if location_kind == "host" {
                format!("http://localhost:{}", address.port())
            } else {
                format!("https://{address}")
            };
            let server = redirect_server(listener, vec![redirect_response(302, &location)]);
            let error = credential_client(client_builder_for_version("1.2.3"))
                .get(format!("http://{address}"))
                .header("X-Api-Key", "dummy-api-key")
                .send()
                .unwrap_err();
            assert!(error.is_redirect(), "{location_kind}: {error}");
            server.join().unwrap();
        }
    }

    #[test]
    fn client_bounds_same_origin_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = redirect_server(listener, vec![redirect_response(302, "/loop"); 11]);
        let error = credential_client(client_builder_for_version("1.2.3"))
            .get(url)
            .send()
            .unwrap_err();
        assert!(error.is_redirect());
        assert_eq!(server.join().unwrap().len(), 11);
    }

    #[test]
    fn callers_can_still_disable_all_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = redirect_server(listener, vec![redirect_response(302, "/redirected")]);
        let response = credential_client(
            client_builder_for_version("1.2.3").redirect(reqwest::redirect::Policy::none()),
        )
        .get(url)
        .send()
        .unwrap();
        assert_eq!(response.status(), reqwest::StatusCode::FOUND);
        assert_eq!(server.join().unwrap().len(), 1);
    }

    #[test]
    fn client_sends_runtime_version_in_user_agent() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = String::new();
            {
                let mut reader = BufReader::new(&mut stream);
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" || line.is_empty() {
                        break;
                    }
                    request.push_str(&line);
                }
            }
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                .unwrap();
            request
        });

        client_builder_for_version("1.2.3")
            .build()
            .unwrap()
            .get(format!("http://{address}"))
            .send()
            .unwrap();

        let request = server.join().unwrap();
        let actual = request.lines().find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("user-agent")
                .then(|| value.trim())
        });
        assert_eq!(actual, Some("mda-cli/1.2.3"));
    }

    /// Throwaway self-signed CA certificate (no key is kept anywhere).
    const TEST_CA_PEM: &str = "-----BEGIN CERTIFICATE-----
MIIBgzCCASmgAwIBAgIUOVk8WT3CerVRSMT4jIEVY7a5ER4wCgYIKoZIzj0EAwIw
FjEUMBIGA1UEAwwLbWRhLXRlc3QtY2EwIBcNMjYxMDA4MTg1NjAwWhgPMjEyNjA5
MTQxODU2MDBaMBYxFDASBgNVBAMMC21kYS10ZXN0LWNhMFkwEwYHKoZIzj0CAQYI
KoZIzj0DAQcDQgAEXDaK9QO5j4FkXadJdnHSJJepwfKIJ5cA4Tuu8nZhx4UbeGos
NWiQJxr78p7EwZtSCe8UNQOhic5N/aAa/AzntaNTMFEwHQYDVR0OBBYEFJRiM7mG
bAZ1Opc/+JxyssWorGknMB8GA1UdIwQYMBaAFJRiM7mGbAZ1Opc/+JxyssWorGkn
MA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIhAJD8P1htMWqmUo+a
kqxej/5qbCLdujLn0KVzcl+Kv3zqAiBUt+ExskUSC8U0357+A9n4LkPyO6HbnMZp
0R82dw66uw==
-----END CERTIFICATE-----
";

    #[test]
    fn extra_ca_certs_are_optional() {
        assert!(load_extra_ca_certs(None).unwrap().is_empty());
        assert!(load_extra_ca_certs(Some(OsString::new()))
            .unwrap()
            .is_empty());
    }

    #[test]
    fn extra_ca_certs_load_a_pem_bundle() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ca.pem");
        std::fs::write(&path, format!("{TEST_CA_PEM}{TEST_CA_PEM}")).unwrap();
        let certs = load_extra_ca_certs(Some(path.into_os_string())).unwrap();
        assert_eq!(certs.len(), 2);
        client_builder_for_version("1.2.3")
            .tls_certs_merge(certs)
            .build()
            .unwrap();
    }

    #[test]
    fn extra_ca_certs_report_unusable_files() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("missing.pem");
        let err = load_extra_ca_certs(Some(missing.into_os_string())).unwrap_err();
        assert!(err.contains("could not read"), "{err}");

        let empty = dir.path().join("empty.pem");
        std::fs::write(&empty, "not a certificate\n").unwrap();
        let err = load_extra_ca_certs(Some(empty.into_os_string())).unwrap_err();
        assert!(err.contains("no certificates"), "{err}");
    }
}
