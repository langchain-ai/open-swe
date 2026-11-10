# Design QA

Design QA shows a pull request's frontend changes as before and after screenshots, beside Robin's code review.
It is an app on the web apps platform ([`docs/PLUGINS.md`](../PLUGINS.md)), built from [`plugins/design-qa`](../../plugins/design-qa).
Add it from **Settings → Apps**; it asks for `storage`, where it keeps captures, `github:read`, to find and read pull requests, and `agent`, to ask the agent about a change.
The app itself uses only the generic app API.

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

## Capturing

Design QA captures through its [review step](../PLUGINS.md#review-steps): a workspace adds it in **Settings → Review → Apps in reviews**, and reviews of that workspace's private repositories then follow its instructions.
When the diff changes what people see and a preview is up, the review signs its browser in, screenshots each changed route on the base and on the preview at one viewport size, diffs each pair with `compare_images`, and saves one capture with `save_app_document`: a document under `pulls/<number>/` with the screenshots and their differences as attachments.
Robin has no Design QA code of its own: the instructions, and the capture document they describe, are `plugins/design-qa/review.md`, which the build puts in the manifest.
Design QA fills in what a review leaves out: a capture's id and times come from its key and the store (`captureOf`).
A capture still unfinished 15 minutes after its last write reads as failed.
Screenshots stay in Robin's PostgreSQL, or, over 256 KiB, in [object storage](../INSTALLATION.md#object-storage) where it is set up.

## Evidence

Captures live in the app's own storage, shared per repository: one document per capture in the `captures` collection, under `pulls/<number>/<id>`, where `id` is a UUIDv7 so a pull request's captures read newest first by key.
A capture document holds its base and head SHAs, base and preview URLs, routes, status and report (`plugins/design-qa/src/lib/capture.ts`).
Its screenshots and their pixel differences are attachments of that document, named by each scenario's `*_url` fields, at most 8 MiB each, and deleted with it.
A pull request's captures are deleted when it closes, merged or not.
People using the app who shared the repository and can read it on GitHub read them.
The manifest marks `captures` `"write": "app"`, so no person can change or delete a capture or its files, from the app or straight over HTTP; only reviews write them, with no person as their author.

A capture is not proof that its preview serves that commit: build verification stays "unverified" until served-build attestation exists.
