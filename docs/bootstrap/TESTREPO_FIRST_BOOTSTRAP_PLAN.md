# Testrepo-First Bootstrap Plan

Generated: 2026-05-21

Scope: Hermes Northstar Agent Harness local/testrepo bootstrap only.

This plan intentionally does not create a GitHub App, configure a webhook,
start a public tunnel, build Docker images, push branches, open PRs, or install
anything in production.

## Gate 0: local readiness

Run first from the harness root:

```bash
scripts/northstar_local_readiness.sh
```

Required result:

```text
NORTHSTAR_HARNESS_READINESS_GATE=PASS
```

If the gate is `WARN`, stop unless the warning is explicitly accepted for the
affected next step. If the gate is `FAIL`, stop.

## Gate 1: testrepo profile dry-run

Run the local-only profile gate:

```bash
scripts/northstar_testrepo_bootstrap_gate.sh
```

Required result:

```text
NORTHSTAR_TESTREPO_BOOTSTRAP_GATE=PASS
```

The gate checks that:

- the harness remains outside `/erp`;
- readiness is still `PASS`;
- the default repository is a non-Northstar test repo;
- repo allowlists are non-empty and include only the selected test repo;
- Slack and Linear webhooks/tools are disabled for the initial profile;
- `http_request`, `fetch_url`, and `web_search` are disabled for coding runs;
- GitHub App, webhook, Docker build, bootstrap install, prod install, and
  Northstar repo flags are not enabled;
- all sensitive settings in `.env.example` are placeholders or empty.

The runtime honors `DISABLED_WEBHOOKS` and `DISABLED_AGENT_TOOLS`, so the same
profile declarations checked here also remove those tools/endpoints when the
LangGraph app is eventually started.

## Gate 2: explicit approval checkpoint

Only after Gate 0 and Gate 1 pass, ask for explicit approval for exactly one
next step. The first external step should be a disposable private test repo and
selected-repo GitHub App install. Do not include `ollehillbom1/north-star-erp`
in the first install.

Approval text should name:

- test repo owner/name;
- allowed trigger GitHub usernames;
- selected sandbox provider;
- whether a GitHub App may be created and installed only on that test repo;
- whether a webhook endpoint may be configured;
- whether Docker/sandbox snapshot build is allowed.

## Gate 3: first approved external test

After explicit approval, perform the smallest GitHub-only test:

1. create or select the disposable private test repo;
2. install GitHub App only on that repo;
3. keep Slack and Linear disabled;
4. keep egress tools disabled unless the task specifically needs them;
5. run a read-only issue-comment task;
6. run a branch-only draft PR task;
7. run deterministic after-agent gates and an independent reviewer;
8. record evidence before considering any Northstar installation proposal.

## Stop conditions

Stop immediately on:

- any real secret in prompt, docs, logs, or tool output;
- GitHub App install scope beyond the approved test repo;
- target repo mismatch;
- trigger user mismatch;
- `SANDBOX_TYPE=local` for autonomy;
- enabled Slack, Linear, or public egress tools in the initial profile;
- missing deterministic gate evidence;
- missing independent reviewer evidence;
- any attempt to edit `/erp` from the raw Open SWE runtime.
