import { CaretDownIcon, StackIcon } from "@phosphor-icons/react"

import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
} from "@/components/ui/menu"
import type { WorkspaceOption } from "@/lib/api"

interface WorkspaceSelectorProps {
  workspaces: Array<WorkspaceOption>
  selectedSlug: string | null
  onChange: (slug: string | null) => void
  disabled?: boolean
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
}: WorkspaceSelectorProps) {
  if (workspaces.length < 2) return null

  const selected = workspaces.find(
    (workspace) => workspace.slug === selectedSlug
  )

  return (
    <Menu>
      <MenuTrigger
        disabled={disabled}
        aria-label="Workspace"
        className="flex max-w-[220px] min-w-0 shrink cursor-pointer items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60"
      >
        <StackIcon className="size-3.5 shrink-0" />
        <span className="flex-1 truncate text-left">
          {selected?.name ?? "No workspace"}
        </span>
        <CaretDownIcon className="size-3 shrink-0 opacity-70" />
      </MenuTrigger>
      <MenuPopup align="start" className="max-h-72 w-64" sideOffset={4}>
        <MenuGroup>
          <MenuGroupLabel>Workspace</MenuGroupLabel>
          <MenuRadioGroup
            value={selectedSlug}
            onValueChange={(value: unknown) => {
              if (typeof value === "string") onChange(value)
            }}
          >
            {workspaces.map((workspace) => (
              <MenuRadioItem
                closeOnClick
                key={workspace.slug}
                value={workspace.slug}
              >
                <span className="flex min-w-0 items-center">
                  <span className="truncate">{workspace.name}</span>
                  {!workspace.has_snapshot && (
                    <span className="ml-2 shrink-0 text-[10px] text-muted-foreground">
                      no snapshot
                    </span>
                  )}
                </span>
              </MenuRadioItem>
            ))}
          </MenuRadioGroup>
        </MenuGroup>
      </MenuPopup>
    </Menu>
  )
}
