import {
  Check,
  Cloud,
  FolderOpen,
  FolderPlus,
  GitBranch,
  Laptop,
  Trash2,
} from "lucide-react"

import { ComposerControlChevron } from "./ComposerControl"
import type { DesktopProject } from "@/desktop"
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

export function LocalBranchSelector({
  branches,
  selectedBranch,
  disabled = false,
  onRefresh,
  onSelectBranch,
}: {
  branches: Array<string>
  selectedBranch: string | null
  disabled?: boolean
  onRefresh: () => void
  onSelectBranch: (branch: string) => void
}) {
  return (
    <Menu onOpenChange={(open) => open && onRefresh()}>
      <MenuTrigger
        className="flex max-w-[260px] items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:opacity-50"
        disabled={disabled}
      >
        <GitBranch className="size-3.5 shrink-0" />
        <span className="truncate">{selectedBranch ?? "No branch"}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-64" sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Base branch</MenuGroupLabel>
          {branches.length === 0 && (
            <MenuItem disabled>No local branches</MenuItem>
          )}
          {branches.map((branch) => (
            <MenuItem key={branch} onClick={() => onSelectBranch(branch)}>
              <GitBranch />
              <span className="min-w-0 flex-1 truncate">{branch}</span>
              {selectedBranch === branch && <Check className="ml-auto" />}
            </MenuItem>
          ))}
        </MenuGroup>
      </MenuPopup>
    </Menu>
  )
}
