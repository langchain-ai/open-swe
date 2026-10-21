import { useEffect, useMemo, useState } from "react"

import { useHost, useHostContext } from "./host"
import { isMarkdown, pageTitle } from "./model"
import { PageView } from "./PageView"
import { SpaceProvider, useSpace } from "./space"

/** `/<owner>/<repo>/<page path>`: the space, then the page in it. */
function decode(segment: string): string {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}

function parsePath(path: string): {
  repository: string | null
  page: string | null
} {
  const [owner, repo, ...rest] = path.split("/").filter(Boolean).map(decode)
  return {
    repository: owner && repo ? `${owner}/${repo}`.toLowerCase() : null,
    page: rest.length ? rest.join("/") : null,
  }
}

export function App() {
  const host = useHost()
  const { path, plugin } = useHostContext()
  const route = parsePath(path)
  const repositories = plugin.repositories
  const repository =
    route.repository && repositories.includes(route.repository)
      ? route.repository
      : (repositories[0] ?? null)

  useEffect(() => {
    if (repository && route.repository !== repository)
      void host.nav.setPath(`/${repository}`, { replace: true })
  }, [host, repository, route.repository])

  if (!repository)
    return (
      <div className="center">
        <h2>Give Spaces a repository</h2>
        <p className="muted">
          Spaces keeps its pages in a GitHub repository. Add one to the plugin
          under Settings → Plugins, then come back.
        </p>
      </div>
    )

  return (
    <SpaceProvider
      key={repository}
      repository={repository}
      fallback={({ error }) => (
        <div className="center">
          {error ? (
            <p className="error">{error}</p>
          ) : (
            <p className="muted">Opening {repository}…</p>
          )}
        </div>
      )}
    >
      <div className="app">
        <Sidebar repositories={repositories} page={route.page} />
        <main className="main">
          {route.page ? (
            <PageView key={`${repository}:${route.page}`} path={route.page} />
          ) : (
            <SpaceHome />
          )}
        </main>
      </div>
    </SpaceProvider>
  )
}

interface Folder {
  name: string
  path: string
  folders: Folder[]
  pages: string[]
}

function buildTree(pages: string[]): Folder {
  const root: Folder = { name: "", path: "", folders: [], pages: [] }
  for (const page of pages) {
    const parts = page.split("/")
    let folder = root
    for (const part of parts.slice(0, -1)) {
      const at = folder.path ? `${folder.path}/${part}` : part
      let next = folder.folders.find((child) => child.name === part)
      if (!next) {
        next = { name: part, path: at, folders: [], pages: [] }
        folder.folders.push(next)
      }
      folder = next
    }
    folder.pages.push(page)
  }
  return root
}

function Sidebar({
  repositories,
  page,
}: {
  repositories: string[]
  page: string | null
}) {
  const host = useHost()
  const space = useSpace()
  const [filter, setFilter] = useState("")
  const [creating, setCreating] = useState(false)
  const pages = useMemo(() => {
    const extra = [...space.drafts.keys()].filter(
      (path) => !space.pages.includes(path)
    )
    const all = [...space.pages, ...extra].sort((a, b) => a.localeCompare(b))
    const q = filter.trim().toLowerCase()
    return q ? all.filter((path) => path.toLowerCase().includes(q)) : all
  }, [filter, space.drafts, space.pages])
  const tree = useMemo(() => buildTree(pages), [pages])
  const open = (path: string) =>
    void host.nav.setPath(`/${space.repository}/${path}`)

  return (
    <nav className="sidebar">
      <select
        className="space-picker"
        value={space.repository}
        onChange={(event) => void host.nav.setPath(`/${event.target.value}`)}
      >
        {repositories.map((repository) => (
          <option key={repository} value={repository}>
            {repository}
          </option>
        ))}
      </select>
      <input
        className="filter"
        placeholder="Find a page"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      <button
        type="button"
        className="new-page"
        onClick={() => setCreating(true)}
      >
        + New page
      </button>
      {creating && (
        <NewPage
          onCancel={() => setCreating(false)}
          onCreate={(path) => {
            setCreating(false)
            open(path)
          }}
        />
      )}
      <div className="tree">
        <FolderView
          folder={tree}
          depth={0}
          current={page}
          drafts={space.drafts}
          forceOpen={Boolean(filter.trim())}
          onOpen={open}
        />
      </div>
      {space.truncated && (
        <p className="muted small">
          This repository is too large to list every page.
        </p>
      )}
    </nav>
  )
}

function FolderView({
  folder,
  depth,
  current,
  drafts,
  forceOpen,
  onOpen,
}: {
  folder: Folder
  depth: number
  current: string | null
  drafts: Map<string, unknown>
  forceOpen: boolean
  onOpen: (path: string) => void
}) {
  const containsCurrent = Boolean(current?.startsWith(`${folder.path}/`))
  const [expanded, setExpanded] = useState(depth === 0 || containsCurrent)
  const shown = forceOpen || expanded || depth === 0
  return (
    <div>
      {depth > 0 && (
        <button
          type="button"
          className="tree-row folder"
          style={{ paddingLeft: 8 + (depth - 1) * 12 }}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="caret">{shown ? "▾" : "▸"}</span>
          {folder.name}
        </button>
      )}
      {shown && (
        <>
          {folder.folders.map((child) => (
            <FolderView
              key={child.path}
              folder={child}
              depth={depth + 1}
              current={current}
              drafts={drafts}
              forceOpen={forceOpen}
              onOpen={onOpen}
            />
          ))}
          {folder.pages.map((path) => (
            <button
              key={path}
              type="button"
              className={`tree-row${path === current ? " current" : ""}`}
              style={{ paddingLeft: 8 + depth * 12 }}
              onClick={() => onOpen(path)}
            >
              {pageTitle(path)}
              {drafts.has(path) && (
                <span className="dot" title="Draft in progress" />
              )}
            </button>
          ))}
        </>
      )}
    </div>
  )
}

function NewPage({
  onCancel,
  onCreate,
}: {
  onCancel: () => void
  onCreate: (path: string) => void
}) {
  const [path, setPath] = useState("docs/")
  const normalized = path.trim().replace(/^\/+/, "")
  const withExtension = isMarkdown(normalized) ? normalized : `${normalized}.md`
  return (
    <form
      className="card new-page-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (normalized && !normalized.endsWith("/")) onCreate(withExtension)
      }}
    >
      <input
        autoFocus
        value={path}
        onChange={(event) => setPath(event.target.value)}
      />
      <span className="muted small">
        Creates {withExtension} when you save a version.
      </span>
      <div className="row end">
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary"
          disabled={!normalized || normalized.endsWith("/")}
        >
          Create
        </button>
      </div>
    </form>
  )
}

function SpaceHome() {
  const host = useHost()
  const space = useSpace()
  const drafts = [...space.drafts.entries()]
  return (
    <div className="home">
      <h1>{space.repository}</h1>
      <p className="muted">
        {space.pages.length} pages on {space.defaultBranch}. Pages are the
        repository&apos;s Markdown files; edits happen in drafts and are
        published as pull requests.
      </p>
      <h2>Drafts in progress</h2>
      {drafts.length === 0 ? (
        <p className="muted">
          No drafts. Open a page and choose Edit to start one.
        </p>
      ) : (
        <ul className="drafts">
          {drafts.map(([path, draft]) => (
            <li key={path}>
              <button
                type="button"
                className="link"
                onClick={() =>
                  void host.nav.setPath(`/${space.repository}/${path}`)
                }
              >
                {pageTitle(path)}
              </button>
              <span className="muted">
                {path} · {draft.startedBy}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
