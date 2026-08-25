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
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "@/components/ui/menu"

export function SidebarProjectSelector({
  projects,
  selectedProjectPath,
  onSelectProject,
  onAddProject,
  onRemoveProject,
}: {
  projects: Array<Pick<DesktopProject, "cwd" | "name">>
  selectedProjectPath: string | null
  onSelectProject: (cwd: string | null) => void
  onAddProject?: () => void
  onRemoveProject?: (cwd: string) => void
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
                onClick={() => onSelectProject(project.cwd)}
                title={project.cwd}
              >
                <FolderIcon />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                {selectedProject?.cwd === project.cwd && (
                  <CheckIcon className="ml-auto" />
                )}
              </MenuItem>
            ))}
          </MenuGroup>
          {projects.length > 0 && onRemoveProject && (
            <>
              <MenuSeparator />
              <MenuSub>
                <MenuSubTrigger>
                  <TrashIcon />
                  Remove project…
                </MenuSubTrigger>
                <MenuSubPopup className="w-60">
                  <MenuGroup>
                    {projects.map((project) => (
                      <MenuItem
                        key={project.cwd}
                        onClick={() => onRemoveProject(project.cwd)}
                        title={project.cwd}
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
