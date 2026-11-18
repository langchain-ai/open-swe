Deliver one pending notification ID returned by merge_queue_recovery_status.
Text is constructed from confirmed controller outcomes and verified Slack identities, including deferral attribution, suggested fix, evidence, and requeue instructions.
Receipts persist; repeat the same ID after uncertain delivery instead of posting a second copy manually.
Only the agent owning the bound Slack thread may invoke this tool.
Do not duplicate its message in a final reply.
Initial diagnosis and routine waiting require no extra message.
