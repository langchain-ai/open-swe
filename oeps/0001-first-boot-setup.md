# OEP-0001: First-boot setup flow instead of pre-boot configuration

- **Authors:** Mukil Loganathan (`@mukil`)
- **Status:** Draft
- **Created:** 2026-09-05
- **Discussion:** https://github.com/langchain-ai/open-swe/pulls?q=is%3Apr+OEP-0001
- **Supersedes:** None

## Summary

Move the GitHub App, Slack app, and first-admin setup out of environment variables and into a
one-time, token-protected setup flow served by the deployment itself on first boot. An operator
deploys with the LangSmith key and two generated secrets, opens the deployment URL, creates the
GitHub App with GitHub's App Manifest flow, creates and installs the Slack app from a manifest, and
becomes the first admin by signing in. Everything the flow creates is stored by the deployment.

## Motivation

Installing Open SWE today means creating a GitHub App by hand (permissions, events, webhook URL,
private key download, client secret), creating a Slack app from a manifest, copying seven values
into the environment before the first boot, and naming admins in `CONFIGURED_ADMINS`. Each copied
value is a place to make a mistake, and none of them can be checked until the server is running.
Comparable products (self-hosted Omni, Omnigent, Probot-based apps) either configure integrations
in the product after boot or create the GitHub App for the operator with the manifest flow.

Runtime discovery of the installation id, bot identity, and LangSmith tenant (previously proposed as
separate changes) removes some of those values but leaves the App and Slack creation manual. A
setup flow removes the manual creation itself.

## Proposal

**Setup mode.** When the backend boots without GitHub App credentials, it serves a `/setup` page
and keeps the webhooks disabled. The page requires a one-time setup token that the server writes
to its log at boot (or that the operator provides as `SETUP_TOKEN`), so the first person to reach
a fresh deployment cannot claim it.

**GitHub App creation.** The page starts GitHub's App Manifest flow with Open SWE's permissions,
events, and webhook URL prefilled from the deployment's own URL. GitHub creates the App and
redirects back with a code; the backend exchanges it for the client id, client secret, private key,
and webhook secret, then links the operator to the App's installation page. The installation id
is resolved from each run's repository, never configured.

**First admin.** Setup ends by starting a GitHub OAuth login with the App just created. The first
account to complete that login while no admin exists is recorded as the bootstrap admin in the
store. `CONFIGURED_ADMINS` remains as an environment seed; admins are otherwise managed from the
dashboard.

**Slack.** The page offers Slack's create-from-manifest link and then an OAuth install, so the bot
token and signing secret are received rather than pasted. The bot's user id and handle are read
from the token.

**Storage.** Created credentials are encrypted with `TOKEN_ENCRYPTION_KEY` and stored in the
LangGraph store. Locally, the flow also writes them to `.env`. On LangGraph Platform, the flow
either shows the values for the operator to add to the deployment or updates the deployment's own
environment through the control plane, subject to the unresolved question below.

**Lock.** Once credentials and an admin exist, `/setup` is gone. Re-running setup requires deleting
the stored credentials deliberately.

**Before boot, an operator sets:** `LANGSMITH_API_KEY` (injected by LangGraph Platform),
`TOKEN_ENCRYPTION_KEY`, and `DASHBOARD_JWT_SECRET`. A model provider key is optional when the
LLM Gateway is used.

**Depends on:** the bundled dashboard (#2486), so the setup page, the login, and the API share one
URL; and a store-backed admin list, which does not exist yet.

### Non-goals

- A vendor-hosted Slack or GitHub app shared across deployments. Each deployment keeps its own apps.
- Replacing `.env` for local development; the flow writes it instead of asking for it.
- Linear, custom sandbox images, and other optional integrations; they keep their current setup.

## Security and privacy

- The setup page is unauthenticated by necessity and therefore short-lived and token-gated. The
  token is single-use, expires when setup completes, and is never logged after that.
- The App Manifest exchange returns long-lived credentials; they are stored encrypted with a key
  the operator controls and never returned to the browser after creation.
- The first-login admin bootstrap is only available while no admin exists and only inside a
  completed setup session, so a later visitor cannot become admin by signing in first.
- Webhooks stay disabled until the App exists, so there is no window where unsigned deliveries are
  accepted.
- The flow reduces credential handling by operators (no private key download or paste), which is
  the main security benefit.

## Alternatives

- **Keep pre-boot configuration and add runtime discovery.** Removes the installation id, bot
  identity, and tenant id from the environment but keeps manual App and Slack creation and the
  seven copied values. Simpler to build; does not change the operator's experience much.
- **Configure integrations in the dashboard after boot, with pasted tokens.** What self-hosted
  Omni does. Avoids the manifest flows but keeps copying, and the dashboard login needs the GitHub
  App first, so a bootstrap is still required.
- **A CLI setup (`make setup`).** Prompts for the same values; it helps local installs but cannot
  create the apps or serve a platform deployment.

## Unresolved questions

- Whether the injected LangSmith key on LangGraph Platform can update the deployment's own
  environment through the control plane. If not, platform deployments get a "copy these values"
  step instead of a fully automatic one.
- Whether the bootstrap admin is the GitHub account that created the App (the manifest exchange
  reports its owner) or the first account to sign in during setup. The first is stricter; the
  second also works for org-owned Apps.
- Where the setup token is best delivered on LangGraph Platform, where operators may not read
  container logs: an environment variable set at deploy time is the likely answer.

## Resolution

Not yet resolved.
