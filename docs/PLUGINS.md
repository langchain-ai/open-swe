# Building Web Apps

An app (a plugin) is one self-contained HTML file published to Open SWE. People add it to their own web UI, where it gets an entry in the sidebar and opens full screen. It runs in a sandboxed frame with no network access and reaches Open SWE only through the web UI's plugin API, acting as the person using it and never beyond what they allowed. An app ships no backend code.

Most apps are built by an agent, for one person and one purpose, in a thread's sandbox: the agent previews each build beside the thread, the person uses it and asks for changes there, and either of them keeps it once it's right. Nothing has to be committed anywhere.

[`plugins/docs`](../plugins/docs) is a complete plugin: Markdown pages in a repository, edited together live and published as pull requests. Read it alongside this guide.

## How a plugin runs

The web UI loads your plugin's single HTML file into a sandboxed `srcdoc` frame, inside a small shell frame the web UI writes itself. Your document gets:

- **An opaque origin.** There are no cookies, and `localStorage`, `sessionStorage` and IndexedDB throw. Keep state in [storage](#storage).
- **No network.** The page's Content-Security-Policy is `default-src 'none'`, plus inline scripts and styles, `data:` fonts, and `data:`/`blob:` images and media. Even a remote image is a request others can observe, such as a download counter, so images in a repository the person shared come through [`github(repo).image(path, ref)`](#github) as Blobs. `fetch`, XHR, WebSocket, EventSource, workers, nested frames and remote scripts, styles, fonts and images are all refused. Anything from the outside world comes through the host API.
- **No navigation.** The frame cannot navigate, open popups or reach the top window. If its document is ever replaced, the web UI tears the plugin down. Open links with [`nav.openExternal`](#navigation-and-links).
- **Forms that never submit.** `submit` events fire for your own handlers (call `preventDefault()`), but the browser never sends a form anywhere.
- **Clipboard writes**, through `navigator.clipboard.writeText`.

The web UI hands your document one end of a `MessageChannel` and talks to it over [Cap'n Web](https://github.com/cloudflare/capnweb). It runs its own copy of Cap'n Web in your document before any of your code, so both ends always speak the same version: don't bundle `capnweb` yourself (the SDK uses it for types only). The web UI checks every request, and the backend checks it again against the permissions the installer approved, the repositories the person shared, and the person's own GitHub access. The SDK handles all of this.

Each scoped thing you open, such as a collection, a repository or a live room, is its own capability on the web UI's side. The SDK reuses one per collection or repository, and leaving a room releases it. Everything is released when the frame goes away. Each frame has an allowance: 16 requests running at once with up to 256 waiting, 3,000 requests a minute, 256 capabilities held at once, and 512 undelivered events per listener (a listener that far behind is dropped). Past it, requests fail with status 429.

## Apps, versions and installs

- **An app** belongs to whoever published it, under its manifest's `name`. Publishing a bundle with the same name makes the app's next version. It is the owner's alone until they list it, in **Settings → Apps**, for anyone in this Open SWE to add.
- **A version** is one immutable build: the bundle and the manifest inside it. Where it was built, such as a repository and commit or a thread, is kept only to show. Open SWE keeps every version someone runs, the newest five releases besides, and each thread's newest preview.
- **An install** is one person adding an app to their sidebar, pinned to a version. It moves only when they press **Update**, and a version asking for more permissions waits for them to approve them. Nobody installs for anyone else.
- **A preview** is the build an agent runs beside its thread. Each preview replaces the one before; it never reaches the sidebar until someone keeps it.

## Publishing

An agent in a thread builds the bundle in its sandbox and calls:

- `preview_app(bundle_path)` to run the build beside the thread, in the thread's **App** panel. The first build, and any build asking for new permissions, waits for the person to approve them there.
- `publish_app(bundle_path)` to release it as the app's next version. It lands in the person's sidebar when it asks for nothing they haven't approved for this app.

From the **App** panel the person chats with the agent in the same thread. Their messages carry the app's tools and the context it shares, as in the [agent dock](#agent), and **Add to sidebar** releases the running build.

Outside a thread, upload the built `.html` file in **Settings → Apps**, or post it as the body of `POST /dashboard/api/plugins/apps` with a session, optionally with `repository`, `ref` and `commit` query parameters to show where it came from.

## The manifest

The manifest is a JSON script inside the bundle, read when a version is published:

```html
<script type="application/openswe-plugin+json">
{
  "name": "docs",
  "displayName": "Docs",
  "version": "0.3.0",
  "description": "Pages over the Markdown in your repositories.",
  "sidebar": { "label": "Docs", "icon": "file-text" },
  "permissions": ["storage", "realtime", "github:read", "github:write", "agent"]
}
</script>
```

| Field | Rules |
| --- | --- |
| `name` | Lowercase letters, digits and `-`, at most 48 characters. It names the app among its owner's, sets its URL, `/agents/plugins/<name>`, and must be unique in each person's sidebar. |
| `displayName` | At most 48 characters. |
| `version` | Free text, at most 32 characters. It is shown to people, not compared. |
| `description` | Optional, at most 280 characters. |
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
| `agent` | an agent the person chats with in a dock beside the plugin, with tools the plugin provides |

The person approves these permissions when they add the app. If a newer version asks for more, the install stays where it is until they approve them. A call outside the approved set fails with status 403.

### Repositories

A plugin reaches no repository until the person shares one with it, and each install has its own grants.

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

A call naming a repository the person hasn't shared, or can no longer read, fails with status 403. People can stop sharing a repository in **Settings → Apps** at any time, so treat a 403 as a cue to ask again rather than as a crash.

## The SDK

`@openswe/plugin-sdk` lives in [`plugins/sdk`](../plugins/sdk) and is not published to npm yet. Inside this repository, depend on it as `"@openswe/plugin-sdk": "workspace:*"`; elsewhere, check out `plugins/sdk` and depend on it by path.

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

The web UI asks the person in dialogs it draws over the plugin: the repository picker and link confirmations. The plugin can neither cover nor imitate them, and their approve buttons work only after a moment on screen.

A plugin behaves like a tab. Once opened, it stays mounted while the person visits threads, pull requests or other pages in the same part of the web UI, and clicking its sidebar entry brings it back exactly as they left it. While hidden it still runs and syncs, but it can't move the web UI or show toasts, and requests that would ask the person something (opening a link, picking repositories, opening the agent) are declined.

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

Data belongs to the app, across its versions and everyone's installs of it. Another owner's app of the same name never sees it.

### GitHub

Every call acts as the person, with their GitHub permissions, in a repository they shared:

```ts
const repo = host.github("acme/app")
const info = await repo.info()                     // { fullName, defaultBranch, private }
const head = await repo.ref(info.defaultBranch)    // commit SHA, or null
const tree = await repo.tree(head!)                // { sha, truncated, entries: [{ path, type, size }] }
const text = await repo.file("README.md", head!)   // string, or null when missing
const logo = await repo.image("docs/logo.png", head!) // Blob for URL.createObjectURL, or null
const log = await repo.history("README.md", info.defaultBranch, 20)

await repo.createBranch("docs/intro", head!)
await repo.deleteBranch("docs/old")               // never the default branch; closes its open pull request
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

### Agent

With the `agent` permission, the bar above your plugin gets an **Agent** button that docks the normal agent thread beside it. The person chats with the agent there; it works as them, with their repository and tools, plus the tools your plugin provides. The person writes every message, and the thread is private to them.

```ts
// Which chat the dock shows: one per person under each name you choose.
await host.agent.use({ name: "repository", repository: "acme/app" }) // or name: path, for one per page

// Tools the agent can call, run here in your frame. Calling provide again replaces them.
const withdraw = host.agent.provide(
  {
    read_page: {
      description: "The open page's Markdown and the person's selection.",
      parameters: { type: "object", properties: {} },
      run: () => ({ path, markdown: editor.getMarkdown() }),
    },
    replace_text: {
      description: "Replaces a passage on the open page.",
      parameters: {
        type: "object",
        properties: { find: { type: "string" }, replace: { type: "string" } },
        required: ["find", "replace"],
      },
      run: ({ find, replace }) => replaceInEditor(find, replace),
    },
  },
  "Use read_page before changing anything." // instructions for the agent, optional
)

// What the person has open; each message they send from the dock carries the latest,
// and the thread shows the label under the message.
await host.agent.share(
  {
    page: "docs/intro.md",
    selection: {
      text: "the selected passage",
      before: "…the text just before it, ",
      after: ", and just after it…",
      note: "before and after only place the selection; they are not selected",
    },
  },
  { label: "intro · 3 words selected" }
)

await host.agent.open({ draft: "> the passage the person selected\n\n" }) // the person presses Send
```

- **Chats** are yours to scope. `use` picks the chat without opening the dock; each name, with or without a repository the person shared, is one thread per person that picks up where it left off.
- **Context** is what you `share`: JSON of at most 8,000 characters, such as the open page and the selection. It goes to the agent just before each message the person sends from the dock, so the agent knows where it is without calling a tool, and the message shows your `label` in small type instead of the context itself. Share again whenever it changes. Send a selection with the text around it, marked as only placing it, so the agent can tell which occurrence the person means.
- **Tools** reach the agent as `<plugin>__<name>`, so they never stand in for its own. Names are a lowercase letter, then letters, digits or `_`; at most 32 tools, with descriptions up to 2,000 characters and instructions up to 4,000. `parameters` is a JSON Schema of type `object`.
- **Calls** run while your plugin is open. The run waits at each call, the dock runs it in your frame, and the thread carries on with what `run` returned, as JSON, or a string as is (up to 200,000 characters). What `run` throws, the agent reads as an error. Open in two tabs, your plugin runs each call once. Only messages sent from the dock get your tools, so the same chat opened as a full thread works without them; closing the dock withdraws them. After 20 runs your results started since the person last wrote, the agent waits for them, and their next message brings it what you returned.
- **Results are data.** The agent treats them as your plugin's, not as instructions from the person, so a document can't steer it.

### Errors
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

Start from [`plugins/starter`](../plugins/starter): React, the Macaw design system, Tailwind and the SDK, with a Vite build that writes `bundle/index.html` with `manifest.json` embedded. Macaw 2 and the SDK aren't on npm yet, so the starter takes them by path from this repository (`ui/vendor/macaw`, `plugins/sdk`); keep an app beside them, or copy those directories too.

The bundle is one self-contained HTML file: every script, stylesheet and font inline, with the manifest script inside. It must be at most 64 MB and contain no NUL characters (escape them as `\u0000`). Any build that emits a single file works, such as Vite with `vite-plugin-singlefile`. Docs builds a TanStack Start SPA with Vite, then [`scripts/inline-bundle.mjs`](../plugins/docs/scripts/inline-bundle.mjs) folds the output into `bundle/index.html`, keeping only the Latin font subsets, and embeds [`manifest.json`](../plugins/docs/manifest.json):

```bash
pnpm --filter openswe-plugin-docs run build
```

## Trying it

Your app only runs inside the web UI: outside the frame, `connect()` waits forever. In a thread, have the agent call `preview_app` after each build. Elsewhere, upload each build in **Settings → Apps** and press **Update** on your install. A local web UI works the same way; [local development](DEVELOPMENT.md) covers running one.

Bump `version` with each release so people can tell what they run.
