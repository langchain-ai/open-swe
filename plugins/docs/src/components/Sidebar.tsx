import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"
import { useNavigate, useParams } from "@tanstack/react-router"
import { useMemo, useState } from "react"

import { useOpenPickedRepository } from "@/components/ShareRepository"
import {
  isHidden,
  isMarkdown,
  pageTitle,
  repositoryLabel,
  type Draft,
} from "@/lib/model"
import { useRepositories } from "@/lib/queries"

interface Folder {
  name: string
  path: string
  folders: Folder[]
  pages: string[]
}

function buildTree(pages: string[]): Folder {
  const root: Folder = { name: "", path: "", folders: [], pages: [] }
  for (const page of pages) {
    let folder = root
    for (const part of page.split("/").slice(0, -1)) {
      let next = folder.folders.find((child) => child.name === part)
      if (!next) {
        next = {
          name: part,
          path: folder.path ? `${folder.path}/${part}` : part,
          folders: [],
          pages: [],
        }
        folder.folders.push(next)
      }
      folder = next
    }
    folder.pages.push(page)
  }
  return root
}

export function Sidebar({
  repository,
  pages,
  drafts,
  truncated,
}: {
  repository: string
  pages: string[]
  drafts: Map<string, Draft>
  truncated: boolean
}) {
  const repositories = useRepositories().data ?? [
    { fullName: repository, isInBaseOrg: false },
  ]
  const share = useOpenPickedRepository()
  const navigate = useNavigate()
  const params = useParams({ strict: false }) as { _splat?: string }
  const current = params._splat ?? null
  const [owner = "", repo = ""] = repository.split("/")
  const [filter, setFilter] = useState("")
  const [showHidden, setShowHidden] = useState(false)
  const [creating, setCreating] = useState(false)

  const visible = useMemo(() => {
    const all = [...new Set([...pages, ...drafts.keys()])].sort((a, b) =>
      a.localeCompare(b)
    )
    const q = filter.trim().toLowerCase()
    return all.filter(
      (path) =>
        (showHidden || !isHidden(path)) &&
        (!q || path.toLowerCase().includes(q))
    )
  }, [drafts, filter, pages, showHidden])
  const tree = useMemo(() => buildTree(visible), [visible])
  const hiddenCount = pages.filter(isHidden).length
  const open = (path: string) =>
    void navigate({
      to: "/$owner/$repo/$",
      params: { owner, repo, _splat: path },
    })

  return (
    <nav className="flex w-64 shrink-0 flex-col gap-space-2 border-r border-default bg-surface-level-1 p-space-2 max-sm:hidden">
      <div className="flex items-center gap-space-1">
        <select
          aria-label="Repository"
          className="h-8 min-w-0 flex-1 truncate rounded-md border border-default bg-transparent py-0 pr-space-6 pl-space-2 text-sm leading-8 text-primary"
          value={repository}
          onChange={(event) => {
            const [o = "", r = ""] = event.target.value.split("/")
            void navigate({
              to: "/$owner/$repo",
              params: { owner: o, repo: r },
            })
          }}
        >
          {repositories.map((shared) => (
            <option key={shared.fullName} value={shared.fullName}>
              {repositoryLabel(shared)}
            </option>
          ))}
        </select>
        <IconButton
          icon={PlusIcon}
          label="Open another repository"
          size="sm"
          variant="outlined"
          loading={share.pending}
          onClick={() => share.open()}
        />
      </div>
      <label className="flex h-8 items-center gap-space-2 rounded-md border border-default px-space-2">
        <MagnifyingGlassIcon size={14} className="text-icon-secondary" />
        <input
          aria-label="Find a page"
          placeholder="Find a page"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-sm text-primary outline-none placeholder:text-placeholder"
        />
      </label>
      <button
        type="button"
        onClick={() => setCreating(true)}
        className="flex h-7 items-center gap-space-2 rounded-md px-space-2 text-sm text-secondary hover:bg-surface-level-2-hover hover:text-primary"
      >
        <PlusIcon size={14} /> New page
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
      <div className="-mx-space-1 min-h-0 flex-1 overflow-y-auto">
        <FolderView
          folder={tree}
          depth={0}
          current={current}
          drafts={drafts}
          forceOpen={Boolean(filter.trim())}
          onOpen={open}
        />
      </div>
      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setShowHidden((value) => !value)}
          className="self-start px-space-2 text-xs text-tertiary hover:text-secondary"
        >
          {showHidden ? "Hide" : "Show"} {hiddenCount} hidden{" "}
          {hiddenCount === 1 ? "page" : "pages"}
        </button>
      )}
      {truncated && (
        <p className="px-space-2 text-xs text-tertiary">
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
  drafts: Map<string, Draft>
  forceOpen: boolean
  onOpen: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(
    depth === 0 || Boolean(current?.startsWith(`${folder.path}/`))
  )
  const shown = forceOpen || expanded || depth === 0
  const indent = { paddingLeft: 8 + Math.max(0, depth - 1) * 12 }
  return (
    <div>
      {depth > 0 && (
        <button
          type="button"
          style={indent}
          onClick={() => setExpanded((value) => !value)}
          className="flex h-7 w-full items-center gap-space-1 rounded-md pr-space-2 text-left text-sm text-secondary hover:bg-surface-level-2-hover"
        >
          {shown ? <CaretDownIcon size={12} /> : <CaretRightIcon size={12} />}
          <span className="truncate">{folder.name}</span>
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
              style={{ paddingLeft: 8 + depth * 12 }}
              onClick={() => onOpen(path)}
              className={`flex h-7 w-full items-center gap-space-2 rounded-md pr-space-2 text-left text-sm hover:bg-surface-level-2-hover ${
                path === current
                  ? "bg-selected font-medium text-primary"
                  : "text-primary"
              }`}
            >
              <FileTextIcon
                size={14}
                className="shrink-0 text-icon-secondary"
              />
              <span className="truncate capitalize">{pageTitle(path)}</span>
              {drafts.has(path) && (
                <span
                  title="Draft in progress"
                  className="ml-auto size-1.5 shrink-0 rounded-full bg-warning"
                />
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
  const target = isMarkdown(normalized) ? normalized : `${normalized}.md`
  const valid = Boolean(normalized) && !normalized.endsWith("/")
  return (
    <form
      className="flex flex-col gap-space-2 rounded-md border border-default p-space-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (valid) onCreate(target)
      }}
    >
      <input
        aria-label="New page path"
        autoFocus
        value={path}
        onChange={(event) => setPath(event.target.value)}
        className="h-8 rounded-md border border-default bg-transparent px-space-2 text-sm text-primary outline-none focus:border-brand"
      />
      <span className="text-xs text-tertiary">
        Creates {target} when you save a version.
      </span>
      <div className="flex justify-end gap-space-1">
        <Button type="button" size="xs" variant="plain" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="xs" disabled={!valid}>
          Create
        </Button>
      </div>
    </form>
  )
}
