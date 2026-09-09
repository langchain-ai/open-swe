This is a scheduled automation run with a validated Slack destination.
- Do not send an initial acknowledgement.
- After a concrete requested action, call `notify_automation_channel` once with a concise outcome and link.
- Use Slack thread tools only when the scheduled task explicitly requires interaction in that destination.
