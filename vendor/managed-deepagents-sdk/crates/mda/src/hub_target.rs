use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::Result;

use crate::agent_config;
use crate::deploy::langsmith_context::{LangSmithContext, WORKSPACE_FLAG_HINT};
use crate::deploy::{agent_environment, agent_target, env_file, resolve_api_key};
use crate::langsmith::{AgentEnvironment, DeploymentAgent};
use crate::naming::normalize_name;
use crate::project::Project;
use crate::telemetry::update_facts;

/// Flags that pick the deployment, and so the Context Hub repo, a command
/// works on after `mda deploy` created it.
#[derive(clap::Args)]
pub(crate) struct HubTargetArgs {
    /// Agent Server name (defaults to the authored agent name).
    #[arg(short = 'n', long)]
    pub name: Option<String>,
    /// Agent environment; the Agent Server name is used as the agent ID.
    /// Defaults to LANGSMITH_AGENT_ENVIRONMENT in .env.
    #[arg(long, value_enum)]
    pub agent_environment: Option<AgentEnvironment>,
    /// Workspace (overrides LANGSMITH_WORKSPACE_ID).
    #[arg(long)]
    pub workspace_id: Option<String>,
    /// Host backend URL override (advanced).
    #[arg(long, hide = true)]
    pub host_url: Option<String>,
    /// Read credentials from this file instead of the project .env.
    #[arg(long, value_name = "PATH")]
    pub env_file: Option<PathBuf>,
}

pub(crate) struct HubTarget {
    /// The deployment's name, which is also its Context Hub repo.
    pub name: String,
    pub agent: Option<DeploymentAgent>,
    pub ctx: LangSmithContext,
}

impl HubTargetArgs {
    /// Names the target the way `mda deploy` does, then signs in, including
    /// with an `mda login` session.
    pub(crate) fn resolve(&self, project_path: &Path, project: &Project) -> Result<HubTarget> {
        let base_name = match self.name.as_deref() {
            Some(name) => normalize_name(name)?,
            None => agent_config::agent_assistant_id(project)?,
        };
        let env_path = env_file::project_env_path(project_path, self.env_file.as_deref());
        let env_vars = env_file::parse_env_file(&env_path);
        let (name, agent) = target(base_name, self.agent_environment, &env_vars)?;
        update_facts(|facts| facts.deployment.deployment_name = Some(name.clone()));
        let ctx = LangSmithContext::resolve_verified(
            &env_vars,
            self.host_url.as_deref(),
            self.workspace_id.as_deref(),
            WORKSPACE_FLAG_HINT,
            |url| resolve_api_key(&env_vars, &env_path, url),
        )?;
        Ok(HubTarget { name, agent, ctx })
    }
}

/// The repo name, suffixed for a non-production agent environment.
fn target(
    base_name: String,
    environment: Option<AgentEnvironment>,
    env_vars: &BTreeMap<String, String>,
) -> Result<(String, Option<DeploymentAgent>)> {
    agent_target(base_name, agent_environment(environment, env_vars)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets_the_agent_environment_repo_deploy_writes() {
        let env = BTreeMap::from([("LANGSMITH_AGENT_ENVIRONMENT".into(), "staging".into())]);
        let name = |environment, env| target("support".into(), environment, env).unwrap().0;
        assert_eq!(name(None, &env), "support-staging");
        assert_eq!(name(Some(AgentEnvironment::Production), &env), "support");
        assert_eq!(name(None, &BTreeMap::new()), "support");
    }
}
