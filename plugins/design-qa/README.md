# Design QA

Design QA shows a pull request's frontend changes as before and after screenshots, beside Robin's code review.
It is an app on the web apps platform ([`docs/PLUGINS.md`](../../docs/PLUGINS.md)), built from this folder with `pnpm --filter openswe-plugin-design-qa build` into `bundle/index.html`, which you upload in **Settings → Apps**.
Add it from **Settings → Apps**; it asks for `storage`, where it keeps captures, `github:read`, to find and read pull requests, and `agent`, to ask the agent about a change.
The app itself uses only the generic app API.

## Inbox

The inbox lists open pull requests, including those without captures, in the repositories you shared with the app: **Requested from you** (GitHub's `review-requested`, which includes your teams) and **My PRs**.
Pull requests the review skipped and never captured, such as ones that change no frontend file, are hidden; **Show N skipped** lists them again.
Each tab lists 10, 15 (the default), 20, 50 or 100 pull requests a page, sorted by recent updates, highest or lowest risk, most design system issues, or newest or oldest; Design QA keeps both choices for you in its private storage.
Pull requests with no risk score or design system check follow the rest when sorting by those.
When a newer Design QA release is out, a banner at the top offers to update; the web UI confirms it, asking about any new permissions, and reloads Design QA on the new release.
One GitHub search lists every pull request, and the filter covers all of them; captures and each pull request's current state on GitHub are read only for the page shown, and a repository's captures stay cached between its pages.
The tab and page are part of the app's path (`/mine`, `/mine/2`, `/requested/3`), so going back from a pull request returns to them.
Share more repositories from the inbox; a repository you stop sharing, or can no longer read, drops out.
It reads each repository's captures in one query rather than one per pull request, shows a capture a review saves within seconds without searching GitHub again, and searches again every five minutes and when you come back to the tab.

Cards show the first paired screenshot, paired screenshot coverage, captured interaction states, the latest capture's date, and a warning when the capture is of an earlier commit than the pull request's head.
**Open PR** takes `owner/repo#number`, a GitHub pull request URL, or a bare number when you share one repository.

## Comparing

Each capture's scenarios render as cards offering **2-up**, **Swipe** and **Onion Skin** over the original screenshots, with synchronized zoom and pan (**Fit**, **100%**, **200%**, **400%**; **+**, **-** and **0** on a focused pane).
A pane scrolls only when its screenshot is larger than it; otherwise the wheel scrolls the page.
A screenshot read again, as when a review writes a capture, keeps showing until its replacement decodes.
Original screenshot colors stay independent of the app's theme; no pixel tint is painted over them.
Each card shows the share of pixels that differ between its two screenshots, which the app measures in the page once both load (`PixelChange`); it is the extent of a change, not its severity.
The recap shows the AI assessment's summary as a few short points, its risk score from 1 to 5 with one line on why (green for 1–2, amber for 3, red for 4–5, as Robin's code review scores risk), and up to three notes; selecting a note highlights its area on both screenshots, outlined in red for an error and amber for a warning.
Above it, the design system check shows as a red banner listing each violation, an amber one for warnings only, or a green one naming what it checked against.
Each finding cites the rule, the file and line, and links to the scenario that shows it, and each scenario card counts its own findings.
Inbox cards show the risk score and the design system result beside the capture status.
Captures saved before reviews scored risk or checked the design system show neither.
Replay steps, missing sides, blockers and clipping warnings stay visible on their card.
An interaction state the review recorded, such as a popover opening, plays its base and PR recordings below its screenshots.
Production, Preview and Base links keep each scenario's route and open in a new tab.

**Ask about this change** opens the agent dock on the pull request's chat with the base and PR screenshots in the composer, labelled by side, and the scenario's route, replay steps and warnings shared with the agent.
Nothing is sent until you press Send.

## Capturing

Design QA captures through its [review step](../../docs/PLUGINS.md#review-steps): a workspace adds it in **Settings → Review → Apps in reviews**, and reviews of that workspace's private repositories then follow its instructions.
The manifest's review `paths` name the frontend files Design QA captures: components, styles, markup, images and fonts, but not tests, stories, type declarations or scripts.
A review of a pull request that changes none of them leaves Design QA out and records it as skipped without running anything, so docs, backend and test-only changes never reach a capture.
The review also records `skipped`, not `blocked`, when the frontend it changes has no preview or no base to compare against; `blocked` is kept for something fixable, such as a preview still deploying or a failed sign-in.
When the diff changes what people see and a preview is up, the review works as a design system engineer: it reads the repository's `DESIGN.md` files, the UI guidance in its `AGENTS.md` files, its design system skills, and the docs and tokens of the component library the app uses (Macaw's from its installed package or `langchain-ai/macaw-design-system`), then checks every frontend line the diff changes against them: components, tokens, icons, states, themes, accessibility, layout patterns and copy.
It records each departure as an error or warning finding, scores the change's risk from 1 to 5, and keeps the recap to at most four points.
It then signs its browser in, screenshots each changed route on the base and on the preview at one viewport size, records up to three interactions whose motion matters (a popover, menu or dialog opening, a hover, a transition) on both sides with its browser's recorder, looks at each pair, and saves one capture with `save_app_document`: a document under `pulls/<number>/` with the screenshots as attachments.
Each screenshot waits for the page to settle: fonts, images in view and loading indicators finish, CSS and web animations freeze, and it's taken again until two takes in a row match.
Anything that keeps moving, such as a carousel, is hidden on both sides where it can be and named in a warning, and the review doesn't note a difference that comes only from when a page rendered.
Previews usually deploy after the review finishes, so name the check that passes once a preview is deployed under **Preview checks**; when it passes, Robin captures against that preview.
A later run of that check on the same commit that is skipped or cancelled, as when editing or labelling the pull request starts the preview's workflow again, leaves the deploy standing.
Robin has no Design QA code of its own: the instructions, and the capture document they describe, are [`review.md`](review.md), which the build puts in the manifest.
Design QA fills in what a review leaves out: a capture's id and times come from its key and the store (`StoredCapture.read`).
A capture still unfinished 15 minutes after its last write reads as failed.
Robin keeps a PNG screenshot as a lossless WebP, in its PostgreSQL or, over 256 KiB, in [object storage](../../docs/INSTALLATION.md#object-storage) where it is set up.

## Evidence

Captures live in the app's own storage, shared per repository: one document per capture in the `captures` collection, under `pulls/<number>/<id>`, where `id` is a UUIDv7 so a pull request's captures read newest first by key.
A capture document holds its base and head SHAs, base and preview URLs, routes, status and report ([`src/lib/capture.ts`](src/lib/capture.ts)).
Its screenshots and recordings (H.264 MP4, 1280×800) are attachments of that document, named by each scenario's `*_url` fields, at most 8 MiB each, and deleted with it.
A pull request's captures are deleted when it closes, merged or not.
People using the app who shared the repository and can read it on GitHub read them.
The manifest marks `captures` `"write": "app"`, so no person can change or delete a capture or its files, from the app or straight over HTTP; only reviews write them, with no person as their author.

A capture is not proof that its preview serves that commit: build verification stays "unverified" until served-build attestation exists.

## Capture recovery and release

Capture and Retry capture request the workspace-approved app review through the host's job queue.
They require the installed app's `agent` and GitHub grants, repository access, a private open PR, and automatic reviews with a configured preview check.
The backend records outcomes separately from captures; blocked, failed and skipped attempts show their reasons.
Retry requests are limited to three per PR and app per hour.
An hourly recovery pass gives each known open reviewed PR one extra preview watch, including those whose original watch expired.
It does not renew that watch indefinitely and starts at most five recovery reviews per repository per hour.
Drafts follow the author's existing draft-review preference and workspace fallback.

Publishing and merging are separate steps.
Build version 0.3.0 from this directory, publish `bundle/index.html` as a new release of the existing app, update the installed app in Settings → Apps, and approve that same release under Defaults → Apps in reviews (or the owning environment's override).
Reviews follow `review.md` and `paths` from the approved release only, so the design system check, recordings and frontend-only triage start once that release is approved, and triage once the backend that reads `paths` is deployed; an earlier backend ignores them.

Before capture, verify Preview checks names the actual deployment check, review guidelines identify a comparison base, and Review browser lists only the allowed review/base domains and an existing tool name from Robin's bot kit.
A legacy `connection/tool` sign-in setting is invalid in the current backend and must be corrected before saving settings.
Verify a current-head screenshot pair and its attachments from an actual run; a passing reviewer status alone is not capture evidence.
