//! Helpers for tests that talk to a stub LangSmith.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc::{self, Receiver};
use std::thread;

/// Serves one response, then hands the request back so a test can assert on
/// the body. Reads to the end of the body so the assertion does not depend
/// on it landing in the same TCP read as the headers.
pub(crate) fn serve_once(status: &'static str, body: String) -> (String, Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let Ok((mut stream, _)) = listener.accept() else {
            return;
        };
        let _ = tx.send(read_request(&mut stream));
        respond(&mut stream, status, &body);
    });
    (format!("http://{addr}"), rx)
}

/// Serves `responses` in order, one per connection, and hands each request
/// back, for code that makes several calls.
pub(crate) fn serve_sequence(responses: Vec<(&'static str, String)>) -> (String, Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for (status, body) in responses {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            let _ = tx.send(read_request(&mut stream));
            respond(&mut stream, status, &body);
        }
    });
    (format!("http://{addr}"), rx)
}

/// Reads the headers and then up to the end of the body.
fn read_request(stream: &mut TcpStream) -> String {
    let mut buf = Vec::new();
    let mut tmp = [0_u8; 1024];
    while let Ok(read) = stream.read(&mut tmp) {
        if read == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..read]);
        let request = String::from_utf8_lossy(&buf);
        if let Some(headers_end) = request.find("\r\n\r\n") {
            let content_length = request
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    (name.eq_ignore_ascii_case("content-length"))
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or(0);
            if buf.len() >= headers_end + 4 + content_length {
                break;
            }
        }
    }
    String::from_utf8(buf).unwrap()
}

fn respond(stream: &mut TcpStream, status: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
}
