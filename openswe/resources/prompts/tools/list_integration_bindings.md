List Robin's repository and Slack channel integration bindings.
Requires an authorized admin thread.
Bindings are global integration configuration, not execution environments, sandbox access grants, or image preloads.
Repository entries include `may_start_threads`; only an explicit true grants GitHub Actions permission to start threads, subject to OIDC and repository-access checks.
Slack entries include their `mentions`, `kitchen`, or `ask` mode.
