import {
  Check,
  Cloud,
  FolderOpen,
  FolderPlus,
  Laptop,
  LockKeyhole,
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

export type RunTarget = "cloud" | "local"

interface RunTargetSelectorProps {
  value: RunTarget
  onChange: (value: RunTarget) => void
  localEnabled?: boolean
  projects: Array<DesktopProject>
  selectedProjectPath: string | null
  onSelectProject: (cwd: string) => void
  onAddProject: () => void
  onRemoveProject: (cwd: string) => void
}

export function RunTargetSelector({
  value,
  onChange,
  localEnabled = false,
  projects,
  selectedProjectPath,
  onSelectProject,
  onAddProject,
  onRemoveProject,
}: RunTargetSelectorProps) {
  const selectedProject = projects.find(
    (project) => project.cwd === selectedProjectPath
  )
  const Icon = value === "local" ? Laptop : Cloud
  const label =
    value === "local"
      ? selectedProject
        ? `This Mac · ${selectedProject.name}${selectedProject.branch ? ` · ${selectedProject.branch}` : ""}`
        : "This Mac"
      : "Cloud"

  return (
    <Menu>
      <MenuTrigger
        className="flex max-w-[260px] items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80"
        title={selectedProject?.cwd}
      >
        <Icon className="size-3.5 shrink-0" />
        <span className="truncate">{label}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-56" sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Run on</MenuGroupLabel>
          <MenuItem onClick={() => onChange("cloud")}>
            <Cloud />
            Cloud
            {value === "cloud" && <Check className="ml-auto" />}
          </MenuItem>
          <MenuSub>
            <MenuSubTrigger disabled={!localEnabled}>
              <Laptop />
              This Mac
              {!localEnabled && <LockKeyhole className="ml-auto" />}
              {localEnabled && value === "local" && (
                <Check className="ml-auto" />
              )}
            </MenuSubTrigger>
            <MenuSubPopup className="w-64">
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
                    <span className="min-w-0 flex-1 truncate">
                      {project.name}
                      {project.branch && (
                        <span className="text-muted-foreground/60">
                          {` · ${project.branch}`}
                        </span>
                      )}
                    </span>
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
            </MenuSubPopup>
          </MenuSub>
        </MenuGroup>
      </MenuPopup>
    </Menu>
  )
}
