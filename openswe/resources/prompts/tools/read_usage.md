Read recorded Robin usage and spend, plus existing live personal gateway policy counters; never execute SQL.

Admins read all users by default, optionally filtered by immutable `user_id`, only from a private admin surface.
`personal_only=true` selects the caller.
Nonadmins always read only themselves; another user ID is refused.
Own-only filtering precedes final ranking, totals and pagination; reviewer/cohort and global ingestion metadata are omitted.
All reads require a private owner surface, never a shared thread.

Use `period` (`24h`, `7d`, `30d`, `mtd` or `all`), `limit` (1–100) and the returned `next_cursor`.
`mtd` means midnight UTC on the first of this month through the current time; recorded analytics may start later at deployment reporting cutover.

`gateway_spend` reuses the Usage page's authenticated gateway-policy reader for the caller only, unless `include_gateway_spend=false`.
It returns policy windows, counters and reset times; these do not change with the recorded-analytics period.
Other-user and all-user reports cannot use another person's gateway credentials: request `personal_only=true` for the caller's live counters.
Preserve `sign_in_required`, `forbidden`, `unavailable` or `unsupported_scope` rather than inventing a live total.

Recorded Robin costs may be incomplete and exclude external gateway calls.
Preserve cost-coverage counts; do not describe recorded costs as a live gateway invoice or remaining budget.
Only a gateway policy window matching the requested dates answers that gateway-period question directly.
