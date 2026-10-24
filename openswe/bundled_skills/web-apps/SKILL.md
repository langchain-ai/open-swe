---
name: web-apps
description: Build an app that runs in the Open SWE web UI, preview it beside the thread with preview_app, and release it with publish_app. Read this before building an Open SWE app or plugin.
---

# Open SWE apps

An app is one self-contained HTML file. The web UI runs it full screen from the
person's sidebar, in a sandboxed frame with no network: it reaches storage,
GitHub, live rooms and an agent only through the host API, as the person using
it, within the permissions in its manifest.

## The manifest

Embed it in the HTML as a JSON script:

```html
<script type="application/openswe-plugin+json">
{
  "name": "standup-notes",
  "displayName": "Standup notes",
  "version": "0.1.0",
  "description": "Notes for the daily standup",
  "sidebar": { "label": "Standup", "icon": "notebook" },
  "permissions": ["storage"]
}
</script>
```

- `name`: lowercase letters, digits and dashes. Publishing under the same name
  makes a new version of the same app.
- `sidebar.icon`: one of `puzzle-piece`, `notebook`, `file-text`, `book-open`,
  `kanban`, `chart-bar`, `chat-circle`, `calendar`, `lightbulb`.
- `permissions`: ask for the fewest. `storage` (JSON documents, private or per
  repository), `realtime` (live rooms with presence), `github:read`,
  `github:write` (branches, commits, pull requests), `agent` (a chat dock with
  tools the app provides).

## The SDK

The SDK is `plugins/sdk` in `langchain-ai/open-swe`; it is not on npm. Copy it in:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/langchain-ai/open-swe /tmp/open-swe
git -C /tmp/open-swe sparse-checkout set plugins/sdk
```

Then depend on it by path (`"@openswe/plugin-sdk": "file:/tmp/open-swe/plugins/sdk"`)
and read its `README.md` and `src/client.ts`. Call `connect()` while the module
loads. Never bundle `capnweb`: the frame provides it.

## Building

Bundle everything into the one file: scripts, styles, fonts and images inlined.
With Vite, `vite-plugin-singlefile` does it; add the manifest script to
`index.html`. The frame's policy blocks every request, so a CDN script, a
`fetch` or a font URL fails. Follow the person's light or dark theme with
`useDashboardTheme` or `prefers-color-scheme`.

## Iterating

1. Build, then call `preview_app` with the built file. The person sees it
   beside the thread and can chat with you from it.
2. Change it, rebuild, call `preview_app` again. Each call replaces the build
   they see.
3. When the person wants to keep it, call `publish_app` with the same file.
