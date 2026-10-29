# Design QA

Open **Design QA** in the Open SWE sidebar to browse your PRs or review assignments as cards. Filter the loaded PRs, paste a GitHub PR link, or open a card to inspect its saved visual evidence. The overview lives at `/agents/qa`; each card links to the pull request's existing review page with **Design QA** selected beside **Code review**. A shared link keeps the selected run:

```text
/agents/reviews/<owner>/<repo>/<number>?view=frontend&run=<run-id>
```

The feature uses the dashboard's existing deployment, GitHub session, and repository authorization. It does not need a separate site or DNS record. The interface uses the app's installed Macaw components, stylesheet, and semantic tokens.

Surface roles follow LangChainPlus's `smith-frontend/STYLES.md`: level 1 for the page, level 2 through Macaw's default neutral `Card`, level 3 for nested screenshot headers and canvas wells, and `bg-elevated` for labels floating over evidence. Inputs and the run picker inherit the installed Macaw controls' theme, hover, focus, and disabled styles. Original screenshot colors remain independent of the viewer's theme. No colored pixel overlay is painted over them.

For a login-free designer walkthrough, build the UI and run `pnpm --dir ui demo:design-qa`, then open `http://127.0.0.1:4189/agents/qa`. This temporary local server supplies scripted PRs, recap text, interaction checkpoints, and capture progress while exercising the real pixel viewer and chat attachment flow. Setup, walkthrough, limitations, and removal steps are documented in [`ui/scripts/design-qa-demo/README.md`](../../ui/scripts/design-qa-demo/README.md).

**AI Recap** shows the saved assessment summary and up to three evidence-linked notes when an AI review has completed. A capture summary is not presented as an AI recap while assessment is pending, not run, or failed. Design QA starts with a full-width, side-by-side comparison; **Chat** or **Ask about this change** opens the conversation. All saved comparisons appear as cards on the same page, with notes and capture details collapsed.

## Capture and inspect

Supply a base preview, the PR preview, and up to five matching routes. For LangChainPlus, the head URL defaults to `https://pr-<number>-fe.review.smith.langchain.com`. Build the base preview from the PR's exact base commit; a shared development deployment is not a reliable baseline.

**Dummy login** is the default. It saves an authentication blocker without opening a browser or submitting credentials. **Public preview** requests anonymous capture, which currently saves a network-isolation blocker before contacting the browser service. Private LangSmith pages still require a disposable QA identity adapter; a login page is blocked rather than counted as coverage. No employee browser session is reused.

Live captures are disabled until the browser provider supplies a verifiable network boundary that covers all browser targets and protocols, including workers, popups, WebSockets, and browser-local services. A sandbox external-egress allowlist alone cannot fence Chrome's loopback services. CDP request interception and backend DNS checks are only supplemental filters: they do not pin the browser's connections or cover all traffic. Enabling live capture requires a provider-enforced policy that rejects private, link-local, metadata, and loopback destinations at connection time, with regressions against DNS rebinding and the non-page channels. There is no application flag that bypasses this blocker. Stored reports, pixel comparisons, and AI assessments remain available.

Each run freezes the current PR base and head SHAs from GitHub and stores screenshots separately from other runs. A captured page is not proof that its deployment serves that commit: the report labels build verification as unverified until served-build attestation is implemented. Do not use the pixel percentage as a pass/fail verdict.

Reports from an older PR commit stay available as historical evidence, with an explicit warning that the latest changes have not been reviewed. Authentication and deployment error pages do not count as completed capture coverage.

The viewer presents original screenshots in **2-up**, **Swipe**, and **Onion Skin** modes. Swipe uses an adjustable divider; Onion Skin blends before/after with an opacity slider. Saved percentages come from capture's maximum RGBA channel distance rather than a perceptual antialiasing model; unstable data and font rendering can create noise.

Use **Fit**, **100%**, **200%**, or **400%** and drag to pan. The 2-up panes stay synchronized. A focused screenshot pane supports **+/-** for zoom and **0** for Fit. Production, Preview, and Base links preserve each scenario's route and query. Transient modal/hover state is described by replay steps rather than encoded in a source-page URL.

Select a capture and choose **Ask about this change** to prepare a message in the PR's **Chat**. It attaches the available original screenshots with their base/PR labels, the selected checkpoint's replay steps, capture blockers, layout warnings, and revision details. Review or edit the question and remove images before sending. Attachments use the original images. A missing side stays explicit, and an image-loading failure leaves the existing draft intact. Changing captures replaces an untouched suggested draft and its screenshots while preserving your own uploads.

## Interaction coverage

Agent-created reviews can include short interaction journeys selected from the PR diff and affected components. The capture tool accepts at most three journeys and five saved states across the run. It replays identical steps against base and PR in fresh contexts, in addition to the static route screenshots. No additional browser dependencies are required.

The supported actions are hover, press (capture while the mouse is held down), click (capture the resulting selected/open state), focus, type synthetic text, and Escape. Steps may name content that must become visible before capture. Setup steps do not need screenshots. The runner waits for rendering to settle, refuses missing/ambiguous/disabled/obscured targets, and records the planned action prefix and per-side blocker. Press releases away from the button to avoid accidentally clicking it. Only ordinary text/search/url inputs and textareas accept synthetic typing; password/file inputs and read-only controls are excluded. This is planned interaction replay, not unconstrained autonomous browsing or an accessibility audit.

Prefer one changed flow: for example button hover/pressed, open dialog, and filled input. Inspect dialog boundaries, clipped controls, long content, and the resulting selected state when relevant to the diff. Geometry warnings flag possible clipping by hidden/clip containers or an off-viewport dialog; they require visual assessment and are not bug verdicts. Do not submit consequential forms or mutate real customer data. Each journey starts from its requested route rather than inheriting another journey's state.

Capture details record interaction coverage, including zero paired states. Interaction screenshots appear alongside static comparisons. Expand **Interaction & notes** for replay steps; blockers and clipping warnings stay visible on their card. Missing base controls preserve PR-only evidence without inventing a baseline or pixel comparison; subsequent dependent states on that side remain incomplete. Historical static reports still work.

Journeys are supplied by the `review_frontend` agent tool, not automatically inferred by the dashboard's URL-only start form. Both static and interactive live capture remain disabled by the same provider network-isolation gate. The local fixture demonstrates saved state presentation, not authenticated LangSmith navigation or production capture.

After a successful dashboard capture, Open SWE starts an MDA assessment of the saved before/after images. The model receives each scenario's screenshots, original dimensions, and pixel metrics. Its summary and annotations are saved alongside the immutable capture. The dedicated assessment run can only read that report and submit an assessment; it cannot edit source, recapture pages, publish a code review, or post GitHub comments. Its screenshot-bearing thread is private to the initiating person under the app's existing private-thread policy and stays out of general thread lists.

Capture status, AI assessment, build verification, and video availability are separate report fields. The interface links to the assessment thread and shows when the model is running or fails. An assessment failure retains all screenshots and pixel evidence. Missing recordings or a review that has not run are shown explicitly. The viewer can render paired recordings when a capture adapter supplies them; the current browser adapter does not record video.

## Comparison workflow references

The inspection controls adapt GitHub-style 2-up/swipe/onion-skin modes and [Argos's synchronized comparison viewer](https://argos-ci.com/docs/learn/review-workflow/review-a-build). They use existing saved evidence and require no vendor account.

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

## Design QA inbox

The Design QA overview defaults to open PRs with saved runs that GitHub requests from the signed-in user or a team they belong to. It uses GitHub's `review-requested:USERNAME` search, which includes team requests; comment @mentions and Open SWE human-review assignments are separate. Completed reviews disappear from this requested scope once GitHub clears the request. **My PRs** shows saved runs on authored open PRs. Both scopes retain GitHub pagination, partial-result errors, and a loaded-results filter; load more to inspect later search pages, including when an earlier page has no saved reviews. The API intersects user-authorized search results with the latest saved run in a batch, then reads live PR heads and requested reviewers through the user's GitHub client.

Cards show paired screenshot coverage, captured interaction count, latest-run date, and earlier-commit warnings. Visual extent uses the largest paired state's saved changed-pixel fraction: small below 2%, moderate below 15%, large at or above 15%; zero is shown separately. This is not issue severity. Saved thresholds, masks, and capture dimensions affect the comparison; blocked or missing pairs have no extent label. Open a card to inspect its evidence and comparison settings.

**Open PR** accepts a positive PR number (defaults to `langchain-ai/langchainplus`), `owner/repo#number`, or a GitHub PR URL. It opens the PR's QA page, including PRs with no prior run, and retains normal repository authorization. Real capture remains blocked by the provider-isolation gate described above; only the isolated demo simulates starting a run.

### Comparison UI and detection tools

Saved scenarios render together as compact cards. Each card offers **2-up**, **Swipe**, and **Onion Skin** using the original screenshots, synchronized zoom/pan, page links, replay notes, and chat attachments. No pixel tint is painted over the images. Saved changed-pixel counts remain capture measurements; hidden capture details retain build provenance and coverage. Event invalidation updates runs without a manual refresh button. **Capture settings** opens the explicit capture form; live captures remain blocked by the existing isolation gate.

Page links preserve the scenario route and query on the recorded base and preview origins. LangChainPlus also links that route on `https://smith.langchain.com`; other repositories need an explicitly known production origin. A link identifies a page, not a deployment attestation or a recreation of hover/modal state. Replay steps describe transient state. The local demo supplies functioning loopback production/preview pages with fictional content and query parameters, never real customer URLs.

Use deterministic comparison and AI judgment together. The current capture implementation records exact channel differences; its thresholds include antialiasing noise. [pixelmatch](https://github.com/mapbox/pixelmatch) adds perceptual comparison and antialias detection in JavaScript; [odiff](https://github.com/dmtrKovalenko/odiff) is a native option for larger workloads. [axe-core](https://github.com/dequelabs/axe-core) evaluates browser DOM accessibility, including supported contrast checks, rather than screenshot design consistency. These are evaluated options, not installed integrations. Adopting an engine needs a shared versioned metric contract across capture and UI; do not silently reinterpret historical percentages.

The assessment prompt explicitly checks table alignment/density, control sizing and legibility, selected/hover/focus states, and clipped overlays. It flags visible evidence in existing per-scenario annotations. Exact token conformance requires authoritative component guidance and DOM/computed-style evidence; image-only suggestions must not claim a verified Macaw violation. Automated browser accessibility checks and runtime style measurements remain follow-ups behind the live-capture gate.
