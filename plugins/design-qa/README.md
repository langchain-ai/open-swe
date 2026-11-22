# Design QA

Design QA shows a pull request's frontend changes as before and after screenshots, beside Robin's code review.
It is an app on the web apps platform ([`docs/PLUGINS.md`](../../docs/PLUGINS.md)), built from this folder with `pnpm --filter openswe-plugin-design-qa build` into `bundle/index.html`, which you upload in **Settings → Apps**.
Add it from **Settings → Apps**; it asks for `storage`, where it keeps captures, `github:read`, to find and read pull requests, and `agent`, to ask the agent about a change.
The app itself uses only the generic app API.

## Inbox

The inbox lists open pull requests, including those without captures, in the repositories you shared with the app: **Requested from you** (GitHub's `review-requested`, which includes your teams) and **My PRs**.
**People** lists anyone's open pull requests: search the people Robin knows by name or GitHub handle and pick one; the tab's path names them (`/people/<login>`), so going back returns to them.
**Pinned** lists eligible pull requests you pinned from a card or a pull request's page, closed ones included; each card is read from GitHub.
Pins stay in your private storage until you unpin them, even while a card is hidden; a pin in a repository you stop sharing is hidden until you share it again.
All tabs, including Pinned, omit skipped and unsuccessful captures before counting, sorting and pagination; there is no visibility switch.
A newer queued attempt or a preview known to be deploying remains visible even if an earlier capture failed or saved no paired screenshots.
Older outcomes do not hide a newer pending or running capture.
Saved paired screenshots remain visible after a later failed or blocked attempt, but a newer skipped outcome hides the card, including its pin.
Each tab lists 10, 15 (the default), 20, 50 or 100 pull requests a page, sorted by recent updates, highest or lowest risk, most design system issues, or newest or oldest; Design QA keeps both choices for you in its private storage.
Pull requests with no risk score or design system check follow the rest when sorting by those.
When a newer Design QA release is out, a banner at the top offers to update; the web UI confirms it, asking about any new permissions, and reloads Design QA on the new release.
GitHub's search is read a page at a time: the first page's cards show as soon as it answers while the rest load, and until then the tab shows the list its last search found, kept in your private storage.
The filter and sorts cover every pull request loaded so far.
Every shared repository's captures load beside the search, the other tabs' first page and your pins load in the background, and each pull request's current state on GitHub is read only for a card that has a capture or a pin, on the page shown and the next one, filling in its card as it answers.
The tab and page are part of the app's path (`/mine`, `/mine/2`, `/requested/3`), so going back from a pull request returns to them.
Share more repositories from the inbox; a repository you stop sharing, or can no longer read, drops out.
It reads each repository's captures in one query rather than one per pull request, shows a capture a review saves within seconds without searching GitHub again, and searches again every five minutes and when you come back to the tab.

Cards show the first paired screenshot, in the dashboard's theme when the review shot it in both, the title, who opened the pull request and the latest capture's date, with badges only for what needs attention: a later capture attempt that was blocked or failed (its reason on hover), a capture of an earlier commit than the pull request's head, a capture or review still running, the textual risk score and the design system result.
Previews known to be deploying read **Preview pending**, not capture errors.
**Open PR** takes `owner/repo#number`, a GitHub pull request URL, or a bare number when you share one repository, and opens it when you press **Open PR** or Enter; pasting only fills the field.

Design QA counts what people do in it, by action name only (switching tabs, picking a person, opening a pull request, pinning, requesting a capture, switching theme or comparison, asking the agent, opening GitHub or Robin's PR view), with `analytics.track` in Robin's product analytics.

## Comparing

Each capture's scenarios render as cards offering **2-up**, **Swipe** and **Onion Skin** over the original screenshots, with synchronized zoom and pan (**Fit**, **100%**, **200%**, **400%**; **+**, **-** and **0** on a focused pane).
A pane scrolls only when its screenshot is larger than it; otherwise the wheel scrolls the page.
A screenshot read again, as when a review writes a capture, keeps showing until its replacement decodes.
Original screenshot colors stay independent of the app's theme; no pixel tint is painted over them.
A scenario the review also shot in dark mode gets a **Light**/**Dark** switch on its card; it starts on the dashboard's theme, changes only that card, and its comparison, pixel change and **Ask about this change** use the pair shown.
Notes stay where the review placed them on the light screenshot, at the same viewport in both themes.
Each card shows the share of pixels that differ between its two screenshots, which the app measures in the page once both load (`PixelChange`); it is the extent of a change, not its severity.
Once every captured theme of a completed scenario compares below 0.1%, that scenario is omitted from scenario cards, counts and covers; an inbox entry with no remaining scenarios is hidden.
The inbox compares only the current page and waits for its initial comparisons before showing cards.
Its candidate counts, sorting and page boundaries use capture metadata before pixel filtering, so comparisons cannot move PRs between pages; a page can be empty without removing its navigation.
Saved media and comparison verdicts are reused when another capture changes.
Exactly 0.1%, incomplete captures and comparisons that cannot be measured stay visible.
Saved capture documents are unchanged.
A pull request's page names who opened it, and its title opens it on GitHub in a new tab, as does the **GitHub** link beside **Pin**; **Robin PR view** opens Robin's review of it.
The **PR Description** card keeps the risk score from 1 to 5 and design system badge visible; its dropdown opens the actual PR description as sanitized Markdown, the risk reason and findings.
Risk and design system badges open the same details; each finding includes its severity, title, file, explanation, rule and scenario name.
The header groups **Open preview**, capture retry and the review-run selector with compact controls; screenshot zoom is capped at 400%.
Saved highlight boxes and notes show by default, and **Hide hints** turns them off per card without resetting zoom.
Clicking a screenshot opens its unannotated original at natural pixel size in a Macaw Dialog; the swipe divider has a compact keyboard focus grip.
The latest capture attempt's reason shows above the captures only when it didn't capture, such as a blocked or failed attempt.
Captures saved before reviews scored risk or checked the design system show neither.
Replay steps, missing sides, blockers and clipping warnings stay visible on their card.
An interaction state the review recorded, such as a popover opening, plays its base and PR recordings below its screenshots.
Production, Preview and Base links keep each scenario's route and open in a new tab.

**Ask about this change** opens the agent dock on the pull request's chat with the base and PR screenshots in the composer, labelled by side, and the scenario's route, replay steps and warnings shared with the agent.
Nothing is sent until you press Send.

## Capturing

Design QA captures through its [review step](../../docs/PLUGINS.md#review-steps): an instance admin adds it in **Defaults → Apps in reviews** (`/settings`), and reviews of private repositories then follow its approved release's instructions.
The manifest's review `paths` name the frontend files Design QA captures: components, styles, markup, images and fonts, but not tests, stories, type declarations or scripts.
A review of a pull request that changes none of them leaves Design QA out and records it as skipped without running anything, so docs, backend and test-only changes never reach a capture.
The review reserves `skipped` for genuinely nonvisual changes.
A missing preview/base is a blocked comparison, a known build in progress is **Preview pending**, and a failed build or sign-in keeps its concrete actionable error.
Shared library components are traced to running consumers or their real Storybook stories, not dismissed because they lack an app route; unavailable evidence is never replaced by fabricated screenshots.
When the diff changes what people see and a preview is up, the review works as a design system engineer: it reads the repository's `DESIGN.md` files, the UI guidance in its `AGENTS.md` files, its design system skills, and the docs and tokens of the component library the app uses (Macaw's from its installed package or `langchain-ai/macaw-design-system`), then checks every frontend line the diff changes against them: components, tokens, icons, states, themes, accessibility, layout patterns and copy.
It records each departure as an error or warning finding, scores the change's risk from 1 to 5, and keeps the recap to at most four points.
Before it opens a browser, it maps every surface the diff reaches from the code: it follows each changed component, hook, style and asset through its importers to the routes, layouts and shared chrome (headers, top bars, sidebars) that render it, notes what gates each one (feature flags, permissions, data), and puts the places the pull request's description targets first; a placement in shared chrome is shot once, on the app's home page.
It reaches gated surfaces by picking an organization or workspace where they show, or with the app's own client-side flag override, never by changing server data, and lists each surface it couldn't reach, with the gate that hid it, under **Not captured** on the pull request's page.
It then signs its browser in, screenshots up to fifteen of those surfaces on the base and on the preview at one viewport size, in light mode and again in dark (switching the page with `prefers-color-scheme`, or the app's own theme setting when it ignores that, and leaving dark out when a side has none), records up to three interactions whose motion matters (a popover, menu or dialog opening, a hover, a transition) on both sides with its browser's recorder, looks at each pair, and saves one capture with `save_app_document`: a document under `pulls/<number>/` with the screenshots as attachments.
Each screenshot waits for the page to settle: fonts, images in view and loading indicators finish, CSS and web animations freeze, and it's taken again until two takes in a row match.
Anything that keeps moving, such as a carousel, is hidden on both sides where it can be and named in a warning, and the review doesn't note a difference that comes only from when a page rendered.
Once a capture is saved and marked complete, Robin's existing GitHub review publication includes an authenticated Design QA page link, even when the capture found no bugs.
The generic manifest `review.path` supplies its route; `save_app_document` returns the app URL only after a successful save, and publication verifies a current-run/current-head completed result and its stored document.
Previously published result markers prevent repeat links after a publication retry; no screenshot attachment or bearer URL is copied to GitHub.
Opening the link still requires an installation, repository sharing and the reader's GitHub access.
For implementation PRs, Robin's canonical pre-PR instructions organize screenshot evidence by named scenario and visible state, with the real route/story, replay steps, viewport/theme and matched Base/PR images, and reference an actual saved Design QA comparison when available.
A PR description does not claim a capture exists just because the app is installed.

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

Capture and the compact refresh-icon **Retry capture** action request the globally approved app review through the host's job queue.
Retry is not offered while capture is queued/running or its current preview is known to be deploying.
After 90 minutes without a fresh preview outcome, its state needs refreshing and manual retry is available again; automatic preview watches are bounded.
A capture blocked only by a known deployment or preview propagation can explicitly request a bounded retry; the preview job starts at most three runs without needing a new webhook.
Missing permissions, a failed build and an unsupported preview remain actionable blockers, not automatic retries.
They require the installed app's `agent` and GitHub grants, repository access, a private open PR, and automatic reviews with a configured preview check.
The backend records outcomes separately from captures; blocked, failed and skipped attempts show their reasons.
Retry requests are limited to three per PR and app per hour.
An hourly recovery pass gives each known open reviewed PR one extra preview watch, including those whose original watch expired.
It does not renew that watch indefinitely and starts at most five recovery reviews per repository per hour.
Drafts follow the author's existing draft-review preference, falling back to the instance default.

Publishing and merging are separate steps.
Build version 0.7.0 from this directory, publish `bundle/index.html` as a new release of the existing app, update the installed app in Settings → Apps, and have an instance admin approve that same release globally under Defaults → Apps in reviews.
Reviews follow `review.md` and `paths` from the approved release only, so the design system check, recordings and frontend-only triage start once that release is approved, and triage once the backend that reads `paths` is deployed; an earlier backend ignores them.
Dark captures start once 0.5.0 is approved, and surface mapping and **Not captured** once 0.6.0 is; 0.6.0's review instructions exceed 8,000 characters, so publish it only once the backend that accepts up to 16,000 is deployed; the **People** tab needs a web UI and backend with `people.search` and `pulls.search`'s `author`, and fails to search on an earlier one; reading the inbox's search a page at a time and **Robin PR view** need a web UI and backend with the paged search and `nav.openReview`, and an earlier web UI reads every page at once.

The backend must support `review.path`, returned app result URLs, completed-result publication and retryable outcomes before approving a release that uses them.
Public repositories are deliberately ineligible for automated app captures, even if their Storybook preview is publicly readable; enabling them needs a separate browser trust-boundary design, not a prompt change.
For a component library, verify the exact deployed story id and package version; a screenshot of the PR's story is not a matched base/PR comparison or a saved Design QA capture.

Before capture, verify Preview checks names the actual deployment check, review guidelines identify a comparison base, and Review browser lists only the allowed review/base domains and an existing tool name from Robin's bot kit.
A legacy `connection/tool` sign-in setting is invalid in the current backend and must be corrected before saving settings.
Verify a current-head screenshot pair and its attachments from an actual run; a passing reviewer status alone is not capture evidence.
