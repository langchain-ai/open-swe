import {
  CheckIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { useState } from "react"
import {
  Command,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/macaw-components/Command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderDashedIcon } from "@phosphor-icons/react/dist/ssr/FolderDashed"
import { FolderPlusIcon } from "@phosphor-icons/react/dist/ssr/FolderPlus"
import { UsersIcon } from "@phosphor-icons/react/dist/ssr/Users"

import {
  COMPOSER_TRIGGER_CLASS_NAME,
  ComposerControlChevron,
  ComposerTriggerIcon,
} from "@/features/agents/components/composer/ComposerControl"
import type { Project } from "@/features/projects/lib/projects"
import { cn } from "@/lib/utils"

const ROW_CLASS_NAME =
  "gap-space-2 rounded-md px-space-2 py-space-1 text-xs text-primary"

const ROW_ICON_CLASS_NAME = "size-3.5 shrink-0 text-icon-secondary"

/**
 * Picks the Open SWE project a new cloud thread is filed in. Typing a name
 * that no project has offers to create it.
 */
export function ProjectSelector({
  projects,
  selectedId,
  onSelect,
  onCreate,
  side = "top",
}: {
  projects: Array<Project>
  selectedId: string | null
  onSelect: (projectId: string | null) => void
  onCreate: (name: string) => void
  side?: "top" | "bottom"
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const selected = projects.find((project) => project.id === selectedId)
  const needle = query.trim().toLowerCase()
  const matches = needle
    ? projects.filter((project) => project.name.toLowerCase().includes(needle))
    : projects
  const creatable =
    needle && !projects.some((project) => project.name.toLowerCase() === needle)

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
        className={cn(COMPOSER_TRIGGER_CLASS_NAME, "max-w-[220px] min-w-0")}
      >
        <ComposerTriggerIcon icon={FolderIcon} />
        <span className="truncate">{selected?.name ?? "No project"}</span>
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
            placeholder="Search or name a new project"
            size="sm"
            value={query}
            variant="plain"
          />
          <CommandList className="max-h-72 p-space-1">
            {creatable && (
              <CommandItem
                className={ROW_CLASS_NAME}
                onSelect={() => choose(() => onCreate(query.trim()))}
                size="sm"
                value="create-project"
              >
                <FolderPlusIcon className={ROW_ICON_CLASS_NAME} />
                <span className="min-w-0 flex-1 truncate">
                  Create project “{query.trim()}”
                </span>
              </CommandItem>
            )}
            {!needle && (
              <CommandItem
                className={ROW_CLASS_NAME}
                onSelect={() => choose(() => onSelect(null))}
                size="sm"
                value="no-project"
              >
                <FolderDashedIcon className={ROW_ICON_CLASS_NAME} />
                <span className="min-w-0 flex-1 truncate">No project</span>
                {selectedId === null && (
                  <CheckIcon className={ROW_ICON_CLASS_NAME} weight="regular" />
                )}
              </CommandItem>
            )}
            {matches.map((project) => (
              <CommandItem
                className={ROW_CLASS_NAME}
                key={project.id}
                onSelect={() => choose(() => onSelect(project.id))}
                size="sm"
                title={project.repo_full_name ?? undefined}
                value={project.id}
              >
                <FolderIcon className={ROW_ICON_CLASS_NAME} />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                {project.members.length > 1 && (
                  <UsersIcon
                    aria-label="Shared"
                    className={ROW_ICON_CLASS_NAME}
                  />
                )}
                {project.id === selectedId && (
                  <CheckIcon className={ROW_ICON_CLASS_NAME} weight="regular" />
                )}
              </CommandItem>
            ))}
            {!needle && projects.length === 0 && (
              <p className="px-space-2 py-space-1 text-xs text-secondary">
                Type a name to create your first project
              </p>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
