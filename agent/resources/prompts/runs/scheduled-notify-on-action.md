$prompt

This automation uses conditional Slack notifications. If and only if you perform a concrete requested action, such as changing code or updating an external system, call `notify_automation_channel` exactly once with a concise final outcome. Do not call it for read-only checks or when no action was needed.
