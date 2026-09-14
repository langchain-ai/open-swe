Activate plan mode mid-run.

Call this only when the user explicitly asks to enter or use plan mode. Do not
infer plan mode from task complexity, size, ambiguity, or the word "plan"
appearing in the request.

Once activated, stay read-only for the target repo: research the codebase,
create or edit a dated, self-contained HTML artifact outside any repo (for
example, ``/workspace/plans/YYYY-MM-DD-short-task-slug.html``), then publish
it with the ``save_plan`` tool and share the plan-review link with the user. Do not
edit repo files, commit, push, or open a PR — the user reviews the plan and
approves it before you implement.
