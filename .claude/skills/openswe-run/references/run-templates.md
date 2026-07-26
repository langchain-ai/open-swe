# openswe-run comment templates

Replace every angle-bracket placeholder and delete unused optional lines. `openswe-run`
refuses bodies with unfilled placeholders unless `--force` is passed. Dispatch and Approval
are verbatim-compatible with the openswe-wave templates — do not drift them independently.

## Dispatch

(Default body of `openswe-run start`; shown for `--body-file` customization.)

```markdown
@openswe repo <owner/repo> — Execute <TICKET> only.

Enter plan mode first. Re-anchor all cited paths and symbols against `<ref>`, state any refuted premise as a Challenge, and do not implement until approval is posted in this Linear thread.

Required scope: <scope>.
Boundaries: <non-goals>.
Verification: <focused tests>, `make lint`, and `make typecheck`.
PR body: include the Linear reference and `Closes <TICKET>` as a standalone line. Let normal Open SWE Review and required CI run; do not directly merge or bypass gates.
```

## Approval

Record real adjudication rulings — the checklist must have been applied first
(`approve` refuses without `--adjudicated`).

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

(Default body of `openswe-run nudge`. One nudge per stall, ever — then escalate.)

```markdown
@openswe Status check on <TICKET>: no visible progress for <minutes> minutes. Post a brief status update in this thread (current step, and the blocker if you are blocked).
```

## Review-findings reply

```markdown
@openswe Review findings on <PR> acknowledged for <TICKET>.

- <finding>: <fix now / justified as-is, with evidence>

Address the accepted findings, push to the same branch, and let Review and CI re-run.
```
