Share the authenticated admin thread owner's non-sensitive settings in the current Slack channel thread, only when they explicitly ask to publish their own settings.

This takes no identity, query, or destination arguments: the server verifies the requester and current thread. Every shared-thread call requires an owner-only approval, including sole-writer threads. On `approval_pending`, stop and wait; the approval button queues the exact retry. Never substitute another read tool to bypass approval.

After approval, the returned settings may be posted in this thread. Only recognized model/effort values and boolean PR/CI/context preferences are returned. Repository paths, custom instructions, connections, credentials, arbitrary stored strings, and other users' settings are excluded. Missing fields are omitted, not resolved to effective defaults. This is not SQL access; arbitrary SQL and the full settings reader remain private-only.
