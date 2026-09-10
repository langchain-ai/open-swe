Create a workspace automation.

Args:
    prompt: Complete instructions for every run.
    schedule: Five-field UTC cron expression.
    name: Short display name.
    repo: Optional ``owner/repo`` the configuring admin can access.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
