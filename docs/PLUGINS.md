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

The web UI hands your document one end of a `MessageChannel`. Every request goes over that port. The web UI checks it, and the backend checks it again against the permissions the installer approved, the repositories the person shared, and the person's own GitHub access. The SDK handles all of this.

## Repository layout

```text
my-plugin/
├── .openswe-plugin/
│   └── plugin.json     # the manifest
├── bundle/
│   └── index.html      # the built plugin, committed
├── src/
└── package.json
```

Installing reads the manifest and the bundle from GitHub at one commit; nothing is built on the server, so **commit the built bundle**.

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
| `entry` | The bundle, a path relative to the plugin directory. |
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

`host.on(listener)` receives events: `theme`, `path`, `storage.changed` and the room events.

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
- `host.nav.openExternal(url)` opens a web link in a new tab. github.com links open directly; anything else opens only after the person confirms in a dialog the web UI draws.
- `host.ui.toast({ title, description, intent })` shows a web UI toast (`intent` is `success`, `error` or `info`).

### People

```ts
const [ada] = await host.people.get(["ada"]) // { login, name, avatar: Blob | null }
const src = ada.avatar ? URL.createObjectURL(ada.avatar) : undefined
```

The frame can't load remote images, so the web UI fetches each picture and hands it over as a Blob. Results come back in the order you asked, logins with no profile are left out, and the client caches profiles for the frame's lifetime.

### Storage

Documents are JSON objects in named collections. Each document lives in one of two scopes:

- **Private to the person**, when no `repository` is given.
- **Shared per repository**: everyone using the plugin who has shared that repository and can read it.

```ts
await host.storage.put("comments", "c1", { body: "Looks good" }, { repository: "acme/app" })
const one = await host.storage.get("comments", "c1", { repository: "acme/app" })
const open = await host.storage.query("comments", { repository: "acme/app", where: { status: "open" }, limit: 50 })
await host.storage.delete("comments", "c1", { repository: "acme/app" })

const stop = host.storage.watch({ repository: "acme/app" }, () => refetch()) // anyone's write in this scope
```

- **Collection names:** a lowercase letter, then up to 63 more lowercase letters, digits, `_` or `-`.
- **Keys:** up to 200 of `A–Z a–z 0–9 . _ : @ / -`, with no empty, `.` or `..` path segment.
- **Size:** a document is at most 256 KB as JSON.
- **Queries:** `where` matches fields exactly, and `limit` is 1–500 (200 by default).

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

## Building the bundle

The entry must be one self-contained HTML file: every script, stylesheet and font inline. It must be at most 8 MB and contain no NUL characters (escape them as `\u0000`). Any build that emits a single file works. Docs builds a TanStack Start SPA with Vite, then [`scripts/inline-bundle.mjs`](../plugins/docs/scripts/inline-bundle.mjs) folds the output into `bundle/index.html`, keeping only the Latin font subsets:

```bash
pnpm --filter openswe-plugin-docs run build
```

## Trying it

Your plugin only runs inside the web UI: outside the frame, `connect()` waits forever. To try a change:

1. Build, then commit and push the bundle to a branch.
2. In **Settings → Plugins**, enter your repository, its plugin directory if it has one, and the branch, then press **Find plugins** and **Install**.
3. Open the plugin from the sidebar.
4. After the next push, press **Update** to move the install to the new commit.

A local web UI works the same way; [local development](DEVELOPMENT.md) covers running one. Installing reads from GitHub, so the branch has to be pushed either way.

## Shipping it

People install from the repository's default branch, or from a branch or tag they name. An install stays on its commit until someone presses **Update**. Anyone can install a plugin for themselves; admins can also install one for everyone. Bump `version` with each release so people can tell what they run.
