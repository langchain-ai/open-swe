import {
  CaretDownIcon,
  CheckIcon,
  FolderIcon,
  FolderPlusIcon,
  TrashIcon,
} from "@phosphor-icons/react"

import type { DesktopProject } from "@/desktop"
import type { SidebarProjectOption } from "@/features/agents/lib/sidebarThreads"
import {
  Menu,
  MenuGroup,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "@/components/ui/menu"

export function SidebarProjectSelector({
  projects,
  localProjects,
  selectedProjectKey,
  onSelectProject,
  onAddProject,
  onRemoveProject,
}: {
  projects: Array<SidebarProjectOption>
  localProjects?: Array<DesktopProject>
  selectedProjectKey: string | null
  onSelectProject: (key: string | null) => void
  onAddProject?: () => void
  onRemoveProject?: (cwd: string) => void
}) {
  const selectedProject = projects.find(
    (project) => project.key === selectedProjectKey
  )

  return (
    <div className="mb-1 flex items-center gap-1 px-1 py-1">
      <Menu>
        <MenuTrigger
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1 text-[13px] font-medium text-foreground transition-colors hover:bg-sidebar-row-hover"
          title={selectedProject?.label}
        >
          <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-left">
            {selectedProject?.label ?? "All projects"}
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
                key={project.key}
                onClick={() => onSelectProject(project.key)}
                title={project.label}
              >
                <FolderIcon />
                <span className="min-w-0 flex-1 truncate">{project.label}</span>
                {selectedProject?.key === project.key && (
                  <CheckIcon className="ml-auto" />
                )}
              </MenuItem>
            ))}
          </MenuGroup>
          {localProjects && localProjects.length > 0 && onRemoveProject && (
            <>
              <MenuSeparator />
              <MenuSub>
                <MenuSubTrigger>
                  <TrashIcon />
                  Remove project…
                </MenuSubTrigger>
                <MenuSubPopup className="w-60">
                  <MenuGroup>
                    {localProjects.map((project) => (
                      <MenuItem
                        key={project.cwd}
                        onClick={() => onRemoveProject(project.cwd)}
                        variant="destructive"
                      >
                        <FolderIcon />
                        <span className="truncate">{project.name}</span>
                      </MenuItem>
                    ))}
                  </MenuGroup>
                </MenuSubPopup>
              </MenuSub>
            </>
          )}
        </MenuPopup>
      </Menu>
      {onAddProject && (
        <button
          aria-label="Add project"
          className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
          onClick={onAddProject}
          title="Add project"
          type="button"
        >
          <FolderPlusIcon className="size-4" />
        </button>
      )}
    </div>
  )
}
