import { useMemo } from "react"
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
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from "@/components/ui/combobox"
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
    <Menu>
      <MenuTrigger
        className={cn(
          "flex max-w-[260px] items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80",
          triggerClassName
        )}
        title={selectedRepo?.cwd}
      >
        <FolderOpen className="size-3.5 shrink-0" />
        <span className="truncate">{selectedRepo?.name ?? placeholder}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-64" side={side} sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Repositories</MenuGroupLabel>
          {repos.length === 0 && (
            <MenuItem disabled>No repositories added</MenuItem>
          )}
          {repos.map((repo) => (
            <MenuItem
              key={repo.cwd}
              onClick={() => onSelectRepo(repo.cwd)}
              title={repo.cwd}
            >
              <FolderOpen />
              <span className="min-w-0 flex-1 truncate">{repo.name}</span>
              {selectedRepoPath === repo.cwd && <Check className="ml-auto" />}
            </MenuItem>
          ))}
        </MenuGroup>
        <MenuSeparator />
        <MenuGroup>
          <MenuItem onClick={onAddRepo}>
            <FolderPlus />
            Add repository…
          </MenuItem>
          {repos.length > 0 && (
            <MenuSub>
              <MenuSubTrigger>
                <Trash2 />
                Remove repository…
              </MenuSubTrigger>
              <MenuSubPopup className="w-64">
                <MenuGroup>
                  {repos.map((repo) => (
                    <MenuItem
                      key={repo.cwd}
                      onClick={() => onRemoveRepo(repo.cwd)}
                      title={repo.cwd}
                      variant="destructive"
                    >
                      <FolderOpen />
                      <span className="truncate">{repo.name}</span>
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
  const refsByName = useMemo(
    () => new Map(refs.map((ref) => [ref.name, ref])),
    [refs]
  )
  const names = useMemo(() => [...refsByName.keys()], [refsByName])

  return (
    <Combobox
      disabled={disabled}
      items={names}
      value={selectedBranch}
      onOpenChange={(open) => {
        if (open) onRefresh()
      }}
      onValueChange={(value) => {
        if (value) onSelectBranch(value)
      }}
    >
      <ComboboxTrigger
        aria-label="Branch"
        className="flex max-w-[260px] min-w-0 shrink cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-50"
      >
        <GitBranch className="size-3.5 shrink-0" />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
      </ComboboxTrigger>
      <ComboboxContent side="top" sideOffset={4} className="w-72">
        <ComboboxInput placeholder="Search refs..." showTrigger={false} />
        <ComboboxEmpty>No refs found.</ComboboxEmpty>
        <ComboboxList>
          {(name: string) => {
            const ref = refsByName.get(name)
            const label = ref ? badge(ref) : null
            return (
              <ComboboxItem key={name} value={name} className="pr-7">
                <span className="min-w-0 flex-1 truncate">{name}</span>
                {label && (
                  <span className="shrink-0 text-[10px] text-muted-foreground/60">
                    {label}
                  </span>
                )}
              </ComboboxItem>
            )
          }}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  )
}
