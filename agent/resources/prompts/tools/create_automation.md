Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    trigger: How the automation fires. "schedule" (the default) runs on a
        cron. "github_issue_opened" runs whenever an issue is opened in
        ``repo``, and passes the issue as untrusted context.
    schedule: Five-field UTC cron expression. Required when ``trigger`` is
        "schedule"; ignored otherwise.
    name: Short display name.
    repo: ``owner/repo`` the configuring admin can access. Optional for
        scheduled automations; required when ``trigger`` is
        "github_issue_opened".
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
