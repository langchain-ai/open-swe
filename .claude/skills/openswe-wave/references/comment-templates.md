# Wave comment templates

Replace every angle-bracket placeholder and delete unused optional lines.

## Dispatch

```markdown
@openswe repo <owner/repo> — Execute <TICKET> only.

Enter plan mode first. Re-anchor all cited paths and symbols against `<ref>`, state any refuted premise as a Challenge, and do not implement until approval is posted in this Linear thread.

Required scope: <scope>.
Boundaries: <non-goals>.
Verification: <focused tests>, `make lint`, and `make typecheck`.
PR body: include the Linear reference and `Closes <TICKET>` as a standalone line. Let normal Open SWE Review and required CI run; do not directly merge or bypass gates.
```

## Approval

```markdown
@openswe Plan approved. Proceed with <TICKET> implementation only.

Challenge adjudication:
- <ratified/refused challenge and evidence>

Clarifications:
- <binding implementation clarification>

Run <focused tests>, `make lint`, and `make typecheck`. Open the normal PR with the Linear reference and standalone `Closes <TICKET>`. Let Open SWE Review and required CI run; do not directly merge or bypass gates.
```

## Reject

```markdown
@openswe Plan not approved for <TICKET>. Revise the plan and repost for review — do not implement.

Blocking rulings:
- <ruling, with the evidence that refutes the plan step>

Required corrections:
- <specific change the revised plan must contain>

Scope is unchanged: <scope>. Post the revised plan in this thread and hold for approval.
```

## Nudge

```markdown
@openswe Status check on <TICKET>: no visible progress for <minutes> minutes. Post a brief status update in this thread (current step, and the blocker if you are blocked).
```

## Spot-audit

```markdown
Operator spot-audit of <PR> at `<head>`:

- Scope/file surface: <result>
- Approved plan rulings: <result>
- Acceptance invariants: <result>
- Failure and recovery paths: <result>
- Tests and unchanged boundaries: <result>

Disposition: <pass / follow-up required, with exact evidence>.
```

## Closeout

```markdown
Completed <TICKET>.

- PR and protected merge: <url> / `<merge-sha>`
- Review and CI: <result>
- Acceptance replay/live evidence: <result>
- Recovery actions, if any: <result>
- Deployment, if in scope: <result>
- Tracker: verify the Linear issue auto-transitioned on merge; flip manually only as fallback
- Follow-ups: <tickets or none>
```

## OSWE-100 tally

```markdown
Plan-gate tally — <TICKET> (<wave>)

Challenges: <count and disposition>
Questions: <count and disposition>
Unverified: <count and resolution status>

Manual adjudication catch: <what changed, or none>.
Review-layer catch: <what changed, or none; keep separate from plan challenges>.
Running ratified-challenge total: <count across plans>, with <false-count> false challenges.
```
