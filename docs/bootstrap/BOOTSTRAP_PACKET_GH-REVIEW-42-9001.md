# Hermes Northstar Testrepo Bootstrap Packet

Verification evidence:
- Renderer schema: hermes.bootstrap-packet-renderer.v1
- Source approval schema: hermes.testrepo-bootstrap-approval.v1
- Source pipeline schema: hermes.review-pipeline.v1
- Side effects performed by renderer: none
- Output type: file-only Markdown approval packet

Status: READY_FOR_HUMAN_APPROVAL
TASK_ID: GH-REVIEW-42-9001
Target repo: ollehillbom1/northstar-agent-harness-testrepo
Source pipeline gate: WARN
Source next action: START_QA_AGENT
Sandbox type: daytona

Required exact approval:
ALLOW_TESTREPO_BOOTSTRAP=YES repo=ollehillbom1/northstar-agent-harness-testrepo

Allowed trigger users:
- ollehillbom1

Requested external setup (not executed by this packet):
- github_app
- webhook
- docker_build

Verification required before final PASS:
- focused UAT/API smoke

Hard disabled actions:
- may_create_github_app: false
- may_configure_webhook: false
- may_push_or_pr: false
- may_start_server: false
- may_edit_erp: false

Recommended next prompt:
Efter separat approval, kör endast testrepo bootstrap dry-run mot allowlistat testrepo.
Skapa ingen GitHub App/webhook/push/PR/server utan explicit ALLOW_BOOTSTRAP_INSTALL=YES.

GATE=WARN
GATE_REASON=File-only bootstrap approval packet rendered without side effects; external setup remains disabled.
