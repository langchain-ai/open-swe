Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    workspace: Slug of the workspace every run launches in, with its settings,
        MCP connections, and sandbox image. Required; ask which workspace when
        it is not clear.
    triggers: Everything that fires the automation, each with its own filters;
        any one of them starts a run:
        - ``{"kind": "schedule", "cron": "0 9 * * 1", "repo": "owner/repo"}``
          runs on a five-field UTC cron. ``repo`` is optional and names the
          repository runs start in.
        - ``{"kind": "github", "repo": "owner/repo", "events": [...]}`` runs on
          events in that repository, passed to the run as untrusted context:
          "issues.opened", "pull_request.opened", "pull_request.closed"
          (merged or not), and "pull_request.merged".
        Every ``repo`` must be one the configuring admin can access.
    name: Short display name.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G, or a
        Slack member ID starting with U or W to DM that person instead.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
        Not allowed with GitHub triggers on public repositories, whose event text
        anyone can write.
