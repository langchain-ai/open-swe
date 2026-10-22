//! A blocking Server-Sent Events reader.
//!
//! The Agent Server streams runs as SSE (`event:` / `data:` fields, one blank
//! line per event, `:` lines as heartbeats). `reqwest::blocking::Response`
//! implements `Read`, so the stream can be consumed line by line without an
//! async runtime, which keeps `mda chat` on the same blocking HTTP stack as the
//! rest of the CLI.

use std::io::BufRead;

use anyhow::{Context, Result};

/// One parsed event. `data` joins multi-line payloads with `\n`, as the spec
/// requires.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SseEvent {
    pub(crate) event: String,
    pub(crate) data: String,
}

/// Iterates events from a line-oriented SSE body until it closes.
pub(crate) struct SseReader<R: BufRead> {
    reader: R,
    skip_lf: bool,
}

impl<R: BufRead> SseReader<R> {
    pub(crate) fn new(reader: R) -> Self {
        Self {
            reader,
            skip_lf: false,
        }
    }

    /// A CR ends the line immediately. Consume an optional LF on the next
    /// call so a CR-only event can dispatch while the connection stays open.
    fn read_line(&mut self, line: &mut Vec<u8>) -> std::io::Result<bool> {
        line.clear();
        loop {
            let available = self.reader.fill_buf()?;
            if available.is_empty() {
                return Ok(!line.is_empty());
            }
            if self.skip_lf {
                self.skip_lf = false;
                if available[0] == b'\n' {
                    self.reader.consume(1);
                    continue;
                }
            }
            if let Some(end) = available
                .iter()
                .position(|byte| matches!(byte, b'\r' | b'\n'))
            {
                line.extend_from_slice(&available[..end]);
                self.skip_lf = available[end] == b'\r';
                self.reader.consume(end + 1);
                return Ok(true);
            }
            let consumed = available.len();
            line.extend_from_slice(available);
            self.reader.consume(consumed);
        }
    }
}

impl<R: BufRead> Iterator for SseReader<R> {
    type Item = Result<SseEvent>;

    fn next(&mut self) -> Option<Self::Item> {
        let mut event = String::new();
        let mut data: Vec<String> = Vec::new();
        let mut line = Vec::new();
        loop {
            let read = match self.read_line(&mut line) {
                Ok(read) => read,
                Err(err) => return Some(Err(err).context("failed to read the event stream")),
            };
            if !read {
                // Stream closed. Flush a trailing event that lacked its blank line.
                return (!event.is_empty() || !data.is_empty()).then(|| {
                    Ok(SseEvent {
                        event,
                        data: data.join("\n"),
                    })
                });
            }
            let trimmed = match std::str::from_utf8(&line) {
                Ok(line) => line,
                Err(err) => {
                    return Some(Err(err).context("the event stream contains invalid UTF-8"))
                }
            };
            if trimmed.is_empty() {
                if event.is_empty() && data.is_empty() {
                    continue;
                }
                return Some(Ok(SseEvent {
                    event,
                    data: data.join("\n"),
                }));
            }
            if trimmed.starts_with(':') {
                continue;
            }
            let (field, value) = match trimmed.split_once(':') {
                Some((field, value)) => (field, value.strip_prefix(' ').unwrap_or(value)),
                None => (trimmed, ""),
            };
            match field {
                "event" => event = value.to_string(),
                "data" => data.push(value.to_string()),
                // `id` and `retry` carry nothing the chat loop acts on.
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    fn events(body: &str) -> Vec<SseEvent> {
        SseReader::new(Cursor::new(body))
            .collect::<Result<Vec<_>>>()
            .unwrap()
    }

    #[test]
    fn parses_event_and_data_fields() {
        let parsed =
            events("event: metadata\ndata: {\"run_id\":\"r1\"}\n\nevent: end\ndata: null\n\n");
        assert_eq!(
            parsed,
            vec![
                SseEvent {
                    event: "metadata".into(),
                    data: "{\"run_id\":\"r1\"}".into()
                },
                SseEvent {
                    event: "end".into(),
                    data: "null".into()
                },
            ]
        );
    }

    #[test]
    fn joins_multi_line_data_and_ignores_comments_and_crlf() {
        let parsed = events(": heartbeat\r\nevent: values\r\ndata: {\r\ndata: }\r\n\r\n");
        assert_eq!(
            parsed,
            vec![SseEvent {
                event: "values".into(),
                data: "{\n}".into()
            }]
        );
    }

    #[test]
    fn flushes_a_final_event_without_a_trailing_blank_line() {
        let parsed = events("event: error\ndata: {\"message\":\"boom\"}");
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].event, "error");
    }

    #[test]
    fn parses_all_line_endings_across_buffer_boundaries() {
        for body in [
            "event: messages\rdata: hello\r\revent: values\rdata: {}\r\r",
            "event: messages\r\ndata: hello\n\nevent: values\rdata: {}\r\n\r\n",
        ] {
            for capacity in [1, 2, 7, 64] {
                let parsed = SseReader::new(std::io::BufReader::with_capacity(
                    capacity,
                    Cursor::new(body),
                ))
                .collect::<Result<Vec<_>>>()
                .unwrap();
                assert_eq!(
                    parsed,
                    vec![
                        SseEvent {
                            event: "messages".into(),
                            data: "hello".into()
                        },
                        SseEvent {
                            event: "values".into(),
                            data: "{}".into()
                        },
                    ]
                );
            }
        }
    }

    #[test]
    fn dispatches_cr_delimited_event_without_waiting_for_more_data() {
        struct OpenStream<'a>(&'a [u8]);
        impl std::io::Read for OpenStream<'_> {
            fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
                if self.0.is_empty() {
                    return Err(std::io::ErrorKind::WouldBlock.into());
                }
                std::io::Read::read(&mut self.0, buf)
            }
        }
        let mut reader = SseReader::new(std::io::BufReader::new(OpenStream(
            b"event: messages\rdata: hello\r\r",
        )));
        assert_eq!(
            reader.next().unwrap().unwrap(),
            SseEvent {
                event: "messages".into(),
                data: "hello".into()
            }
        );
    }

    #[test]
    fn preserves_utf8_across_buffers_and_reports_invalid_bytes() {
        let mut reader = SseReader::new(std::io::BufReader::with_capacity(
            1,
            Cursor::new("event: messages\rdata: héllo 🌍\r\r"),
        ));
        assert_eq!(reader.next().unwrap().unwrap().data, "héllo 🌍");
        let mut reader = SseReader::new(Cursor::new(b"event: messages\ndata: \xff\n\n"));
        assert!(reader.next().unwrap().is_err());
    }

    #[test]
    fn skips_leading_blank_lines() {
        assert!(events("\n\n\n").is_empty());
    }
}
