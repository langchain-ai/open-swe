Find people Open SWE knows and get the `user_id` (`user:<uuid>`) that every tool naming a person takes, such as a reviewer, a pull request author, a thread participant, or someone to invite.

Pass `query` as whatever identifies them: a name or part of one ("Ramon"), a GitHub login, an email, a Slack mention (`<@U123>`) or member id, or a `user:<uuid>` id. Each match returns `user_id`, `display_name`, `github_login`, and `slack_user_id`.

People introduced in this conversation's `person` blocks already carry their `user_id` as `id`; use it without looking them up. Never guess or construct a `user_id`. When several people match and the request does not make clear which one is meant, ask rather than picking one.
