Create a workspace automation.

Runs share one thread and sandbox by default, so files written in the sandbox persist between runs. Set ``thread_mode`` to ``new`` only when every run should start with a fresh sandbox and history.

Args:
    prompt: Complete instructions for every run.
    schedule: Five-field UTC cron expression.
    name: Short display name.
    repo: Optional ``owner/repo`` the configuring admin can access.
    model_id: Optional supported model ID.
    effort: Optional reasoning effort for the model.
    slack_channel_id: Optional Slack channel ID starting with C or G.
    slack_notification_mode: Post every run or only when the run takes action.
    thread_mode: Share one sandbox across runs by default, or use ``new`` for fresh sandboxes.
    admin_thread: Give runs workspace-admin capabilities while the creator remains an admin.
