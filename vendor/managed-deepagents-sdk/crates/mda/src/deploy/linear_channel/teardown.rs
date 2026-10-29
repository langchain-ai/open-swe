//! What `mda delete` removes for the Linear channel: the deployment's app in
//! Agent Auth, which takes its connection, the agent's token and the webhook
//! signing secret with it. Deployments set up by an earlier mda hold the app
//! as two workspace connections instead, which only a workspace admin can
//! delete.
//!
//! Linear has no public API to delete an app, so the app stays in Linear;
//! delete says where its admins can remove it.

use anyhow::Result;

use super::{PROVIDER, SLUGS, WEBHOOK_SECRET_SUFFIX};
use crate::deploy::channel_app::find_connection;
use crate::deploy::channel_setup::ChannelTeardown;
use crate::langsmith::{Client, ConnectionSummary};

/// The deployment's Linear app, or an earlier mda's connections, if any are
/// left.
pub(crate) fn find(agent_auth: &Client, deployment_id: &str) -> Result<Option<ChannelTeardown>> {
    let apps: Vec<(String, String)> = agent_auth
        .agent_apps_if_supported(deployment_id)?
        .unwrap_or_default()
        .into_iter()
        .filter(|app| app.provider == PROVIDER)
        .map(|app| (app.id, app.slug))
        .collect();
    // The app's connection and secret go with it; only an earlier mda's
    // connections are deleted one by one.
    let connections = if apps.is_empty() {
        plan(&agent_auth.list_connections_raw()?, deployment_id)
    } else {
        Vec::new()
    };
    if apps.is_empty() && connections.is_empty() {
        return Ok(None);
    }
    Ok(Some(ChannelTeardown {
        apps,
        connections,
        notes: vec![(
            "Delete the Linear app in Linear".to_string(),
            "Linear has no API to delete an app, so the app this agent used still exists. A workspace admin can delete it under Settings → Administration → API → OAuth Applications.".to_string(),
        )],
    }))
}

/// `(connection id, slug)` of an earlier mda's signing secret, then its app
/// connection.
fn plan(connections: &[ConnectionSummary], deployment_id: &str) -> Vec<(String, String)> {
    [
        SLUGS.secret(deployment_id, WEBHOOK_SECRET_SUFFIX),
        SLUGS.connection(deployment_id),
    ]
    .into_iter()
    .filter_map(|slug| {
        find_connection(connections, &slug)
            .map(|connection| (connection.connection_id.clone(), slug))
    })
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::langsmith::test_support::serve_once;
    use crate::langsmith::ApiKey;

    fn connection(id: &str, slug: &str) -> ConnectionSummary {
        serde_json::from_value(serde_json::json!({
            "connection_id": id, "slug": slug, "kind": "secret",
        }))
        .unwrap()
    }

    #[test]
    fn plans_an_earlier_mdas_connection_and_secret() {
        let listed = [
            connection("c1", "mda/linear-app/dep-1"),
            connection("c2", "mda/linear-app/dep-1/webhook-secret"),
            connection("c3", "mda/linear-app/dep-10"),
            connection("c4", "mda/github-app/dep-1"),
        ];
        assert_eq!(
            plan(&listed, "dep-1"),
            vec![
                ("c2".into(), "mda/linear-app/dep-1/webhook-secret".into()),
                ("c1".into(), "mda/linear-app/dep-1".into()),
            ]
        );
        assert!(plan(&listed, "dep-2").is_empty());
    }

    #[test]
    fn deletes_the_deployments_linear_app_and_not_its_connections() {
        let apps = serde_json::json!([
            { "id": "app-gh", "provider": "github", "slug": "mda/github-app/dep-1" },
            {
                "id": "app-1", "provider": "linear", "source": "existing",
                "slug": "mda/linear-app/dep-1", "connection_id": "conn-1",
            },
        ]);
        // Only the apps are asked for: the app takes its connections with it.
        let (base, requests) = serve_once("200 OK", apps.to_string());
        let client = Client::new(&base, &base, ApiKey::new("lsv2_key"), None).unwrap();
        let teardown = find(&client, "dep-1").unwrap().unwrap();
        assert_eq!(
            teardown.apps,
            vec![("app-1".to_string(), "mda/linear-app/dep-1".to_string())]
        );
        assert!(teardown.connections.is_empty());
        assert!(requests
            .recv()
            .unwrap()
            .starts_with("GET /v1/agent-auth/apps?owner_id=dep-1 "));
    }
}
