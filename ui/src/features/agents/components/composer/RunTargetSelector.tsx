import { Button } from "@langchain/macaw-components/Button"
import {
  CheckIcon,
  LaptopRegularIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { useMemo, useState } from "react"
import {
  Command,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/macaw-components/Command"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderDashedIcon } from "@phosphor-icons/react/dist/ssr/FolderDashed"
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
      {pending && <span className="text-tertiary">· moving…</span>}
    </>
  )
  const title = pending
    ? "Moving this thread and its checkout; send once it's there"
    : undefined
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

const PROJECT_ROW_CLASS_NAME =
  "group/project gap-space-2 rounded-md px-space-2 py-space-1 text-xs text-primary"

function ProjectRowIcon({ icon: Icon }: { icon: IconComponent }) {
  return (
    <Icon className="size-3.5 shrink-0 text-icon-secondary" weight="regular" />
  )
}

/**
 * Picks the project a This Mac thread works in. With `onClear`, choosing none
 * is allowed: the agent then works in a task folder of its own.
 */
export function LocalProjectSelector({
  projects,
  selectedPath,
  onSelect,
  onAdd,
  onRemove,
  onClear,
  side = "bottom",
}: {
  projects: Array<DesktopProject>
  selectedPath: string | null
  onSelect: (cwd: string) => void
  onAdd: () => void
  onRemove: (cwd: string) => void
  onClear?: () => void
  side?: "top" | "bottom"
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const selected = projects.find((project) => project.cwd === selectedPath)
  const needle = query.trim().toLowerCase()
  const matches = needle
    ? projects.filter((project) => project.name.toLowerCase().includes(needle))
    : projects

  const toggle = (next: boolean) => {
    if (!next) setQuery("")
    setOpen(next)
  }
  const choose = (action: () => void) => {
    action()
    toggle(false)
  }

  return (
    <Popover open={open} onOpenChange={toggle}>
      <PopoverTrigger
        className={cn(COMPOSER_TRIGGER_CLASS_NAME, "max-w-[260px] min-w-0")}
        title={selected?.cwd}
      >
        <ComposerTriggerIcon icon={FolderIcon} />
        <span className="truncate">{selected?.name ?? "Choose project"}</span>
        <ComposerControlChevron />
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side={side}
        sideOffset={7}
        className="w-72 overflow-hidden p-0"
      >
        <Command label="Projects" loop shouldFilter={false}>
          <CommandInput
            autoFocus
            className="rounded-none border-0 border-b border-muted bg-transparent focus-within:border-muted"
            leftDecorator={
              <MagnifyingGlassRegularIcon
                className="text-icon-secondary"
                size={14}
              />
            }
            onValueChange={setQuery}
            placeholder="Search projects"
            size="sm"
            value={query}
            variant="plain"
          />
          <CommandList className="max-h-72 p-space-1">
            <CommandItem
              className={PROJECT_ROW_CLASS_NAME}
              onSelect={() => choose(onAdd)}
              size="sm"
              value="add-project"
            >
              <ProjectRowIcon icon={FolderPlusIcon} />
              Add project…
            </CommandItem>
            {selected && onClear && (
              <CommandItem
                className={PROJECT_ROW_CLASS_NAME}
                onSelect={() => choose(onClear)}
                size="sm"
                title="Work in a task folder of its own"
                value="no-project"
              >
                <ProjectRowIcon icon={FolderDashedIcon} />
                No project
              </CommandItem>
            )}
            {matches.map((project) => (
              <CommandItem
                className={PROJECT_ROW_CLASS_NAME}
                key={project.cwd}
                onSelect={() => choose(() => onSelect(project.cwd))}
                size="sm"
                title={project.cwd}
                value={project.cwd}
              >
                <ProjectRowIcon icon={FolderIcon} />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                {project.cwd === selectedPath && (
                  <CheckIcon
                    className="size-3.5 shrink-0 text-icon-secondary"
                    weight="regular"
                  />
                )}
                <IconButton
                  className="-my-space-1 opacity-0 group-hover/project:opacity-100 group-data-[selected=true]/project:opacity-100 focus-visible:opacity-100"
                  color="secondary"
                  icon={TrashIcon}
                  label={`Remove ${project.name} from projects`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onRemove(project.cwd)
                  }}
                  size="xs"
                  variant="plain"
                />
              </CommandItem>
            ))}
            {needle && matches.length === 0 && (
              <p className="px-space-2 py-space-1 text-xs text-secondary">
                No matching projects
              </p>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
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
              <Button
                color="secondary"
                variant="plain"
                size="sm"
                aria-label={ref.name}
                key={ref.name}
                type="button"
                onClick={() => select(ref.name)}
                className={cn(
                  "focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
                  "h-auto justify-start whitespace-normal",
                  "w-full gap-space-2 px-space-2 py-space-1 text-left hover:bg-elevated-hover hover:text-primary focus-visible:bg-elevated-hover",
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
              </Button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
