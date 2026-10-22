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
    let builder = Client::builder().user_agent(format!("mda-cli/{version}"));
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
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::thread;

    use super::*;

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
