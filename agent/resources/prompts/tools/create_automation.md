Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    workspace: Slug of the workspace every run launches in, with its settings,
        MCP connections, and sandbox image. Required; ask which workspace when
        it is not clear.
    triggers: Everything that fires the automation, each with its own filters;
        any one of them starts a run:
        - ``{"kind": "schedule", "cron": "0 9 * * 1"}`` runs on a five-field
          UTC cron. Name any repository the run should work in in ``prompt``.
        - ``{"kind": "github", "repo": "owner/repo", "events": [...]}`` runs on
          events in that repository, passed to the run as untrusted context:
          "issues.opened", "pull_request.opened", "pull_request.closed"
          (merged or not), and "pull_request.merged".
        - ``{"kind": "slack", "channel": "C0123456789", "events": ["message.posted"]}``
          runs on new top-level messages in a Slack channel Open SWE is a
          member of, passed to the run as untrusted context. Optional filters:
          ``senders`` ("anyone", "people", or "bots"), ``match`` (a
          case-insensitive regular expression the text must match), and
          ``max_runs_per_hour``.
        - ``{"kind": "linear", "team": "ENG", "events": [...]}`` runs on issue
          events in a Linear team, passed to the run as untrusted context:
          "issue.created" and "issue.labeled" (a label was added). Optional
          filters: ``labels`` (names; the issue must carry, or for
          "issue.labeled" gain, one of them), ``project`` (a project name),
          and ``max_runs_per_hour``.
        A GitHub trigger's ``repo`` must be one the configuring admin can access.
    name: Short display name.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G, or a
        Slack member ID starting with U or W to DM that person instead.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
        Not allowed with GitHub triggers on public repositories, whose event text
        anyone can write.
