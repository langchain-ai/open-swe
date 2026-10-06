import { useMemo, useState } from "react"

import {
  COMPOSER_POPUP_ROW_CLASS,
  ComposerControl,
  ComposerControlChevron,
} from "./ComposerControl"
import type {
  DesktopProject,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import {
  Check,
  Cloud,
  Folder,
  FolderOpen,
  FolderPlus,
  GitBranch,
  Layers,
  Monitor,
  Trash2,
} from "@/components/glyphs"
import { cn } from "@/lib/utils"

export type RunTarget = "cloud" | "local"

function isRunTarget(value: unknown): value is RunTarget {
  return value === "cloud" || value === "local"
}

function isWorkspaceMode(value: unknown): value is DesktopWorkspaceMode {
  return value === "local" || value === "worktree"
}

export function RunTargetSelector({
  value,
  onChange,
}: {
  value: RunTarget
  onChange: (value: RunTarget) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <ComposerControl>
            <Icon icon={value === "local" ? Monitor : Cloud} size="sm" />
            <span>{value === "local" ? "This Mac" : "Cloud"}</span>
            <ComposerControlChevron />
          </ComposerControl>
        }
      />
      <DropdownMenuContent align="start" className="w-44" side="top">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (isRunTarget(next)) onChange(next)
          }}
        >
          <DropdownMenuLabel>Work in</DropdownMenuLabel>
          <DropdownMenuRadioItem value="local">
            <Icon icon={Monitor} size="sm" />
            This Mac
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="cloud">
            <Icon icon={Cloud} size="sm" />
            Cloud
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
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
        render={
          <ComposerControl
            className={cn("max-w-64", triggerClassName)}
            title={selectedRepo?.cwd}
          >
            <Icon icon={FolderOpen} size="sm" />
            <span className="min-w-0 truncate">
              {selectedRepo?.name ?? placeholder}
            </span>
            <ComposerControlChevron />
          </ComposerControl>
        }
      />
      <DropdownMenuContent align="start" className="w-64" side={side}>
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
              <Icon icon={FolderOpen} size="sm" />
              <span className="min-w-0 flex-1 truncate">{repo.name}</span>
              {selectedRepoPath === repo.cwd && (
                <Icon icon={Check} size="sm" className="ml-auto" />
              )}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={onAddRepo}>
            <Icon icon={FolderPlus} size="sm" />
            Add repository…
          </DropdownMenuItem>
          {repos.length > 0 && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Icon icon={Trash2} size="sm" />
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
                      <Icon icon={FolderOpen} size="sm" />
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
  const glyph = value === "worktree" ? Layers : Folder
  const label = value === "worktree" ? worktreeLabel : "Current checkout"
  if (!onChange)
    return (
      <Inline
        gap="xs"
        className="h-control-sm px-1.5 text-meta text-ink-subtle"
      >
        <Icon icon={glyph} size="sm" />
        {label}
      </Inline>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <ComposerControl>
            <Icon icon={glyph} size="sm" />
            <span>{label}</span>
            <ComposerControlChevron />
          </ComposerControl>
        }
      />
      <DropdownMenuContent align="start" className="w-52" side="top">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (isWorkspaceMode(next)) onChange(next)
          }}
        >
          <DropdownMenuLabel>Workspace</DropdownMenuLabel>
          <DropdownMenuRadioItem value="local">
            <Icon icon={Folder} size="sm" />
            Current checkout
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="worktree">
            <Icon icon={Layers} size="sm" />
            New worktree
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
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

  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase()
    if (!value) return refs
    return refs.filter((ref) => ref.name.toLowerCase().includes(value))
  }, [query, refs])

  const select = (branch: string) => {
    onSelectBranch(branch)
    setOpen(false)
    setQuery("")
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next && !open) onRefresh()
        setOpen(next)
      }}
    >
      <PopoverTrigger
        render={
          <ComposerControl className="max-w-64" disabled={disabled}>
            <Icon icon={GitBranch} size="sm" />
            <span className="min-w-0 truncate">
              {selectedBranch ?? "No branch"}
            </span>
            <ComposerControlChevron />
          </ComposerControl>
        }
      />
      <PopoverContent
        align="start"
        className="flex max-h-72 w-72 flex-col"
        inset="flush"
        side="top"
      >
        <Box padding="xs" className="shrink-0 border-b border-line">
          <SearchInput
            autoFocus
            label="Search refs"
            placeholder="Search refs..."
            value={query}
            onValueChange={setQuery}
          />
        </Box>
        <Box padding="xs" className="min-h-0 flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="px-1.5 py-1 text-label text-ink-subtle">
              No refs found.
            </p>
          ) : (
            filtered.map((ref) => (
              <button
                key={ref.name}
                type="button"
                onClick={() => select(ref.name)}
                className={cn(
                  COMPOSER_POPUP_ROW_CLASS,
                  "cursor-pointer hover:bg-hover focus-visible:bg-hover",
                  ref.name === selectedBranch && "font-medium"
                )}
              >
                <span className="min-w-0 flex-1 truncate font-mono">
                  {ref.name}
                </span>
                {badge(ref) && (
                  <span className="shrink-0 text-meta text-ink-subtle">
                    {badge(ref)}
                  </span>
                )}
                {ref.name === selectedBranch && (
                  <Icon icon={Check} size="sm" className="text-ink-subtle" />
                )}
              </button>
            ))
          )}
        </Box>
      </PopoverContent>
    </Popover>
  )
}
