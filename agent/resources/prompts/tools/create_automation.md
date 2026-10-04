Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    workspace: Slug of the workspace every run launches in, with its settings,
        MCP connections, and sandbox image. Required; ask which workspace when
        it is not clear.
    schedule: Five-field UTC cron expression to run on.
    github_events: GitHub events in ``repo`` to run on, passed to the run as
        untrusted context: "issues.opened", "pull_request.opened",
        "pull_request.closed" (merged or not), and "pull_request.merged".
        Give ``schedule``, ``github_events``, or both; any of them fires it.
    name: Short display name.
    repo: ``owner/repo`` the configuring admin can access. Required
        with ``github_events``; optional otherwise.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G, or a
        Slack member ID starting with U or W to DM that person instead.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
