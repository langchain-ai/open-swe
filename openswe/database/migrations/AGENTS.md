# AGENTS.md

A row that belongs to a person references `users.id` with a foreign key. Never key it on a provider identifier (a GitHub id or login, a Slack user id, an email) as a stand-in for a person, even when the event that creates the row only carries one: resolve the person through `user_identity` (`User.for_identity`) where the event arrives, and skip, with a log line, events from someone with no Open SWE account. Provider identifiers belong in `user_identity` only.
