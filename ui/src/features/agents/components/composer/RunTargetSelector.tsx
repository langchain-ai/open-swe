import { CheckIcon, LaptopRegularIcon } from "@langchain/macaw-components/icons"
import { useEffect, useMemo, useRef, useState } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderOpenIcon } from "@phosphor-icons/react/dist/ssr/FolderOpen"
import { FolderPlusIcon } from "@phosphor-icons/react/dist/ssr/FolderPlus"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitForkIcon } from "@phosphor-icons/react/dist/ssr/GitFork"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import type { ReactNode } from "react"

import { ComposerControlChevron } from "./ComposerControl"
import type {
  DesktopProject,
  DesktopProjectRef,
  DesktopWorkspaceMode,
} from "@/desktop"
import { cn } from "@/lib/utils"

export type RunTarget = "cloud" | "local"

const TRIGGER_CLASS_NAME =
  "flex items-center gap-1 text-secondary transition-opacity outline-none hover:opacity-80 focus-visible:ring-2 focus-visible:ring-focus"

function TriggerIcon({ icon: Icon }: { icon: IconComponent }) {
  return <Icon className="size-3.5 shrink-0" weight="regular" />
}

function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <DropdownMenuLabel className="px-space-2 py-space-1 text-xxs text-tertiary">
      {children}
    </DropdownMenuLabel>
  )
}

/** A menu row: leading icon, label, and a check when it is the current choice. */
function MenuOption({
  icon: Icon,
  selected = false,
  destructive = false,
  disabled,
  title,
  onSelect,
  children,
}: {
  icon?: IconComponent
  selected?: boolean
  destructive?: boolean
  disabled?: boolean
  title?: string
  onSelect?: () => void
  children: ReactNode
}) {
  return (
    <DropdownMenuItem
      className={cn(
        "gap-space-2 text-xs",
        destructive && "text-error-secondary focus:bg-error-subtle"
      )}
      disabled={disabled}
      onSelect={onSelect}
      size="sm"
      title={title}
    >
      {Icon ? (
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            destructive ? "text-icon-error" : "text-icon-secondary"
          )}
          weight="regular"
        />
      ) : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {selected ? (
        <CheckIcon
          className="size-3.5 shrink-0 text-icon-secondary"
          weight="regular"
        />
      ) : null}
    </DropdownMenuItem>
  )
}

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
      <TriggerIcon icon={value === "local" ? LaptopRegularIcon : CloudIcon} />
      <span>{value === "local" ? "This Mac" : "Cloud"}</span>
      {pending && <span className="text-tertiary">· next message</span>}
    </>
  )
  const title = pending ? "Moves with your next message" : undefined
  if (!onChange)
    return (
      <span className="flex items-center gap-1 text-secondary" title={title}>
        {current}
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={TRIGGER_CLASS_NAME} title={title}>
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
          <MenuLabel>Work in</MenuLabel>
          <MenuOption
            icon={LaptopRegularIcon}
            onSelect={() => onChange("local")}
            selected={value === "local"}
          >
            This Mac
          </MenuOption>
          <MenuOption
            icon={CloudIcon}
            onSelect={() => onChange("cloud")}
            selected={value === "cloud"}
          >
            Cloud
          </MenuOption>
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
        className={cn(TRIGGER_CLASS_NAME, "max-w-[260px]", triggerClassName)}
        title={selectedRepo?.cwd}
      >
        <TriggerIcon icon={FolderOpenIcon} />
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
          <MenuLabel>Repositories</MenuLabel>
          {repos.length === 0 && (
            <MenuOption disabled>No repositories added</MenuOption>
          )}
          {repos.map((repo) => (
            <MenuOption
              icon={FolderOpenIcon}
              key={repo.cwd}
              onSelect={() => onSelectRepo(repo.cwd)}
              selected={selectedRepoPath === repo.cwd}
              title={repo.cwd}
            >
              {repo.name}
            </MenuOption>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <MenuOption icon={FolderPlusIcon} onSelect={onAddRepo}>
            Add repository…
          </MenuOption>
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
                    <MenuOption
                      destructive
                      icon={FolderOpenIcon}
                      key={repo.cwd}
                      onSelect={() => onRemoveRepo(repo.cwd)}
                      title={repo.cwd}
                    >
                      {repo.name}
                    </MenuOption>
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
      <span className="flex items-center gap-1 text-secondary">
        <TriggerIcon icon={icon} />
        {label}
      </span>
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={TRIGGER_CLASS_NAME}>
        <TriggerIcon icon={icon} />
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
          <MenuLabel>Workspace</MenuLabel>
          <MenuOption
            icon={FolderIcon}
            onSelect={() => onChange("local")}
            selected={value === "local"}
          >
            Current checkout
          </MenuOption>
          <MenuOption
            icon={GitForkIcon}
            onSelect={() => onChange("worktree")}
            selected={value === "worktree"}
          >
            New worktree
          </MenuOption>
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
        className="flex max-w-[260px] cursor-pointer items-center gap-1 text-secondary transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-50"
      >
        <TriggerIcon icon={GitBranchIcon} />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
        <ComposerControlChevron />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-1 flex max-h-72 w-72 flex-col overflow-hidden rounded-lg border border-subtle bg-elevated text-xs text-primary shadow-md">
          <div className="border-b border-default">
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search refs..."
              className="w-full bg-transparent px-3 py-2 text-primary outline-none placeholder:text-placeholder"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <div className="px-2 py-1.5 text-secondary">No refs found.</div>
            ) : (
              filtered.map((ref) => (
                <button
                  key={ref.name}
                  type="button"
                  onClick={() => select(ref.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-elevated-hover hover:text-primary",
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
        </div>
      )}
    </div>
  )
}
