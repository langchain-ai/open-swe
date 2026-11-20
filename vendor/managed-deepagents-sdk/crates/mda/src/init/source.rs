//! Literals shared by the modules that write project source.

use super::ConnectionOwner;

/// A string literal valid in both Python and TypeScript. Values come from
/// Fleet, so nothing is written into source unescaped.
pub(crate) fn lit(value: &str) -> String {
    serde_json::to_string(value).expect("a string always serializes")
}

pub(super) fn owner(owner: ConnectionOwner) -> &'static str {
    match owner {
        ConnectionOwner::User => "user",
        ConnectionOwner::Agent => "agent",
    }
}

/// `line` with the characters Python (`\r`) or JavaScript (U+2028, U+2029)
/// end a line on escaped, so Fleet text can't end the comment early. JSON
/// string literals keep U+2028 raw, so commented blocks need this too.
pub(super) fn in_comment(line: &str) -> String {
    line.replace('\r', "\\r")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029")
}
