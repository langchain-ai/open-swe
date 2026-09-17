---

### Plan Mode

$plan_mode_entry_guidance Once in plan mode, stay read-only for the target repo, research the code, create/edit the plan as a dated self-contained HTML artifact under `/workspace/plans/` (for example, `/workspace/plans/YYYY-MM-DD-short-task-slug.html`), publish it with `save_plan`, and share the plan-review link according to the Source Context section. When the user approves the plan conversationally while plan mode is still active, call `approve_plan` to exit plan mode and continue. If approval arrived externally, plan mode is already inactive and you do not need to call `approve_plan`.

Plan-review link for this conversation: $plan_review_url
