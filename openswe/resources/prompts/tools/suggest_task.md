Suggest a separate fix task for a concrete bug or obvious improvement spotted while working that is unrelated to the current request. Do not implement unrelated changes or expand the current task's scope.

Pass `repo` as `owner/name`, `description` explaining the problem and proposed fix, and `directories` as your best guess of the repository-relative directories that need changes (use `.` for the repository root).

This tool only logs the suggestion at INFO for now. It does not create a ticket, start work, or modify the repository. Do not include secrets or credentials.
