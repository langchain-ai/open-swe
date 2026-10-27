# Frontend reviewer

Frontend review lives on a pull request's existing Open SWE review page. Select **Frontend reviewer** beside **Code review** to start a capture or inspect an earlier run. A shared link keeps the selected run:

```text
/agents/reviews/<owner>/<repo>/<number>?view=frontend&run=<run-id>
```

The feature uses the dashboard's existing deployment, GitHub session, and repository authorization. It does not need a separate site or DNS record. The interface uses the app's installed Macaw components, stylesheet, and semantic tokens.

## Capture and inspect

Supply a base preview, the PR preview, and up to five matching routes. For LangChainPlus, the head URL defaults to `https://pr-<number>-fe.review.smith.langchain.com`. Build the base preview from the PR's exact base commit; a shared development deployment is not a reliable baseline.

**Dummy login** is the default. It saves an authentication blocker without opening a browser or submitting credentials. **Public preview** explicitly opts into anonymous capture. Private LangSmith pages still require a disposable QA identity adapter; a login page is blocked rather than counted as coverage. No employee browser session is reused.

Each run freezes the current PR base and head SHAs from GitHub and stores screenshots separately from other runs. A captured page is not proof that its deployment serves that commit: the report labels build verification as unverified until served-build attestation is implemented. Do not use the pixel percentage as a pass/fail verdict.

Reports from an older PR commit stay available as historical evidence, with an explicit warning that the latest changes have not been reviewed. Authentication and deployment error pages do not count as completed capture coverage.

The viewer computes an exact changed-pixel mask from the original before/after screenshots. Pink pixels, optional region outlines, and review annotations are independent layers. It supports side-by-side, wipe, and difference views, adjustable highlight opacity and difference threshold, and corresponding before/after/diff crops. The comparison uses maximum RGBA channel distance rather than a perceptual antialiasing model; unstable data and font rendering can create noise.

After a successful dashboard capture, Open SWE starts an MDA assessment of the saved before/after images. The model receives each scenario's screenshots, original dimensions, and pixel metrics. Its summary and annotations are saved alongside the immutable capture. The dedicated assessment run can only read that report and submit an assessment; it cannot edit source, recapture pages, publish a code review, or post GitHub comments. Its screenshot-bearing thread is private to the initiating person under the app's existing private-thread policy and stays out of general thread lists.

Capture status, AI assessment, build verification, and video availability are separate report fields. The interface links to the assessment thread and shows when the model is running or fails. An assessment failure retains all screenshots and pixel evidence. Missing recordings or a review that has not run are shown explicitly. The viewer can render paired recordings when a capture adapter supplies them; the current browser adapter does not record video.

## Application integration

The feature's dashboard API is under:

```text
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews
POST /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews/<run-id>
GET  /dashboard/api/reviews/<owner>/<repo>/<number>/frontend-reviews/<run-id>/media/<asset-name>
```

All reads and writes require repository access. The POST also inherits the dashboard's same-origin mutation checks. Original images and report data remain behind these endpoints; real evidence is not copied into the public UI bundle. Run records and bounded media are stored in PostgreSQL, using the same migration and deployment path as the application. This initial storage path suits a limited pilot; larger recordings should move to private object storage before expanding coverage.

On-demand capture is bounded and awaited, so it does not depend on a process-local background queue. A capture failure is saved as a failed run. AI assessment uses Open SWE's existing durable MDA dispatch. Interrupted captures and assessments become failed after fifteen minutes when the report is next read. A frontend run does not replace the code review or create an approving GitHub review.

Ordinary agent/reviewer runs also have typed `review_frontend`, `read_frontend_review`, and `submit_frontend_review` tools. Their run token supplies the repository, PR, and revision; the model cannot select an unrelated repository or head. The dedicated dashboard assessment is further bound to one saved report.

## Rollout

1. Apply the feature's database migration with the normal Open SWE deployment process, and deploy the backend, MDA runtime, and UI together.
2. Confirm the configured LangSmith browser service can create an isolated browser. Verify one anonymous paired capture and one dummy-auth blocker.
3. Connect the existing QA harness's disposable identity and seeded workspace before enabling private LangSmith navigation.
4. Add full served-SHA verification and deterministic route readiness, interaction journeys, flags, and data. Keep incomplete coverage visible.
5. Pilot a few stable surfaces before adding automatic PR/deployment triggers or a required check.

This implementation does not create a custom domain, deploy the application, submit a real login, or enable automatic review for every PR.

## Verification scope

Local checks cover the real PostgreSQL migration and evidence lifecycle, capture/token restrictions, exact pixel masks, and the shared managed graph's complete read-images/submit/stop flow using a simulated model. A real local Chrome session also captured paired synthetic pages through CDP. The built dashboard passed interactive checks with explicitly synthetic API fixtures, including mobile/dark layouts, recording playback, dummy authentication, and live assessment updates. These checks do not exercise the deployed LangSmith browser service, a live model provider, or authenticated LangSmith pages.

The UI build and focused tests pass. The standard UI typecheck has two existing `Promise.withResolvers` errors in `RequestHumanReview.test.tsx` under its ES2023 library configuration; checking with the ES2024 library passes. No package dependencies or lockfiles changed.
