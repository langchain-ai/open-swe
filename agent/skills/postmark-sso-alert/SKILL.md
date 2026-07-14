---
name: postmark-sso-alert
description: Runbook for triaging the Postmark / SSO email-verification send alert. Use this when a Slack mention or alert references "send_endpoint", "sso email verification", "email-verification/send", or a Postmark send failure on the SSO invite path. The answer is repo-structural and stable, so read this skill and jump straight to the checks instead of re-cloning the backend and re-grepping the file map from scratch.
---

# Postmark / SSO email-verification alert

You are triaging an alert on the `POST /sso/email-verification/send` path (metric
`source:send_endpoint`). This investigation has a **stable, repo-structural** answer:
the file map below does not change per incident. Do **not** re-clone the backend and
re-walk `invites.py` / `auth.py` / re-grep `send_endpoint` from cold start each time —
read this runbook, then jump to the checks in section 2.

## 1. Flow map

The send path is three hops. Confirm the current line numbers with a quick `grep`
only if the code has moved; otherwise trust the map.

- **Endpoint entry point — `auth.py`.** `POST /sso/email-verification/send` is handled by
  the `sso_email_verification_send` view. It validates the SSO session, resolves the
  target address(es), and hands off to the batch dispatch. This is where the
  `send_endpoint` request is first accepted.
- **Batch email dispatch — `invites.py`.** The view calls into the invite/verification
  batch dispatch here, which builds the verification email(s) and loops the recipients
  into the Postmark client. Bulk/partial-failure handling lives here.
- **Postmark client + metric emission.** The Postmark client wraps the outbound send;
  the `send_endpoint` metric (success/failure count, latency) is emitted around this
  call. A non-200 from Postmark is what surfaces as the alert.

## 2. Diagnostic checklist (copy-paste)

Work top to bottom — the first check resolves the majority of these alerts.

1. **Was `POSTMARK_SERVER_TOKEN` rotated recently?** This is the most common cause.
   Check the deployments repo for a recent token/secret PR and confirm the 1Password
   sync landed the new value in the environment the alert fired from. A stale/rotated
   token surfaces as Postmark 401/422 auth errors on every send.
   ```
   GH_TOKEN=dummy gh pr list --repo <org>/<deployments-repo> --state merged --limit 20 --search "POSTMARK token 1password"
   ```
2. **Classify the Postmark non-200 response.** Pull the response code + Postmark
   `ErrorCode` from the failing send and classify it:
   - `401` / `ErrorCode 10` — bad/rotated server token → go back to check 1.
   - `422` / `ErrorCode 300|400|401` — invalid request / inactive recipient / not
     allowed → payload or recipient issue, not infra.
   - `5xx` — Postmark-side outage → check Postmark status, then retry/backoff.
3. **Check rate-limit / blacklist.** Confirm the account is not rate-limited or the
   recipient domain blacklisted/suppressed on the Postmark side (suppression list,
   inactive recipients). A suppression storm looks like a spike of `send_endpoint`
   failures scoped to one domain.

## 3. Why this is a skill and not a fresh investigation

The endpoint, the dispatch, and the metric emission are fixed structure in the backend
— they do not change between incidents. The variable part is only *which* of the three
checks above is failing this time. Reading this runbook and going straight to section 2
replaces the ~cold-start file walk (read AGENTS.md, clone the backend, walk `invites.py`
and `auth.py`, grep `send_endpoint`) that each Slack thread would otherwise repeat. If
the flow map above ever drifts from the code, fix the map here so the next thread stays
fast — do not fork a one-off investigation.
