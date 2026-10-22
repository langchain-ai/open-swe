//! What `mda delete` removes for the Linear channel: the deployment's app
//! connection in Agent Auth, which holds the agent's token, and the webhook
//! signing secret next to it.
//!
//! Linear has no public API to delete an app, so the app stays in Linear;
//! delete says where its admins can remove it.

use anyhow::Result;

use super::{SLUGS, WEBHOOK_SECRET_SUFFIX};
use crate::deploy::channel_app::find_connection;
use crate::deploy::channel_setup::ChannelTeardown;
use crate::langsmith::{Client, ConnectionSummary};

/// The deployment's Linear connections, if any are left.
pub(crate) fn find(agent_auth: &Client, deployment_id: &str) -> Result<Option<ChannelTeardown>> {
    let connections = plan(&agent_auth.list_connections_raw()?, deployment_id);
    if connections.is_empty() {
        return Ok(None);
    }
    Ok(Some(ChannelTeardown {
        apps: Vec::new(),
        connections,
        notes: vec![(
            "Delete the Linear app in Linear".to_string(),
            "Linear has no API to delete an app, so the app this agent used still exists. A workspace admin can delete it under Settings → Administration → API → OAuth Applications.".to_string(),
        )],
    }))
}

/// `(connection id, slug)` of the signing secret, then the app's connection.
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

    fn connection(id: &str, slug: &str) -> ConnectionSummary {
        serde_json::from_value(serde_json::json!({
            "connection_id": id, "slug": slug, "kind": "secret",
        }))
        .unwrap()
    }

    #[test]
    fn plans_the_apps_connection_and_secret() {
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
}
