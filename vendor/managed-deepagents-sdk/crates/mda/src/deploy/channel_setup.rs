//! Channels that are set up outside the deployment: Slack through the trigger
//! server, GitHub through an App in Agent Auth, Linear through an OAuth app in
//! Agent Auth. Deploy prepares them before
//! uploading and sets them up once the revision is live, without knowing
//! which providers are involved.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde_json::Value;

use super::channel_app;
use super::github_channel::{self, GitHubChannel};
use super::helpers::deployment_runtime_url;
use super::linear_channel::{self, LinearChannel};
use super::trigger_channels::{self, PreparedTriggerChannel};
use crate::compile::ChannelRequirements;
use crate::langsmith;
use crate::project::Language;

/// The project's channels that need setup, validated.
pub(crate) struct ChannelSetup {
    trigger: Option<PreparedTriggerChannel>,
    github: Option<GitHubChannel>,
    linear: Option<LinearChannel>,
}

/// The live deployment the channels are set up for.
pub(crate) struct SetupTarget<'a> {
    pub(crate) client: &'a langsmith::Client,
    pub(crate) agent_server_url: &'a str,
    pub(crate) deployment_id: &'a str,
    pub(crate) assistant_id: &'a str,
    pub(crate) project_root: &'a Path,
    pub(crate) env_vars: &'a BTreeMap<String, String>,
}

impl ChannelSetup {
    /// Validate the channels' configuration. `wait` says whether deploy waits
    /// for the revision, which setup needs.
    pub(crate) fn prepare(
        requirements: &ChannelRequirements,
        wait: bool,
        project_root: &Path,
        project_files: &[PathBuf],
        language: Language,
        assistant_id: &str,
    ) -> Result<Self> {
        Ok(Self {
            trigger: trigger_channels::prepare(
                requirements,
                wait,
                project_root,
                language,
                assistant_id,
            )?,
            github: github_channel::prepare(requirements, wait, assistant_id)?,
            linear: linear_channel::prepare(
                requirements,
                wait,
                assistant_id,
                project_root,
                project_files,
            )?,
        })
    }

    /// Fail before anything is created or uploaded when setup could not
    /// finish, e.g. without a terminal to answer a question it needs.
    /// `existing_deployment` looks up the deployment being updated, only when
    /// a channel needs it.
    pub(crate) fn preflight(
        &self,
        client: &langsmith::Client,
        existing_deployment: impl FnOnce() -> Result<Option<Value>>,
        env_vars: &BTreeMap<String, String>,
    ) -> Result<()> {
        if self.github.is_none() && self.linear.is_none() && self.trigger.is_none() {
            return Ok(());
        }
        let existing = existing_deployment()?;
        let deployment_id = existing
            .as_ref()
            .and_then(|deployment| deployment.get("id")?.as_str());
        if let Some(trigger) = &self.trigger {
            trigger_channels::preflight(client, trigger, deployment_id, env_vars)?;
        }
        if let Some(github) = &self.github {
            let runtime_url = existing.as_ref().and_then(deployment_runtime_url);
            github_channel::preflight(
                client,
                github,
                deployment_id,
                runtime_url.as_deref(),
                env_vars,
            )?;
        }
        if let Some(linear) = &self.linear {
            linear_channel::preflight(client, linear, deployment_id, env_vars)?;
        }
        Ok(())
    }

    /// Whether setup needs the live Agent Server URL.
    pub(crate) fn needs_live_url(&self) -> bool {
        self.trigger.is_some() || self.github.is_some() || self.linear.is_some()
    }

    /// The trigger-server channel, which the build artifacts describe.
    pub(crate) fn trigger(&self) -> Option<&PreparedTriggerChannel> {
        self.trigger.as_ref()
    }

    /// Set the channels up for the live deployment. Each channel is set up
    /// even when another's setup fails; the failures end the deploy together
    /// once every channel had its turn.
    pub(crate) fn set_up(&self, target: &SetupTarget<'_>) -> Result<()> {
        let mut failures = Vec::new();
        if let Some(trigger) = &self.trigger {
            let result = trigger_channels::register(
                target.client,
                trigger,
                target.agent_server_url,
                target.deployment_id,
                target.assistant_id,
                target.env_vars,
            );
            collect_failure(&mut failures, "Slack", result);
        }
        if let Some(github) = &self.github {
            let result = github_channel::setup(
                target.client,
                github,
                target.agent_server_url,
                target.deployment_id,
                target.project_root,
                target.env_vars,
            );
            collect_failure(&mut failures, "GitHub", result);
        }
        if let Some(linear) = &self.linear {
            let result = linear_channel::setup(
                target.client,
                linear,
                target.agent_server_url,
                target.deployment_id,
                target.env_vars,
            );
            collect_failure(&mut failures, "Linear", result);
        }
        combine_failures(failures)
    }
}

fn collect_failure(
    failures: &mut Vec<(&'static str, anyhow::Error)>,
    channel: &'static str,
    result: Result<()>,
) {
    if let Err(error) = result {
        failures.push((channel, error));
    }
}

/// One channel's failure as it is; several, each under its channel's name.
fn combine_failures(mut failures: Vec<(&'static str, anyhow::Error)>) -> Result<()> {
    match failures.len() {
        0 => Ok(()),
        1 => Err(failures.remove(0).1),
        _ => {
            let lines: Vec<String> = failures
                .iter()
                .map(|(channel, error)| format!("{channel}: {error:#}"))
                .collect();
            anyhow::bail!(
                "The deployment is live, but these channels did not finish setting up:\n{}",
                lines.join("\n")
            )
        }
    }
}

/// What `mda dev` says about channels that only work once deployed, one
/// summary line each.
pub(crate) fn dev_notes(providers: &BTreeSet<String>) -> Vec<String> {
    let mut notes = Vec::new();
    if providers.contains(github_channel::PROVIDER) {
        notes.push(
            "GitHub         not available in dev · deploy to receive GitHub events".to_string(),
        );
    }
    if providers.contains(linear_channel::PROVIDER) {
        notes.push(
            "Linear         not available in dev · deploy to receive Linear events".to_string(),
        );
    }
    notes
}

/// What `mda delete` removes for the deployment's channels.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct ChannelTeardown {
    /// Agent Auth apps as `(app id, slug)`. Deleting an app removes its
    /// connection, credentials and webhook secret.
    pub(crate) apps: Vec<(String, String)>,
    /// Agent Auth connections as `(connection id, slug)`, in deletion order.
    pub(crate) connections: Vec<(String, String)>,
    /// What only the user can remove, as `(title, message)`.
    pub(crate) notes: Vec<(String, String)>,
}

/// The Agent Auth apps and connections the deployment's channels left, if any.
pub(crate) fn find_teardown(
    client: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
    deployment_id: &str,
) -> Result<Option<ChannelTeardown>> {
    let agent_auth = channel_app::agent_auth(client, env_vars)?;
    let found = [
        github_channel::teardown::find(&agent_auth, deployment_id)?,
        linear_channel::teardown::find(&agent_auth, deployment_id)?,
    ];
    Ok(found.into_iter().flatten().reduce(|mut all, teardown| {
        all.apps.extend(teardown.apps);
        all.connections.extend(teardown.connections);
        all.notes.extend(teardown.notes);
        all
    }))
}

/// Delete the apps and connections. Returns how many still existed.
pub(crate) fn tear_down(
    client: &langsmith::Client,
    env_vars: &BTreeMap<String, String>,
    teardown: &ChannelTeardown,
) -> Result<usize> {
    let agent_auth = channel_app::agent_auth(client, env_vars)?;
    github_channel::teardown::tear_down(&agent_auth, &teardown.apps, &teardown.connections)
}

/// Whether deploy lists a channel's events URL for the user to configure.
/// Channels set up here point their provider at it themselves.
pub(crate) fn lists_events_url(manifest: &Value) -> bool {
    let field = |key: &str| manifest.get(key).and_then(Value::as_str);
    field("transport") != Some("trigger_server")
        && field("provider") != Some(github_channel::PROVIDER)
        && field("provider") != Some(linear_channel::PROVIDER)
}

#[cfg(test)]
mod tests {
    use anyhow::anyhow;

    use super::*;

    #[test]
    fn every_channels_failure_is_reported() {
        assert!(combine_failures(Vec::new()).is_ok());

        let one = combine_failures(vec![("Slack", anyhow!("no app"))]).unwrap_err();
        assert_eq!(one.to_string(), "no app");

        let mut failures = Vec::new();
        collect_failure(&mut failures, "Slack", Err(anyhow!("no app")));
        collect_failure(&mut failures, "GitHub", Ok(()));
        collect_failure(&mut failures, "Linear", Err(anyhow!("no secret")));
        let both = combine_failures(failures).unwrap_err().to_string();
        assert!(both.contains("Slack: no app"), "{both}");
        assert!(both.contains("Linear: no secret"), "{both}");
        assert!(!both.contains("GitHub"), "{both}");
    }
}
