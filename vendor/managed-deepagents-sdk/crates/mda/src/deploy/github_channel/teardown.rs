//! What `mda delete` removes for the GitHub channel: the deployment's App in
//! Agent Auth, which takes its connection, key, the agent's token and the
//! webhook secret with it, and what deploy stored next to it: the bot ID and
//! the markers of webhook URLs the App kept.
//!
//! GitHub has no API to delete an App, so the App stays on GitHub; delete
//! links its page so its owner can remove it.

use anyhow::Result;

use super::{BOT_ID_SUFFIX, PROVIDER, SLUGS, WEBHOOK_ELSEWHERE_SUFFIX, WEBHOOK_SECRET_SUFFIX};
use crate::deploy::channel_app::find_connection;
use crate::deploy::channel_setup::ChannelTeardown;
use crate::langsmith::github_app_setup::AgentAuthApp;
use crate::langsmith::{Client, ConnectionSummary};

/// The deployment's GitHub App and what deploy stored next to it, if any is
/// left.
pub(crate) fn find(agent_auth: &Client, deployment_id: &str) -> Result<Option<ChannelTeardown>> {
    // Without Agent Auth apps, deploy never set up a GitHub channel.
    let Some(apps) = agent_auth.agent_apps_if_supported(deployment_id)? else {
        return Ok(None);
    };
    let apps: Vec<AgentAuthApp> = apps
        .into_iter()
        .filter(|app| app.provider == PROVIDER)
        .collect();
    let listed = agent_auth.list_connections_raw()?;
    let connections = plan(&listed, deployment_id, !apps.is_empty());
    if apps.is_empty() && connections.is_empty() {
        return Ok(None);
    }
    Ok(Some(ChannelTeardown {
        notes: apps.iter().flat_map(deletion_notes).collect(),
        apps: apps.into_iter().map(|app| (app.id, app.slug)).collect(),
        connections,
    }))
}

/// What the App's owner deletes on GitHub by hand: the App this agent used,
/// and any App GitHub created for it that Agent Auth could not store.
fn deletion_notes(app: &AgentAuthApp) -> Vec<(String, String)> {
    let used = app.html_url().map(|url| {
        format!(
            "GitHub has no API to delete an App, so the App this agent used still \
             exists. Its owner can delete it under App settings → Advanced:\n{url}"
        )
    });
    let stranded = app.stranded_urls().into_iter().map(|url| {
        format!(
            "GitHub created this App for the agent, but Agent Auth could not store \
             it. Its owner can delete it under App settings → Advanced:\n{url}"
        )
    });
    used.into_iter()
        .chain(stranded)
        .map(|note| ("Delete the GitHub App on GitHub".to_string(), note))
        .collect()
}

/// Delete the apps, then the connections left next to them. Returns how many
/// still existed.
pub(crate) fn tear_down(
    agent_auth: &Client,
    apps: &[(String, String)],
    connections: &[(String, String)],
) -> Result<usize> {
    let mut deleted = 0;
    for (id, _) in apps {
        if agent_auth.delete_agent_app(id)? {
            deleted += 1;
        }
    }
    for (id, _) in connections {
        if agent_auth.delete_connection(id)? {
            deleted += 1;
        }
    }
    Ok(deleted)
}

/// `(connection id, slug)` of the secrets deploy stored next to the App.
/// Deleting the App takes its webhook secret with it, so that is only left
/// over when the App is gone.
fn plan(
    connections: &[ConnectionSummary],
    deployment_id: &str,
    app_exists: bool,
) -> Vec<(String, String)> {
    let mut suffixes = vec![BOT_ID_SUFFIX];
    if !app_exists {
        suffixes.push(WEBHOOK_SECRET_SUFFIX);
    }
    let mut planned: Vec<(String, String)> = suffixes
        .into_iter()
        .map(|suffix| SLUGS.secret(deployment_id, suffix))
        .filter_map(|slug| {
            find_connection(connections, &slug)
                .map(|connection| (connection.connection_id.clone(), slug))
        })
        .collect();
    // One marker per webhook URL the App kept.
    let kept = SLUGS.secret(deployment_id, WEBHOOK_ELSEWHERE_SUFFIX);
    planned.extend(
        connections
            .iter()
            .filter(|connection| connection.slug.starts_with(&kept))
            .map(|connection| (connection.connection_id.clone(), connection.slug.clone())),
    );
    planned
}

#[cfg(test)]
mod tests {
    use super::*;

    fn connection(id: &str, slug: &str) -> ConnectionSummary {
        serde_json::from_value(serde_json::json!({
            "connection_id": id, "slug": slug, "kind": "secret",
        }))
        .unwrap()
    }

    #[test]
    fn notes_every_app_left_on_github() {
        let app: AgentAuthApp = serde_json::from_value(serde_json::json!({
            "id": "app-1", "provider": "github", "source": "manifest",
            "slug": "mda/github-app/dep-1", "app_id": "42",
            "app_metadata": { "slug": "acme", "html_url": "https://github.com/apps/acme" },
            "stranded_apps": [{ "slug": "acme-1", "html_url": "https://github.com/apps/acme-1" }],
        }))
        .unwrap();
        let notes = deletion_notes(&app);
        assert_eq!(notes.len(), 2, "{notes:?}");
        assert!(notes[0].1.ends_with("\nhttps://github.com/apps/acme"));
        assert!(notes[1].1.contains("could not store"));
        assert!(notes[1].1.ends_with("\nhttps://github.com/apps/acme-1"));
    }

    #[test]
    fn plans_the_secrets_deploy_stored_next_to_the_app() {
        let listed = [
            connection("c1", "mda/github-app/dep-1"),
            connection("c2", "mda/github-app/dep-1/webhook-secret"),
            connection("c3", "mda/github-app/dep-1/bot-id"),
            connection("c4", "mda/github-app/dep-10/bot-id"),
            connection("c5", "mda/github-app/dep-1/other"),
            connection("c6", "tavily"),
            connection(
                "c7",
                "mda/github-app/dep-1/webhook-elsewhere/0123456789abcdef",
            ),
            connection(
                "c8",
                "mda/github-app/dep-10/webhook-elsewhere/0123456789abcdef",
            ),
        ];
        // The App takes its connection and webhook secret with it.
        assert_eq!(
            plan(&listed, "dep-1", true),
            vec![
                ("c3".into(), "mda/github-app/dep-1/bot-id".into()),
                (
                    "c7".into(),
                    "mda/github-app/dep-1/webhook-elsewhere/0123456789abcdef".into()
                ),
            ]
        );
        assert_eq!(
            plan(&listed, "dep-1", false),
            vec![
                ("c3".into(), "mda/github-app/dep-1/bot-id".into()),
                ("c2".into(), "mda/github-app/dep-1/webhook-secret".into()),
                (
                    "c7".into(),
                    "mda/github-app/dep-1/webhook-elsewhere/0123456789abcdef".into()
                ),
            ]
        );
        assert!(plan(&listed, "dep-2", false).is_empty());
    }
}
