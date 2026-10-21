# @openswe/plugin-sdk

Build plugins for the [Open SWE](https://github.com/langchain-ai/open-swe) dashboard.

A plugin is a web app in a GitHub repository. Open SWE runs it full screen from
its sidebar, in a sandboxed frame with **no network access**: everything it does
goes through the dashboard, as the person using it, within the permissions they
approved when they installed it.

## Layout

```
my-plugin/
  .openswe-plugin/plugin.json   # the manifest
  bundle/index.html             # one self-contained HTML file: scripts, styles, fonts inlined
```

A repository can list several plugins in `.openswe-plugin/marketplace.json`, in
the same shape as a Claude Code marketplace. Commit the built bundle: Open SWE
never runs a build.

```json
{
  "name": "docs",
  "displayName": "Docs",
  "version": "0.1.0",
  "entry": "bundle/index.html",
  "sidebar": { "label": "Docs", "icon": "file-text" },
  "permissions": ["storage", "realtime", "github:read", "github:write", "threads"]
}
```

| Permission | Lets the plugin |
| --- | --- |
| `storage` | keep JSON documents, private to the person or shared per repository |
| `realtime` | join live rooms: an ordered update log plus presence, for co-editing |
| `github:read` | read files, trees, history and pull requests in its repositories |
| `github:write` | create branches, commit files and open pull requests there |
| `threads` | ask to start agent threads; the person approves each request |

## Client

```ts
import { connect } from "@openswe/plugin-sdk"

const host = await connect() // call while the module loads
host.context.user // { login, name, avatar: Blob | null }
await host.storage.put("notes", "n1", { text: "hi" }, { repository: "acme/app" })
await host.github("acme/app").file("README.md", "main")
const [person] = await host.people.get(["octocat"])
```

React bindings live in `@openswe/plugin-sdk/react` (`PluginHostProvider`,
`useHostContext`, `useDashboardTheme`), and a Yjs provider for live rooms in
`@openswe/plugin-sdk/yjs` (`RoomProvider`).

See `plugins/docs` in the Open SWE repository for a complete plugin.
