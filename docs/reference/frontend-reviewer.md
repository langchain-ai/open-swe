# Design QA

Open **Design QA** in the Open SWE sidebar to browse your PRs or review assignments as cards. Filter the loaded PRs, paste a GitHub PR link, or open a card to inspect its saved visual evidence. The overview lives at `/agents/qa`; each card links to the pull request's existing review page with **Design QA** selected beside **Code review**. A shared link keeps the selected run:

```text
/agents/reviews/<owner>/<repo>/<number>?view=frontend&run=<run-id>
```

The feature uses the dashboard's existing deployment, GitHub session, and repository authorization. It does not need a separate site or DNS record. The interface uses the app's installed Macaw components, stylesheet, and semantic tokens.

## Capture and inspect

Supply a base preview, the PR preview, and up to five matching routes. For LangChainPlus, the head URL defaults to `https://pr-<number>-fe.review.smith.langchain.com`. Build the base preview from the PR's exact base commit; a shared development deployment is not a reliable baseline.

**Dummy login** is the default. It saves an authentication blocker without opening a browser or submitting credentials. **Public preview** requests anonymous capture, which currently saves a network-isolation blocker before contacting the browser service. Private LangSmith pages still require a disposable QA identity adapter; a login page is blocked rather than counted as coverage. No employee browser session is reused.

Live captures are disabled until the browser provider supplies a verifiable network boundary that covers all browser targets and protocols, including workers, popups, WebSockets, and browser-local services. A sandbox external-egress allowlist alone cannot fence Chrome's loopback services. CDP request interception and backend DNS checks are only supplemental filters: they do not pin the browser's connections or cover all traffic. Enabling live capture requires a provider-enforced policy that rejects private, link-local, metadata, and loopback destinations at connection time, with regressions against DNS rebinding and the non-page channels. There is no application flag that bypasses this blocker. Stored reports, pixel comparisons, and AI assessments remain available.

Each run freezes the current PR base and head SHAs from GitHub and stores screenshots separately from other runs. A captured page is not proof that its deployment serves that commit: the report labels build verification as unverified until served-build attestation is implemented. Do not use the pixel percentage as a pass/fail verdict.

Reports from an older PR commit stay available as historical evidence, with an explicit warning that the latest changes have not been reviewed. Authentication and deployment error pages do not count as completed capture coverage.

The viewer computes an exact changed-pixel mask from the original before/after screenshots. Pink pixels, optional region outlines, and review annotations are independent layers. It supports side-by-side, wipe, difference, and manual before/after blink views, adjustable highlight opacity and difference threshold, and corresponding before/after/diff crops. The comparison uses maximum RGBA channel distance rather than a perceptual antialiasing model; unstable data and font rendering can create noise.

Use **Fit**, **100%**, **200%**, or **400%** to inspect screenshots, and drag to pan. Side-by-side panes stay synchronized. **Previous change**, **Next change**, and **Focus change** center and magnify individual regions. While the comparison is focused, **H** switches between the original screenshots, **D** toggles pixel highlights, and **J/K** navigate changes. A focused screenshot pane also supports **+/-** for zoom and **0** for Fit. Blink is manual; the viewer does not flash automatically.

Expand **Ignore noisy areas** to draw up to twenty local exclusion rectangles, or exclude the selected change without dragging. Dashed overlays distinguish recorded and local exclusions. **Include ignored pixels** reveals all excluded differences; local exclusions can be removed, undone, or cleared. These controls affect only the open viewer. They never modify screenshots, recorded metrics, capture rules, or AI findings. An entirely excluded image is labeled **All pixels ignored**, rather than reported as unchanged.

The route list shows recorded change percentages, independent of viewer adjustments. **Changes and incomplete** hides completed paired captures with recorded zero differences, while retaining blocked, failed, missing, and fully excluded comparisons.

Select a capture and choose **Ask about this change** to prepare a message in the PR's **Chat**. It attaches the available original screenshots with their base/PR labels, the selected checkpoint's replay steps, capture blockers, layout warnings, and revision details. Review or edit the question and remove images before sending. Viewer overlays, local ignore regions, and crops are not attached. A missing side stays explicit, and an image-loading failure leaves the existing draft intact. Changing captures replaces an untouched suggested draft and its screenshots while preserving your own uploads.

## Interaction coverage

Agent-created reviews can include short interaction journeys selected from the PR diff and affected components. The capture tool accepts at most three journeys and five saved states across the run. It replays identical steps against base and PR in fresh contexts, in addition to the static route screenshots. No additional browser dependencies are required.

The supported actions are hover, press (capture while the mouse is held down), click (capture the resulting selected/open state), focus, type synthetic text, and Escape. Steps may name content that must become visible before capture. Setup steps do not need screenshots. The runner waits for rendering to settle, refuses missing/ambiguous/disabled/obscured targets, and records the planned action prefix and per-side blocker. Press releases away from the button to avoid accidentally clicking it. Only ordinary text/search/url inputs and textareas accept synthetic typing; password/file inputs and read-only controls are excluded. This is planned interaction replay, not unconstrained autonomous browsing or an accessibility audit.

Prefer one changed flow: for example button hover/pressed, open dialog, and filled input. Inspect dialog boundaries, clipped controls, long content, and the resulting selected state when relevant to the diff. Geometry warnings flag possible clipping by hidden/clip containers or an off-viewport dialog; they require visual assessment and are not bug verdicts. Do not submit consequential forms or mutate real customer data. Each journey starts from its requested route rather than inheriting another journey's state.

The report keeps **Interactions: Not captured** explicit for static captures. Interaction screenshots use the existing pixel comparison viewer and live in a collapsed **Interaction states** group. Opening a state reveals its planned replay steps and any blockers/clipping warnings. Missing base controls preserve PR-only evidence without inventing a baseline or pixel comparison; subsequent dependent states on that side remain incomplete. Historical static reports still work.

Journeys are supplied by the `review_frontend` agent tool, not automatically inferred by the dashboard's URL-only start form. Both static and interactive live capture remain disabled by the same provider network-isolation gate. The local fixture demonstrates saved state presentation, not authenticated LangSmith navigation or production capture.

After a successful dashboard capture, Open SWE starts an MDA assessment of the saved before/after images. The model receives each scenario's screenshots, original dimensions, and pixel metrics. Its summary and annotations are saved alongside the immutable capture. The dedicated assessment run can only read that report and submit an assessment; it cannot edit source, recapture pages, publish a code review, or post GitHub comments. Its screenshot-bearing thread is private to the initiating person under the app's existing private-thread policy and stays out of general thread lists.

Capture status, AI assessment, build verification, and video availability are separate report fields. The interface links to the assessment thread and shows when the model is running or fails. An assessment failure retains all screenshots and pixel evidence. Missing recordings or a review that has not run are shown explicitly. The viewer can render paired recordings when a capture adapter supplies them; the current browser adapter does not record video.

## Comparison workflow references

The inspection controls adapt established patterns from [Percy's visible ignored regions](https://www.browserstack.com/docs/percy/set-regions/define-regions#region-settings), [Argos's synchronized comparison viewer](https://argos-ci.com/docs/learn/review-workflow/review-a-build), and [Chromatic's Diff Inspector](https://www.chromatic.com/docs/diff-inspector). They use existing saved evidence and require no vendor account.

[Browserbase session replay](https://docs.browserbase.com/platform/browser/observability/session-replay) and [live view](https://docs.browserbase.com/platform/browser/observability/session-live-view) are useful future capture integrations. Replay, console/network inspection, live takeover, persisted review decisions, and repeat-capture stability are not implemented by these viewer controls. A provider switch alone does not satisfy the network-isolation gate.

## Application integration

The feature's dashboard API is under:

```text
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews
POST /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews/<run-id>
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews/<run-id>/media/<asset-name>
```

All reads and writes require repository access. The POST also inherits the dashboard's same-origin mutation checks. Original images and report data remain behind these endpoints; real evidence is not copied into the public UI bundle. Run records and bounded media are stored in PostgreSQL, using the same migration and deployment path as the application. This initial storage path suits a limited pilot; larger recordings should move to private object storage before expanding coverage.

The dashboard POST atomically saves a queued run and its job on Open SWE's shared PostgreSQL queue, then returns before the capture starts. The existing job worker claims one job per run across replicas, renews its lease while processing, and recovers abandoned jobs after a restart. Each phase invalidates the PR topic so the dashboard shows queued, running, and terminal progress. Leaving the page cannot trigger a late navigation, and an active start or capture stays visible when the page is reopened. A capture failure is saved as a failed run. AI assessment uses Open SWE's existing durable MDA dispatch. Interrupted captures without a queued job, and interrupted assessments, become failed after fifteen minutes when the report is next read. A frontend run does not replace the code review or create an approving GitHub review.

Ordinary agent/reviewer runs also have typed `review_frontend`, `read_frontend_review`, and `submit_frontend_review` tools. Their run token supplies the repository, PR, and revision; the model cannot select an unrelated repository or head. The dedicated dashboard assessment is further bound to one saved report.

## Rollout

1. Apply the feature's database migration with the normal Open SWE deployment process, and deploy the backend, MDA runtime, and UI together.
2. Integrate and verify the browser provider's complete network-isolation contract before replacing the capture blocker. Confirm private-network, metadata, loopback, worker, popup, WebSocket, and DNS-rebinding attempts cannot reach their destinations. Then verify one anonymous paired capture and one dummy-auth blocker.
3. Connect the existing QA harness's disposable identity and seeded workspace before enabling private LangSmith navigation.
4. Add full served-SHA verification and deterministic application readiness, seeded flags/data, and automatic journey planning from dashboard starts. Keep incomplete coverage visible.
5. Pilot a few stable surfaces before adding automatic PR/deployment triggers or a required check.

This implementation does not create a custom domain, deploy the application, submit a real login, or enable automatic review for every PR.

## Verification scope

Local checks cover the real PostgreSQL migration and evidence lifecycle, capture/token restrictions, exact pixel masks, and the shared managed graph's complete read-images/submit/stop flow using a simulated model. A real local Chrome session also captured paired synthetic pages through CDP. The built dashboard passed interactive checks with explicitly synthetic API fixtures, including mobile/dark layouts, recording playback, dummy authentication, and live assessment updates. These checks do not exercise the deployed LangSmith browser service, a live model provider, or authenticated LangSmith pages.

The UI build and focused tests pass. The standard UI typecheck has two existing `Promise.withResolvers` errors in `RequestHumanReview.test.tsx` under its ES2023 library configuration; checking with the ES2024 library passes. No package dependencies or lockfiles changed.
