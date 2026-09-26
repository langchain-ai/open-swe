Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    trigger: How the automation fires. "schedule" (the default) runs on a
        cron. "github_issue_opened" runs whenever an issue is opened in
        ``repo``, and passes the issue as untrusted context.
        "slack_channel_message" runs whenever a top-level post in
        ``slack_channel_id`` matches ``message_pattern``; the run replies in
        that post's thread and receives the post as untrusted context. Thread
        replies, posts that mention Open SWE, Open SWE's own posts, and posts
        from bots a workspace admin has not allowed never trigger it.
    schedule: Five-field UTC cron expression. Required when ``trigger`` is
        "schedule"; ignored otherwise.
    name: Short display name.
    repo: ``owner/repo`` the configuring admin can access. Optional for
        scheduled automations; required when ``trigger`` is
        "github_issue_opened".
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G. For
        "slack_channel_message" it is the watched channel and is required.
    slack_notification_mode: Post every run or only when the run takes action.
        Ignored for "slack_channel_message", which always replies in the
        matching post's thread.
    message_pattern: RE2 regular expression searched anywhere in a post's
        text. Required when ``trigger`` is "slack_channel_message". Check it
        with ``preview_automation_matches`` first.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
