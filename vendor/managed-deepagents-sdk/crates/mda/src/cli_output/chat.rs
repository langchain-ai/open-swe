//! Chat input and transcript presentation. Wire formats belong to agent_server.

use std::collections::HashSet;
use std::io::{self, Write};

use cliclack::{StringCursor, Theme, ThemeState};

use super::supporting_style;
use crate::langsmith::agent_server::{Message, ToolCall};

/// Longest tool result echoed to the terminal before it is cut.
const TOOL_RESULT_PREVIEW: usize = 200;

/// Hide the terminal cursor during a turn and restore it on every return path.
pub(crate) struct HiddenCursor(console::Term);

impl HiddenCursor {
    pub(crate) fn new() -> io::Result<Self> {
        let term = console::Term::stderr();
        if term.is_term() {
            term.hide_cursor()?;
        }
        Ok(Self(term))
    }
}

impl Drop for HiddenCursor {
    fn drop(&mut self) {
        if self.0.is_term() {
            let _ = self.0.show_cursor();
        }
    }
}

/// Apply the compact layout only while the message input is active.
pub(crate) fn with_input<T>(read: impl FnOnce() -> T) -> T {
    struct ResetTheme;
    impl Drop for ResetTheme {
        fn drop(&mut self) {
            cliclack::reset_theme();
        }
    }
    cliclack::set_theme(ChatInputTheme {
        colors: console::colors_enabled_stderr(),
    });
    let _reset = ResetTheme;
    read()
}

struct ChatInputTheme {
    colors: bool,
}

impl ChatInputTheme {
    fn submitted_message(&self, text: &str) -> String {
        let text = format!("❯ {text}");
        if !self.colors {
            return text;
        }
        let background = console::Style::new()
            .on_color256(236)
            .white()
            .force_styling(true);
        text.split('\n')
            .map(|line| {
                // Fill the remaining terminal cells with the active background.
                // Unlike padding spaces, this does not alter copied message text.
                background.apply_to(format!("{line}\x1b[K")).to_string()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn format_cursor(
        &self,
        state: &ThemeState,
        cursor: &StringCursor,
        style: console::Style,
    ) -> String {
        match state {
            ThemeState::Active | ThemeState::Error(_) => self.cursor_with_style(cursor, &style),
            _ => style.apply_to(cursor).to_string(),
        }
    }
}

impl Theme for ChatInputTheme {
    fn format_header(&self, state: &ThemeState, _prompt: &str) -> String {
        if matches!(state, ThemeState::Submit) {
            String::new()
        } else {
            "❯ ".to_string()
        }
    }

    fn format_input(&self, state: &ThemeState, cursor: &StringCursor) -> String {
        if matches!(state, ThemeState::Submit) {
            self.submitted_message(&cursor.to_string())
        } else {
            self.format_cursor(state, cursor, console::Style::new())
        }
    }

    fn format_placeholder(&self, state: &ThemeState, cursor: &StringCursor) -> String {
        if matches!(state, ThemeState::Cancel) {
            String::new()
        } else {
            self.format_cursor(state, cursor, supporting_style(self.colors))
        }
    }

    fn format_footer_with_message(&self, state: &ThemeState, _message: &str) -> String {
        match state {
            ThemeState::Error(error) => {
                format!("\n{}\n\n", supporting_style(self.colors).apply_to(error))
            }
            ThemeState::Cancel => "\nOperation cancelled.\n\n".to_string(),
            _ => "\n\n".to_string(),
        }
    }
}

/// Show a status outside the closed setup frame.
pub(crate) fn show_notice(message: &str) -> io::Result<()> {
    writeln!(
        io::stderr(),
        "{}\n",
        supporting_style(console::colors_enabled_stderr()).apply_to(message)
    )
}

/// Report a failed run without ending the session.
pub(crate) fn show_run_failure(message: &str, hint: Option<&str>) -> io::Result<()> {
    write!(
        io::stderr(),
        "{}",
        run_failure_text(message, hint, console::colors_enabled_stderr())
    )
}

fn run_failure_text(message: &str, hint: Option<&str>, colors: bool) -> String {
    let label = console::Style::new().red().force_styling(colors);
    let mut out = format!("{} {message}\n", label.apply_to("Run failed:"));
    if let Some(hint) = hint {
        out.push_str(&format!("{}\n", supporting_style(colors).apply_to(hint)));
    }
    out.push('\n');
    out
}

/// Copyable, unindented streamed text. Callers supply sanitized content.
/// Use the prompt stream without holding its lock while the spinner runs.
struct ChatTranscript<W: Write> {
    out: W,
    colors: bool,
    started: bool,
    at_line_start: bool,
}

impl<W: Write> ChatTranscript<W> {
    pub(crate) fn new(out: W, colors: bool) -> Self {
        Self {
            out,
            colors,
            started: false,
            at_line_start: true,
        }
    }

    pub(crate) fn text(&mut self, text: &str) -> io::Result<()> {
        if !text.is_empty() {
            self.out.write_all(text.as_bytes())?;
            self.started = true;
            self.at_line_start = text.ends_with('\n');
        }
        self.out.flush()
    }

    pub(crate) fn line(&mut self, text: &str) -> io::Result<()> {
        self.break_line()?;
        writeln!(self.out, "{}", supporting_style(self.colors).apply_to(text))?;
        self.started = true;
        self.out.flush()
    }

    fn break_line(&mut self) -> io::Result<()> {
        if !self.at_line_start {
            writeln!(self.out)?;
            self.at_line_start = true;
        }
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> io::Result<()> {
        if self.started {
            self.break_line()?;
            writeln!(self.out)?;
            self.started = false;
        }
        self.out.flush()
    }
}

/// Writes a streamed run to the terminal.
pub(crate) struct TurnRenderer<W: Write> {
    transcript: ChatTranscript<W>,
    /// Whether any assistant text has been written this turn.
    printed_text: bool,
    /// Tool calls already announced, by id or stream index.
    announced_tool_calls: HashSet<String>,
}

impl<W: Write> TurnRenderer<W> {
    pub(crate) fn new(out: W) -> Self {
        Self {
            transcript: ChatTranscript::new(out, console::colors_enabled_stderr()),
            printed_text: false,
            announced_tool_calls: HashSet::new(),
        }
    }

    pub(crate) fn render_message(&mut self, message: &Message) -> io::Result<()> {
        match message {
            Message::Assistant { text, tool_calls } => {
                self.render_text(text)?;
                for ToolCall { key, name } in tool_calls {
                    if self.announced_tool_calls.insert(key.clone()) {
                        self.transcript.line(&format!("⚙ {}", sanitize(name)))?;
                    }
                }
            }
            Message::Tool { name, text } => {
                let name = sanitize(name);
                let preview = preview(&sanitize(text), TOOL_RESULT_PREVIEW);
                self.transcript.line(&format!("↳ {name}: {preview}"))?;
            }
        }
        Ok(())
    }

    fn render_text(&mut self, text: &str) -> io::Result<()> {
        let text = sanitize(text);
        if !text.is_empty() {
            self.transcript.text(&text)?;
            self.printed_text = true;
        }
        Ok(())
    }

    pub(crate) fn finish(&mut self) -> io::Result<()> {
        self.transcript.finish()
    }

    pub(crate) fn finish_turn(&mut self, reply: Option<&str>) -> io::Result<()> {
        // Graphs without token chunks still leave new assistant output in state.
        if !self.printed_text {
            if let Some(reply) = reply {
                self.render_text(reply)?;
            }
        }
        self.finish()
    }
}

/// Drop control characters so server-provided text cannot drive the terminal.
pub(crate) fn sanitize(text: &str) -> String {
    text.chars()
        .filter(|c| !c.is_control() || matches!(c, '\n' | '\t'))
        .collect()
}

fn preview(text: &str, limit: usize) -> String {
    let first_line = text.lines().next().unwrap_or_default().trim();
    let mut out: String = first_line.chars().take(limit).collect();
    if first_line.chars().count() > limit || text.lines().count() > 1 {
        out.push('…');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::langsmith::agent_server::ToolCall;

    #[test]
    fn run_failure_shows_the_message_then_the_fix() {
        assert_eq!(
            run_failure_text(
                "Missing credentials.",
                Some("Add `OPENAI_API_KEY=...`"),
                false
            ),
            "Run failed: Missing credentials.\nAdd `OPENAI_API_KEY=...`\n\n"
        );
    }

    #[test]
    fn chat_prompt_is_inline_with_a_blank_line_after_it() {
        use cliclack::{StringCursor, Theme, ThemeState};
        let theme = super::ChatInputTheme { colors: false };
        let mut cursor = StringCursor::default();
        for c in "Message the agent".chars() {
            cursor.insert(c);
        }
        let active = format!(
            "{}{}{}",
            theme.format_header(&ThemeState::Active, "You"),
            theme.format_placeholder(&ThemeState::Active, &cursor),
            theme.format_footer(&ThemeState::Active)
        );
        // cliclack adds a cursor cell at the end of active text.
        assert_eq!(
            console::strip_ansi_codes(&active),
            "❯ Message the agent \n\n"
        );
        let submitted = format!(
            "{}{}{}",
            theme.format_header(&ThemeState::Submit, "You"),
            theme.format_input(&ThemeState::Submit, &cursor),
            theme.format_footer(&ThemeState::Submit)
        );
        assert_eq!(
            console::strip_ansi_codes(&submitted),
            "❯ Message the agent\n\n"
        );
        assert_eq!(
            theme.format_footer(&ThemeState::Error("Type a message first".into())),
            "\nType a message first\n\n"
        );
        assert_eq!(
            theme.format_footer(&ThemeState::Cancel),
            "\nOperation cancelled.\n\n"
        );
        assert!(theme
            .format_placeholder(&ThemeState::Cancel, &cursor)
            .is_empty());
    }

    #[test]
    fn submitted_messages_fill_the_row_without_copyable_padding() {
        use cliclack::{StringCursor, Theme, ThemeState};
        let theme = super::ChatInputTheme { colors: true };
        let mut cursor = StringCursor::default();
        for c in "how are you\n你好\n\nnext".chars() {
            cursor.insert(c);
        }
        let output = theme.format_input(&ThemeState::Submit, &cursor);
        assert!(theme.format_header(&ThemeState::Submit, "You").is_empty());
        assert_eq!(
            console::strip_ansi_codes(&output),
            "❯ how are you\n你好\n\nnext"
        );
        for line in output.split('\n') {
            assert!(line.contains("\x1b[48;5;236m"), "{line:?}");
            assert!(line.ends_with("\x1b[K\x1b[0m"), "{line:?}");
        }
        assert_eq!(theme.format_footer(&ThemeState::Submit), "\n\n");
    }

    #[test]
    fn submitted_messages_have_a_plain_marker_without_color() {
        let output = super::ChatInputTheme { colors: false }.submitted_message("hello\nworld");
        assert_eq!(output, "❯ hello\nworld");
        assert!(!output.contains('\x1b'));
    }

    #[test]
    fn chat_placeholder_and_tool_notices_are_grey_but_replies_are_plain() {
        use cliclack::{StringCursor, Theme, ThemeState};
        let mut cursor = StringCursor::default();
        cursor.insert('x');
        let output =
            super::ChatInputTheme { colors: true }.format_placeholder(&ThemeState::Submit, &cursor);
        assert!(output.contains(&super::supporting_style(true).apply_to("x").to_string()));
        let mut transcript = super::ChatTranscript::new(Vec::new(), true);
        transcript.text("reply").unwrap();
        transcript.line("tool").unwrap();
        let output = String::from_utf8(transcript.out).unwrap();
        assert_eq!(
            output,
            format!(
                "reply\n{}\n",
                super::supporting_style(true).apply_to("tool")
            )
        );
    }

    #[test]
    fn chat_transcript_preserves_chunk_and_line_boundaries() {
        for chunks in [
            vec!["Hey!\nHow can I help?"],
            vec!["He", "", "y!\n", "How can", " I help?"],
        ] {
            let mut section = super::ChatTranscript::new(Vec::new(), false);
            for chunk in chunks {
                section.text(chunk).unwrap();
            }
            section.line("⚙ query_db").unwrap();
            section.line("↳ query_db: 3 rows…").unwrap();
            section.text("Done.\n").unwrap();
            section.finish().unwrap();
            section.finish().unwrap();
            assert_eq!(
                String::from_utf8(section.out).unwrap(),
                "Hey!\nHow can I help?\n⚙ query_db\n↳ query_db: 3 rows…\nDone.\n\n"
            );
        }
    }

    #[test]
    fn empty_stream_produces_no_transcript() {
        let mut section = super::ChatTranscript::new(Vec::new(), false);
        section.text("").unwrap();
        section.finish().unwrap();
        assert!(section.out.is_empty());
    }

    #[test]
    fn tool_output_cannot_send_terminal_control_commands() {
        let mut output = Vec::new();
        let mut renderer = TurnRenderer::new(&mut output);
        renderer
            .render_message(&Message::Tool {
                name: "read\u{1b}[2J".into(),
                text: "file\u{1b}]52;c;YQ==\u{7} content\rspoof".into(),
            })
            .unwrap();
        let raw = String::from_utf8(output).unwrap();
        assert!(!raw.contains("\u{1b}[2J"), "unsafe tool name: {raw:?}");
        assert!(!raw.contains("\u{1b}]52"), "unsafe tool result: {raw:?}");
        assert!(!raw.contains('\u{7}'));
        assert!(!raw.contains('\r'));
    }

    #[test]
    fn tool_call_names_cannot_send_terminal_control_commands() {
        let mut output = Vec::new();
        TurnRenderer::new(&mut output)
            .render_message(&Message::Assistant {
                text: String::new(),
                tool_calls: vec![ToolCall {
                    key: "call".into(),
                    name: "read\u{1b}[2J".into(),
                }],
            })
            .unwrap();
        assert!(!String::from_utf8(output).unwrap().contains("\u{1b}[2J"));
    }

    #[test]
    fn renderer_streams_text_then_announces_tool_calls_once() {
        let mut out = Vec::new();
        {
            let mut renderer = TurnRenderer::new(&mut out);
            renderer
                .render_message(&Message::Assistant {
                    text: "Let me check".into(),
                    tool_calls: vec![],
                })
                .unwrap();
            let call = Message::Assistant {
                text: String::new(),
                tool_calls: vec![ToolCall {
                    key: "c1".into(),
                    name: "query_db".into(),
                }],
            };
            renderer.render_message(&call).unwrap();
            renderer.render_message(&call).unwrap();
            renderer
                .render_message(&Message::Tool {
                    name: "query_db".into(),
                    text: "3 rows\nmore".into(),
                })
                .unwrap();
            renderer.finish().unwrap();
            assert!(renderer.printed_text);
        }
        let text = console::strip_ansi_codes(std::str::from_utf8(&out).unwrap()).to_string();
        assert!(text.contains("Let me check"));
        assert_eq!(text.matches("⚙ query_db").count(), 1);
        assert!(text.contains("↳ query_db: 3 rows…"));
    }

    #[test]
    fn sanitize_strips_terminal_control_sequences() {
        assert_eq!(sanitize("ok\u{1b}[31m red\n\ttab"), "ok[31m red\n\ttab");
    }

    #[test]
    fn preview_keeps_the_first_line_and_marks_cuts() {
        assert_eq!(preview("short", 10), "short");
        assert_eq!(preview("first\nsecond", 10), "first…");
        assert_eq!(preview("abcdefghijk", 5), "abcde…");
    }

    #[test]
    fn fallback_is_used_only_when_no_text_was_streamed() {
        for streamed in [false, true] {
            let mut output = Vec::new();
            let mut renderer = TurnRenderer::new(&mut output);
            if streamed {
                renderer
                    .render_message(&Message::Assistant {
                        text: "streamed".into(),
                        tool_calls: vec![],
                    })
                    .unwrap();
            }
            renderer.finish_turn(Some("fallback")).unwrap();
            assert_eq!(
                String::from_utf8(output).unwrap(),
                if streamed {
                    "streamed\n\n"
                } else {
                    "fallback\n\n"
                }
            );
        }
    }

    #[test]
    fn absent_fallback_leaves_an_empty_turn_empty() {
        let mut output = Vec::new();
        TurnRenderer::new(&mut output).finish_turn(None).unwrap();
        assert!(output.is_empty());
    }
}
