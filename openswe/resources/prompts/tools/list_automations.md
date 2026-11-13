List the automations you can see and their current run state: your own and system ones.

Args:
- all_users: Admins only.
  Also list every user's personal automations, to inspect what people created.

Each automation names its creator (`createdBy`, `ownerId`), triggers, prompt, and whether it is `enabled` (paused when false).
A system automation has `system: true`; a personal one is `restricted`: it runs on the workspace Fast model, and `usageToday` holds its busiest thread today (`threadId`, `tokens`) against `dailyTokenCap`.
List an automation's recent runs with `list_threads` (`scope: "automation"`, `automation_id`); pause one with `update_automation` (`enabled: false`) or remove it with `delete_automation`.
