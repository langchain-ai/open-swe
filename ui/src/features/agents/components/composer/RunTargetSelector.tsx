import { useEffect, useMemo, useRef, useState } from "react"
import {
  Check,
  Cloud,
  Folder,
  FolderGit2,
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
import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "@/components/ui/menu"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
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
    <Menu>
      <MenuTrigger className="flex items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80">
        <Icon className="size-3.5 shrink-0" />
        <span>{value === "local" ? "This Mac" : "Cloud"}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-44" sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Work in</MenuGroupLabel>
          <MenuItem onClick={() => onChange("local")}>
            <Laptop />
            This Mac{value === "local" && <Check className="ml-auto" />}
          </MenuItem>
          <MenuItem onClick={() => onChange("cloud")}>
            <Cloud />
            Cloud{value === "cloud" && <Check className="ml-auto" />}
          </MenuItem>
        </MenuGroup>
      </MenuPopup>
    </Menu>
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
  return (
    <RepoSelector
      repos={repos.map((repo) => ({ full_name: repo.cwd, label: repo.name }))}
      selectedRepo={selectedRepoPath}
      onRepoChange={(repo) => {
        if (repo) onSelectRepo(repo)
      }}
      placeholder={placeholder}
      triggerClassName={triggerClassName}
      side={side}
      historyScope="local"
      refreshable={false}
      footer={
        <div className="border-t border-border p-2">
          <button
            type="button"
            onClick={onAddRepo}
            className="flex items-center gap-2 py-1"
          >
            <FolderPlus className="size-3.5" />
            Add repository…
          </button>
          {repos.map((repo) => (
            <button
              key={repo.cwd}
              type="button"
              onClick={() => onRemoveRepo(repo.cwd)}
              className="flex items-center gap-2 py-1 text-muted-foreground"
              aria-label={`Remove ${repo.name}`}
            >
              <Trash2 className="size-3.5" />
              Remove {repo.name}
            </button>
          ))}
        </div>
      }
    />
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
      <span className="flex items-center gap-1 text-muted-foreground">
        <Icon className="size-3.5 shrink-0" />
        {label}
      </span>
    )
  return (
    <Menu>
      <MenuTrigger className="flex items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80">
        <Icon className="size-3.5 shrink-0" />
        <span>{label}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-52" sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Workspace</MenuGroupLabel>
          <MenuItem onClick={() => onChange("local")}>
            <Folder />
            Current checkout
            {value === "local" && <Check className="ml-auto" />}
          </MenuItem>
          <MenuItem onClick={() => onChange("worktree")}>
            <FolderGit2 />
            New worktree
            {value === "worktree" && <Check className="ml-auto" />}
          </MenuItem>
        </MenuGroup>
      </MenuPopup>
    </Menu>
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
        className="flex max-w-[260px] cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-50"
      >
        <GitBranch className="size-3.5 shrink-0" />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
        <ComposerControlChevron />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1 flex max-h-72 w-72 flex-col overflow-hidden rounded-lg bg-popover text-xs text-popover-foreground shadow-md ring-1 ring-foreground/10">
          <div className="border-b border-border">
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search refs..."
              className="w-full bg-transparent px-3 py-2 text-foreground outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <div className="px-2 py-1.5 text-muted-foreground">
                No refs found.
              </div>
            ) : (
              filtered.map((ref) => (
                <button
                  key={ref.name}
                  type="button"
                  onClick={() => select(ref.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent hover:text-accent-foreground",
                    ref.name === selectedBranch
                      ? "text-foreground"
                      : "text-muted-foreground"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{ref.name}</span>
                  {badge(ref) && (
                    <span className="shrink-0 text-[10px] text-muted-foreground/60">
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
