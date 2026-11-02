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

## Start from the starter

`plugins/starter` in `langchain-ai/open-swe` is an app ready to change: React,
the Macaw design system (Open SWE's own components, which follow the web UI's
light and dark theme), Tailwind, the plugin SDK, and a Vite build that writes
the one self-contained file with `manifest.json` inside it. Neither Macaw 2 nor
the SDK is on npm, so take them from the repository:

```bash
APPS=/workspace/.openswe-apps/open-swe
git clone --depth 1 --filter=blob:none --sparse https://github.com/langchain-ai/open-swe "$APPS"
git -C "$APPS" sparse-checkout set plugins/sdk plugins/starter ui/vendor/macaw
cp -r "$APPS/plugins/starter" "$APPS/plugins/<name>"
cd "$APPS/plugins/<name>" && npm install && npm run build
```

Clone it there, never over a checkout of the thread's own repository, and keep
the app beside `plugins/sdk` and `ui/vendor/macaw`, where its `package.json`
finds them. Edit `manifest.json` and `src/App.tsx`; `npm run
build` writes `bundle/index.html`, the file to preview. Build the UI from
Macaw's components (`@langchain/macaw-components/<Component>`: Button, Card,
Input, Select, Tabs, Dialog, Text and the rest) and its tokens (`p-space-4`,
`text-secondary`, `bg-surface-level-2`) rather than your own styles, so the app
looks like the web UI around it. The SDK's `README.md` and `src/client.ts`
document the host API.

The frame's policy blocks every request, so a CDN script, a `fetch` or a font
URL fails; the starter's build inlines everything.

## Iterating

1. Build, then call `preview_app` with `bundle/index.html`. The person sees it
   beside the thread and can chat with you from it.
2. Change it, rebuild, call `preview_app` again. Each call replaces the build
   they see.
3. When the person wants to keep it, call `publish_app` with the same file.
