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
import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "@/components/ui/menu"
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

export function LocalProjectSelector({
  projects,
  selectedProjectPath,
  onSelectProject,
  onAddProject,
  onRemoveProject,
  placeholder = "Select project",
  triggerClassName,
  side = "bottom",
}: {
  projects: Array<DesktopProject>
  selectedProjectPath: string | null
  onSelectProject: (cwd: string) => void
  onAddProject: () => void
  onRemoveProject: (cwd: string) => void
  placeholder?: string
  triggerClassName?: string
  side?: "top" | "bottom"
}) {
  const selectedProject = projects.find(
    (project) => project.cwd === selectedProjectPath
  )
  return (
    <Menu>
      <MenuTrigger
        className={cn(
          "flex max-w-[260px] items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80",
          triggerClassName
        )}
        title={selectedProject?.cwd}
      >
        <FolderOpen className="size-3.5 shrink-0" />
        <span className="truncate">{selectedProject?.name ?? placeholder}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-64" side={side} sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Projects</MenuGroupLabel>
          {projects.length === 0 && (
            <MenuItem disabled>No projects added</MenuItem>
          )}
          {projects.map((project) => (
            <MenuItem
              key={project.cwd}
              onClick={() => onSelectProject(project.cwd)}
              title={project.cwd}
            >
              <FolderOpen />
              <span className="min-w-0 flex-1 truncate">{project.name}</span>
              {selectedProjectPath === project.cwd && (
                <Check className="ml-auto" />
              )}
            </MenuItem>
          ))}
        </MenuGroup>
        <MenuSeparator />
        <MenuGroup>
          <MenuItem onClick={onAddProject}>
            <FolderPlus />
            Add project…
          </MenuItem>
          {projects.length > 0 && (
            <MenuSub>
              <MenuSubTrigger>
                <Trash2 />
                Remove project…
              </MenuSubTrigger>
              <MenuSubPopup className="w-64">
                <MenuGroup>
                  {projects.map((project) => (
                    <MenuItem
                      key={project.cwd}
                      onClick={() => onRemoveProject(project.cwd)}
                      title={project.cwd}
                      variant="destructive"
                    >
                      <FolderOpen />
                      <span className="truncate">{project.name}</span>
                    </MenuItem>
                  ))}
                </MenuGroup>
              </MenuSubPopup>
            </MenuSub>
          )}
        </MenuGroup>
      </MenuPopup>
    </Menu>
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
