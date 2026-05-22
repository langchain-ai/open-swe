# NORTHSTAR_AGENTS.md Template

This is a template for the target North Star ERP repository. Do not overwrite `/erp/AGENTS.md` from a raw harness run. Apply only through an approved PR.

## Mission

North Star ERP is a Swedish-first ERP/accounting platform. Agents must preserve accounting correctness, auditability, tenant isolation, and operational safety over speed.

## Swedish accounting context

Respect and explicitly consider these domains when relevant:

- BAS account plan and account-class semantics.
- SIE import/export integrity.
- Moms/VAT periods, VAT codes, and report consistency.
- SRU mappings for Swedish tax reporting.
- AGI/payroll reporting.
- Audit trail immutability: posted accounting events must be traceable and reversible through explicit corrections, not silent mutation.
- Bokföringsmässiga invariants: debet=kredit, period locking, voucher numbering, no cross-tenant leakage, no silent rounding drift.

## Multi-tenant security

- Never bypass tenant isolation.
- Every tenant-scoped entity, table, route, service, queue, and migration must be checked for tenant id propagation.
- For new tenant-scoped data, verify module wiring, `tenant-connection.service.ts`, tenant provisioning, and tenant migration.
- Do not log customer data, secrets, tokens, `.env`, credentials, private keys, or auth headers.

## Branch and PR rules

- Branch from `github/main`, not local `main`.
- Before PR/review/merge, run:
  - `git diff --name-only github/main...HEAD`
  - `git log --oneline github/main..HEAD`
  - `npm run -s pr:doctor`
- Prefer small scoped draft PRs.
- PR evidence must include root cause, diff scope, verification, data impact, and proposed commit message.
- Final PASS requires independent reviewer evidence; coder/orchestrator cannot self-approve.
- Do not merge without explicit human approval.

## Migration rules

For migrations and tenant-scoped schema changes, verify:

- module wiring
- `tenant-connection.service.ts`
- tenant provisioning
- tenant migration
- rollback/fix-forward path
- data backfill safety
- audit trail impact

## Test requirements

Default verification stack:

- `./b.sh --quick` after app/lib changes.
- `npm run typecheck` when API/types/hooks are touched.
- At least one targeted test covering the changed path.
- UAT/API smoke for runtime claims. Include exact command and raw response sample.
- Use npm for Nx/Northstar gates because workspace packageManager is npm. Do not use pnpm for `/erp` Nx gates.

## Forbidden actions

- Direct commit to local main.
- Direct `/erp` product edit from raw Hermes outside approved sandbox/worktree flow.
- Reading/logging secrets, `.env`, private keys, tokens, or credentials.
- Production deploy without explicit approval.
- GitHub push/PR without approval packet and human approval.
- Treating GitHub Actions as required Northstar gate unless explicitly requested; use local evidence + reviewer/admin decisions.
- Runtime/live claims without evidence.

## Required run metadata

Every autonomous run must carry:

- `TASK_ID`
- `OWNER_SESSION`
- `AGENT_SCOPE`
- `HERMES_TASK_ID`
- lease id/path
- allowed repo/path scope
- verification commands
- reviewer gate

End every handoff/report with `GATE=PASS|WARN|FAIL` and `GATE_REASON=...`.
