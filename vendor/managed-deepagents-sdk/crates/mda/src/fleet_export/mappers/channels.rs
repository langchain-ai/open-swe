use crate::channel::{is_valid_slack_app_name, InitChannel, SlackChannelOptions};
use crate::fleet_export::snapshot::TriggerRegistration;
use crate::fleet_export::{Export, FleetAgent};
use crate::langsmith::SlackTriggerMode;

// Trigger Server's default template ids (`agent-builder-trigger-server/app/config.py`).
const CRON: &str = "c809e66e-0000-4000-8000-000000000001";
const GMAIL: &str = "c809e66e-0000-4000-8000-000000000002";
const SLACK_SHARED_APP: &str = "d809e66e-0000-4000-8000-000000000002";
const SLACK_CUSTOM_APP: &str = "d809e66e-0000-4000-8000-000000000003";
const TEAMS: &str = "d809e66e-0000-4000-8000-000000000004";
const OUTLOOK: &str = "e809e66e-0000-4000-8000-000000000003";

/// Copies the agent's custom Slack app settings into `channels/slack.*`.
/// `mda deploy` creates a new Slack app from them; Fleet's app isn't reused.
/// Only the caller's registrations are visible, as with schedules.
pub(super) fn map(agent: &FleetAgent, out: &mut Export) {
    let Some(triggers) = &agent.triggers else {
        out.report.warn(
            "Couldn't read this agent's channels from Trigger Server. If it answered in Slack, run `mda channels init slack`.",
        );
        return;
    };

    // Trigger Server keeps one custom Slack app per agent.
    let slack_app = triggers
        .iter()
        .find(|trigger| trigger.template_id == SLACK_CUSTOM_APP);
    if let Some(app) = slack_app {
        map_slack_app(app, out);
    }

    for trigger in triggers {
        let (summary, detail) = match trigger.template_id.as_str() {
            CRON | SLACK_CUSTOM_APP => continue,
            SLACK_SHARED_APP => (
                format!(
                    "Slack #{}",
                    trigger.config.channel_name.as_deref().unwrap_or("channel")
                ),
                if slack_app.is_some() {
                    "Fleet's shared Slack app isn't exported".to_string()
                } else {
                    "Fleet's shared Slack app isn't exported; run `mda channels init slack` to deploy the agent's own Slack app".to_string()
                },
            ),
            GMAIL => ("Gmail".to_string(), "MDA has no Gmail channel".to_string()),
            OUTLOOK => (
                "Outlook".to_string(),
                "MDA has no Outlook channel".to_string(),
            ),
            TEAMS => ("Teams".to_string(), "MDA has no Teams channel".to_string()),
            other => (
                format!("trigger {other}"),
                "unknown Fleet trigger".to_string(),
            ),
        };
        out.report.skip("channels", summary, detail);
    }
}

fn map_slack_app(app: &TriggerRegistration, out: &mut Export) {
    // Trigger Server reads any other mode as `mention`, MDA's default too.
    let trigger_mode = match app.config.trigger_mode.as_deref() {
        Some("thread") => Some(SlackTriggerMode::Thread),
        Some("channel") => Some(SlackTriggerMode::All),
        _ => None,
    };
    // Fleet doesn't check the name of a Slack app the user brought, so one
    // the SDKs reject is left out instead of failing the first build.
    let name = match &app.config.app_name {
        Some(name) if !is_valid_slack_app_name(name) => {
            out.report.warn(format!(
                "Fleet's Slack app name `{name}` isn't a valid `channels.slack` name (letters, numbers, spaces, `_`, `.` and `-`, at most 35 characters), so `channels/slack` leaves it out and the deployment uses the agent's name. Set `name` to choose another."
            ));
            None
        }
        name => name.clone(),
    };
    out.blueprint
        .channels
        .push(InitChannel::Slack(SlackChannelOptions {
            name,
            trigger_mode,
            allow_bot_triggers: app.config.allow_bot_triggers,
        }));

    let fleet_app = match &app.config.app_name {
        Some(name) => format!("Slack app `{name}`"),
        None => "Slack app".to_string(),
    };
    let mut warning = format!(
        "`mda deploy` creates a new Slack app; it doesn't reuse Fleet's {fleet_app}. Invite its bot to the channels Fleet's bot was in. Fleet's Slack app keeps answering until you remove the channel from the Fleet agent."
    );
    if app.status == "paused" {
        warning.push_str(" The Fleet channel was paused; the exported one is active.");
    }
    out.report.warn(warning);
}
