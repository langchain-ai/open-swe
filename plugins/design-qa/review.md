Design QA saves matched before/after screenshots for visual review.

Capture only visual frontend changes, once a successful deployment or deployment-bot comment links a preview serving the reviewed head commit.
A building or deploying preview may serve an earlier commit and does not count.
If the diff has no visual frontend changes, record `skipped` with that reason.
If a current preview cannot be verified, record `blocked` with the missing prerequisite.
Always record an outcome with `record_app_review_outcome`; never silently skip capture work.

1. Find the latest successful preview deployment for the reviewed head commit, using current deployment records or the latest updated deployment-bot comment, not an older comment or a guessed URL.
   Open its link and verify it loads before capturing.
   Find its base: the site built from the default branch that shares the preview's data.
   The workspace's review guidelines name the base when there is one; without a base, record `blocked` and explain that the workspace needs a base URL.
1. If the pages need a sign-in, sign the browser in to the base's host and to the preview's host.
1. Pick the pages and interaction states the diff changes, at most ten.
   For each, open the same route on the base and on the preview at one viewport size, such as 1440×900, reach the same state, and take a viewport screenshot of each with `shoot`, below, one side per run: `return await shoot('settings-before')` saves `/workspace/design-qa/settings-before.png`.
   Don't take full-page screenshots: pages of different heights can't be compared.
1. Look at each pair with `read_file` for what changed and anything that looks wrong; Design QA measures how many pixels differ itself.
   Never note a difference that comes from when a screenshot was taken rather than from the diff.
1. Save the capture once, with `save_app_document`: collection `captures`, key `pulls/<number>/`, the document below, and `files` naming every screenshot you refer to.

```json
{
  "status": "complete",
  "auth_mode": "dummy",
  "base_url": "https://base.example.com",
  "head_url": "https://pr-123.preview.example.com",
  "base_sha": "<the base commit>",
  "head_sha": "<the head commit you reviewed>",
  "report": {
    "summary": "What changed visually, and anything that looks wrong, in a few sentences.",
    "scenarios": [
      {
        "id": "settings",
        "title": "Settings page",
        "path": "/settings",
        "before_url": "settings-before.png",
        "after_url": "settings-after.png",
        "width": 1440,
        "height": 900,
        "actions": ["Open the account menu"],
        "annotations": [
          { "x": 120, "y": 64, "width": 300, "height": 40, "title": "Label clipped", "body": "The new label is cut off at this width." }
        ]
      }
    ]
  }
}
```

Use `files` such as `{"settings-before.png": "/workspace/design-qa/settings-before.png", "settings-after.png": "/workspace/design-qa/settings-after.png"}`.

Record `complete` only after saving at least one matched pair and its files.
Record `blocked` for missing prerequisites or `failed` for a capture/save error, with a concrete reason.

- Save `base_url` and `head_url` as the verified site origins.
  Save each scenario's `path` as the actual browser pathname, query and fragment, including any organization prefix, not a route inferred from source code.
  For modal-only states, save the working parent route and put the interaction in `actions`.
- `auth_mode` is `"dummy"` when you signed in, `"none"` otherwise.
- Each `*_url` is a name in `files`, of letters, digits, `.`, `_` and `-`.
  Give each scenario a distinct `id` of the same characters.
- `width` and `height` are the viewport size you took both screenshots at.
- `actions` are the steps that reached an interaction state, such as opening a menu; leave them out for a page as it loads.
- `annotations` are at most three areas of the after screenshot a reviewer should look at, in its pixels, each with a short `title` and a `body`; leave them out when nothing stands out.
- When the two sides of a pair can't reach the same state or size, or something on them keeps moving, add `"layout_warnings": ["<why>"]`, saying what moves and if you hid it.
- When the preview or the base won't load, or wants a sign-in you can't do, record `blocked` with the specific reason; never report an empty capture as complete.

Loading images or fonts, skeletons, spinners, animations and live data can create differences unrelated to the diff.
`shoot` keeps most of that out, so put it in each run that shoots: it waits for fonts, images in view and loading indicators, pauses videos, freezes CSS and web animations, and shoots again until two takes in a row match.

```js
async function shoot(name) {
  await page.evaluate(async () => {
    const busy = '[aria-busy="true"], [role="progressbar"]:not([aria-valuenow]), [class*="skeleton" i], [class*="spinner" i], .animate-pulse, .animate-spin'
    const seen = (el) => { const box = el.getBoundingClientRect(); return box.width > 0 && box.bottom > 0 && box.top < innerHeight }
    for (const video of document.querySelectorAll('video')) { video.pause(); video.currentTime = 0 }
    for (let wait = 0; wait < 40; wait++) {
      await document.fonts.ready
      if (![...document.querySelectorAll(busy), ...[...document.images].filter((img) => !img.complete)].some(seen)) break
      await new Promise((done) => setTimeout(done, 250))
    }
  })
  const options = { path: `design-qa/${name}.png`, animations: 'disabled', style: '[data-design-qa-moving] { opacity: 0 !important }' }
  let last = await page.screenshot(options)
  for (let take = 0; take < 8; take++) {
    await page.evaluate(() => new Promise((done) => setTimeout(done, 500 + Math.random() * 500)))
    const next = await page.screenshot(options)
    if (next.length === last.length && next.every((byte, i) => byte === last[i])) return { name, steady: true }
    last = next
  }
  return { name, steady: false }
}
```

`steady: false` means something kept moving that freezing can't stop, such as a carousel, canvas or ticking counter.
Mark what moves by running this on both sides, then shoot both again; `shoot` hides marked elements:

```js
return await page.evaluate(async () => {
  const running = document.getAnimations().filter((animation) => animation.playState === 'running')
  for (const animation of running) animation.pause()
  const all = [...document.body.querySelectorAll('*')]
  const look = (el) => { try { const box = el.getBoundingClientRect(); return [box.x, box.y, box.width, box.height, getComputedStyle(el).transform, el.toDataURL?.(), [...el.childNodes].map((node) => node.nodeType === Node.TEXT_NODE ? node.data : '').join('')].join() } catch { return '' } }
  const first = all.map(look)
  const moving = new Set()
  for (let sample = 0; sample < 6; sample++) {
    await new Promise((done) => setTimeout(done, 250 + Math.random() * 250))
    all.forEach((el, i) => look(el) !== first[i] && moving.add(el))
  }
  for (const animation of running) animation.play()
  for (const el of moving) el.setAttribute('data-design-qa-moving', '')
  return { marked: moving.size, examples: [...moving].slice(0, 5).map((el) => el.outerHTML.slice(0, 80)) }
})
```

If that hides much of the page, such as a live feed, or what the diff changed, clear the marks and shoot again; a page that still isn't steady keeps its last take.
Before you note a difference timing could explain, such as something on one side only, reload both sides, shoot again and look again: note it only if it's still there, and never one inside something that moves.

Say something about the capture in your review only when it shows a visual problem, and then say that Design QA has the screenshots.
