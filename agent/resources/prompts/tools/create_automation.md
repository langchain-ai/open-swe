Create or request a workspace automation.

Admins create automations immediately. Non-admins add a request to the admin review queue; tell them it is pending approval rather than claiming it was created.

Args:
    prompt: Complete instructions for every run.
    schedule: Five-field UTC cron expression.
    name: Short display name.
    repo: Optional ``owner/repo`` the requester can access.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G. Its real channel name is recorded in the request.
    slack_notification_mode: Post every run or only when the run takes action.
    admin_thread: Give runs workspace-admin capabilities. Only admins may create or request this mode.