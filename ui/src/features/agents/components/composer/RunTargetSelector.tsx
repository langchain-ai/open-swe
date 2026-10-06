import { useEffect, useMemo, useRef, useState } from "react"
import {
  Check,
  Cloud,
  Folder,
  FolderGit2,
  FolderOpen,
  FolderPlus,
  GitBranch,
  Laptop,
  Trash2,
} from "lucide-react"

import { ComposerControlChevron } from "./ComposerControl"
import type {
  DesktopProject,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import { DropdownMenu, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuItem, DropdownMenuContent, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger } from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { cn } from "@/lib/utils"

export type RunTarget = "cloud" | "local"

export function RunTargetSelector({
  value,
  onChange,
}: {
  value: RunTarget
  onChange: (value: RunTarget) => void
}) {
  const Icon = value === "local" ? Laptop : Cloud
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-1 text-ink-subtle transition-opacity hover:opacity-80">
        <Icon className="size-3.5 shrink-0" />
        <span>{value === "local" ? "This Mac" : "Cloud"}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44" sideOffset={7}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>Work in</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => onChange("local")}>
            <Laptop />
            This Mac{value === "local" && <Check className="ml-auto" />}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange("cloud")}>
            <Cloud />
            Cloud{value === "cloud" && <Check className="ml-auto" />}
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function LocalRepoSelector({
  repos,
  selectedRepoPath,
  onSelectRepo,
  onAddRepo,
  onRemoveRepo,
  placeholder = "Select repository",
  triggerClassName,
  side = "bottom",
}: {
  repos: Array<DesktopProject>
  selectedRepoPath: string | null
  onSelectRepo: (cwd: string) => void
  onAddRepo: () => void
  onRemoveRepo: (cwd: string) => void
  placeholder?: string
  triggerClassName?: string
  side?: "top" | "bottom"
}) {
  const selectedRepo = repos.find((repo) => repo.cwd === selectedRepoPath)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex max-w-[260px] items-center gap-1 text-ink-subtle transition-opacity hover:opacity-80",
          triggerClassName
        )}
        title={selectedRepo?.cwd}
      >
        <FolderOpen className="size-3.5 shrink-0" />
        <span className="truncate">{selectedRepo?.name ?? placeholder}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64" side={side} sideOffset={7}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>Repositories</DropdownMenuLabel>
          {repos.length === 0 && (
            <DropdownMenuItem disabled>No repositories added</DropdownMenuItem>
          )}
          {repos.map((repo) => (
            <DropdownMenuItem
              key={repo.cwd}
              onClick={() => onSelectRepo(repo.cwd)}
              title={repo.cwd}
            >
              <FolderOpen />
              <span className="min-w-0 flex-1 truncate">{repo.name}</span>
              {selectedRepoPath === repo.cwd && <Check className="ml-auto" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={onAddRepo}>
            <FolderPlus />
            Add repository…
          </DropdownMenuItem>
          {repos.length > 0 && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Trash2 />
                Remove repository…
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-64">
                <DropdownMenuGroup>
                  {repos.map((repo) => (
                    <DropdownMenuItem
                      key={repo.cwd}
                      onClick={() => onRemoveRepo(repo.cwd)}
                      title={repo.cwd}
                      variant="destructive"
                    >
                      <FolderOpen />
                      <span className="truncate">{repo.name}</span>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          )}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function LocalWorkspaceSelector({
  value,
  onChange,
  worktreeLabel = "New worktree",
}: {
  value: DesktopWorkspaceMode
  /** Omitted once the thread has started: its workspace follows the branch. */
  onChange?: (value: DesktopWorkspaceMode) => void
  /** What the label calls a worktree the thread is already working in. */
  worktreeLabel?: string
}) {
  const Icon = value === "worktree" ? FolderGit2 : Folder
  const label = value === "worktree" ? worktreeLabel : "Current checkout"
  if (!onChange)
    return (
      <span className="flex items-center gap-1 text-ink-subtle">
        <Icon className="size-3.5 shrink-0" />
        {label}
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-1 text-ink-subtle transition-opacity hover:opacity-80">
        <Icon className="size-3.5 shrink-0" />
        <span>{label}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-52" sideOffset={7}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>Workspace</DropdownMenuLabel>
          <DropdownMenuItem onClick={() => onChange("local")}>
            <Folder />
            Current checkout
            {value === "local" && <Check className="ml-auto" />}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onChange("worktree")}>
            <FolderGit2 />
            New worktree
            {value === "worktree" && <Check className="ml-auto" />}
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function badge(ref: DesktopProjectRef) {
  if (ref.current) return "current"
  if (ref.worktreePath) return "worktree"
  return ref.isDefault ? "default" : null
}

export function LocalBranchSelector({
  refs,
  selectedBranch,
  disabled = false,
  onRefresh,
  onSelectBranch,
}: {
  refs: Array<DesktopProjectRef>
  selectedBranch: string | null
  disabled?: boolean
  onRefresh: () => void
  onSelectBranch: (branch: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase()
    if (!value) return refs
    return refs.filter((ref) => ref.name.toLowerCase().includes(value))
  }, [query, refs])

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handlePointerDown)
    return () => document.removeEventListener("mousedown", handlePointerDown)
  }, [])

  const select = (branch: string) => {
    onSelectBranch(branch)
    setOpen(false)
    setQuery("")
  }

  return (
    <div ref={containerRef} className="relative min-w-0 shrink">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (!open) onRefresh()
          setOpen((value) => !value)
        }}
        className="flex max-w-[260px] cursor-pointer items-center gap-1 text-ink-subtle transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-50"
      >
        <GitBranch className="size-3.5 shrink-0" />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
        <ComposerControlChevron />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1 flex max-h-72 w-72 flex-col overflow-hidden rounded-compact bg-panel text-label text-ink shadow-popup ring-1 ring-ink/10">
          <div className="border-b border-line">
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search refs..."
              className="w-full bg-transparent px-3 py-2 text-ink outline-none placeholder:text-ink-subtle"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <div className="px-2 py-1.5 text-ink-subtle">
                No refs found.
              </div>
            ) : (
              filtered.map((ref) => (
                <button
                  key={ref.name}
                  type="button"
                  onClick={() => select(ref.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-badge px-2 py-1.5 text-left transition-colors hover:bg-hover hover:text-ink",
                    ref.name === selectedBranch
                      ? "text-ink"
                      : "text-ink-subtle"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{ref.name}</span>
                  {badge(ref) && (
                    <span className="shrink-0 text-meta text-ink-subtle/60">
                      {badge(ref)}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
