//! Static CLI guidance sequences.

use std::io::{self, Write};

use anyhow::{bail, Context, Result};
use cliclack::{intro, note, Theme, ThemeState};

pub(crate) mod chat;

/// Braille wordmark printed above Clack intros for `mda init` / `mda deploy`.
pub(crate) const WORDMARK: &str = concat!(
    " ⡷⢾ ⣎⣱ ⡷⣸ ⣎⣱ ⡎⠑ ⣏⡉ ⡏⢱           \n",
    " ⠇⠸ ⠇⠸ ⠇⠹ ⠇⠸ ⠣⠝ ⠧⠤ ⠧⠜           \n",
    " ⡏⢱ ⣏⡉ ⣏⡉ ⣏⡱   ⣎⣱ ⡎⠑ ⣏⡉ ⡷⣸ ⢹⠁ ⢎⡑\n",
    " ⠧⠜ ⠧⠤ ⠧⠤ ⠇    ⠇⠸ ⠣⠝ ⠧⠤ ⠇⠹ ⠸  ⠢⠜",
);

/// Wordmark plus `v{package}` Clack intro, matching `mda init`.
pub(crate) fn show_wordmark_intro() -> Result<()> {
    writeln!(io::stderr().lock(), "\n{WORDMARK}").context("failed to show CLI wordmark")?;
    intro(version_label(console::colors_enabled_stderr()))
        .context("failed to start CLI session")?;
    Ok(())
}

fn version_label(colors: bool) -> String {
    supporting_style(colors)
        .apply_to(format!("v{}", crate::version::package_version()))
        .to_string()
}

/// Horizontal room `cliclack::note` spends on its box: the border columns plus
/// the message indent. A body line wider than the terminal minus this padding
/// is hard-wrapped inside the box.
const NOTE_BODY_PADDING: usize = 6;

/// Print a titled block of labeled URLs, keeping every URL on one line.
///
/// `cliclack::note` hard-wraps its body to the terminal width and indents each
/// line inside a box, which splits a long URL across lines with border
/// characters in between — the URL stops being clickable and copies back
/// broken. So the box is used only while every link fits; otherwise the links
/// are printed unindented, where the terminal soft-wraps them and each URL
/// stays a single selectable, clickable run.
///
/// A link with an empty label prints as the bare URL.
pub(crate) fn note_links(title: &str, links: &[(&str, &str)]) -> Result<()> {
    note_links_with_section(title, links, "", &[])
}

/// Add an optional group inside the same link note, preserving URL wrapping behavior.
pub(crate) fn note_links_with_section(
    title: &str,
    links: &[(&str, &str)],
    section_title: &str,
    section_links: &[(&str, &str)],
) -> Result<()> {
    let lines = sectioned_link_lines(links, section_title, section_links);
    if lines.is_empty() {
        bail!("link notes require at least one link");
    }

    if links_fit(&lines, console::Term::stderr().size().1 as usize) {
        return note(title, lines.join("\n")).context("failed to show CLI output");
    }

    let rendered = render_link_lines(title, &lines, console::colors_enabled_stderr());
    console::Term::stderr()
        .write_str(&rendered)
        .context("failed to show CLI output")
}

fn sectioned_link_lines(
    links: &[(&str, &str)],
    section_title: &str,
    section_links: &[(&str, &str)],
) -> Vec<String> {
    let mut lines = link_lines(links);
    if !section_links.is_empty() {
        lines.push(String::new());
        lines.push(section_title.to_string());
        lines.extend(
            link_lines(section_links)
                .into_iter()
                .map(|line| format!("  {line}")),
        );
    }
    lines
}

/// One line per link, labels padded into a column so the URLs line up.
fn link_lines(links: &[(&str, &str)]) -> Vec<String> {
    let label_width = links
        .iter()
        .map(|(label, _)| console::measure_text_width(label))
        .max()
        .unwrap_or(0);

    links
        .iter()
        .map(|(label, url)| {
            if label_width == 0 {
                url.to_string()
            } else {
                format!("{label:<label_width$}  {url}")
            }
        })
        .collect()
}

/// Whether every link line survives `cliclack::note` unwrapped.
fn links_fit(lines: &[String], terminal_width: usize) -> bool {
    lines
        .iter()
        .all(|line| console::measure_text_width(line) + NOTE_BODY_PADDING <= terminal_width)
}

/// Render the unboxed form: the Clack step heading, then the links at column
/// zero so nothing but the terminal's own soft wrap touches them.
fn render_link_lines(title: &str, lines: &[String], colors: bool) -> String {
    let marker = console::Style::new().green().force_styling(colors);
    let mut rendered = format!(
        "{}  {}\n",
        marker.apply_to("◇"),
        heading_style(colors).apply_to(title)
    );
    for line in lines {
        rendered.push_str(line);
        rendered.push('\n');
    }
    rendered.push_str(&format!("{}\n", supporting_style(colors).apply_to("│")));
    rendered
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum SequenceItem {
    Section {
        heading: String,
        supporting_lines: Vec<String>,
    },
    AgentNote {
        title: String,
        body: String,
    },
}

struct GuidanceTheme {
    colors: bool,
}

impl Theme for GuidanceTheme {
    fn bar_color(&self, _state: &ThemeState) -> console::Style {
        supporting_style(self.colors)
    }

    fn input_style(&self, _state: &ThemeState) -> console::Style {
        supporting_style(self.colors)
    }
}

impl SequenceItem {
    pub(crate) fn section(
        heading: impl Into<String>,
        supporting: impl IntoIterator<Item = impl Into<String>>,
    ) -> Self {
        let mut supporting_lines = Vec::new();
        for text in supporting {
            let text = text.into();
            if text.is_empty() {
                supporting_lines.push(String::new());
            } else {
                supporting_lines.extend(text.lines().map(str::to_owned));
            }
        }

        Self::Section {
            heading: heading.into(),
            supporting_lines,
        }
    }

    pub(crate) fn agent_note(title: impl Into<String>, body: impl Into<String>) -> Self {
        Self::AgentNote {
            title: title.into(),
            body: body.into(),
        }
    }
}

pub(crate) fn supporting_style(colors: bool) -> console::Style {
    console::Style::new().color256(8).force_styling(colors)
}

pub(crate) fn heading_style(colors: bool) -> console::Style {
    console::Style::new().white().force_styling(colors)
}

/// Match clap's built-in `Commands:` / `Options:` heading (bold + underline).
pub fn help_heading(label: &str) -> String {
    let style = *clap::builder::styling::Styles::styled().get_header();
    format!("{style}{label}:{style:#}")
}

pub fn styled_help_template(examples: Option<&str>, docs: &str) -> String {
    let docs_section = format!("{}\n{docs}", help_heading("Docs"));
    let tail = match examples {
        Some(examples) => format!("{}\n{examples}\n\n{docs_section}", help_heading("Examples")),
        None => docs_section,
    };
    format!(
        "\
{{before-help}}{{about-with-newline}}
{{usage-heading}} {{usage}}

{{all-args}}

{tail}\
"
    )
}

pub(crate) fn show_sequence(items: &[SequenceItem]) -> Result<()> {
    write_sequence(items, true)
}

pub(crate) fn continue_sequence(items: &[SequenceItem]) -> Result<()> {
    write_sequence(items, false)
}

/// Continue static guidance into an interactive prompt without closing its bar.
pub(crate) fn continue_open_sequence(items: &[SequenceItem]) -> Result<()> {
    let rendered = render_sequence_with_end(items, false, console::colors_enabled_stderr(), false)?;
    console::Term::stderr()
        .write_str(&rendered)
        .context("failed to show CLI output")
}

fn write_sequence(items: &[SequenceItem], leading_blank_line: bool) -> Result<()> {
    let rendered = render_sequence(items, leading_blank_line, console::colors_enabled_stderr())?;
    console::Term::stderr()
        .write_str(&rendered)
        .context("failed to show CLI output")
}

fn render_sequence(
    items: &[SequenceItem],
    leading_blank_line: bool,
    colors: bool,
) -> Result<String> {
    render_sequence_with_end(items, leading_blank_line, colors, true)
}

fn render_sequence_with_end(
    items: &[SequenceItem],
    leading_blank_line: bool,
    colors: bool,
    close: bool,
) -> Result<String> {
    if items.is_empty() {
        bail!("CLI output sequences require at least one item");
    }
    if items.iter().any(invalid_item) {
        bail!("CLI output sequence items require a single-line title and non-empty content");
    }

    let marker = console::Style::new().green().force_styling(colors);
    let heading = heading_style(colors);
    let supporting = supporting_style(colors);
    let note_theme = GuidanceTheme { colors };
    let mut rendered = if leading_blank_line {
        String::from("\n")
    } else {
        String::new()
    };

    for (index, item) in items.iter().enumerate() {
        let final_item = close && index + 1 == items.len();
        match item {
            SequenceItem::Section {
                heading: heading_text,
                supporting_lines,
            } => {
                rendered.push_str(&format!(
                    "{}  {}\n",
                    marker.apply_to("◇"),
                    heading.apply_to(heading_text)
                ));
                for (line_index, line) in supporting_lines.iter().enumerate() {
                    let final_line = final_item && line_index + 1 == supporting_lines.len();
                    let connector = if final_line { "└" } else { "│" };
                    rendered.push_str(&format!(
                        "{}  {}\n",
                        supporting.apply_to(connector),
                        supporting.apply_to(line)
                    ));
                }
                if !final_item {
                    rendered.push_str(&format!("{}  \n", supporting.apply_to("│")));
                }
            }
            SequenceItem::AgentNote { title, body } => {
                let symbol = supporting.apply_to("├").to_string();
                let title = supporting.apply_to(title).to_string();
                rendered.push_str(
                    &note_theme.format_note_with_symbol(final_item, &symbol, &title, body),
                );
            }
        }
    }
    if close {
        rendered.push('\n');
    }
    Ok(rendered)
}

fn invalid_item(item: &SequenceItem) -> bool {
    match item {
        SequenceItem::Section {
            heading,
            supporting_lines,
        } => {
            heading.is_empty()
                || heading.contains(['\r', '\n'])
                || supporting_lines.is_empty()
                || supporting_lines.iter().any(|line| line.contains('\r'))
        }
        SequenceItem::AgentNote { title, body } => {
            title.is_empty() || title.contains(['\r', '\n']) || body.is_empty()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        help_heading, link_lines, links_fit, render_link_lines, render_sequence,
        sectioned_link_lines, styled_help_template, version_label, SequenceItem, WORDMARK,
    };

    #[test]
    fn setup_frame_closes_before_plain_chat() {
        let instruction = "Type a message, /new for a fresh thread, /exit to leave.";
        let output = super::render_sequence(
            &[SequenceItem::section(
                "Conversation",
                ["Thread: test", instruction],
            )],
            false,
            false,
        )
        .unwrap();
        assert_eq!(
            output,
            format!("◇  Conversation\n│  Thread: test\n└  {instruction}\n\n")
        );
    }

    #[test]
    fn empty_link_section_keeps_existing_output() {
        let links = [("Agent Server", "https://agent.example")];
        assert_eq!(
            sectioned_link_lines(&links, "HTTP channels", &[]),
            link_lines(&links)
        );
    }

    #[test]
    fn channel_links_are_grouped_inside_deployment_without_splitting_long_urls() {
        let url = "https://agent.example/channels/orders/events";
        let lines = sectioned_link_lines(
            &[("Agent Server", "https://agent.example")],
            "HTTP channels",
            &[("orders", url)],
        );
        assert_eq!(
            lines,
            [
                "Agent Server  https://agent.example",
                "",
                "HTTP channels",
                "  orders  https://agent.example/channels/orders/events",
            ]
        );
        assert!(!links_fit(&lines, 40));
        assert!(links_fit(&lines, 120));
        assert!(render_link_lines("Deployment", &lines, false).contains(url));
    }

    #[test]
    fn link_lines_align_urls_under_padded_labels() {
        assert_eq!(
            link_lines(&[
                ("Agent Server", "https://agent.us.langgraph.app"),
                ("Dashboard", "https://smith.langchain.com/o/t-1"),
            ]),
            [
                "Agent Server  https://agent.us.langgraph.app",
                "Dashboard     https://smith.langchain.com/o/t-1",
            ]
        );
    }

    #[test]
    fn link_lines_drop_the_label_column_when_no_link_is_labeled() {
        assert_eq!(
            link_lines(&[("", "https://smith.langchain.com/o/t-1")]),
            ["https://smith.langchain.com/o/t-1"]
        );
    }

    #[test]
    fn links_fit_only_while_the_note_box_leaves_room() {
        let lines = vec!["Dashboard  https://smith.langchain.com".to_string()];

        assert!(links_fit(&lines, 44));
        assert!(!links_fit(&lines, 43));
    }

    #[test]
    fn unboxed_links_start_at_column_zero_under_a_step_heading() {
        let lines = vec!["Agent Server  https://agent.us.langgraph.app".to_string()];

        assert_eq!(
            render_link_lines("Deployment", &lines, false),
            concat!(
                "◇  Deployment\n",
                "Agent Server  https://agent.us.langgraph.app\n",
                "│\n",
            )
        );
    }

    #[test]
    fn sequence_owns_its_shape_and_spacing() {
        let items = [
            SequenceItem::section("First heading", ["first detail\nsecond detail"]),
            SequenceItem::section("Second heading", ["final detail"]),
        ];

        assert_eq!(
            render_sequence(&items, true, false).unwrap(),
            concat!(
                "\n",
                "◇  First heading\n",
                "│  first detail\n",
                "│  second detail\n",
                "│  \n",
                "◇  Second heading\n",
                "└  final detail\n",
                "\n",
            )
        );
    }

    #[test]
    fn sequence_styles_headings_white_and_supporting_lines_gray() {
        let items = [SequenceItem::section("Heading", ["detail"])];

        assert_eq!(
            render_sequence(&items, true, true).unwrap(),
            concat!(
                "\n",
                "\x1b[32m◇\x1b[0m  \x1b[37mHeading\x1b[0m\n",
                "\x1b[38;5;8m└\x1b[0m  \x1b[38;5;8mdetail\x1b[0m\n",
                "\n",
            )
        );
    }

    #[test]
    fn sequence_closes_a_final_agent_note() {
        let items = [SequenceItem::agent_note(
            "Agent note",
            "Follow these instructions",
        )];

        let rendered = render_sequence(&items, true, false).unwrap();

        assert!(rendered.ends_with("╯\n\n"));
        assert!(rendered.trim_end().lines().last().unwrap().starts_with('└'));
    }

    #[test]
    fn version_label_is_the_package_version() {
        assert_eq!(
            version_label(false),
            format!("v{}", crate::version::package_version())
        );
    }

    #[test]
    fn wordmark_is_the_managed_deep_agent_prefix() {
        assert_eq!(
            WORDMARK,
            concat!(
                " ⡷⢾ ⣎⣱ ⡷⣸ ⣎⣱ ⡎⠑ ⣏⡉ ⡏⢱           \n",
                " ⠇⠸ ⠇⠸ ⠇⠹ ⠇⠸ ⠣⠝ ⠧⠤ ⠧⠜           \n",
                " ⡏⢱ ⣏⡉ ⣏⡉ ⣏⡱   ⣎⣱ ⡎⠑ ⣏⡉ ⡷⣸ ⢹⠁ ⢎⡑\n",
                " ⠧⠜ ⠧⠤ ⠧⠤ ⠇    ⠇⠸ ⠣⠝ ⠧⠤ ⠇⠹ ⠸  ⠢⠜",
            )
        );
    }

    #[test]
    fn help_heading_matches_clap_header_style() {
        let style = *clap::builder::styling::Styles::styled().get_header();
        let rendered = help_heading("Examples");
        assert_eq!(rendered, format!("{style}Examples:{style:#}"));
        assert!(rendered.contains("\u{1b}[4m"), "{rendered:?}");
        assert_eq!(help_heading("Docs"), format!("{style}Docs:{style:#}"));
    }

    #[test]
    fn styled_help_template_puts_examples_above_docs() {
        let template = styled_help_template(
            Some("  mda connections catalog"),
            "  https://example.com/docs",
        );
        let examples = template
            .find("Examples:")
            .unwrap_or_else(|| panic!("{template}"));
        let docs = template
            .find("Docs:")
            .unwrap_or_else(|| panic!("{template}"));
        assert!(examples < docs, "{template}");
        assert!(
            template.contains("  mda connections catalog\n\n"),
            "{template}"
        );
        assert!(
            template.contains("  https://example.com/docs"),
            "{template}"
        );
        assert!(template.contains("{all-args}"), "{template}");
    }
}
