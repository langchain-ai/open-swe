# Building Web Plugins

A plugin is a web app that people install from a GitHub repository. It gets an entry in the web UI's sidebar and opens full screen there. It runs in a sandboxed frame with no network access and reaches Open SWE only through the web UI's plugin API, acting as the person using it and never beyond what they allowed. A plugin ships no backend code.

[`plugins/docs`](../plugins/docs) is a complete plugin: Markdown pages in a repository, edited together live and published as pull requests. Read it alongside this guide.

## How a plugin runs

The web UI loads your plugin's single HTML file into a sandboxed `srcdoc` frame, inside a small shell frame the web UI writes itself. Your document gets:

- **An opaque origin.** There are no cookies, and `localStorage`, `sessionStorage` and IndexedDB throw. Keep state in [storage](#storage).
- **No network.** The page's Content-Security-Policy is `default-src 'none'`, plus inline scripts and styles, `data:` fonts, and `data:`/`blob:` images and media. `fetch`, XHR, WebSocket, EventSource, workers, nested frames and remote scripts, styles, fonts and images are all refused. Anything from the outside world comes through the host API.
- **No navigation.** The frame cannot navigate, open popups or reach the top window. If its document is ever replaced, the web UI tears the plugin down. Open links with [`nav.openExternal`](#navigation-and-links).
- **Forms that never submit.** `submit` events fire for your own handlers (call `preventDefault()`), but the browser never sends a form anywhere.
- **Clipboard writes**, through `navigator.clipboard.writeText`.

The web UI hands your document one end of a `MessageChannel` and talks to it over [Cap'n Web](https://github.com/cloudflare/capnweb). It runs its own copy of Cap'n Web in your document before any of your code, so both ends always speak the same version: don't bundle `capnweb` yourself (the SDK uses it for types only). The web UI checks every request, and the backend checks it again against the permissions the installer approved, the repositories the person shared, and the person's own GitHub access. The SDK handles all of this.

Each scoped thing you open, such as a collection, a repository or a live room, is its own capability on the web UI's side. The SDK reuses one per collection or repository, and leaving a room releases it. Everything is released when the frame goes away. Each frame has an allowance: 16 requests running at once with up to 256 waiting, 3,000 requests a minute, 256 capabilities held at once, and 512 undelivered events per listener (a listener that far behind is dropped). Past it, requests fail with status 429.

## Repository layout

```text
my-plugin/
├── .openswe-plugin/
│   └── plugin.json     # the manifest
├── .github/workflows/
│   └── plugin.yml      # builds and publishes the plugin
├── src/
└── package.json        # `build` writes the manifest's entry, e.g. bundle/index.html
```

The built UI is never committed, and nothing is built on Open SWE's servers. A GitHub Actions workflow builds the plugin on each push and publishes the build as a release asset; installs read it from there:

```yaml
# .github/workflows/plugin.yml
name: Plugin
on:
  push:
  workflow_dispatch:
  delete:
permissions:
  contents: write
jobs:
  plugin:
    uses: langchain-ai/open-swe/.github/workflows/openswe-plugin.yml@main
    with:
      install: npm ci          # once, at the repository root
      build: npm run build     # in each plugin's directory
```

The newest build of each branch or tag is the asset `<name>-<commit>.html` on a release tagged `openswe-plugin/<name>/<ref>`, a pre-release for branches. Each build replaces the last, and deleting a branch deletes its releases. An install takes the newest build of the ref it follows and pins the commit that build came from, reading the manifest at that commit too. Anyone who can push to the repository can publish a build, the same people who could change its code.

One repository can offer several plugins with `.openswe-plugin/marketplace.json` (the same layout as Claude Code plugin marketplaces):

```json
{
  "name": "acme-plugins",
  "owner": { "name": "Acme" },
  "plugins": [
    { "name": "docs", "source": "./plugins/docs" },
    { "name": "board", "source": { "source": "github", "repo": "acme/board", "ref": "v2" } },
    {
      "name": "notes",
      "source": { "source": "git-subdir", "url": "acme/tools", "path": "notes", "sha": "4f2c…" }
    }
  ]
}
```

A source is a directory in the same repository, another repository (`github`, with an optional `ref` or `sha`), or a directory of another repository (`git-subdir`).

## The manifest

`.openswe-plugin/plugin.json`:

```json
{
  "name": "docs",
  "displayName": "Docs",
  "version": "0.3.0",
  "description": "Pages over the Markdown in your repositories.",
  "entry": "bundle/index.html",
  "sidebar": { "label": "Docs", "icon": "file-text" },
  "permissions": ["storage", "realtime", "github:read", "github:write", "threads"]
}
```

| Field | Rules |
| --- | --- |
| `name` | Lowercase letters, digits and `-`, at most 48 characters. It sets the plugin's URL, `/agents/plugins/<name>`, and must be unique for each person. |
| `displayName` | At most 48 characters. |
| `version` | Free text, at most 32 characters. It is shown to people, not compared. |
| `description` | Optional, at most 280 characters. |
| `entry` | Where the build writes the UI, relative to the plugin directory. The plugin workflow publishes that file. |
| `sidebar.label` | At most 24 characters. |
| `sidebar.icon` | One of `puzzle-piece`, `notebook`, `file-text`, `book-open`, `kanban`, `chart-bar`, `chat-circle`, `calendar`, `lightbulb`. |
| `permissions` | What the plugin may ask for; see below. |

### Permissions

| Permission | Lets the plugin |
| --- | --- |
| *(none needed)* | navigate, show toasts, open links, look up people, and list or pick repositories |
| `storage` | keep JSON documents, private to the person or shared per repository |
| `realtime` | join live rooms: an ordered update log plus presence, for editing together |
| `github:read` | read repository info, refs, trees, files, history and pull requests |
| `github:write` | create branches, commit files and open pull requests (also allows reads) |
| `threads` | ask to start agent threads, which the person approves one by one, and list the ones it started |

The person approves these permissions when they install. **Update** re-reads the plugin's ref. If the new commit asks for more permissions, nothing changes until the person approves them. A call outside the approved set fails with status 403.

### Repositories

A plugin reaches no repository until the person shares one with it, and grants are per person. When an admin installs a plugin for everyone, each person still picks their own repositories.

Ask for one with the picker the web UI draws:

```ts
const [repository] = await host.repositories.pick()           // "acme/app", or undefined if cancelled
const chosen = await host.repositories.pick({ multiple: true }) // several
const shared = await host.repositories.list()                   // everything shared so far
```

`suggested` preselects repositories, such as one a shared link names. When every suggested repository is already shared, `pick` returns at once without asking:

```ts
await host.repositories.pick({ suggested: ["acme/app"] })
```

A call naming a repository the person hasn't shared, or can no longer read, fails with status 403. People can stop sharing a repository in **Settings → Plugins** at any time, so treat a 403 as a cue to ask again rather than as a crash.

## The SDK

`@openswe/plugin-sdk` lives in [`plugins/sdk`](../plugins/sdk) and is not published to npm yet. Inside this repository, depend on it as `"@openswe/plugin-sdk": "workspace:*"`.

### Connecting

Call `connect()` while your module loads, not inside a React effect. The web UI hands over the port as soon as the frame loads, and a late listener would miss it.

```ts
import { connect } from "@openswe/plugin-sdk"

export const hostReady = connect() // resolves once the port and context arrive
```

`host.context` holds:

- the plugin (`id`, `name`, `displayName`, `version`, `permissions`);
- the person using it (`user`: `login`, `name`, and `avatar` as a Blob);
- the web UI's `theme` (`"light"` or `"dark"`);
- the plugin's own `path` after its web UI URL.

`host.on(listener)` receives `theme` and `path` events. Storage changes come through each collection's [`watch`](#changes), and room events through the room's handlers.

### React

```tsx
import { PluginHostProvider, useDashboardTheme, useHostContext } from "@openswe/plugin-sdk/react"

function App({ host }: { host: PluginHost }) {
  return (
    <PluginHostProvider host={host}>
      <Themed />
    </PluginHostProvider>
  )
}

function Themed() {
  useDashboardTheme() // follows the web UI's light/dark mode
  const { user } = useHostContext()
  return <p>Hello, {user.name}</p>
}
```

`useDashboardTheme` toggles `html.dark` and sets `color-scheme` and `data-theme`, which is what the web UI's Macaw design system reads. To match the web UI, use Macaw as Docs does (`@langchain/macaw-components` and `@langchain/macaw-tokens` from `ui/vendor/macaw`).

### Navigation and links

The plugin's path is part of the web UI URL (`/agents/plugins/<name>/<path>`), so links to a page inside your plugin can be shared and reloaded. Start your router at `host.context.path`, follow `path` events (back and forward in the web UI), and report your own moves:

```ts
await host.nav.setPath("/acme/app/docs/intro.md") // { replace: true } to replace the entry
host.on((event) => event.type === "path" && router.navigate(event.path))
```

A memory-history router fits well; see `PathSync` in [`plugins/docs/src/routes/__root.tsx`](../plugins/docs/src/routes/__root.tsx). Other navigation:

- `host.nav.openThread(threadId)` opens an agent thread in the web UI.
- `host.nav.openExternal(url)` opens a web link in a new tab. github.com links open directly; anything else opens only after the person confirms it.
- `host.ui.toast({ title, description, intent })` shows a web UI toast (`intent` is `success`, `error` or `info`).
- `host.ui.setTitle(title)` shows `title` beside the plugin's name in the bar above it, and names the browser tab "title · Open SWE". Up to 120 characters; `null` clears it, and the tab goes back to the plugin's name.

The web UI asks the person in dialogs it draws over the plugin: the repository picker, link confirmations and thread approvals. The plugin can neither cover nor imitate them, and their approve buttons work only after a moment on screen.

A plugin behaves like a tab. Once opened, it stays mounted while the person visits threads, pull requests or other pages in the same part of the web UI, and clicking its sidebar entry brings it back exactly as they left it. While hidden it still runs and syncs, but it can't move the web UI or show toasts, and requests that would ask the person something (opening a link, starting a thread, picking repositories) are declined.

### People

```ts
const [ada] = await host.people.get(["ada"]) // { login, name, avatar: Blob | null }
const src = ada.avatar ? URL.createObjectURL(ada.avatar) : undefined
```

The frame can't load remote images, so the web UI fetches each picture and hands it over as a Blob. Results come back in the order you asked, logins with no profile are left out, and the client caches profiles for the frame's lifetime.

### Storage

Storage works like Firebase: named collections of JSON documents, ordered by key, that you can read in order and in ranges, and watch for changes. Open a collection in one of two scopes:

- **Private to the person**, when no `repository` is given.
- **Shared per repository**: everyone using the plugin who has shared that repository and can read it.

Type the collection with its key (`string`, or any subtype such as a template literal) and its documents. Plain interfaces work; they need no index signature, and anything that isn't JSON, such as a function or a `Date`, fails to compile:

```ts
interface Comment {
  body: string
  status: "open" | "resolved"
  parent: string | null
  createdAt: string
}

const comments = host.storage.collection<`c-${string}`, Comment>("comments", { repository: "acme/app" })

await comments.put("c-1", { body: "Looks good", status: "open", parent: null, createdAt: new Date().toISOString() })
const one = await comments.get("c-1") // { key, document, updatedAt } | null
await comments.delete("c-1")
```

The SDK trusts these types; nothing checks documents at runtime. Validate what you read if older versions of your plugin may have written it.

#### Queries

`query` returns documents in order, by key unless you pass `orderBy`:

```ts
await comments.query({ prefix: "c-", limit: 50 })                    // by key
await comments.query({ startAt: "c-100", endBefore: "c-200" })       // a key range
await comments.query({ where: { status: "open" }, orderBy: "createdAt", direction: "desc", limit: 20 })

// The next page starts after the last document of this one, ties included.
const page = await comments.query({ orderBy: "createdAt", limit: 20 })
const next = await comments.query({ orderBy: "createdAt", limit: 20, startAfter: page.at(-1) })
```

- **Key order:** keys compare character by character, so `"B"` comes before `"a"`. Keys that sort by time, such as ISO timestamps or zero-padded counters, keep a collection in insertion order.
- **`orderBy`:** a dotted path to a field holding a string, number, boolean or `null` (`"createdAt"`, `"meta.rank"`). The types offer only those paths. Documents come by that field, then by key. Values order like Firebase: missing or `null`, `false`, `true`, numbers, then strings character by character.
- **Bounds:** `startAt` or `startAfter`, and `endAt` or `endBefore`, take a key, or with `orderBy` a value of that field, or a document you read, which stands for its value and then its key. They follow `direction`, so in descending order `startAt` is the highest.
- **`where`:** a document matches when it contains every field of `where`, nested objects included.
- **`prefix`:** only documents whose key starts with it.
- **`limit`:** 1 to 500, 200 by default. With `direction: "desc"`, it takes the last ones.

#### Changes

`watch` calls you whenever anyone writes or deletes a document in the collection, in this scope. It says only that something changed, so read again what you show:

```ts
const stop = comments.watch(() => queryClient.invalidateQueries({ queryKey: ["comments"] }))
stop() // when you no longer show them
```

#### Limits

- **Collection names:** a lowercase letter, then up to 63 more lowercase letters, digits, `_` or `-`.
- **Keys:** up to 200 of `A–Z a–z 0–9 . _ : @ / -`, with no empty, `.` or `..` path segment.
- **Size:** a document is at most 256 KB as JSON.

Data belongs to the plugin's source, its repository and directory. Updating keeps it. A plugin with the same name installed from another source never sees it.

### GitHub

Every call acts as the person, with their GitHub permissions, in a repository they shared:

```ts
const repo = host.github("acme/app")
const info = await repo.info()                     // { fullName, defaultBranch, private }
const head = await repo.ref(info.defaultBranch)    // commit SHA, or null
const tree = await repo.tree(head!)                // { sha, truncated, entries: [{ path, type, size }] }
const text = await repo.file("README.md", head!)   // string, or null when missing
const log = await repo.history("README.md", info.defaultBranch, 20)

await repo.createBranch("docs/intro", head!)
const sha = await repo.commit({
  branch: "docs/intro",
  expectedHead: head!,                             // fails if the branch moved, so no push is lost
  message: "Update the intro",
  files: { "docs/intro.md": "# Intro\n" },
  deletions: ["docs/old.md"],
})
const pull = (await repo.findPull("docs/intro")) ?? (await repo.openPull({ head: "docs/intro", base: info.defaultBranch, title: "Update the intro" }))
```

A commit carries at most 100 files and 100 deletions. `openPull` also takes `body` and `draft`.

### Live rooms

A room is an ordered log of opaque updates, plus presence that is broadcast and never stored. Rooms are scoped like documents, and their names follow the key rules. Open SWE keeps the log in PostgreSQL and fans each update out to everyone in the room.

For collaborative editing, use the Yjs provider:

```ts
import * as Y from "yjs"
import { RoomProvider } from "@openswe/plugin-sdk/yjs"

const doc = new Y.Doc()
const live = new RoomProvider(host, { room: "draft/docs/intro.md", repository: "acme/app" }, doc)
await live.whenSynced()
live.awareness.setLocalStateField("user", { name: host.context.user.name, color: "#2563eb" })
// …bind doc and live.awareness to your editor (Docs uses Tiptap's Collaboration extensions)
live.destroy() // when leaving
```

The provider batches local edits into one update every 50 ms, retries failed sends, and replaces the log with a snapshot every 200 updates. For other data, `host.joinRoom(scope, { onUpdate, onSynced, onPresence })` gives you the raw room: `publish(update)`, `compact(snapshot, through)`, `presence(client, bytes)` and `leave()`. Limits per message: an update is at most 1 MB, a snapshot 8 MB, and presence 2,900 bytes.

### Agent threads

```ts
const started = await host.threads.start({
  repository: "acme/app",
  title: "Tighten the intro",
  request: "Rewrite docs/intro.md so it fits on one screen.",
})
if (started) host.nav.openThread(started.threadId) // null: the person declined
const mine = await host.threads.list()              // threads this install started
```

The web UI shows the person your request word for word before anything runs. The thread is theirs: private, working with their access, and its prompt says which plugin asked. The title is at most 120 characters and the request at most 40,000.

### Errors

A failed request rejects with `HostError`, which carries `message` and an HTTP-like `status`:

| Status | Meaning |
| --- | --- |
| 400 | The request's parameters are invalid; the backend was never called. |
| 401 | The person's GitHub sign-in expired. They need to sign in to the web UI again. |
| 403 | The permission wasn't granted, the repository isn't shared, or the person can't do this on GitHub. |
| 404 | No such document, file or repository. |
| 409 | Another approval is already open for this plugin. |
| 422 | GitHub refused the change, for example because the branch moved past `expectedHead`. |
| 429 | The plugin is past its allowance: too many requests at once or in a minute, or too many open capabilities. |

## Building the bundle

The build must write the manifest's `entry` as one self-contained HTML file: every script, stylesheet and font inline. It must be at most 8 MB and contain no NUL characters (escape them as `\u0000`). Any build that emits a single file works. Docs builds a TanStack Start SPA with Vite, then [`scripts/inline-bundle.mjs`](../plugins/docs/scripts/inline-bundle.mjs) folds the output into `bundle/index.html`, keeping only the Latin font subsets:

```bash
pnpm --filter openswe-plugin-docs run build
```

## Trying it

Your plugin only runs inside the web UI: outside the frame, `connect()` waits forever. To try a change:

1. Push your change to a branch and wait for the plugin workflow to publish its build, usually a minute or two. Run the workflow by hand for a branch it hasn't built yet.
2. In **Settings → Plugins**, enter your repository, its plugin directory if it has one, and the branch, then press **Find plugins** and **Install**.
3. Open the plugin from the sidebar.
4. After the next build, press **Update** to move the install to it.

A local web UI works the same way; [local development](DEVELOPMENT.md) covers running one. Installing reads the build from GitHub either way.

## Shipping it

People install from the repository's default branch, or from a branch or tag they name, so the workflow must have built that ref. A marketplace that points at another repository names a branch or tag there, not a commit. An install stays on its build until someone presses **Update**. Anyone can install a plugin for themselves; admins can also install one for everyone. Bump `version` with each release so people can tell what they run.
