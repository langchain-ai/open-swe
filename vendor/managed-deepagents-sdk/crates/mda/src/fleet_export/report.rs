//! What an export could not carry over, and what the developer should know.

use serde::Serialize;

use crate::fleet_export::EXPORT_FILES_DIR;
use crate::project::Language;

/// Explains, above the connections, that MDA has no OAuth app to lend.
const OAUTH_INTRO: &str = "Each OAuth connection below needs an OAuth app that you register with the service yourself. For services listed by `mda connections catalog`, MDA already has the service's endpoints and default scopes, so you only pass your app's client ID and secret. For any other service, also pass its authorization and token URLs (`--authorize-url`, `--token-url`).";

/// A Fleet feature the agent uses that export can't carry over yet.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct NotExported {
    pub feature: &'static str,
    /// A few words for the console, such as `2 skills`.
    pub summary: String,
    /// The full list, for FLEET_EXPORT.md.
    pub detail: String,
}

/// A connection the exported project reads that the developer creates, with
/// the steps to create it in order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct ConnectionToCreate {
    pub slug: String,
    /// Agent-owned connections need the deployment to exist first.
    pub after_first_deploy: bool,
    /// What the connection signs in to, and with whose account.
    pub summary: String,
    pub steps: Vec<String>,
}

/// Findings collected across every mapper, in mapper order.
#[derive(Debug, Default, Serialize)]
pub(crate) struct ExportReport {
    /// Fleet features the project doesn't have.
    pub not_exported: Vec<NotExported>,
    /// Things the developer must act on before deploying.
    pub warnings: Vec<String>,
    /// Fleet state that never travels with an export.
    pub left_behind: Vec<String>,
    /// Printed after a successful export as information, not a warning.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
    /// Connections the exported tools and sandbox read, with the steps that
    /// create each.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub connections: Vec<ConnectionToCreate>,
    /// What the developer should know about `sandbox/`. Printed as warnings
    /// and listed under "Sandbox" in FLEET_EXPORT.md.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub sandbox: Vec<String>,
    /// Steps that need the deployment to exist, printed as the console's last
    /// next step. FLEET_EXPORT.md has them under "Connections to create".
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub after_first_deploy: Vec<String>,
    /// Exported tool names longer than some model providers accept. The
    /// console gives the count; FLEET_EXPORT.md lists them.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub long_tool_names: Vec<String>,
    /// What the developer should know about `skills/`. Listed under "Skills"
    /// in FLEET_EXPORT.md. The console prints only the first, so it says where
    /// the skills went.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub skills: Vec<String>,
    /// What the developer should know about memory. Listed under "Memory" in
    /// FLEET_EXPORT.md.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub memory: Vec<String>,
    /// Fleet memory files for `mda memory upload` to upload after the first
    /// deploy.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub memory_files: Vec<String>,
}

impl ExportReport {
    /// Leaves a feature out of the project. The export still completes.
    pub(crate) fn skip(
        &mut self,
        feature: &'static str,
        summary: impl Into<String>,
        detail: impl Into<String>,
    ) {
        self.not_exported.push(NotExported {
            feature,
            summary: summary.into(),
            detail: detail.into(),
        });
    }

    /// Printed after a successful export and listed under "Before you deploy"
    /// in FLEET_EXPORT.md.
    pub(crate) fn warn(&mut self, warning: impl Into<String>) {
        self.warnings.push(warning.into());
    }

    /// Printed to the console only.
    pub(crate) fn note(&mut self, note: impl Into<String>) {
        self.notes.push(note.into());
    }

    /// Printed after a successful export and listed under "Left behind" in
    /// FLEET_EXPORT.md.
    pub(crate) fn leave_behind(&mut self, item: impl Into<String>) {
        self.left_behind.push(item.into());
    }

    /// Listed under "Connections to create" in FLEET_EXPORT.md only.
    pub(crate) fn connection(&mut self, item: ConnectionToCreate) {
        self.connections.push(item);
    }

    /// Printed after a successful export and listed under "Sandbox" in
    /// FLEET_EXPORT.md.
    pub(crate) fn sandbox(&mut self, item: impl Into<String>) {
        self.sandbox.push(item.into());
    }

    pub(crate) fn skill(&mut self, item: impl Into<String>) {
        self.skills.push(item.into());
    }

    pub(crate) fn memory(&mut self, item: impl Into<String>) {
        self.memory.push(item.into());
    }

    /// The console line naming what wasn't exported, if anything was left out.
    pub(crate) fn not_exported_summary(&self) -> Option<String> {
        if self.not_exported.is_empty() {
            return None;
        }
        let items: Vec<String> = self
            .not_exported
            .iter()
            .map(|item| format!("{} ({})", item.feature, item.summary))
            .collect();
        Some(format!("Not exported: {}", items.join(", ")))
    }

    /// `FLEET_EXPORT.md`: what the developer needs to know about the export,
    /// written so a person or a coding agent can follow it top to bottom.
    /// `language` picks the docs pages the sections link to.
    pub(crate) fn markdown(&self, agent_name: &str, language: Language) -> String {
        let mut text = format!(
            "# Exported from Fleet\n\nGenerated by `mda init --from` from the Fleet agent \"{agent_name}\".\n"
        );
        let not_exported: Vec<String> = self
            .not_exported
            .iter()
            .map(|item| format!("{}: {}", item.feature, item.detail))
            .collect();
        list_section(&mut text, "Not exported", None, &not_exported);
        list_section(&mut text, "Before you deploy", None, &self.warnings);
        self.connections_section(&mut text, language);
        list_section(
            &mut text,
            "Tool names to shorten",
            None,
            &self.long_tool_names,
        );
        list_section(
            &mut text,
            "Sandbox",
            Some(("sandboxes", docs_url(language, "sandboxes"))),
            &self.sandbox,
        );
        list_section(&mut text, "Skills", None, &self.skills);
        self.memory_section(&mut text);
        list_section(&mut text, "Left behind", None, &self.left_behind);
        text
    }

    /// The memory notes, then the steps that upload Fleet's memory files.
    fn memory_section(&self, text: &mut String) {
        list_section(text, "Memory", None, &self.memory);
        if self.memory_files.is_empty() {
            return;
        }
        let files: Vec<String> = self
            .memory_files
            .iter()
            .map(|path| format!("`{path}`"))
            .collect();
        text.push_str(&format!(
            "\nThe Fleet agent's other memory files are in `{dir}/memories/`: {}. To move them into the deployment's memory:\n\n\
             1. Deploy: `mda deploy`\n\
             2. Upload: `mda memory upload {dir}/memories`. It skips files the deployment's memory already has.\n\
             3. Delete `{dir}/` once you've read the rest of it.\n",
            files.join(", "),
            dir = EXPORT_FILES_DIR,
        ));
    }

    /// One heading per connection, then its steps as a numbered list.
    fn connections_section(&self, text: &mut String, language: Language) {
        if self.connections.is_empty() {
            return;
        }
        text.push_str(&format!(
            "\n## Connections to create\n\nHow connections work: {}\n\n{OAUTH_INTRO}\n",
            docs_url(language, "connections")
        ));
        for item in &self.connections {
            let when = if item.after_first_deploy {
                "after your first deploy"
            } else {
                "before you deploy"
            };
            text.push_str(&format!(
                "\n### `{}`: {when}\n\n{}\n",
                item.slug, item.summary
            ));
            if !item.steps.is_empty() {
                text.push('\n');
                for (n, step) in item.steps.iter().enumerate() {
                    text.push_str(&format!("{}. {step}\n", n + 1));
                }
            }
        }
    }
}

/// A `## heading` and its items as a bullet list, after an optional docs link,
/// or nothing when there are no items.
fn list_section(text: &mut String, heading: &str, docs: Option<(&str, String)>, items: &[String]) {
    if items.is_empty() {
        return;
    }
    text.push_str(&format!("\n## {heading}\n\n"));
    if let Some((topic, url)) = docs {
        text.push_str(&format!("How {topic} work: {url}\n\n"));
    }
    for item in items {
        text.push_str(&format!("- {item}\n"));
    }
}

/// The MDA docs page on `topic` for the project's language.
fn docs_url(language: Language, topic: &str) -> String {
    let language = match language {
        Language::Python => "python",
        Language::TypeScript => "javascript",
    };
    format!("https://docs.langchain.com/langsmith/{language}/managed-deep-agents-{topic}")
}
