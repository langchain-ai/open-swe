# Self-only LangSmith trace access

Before starting a run, Robin's backend resolves the stored thread owner through its verified LangSmith identity and stamps ordinary `owner_ls_user_id` metadata.
Shared threads retain the original owner; private continuations use their new owner.
Retries and background runs resolve the same stored owner.
Async workers carry their coordinator's owner in run context before their first run, including before adoption.
New human-owned threads store the original owner's stable Robin user ID at creation, including dashboard, Slack, private continuation and worker threads.
Trace attribution uses that stored ID, never a current GitHub handle lookup.
Legacy login-only threads are not backfilled and receive no personal grant.
System-owned threads, missing identities or lookup failures keep tracing operational without a personal grant.

The verified OAuth `/userinfo` `sub` stored by `LangSmithAccount` / `User._claim` is LangSmith's `ls_user_id` (`smith-go/oauth/oidc_handler.go`, `UserinfoResponse.Subject`), not the separate `auth/public.user_id`.
No fresh login is needed to read that stored mapping.

The backend supplies ownership, never the client.
This uses existing run metadata and Agent Server context-to-configurable metadata propagation, before native root creation, without a protected SDK API or trace-parent isolation.
Ordinary metadata overrides are intentionally not hardened against caller tampering.
Existing traces are not relabeled.

## LangSmith rollout

1. Enable LangSmith ABAC for the organization, project-scoped permissions, and `abac_root_run_metadata_conditions` in the target deployment.
1. Remove broad `runs:read` grants from every role/team assigned to individuals; grants are additive.
   Grant `runs:read` on resource type `project`, scoped to the Robin tracing project, with this condition:

   ```json
   {
     "attribute_name": "root_run_metadata",
     "attribute_key": "owner_ls_user_id",
     "operator": "equals",
     "attribute_value": "${user.ls_user_id}"
   }
   ```

1. Grant project discovery and input/output permissions only as needed, without restoring broad trace reads.
   Verify with two non-admin users: own roots and descendants are visible; another owner's and unknown-owner traces are denied, including direct URLs and list/search.
   Verify a shared thread, private continuation, worker and retry before enabling personal access.

No live permissions or deployment are changed here.
Robin's operational service key remains privileged, and its existing authorized collaborative thread access is unchanged.
Service credentials must never be exposed to personal clients.
