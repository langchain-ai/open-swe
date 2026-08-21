import {
  CaretDownIcon,
  CheckIcon,
  FolderIcon,
  FolderPlusIcon,
  TrashIcon,
} from "@phosphor-icons/react"

import type { DesktopProject } from "@/desktop"
import {
  Menu,
  MenuGroup,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "@/components/ui/menu"

export function SidebarProjectSelector({
  projects,
  selectedProjectPath,
  onSelectProject,
  onAddProject,
  onRemoveProject,
}: {
  projects: Array<DesktopProject>
  selectedProjectPath: string | null
  onSelectProject: (cwd: string | null) => void
  onAddProject: () => void
  onRemoveProject: (cwd: string) => void
}) {
  const selectedProject = projects.find(
    (project) => project.cwd === selectedProjectPath
  )

  return (
    <div className="mb-1 flex items-center gap-1 px-1 py-1">
      <Menu>
        <MenuTrigger
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1 text-[13px] font-medium text-foreground transition-colors hover:bg-sidebar-row-hover"
          title={selectedProject?.cwd}
        >
          <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-left">
            {selectedProject?.name ?? "All projects"}
          </span>
          <CaretDownIcon className="size-3 shrink-0 text-muted-foreground" />
        </MenuTrigger>
        <MenuPopup align="start" className="w-60" sideOffset={4}>
          <MenuGroup>
            <MenuItem onClick={() => onSelectProject(null)}>
              <FolderIcon />
              <span className="min-w-0 flex-1 truncate">All projects</span>
              {!selectedProject && <CheckIcon className="ml-auto" />}
            </MenuItem>
            {projects.map((project) => (
              <MenuItem
                key={project.cwd}
                className="pe-1"
                onClick={() => onSelectProject(project.cwd)}
                title={project.cwd}
              >
                <FolderIcon />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                {selectedProject?.cwd === project.cwd && <CheckIcon />}
                <button
                  aria-label={`Remove ${project.name}`}
                  className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground/60 transition-colors hover:bg-sidebar-row-hover hover:text-destructive"
                  onClick={(event) => {
                    event.stopPropagation()
                    if (selectedProject?.cwd === project.cwd)
                      onSelectProject(null)
                    onRemoveProject(project.cwd)
                  }}
                  title="Remove project"
                  type="button"
                >
                  <TrashIcon className="size-3.5" />
                </button>
              </MenuItem>
            ))}
          </MenuGroup>
        </MenuPopup>
      </Menu>
      <button
        aria-label="Add project"
        className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
        onClick={onAddProject}
        title="Add project"
        type="button"
      >
        <FolderPlusIcon className="size-4" />
      </button>
    </div>
  )
}
