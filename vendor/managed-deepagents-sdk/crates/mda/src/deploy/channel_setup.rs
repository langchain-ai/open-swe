//! Channels that are set up outside the deployment: Slack through the trigger
//! server, GitHub through an App in Agent Auth, Linear through an OAuth app in
//! Agent Auth. Deploy prepares them before uploading, and sets them up on the
//! live deployment without knowing which providers are involved: channels
//! already set up on it before the upload, others once the new revision is
//! live.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde_json::Value;

use super::channel_app;
use super::github_channel::{self, GitHubChannel};
use super::linear_channel::{self, LinearChannel};
use super::trigger_channels::{self, PreparedTriggerChannel};
use crate::channel::ChannelKind;
use crate::compile::ChannelRequirements;
use crate::langsmith;
use crate::project::Language;

/// The project's channels that need setup, validated.
pub(crate) struct ChannelSetup {
    trigger: Option<PreparedTriggerChannel>,
    github: Option<GitHubChannel>,
    linear: Option<LinearChannel>,
}

/// Where a channel's setup stands for a deployment, read without changing
/// anything.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ChannelStatus {
    /// The provider sends the channel's events to the deployment.
    Ready,
    /// The channel handles no events, so it needs no app.
    NoApp,
    /// Setup has not finished; what is left.
    NeedsSetup(String),
}

/// Whether `status` leaves nothing to set up. A channel whose status could
/// not be read is not settled.
pub(crate) fn is_settled(status: &Result<ChannelStatus>) -> bool {
    matches!(status, Ok(ChannelStatus::Ready | ChannelStatus::NoApp))
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
    /// Validate the channels' configuration.
    pub(crate) fn prepare(
        requirements: &ChannelRequirements,
        project_root: &Path,
        project_files: &[PathBuf],
        language: Language,
        assistant_id: &str,
    ) -> Result<Self> {
        Ok(Self {
            trigger: trigger_channels::prepare(requirements, project_root, language, assistant_id)?,
            github: github_channel::prepare(requirements, assistant_id)?,
            linear: linear_channel::prepare(
                requirements,
                assistant_id,
                project_root,
                project_files,
            )?,
        })
    }

    /// The channels the project has, in setup order.
    pub(crate) fn kinds(&self) -> Vec<ChannelKind> {
        let mut kinds = Vec::new();
        if self.trigger.is_some() {
            kinds.push(ChannelKind::Slack);
        }
        if self.github.is_some() {
            kinds.push(ChannelKind::GitHub);
        }
        if self.linear.is_some() {
            kinds.push(ChannelKind::Linear);
        }
        kinds
    }

    /// Keep only the channels in `kinds`.
    pub(crate) fn retain(&mut self, kinds: &[ChannelKind]) {
        *self = self.take(kinds);
    }

    /// Move the channels in `kinds` out into a setup of their own.
    pub(crate) fn take(&mut self, kinds: &[ChannelKind]) -> Self {
        let has = |kind| kinds.contains(&kind);
        Self {
            trigger: self.trigger.take_if(|_| has(ChannelKind::Slack)),
            github: self.github.take_if(|_| has(ChannelKind::GitHub)),
            linear: self.linear.take_if(|_| has(ChannelKind::Linear)),
        }
    }

    /// Where each channel's setup stands for the deployment. A channel whose
    /// lookup fails gets its error; the others are still checked.
    pub(crate) fn statuses(
        &self,
        client: &langsmith::Client,
        deployment_id: &str,
        env_vars: &BTreeMap<String, String>,
    ) -> Vec<(ChannelKind, Result<ChannelStatus>)> {
        let mut statuses = Vec::new();
        if let Some(trigger) = &self.trigger {
            statuses.push((
                ChannelKind::Slack,
                trigger_channels::status(client, trigger, deployment_id),
            ));
        }
        if let Some(github) = &self.github {
            statuses.push((
                ChannelKind::GitHub,
                github_channel::status(client, github, deployment_id, env_vars),
            ));
        }
        if let Some(linear) = &self.linear {
            statuses.push((
                ChannelKind::Linear,
                linear_channel::status(client, linear, deployment_id, env_vars),
            ));
        }
        statuses
    }

    /// Fail before anything is created or uploaded when setup could not
    /// finish, e.g. without a terminal to answer a question it needs.
    /// `deployment_id` is the deployment being updated, if there is one, and
    /// `agent_server_url` its Agent Server URL once it has one.
    pub(crate) fn preflight(
        &self,
        client: &langsmith::Client,
        deployment_id: Option<&str>,
        agent_server_url: Option<&str>,
        env_vars: &BTreeMap<String, String>,
        interactive: bool,
    ) -> Result<()> {
        if let Some(trigger) = &self.trigger {
            trigger_channels::preflight(client, trigger, deployment_id, env_vars, interactive)?;
        }
        if let Some(github) = &self.github {
            github_channel::preflight(
                client,
                github,
                deployment_id,
                agent_server_url,
                env_vars,
                interactive,
            )?;
        }
        if let Some(linear) = &self.linear {
            linear_channel::preflight(client, linear, deployment_id, env_vars, interactive)?;
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

    /// How messages name the trigger-server channel, when there is one.
    pub(crate) fn trigger_label(&self) -> Option<&'static str> {
        self.trigger
            .as_ref()
            .map(|_| trigger_channels::PROVIDER_LABEL)
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
            collect_failure(&mut failures, ChannelKind::Slack.label(), result);
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
            collect_failure(&mut failures, ChannelKind::GitHub.label(), result);
        }
        if let Some(linear) = &self.linear {
            let result = linear_channel::setup(
                target.client,
                linear,
                target.agent_server_url,
                target.deployment_id,
                target.env_vars,
            );
            collect_failure(&mut failures, ChannelKind::Linear.label(), result);
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
/// Neither says whether the new revision is live: deploy sets channels up
/// both before uploading it and after.
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
                "These channels did not finish setting up:\n{}",
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

    fn github_only() -> ChannelSetup {
        ChannelSetup {
            trigger: None,
            github: Some(GitHubChannel {
                name: "github".into(),
                events_path: "/channels/github/events".into(),
                handled: vec!["issues.opened".into()],
                events: vec!["issues".into()],
                permissions: BTreeMap::new(),
                app_name: "agent".into(),
                repositories: Vec::new(),
                event_permissions: Vec::new(),
            }),
            linear: None,
        }
    }

    #[test]
    fn take_moves_out_only_the_named_channels() {
        let mut setup = github_only();

        let others = setup.take(&[ChannelKind::Slack, ChannelKind::Linear]);
        assert_eq!(others.kinds(), Vec::new());
        assert_eq!(setup.kinds(), vec![ChannelKind::GitHub]);

        let github = setup.take(&[ChannelKind::GitHub]);
        assert_eq!(github.kinds(), vec![ChannelKind::GitHub]);
        assert_eq!(setup.kinds(), Vec::new());
        assert!(!setup.needs_live_url());
    }

    #[test]
    fn an_unreadable_status_is_not_settled() {
        assert!(is_settled(&Ok(ChannelStatus::Ready)));
        assert!(is_settled(&Ok(ChannelStatus::NoApp)));
        assert!(!is_settled(&Ok(ChannelStatus::NeedsSetup("new".into()))));
        assert!(!is_settled(&Err(anyhow!("Agent Auth is down"))));
    }

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
