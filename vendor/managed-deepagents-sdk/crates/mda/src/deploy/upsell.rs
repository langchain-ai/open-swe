//! Plan-entitlement upsell for `mda deploy`.
//!
//! Shipping a Managed Deep Agent needs two LangSmith entitlements, each carried
//! by the organization's plan. When either is missing the host backend answers
//! `403` with a fixed `detail` (langchainplus `host/api/util.py`):
//!
//! - `LangSmith Deployment is not enabled for this organization` — the plan does
//!   not include LangSmith Deployment at all (`can_use_langgraph_cloud`).
//! - `Managed Deep Agents is not enabled for this organization` — the plan
//!   includes Deployment but not Managed Deep Agents
//!   (`managed_deep_agents_enabled`, which the private beta can also grant
//!   outside the plan).
//!
//! Neither is a bug or a misconfiguration the developer can fix in their project,
//! so a bare `... (HTTP 403)` line sends them looking in the wrong place. Report
//! them as what they are — a billing decision — and link to the organization's
//! plans-and-billing page.
//!
//! Classification reads the typed [`ApiError`] rather than the flattened message,
//! but the `detail` strings are still a server contract: a reworded gate degrades
//! to the plain error, never to a wrong claim about someone's plan.

use anyhow::{Error, Result};
use cliclack::{log, note, outro_cancel};
use reqwest::StatusCode;

use crate::langsmith::{self, ApiError};

use super::helpers::plan_upgrade_url;

/// Public pricing page. Self-serve upgrades happen on the billing page, but
/// Enterprise is a sales conversation, so always offer this alongside it.
const PRICING_URL: &str = "https://www.langchain.com/pricing";

/// Shared tail of the host backend's entitlement `detail` messages.
const NOT_ENABLED: &str = "is not enabled for this organization";

/// A LangSmith entitlement the deploying organization's plan does not include.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub(crate) enum PlanGate {
    /// The organization cannot use LangSmith Deployment at all.
    Deployment,
    /// The organization can deploy, but not Managed Deep Agents.
    ManagedDeepAgents,
}

impl PlanGate {
    /// Why the deploy stopped, in the developer's terms rather than the API's.
    fn explanation(self) -> &'static str {
        match self {
            Self::Deployment => {
                "Your LangSmith organization's plan does not include LangSmith Deployment, \
                 which `mda deploy` needs to host a Managed Deep Agent. It is available on \
                 the Plus and Enterprise plans."
            }
            Self::ManagedDeepAgents => {
                "Your LangSmith organization can deploy, but does not have Managed Deep \
                 Agents enabled. It is available on the Plus and Enterprise plans, and is \
                 still in private beta — if upgrading does not enable it, contact LangChain \
                 to be added."
            }
        }
    }

    /// Cancelled-session footer. Names the missing entitlement so a scrolled-back
    /// terminal still says which one it was.
    fn footer(self) -> &'static str {
        match self {
            Self::Deployment => {
                "Deploy cancelled — this plan does not include LangSmith Deployment."
            }
            // Not phrased as a plan limit: this entitlement is also gated by the
            // private beta, so an upgraded org can still land here.
            Self::ManagedDeepAgents => {
                "Deploy cancelled — Managed Deep Agents is not enabled for this organization."
            }
        }
    }
}

/// Classify an error as a plan-entitlement gate, or `None` for anything else.
///
/// Walks the whole chain because deploy steps add context above the transport
/// error, and matches case-insensitively so the entitlement is identified by the
/// feature it names rather than by exact server casing.
pub(crate) fn plan_gate(err: &Error) -> Option<PlanGate> {
    let api = err
        .chain()
        .find_map(|cause| cause.downcast_ref::<ApiError>())?;
    if api.status != StatusCode::FORBIDDEN {
        return None;
    }
    let detail = api.detail.to_ascii_lowercase();
    if !detail.contains(NOT_ENABLED) {
        return None;
    }
    // Most specific first: the Managed Deep Agents gate is the narrower one.
    if detail.contains("managed deep agents") {
        Some(PlanGate::ManagedDeepAgents)
    } else if detail.contains("langsmith deployment") {
        Some(PlanGate::Deployment)
    } else {
        None
    }
}

/// Best-effort plans-and-billing link for the organization this deploy targets.
pub(crate) fn upgrade_url(client: &langsmith::Client) -> Option<String> {
    let workspace_id = client.workspace_id_for_links()?;
    let dashboard_url = client.dashboard_url().ok()?;
    plan_upgrade_url(&dashboard_url, &workspace_id)
}

/// Render the upsell and close the session, in place of the plain error footer.
///
/// `upgrade_url` is optional: without a resolvable workspace the deep link is
/// replaced by directions to the same page, which is still more actionable than
/// the raw 403. Each URL sits on its own line, unprefixed, so nothing but the
/// note's own border competes with it for terminal width.
pub(crate) fn report(gate: PlanGate, upgrade_url: Option<&str>) -> Result<()> {
    let upgrade =
        upgrade_url.unwrap_or("In LangSmith, open Settings > Billing > Plans and Pricing.");
    log::warning(gate.explanation())?;
    note(
        "Next step",
        format!(
            "Upgrade this organization\n{upgrade}\n\n\
             Compare plans, or contact sales for Enterprise\n{PRICING_URL}"
        ),
    )?;
    outro_cancel(gate.footer())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::anyhow;

    fn forbidden(detail: &str) -> Error {
        anyhow!(ApiError {
            status: StatusCode::FORBIDDEN,
            detail: detail.to_string(),
        })
    }

    #[test]
    fn recognizes_the_deployment_entitlement_gate() {
        let err = forbidden("LangSmith Deployment is not enabled for this organization");
        assert_eq!(plan_gate(&err), Some(PlanGate::Deployment));
    }

    #[test]
    fn recognizes_the_managed_deep_agents_entitlement_gate() {
        let err = forbidden("Managed Deep Agents is not enabled for this organization");
        assert_eq!(plan_gate(&err), Some(PlanGate::ManagedDeepAgents));
    }

    #[test]
    fn finds_the_gate_under_added_context() {
        let err = forbidden("Managed Deep Agents is not enabled for this organization")
            .context("POST /v2/deployments request failed");
        assert_eq!(plan_gate(&err), Some(PlanGate::ManagedDeepAgents));
    }

    #[test]
    fn other_forbidden_responses_are_not_plan_gates() {
        let err = forbidden("missing permission");
        assert_eq!(plan_gate(&err), None);
    }

    #[test]
    fn an_unknown_entitlement_is_not_attributed_to_a_plan() {
        let err = forbidden("Dev zero deployments are not enabled for this organization");
        assert_eq!(plan_gate(&err), None);
    }

    #[test]
    fn a_non_forbidden_status_is_not_a_plan_gate() {
        let err = anyhow!(ApiError {
            status: StatusCode::TOO_MANY_REQUESTS,
            detail: "LangSmith Deployment is not enabled for this organization".to_string(),
        });
        assert_eq!(plan_gate(&err), None);
    }

    #[test]
    fn errors_without_an_api_response_are_not_plan_gates() {
        let err = anyhow!("could not reach LangSmith");
        assert_eq!(plan_gate(&err), None);
    }
}
