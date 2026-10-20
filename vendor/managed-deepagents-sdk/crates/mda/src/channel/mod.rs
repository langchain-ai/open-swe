//! Scaffold external messaging channels in an initialized project.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};

use crate::cli_output::{show_sequence, SequenceItem};
use crate::project::{self, Language};

struct InitSummary<'a> {
    title: &'static str,
    path: &'a Path,
    next_step: &'static str,
}

fn init_summary(path: &Path) -> InitSummary<'_> {
    InitSummary {
        title: "Slack channel added",
        path,
        next_step: "Your agent will be added to Slack on the next deploy.",
    }
}

/// Show the added Slack channel and its deployment next step.
pub fn show_added(path: &Path) -> Result<()> {
    let summary = init_summary(path);
    show_sequence(&[SequenceItem::section(
        summary.title,
        [
            summary.path.display().to_string(),
            String::new(),
            summary.next_step.to_string(),
        ],
    )])
}

/// Create a Slack channel definition in the project rooted at `root`.
///
/// Returns the created path relative to the project root.
///
/// # Errors
///
/// Returns an error when `root` is not an initialized MDA project, the
/// `channels` directory is unsafe, the channel already exists, or the file
/// cannot be written.
pub fn init_slack(root: &Path) -> Result<PathBuf> {
    let project = project::load(root)?;
    let (relative, contents) = match project.language {
        Language::Python => (
            PathBuf::from("channels/slack.py"),
            concat!(
                "from managed_deepagents import channels\n\n",
                "channel = channels.slack()\n",
            ),
        ),
        Language::TypeScript => (
            PathBuf::from("channels/slack.ts"),
            concat!(
                "import { channels } from \"managed-deepagents\";\n\n",
                "export const channel = channels.slack({});\n",
            ),
        ),
    };
    let channels = project.root.join("channels");
    ensure_channels_directory(&channels)?;

    write_new_file(&project.root.join(&relative), &relative, contents)?;
    Ok(relative)
}

fn write_new_file(target: &Path, relative: &Path, contents: &str) -> Result<()> {
    let mut file = match OpenOptions::new().write(true).create_new(true).open(target) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            bail!("{} already exists", relative.display())
        }
        Err(error) => {
            return Err(error).with_context(|| format!("failed to create {}", relative.display()))
        }
    };
    if let Err(write_error) = file.write_all(contents.as_bytes()) {
        drop(file);
        match fs::remove_file(target) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(error).with_context(|| {
                    format!(
                        "failed to remove incomplete {} after write failed: {write_error}",
                        relative.display()
                    )
                });
            }
        }
        return Err(write_error).with_context(|| format!("failed to write {}", relative.display()));
    }
    Ok(())
}

fn ensure_channels_directory(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            bail!("refusing unsafe channels directory")
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).with_context(|| format!("failed to create {}", path.display()))
        }
        Err(error) => Err(error).with_context(|| format!("failed to inspect {}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_slack_creates_python_channel() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("agent.py"),
            "agent = define_deep_agent(name=\"support-agent\")\n",
        )
        .unwrap();

        let created = init_slack(root.path()).unwrap();
        let contents = fs::read_to_string(root.path().join(&created)).unwrap();

        assert_eq!(
            (created, contents),
            (
                PathBuf::from("channels/slack.py"),
                concat!(
                    "from managed_deepagents import channels\n\n",
                    "channel = channels.slack()\n",
                )
                .to_string(),
            )
        );
    }

    #[test]
    fn init_slack_creates_typescript_channel() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("agent.ts"),
            "export const agent = defineDeepAgent({ name: \"research-agent\" });\n",
        )
        .unwrap();

        let created = init_slack(root.path()).unwrap();
        let contents = fs::read_to_string(root.path().join(&created)).unwrap();

        assert_eq!(
            (created, contents),
            (
                PathBuf::from("channels/slack.ts"),
                concat!(
                    "import { channels } from \"managed-deepagents\";\n\n",
                    "export const channel = channels.slack({});\n",
                )
                .to_string(),
            )
        );
    }

    #[test]
    fn init_summary_uses_standard_slack_copy() {
        let summary = init_summary(Path::new("channels/slack.py"));

        assert_eq!(
            (summary.title, summary.path, summary.next_step),
            (
                "Slack channel added",
                Path::new("channels/slack.py"),
                "Your agent will be added to Slack on the next deploy.",
            )
        );
    }

    #[test]
    fn init_slack_requires_an_initialized_project() {
        let root = tempfile::tempdir().unwrap();

        let error = init_slack(root.path()).unwrap_err();

        assert!(error.to_string().contains("no agent entry file found"));
    }

    #[test]
    fn init_slack_preserves_existing_channel() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("agent.py"),
            "agent = define_deep_agent(name=\"support-agent\")\n",
        )
        .unwrap();
        fs::create_dir(root.path().join("channels")).unwrap();
        fs::write(root.path().join("channels/slack.py"), "keep me\n").unwrap();

        let error = init_slack(root.path()).unwrap_err();
        let contents = fs::read_to_string(root.path().join("channels/slack.py")).unwrap();

        assert_eq!(
            (error.to_string(), contents),
            (
                "channels/slack.py already exists".to_string(),
                "keep me\n".to_string(),
            )
        );
    }

    #[cfg(unix)]
    #[test]
    fn init_slack_rejects_channels_symlink() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("agent.py"),
            "agent = define_deep_agent(name=\"support-agent\")\n",
        )
        .unwrap();
        symlink(outside.path(), root.path().join("channels")).unwrap();

        let error = init_slack(root.path()).unwrap_err();

        assert_eq!(
            (error.to_string(), outside.path().join("slack.py").exists()),
            ("refusing unsafe channels directory".to_string(), false)
        );
    }
}
