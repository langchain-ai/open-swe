# Design QA

Design QA shows a pull request's frontend changes as before and after screenshots, beside Open SWE's code review.
It is an app on the web apps platform ([`docs/PLUGINS.md`](../PLUGINS.md)), built from [`plugins/design-qa`](../../plugins/design-qa).
Add it from **Settings → Apps**; it asks for `frontend-reviews`, to read captures, and `agent`, to ask the agent about a change.

## Inbox

The inbox lists open pull requests with captures in the repositories you shared with the app: **Requested from you** (GitHub's `review-requested`, which includes your teams) and **My PRs**.
Share more repositories from the inbox; a repository you stop sharing, or can no longer read, drops out.

Cards show paired screenshot coverage, captured interaction states, the latest capture's date, and a warning when the capture is of an earlier commit than the pull request's head.
Visual extent uses the largest paired state's changed-pixel fraction: small below 2%, moderate below 15%, large at or above 15%; zero is labelled "No changes above threshold".
It is a capture measurement, not issue severity.
**Open PR** takes `owner/repo#number`, a GitHub pull request URL, or a bare number when you share one repository.

## Comparing

Each capture's scenarios render as cards offering **2-up**, **Swipe** and **Onion Skin** over the original screenshots, with synchronized zoom and pan (**Fit**, **100%**, **200%**, **400%**; **+**, **-** and **0** on a focused pane).
Original screenshot colors stay independent of the app's theme; no pixel tint is painted over them.
The recap shows the AI assessment's summary and up to three notes; selecting a note highlights its area on both screenshots.
Replay steps, missing sides, blockers and clipping warnings stay visible on their card.
Production, Preview and Base links keep each scenario's route and open in a new tab.

**Ask about this change** opens the agent dock on the pull request's chat with the base and PR screenshots in the composer, labelled by side, and the scenario's route, replay steps and warnings shared with the agent.
Nothing is sent until you press Send.

## Evidence

Captures live in Open SWE's PostgreSQL: `frontend_review_run` holds a capture's frozen base and head SHAs, routes, interaction journeys, status and report, and `frontend_review_media` its screenshots and recordings, at most 8 MiB each.
Only Open SWE writes them.
Apps read them through `/plugins/{installation}/frontend-reviews/…`, which checks the installation's `frontend-reviews` permission, that the repository is shared with it, and the person's own GitHub read access on every call.
Media is served `private, no-store`, with `nosniff` and `default-src 'none'`.

Live capture and the AI assessment that follows it stay off until the browser service enforces network isolation for all browser traffic, including loopback services, workers, popups and WebSockets.
Until then no new captures are made, and pull requests without one show that capture is unavailable.
A capture is not proof that its deployment serves that commit: build verification stays "unverified" until served-build attestation exists.
