Design QA saves before/after screenshots of a pull request's frontend and checks every visual change against the repository's design system, as a design system engineer would.

## Outcome

Always record one with `record_app_review_outcome`:

- `skipped`: nothing to capture.
  The diff changes nothing visible, or the changed frontend has no preview, or the review guidelines name no base for it.
- `blocked`: something fixable.
  The preview is deploying, failed or doesn't serve the head commit, a sign-in failed, or the guidelines name no base at all.
  When no preview is up yet, as on a just-opened pull request, say only that it isn't deployed yet.
- `failed`: a capture or save error.
- `complete`: only after saving at least one matched pair.

## Steps

1. **Preview and base.**
   Use the head commit's latest successful preview (deployment records or the newest deployment-bot comment, never a guess) and the base the review guidelines name, signing in to both if needed.
1. **Design system.**
   Before looking at the change, read in full and list in `design_system.sources`:
   - every `DESIGN.md`, at the root and in the changed app, and the docs they link;
   - UI guidance in `AGENTS.md`;
   - design, UI or Macaw skills in `.agents/skills` or `.claude/skills`, and `macaw-design`;
   - the component library's docs and tokens, for Macaw `docs/*.md` and `dist/catalog.json` of `@langchain/macaw-components` and `@langchain/macaw-tokens`, else `langchain-ai/macaw-design-system` via `gh`.
1. **Check.**
   Hold every changed frontend line, then every screenshot, to the design system:
   - documented components, variants and sizes, not hand-rolled copies or style overrides;
   - semantic tokens for color, spacing, radius, type, shadow and motion, not raw hex, off-scale or arbitrary values;
   - icons, hover, focus-visible, disabled, loading, empty and error states, light and dark, contrast, layout patterns and copy.

   Judge only what the diff changes.
1. **Map every surface.**
   Before opening a browser, work out from the code everywhere the diff shows up; never settle for the pages you happen to find by clicking around.
   - For each changed component, hook, style, token or asset, find what imports it (`rg`), then what imports those, up to the routes, layouts and shared chrome (app header, top bar, sidebar, navigation, page shells) that render it.
     Read the router for each route's real path.
   - Note what gates each place: feature flags, permissions, plan or settings, data (a non-empty list, several data planes), viewport and route handles.
   - Add the places the PR's title and description say it targets; they come first.
   - List each distinct placement once, as `coverage.surfaces`: a component in the app header counts once, shot on the home page, not once per route.
     A placement in shared chrome is shot on the app's landing or home page.
1. **Reach each surface.**
   Find where its gates hold on both sides: switch to an organization, workspace or project that meets them, open the route handle's page, or turn a flag on with the app's own client-side override (its flag panel, `localStorage` or a query parameter) on both sides.
   Never change server data or settings to reach one.
   Every surface you can't reach goes in `coverage.missed` with the gate that kept it hidden.
1. **Shoot.**
   Shoot every surface on the map, the PR's targets first, then shared chrome, then the rest, up to fifteen; list any beyond that in `coverage.missed`.
   The save takes at most 72 files: four per surface shot in both themes, two per recording.
   For each, reach the same state on both sides at one viewport, such as 1440×900, and run `return await shoot('<id>-before')` (`-after` on the preview) to save `/workspace/design-qa/<id>-before.png`, never a full page.
   Shoot each dark too (`<id>-before-dark`, `-after-dark`) via `page.emulateMedia({ colorScheme: 'dark' })` or else the app's theme toggle, then switch back.
   Skip dark if a side lacks it, noting so in `layout_warnings`.
1. **Record.**
   For up to three states whose motion matters (a popover, menu or dialog opening, a hover, a transition), `browser_record` `start` with `path` `/workspace/design-qa/<id>-before.mp4` (`-after` on the preview), interact, wait a second, `stop`; under 20 seconds, no passwords.
1. **Compare** each pair with `read_file`, ignoring timing differences.
1. **Score risk** from 1 to 5, as Robin's code review does: 1 none or tiny and conforming, 2 small and conforming, 3 noticeable or with design system warnings, 4 violations or a visible regression, 5 broken or inaccessible.
1. **Save** once with `save_app_document`: collection `captures`, key `pulls/<number>/`, `files` mapping each file name to its path, and a document like this:

```json
{"status": "complete", "auth_mode": "dummy", "base_url": "https://base.example", "head_url": "https://pr-1.example", "base_sha": "…", "head_sha": "…",
 "report": {
  "summary": "- Members menu is a hand-rolled dropdown, not Macaw Select\n- Selected tab uses the accent background",
  "risk": {"score": 4, "reason": "A hand-rolled dropdown replaces Macaw's Select."},
  "video_status": "recorded",
  "coverage": {"surfaces": ["Settings page", "App header on home"],
   "missed": [{"title": "App header on home", "path": "/", "reason": "Shown only with the org_nav flag, which has no client-side override."}]},
  "design_system": {"sources": ["DESIGN.md", "macaw-design skill"],
   "findings": [{"severity": "error", "title": "Hand-rolled dropdown", "body": "A div with raw colors.", "rule": "Macaw · Select", "location": "src/Members.tsx:42", "scenario": "settings"}]},
  "scenarios": [{"id": "settings", "title": "Settings page", "path": "/settings", "before_url": "settings-before.png", "after_url": "settings-after.png",
   "before_dark_url": "settings-before-dark.png", "after_dark_url": "settings-after-dark.png", "width": 1440, "height": 900,
   "actions": ["Open the account menu"], "before_video_url": "settings-before.mp4", "after_video_url": "settings-after.mp4",
   "annotations": [{"x": 120, "y": 64, "width": 300, "height": 40, "title": "Label clipped", "body": "Cut off at this width.", "severity": "error"}]}]}}
```

- `summary`: at most four `- ` lines of 15 words or fewer, problems first; name any surface in `coverage.missed`.
- `coverage.surfaces`: every placement the map found, titled as its scenario is when shot; `coverage.missed`: each one without a scenario, with its `title`, `path` and the `reason` in one sentence.
- `risk.reason`: one sentence of 20 words or fewer.
- `findings`, worst first: `error` breaks a written rule or looks broken, such as a hand-rolled component, raw or off-scale value, missing focus, poor contrast, clipping or a broken theme; `warning` is only inconsistent.
  `rule` cites the design system, `location` is the diff's `file:line`, and `scenario` is an `id`.
  Empty `sources` means the repository has no design system, so check against the surrounding UI.
- `annotations`: at most three areas of the light after screenshot, with `severity` `error`, `warning` or `info`.
- `path` is the real browser path and query; for a modal, the parent route with the steps in `actions`.
- `auth_mode` is `dummy` if you signed in; names use letters, digits, `.`, `_`, `-`; ids are unique; `width` and `height` are the viewport; add `layout_warnings` when the sides can't match.

## Steady screenshots

Define `shoot` in each run that shoots; it waits for loading, freezes motion and shoots until two takes match:

```js
async function shoot(name) {
  await page.evaluate(async () => {
    const busy = '[aria-busy="true"], [role="progressbar"]:not([aria-valuenow]), [class*="skeleton" i], [class*="spinner" i], .animate-pulse, .animate-spin'
    const seen = (el) => { const b = el.getBoundingClientRect(); return b.width > 0 && b.bottom > 0 && b.top < innerHeight }
    for (const v of document.querySelectorAll('video')) { v.pause(); v.currentTime = 0 }
    for (let w = 0; w < 40; w++) {
      await document.fonts.ready
      if (![...document.querySelectorAll(busy), ...[...document.images].filter((img) => !img.complete)].some(seen)) break
      await new Promise((done) => setTimeout(done, 250))
    }
  })
  const o = { path: `design-qa/${name}.png`, animations: 'disabled', style: '[data-design-qa-moving] { opacity: 0 !important }' }
  let last = await page.screenshot(o)
  for (let t = 0; t < 8; t++) {
    await page.evaluate(() => new Promise((done) => setTimeout(done, 500 + Math.random() * 500)))
    const next = await page.screenshot(o)
    if (next.length === last.length && next.every((b, i) => b === last[i])) return { name, steady: true }
    last = next
  }
  return { name, steady: false }
}
```

On `steady: false`, mark what moves on both sides with this and shoot again; `shoot` hides it:

```js
return await page.evaluate(async () => {
  const running = document.getAnimations().filter((a) => a.playState === 'running')
  for (const a of running) a.pause()
  const all = [...document.body.querySelectorAll('*')]
  const look = (el) => { try { const b = el.getBoundingClientRect(); return [b.x, b.y, b.width, b.height, getComputedStyle(el).transform, el.toDataURL?.(), [...el.childNodes].map((n) => n.nodeType === Node.TEXT_NODE ? n.data : '').join('')].join() } catch { return '' } }
  const first = all.map(look)
  const moving = new Set()
  for (let i = 0; i < 6; i++) {
    await new Promise((done) => setTimeout(done, 250 + Math.random() * 250))
    all.forEach((el, j) => look(el) !== first[j] && moving.add(el))
  }
  for (const a of running) a.play()
  for (const el of moving) el.setAttribute('data-design-qa-moving', '')
  return { marked: moving.size }
})
```

If the marks hide what the diff changed, clear them and keep the last take.
Reload and shoot again before noting a difference timing could explain.

Mention the capture in your review only for a visual problem or design system violation, saying Design QA has the details.
