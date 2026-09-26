# mcp-tool-usage

Top MCP tools in Open SWE with a representative thread per tool

A LangSmith custom app, scaffolded by `langsmith apps init`. See `context.md`
for how it works and `AGENTS.md` for the LangSmith API surface it can call.

Run it against prod traces with `langsmith --profile prod apps dev`.

## What this is

A custom app is a small React/TS UI rendered inside LangSmith in a
locked-down sandbox (`sandbox="allow-scripts"`, no `allow-same-origin`). It
never gets direct network access or a real credential — everything goes
through a `postMessage` bridge to the host page (`window.langsmith.call`),
which proxies to the real LangSmith API under the viewer's own session (or
your local `LANGSMITH_API_KEY` when running `langsmith apps dev`).

Since the sandbox has no bundler or npm access at runtime, this app is built
with Vite in **library mode**: `npm run build` bundles everything (React
included) into a single dependency-free file (`dist/bundle.js`) before it's
pushed. Dependencies are inlined at build time; use Macaw for all UI.

## Design system

Macaw is installed and configured by default. Use `@langchain/macaw-components`
for UI and `@langchain/macaw-tokens` for semantic styles. The stylesheet includes
fonts and tokens and is bundled inline for the sandbox. `HostTheme` follows
LangSmith's light/dark mode without resetting app state.

Read `AGENTS.md` before editing. Find the installed components with
`npm run macaw -- search "<capability>"` or
`npm run macaw -- inspect Button --json`. Every UI edit should use Macaw.

## Develop

Use Node.js 22.12 or newer. Run `npm run typecheck` and `npm run build`
before handing off changes.

Install dependencies, then start the dev server (it builds on the first run):

```bash
npm install
langsmith apps dev
```

`apps dev` runs the app inside a real sandboxed iframe, identical
restrictions to production, and automatically starts `npm run watch` for
you (it detects the script in `package.json`) — edit `src/App.tsx`, save,
and the preview reloads on its own.

## The bridge contract

`src/entry.tsx` exports a `render(data, root, metadata)` function that the
host calls once on load and again whenever `data` or `metadata` changes —
`data` is always `{}` (apps fetch whatever they need themselves).
`metadata.mode` is `"dark"` or
`"light"`; the sandbox sets `html.dark` from it, so this token-based UI themes
automatically through `HostTheme` and Macaw semantic tokens.
`src/App.tsx` is the actual UI; edit it freely, it's just a React component.

`window.langsmith`, injected by the host page, gives you:

- `window.langsmith.call(operation, args)` — `operation` is a
  `"<METHOD> <path>"` string (e.g. `"GET /api/v1/sessions"`), forwarded
  as-is to the real LangSmith API. Not a curated allowlist — see
  `AGENTS.md` for the available surface. Returns a Promise.
- `window.langsmith.setData(patch)` — push a data mutation out for the host
  to persist.
- `window.langsmith.feedback.create(args)` — sugar over
  `call('POST /api/v1/feedback', {body: args})`.

## Deploy

```bash
npm run build
langsmith apps push
```

The first `push` creates the app and writes `.langsmith/app.json` (the
app's ID) into this directory — commit it so teammates' pushes update the
same app instead of creating a new one. Every push after that updates it in
place.
