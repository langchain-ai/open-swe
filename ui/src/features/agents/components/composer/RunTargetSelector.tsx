import { LaptopRegularIcon } from "@langchain/macaw-components/icons"
import { useMemo, useState } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderOpenIcon } from "@phosphor-icons/react/dist/ssr/FolderOpen"
import { FolderPlusIcon } from "@phosphor-icons/react/dist/ssr/FolderPlus"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitForkIcon } from "@phosphor-icons/react/dist/ssr/GitFork"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"

import {
  COMPOSER_TRIGGER_CLASS_NAME,
  ComposerControlChevron,
  ComposerMenuLabel,
  ComposerMenuOption,
  ComposerTriggerIcon,
} from "./ComposerControl"
import type {
  DesktopProject,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import { cn } from "@/lib/utils"

export type RunTarget = "cloud" | "local"

export function RunTargetSelector({
  value,
  onChange,
  pending = false,
}: {
  value: RunTarget
  /** Omitted while the target can't change. */
  onChange?: (value: RunTarget) => void
  pending?: boolean
}) {
  const current = (
    <>
      <ComposerTriggerIcon
        icon={value === "local" ? LaptopRegularIcon : CloudIcon}
      />
      <span>{value === "local" ? "This Mac" : "Cloud"}</span>
      {pending && <span className="text-tertiary">· next message</span>}
    </>
  )
  const title = pending ? "Moves with your next message" : undefined
  if (!onChange)
    return (
      <span
        className="flex items-center gap-space-1 text-secondary"
        title={title}
      >
        {current}
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={COMPOSER_TRIGGER_CLASS_NAME}
        title={title}
      >
        {current}
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-44"
        side="top"
        sideOffset={7}
      >
        <DropdownMenuGroup>
          <ComposerMenuLabel>Work in</ComposerMenuLabel>
          <ComposerMenuOption
            icon={LaptopRegularIcon}
            onSelect={() => onChange("local")}
            selected={value === "local"}
          >
            This Mac
          </ComposerMenuOption>
          <ComposerMenuOption
            icon={CloudIcon}
            onSelect={() => onChange("cloud")}
            selected={value === "cloud"}
          >
            Cloud
          </ComposerMenuOption>
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
          COMPOSER_TRIGGER_CLASS_NAME,
          "max-w-[260px]",
          triggerClassName
        )}
        title={selectedRepo?.cwd}
      >
        <ComposerTriggerIcon icon={FolderOpenIcon} />
        <span className="truncate">{selectedRepo?.name ?? placeholder}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-64"
        side={side}
        sideOffset={7}
      >
        <DropdownMenuGroup>
          <ComposerMenuLabel>Repositories</ComposerMenuLabel>
          {repos.length === 0 && (
            <ComposerMenuOption disabled>
              No repositories added
            </ComposerMenuOption>
          )}
          {repos.map((repo) => (
            <ComposerMenuOption
              icon={FolderOpenIcon}
              key={repo.cwd}
              onSelect={() => onSelectRepo(repo.cwd)}
              selected={selectedRepoPath === repo.cwd}
              title={repo.cwd}
            >
              {repo.name}
            </ComposerMenuOption>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <ComposerMenuOption icon={FolderPlusIcon} onSelect={onAddRepo}>
            Add repository…
          </ComposerMenuOption>
          {repos.length > 0 && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="gap-space-2 text-xs" size="sm">
                <TrashIcon
                  className="size-3.5 shrink-0 text-icon-secondary"
                  weight="regular"
                />
                Remove repository…
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-64">
                <DropdownMenuGroup>
                  {repos.map((repo) => (
                    <ComposerMenuOption
                      destructive
                      icon={FolderOpenIcon}
                      key={repo.cwd}
                      onSelect={() => onRemoveRepo(repo.cwd)}
                      title={repo.cwd}
                    >
                      {repo.name}
                    </ComposerMenuOption>
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
  const icon = value === "worktree" ? GitForkIcon : FolderIcon
  const label = value === "worktree" ? worktreeLabel : "Current checkout"
  if (!onChange)
    return (
      <span className="flex items-center gap-space-1 text-secondary">
        <ComposerTriggerIcon icon={icon} />
        {label}
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={COMPOSER_TRIGGER_CLASS_NAME}>
        <ComposerTriggerIcon icon={icon} />
        <span>{label}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-52"
        side="top"
        sideOffset={7}
      >
        <DropdownMenuGroup>
          <ComposerMenuLabel>Workspace</ComposerMenuLabel>
          <ComposerMenuOption
            icon={FolderIcon}
            onSelect={() => onChange("local")}
            selected={value === "local"}
          >
            Current checkout
          </ComposerMenuOption>
          <ComposerMenuOption
            icon={GitForkIcon}
            onSelect={() => onChange("worktree")}
            selected={value === "worktree"}
          >
            New worktree
          </ComposerMenuOption>
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

  const filtered = useMemo(() => {
    const value = query.trim().toLowerCase()
    if (!value) return refs
    return refs.filter((ref) => ref.name.toLowerCase().includes(value))
  }, [query, refs])

  const toggle = (next: boolean) => {
    if (next) onRefresh()
    else setQuery("")
    setOpen(next)
  }

  const select = (branch: string) => {
    onSelectBranch(branch)
    toggle(false)
  }

  return (
    <Popover open={open} onOpenChange={toggle}>
      <PopoverTrigger
        disabled={disabled}
        className={cn(COMPOSER_TRIGGER_CLASS_NAME, "max-w-[260px] min-w-0")}
      >
        <ComposerTriggerIcon icon={GitBranchIcon} />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
        <ComposerControlChevron />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        sideOffset={7}
        className="flex max-h-72 w-72 flex-col overflow-hidden p-0 text-xs text-primary"
      >
        <div className="border-b border-default">
          <Input
            autoFocus
            size="sm"
            variant="plain"
            value={query}
            onChange={setQuery}
            placeholder="Search refs…"
            aria-label="Search refs"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-space-1">
          {filtered.length === 0 ? (
            <div className="px-space-2 py-space-1 text-secondary">
              No refs found
            </div>
          ) : (
            filtered.map((ref) => (
              <button
                key={ref.name}
                type="button"
                onClick={() => select(ref.name)}
                className={cn(
                  "flex w-full items-center gap-space-2 rounded-sm px-space-2 py-space-1 text-left transition-colors hover:bg-elevated-hover hover:text-primary focus-visible:bg-elevated-hover focus-visible:outline-none",
                  ref.name === selectedBranch
                    ? "text-primary"
                    : "text-secondary"
                )}
              >
                <span className="min-w-0 flex-1 truncate">{ref.name}</span>
                {badge(ref) && (
                  <span className="shrink-0 text-xxs text-tertiary">
                    {badge(ref)}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
