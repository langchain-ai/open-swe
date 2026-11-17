Create an automation: personal by default, or a system one for admins.

A personal automation belongs to its creator, admin or not: it runs as them on Auto model routing and is billed to them, with no cap on how many they keep or how much each spends.
Only its creator or an admin may edit, test, or delete it.
A system automation belongs to the environment rather than a person: only admins create or manage it, and it may pick its model.
Its spend is credited to no person.

Args:
- prompt: Complete instructions for every run.
  Runs post nowhere on their own; say in the prompt if a run should report to a Slack channel.
  Runs act as the automation, not as you: they can read public threads, including their own earlier runs, but cannot message threads, start threads, or see private conversations.
  For anything they report, name a channel, or say to send it to you: a run can DM its result to the automation's creator, and only to them.
- workspace: Slug of the workspace every run launches in, with its settings and sandbox image.
  Required; ask which workspace when it is not clear.
- triggers: Everything that fires the automation, each with its own filters; any one of them starts a run:
  - ``{"kind": "schedule", "cron": "0 9 * * 1", "timezone": "America/New_York"}`` runs on a five-field cron in an IANA time zone.
    Omitted ``cron`` defaults to weekly, Mondays at 09:00 (``0 9 * * 1``).
    Omitted ``timezone`` defaults to UTC, preserving existing schedules.
    Use a region time zone, not an abbreviation or fixed offset, for local schedules that follow daylight saving time.
    At DST transitions, nonexistent local times run at the first valid time after the gap; repeated local times can run at both occurrences.
    Name any repository the run should work in in ``prompt``.
  - ``{"kind": "github", "repo": "owner/repo", "events": [...]}`` runs on events in that repository, passed to the run as untrusted context: "issues.opened", "pull_request.opened", "pull_request.closed" (merged or not), and "pull_request.merged".
    A public repository is refused unless the trigger sets ``"allow_public_repo": true``: anyone on the internet can write its issues and pull requests, and that text becomes part of the run's prompt while the run acts as the creator.
    Set it only after the user confirms they want that, never by yourself.
    Optional pull request filters, as case-sensitive globs such as ``"actions/*"``: ``head_branches_exclude`` (a pull request whose head branch matches any is skipped) and ``base_branches`` (when listed, the base branch must match one); issue events ignore them.
  - ``{"kind": "slack", "channel": "C0123456789", "events": ["message.posted"]}`` runs on new top-level messages in a Slack channel Robin is a member of, passed to the run as untrusted context.
    Optional filters: ``senders`` ("anyone", "people", or "bots"), ``match`` (a case-insensitive regular expression the text must match), and ``max_runs_per_hour``.
  - ``{"kind": "linear", "team": "ENG", "events": [...]}`` runs on issue events in a Linear team, passed to the run as untrusted context: "issue.created", "issue.labeled" (a label was added), and "issue.accepted" (the issue's first `triagedAt` timestamp is set, with a non-canceled, non-duplicate destination state).
    Acceptance ignores creation outside Triage, rejection, and later status edits; deliveries are deduplicated for 24 hours.
    Optional filters: ``labels`` (names; the issue must carry, or for "issue.labeled" gain, one of them), ``project`` (a project name), and ``max_runs_per_hour``.
    A GitHub trigger's ``repo`` must be one the configuring user can access.
- name: Short display name.
- model_id: Optional supported model ID; a personal automation always runs on Auto.
- effort: Optional reasoning effort for the model.
- system: Admins only.
  Make it a system automation.
- admin_thread: Only for an automation an admin creates, personal or system.
  Give runs workspace-admin capabilities while the creator remains an admin.
  Not allowed with GitHub triggers on public repositories, whose event text anyone can write.
