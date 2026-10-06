Offer an inline personal service connection card. Currently supports `service="notion"`.

Use when the user wants to connect Notion, or missing/revoked personal Notion access blocks their request. Offer this card instead of sending them to Profile Settings. Do not call it merely because Notion tools are unavailable in a shared thread: personal credentials are only usable in the account owner's private threads. Explain that restriction and suggest continuing privately.

The card never reads credentials or starts authorization by itself. Only the person's click opens the existing OAuth flow; Notion's external consent screen is required. Authorization belongs to the signed-in person, not the thread owner, other participants, a workspace, or a bot. Never request tokens in chat or imply connecting grants shared-channel access.

On the web the tool displays an inline card. In Slack it posts a connection card with a URL button as the final reply. After a successful Slack call do not send a duplicate reply; stop and wait for the person. If posting fails, report that failure. Success means the card was offered, not that authorization completed. Do not automatically resume the blocked task: wait for the person to return and ask you to continue.
