Design QA keeps before and after screenshots of a pull request's frontend, which reviewers compare side by side.

Capture only when the diff changes what people see in a web frontend and a preview of this pull request serves its head commit: a deployment for that commit, or a bot's comment on the pull request saying the preview is deployed, links it.
A link while the preview is still building or deploying doesn't count: it can still serve an earlier commit, or nothing.
Otherwise do nothing for Design QA, and don't mention it.

1. Find the preview, and its base: the site built from the default branch that shares the preview's data.
   The workspace's review guidelines name the base when there is one; without a base, don't capture.
1. If the pages need a sign-in, sign the browser in to the base's host and to the preview's host.
1. Pick the pages and interaction states the diff changes, at most ten.
   For each, open the same route on the base and on the preview at one viewport size, such as 1440×900, reach the same state, let the page settle, and save a viewport screenshot of each with run code, such as `await page.screenshot({ path: 'design-qa/settings-before.png' })`, which saves `/workspace/design-qa/settings-before.png`.
   Don't take full-page screenshots: pages of different heights can't be compared.
1. Look at each pair with `read_file` for what changed and anything that looks wrong; Design QA measures how many pixels differ itself.
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

with `files` such as `{"settings-before.png": "/workspace/design-qa/settings-before.png", "settings-after.png": "/workspace/design-qa/settings-after.png"}`.

- `auth_mode` is `"dummy"` when you signed in, `"none"` otherwise.
- Each `*_url` is a name in `files`, of letters, digits, `.`, `_` and `-`.
  Give each scenario a distinct `id` of the same characters.
- `width` and `height` are the viewport size you took both screenshots at.
- `actions` are the steps that reached an interaction state, such as opening a menu; leave them out for a page as it loads.
- `annotations` are at most three areas of the after screenshot a reviewer should look at, in its pixels, each with a short `title` and a `body`; leave them out when nothing stands out.
- When the two sides of a pair can't reach the same state or size, add `"layout_warnings": ["<why>"]`.
- When the preview or the base won't load, or wants a sign-in you can't do, save a capture with `"status": "blocked"`, an `"error"` saying why, and no scenarios.

Say something about the capture in your review only when it shows a visual problem, and then say that Design QA has the screenshots.
