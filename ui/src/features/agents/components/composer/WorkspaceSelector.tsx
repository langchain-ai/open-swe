import { StackRegularIcon } from "@langchain/macaw-components/icons"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"

import {
  COMPOSER_TRIGGER_CLASS_NAME,
  ComposerControlChevron,
  ComposerMenuLabel,
  ComposerMenuOption,
  ComposerTriggerIcon,
} from "./ComposerControl"
import type { WorkspaceOption } from "@/lib/api"
import { cn } from "@/lib/utils"

interface WorkspaceSelectorProps {
  workspaces: Array<WorkspaceOption>
  selectedSlug: string | null
  onChange: (slug: string | null) => void
  disabled?: boolean
  /** Render even with a single workspace, where a choice must still be made. */
  showWithOneWorkspace?: boolean
  /** Shown while nothing is selected. */
  placeholder?: string
  side?: "top" | "bottom"
}

/**
 * Picks the workspace a new thread's sandbox boots from.
 *
 * Only rendered when there is more than one to choose between — with a single
 * workspace (or none) the choice is already made, so the control would be
 * noise.
 */
export function WorkspaceSelector({
  workspaces,
  selectedSlug,
  onChange,
  disabled = false,
  showWithOneWorkspace = false,
  placeholder = "No workspace",
  side = "bottom",
}: WorkspaceSelectorProps) {
  if (workspaces.length < (showWithOneWorkspace ? 1 : 2)) return null

  const selected = workspaces.find(
    (workspace) => workspace.slug === selectedSlug
  )

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Workspace"
        disabled={disabled}
        className={cn(COMPOSER_TRIGGER_CLASS_NAME, "max-w-[220px] min-w-0")}
      >
        <ComposerTriggerIcon icon={StackRegularIcon} />
        <span className="truncate">{selected?.name ?? placeholder}</span>
        <ComposerControlChevron />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-72 w-64"
        side={side}
        sideOffset={7}
      >
        <DropdownMenuGroup>
          <ComposerMenuLabel>Workspace</ComposerMenuLabel>
          {workspaces.map((workspace) => (
            <ComposerMenuOption
              key={workspace.slug}
              onSelect={() => onChange(workspace.slug)}
              selected={workspace.slug === selectedSlug}
              trailing={
                !workspace.has_snapshot && (
                  <span className="shrink-0 text-xxs text-tertiary">
                    No snapshot
                  </span>
                )
              }
            >
              {workspace.name}
            </ComposerMenuOption>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
