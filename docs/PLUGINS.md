# Building Web Apps

An app (a plugin) is one self-contained HTML file published to Open SWE.
People add it to their own web UI, where it gets an entry in the sidebar and opens full screen.
It runs in a sandboxed frame with no network access and reaches Open SWE only through the web UI's plugin API, acting as the person using it and never beyond what they allowed.
An app ships no backend code.

Most apps are built by an agent, for one person and one purpose, in a thread's sandbox: the agent previews each build beside the thread, the person uses it and asks for changes there, and either of them keeps it once it's right.
Nothing has to be committed anywhere.

[`plugins/starter`](../plugins/starter) is a small working app to start from, and [`plugins/docs`](../plugins/docs) a complete one: Markdown pages in a repository, edited together live and published as pull requests.
Read them alongside this guide.

## How a plugin runs

The web UI loads your plugin's single HTML file into a sandboxed `srcdoc` frame, inside a small shell frame the web UI writes itself.
Your document gets:

- **An opaque origin.**
  There are no cookies, and `localStorage`, `sessionStorage` and IndexedDB throw.
  Keep state in [storage](#storage).
- **No network.**
  The page's Content-Security-Policy is `default-src 'none'`, plus inline scripts and styles, `data:` fonts, and `data:`/`blob:` images and media.
  Even a remote image is a request others can observe, such as a download counter, so images in a repository the person shared come through [`github(repo).image(path, ref)`](#github) as Blobs.
  `fetch`, XHR, WebSocket, EventSource, beacons, workers, remote scripts, styles, fonts and images, and loading any URL into a nested frame are all refused.
  WebRTC is beyond what the policy covers, so before any of your code runs the web UI removes `RTCPeerConnection` and every other `RTC…` interface from your window, where they can't be put back.
  That keeps your code and its libraries off WebRTC; it isn't a wall against code written to get around it, since a frame you create gets its own copies.
  Anything from the outside world comes through the host API.
- **No navigation.**
  Your frame sits in a shell whose policy has `frame-src 'none'`, which browsers apply to every navigation of your frame, so navigating it anywhere is refused before a request leaves.
  The sandbox keeps it from opening popups or navigating the shell or the top window.
  If its document is ever replaced, the web UI tears the plugin down.
  Open links with [`nav.openExternal`](#navigation-and-links).
- **Forms that never submit.**
  `submit` events fire for your own handlers (call `preventDefault()`), but the browser never sends a form anywhere.
- **Clipboard writes**, through `navigator.clipboard.writeText`.

The web UI hands your document one end of a `MessageChannel` and talks to it over [Cap'n Web](https://github.com/cloudflare/capnweb).
It runs its own copy of Cap'n Web in your document before any of your code, so both ends always speak the same version: don't bundle `capnweb` yourself (the SDK uses it for types only).
The web UI checks every request, and the backend checks it again against the permissions the installer approved, the repositories the person shared, and the person's own GitHub access.
The SDK handles all of this.

Each scoped thing you open, such as a collection, a repository or a live room, is its own capability on the web UI's side.
The SDK reuses one per collection or repository, and leaving a room releases it.
Everything is released when the frame goes away.
Each frame has an allowance: 16 requests running at once with up to 256 waiting, 3,000 requests a minute, 256 capabilities held at once, and 512 undelivered events per listener (a listener that far behind is dropped).
Past it, requests fail with status 429.

## Apps, versions and installs

- **An app** belongs to whoever published it, under its manifest's `name`.
  Publishing a bundle with the same name makes the app's next version.
  It is the owner's alone until they list it, in **Settings → Apps**, for anyone in this Open SWE to add.
- **A version** is one immutable build: the bundle and the manifest inside it.
  Where it was built, such as a repository and commit or a thread, is kept only to show.
  Open SWE keeps every version someone runs, the newest five releases besides, and each thread's newest preview.
- **An install** is one person adding an app to their sidebar, pinned to a version.
  It moves only when they press **Update**, and a version asking for more permissions waits for them to approve them.
  Nobody installs for anyone else.
- **A preview** is the build an agent runs beside its thread.
  Each preview replaces the one before; it never reaches the sidebar until someone keeps it.

## Publishing

An agent in a thread builds the bundle in its sandbox and calls:

- `preview_app(bundle_path)` to run the build beside the thread, in the thread's **App** panel.
  The first build, and any build asking for new permissions, waits for the person to approve them there.
- `publish_app(bundle_path)` to release it as the app's next version.
  It lands in the person's sidebar when it asks for nothing they haven't approved for this app.

From the **App** panel the person chats with the agent in the same thread.
Their messages carry the app's tools and the context it shares, as in the [agent dock](#agent), and **Add to sidebar** releases the running build.

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
| `collections` | Optional rules for storage collections, by name: `{ "captures": { "write": "app" } }`; see [Who writes a collection](#who-writes-a-collection). |
| `review` | Optional work pull request reviews do for the app: `{ "instructions": "…", "writes": ["captures"], "browser": true }`; see [Review steps](#review-steps). |

### Permissions

| Permission | Lets the plugin |
| --- | --- |
| *(none needed)* | navigate, show toasts, open links, look up people, and list or pick repositories |
| `storage` | keep JSON documents and files attached to them, private to the person or shared per repository |
| `realtime` | join live rooms: an ordered update log plus presence, for editing together |
| `github:read` | read repository info, refs, trees, files, history and pull requests, and search pull requests |
| `github:write` | create branches, commit files and open pull requests (also allows reads) |
| `agent` | an agent the person chats with in a dock beside the plugin, with tools the plugin provides |

The person approves these permissions when they add the app.
If a newer version asks for more, the install stays where it is until they approve them.
A call outside the approved set fails with status 403.

### Repositories

A plugin reaches no repository until the person shares one with it, and each install has its own grants.

Ask for one with the picker the web UI draws:

```ts
const [repository] = await host.repositories.pick()           // "acme/app", or undefined if cancelled
const chosen = await host.repositories.pick({ multiple: true }) // several
const shared = await host.repositories.list()                   // everything shared so far
```

`suggested` preselects repositories, such as one a shared link names.
When every suggested repository is already shared, `pick` returns at once without asking:

```ts
await host.repositories.pick({ suggested: ["acme/app"] })
```

A call naming a repository the person hasn't shared, or can no longer read, fails with status 403.
People can stop sharing a repository in **Settings → Apps** at any time, so treat a 403 as a cue to ask again rather than as a crash.

## The SDK

`@openswe/plugin-sdk` lives in [`plugins/sdk`](../plugins/sdk) and is not published to npm yet.
Depend on it by path, as the starter does with `"@openswe/plugin-sdk": "file:../sdk"`; outside this repository, check out `plugins/sdk` and point the path at it.

### Connecting

Call `connect()` once, such as while your module loads, and share the `PluginHost` it resolves to.
Calling it late is fine: the web UI waits for the frame's hello.
It answers only the first hello, though, so a second `connect()` never resolves; that includes one in a React effect, which StrictMode runs twice.

```ts
import { connect } from "@openswe/plugin-sdk"

export const hostReady = connect() // resolves once the port and context arrive
```

`host.context` holds:

- the plugin (`id`, `name`, `displayName`, `version`, `permissions`);
- the person using it (`user`: `login`, `name`, and `avatar` as a Blob);
- the web UI's `theme` (`"light"` or `"dark"`);
- the plugin's own `path` after its web UI URL.

`host.on(listener)` receives `theme` and `path` events.
Storage changes come through each collection's [`watch`](#changes), and room events through the room's handlers.

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

`useDashboardTheme` toggles `html.dark` and sets `color-scheme` and `data-theme`, which is what the web UI's Macaw design system reads.
To match the web UI, use Macaw, as the starter does (the published `@langchain/macaw-components` and `@langchain/macaw-tokens` packages).

### Navigation and links

The plugin's path is part of the web UI URL (`/agents/plugins/<name>/<path>`), so links to a page inside your plugin can be shared and reloaded.
Start your router at `host.context.path`, follow `path` events (back and forward in the web UI), and report your own moves:

```ts
await host.nav.setPath("/acme/app/docs/intro.md") // { replace: true } to replace the entry
host.on((event) => event.type === "path" && router.navigate(event.path))
```

A memory-history router fits well; see `PathSync` in [`plugins/docs/src/routes/__root.tsx`](../plugins/docs/src/routes/__root.tsx).
Other navigation:

- `host.nav.openThread(threadId)` opens an agent thread in the web UI.
- `host.nav.openExternal(url)` opens a web link in a new tab. github.com links open directly; anything else opens only after the person confirms it.
- `host.ui.toast({ title, description, intent })` shows a web UI toast (`intent` is `success`, `error` or `info`), its title led by your plugin's name.
- `host.ui.setTitle(title)` shows `title` beside the plugin's name in the bar above it, and names the browser tab "title · Open SWE".
  Up to 120 characters; `null` clears it, and the tab goes back to the plugin's name.

The web UI asks the person in dialogs it draws over the plugin: the repository picker and link confirmations.
The plugin can neither cover nor imitate them, and their approve buttons work only after a moment on screen.

A plugin behaves like a tab.
Once opened, it stays mounted while the person visits threads, pull requests or other pages in the same part of the web UI, and clicking its sidebar entry brings it back exactly as they left it.
While hidden it still runs and syncs, but it can't move the web UI or show toasts, and requests that would ask the person something or open something (a link, even to GitHub; picking repositories; opening the agent) are declined.

### People

```ts
const [ada] = await host.people.get(["ada"]) // { login, name, avatar: Blob | null }
const src = ada.avatar ? URL.createObjectURL(ada.avatar) : undefined
```

The frame can't load remote images, so the web UI fetches each picture and hands it over as a Blob.
Results come back in the order you asked, logins with no profile are left out, and the client caches profiles for the frame's lifetime.

### Storage

Storage works like Firebase: named collections of JSON documents, ordered by key, that you can read in order and in ranges, and watch for changes.
Open a collection in one of two scopes:

- **Private to the person**, when no `repository` is given.
- **Shared per repository**: everyone using the plugin who has shared that repository and can read it.

Type the collection with its key (`string`, or any subtype such as a template literal) and its documents.
Plain interfaces work; they need no index signature, and anything that isn't JSON, such as a function or a `Date`, fails to compile:

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

`put` overwrites.
`create` stores a document only when its key is free and returns whatever is stored, so when several people create the same key at once, every one of them gets back the single document that won.

The SDK trusts these types; nothing checks documents at runtime.
Validate what you read if older versions of your plugin may have written it.

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

- **Key order:** keys compare character by character, so `"B"` comes before `"a"`.
  Keys that sort by time, such as ISO timestamps or zero-padded counters, keep a collection in insertion order.
- **`orderBy`:** a dotted path to a field holding a string, number, boolean or `null` (`"createdAt"`, `"meta.rank"`).
  The types offer only those paths.
  Documents come by that field, then by key.
  Values order like Firebase: missing or `null`, `false`, `true`, numbers, then strings character by character.
- **Bounds:** `startAt` or `startAfter`, and `endAt` or `endBefore`, take a key, or with `orderBy` a value of that field, or a document you read, which stands for its value and then its key.
  They follow `direction`, so in descending order `startAt` is the highest.
- **`where`:** a document matches when it contains every field of `where`, nested objects included.
- **`prefix`:** only documents whose key starts with it.
- **`limit`:** 1 to 500, 200 by default.
  With `direction: "desc"`, it takes the last ones.

#### Changes

`watch` calls you whenever anyone writes or deletes a document in the collection, in this scope.
It says only that something changed, so read again what you show:

```ts
const stop = comments.watch(() => queryClient.invalidateQueries({ queryKey: ["comments"] }))
stop() // when you no longer show them
```

#### Attachments

A document can carry files, such as screenshots, that are too big or too binary for JSON.
They are read and written by whoever may read and write the document, and deleted with it; rewriting the document keeps them.

```ts
await captures.put(key, capture)                       // the document must exist first
await captures.attach(key, "home-after.png", blob)     // replaces any file of that name
const shot = await captures.attachment(key, "home-after.png") // Blob for URL.createObjectURL, or null
await captures.detach(key, "home-after.png")
```

Attaching or detaching a file counts as a change for `watch`.
Name the files your documents need in the documents themselves.

#### Limits

- **Collection names:** a lowercase letter, then up to 63 more lowercase letters, digits, `_` or `-`.
- **Keys:** up to 200 of `A–Z a–z 0–9 . _ : @ / -`, with no empty, `.` or `..` path segment.
- **Size:** a document is at most 256 KB as JSON.
- **Attachments:** a PNG, JPEG, WebP or GIF image, or a WebM or MP4 video, of at most 8 MiB, named with up to 120 of `A–Z a–z 0–9 . _ -` and starting with a letter or digit.

Data belongs to the app, across its versions and everyone's installs of it.
Another owner's app of the same name never sees it.

#### Who writes a collection

Anyone using the app who may read a scope may change it: write, create and delete its documents, and attach and detach their files.
A collection the manifest marks `"write": "app"` is written only by the app's own server-side work; people read it, and every change they make, from the frame or straight over HTTP, fails with status 403.
Today that work is the app's [review step](#review-steps): reviews write the collections it names.

A rule binds the app's data as soon as the version that declares it runs: an install still pinned to an older version, or a preview, can't change a collection the newest release marks `app`.

#### Review steps

An app can have pull request reviews do work for it, such as screenshotting a frontend change, by declaring a review step:

```json
"review": {
  "instructions": "Screenshot the routes the diff changes on the preview and on the base…",
  "writes": ["captures"],
  "browser": true
}
```

- `instructions`, at most 8,000 characters, are what the review does for the app, after reviewing the code.
  Reviews read them inside the app's own block; they can't change the review's rules, add tools, or widen where it writes.
- `writes` names collections the manifest marks `"write": "app"`, so what a review saves can't be forged or deleted by people.
  The review saves with `save_app_document`: a document and files from its sandbox as attachments, in the reviewed repository's scope, only under keys starting `pulls/<number>/` of that pull request.
  A key ending in `/` gets a time-ordered id, so the newest of a prefix reads first.
  A save keeps its document only once every file is attached.
  A PNG is kept as a lossless WebP, under the name the review gave it, whenever that is smaller and changes no pixel, so take a file's type from its Blob, not its name.
  When the pull request closes, merged or not, Robin deletes what reviews saved under its `pulls/<number>/` in these collections, with the files; a document a person created or changed stays, as does anything in the app's other collections.
- `browser` gives the review the thread's LangSmith browser and `compare_images`, which diffs two screenshots pixel by pixel.
  The browser never reaches the review's sandbox, and signs in only to the domains an instance admin lists under **Defaults → Review browser**, with the one MCP tool named there; sessions install only on LangSmith sites.

A workspace admin adds apps to reviews in **Settings → Review → Apps in reviews**, or with `manage_review_apps`.
Reviews follow the release that was newest when the admin added the app, so a later release changes no review until an admin updates it, and only on private repositories, whose previews run only code the organization's members pushed.

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

Read a pull request by its number, as GitHub has it now, or `null` when it has none by that number:

```ts
const pr = await repo.pull(12)
// { number, title, url, state, merged, draft, head, base, headSha, baseSha, author,
//   createdAt, updatedAt, requestedReviewers, requestedTeams }
```

`requestedTeams` are `org/slug`; GitHub drops a request once it is answered with a review.

Search the open pull requests of every repository the person shared and can still read, by their part in them:

```ts
const { pulls, incomplete } = await host.pulls.search({ role: "review-requested" }) // or "author"
// pulls: [{ repository, number, title, url, draft, author, createdAt, updatedAt }]
```

`review-requested` includes requests to the person's teams.
`incomplete` says GitHub left some out: past its first page of results, or because its search timed out.

A commit carries at most 100 files and 100 deletions.
`openPull` also takes `body` and `draft`.
File paths and branch names have no empty, `.` or `..` segment and no backslash, or the call fails with status 400.

### Live rooms

A room is an ordered log of opaque updates, plus presence that is broadcast and never stored.
Rooms are scoped like documents, and their names follow the key rules.
Open SWE keeps the log in PostgreSQL and fans each update out to everyone in the room.

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

The provider batches local edits into one update every 50 ms, retries failed sends, and replaces the log with a snapshot every 200 updates.
For other data, `host.joinRoom(scope, { onUpdate, onSynced, onPresence, onError })` gives you the raw room: `publish(update)`, `compact(snapshot, through)`, `presence(client, bytes)` and `leave()`.
Limits per message: an update is at most 1 MB, a snapshot 8 MB, and presence 2,900 bytes.

A frame is in at most 20 rooms at once; joining a 21st fails with status 429, so leave rooms you no longer show.
Joining needs the `realtime` permission and, for a repository's room, a repository the person shared, or it fails with status 403.
Should the backend later refuse a room, such as when the person stops sharing its repository, the room gets `onError` with the status and receives nothing more; the provider takes `onError` in its options and rejects `whenSynced()`.
Your other rooms keep syncing.

### Agent

With the `agent` permission, the bar above your plugin gets an **Agent** button that docks the normal agent thread beside it.
The person chats with the agent there; it works as them, with their repository and tools, plus the tools your plugin provides.
The person writes every message, and the thread is private to them.

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

// Images go in the composer beside the draft, for the person to check before sending.
await host.agent.open({ draft: "What changed here?", images: [{ name: "after.png", blob }] })
```

- **Chats** are yours to scope.
  `use` picks the chat without opening the dock; each name, with or without a repository the person shared, is one thread per person that picks up where it left off.
- **Context** is what you `share`: JSON of at most 8,000 characters, such as the open page and the selection.
  It goes to the agent just before each message the person sends from the dock, so the agent knows where it is without calling a tool, and the message shows your `label` in small type instead of the context itself.
  Share again whenever it changes.
  Send a selection with the text around it, marked as only placing it, so the agent can tell which occurrence the person means.
- **Tools** reach the agent as `<plugin>__<name>`, so they never stand in for its own.
  Names are a lowercase letter, then letters, digits or `_`; at most 32 tools, with descriptions up to 2,000 characters and instructions up to 4,000.
  `parameters` is a JSON Schema of type `object`.
  Your instructions go to the agent with your context, just before each message the person sends from the dock, never in its system prompt.
- **Calls** run while your plugin is open.
  The run waits at each call, the dock runs it in your frame, and the thread carries on with what `run` returned, as JSON, or a string as is (up to 200,000 characters).
  What `run` throws, the agent reads as an error.
  Open in two tabs, your plugin runs each call once.
  Only messages sent from the dock, and the runs your results resume, get your tools, so the same chat opened as a full thread works without them.
  Each dock offers the chat its frame's tools again just before it sends a message, so a dock closing in another tab leaves them be.
  After 20 runs your results started since the person last wrote, the agent waits for them, and their next message brings it what you returned.
- **Results are data.**
  The agent treats them, and your instructions and context, as your plugin's, not as instructions from the person, so a document can't steer it.
- **Images** in a draft are PNG, JPEG, WebP or GIF Blobs: at most 5, of 10 MB each, as the composer takes them itself.
  Nothing is sent until the person presses Send, and they can remove any image first.

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
| 429 | The plugin is past its allowance: too many requests at once or in a minute, too many open capabilities, or more than 20 rooms. |

## Building the bundle

Start from [`plugins/starter`](../plugins/starter): React, the Macaw design system, Tailwind and the SDK, with a Vite build that writes `bundle/index.html` with `manifest.json` embedded.
Macaw comes from npm.
The SDK is local to this repository (`plugins/sdk`); keep an app beside it, or copy that directory too.

The bundle is one self-contained HTML file: every script, stylesheet and font inline, with the manifest script inside.
It must be at most 64 MB and contain no NUL characters (escape them as `\u0000`); the manifest script is at most 64,000 characters.
Any build that emits a single file works.
The starter uses Vite with `vite-plugin-singlefile` and adds [`manifest.json`](../plugins/starter/manifest.json) as the first script in the page's head:

```bash
pnpm --filter openswe-app-starter run build
```

Docs builds a TanStack Start SPA with Vite instead, then [`scripts/inline-bundle.mjs`](../plugins/docs/scripts/inline-bundle.mjs) folds the output into `bundle/index.html`, keeping only the Latin font subsets, and embeds [`manifest.json`](../plugins/docs/manifest.json):

```bash
pnpm --filter openswe-plugin-docs run build
```

## Trying it

Your app only runs inside the web UI: outside the frame, `connect()` waits forever.
In a thread, have the agent call `preview_app` after each build.
Elsewhere, upload each build in **Settings → Apps** and press **Update** on your install.
A local web UI works the same way; [local development](DEVELOPMENT.md) covers running one.

Bump `version` with each release so people can tell what they run.
