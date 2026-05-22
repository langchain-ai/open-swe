# Review Trigger Test

Purpose: verify the minimal Hermes/Open SWE review loop without starting real agents, creating webhooks, using GitHub credentials, deploying, or modifying Northstar production code.

Scope: documentation + prompt-generation contract only.

## Test case: VAT/reporting medium-risk change with missing UAT

### Event

```json
{
  "type": "independent review recommended",
  "source": "hermes_orchestrator",
  "id": "evt_dummy_vat_review_001"
}
```

### Input files

- `docs/hermes/examples/review_trigger_input.example.json`
- `docs/hermes/templates/INDEPENDENT_REVIEW_RUNTIME_PROMPT.md`
- `docs/hermes/templates/NEXT_ACTION_RUNTIME_PROMPT.md`

### Preconditions

- Open SWE clone exists at `/home/olle/northstar-agent-harness/open-swe-hermes`.
- No production webhook is enabled.
- No GitHub App is created.
- No real agent is started.
- Test reads only example JSON/templates and writes no production code.

### Deterministic routing contract

Given event type `independent review recommended`, the router must:

1. Validate required input fields:
   - latest Reality State
   - current task card
   - current git diff summary
   - latest evidence log
   - allowed paths
   - forbidden paths
   - required tests
   - stop conditions
2. Generate an Independent Review Agent prompt by substituting:
   - `{{REALITY_STATE}}`
   - `{{TASK_CARD}}`
   - `{{GIT_DIFF_SUMMARY}}`
   - `{{EVIDENCE_LOG}}`
   - `{{ALLOWED_PATHS}}`
   - `{{FORBIDDEN_PATHS}}`
   - `{{REQUIRED_TESTS}}`
   - `{{STOP_CONDITIONS}}`
3. Simulate or receive an independent review report.
4. Generate a Next Action Prompt Agent prompt by substituting:
   - `{{REALITY_STATE}}`
   - `{{INDEPENDENT_REVIEW_REPORT}}`
   - `{{PRODUCT_GOALS}}`
   - `{{EPIC_MAP}}`
   - `{{TASK_BACKLOG}}`
5. The expected next-action decision is `START_QA_AGENT`.

### Expected independent review verdict

`NEEDS_FOLLOWUP`, not `PASS`.

Reason: builder says PASS, but for medium-risk VAT/reporting logic, the evidence log has:

- `./b.sh --quick`: PASS
- targeted VAT unit test: PASS
- `npm run typecheck`: SKIPPED
- VAT/reporting UAT/API smoke: MISSING

### Expected next task

Start a bounded QA agent with:

- owner/agent role: `qa_agent`
- allowed paths: VAT/reporting backend/test paths only
- forbidden paths: secrets, prod infra, unrelated frontend paths
- required tests:
  - `git diff --name-only github/main...HEAD`
  - `npm run typecheck`
  - targeted VAT/reporting unit test
  - focused VAT/reporting UAT/API smoke
- stop conditions:
  - secrets exposure
  - forbidden paths
  - production deploy required
  - tenant-impacting migration without provisioning evidence

### Manual dry-run command

No dependencies required:

```bash
python3 docs/hermes/pseudo_router/review_trigger_router.py docs/hermes/examples/review_trigger_input.example.json
```

Expected summary:

- generated review prompt contains the dummy Reality State, task card, git diff, and evidence log
- simulated review verdict is `NEEDS_FOLLOWUP`
- generated next-action decision is `START_QA_AGENT`
- no code changes, webhooks, GitHub calls, or production writes occur

GATE=PASS for this documentation-level test if the pseudo-router exits 0 and the output decision is `START_QA_AGENT`.
