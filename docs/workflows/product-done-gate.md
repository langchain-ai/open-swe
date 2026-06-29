# Product Done Gate

Open SWE delivery work is not product-complete just because the backend path is implemented. A ticket can count as product-complete only when the user-facing delivery path is visible, configured, and proven at the right level for the change.

## Completion States

| State | Meaning | Can be counted as product-complete? |
|---|---|---|
| Backend-complete | Server behavior, APIs, schemas, or internal queue transitions are implemented and tested, but the operator cannot manage or inspect the capability in the dashboard. | No |
| Product-complete | The relevant operator surface exists, the feature can be configured or inspected by a user, and proof artifacts show the intended delivery path working. | Yes |

## Product-Complete Checklist

Every delivery-platform ticket must include:

- Tests that cover the changed behavior and failure modes.
- UI visibility when the feature affects workspace setup, queue state, review, QA, merge policy, credentials, or delivery evidence.
- Browser verification for frontend changes, including screenshots or traces for the relevant desktop and mobile states.
- Real or representative configured-project proof for delivery-critical features such as Linear intake, GitHub PR delivery, reviewer/QA handoff, evidence capture, and policy-gated merge.
- A clear before-and-after note covering cause, changed files, proof, risks, and the PR summary.

## Configuration Proof

Tickets touching Linear, GitHub, or workspace configuration must prove that a workspace user can manage the setting from the product surface. Environment variables, seed data, or code constants are not enough unless the ticket is explicitly backend-only and linked to a product follow-up.

The proof must show:

- Where the user manages the configuration.
- The saved API or store state after the user action.
- The readiness or preflight result that consumes the saved configuration.

## Credential Proof

Tickets touching credentials must prove:

- Redacted display in the product surface.
- Create or update behavior.
- Rotation or revoke behavior.
- No secret value in logs, snapshots, screenshots, API responses, queue metadata, or PR evidence.

## Done Without Product Proof

A Done ticket that lacks product proof must not be counted as product-complete. It must be reopened or linked to a follow-up issue that names the missing proof. The follow-up must stay open until the product proof is captured and attached to the original delivery path.

## Linear Closeout Evidence

Before moving a delivery-platform issue to Done, add evidence to the issue or PR that includes:

- Commit or PR reference.
- Test commands and results.
- Browser verification artifacts when UI changed.
- Configured-project proof when delivery-critical behavior changed.
- Known risks or follow-up issue links.
