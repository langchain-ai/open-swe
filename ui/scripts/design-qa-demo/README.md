# Temporary designer demo

This disposable, local-only demo runs the real Open SWE UI. No login, database, browser provider, API key, or live model is needed. The server listens on `127.0.0.1` and never forwards requests to another service.

From the repository root, with workspace dependencies installed:

```sh
pnpm --dir ui build
pnpm --dir ui demo:design-qa
```

Open <http://127.0.0.1:4189/agents/qa>. For another port, run `pnpm --dir ui demo:design-qa 4190`. Stop with Ctrl+C. Rebuild and reload after UI edits; restart the server after fixture edits.

## Walkthrough

1. The **For you & your teams** inbox has four saved reviews: a direct request, requests to the fictional `design` and `frontend` teams, and an authentication blocker. Cards show paired-state coverage, interaction counts, requested reviewers, capture date, and **Small/Large visual change**. The labels describe the largest changed-pixel fraction (small <2%, moderate <15%, large ≥15%), not issue severity; thresholds may differ between captures. The stale card is visibly marked. **My PRs** shows a separate authored example. Filter by title, repo, or number, then clear the filter.
2. Open **Refine trace controls and project dialog**. Read **AI Recap**, then use **Inspect** to jump to its evidence.
3. On **Button color · subtle shift**, exact comparison highlights an 8-level RGB change. Expand **Display and sensitivity** and move **Difference threshold** from 0 to 18: the faint change disappears. Return it to 0. This demonstrates a real sensitivity tradeoff.
4. Select **Button size · 2 px taller**. Use **Pixel diff**, **200%** or **400%**, and **Focus change** to inspect the extra two pixels. Try **Wipe** or **Blink** to compare originals.
5. Expand **Interaction states**. Inspect **Hover**, **Pressed**, **Open dialog**, and **Filled input**. The hover example loses text contrast; the candidate dialog extends below the viewport. These are authored state examples, not an agent navigating a preview.
6. Choose **Ask about this change**. The real chat composer opens with the selected PNGs and replay steps. Edit/remove attachments. Sending deliberately returns a demo-only error: no model is connected and nothing is transmitted elsewhere.
7. Enter **43399** (or **#43399**) in the lookup and choose **Open PR** to open **Polish project settings**. Numbers default to `langchain-ai/langchainplus`; a full `owner/repo#number` reference or GitHub PR URL can target another repository in the real app. This demo serves only fictional PRs 43396–43401, never GitHub. Start a **New Design QA review**, enter syntactically valid preview URLs, and submit. The server simulates queued → capturing → AI reviewing → ready in about four seconds. The URLs and auth selection do not cause a login or browser request.
8. Return to the inbox: the new scripted review now appears because it is requested from you. Visiting `/__demo/reset` restores the initial cards, including the protected-preview blocker. State is memory-only and also resets on restart.

## Real versus scripted

**Real:** the React interface, Macaw components/tokens, pixel calculation, sensitivity/opacity controls, crops, synchronized zoom/pan, local masks, keyboard shortcuts, evidence selection, and chat attachment preparation.

**Scripted:** PR numbers (including LangChainPlus references), authors, review requests, team membership, PRs, review states/timings, recap prose, annotations, replay-step descriptions, clipping warnings, and image content. There is no live AI assessment, deployment/SHA verification, autonomous interaction, video recording, or real GitHub activity. The recap labels its authored findings; this README records the scripted behavior. Blocked captures do not count as reviewed coverage.

`fixtures.mjs` contains synthetic SVG sources and report metadata. `assets/` contains 12 rasterized PNGs plus metrics computed with the application's `comparePixels` at threshold 0. Images depict a fictional workspace and contain no captured product/customer data. The development-only source gallery is `/__demo/fixtures`. To regenerate PNGs, rasterize `snapshot(id, side)` using Sharp or another SVG renderer, then recompute `metrics.json` with `comparePixels`. A rasterizer is not needed to run the demo. Missing assets fail startup so the evidence cannot silently change.

Exact mode exposes any different pixel, including antialiasing and unstable data. Production uses threshold 18 unless a saved report specifies another threshold. Regions smaller than `min_region_pixels` may not get an outline even though their pixels are counted. A pixel difference is evidence to inspect, not a design-quality verdict.

## Remove the demo

Delete `ui/scripts/design-qa-demo/`, remove `demo:design-qa` from `ui/package.json`, and remove the local-demo paragraph in `docs/reference/frontend-reviewer.md`. No production UI/backend code imports this folder. The mock session, fake activity, and reset endpoint exist only in this server. There is no feature flag, schema field, migration, or authentication bypass to remove.

Keep **AI Recap**, its evidence links, full-width comparisons, and collapsed advanced controls: they work with normal saved reports and contain no scripted responses.
