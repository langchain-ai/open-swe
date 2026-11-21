Record this run's outcome for one approved review app, scoped to this pull request and commit.
For an app that writes documents, use `complete` only after `save_app_document` saved the finished result successfully.
For an app with no write collections, use `complete` with a reason summarizing the work performed.
Use `blocked` for missing prerequisites, `failed` for an unsuccessful attempt, or `skipped` when the diff legitimately needs no work for the app.
Those three statuses require a concrete `reason`.
For a temporary blocked prerequisite, set `retryable: true` to request another attempt within the preview job's existing bounded retry budget.
Leave it false for missing access, unsupported previews, or failed deployments; it does not start a new watch by itself.
Check `success` in the returned object; an error means the outcome was not recorded.
