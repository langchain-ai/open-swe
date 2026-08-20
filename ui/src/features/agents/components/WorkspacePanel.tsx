import { useCallback, useEffect, useMemo, useState } from "react"
import Editor from "@monaco-editor/react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChevronDown,
  ChevronRight,
  File,
  Folder,
  FolderOpen,
  RefreshCw,
  Save,
} from "lucide-react"

import type {
  WorkspaceFileContent,
  WorkspaceFileEntry,
  WorkspaceFileListing,
} from "@/features/agents/lib/api"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

export interface WorkspaceAdapter {
  key: string
  list: (path: string) => Promise<WorkspaceFileListing>
  read: (path: string) => Promise<WorkspaceFileContent>
  write: (path: string, content: string) => Promise<unknown>
}

function Directory({
  adapter,
  path,
  depth,
  onOpen,
}: {
  adapter: WorkspaceAdapter
  path: string
  depth: number
  onOpen: (path: string) => void
}) {
  const listing = useQuery({
    queryKey: ["workspace-files", adapter.key, path],
    queryFn: () => adapter.list(path),
  })
  if (listing.isPending)
    return (
      <div className="px-3 py-2 text-xs text-muted-foreground">Loading…</div>
    )
  if (listing.error)
    return (
      <div className="px-3 py-2 text-xs text-destructive">
        {listing.error instanceof Error
          ? listing.error.message
          : "Could not read folder."}
      </div>
    )
  return (
    <div role="tree">
      {listing.data.entries.map((entry) => (
        <WorkspaceEntry
          key={entry.path}
          adapter={adapter}
          entry={entry}
          depth={depth}
          onOpen={onOpen}
        />
      ))}
      {listing.data.truncated && (
        <div className="px-3 py-2 text-xs text-muted-foreground">
          More entries are hidden.
        </div>
      )}
    </div>
  )
}

function WorkspaceEntry({
  adapter,
  entry,
  depth,
  onOpen,
}: {
  adapter: WorkspaceAdapter
  entry: WorkspaceFileEntry
  depth: number
  onOpen: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <>
      <button
        type="button"
        role="treeitem"
        aria-expanded={entry.isDirectory ? expanded : undefined}
        onClick={() =>
          entry.isDirectory
            ? setExpanded((value) => !value)
            : onOpen(entry.path)
        }
        className="flex h-7 w-full items-center gap-1.5 truncate text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        {entry.isDirectory ? (
          expanded ? (
            <ChevronDown className="size-3 shrink-0" />
          ) : (
            <ChevronRight className="size-3 shrink-0" />
          )
        ) : (
          <span className="w-3" />
        )}
        {entry.isDirectory ? (
          expanded ? (
            <FolderOpen className="size-3.5 shrink-0" />
          ) : (
            <Folder className="size-3.5 shrink-0" />
          )
        ) : (
          <File className="size-3.5 shrink-0" />
        )}
        <span className="truncate">{entry.name}</span>
      </button>
      {entry.isDirectory && expanded && (
        <Directory
          adapter={adapter}
          path={entry.path}
          depth={depth + 1}
          onOpen={onOpen}
        />
      )}
    </>
  )
}

export function FileExplorerPanel({
  adapter,
  onOpen,
}: {
  adapter: WorkspaceAdapter
  onOpen: (path: string) => void
}) {
  const queryClient = useQueryClient()
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center border-b border-border px-3 text-xs font-medium">
        Workspace
        <button
          type="button"
          className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Refresh files"
          onClick={() =>
            void queryClient.invalidateQueries({
              queryKey: ["workspace-files", adapter.key],
            })
          }
        >
          <RefreshCw className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto py-1">
        <Directory adapter={adapter} path="." depth={0} onOpen={onOpen} />
      </div>
    </div>
  )
}

function languageFor(path: string) {
  const extension = path.slice(path.lastIndexOf(".") + 1).toLowerCase()
  return {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    py: "python",
    json: "json",
    md: "markdown",
    css: "css",
    html: "html",
    yml: "yaml",
    yaml: "yaml",
  }[extension]
}

export function FileEditorPanel({
  adapter,
  path,
  onDirtyChange,
}: {
  adapter: WorkspaceAdapter
  path: string
  onDirtyChange?: (path: string, dirty: boolean) => void
}) {
  const { resolvedTheme } = useTheme()
  const file = useQuery({
    queryKey: ["workspace-file", adapter.key, path],
    queryFn: () => adapter.read(path),
  })
  const [value, setValue] = useState("")
  const [savedValue, setSavedValue] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  useEffect(() => {
    if (file.data?.content == null) return
    setValue(file.data.content)
    setSavedValue(file.data.content)
    setSaveError(null)
  }, [file.data])
  const dirty = value !== savedValue
  useEffect(() => {
    onDirtyChange?.(path, dirty)
    return () => onDirtyChange?.(path, false)
  }, [dirty, onDirtyChange, path])
  const save = useCallback(async () => {
    setSaving(true)
    setSaveError(null)
    try {
      await adapter.write(path, value)
      setSavedValue(value)
    } catch (error) {
      setSaveError(
        error instanceof Error ? error.message : "Could not save file."
      )
    } finally {
      setSaving(false)
    }
  }, [adapter, path, value])
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault()
        if (dirty && !saving) void save()
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [dirty, save, saving])
  const state = useMemo(() => {
    if (file.isPending) return "Loading file…"
    if (file.error)
      return file.error instanceof Error
        ? file.error.message
        : "Could not read file."
    if (file.data?.binary) return "Binary files cannot be edited here."
    if (file.data?.truncated) return "This file is too large to edit here."
    return null
  }, [file.data, file.error, file.isPending])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3 text-xs">
        <span className="min-w-0 truncate" title={path}>
          {path}
        </span>
        {dirty && (
          <span
            className="size-1.5 shrink-0 rounded-full bg-primary"
            aria-label="Unsaved"
          />
        )}
        <button
          type="button"
          className={cn(
            "ml-auto rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground",
            (!dirty || saving) && "opacity-40"
          )}
          disabled={!dirty || saving}
          onClick={() => void save()}
          aria-label="Save file"
        >
          <Save className="size-3.5" />
        </button>
      </div>
      {saveError && (
        <div className="border-b border-border px-3 py-2 text-xs text-destructive">
          {saveError}
        </div>
      )}
      {state ? (
        <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground">
          {state}
        </div>
      ) : (
        <Editor
          className="min-h-0 flex-1"
          value={value}
          language={languageFor(path)}
          theme={resolvedTheme === "dark" ? "vs-dark" : "light"}
          onChange={(next) => setValue(next ?? "")}
          options={{
            minimap: { enabled: false },
            fontSize: 12,
            wordWrap: "on",
            automaticLayout: true,
          }}
        />
      )}
    </div>
  )
}
